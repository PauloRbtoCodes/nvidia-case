"""Search Planner: pedido em linguagem natural → plano de busca executável.

Primeiro nó do grafo externo. Roda no modelo rápido (ver `DEFAULT_TASK_TIERS`)
porque uma query ruim custa uma busca, não um diagnóstico errado.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

import structlog

from radar.graph.nodes.base import falha, node_guard
from radar.graph.nodes.deps import NodeDeps
from radar.graph.state import RadarState, SearchPlan
from radar.llm.model_registry import LLMTask
from radar.llm.schemas import SearchPlan as LLMSearchPlan

log = structlog.get_logger(__name__)

#: Quantas queries pedimos ao planner. O prompt exige cobrir três famílias
#: (descoberta, sinais de stack, tração), então menos de seis deixa alguma
#: família com uma query só — e é a de stack que costuma ficar de fora.
DEFAULT_MAX_QUERIES = 8

#: Empresas por execução, quando o usuário não diz. Doze cabe numa revisão
#: humana de uma sentada; acima disso o gerente para de ler a fila.
DEFAULT_MAX_COMPANIES = 12


def _plano_de_emergencia(query: str, max_companies: int) -> SearchPlan:
    """Plano mínimo quando o planner falha.

    A busca do usuário, crua, ainda é uma query utilizável. Perder a execução
    inteira porque o modelo pequeno devolveu JSON torto seria desproporcional —
    e é justamente o cenário para o qual o modelo pequeno foi escolhido aqui.
    """
    return SearchPlan(
        queries=[query],
        priority_domains=[],
        sector_hint=None,
        max_companies=max_companies,
    )


def make_plan_search(
    deps: NodeDeps,
) -> Callable[[RadarState], Coroutine[Any, Any, dict[str, Any]]]:
    @node_guard("search_planner", company_key=None)
    async def plan_search(state: RadarState) -> dict[str, Any]:
        query = (state.get("query") or "").strip()
        max_companies = state.get("max_companies") or DEFAULT_MAX_COMPANIES
        if not query:
            raise ValueError("Execução sem `query`: não há o que planejar.")

        try:
            plano = await deps.run_llm(
                LLMTask.SEARCH_PLANNER,
                LLMSearchPlan,
                user_query=query,
                max_queries=DEFAULT_MAX_QUERIES,
            )
        except Exception as exc:  # noqa: BLE001 - degradar aqui é melhor que abortar
            log.warning("planner_degradado", erro=str(exc)[:200])
            return {
                "search_plan": _plano_de_emergencia(query, max_companies),
                "failures": [falha("search_planner", exc, recoverable=True)],
            }

        # Prioridade 1 primeiro: `dedupe_by_domain` mantém a primeira ocorrência
        # de cada domínio, então a ordem das queries decide qual resultado sobrevive.
        ordenadas = sorted(plano.queries, key=lambda q: q.priority)

        log.info(
            "plano_de_busca",
            intencao=plano.interpreted_intent[:120],
            queries=len(ordenadas),
            dominios=len(plano.priority_domains),
        )
        return {
            "search_plan": SearchPlan(
                queries=[q.query for q in ordenadas],
                priority_domains=list(plano.priority_domains),
                sector_hint=plano.sector_focus[0] if plano.sector_focus else None,
                max_companies=max_companies,
            )
        }

    return plan_search
