"""Coleta de HTML em dois níveis: httpx no caminho normal, Playwright na exceção.

A ordem de cada requisição é fixa e não negociável: **cache → robots → throttle
→ rede**. Cache antes de robots porque uma leitura de disco não é requisição e
não precisa de permissão; robots antes do throttle porque não faz sentido
esperar por um host que não autorizou a coleta.

Sobre o Playwright: ele resolve o problema real de SPA que renderiza tudo no
cliente, mas é ordens de grandeza mais lento, quebra com frequência e exige
binários de navegador que nem sempre estão instalados. Por isso o gatilho é uma
heurística explícita e estreita — HTML que rendeu quase nenhum texto — e nunca
o caminho padrão. Se o fallback começar a disparar em toda página, o problema
está na heurística, não no site.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import structlog
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from radar.config import Settings, get_settings
from radar.scraping.cache import DEFAULT_TTL_SECONDS, CachedResponse, ResponseCache
from radar.scraping.extract import visible_text
from radar.scraping.politeness import PolitenessGate

log = structlog.get_logger(__name__)

#: Abaixo disto consideramos que a página não entregou conteúdo sem JavaScript.
#: 200 caracteres é menos que um parágrafo: qualquer página institucional real
#: passa com folga, e o casco de uma SPA (`<div id="root"></div>`) não chega perto.
MIN_TEXT_CHARS = 200

#: Renderizador injetável: recebe a URL e devolve HTML renderizado, ou None.
Renderer = Callable[[str], Awaitable[str | None]]


class RetryableStatusError(Exception):
    """Status que merece nova tentativa: 429 e 5xx.

    Existe como exceção porque o tenacity decide por tipo de exceção — e a
    distinção importa: repetir um 404 é desperdício e repetir um 403 é insistir
    onde já disseram não.
    """

    def __init__(self, status_code: int, url: str) -> None:
        super().__init__(f"status {status_code} em {url}")
        self.status_code = status_code
        self.url = url


@dataclass(slots=True)
class FetchResult:
    """Resposta bruta de uma página, com a procedência da coleta."""

    url: str
    status_code: int
    html: str
    final_url: str | None = None
    from_cache: bool = False
    rendered: bool = False

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300 and bool(self.html.strip())


def needs_rendering(html: str, *, min_text_chars: int = MIN_TEXT_CHARS) -> bool:
    """Heurística única que autoriza o fallback de navegador."""
    if not html or not html.strip():
        return True
    return len(visible_text(html)) < min_text_chars


async def playwright_renderer(
    url: str,
    *,
    user_agent: str,
    timeout_seconds: float = 30.0,
) -> str | None:
    """Renderiza a página em navegador headless.

    Import tardio de propósito: o Playwright (e seus binários) é opcional em
    tempo de execução, e a ausência dele deve degradar a coleta de uma página,
    não impedir o import do módulo — inclusive nos testes, que nunca o exercitam.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        log.warning("playwright_indisponivel", url=url)
        return None

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                page = await browser.new_page(user_agent=user_agent)
                await page.goto(url, timeout=timeout_seconds * 1000, wait_until="networkidle")
                return await page.content()
            finally:
                await browser.close()
    except Exception as exc:  # navegador headless falha de mil formas distintas
        log.warning("playwright_falhou", url=url, error=str(exc))
        return None


class HttpFetcher:
    """Cliente de coleta com cache, política de educação e fallback opcional.

    Tudo que toca o mundo externo é injetável (`transport`, `renderer`, `cache`,
    `gate`) para que a suíte de testes rode sem rede, sem navegador e sem chave.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        gate: PolitenessGate | None = None,
        cache: ResponseCache | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        renderer: Renderer | None = None,
        use_playwright_fallback: bool = True,
        max_attempts: int = 3,
        retry_wait_multiplier: float = 0.5,
        min_text_chars: int = MIN_TEXT_CHARS,
        cache_ttl_seconds: float = DEFAULT_TTL_SECONDS,
    ) -> None:
        self._settings = settings or get_settings()
        self._gate = gate or PolitenessGate(self._settings)
        self._cache = cache or ResponseCache(
            self._settings.cache_dir, ttl_seconds=cache_ttl_seconds
        )
        self._transport = transport
        self._use_fallback = use_playwright_fallback
        self._renderer = renderer
        self._max_attempts = max(1, max_attempts)
        self._retry_wait = retry_wait_multiplier
        self._min_text_chars = min_text_chars
        self._client: httpx.AsyncClient | None = None

    @property
    def cache(self) -> ResponseCache:
        return self._cache

    @property
    def gate(self) -> PolitenessGate:
        return self._gate

    async def __aenter__(self) -> HttpFetcher:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _get_client(self) -> httpx.AsyncClient:
        # Um único cliente por fetcher: reaproveitar conexões é o que mantém a
        # coleta rápida sem aumentar a pressão sobre o host.
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers=self._gate.headers(),
                timeout=self._settings.scraper_timeout_seconds,
                follow_redirects=True,
                transport=self._transport,
            )
        return self._client

    async def fetch(self, url: str, *, force_refresh: bool = False) -> FetchResult | None:
        """Busca uma página. Devolve None quando robots.txt proíbe ou a rede falha."""
        if not force_refresh:
            cached = self._cache.get(url)
            if cached is not None:
                return FetchResult(
                    url=url,
                    status_code=cached.status_code,
                    html=cached.body,
                    final_url=cached.final_url,
                    from_cache=True,
                    rendered=cached.rendered,
                )

        if not await self._gate.can_fetch(url):
            log.info("fetch_bloqueado_por_robots", url=url)
            return None

        try:
            response = await self._request(url)
        except (RetryError, httpx.HTTPError) as exc:
            log.warning("fetch_falhou", url=url, error=str(exc))
            return None
        except RetryableStatusError as exc:
            log.warning("fetch_desistiu", url=url, status=exc.status_code)
            return None

        result = FetchResult(
            url=url,
            status_code=response.status_code,
            html=response.text,
            final_url=str(response.url),
        )

        if not (200 <= response.status_code < 300):
            # Erro do cliente não vai para o cache: cachear um 404 transitório
            # esconderia a página real por uma semana inteira.
            log.info("fetch_status_nao_ok", url=url, status=response.status_code)
            return result

        if self._should_render(result):
            rendered_html = await self._render(url)
            if rendered_html:
                result.html = rendered_html
                result.rendered = True

        self._cache.set(
            CachedResponse(
                url=url,
                status_code=result.status_code,
                body=result.html,
                headers=dict(response.headers),
                fetched_at=datetime.now(UTC),
                final_url=result.final_url,
                rendered=result.rendered,
            )
        )
        return result

    async def fetch_many(
        self, urls: Iterable[str], *, force_refresh: bool = False
    ) -> list[FetchResult]:
        """Sequencial de propósito: o paralelismo do lote vive no grafo (`Send`),
        e disparar tudo aqui atropelaria o rate limit de hosts repetidos.

        `force_refresh` existe para a passada de monitoramento do radar: o TTL de
        cache é o que faz uma re-execução dentro da semana concluir "nada mudou"
        sem ter olhado. Quem decide quais URLs merecem a rede de novo é o
        `collector`, que sabe distinguir fonte de sinal de fonte institucional.
        """
        results: list[FetchResult] = []
        for url in urls:
            result = await self.fetch(url, force_refresh=force_refresh)
            if result is not None:
                results.append(result)
        return results

    def _should_render(self, result: FetchResult) -> bool:
        if not self._use_fallback:
            return False
        empty = needs_rendering(result.html, min_text_chars=self._min_text_chars)
        if empty:
            log.info("fallback_playwright", url=result.url, chars=len(visible_text(result.html)))
        return empty

    async def _default_renderer(self, url: str) -> str | None:
        return await playwright_renderer(
            url,
            user_agent=self._gate.user_agent,
            # Navegador headless carrega a página inteira: dar o mesmo timeout do
            # httpx faria o fallback falhar exatamente onde ele deveria ajudar.
            timeout_seconds=self._settings.scraper_timeout_seconds * 1.5,
        )

    async def _render(self, url: str) -> str | None:
        renderer: Renderer = self._renderer or self._default_renderer
        # O render também consome um slot do host: é uma segunda visita real.
        async with self._gate.slot(url):
            return await renderer(url)

    async def _request(self, url: str) -> httpx.Response:
        client = self._get_client()
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self._max_attempts),
            wait=wait_exponential(multiplier=self._retry_wait, max=10),
            retry=retry_if_exception_type((httpx.TransportError, RetryableStatusError)),
            reraise=True,
        ):
            with attempt:
                async with self._gate.slot(url):
                    response = await client.get(url)
                # 429 e 5xx são transitórios; os demais 4xx são resposta final e
                # repeti-los só gasta a paciência do servidor.
                if response.status_code == 429 or response.status_code >= 500:
                    raise RetryableStatusError(response.status_code, url)
                return response
        raise RuntimeError("inalcançável: AsyncRetrying sempre retorna ou levanta")


async def fetch_urls(
    urls: Sequence[str],
    *,
    settings: Settings | None = None,
) -> list[FetchResult]:
    """Atalho para uso pontual (scripts, notebooks) com fetcher descartável."""
    async with HttpFetcher(settings) as fetcher:
        return await fetcher.fetch_many(urls)
