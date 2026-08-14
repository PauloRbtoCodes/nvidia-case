"""Scraper Agent: URLs semente → páginas coletadas e extraídas.

Primeiro nó do subgrafo por empresa, e o único que toca a rede aberta. Três
camadas, da mais barata para a mais cara, como no plano de arquitetura:

1. as URLs que já temos (semente da descoberta, ou re-busca do validador);
2. os links de sinal que a home revela — carreiras, blog de engenharia, preços,
   clientes. É aqui que o diagnóstico ganha a evidência que a home não dá;
3. o fallback de navegador, que fica dentro do `HttpFetcher` e só dispara quando
   a página vem vazia sem JS.

O nó é reentrante: o Evidence Validator pode devolver o fluxo para cá com
`refetch_queries`, e `scrape_attempts` é o teto que impede que evidência
genuinamente ausente vire consumo infinito de quota.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

import structlog

from radar.graph.nodes.base import falha, page_to_dict
from radar.graph.nodes.deps import NodeDeps
from radar.graph.state import CompanyState
from radar.scraping.extract import (
    extract_career_links,
    extract_job_titles,
    extract_page,
    extract_signal_links,
)
from radar.scraping.search import domain_of

log = structlog.get_logger(__name__)

#: Páginas extras por empresa, além das sementes. Cinco cobre carreiras + blog +
#: preços + clientes com folga; acima disso o custo cresce sem que o Extractor
#: consiga usar o contexto (ver `MAX_DOCS_IN_PROMPT`).
MAX_LINKS_SEGUIDOS = 5

#: Resultados por query de re-busca. A re-coleta persegue uma lacuna específica
#: apontada pelo validador — não é hora de ampliar o funil.
REFETCH_RESULTS = 3


def _dominio_da_empresa(state: CompanyState) -> str | None:
    for url in state.get("seed_urls") or []:
        dominio = domain_of(url)
        if dominio:
            return dominio
    return None


async def _urls_de_rebusca(deps: NodeDeps, state: CompanyState) -> list[str]:
    """Traduz as queries do validador em URLs novas.

    Sem Search API configurada a re-coleta simplesmente não acontece — e isso é
    aceitável: o validador já registrou a lacuna, e o briefing vai declará-la nos
    `caveats` em vez de mascará-la.
    """
    queries = state.get("refetch_queries") or []
    if not queries or deps.search is None:
        return []

    candidatos = await deps.search.search(queries[:3], max_results=REFETCH_RESULTS, per_domain=2)
    return [c.url for c in candidatos]


async def _coletar(deps: NodeDeps, state: CompanyState, tentativa: int) -> dict[str, Any]:
    """A coleta propriamente dita, sem o controle de orçamento em volta."""
    ja_coletadas = {p["url"] for p in state.get("raw_pages") or []}

    if deps.fetcher is None:
        raise RuntimeError("Coleta sem `HttpFetcher` configurado.")

    alvos = [u for u in (state.get("seed_urls") or []) if u not in ja_coletadas]
    if tentativa > 0:
        alvos.extend(await _urls_de_rebusca(deps, state))

    alvos = list(dict.fromkeys(u for u in alvos if u not in ja_coletadas))
    if not alvos:
        raise ValueError("Nenhuma URL nova para coletar.")

    dominio = _dominio_da_empresa(state)
    paginas: list[dict[str, Any]] = []
    vagas: list[str] = []
    seguir: list[str] = []

    for resultado in await deps.fetcher.fetch_many(alvos):
        if not resultado.ok:
            continue
        base = resultado.final_url or resultado.url
        pagina = extract_page(base, resultado.html, company_domain=dominio)
        if not pagina.is_empty:
            paginas.append(page_to_dict(pagina))
        # Títulos de vaga saem do HTML e não do texto principal: eles moram em
        # `<a>` e `<li>` que o extrator de prosa descarta.
        vagas.extend(extract_job_titles(resultado.html))

        if tentativa == 0:
            seguir.extend(extract_career_links(resultado.html, base))
            seguir.extend(extract_signal_links(resultado.html, base, same_domain_only=True))

    # Segunda onda: só na primeira tentativa, e só o que ainda não vimos.
    vistas = ja_coletadas | {p["url"] for p in paginas} | set(alvos)
    extras = [u for u in dict.fromkeys(seguir) if u not in vistas][:MAX_LINKS_SEGUIDOS]
    if extras:
        for resultado in await deps.fetcher.fetch_many(extras):
            if not resultado.ok:
                continue
            base = resultado.final_url or resultado.url
            pagina = extract_page(base, resultado.html, company_domain=dominio)
            if not pagina.is_empty:
                paginas.append(page_to_dict(pagina))
            vagas.extend(extract_job_titles(resultado.html))

    log.info(
        "coleta",
        empresa=state.get("company_name"),
        tentativa=tentativa,
        alvos=len(alvos),
        extras=len(extras),
        paginas=len(paginas),
        vagas=len(set(vagas)),
    )
    return {
        "raw_pages": paginas,
        # Guardado fora do perfil porque o Extractor ainda não rodou; o nó de
        # extração mescla esta lista determinística com o que o LLM achar. A
        # fusão é aqui e não num reducer: `operator.add` repetiria o mesmo título
        # a cada re-coleta da mesma página de vagas.
        "job_titles": list(dict.fromkeys([*(state.get("job_titles") or []), *vagas]))[:30],
    }


def make_collect_sources(
    deps: NodeDeps,
) -> Callable[[CompanyState], Coroutine[Any, Any, dict[str, Any]]]:
    """Este nó não usa `node_guard`, ao contrário de todos os outros.

    O orçamento de retry é consumido pela **tentativa**, não pelo sucesso. O
    guard, ao capturar a exceção, devolve apenas `failures` — o incremento de
    `scrape_attempts` se perderia junto, e uma empresa cujo site não responde
    ficaria em ciclo infinito entre o validador e este nó, porque o validador
    continuaria vendo a mesma tentativa de sempre.
    """

    async def collect_sources(state: CompanyState) -> dict[str, Any]:
        empresa = state.get("company_name") or "?"
        tentativa = state.get("scrape_attempts", 0)
        saida: dict[str, Any] = {"scrape_attempts": tentativa + 1}
        try:
            saida.update(await _coletar(deps, state, tentativa))
        except Exception as exc:  # noqa: BLE001 - HTML e rede de terceiros
            log.warning("no_falhou", node="scraper", empresa=empresa, erro=str(exc)[:300])
            saida["failures"] = [falha("scraper", exc, company=empresa, recoverable=True)]
        return saida

    return collect_sources
