"""Descoberta: plano de busca → lista de empresas candidatas.

Segundo nó do grafo externo. A saída aqui é o que o `Send` distribui para os
subgrafos, então o custo de um falso positivo é uma empresa inteira processada
à toa — daí o filtro por domínio e a deduplicação antes do fan-out.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

import structlog

from radar.graph.nodes.base import node_guard
from radar.graph.nodes.deps import NodeDeps
from radar.graph.state import RadarState
from radar.scraping.search import SearchCandidate

log = structlog.get_logger(__name__)

#: Resultados por query pedidos à Search API. Acima disso a cauda vira ruído
#: (agregadores, listas de "top 10 startups") e o dedupe por domínio descarta
#: quase tudo mesmo.
RESULTS_PER_QUERY = 6

#: Domínios que hospedam muitas empresas diferentes. Não são candidatos a
#: "empresa", são fontes *sobre* empresas: um perfil no Distrito é evidência,
#: não é a startup. Deixá-los virar candidato produz um subgrafo que tenta
#: diagnosticar o diretório inteiro como se fosse uma companhia.
AGGREGATOR_HINTS: tuple[str, ...] = (
    "distrito.me",
    "startupbase.com.br",
    "abstartups.com.br",
    "crunchbase.com",
    "cubo.network",
    "braziljournal.com",
    "startups.com.br",
    "baguete.com.br",
    "neofeed.com.br",
    "exame.com",
    "infomoney.com.br",
    "g1.globo.com",
    "medium.com",
    "github.com",
)


def _e_agregador(domain: str) -> bool:
    return any(domain == hint or domain.endswith(f".{hint}") for hint in AGGREGATOR_HINTS)


def _nome_provavel(candidate: SearchCandidate) -> str:
    """Nome de trabalho da empresa, refinado depois pelo Extractor.

    O título de resultado de busca costuma ser "Acme | Plataforma de X"; o
    primeiro segmento antes do separador é quase sempre o nome. Quando não há
    título, o domínio sem TLD serve — e o Extractor corrige a partir do site.
    """
    titulo = candidate.title.strip()
    for separador in ("|", "–", "—", "-", ":", "·"):
        if separador in titulo:
            titulo = titulo.split(separador)[0].strip()
            break
    if len(titulo) >= 2:
        return titulo[:80]
    return candidate.domain.split(".")[0].capitalize()


def make_discover_companies(
    deps: NodeDeps,
) -> Callable[[RadarState], Coroutine[Any, Any, dict[str, Any]]]:
    @node_guard("discovery", company_key=None)
    async def discover_companies(state: RadarState) -> dict[str, Any]:
        plano = state.get("search_plan") or {}
        queries: list[str] = list(plano.get("queries") or [])
        limite = plano.get("max_companies") or state.get("max_companies") or 12

        if deps.search is None:
            raise RuntimeError(
                "Search API não configurada (TAVILY_API_KEY ausente). Rode o grafo "
                "com `seed_urls` explícitas ou configure a chave."
            )
        if not queries:
            raise ValueError("Plano de busca sem queries.")

        candidatos = await deps.search.search(
            queries, max_results=RESULTS_PER_QUERY, per_domain=1
        )

        descobertos: list[dict[str, Any]] = []
        for candidato in candidatos:
            if _e_agregador(candidato.domain):
                log.debug("agregador_ignorado", url=candidato.url)
                continue
            descobertos.append(
                {
                    "url": candidato.url,
                    "domain": candidato.domain,
                    "title": candidato.title,
                    "snippet": candidato.snippet,
                    "query": candidato.query,
                    "company_name": _nome_provavel(candidato),
                }
            )
            if len(descobertos) >= limite:
                break

        log.info(
            "descoberta",
            queries=len(queries),
            candidatos=len(candidatos),
            selecionados=len(descobertos),
        )
        return {"discovered": descobertos}

    return discover_companies
