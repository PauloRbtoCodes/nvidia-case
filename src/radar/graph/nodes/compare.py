"""Nó `compare`: o gatilho temporal, plugado entre o score e o RAG.

`scoring/delta.py` é aritmética pura e não sabe de onde vêm os dois scores. Este
nó é quem busca o "antes" — pela porta `ScoreHistoryPort`, sem importar
`persistence/` — e roda a comparação.

Fica **entre `score` e `rag`** de propósito:

- o diff precisa estar no estado antes do briefing, porque o talk track abre com
  a evidência nova ("vi que vocês abriram vaga de MLOps pedindo vLLM e Triton");
- e não pode influenciar `candidate_technologies()`, que já saiu pronto do
  scorer — a recomendação continua causal, derivada do eixo mais fraco, não do
  que mudou desde a última vez.

Sem banco (`deps.score_history is None`) ou na primeira vez que a empresa é vista
(`previous_score` devolve `None`), o nó não emite nada e o grafo segue: não ter
com que comparar não é falha. Erro real na leitura do histórico (banco caiu no
meio do lote) vira `NodeFailure` via `node_guard` e também não interrompe o
diagnóstico — o diff é um extra, não um pré-requisito do briefing.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

import structlog

from radar.graph.nodes.base import node_guard
from radar.graph.nodes.deps import NodeDeps
from radar.graph.state import CompanyState
from radar.scoring.delta import comparar_scores

log = structlog.get_logger(__name__)


def make_compare_scores(
    deps: NodeDeps,
) -> Callable[[CompanyState], Coroutine[Any, Any, dict[str, Any]]]:
    @node_guard("compare")
    async def compare_scores(state: CompanyState) -> dict[str, Any]:
        score = state.get("defensibility")
        if score is None:
            raise ValueError("Compare sem score.")

        if deps.score_history is None:
            return {}

        anterior = await asyncio.to_thread(
            deps.score_history.previous_score, score.company_name
        )
        if anterior is None:
            log.info("delta_temporal", empresa=score.company_name, primeira_execucao=True)
            return {}

        delta = comparar_scores(anterior, score)
        manchete = delta.headline_axis
        log.info(
            "delta_temporal",
            empresa=score.company_name,
            mudou=delta.has_changes,
            manchete=manchete.kind.value if manchete else None,
            eixo=manchete.axis.value if manchete else None,
        )
        return {"score_delta": delta}

    return compare_scores
