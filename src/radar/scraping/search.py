"""Descoberta de candidatas via Tavily.

Primeira camada do pipeline e a mais barata: é ela que transforma as queries do
Search Planner em URLs para o Scraper visitar. Duas decisões moldam o módulo:

* **Falhar alto quando não há chave.** Busca vazia por falta de credencial se
  parece com "nenhuma startup encontrada", e um pipeline que devolve zero
  resultados sem reclamar é pior que um que quebra — o erro silencioso vira
  conclusão errada no relatório final.
* **Deduplicar por domínio.** Dez URLs do mesmo site são uma empresa, não dez
  candidatas. A unidade de análise do projeto é a empresa, e o domínio é o
  identificador mais estável que a busca oferece.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

import structlog

from radar.config import Settings, get_settings

log = structlog.get_logger(__name__)

#: Domínios que nunca são a fonte primária sobre uma startup: rede social,
#: agregador genérico e afins. Não é lista de "sites ruins" — é lista de sites
#: cujo conteúdo não sustenta evidência técnica sobre a empresa.
DEFAULT_BLOCKED_DOMAINS: frozenset[str] = frozenset(
    {
        "facebook.com",
        "instagram.com",
        "twitter.com",
        "x.com",
        "tiktok.com",
        "pinterest.com",
        "reddit.com",
        "youtube.com",
        "youtu.be",
        "quora.com",
        "wikipedia.org",
        "glassdoor.com",
        "glassdoor.com.br",
        "indeed.com",
        "vagas.com.br",
        "catho.com.br",
        "amazon.com",
        "mercadolivre.com.br",
        "google.com",
        "bing.com",
        "yahoo.com",
        "scribd.com",
        "slideshare.net",
    }
)


class SearchError(RuntimeError):
    """Falha da camada de descoberta."""


class MissingSearchKeyError(SearchError):
    """`TAVILY_API_KEY` ausente. Erro explícito para não virar resultado vazio."""


@runtime_checkable
class TavilyLike(Protocol):
    """Contrato mínimo do cliente Tavily, para permitir dublê nos testes."""

    async def search(self, query: str, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(slots=True, frozen=True)
class SearchCandidate:
    """Uma URL candidata, com o que a busca já sabe sobre ela."""

    url: str
    title: str = ""
    snippet: str = ""
    score: float = 0.0
    query: str = ""

    @property
    def domain(self) -> str:
        host = urlsplit(self.url).netloc.lower().split("@")[-1].split(":")[0]
        return host.removeprefix("www.")


def domain_of(url: str) -> str:
    host = urlsplit(url).netloc.lower().split("@")[-1].split(":")[0]
    return host.removeprefix("www.")


def is_blocked(domain: str, blocked: Iterable[str] = DEFAULT_BLOCKED_DOMAINS) -> bool:
    """Casa o domínio e seus subdomínios (`br.linkedin.com` conta como bloqueado)."""
    domain = domain.lower().removeprefix("www.")
    return any(domain == item or domain.endswith(f".{item}") for item in blocked)


def dedupe_by_domain(
    candidates: Sequence[SearchCandidate], *, per_domain: int = 1
) -> list[SearchCandidate]:
    """Mantém os melhores resultados de cada domínio, preservando a ordem de chegada.

    Ordem de chegada importa: as queries do Search Planner vêm da mais específica
    para a mais genérica, e a primeira menção a um domínio costuma ser a melhor.
    """
    best: dict[str, list[SearchCandidate]] = {}
    order: list[str] = []

    for candidate in candidates:
        domain = candidate.domain
        if domain not in best:
            best[domain] = []
            order.append(domain)
        bucket = best[domain]
        bucket.append(candidate)
        bucket.sort(key=lambda c: c.score, reverse=True)
        del bucket[per_domain:]

    return [candidate for domain in order for candidate in best[domain]]


class TavilySearch:
    """Wrapper async sobre `tavily-python`."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: TavilyLike | None = None,
        blocked_domains: Iterable[str] | None = None,
        max_results: int = 5,
        search_depth: str = "basic",
    ) -> None:
        self._settings = settings or get_settings()
        self._blocked = frozenset(
            blocked_domains if blocked_domains is not None else DEFAULT_BLOCKED_DOMAINS
        )
        self._max_results = max_results
        self._search_depth = search_depth

        if client is not None:
            self._client: TavilyLike = client
            return

        # Falha na construção, não na primeira query: quem monta o grafo descobre
        # o problema antes de gastar tokens em um plano de busca inútil.
        if not self._settings.tavily_api_key:
            raise MissingSearchKeyError(
                "TAVILY_API_KEY nao configurada — defina no .env para habilitar a descoberta"
            )
        from tavily import AsyncTavilyClient

        self._client = AsyncTavilyClient(api_key=self._settings.tavily_api_key)

    @property
    def blocked_domains(self) -> frozenset[str]:
        return self._blocked

    async def search(
        self,
        queries: Sequence[str],
        *,
        max_results: int | None = None,
        per_domain: int = 1,
    ) -> list[SearchCandidate]:
        """Roda as queries em paralelo e devolve candidatos filtrados e deduplicados."""
        wanted = [q.strip() for q in queries if q and q.strip()]
        if not wanted:
            return []

        raw = await asyncio.gather(
            *(self._search_one(q, max_results or self._max_results) for q in wanted),
            return_exceptions=True,
        )

        collected: list[SearchCandidate] = []
        seen_urls: set[str] = set()
        for query, outcome in zip(wanted, raw, strict=True):
            if isinstance(outcome, BaseException):
                # Uma query ruim não pode derrubar o lote: o Search Planner emite
                # várias justamente porque nem todas funcionam.
                log.warning("query_falhou", query=query, error=str(outcome))
                continue
            for candidate in outcome:
                key = candidate.url.rstrip("/").casefold()
                if key in seen_urls:
                    continue
                seen_urls.add(key)
                collected.append(candidate)

        result = dedupe_by_domain(collected, per_domain=per_domain)
        log.info(
            "descoberta_concluida",
            queries=len(wanted),
            brutos=len(collected),
            candidatos=len(result),
        )
        return result

    async def _search_one(self, query: str, max_results: int) -> list[SearchCandidate]:
        try:
            payload = await self._client.search(
                query=query,
                max_results=max_results,
                search_depth=self._search_depth,
            )
        except Exception as exc:  # a lib levanta erros próprios; normalizamos aqui
            raise SearchError(f"busca falhou para {query!r}: {exc}") from exc

        candidates: list[SearchCandidate] = []
        for item in payload.get("results") or []:
            url = (item.get("url") or "").strip()
            if not url:
                continue
            if is_blocked(domain_of(url), self._blocked):
                log.debug("dominio_filtrado", url=url)
                continue
            candidates.append(
                SearchCandidate(
                    url=url,
                    title=(item.get("title") or "").strip(),
                    snippet=(item.get("content") or "").strip(),
                    score=float(item.get("score") or 0.0),
                    query=query,
                )
            )
        return candidates
