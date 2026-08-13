"""Coleta educada: robots.txt, rate limit por domínio e User-Agent identificável.

Por que isto é uma camada própria e não um detalhe escondido dentro do fetch: o
case é acadêmico, mas a coleta é real e roda sobre sites de startups pequenas.
Um crawler anônimo e sem freio derruba servidor alheio e queima a credibilidade
do projeto inteiro — o mesmo argumento que sustenta "nenhuma afirmação sem
evidência" sustenta "nenhuma requisição sem consentimento e sem freio".

O rate limit é POR DOMÍNIO, nunca global: raspar dez empresas em paralelo é
legítimo; martelar o mesmo host dez vezes por segundo não é.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from functools import lru_cache
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
import structlog

from radar.config import Settings, get_settings

log = structlog.get_logger(__name__)

#: Assinatura do buscador de robots.txt. Injetável para que os testes não toquem a rede.
RobotsFetcher = Callable[[str], Awaitable[str | None]]


def domain_of(url: str) -> str:
    """Host normalizado (sem porta e sem `www.`), que é a chave de todo o módulo."""
    netloc = urlsplit(url).netloc.lower()
    host = netloc.split("@")[-1].split(":")[0]
    return host.removeprefix("www.")


def robots_url_for(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme or "https", parts.netloc, "/robots.txt", "", ""))


def http_robots_fetcher(user_agent: str, timeout_seconds: float = 10.0) -> RobotsFetcher:
    """Buscador padrão de robots.txt.

    Fala HTTP direto em vez de passar por `fetch.py` de propósito: o fetch depende
    da política, e a política não pode depender do fetch sob pena de ciclo — além
    de que buscar robots.txt não deve ser barrado pelo próprio robots.txt.
    """

    async def _fetch(url: str) -> str | None:
        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": user_agent},
                timeout=timeout_seconds,
                follow_redirects=True,
            ) as client:
                response = await client.get(url)
        except httpx.HTTPError as exc:  # rede instável não pode travar a pipeline
            log.warning("robots_fetch_failed", url=url, error=str(exc))
            return None
        if response.status_code >= 400:
            return None
        return response.text

    return _fetch


class RobotsCache:
    """Cache de robots.txt por domínio, com parsing via `urllib.robotparser`.

    Política de erro: ausência ou falha de robots.txt libera a coleta. É o
    comportamento definido pelo próprio padrão (sem arquivo = sem restrição), e
    tratar erro de rede como proibição transformaria instabilidade alheia em
    perda silenciosa de cobertura.
    """

    def __init__(
        self,
        *,
        user_agent: str,
        fetcher: RobotsFetcher | None = None,
        enabled: bool = True,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._user_agent = user_agent
        self._fetcher = fetcher or http_robots_fetcher(user_agent, timeout_seconds)
        self._enabled = enabled
        self._parsers: dict[str, RobotFileParser | None] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def can_fetch(self, url: str) -> bool:
        if not self._enabled:
            return True

        parser = await self._parser_for(url)
        if parser is None:
            return True

        allowed = parser.can_fetch(self._user_agent, url)
        if not allowed:
            log.info("robots_disallow", url=url, user_agent=self._user_agent)
        return allowed

    async def crawl_delay(self, url: str) -> float | None:
        """Delay declarado pelo site, quando existir — o dono do host manda mais que nós."""
        if not self._enabled:
            return None
        parser = await self._parser_for(url)
        if parser is None:
            return None
        raw = parser.crawl_delay(self._user_agent)
        return float(raw) if raw is not None else None

    async def _parser_for(self, url: str) -> RobotFileParser | None:
        # Com a checagem desligada não buscamos robots.txt: seria uma requisição
        # de rede a mais para produzir uma resposta que já é conhecida.
        if not self._enabled:
            return None

        domain = domain_of(url)
        if domain in self._parsers:
            return self._parsers[domain]

        # Lock por domínio evita que N corrotinas do mesmo host busquem o mesmo
        # robots.txt simultaneamente na primeira visita.
        lock = self._locks.setdefault(domain, asyncio.Lock())
        async with lock:
            if domain in self._parsers:
                return self._parsers[domain]

            body = await self._fetcher(robots_url_for(url))
            parser: RobotFileParser | None = None
            if body is not None:
                parser = RobotFileParser()
                parser.parse(body.splitlines())
            self._parsers[domain] = parser
            return parser


class DomainRateLimiter:
    """Espaça requisições por host em ao menos `interval_seconds`.

    `clock` e `sleeper` são injetáveis para que o teste verifique o espaçamento
    de forma determinística, sem depender de dormir de verdade.
    """

    def __init__(
        self,
        interval_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._interval = max(0.0, interval_seconds)
        self._clock = clock
        self._sleeper = sleeper
        self._last_start: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._overrides: dict[str, float] = {}

    def set_interval_for(self, url: str, seconds: float) -> None:
        """Permite honrar um `Crawl-delay` maior que o nosso padrão."""
        domain = domain_of(url)
        self._overrides[domain] = max(self._interval, seconds)

    def interval_for(self, url: str) -> float:
        return self._overrides.get(domain_of(url), self._interval)

    async def acquire(self, url: str) -> None:
        domain = domain_of(url)
        interval = self.interval_for(url)
        lock = self._locks.setdefault(domain, asyncio.Lock())

        # O lock cobre apenas o cálculo da espera e a marcação do horário: manter
        # o lock durante a requisição inteira serializaria hosts lentos sem ganho
        # de educação — o que importa é o intervalo entre inícios.
        async with lock:
            last = self._last_start.get(domain)
            now = self._clock()
            if last is not None:
                wait = interval - (now - last)
                if wait > 0:
                    log.debug("rate_limit_wait", domain=domain, seconds=round(wait, 3))
                    await self._sleeper(wait)
                    now = self._clock()
            self._last_start[domain] = now


class PolitenessGate:
    """Fachada usada pelo fetch: decide se pode buscar e aplica o freio."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        robots: RobotsCache | None = None,
        limiter: DomainRateLimiter | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._robots = robots or RobotsCache(
            user_agent=self._settings.scraper_user_agent,
            enabled=self._settings.scraper_respect_robots,
            timeout_seconds=self._settings.scraper_timeout_seconds,
        )
        self._limiter = limiter or DomainRateLimiter(self._settings.scraper_rate_limit_seconds)

    @property
    def user_agent(self) -> str:
        return self._settings.scraper_user_agent

    @property
    def robots(self) -> RobotsCache:
        return self._robots

    @property
    def limiter(self) -> DomainRateLimiter:
        return self._limiter

    def headers(self) -> dict[str, str]:
        """Cabeçalhos de toda requisição: quem somos e como nos contatar."""
        return {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
        }

    async def can_fetch(self, url: str) -> bool:
        return await self._robots.can_fetch(url)

    @asynccontextmanager
    async def slot(self, url: str) -> AsyncIterator[None]:
        """`async with gate.slot(url):` — bloco que só entra após o throttle do host."""
        declared = await self._robots.crawl_delay(url)
        if declared is not None:
            self._limiter.set_interval_for(url, declared)
        await self._limiter.acquire(url)
        yield


@lru_cache
def default_gate() -> PolitenessGate:
    """Instância compartilhada — o estado de rate limit só serve se for único."""
    return PolitenessGate()


async def can_fetch(url: str) -> bool:
    """Atalho de conveniência sobre o gate padrão.

    É `async` porque decidir exige possivelmente buscar o robots.txt do domínio;
    uma versão síncrona só poderia mentir ou bloquear o event loop.
    """
    return await default_gate().can_fetch(url)
