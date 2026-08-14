"""Montagem dos dois grafos e das arestas condicionais.

```
externo:   plan → discover ──Send(N)──▶ process_company ──▶ consolidate ──▶ END
                     │                       (subgrafo)
                     └──(nada encontrado)────────────────▶ consolidate

subgrafo:  collect → extract → validate ─┬─(lacuna e orçamento)──▶ collect
                                         └─▶ classify ─┬─(non_ai)─▶ END
                                                       └─▶ score → rag
                                                              → recommend → briefing → END
```

Duas decisões estruturais, ambas sobre isolamento de falha:

1. **O subgrafo é invocado dentro de um nó comum do grafo externo**, e não
   plugado direto como nó. Assim o mapeamento `CompanyState → RadarState` é
   explícito (só `company_results`, `briefings`, `skipped` e `failures` sobem), e
   uma empresa que estoure não escreve chaves inesperadas no estado do lote.
2. **Todo roteador testa o que precisa existir e sai para `END` quando falta.**
   Um nó que falhou registrou a falha e devolveu estado incompleto; o roteador
   seguinte não tem o que fazer com isso, e insistir só produziria uma segunda
   falha derivada, mais confusa que a primeira no relatório.
"""

from __future__ import annotations

from typing import Any

import structlog
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from radar.graph.nodes import (
    NodeDeps,
    falha,
    make_classify_company,
    make_collect_sources,
    make_discover_companies,
    make_extract_profile,
    make_plan_search,
    make_recommend_technologies,
    make_retrieve_kb,
    make_score_defensibility,
    make_validate_evidence,
    make_write_briefing,
)
from radar.graph.state import CompanyState, RadarState
from radar.models.company import AIMaturity

log = structlog.get_logger(__name__)


# --------------------------------------------------------------------------- #
# Roteadores do subgrafo
# --------------------------------------------------------------------------- #
def route_after_collect(state: CompanyState) -> str:
    """Sem página coletada não há o que extrair.

    Acontece de verdade: site atrás de Cloudflare, domínio parqueado, `robots.txt`
    proibindo tudo. A empresa sai do lote com a falha registrada, e o relatório
    consolidado mostra a lacuna em vez de um perfil inventado a partir do nada.
    """
    return "extract" if state.get("raw_pages") else END


def route_after_extract(state: CompanyState) -> str:
    return "validate" if state.get("profile") is not None else END


def route_after_validate(state: CompanyState) -> str:
    """O único ciclo do subgrafo. O teto já foi aplicado pelo validador."""
    if state.get("profile") is None:
        return END
    return "collect" if state.get("requires_recollection") else "classify"


def route_after_classify(state: CompanyState) -> str:
    """`non_ai` sai aqui; `indeterminado` segue.

    A distinção é o invariante de justiça do projeto: sem sinal de IA encontrado
    é `indeterminado`, e uma startup discreta precisa chegar ao score (com
    confiança baixa, no bucket `MONITORAR`) em vez de sumir da fila.
    """
    classificacao = state.get("classification")
    if classificacao is None:
        return END
    return END if classificacao.maturity is AIMaturity.NON_AI else "score"


def route_after_score(state: CompanyState) -> str:
    return "rag" if state.get("defensibility") is not None else END


def route_after_rag(state: CompanyState) -> str:
    """Segue para o recomendador mesmo sem chunks.

    O nó de recomendação é quem aplica o guardrail (lista vazia + nota de
    bloqueio) e o briefing sai assim mesmo: diagnóstico com lacuna declarada é
    útil para o gerente; nenhum documento não é.
    """
    return "recommend" if state.get("defensibility") is not None else END


def route_after_recommend(state: CompanyState) -> str:
    return "briefing" if state.get("defensibility") is not None else END


# --------------------------------------------------------------------------- #
# Subgrafo por empresa
# --------------------------------------------------------------------------- #
def build_company_graph(deps: NodeDeps) -> Any:
    """Pipeline de diagnóstico de uma única startup."""
    grafo = StateGraph(CompanyState)

    grafo.add_node("collect", make_collect_sources(deps))
    grafo.add_node("extract", make_extract_profile(deps))
    grafo.add_node("validate", make_validate_evidence(deps))
    grafo.add_node("classify", make_classify_company(deps))
    grafo.add_node("score", make_score_defensibility(deps))
    grafo.add_node("rag", make_retrieve_kb(deps))
    grafo.add_node("recommend", make_recommend_technologies(deps))
    grafo.add_node("briefing", make_write_briefing(deps))

    grafo.add_edge(START, "collect")
    grafo.add_conditional_edges("collect", route_after_collect, ["extract", END])
    grafo.add_conditional_edges("extract", route_after_extract, ["validate", END])
    grafo.add_conditional_edges("validate", route_after_validate, ["collect", "classify", END])
    grafo.add_conditional_edges("classify", route_after_classify, ["score", END])
    grafo.add_conditional_edges("score", route_after_score, ["rag", END])
    grafo.add_conditional_edges("rag", route_after_rag, ["recommend", END])
    grafo.add_conditional_edges("recommend", route_after_recommend, ["briefing", END])
    grafo.add_edge("briefing", END)

    return grafo.compile()


# --------------------------------------------------------------------------- #
# Grafo externo
# --------------------------------------------------------------------------- #
def fan_out_companies(state: RadarState) -> Any:
    """Map-reduce: uma cópia isolada do subgrafo por empresa descoberta.

    `Send` carrega o estado inicial de cada empresa. Cada subgrafo tem seu
    próprio orçamento de retry e sua própria trilha de erro — que é o ponto: uma
    startup cujo site derruba o extrator não pode derrubar o lote.
    """
    descobertos = state.get("discovered") or []
    if not descobertos:
        return "consolidate"

    envios: list[Send] = []
    for empresa in descobertos:
        envios.append(
            Send(
                "process_company",
                {
                    "company_name": empresa.get("company_name") or empresa.get("title") or "?",
                    "seed_urls": [empresa["url"]],
                    "search_plan": state.get("search_plan", {}),
                    "scrape_attempts": 0,
                    "trace_id": state.get("trace_id"),
                },
            )
        )
    return envios


def make_process_company(deps: NodeDeps) -> Any:
    """Executa o subgrafo e traduz sua saída para o estado do lote."""
    subgrafo = build_company_graph(deps)

    async def process_company(state: CompanyState) -> dict[str, Any]:
        nome = state.get("company_name", "?")
        try:
            resultado: CompanyState = await subgrafo.ainvoke(state)
        except Exception as exc:  # noqa: BLE001 - ver docstring do módulo
            log.warning("subgrafo_falhou", empresa=nome, erro=str(exc)[:300])
            return {"failures": [falha("process_company", exc, company=nome)]}

        saida: dict[str, Any] = {
            "company_results": [resultado],
            "failures": list(resultado.get("failures") or []),
        }

        briefing = resultado.get("briefing")
        if briefing is not None:
            saida["briefings"] = [briefing]

        classificacao = resultado.get("classification")
        if classificacao is not None and classificacao.maturity is AIMaturity.NON_AI:
            # Descartada cedo e com motivo: a lacuna no relatório é intencional e
            # explicável, não uma empresa que sumiu sem que ninguém saiba por quê.
            saida["skipped"] = [
                {
                    "company": nome,
                    "reason": "classificada como non_ai",
                    "rationale": classificacao.rationale,
                }
            ]
        elif briefing is None:
            saida["skipped"] = [
                {
                    "company": nome,
                    "reason": "pipeline interrompida antes do briefing",
                    "rationale": "; ".join(
                        f["node"] for f in resultado.get("failures") or []
                    )
                    or "evidência insuficiente",
                }
            ]

        return saida

    return process_company


def make_consolidate(deps: NodeDeps) -> Any:
    """Ponto de junção do fan-out: ordena a fila e fecha os traces.

    A ordenação acontece aqui e não na API porque é a conclusão do trabalho do
    grafo — o produto para o gerente é uma *fila*, e devolver as empresas na
    ordem em que as buscas voltaram desperdiçaria o cálculo de prioridade.
    """

    async def consolidate(state: RadarState) -> dict[str, Any]:
        briefings = state.get("briefings") or []
        ordenados = sorted(briefings, key=lambda b: b.priority.urgency, reverse=True)

        log.info(
            "lote_concluido",
            briefings=len(ordenados),
            descartadas=len(state.get("skipped") or []),
            falhas=len(state.get("failures") or []),
        )
        # Traces do Langfuse são bufferizados; sem descarregar aqui, uma execução
        # curta termina antes de o cliente enviar e o lote some da observabilidade.
        deps.llm.flush()

        # Índice, não cópia: `briefings` já tem o reducer `operator.add` do
        # fan-out, e devolver a lista ordenada nessa chave a concatenaria consigo
        # mesma. A fila é a ordem; os documentos continuam num lugar só.
        return {"queue": [b.company_name for b in ordenados]}

    return consolidate


def build_radar_graph(deps: NodeDeps) -> Any:
    """Grafo externo: planeja, descobre, distribui e consolida."""
    grafo = StateGraph(RadarState)

    grafo.add_node("plan", make_plan_search(deps))
    grafo.add_node("discover", make_discover_companies(deps))
    grafo.add_node("process_company", make_process_company(deps))
    grafo.add_node("consolidate", make_consolidate(deps))

    grafo.add_edge(START, "plan")
    grafo.add_edge("plan", "discover")
    grafo.add_conditional_edges(
        "discover", fan_out_companies, ["process_company", "consolidate"]
    )
    grafo.add_edge("process_company", "consolidate")
    grafo.add_edge("consolidate", END)

    return grafo.compile()
