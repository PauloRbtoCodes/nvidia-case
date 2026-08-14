"""Defensibility Scorer + TCO + fila de prioridade.

Nó em três partes, com uma divisão de trabalho deliberada:

- **o LLM pontua os quatro eixos** e só isso (`AxisScoreSet`). Julgar se um
  dataset descrito num blog é fosso ou não é leitura de texto, que é o que um
  modelo faz bem;
- **o TCO é aritmética** sobre `weights.yaml`. Pedir os números ao modelo abriria
  espaço para valor inventado exatamente onde o projeto promete auditabilidade;
- **a fila de prioridade é aritmética** também, e pelo mesmo motivo mais um: ela
  ordena a agenda de uma pessoa, e uma ordem que muda sozinha entre execuções
  não é uma fila, é ruído.

O gate determinístico de tecnologias sai daqui (`candidate_technologies()`), não
do RAG e não do recomendador: é o que torna a recomendação causal — ela vem do
eixo mais fraco ponderado por peso *e* confiança, não de uma regra por setor.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

import structlog

from radar.graph.nodes.base import dump, node_guard
from radar.graph.nodes.deps import NodeDeps
from radar.graph.state import CompanyState
from radar.llm.model_registry import LLMTask
from radar.llm.rubric import signals_rubric
from radar.llm.schemas import AxisScoreSet
from radar.models.scoring import TCOEstimate
from radar.scoring.priority import avaliar_prioridade
from radar.scoring.product import inferir_categoria_produto, inferir_chave_provider
from radar.scoring.tco import estimar_todos_cenarios

log = structlog.get_logger(__name__)


def _calcular_tco(state: CompanyState, deps: NodeDeps) -> list[TCOEstimate]:
    """Os três cenários — ou nenhum, quando comparar não faria sentido.

    Sem provedor de API identificado (a empresa já roda inferência própria, ou
    não sabemos como serve os modelos), a comparação "API vs GPU dedicada" não
    tem lado esquerdo. Produzir os números assim mesmo daria ao gerente uma
    economia projetada contra um custo que ninguém sabe se existe.
    """
    profile = state["profile"]
    provider = inferir_chave_provider(profile)
    if provider is None:
        log.info("tco_omitido", empresa=profile.name, motivo="sem_provedor_de_api_identificado")
        return []

    categoria = inferir_categoria_produto(profile)
    return estimar_todos_cenarios(
        categoria,
        provider=provider,
        headcount=profile.headcount_estimate,
        weights=deps.weights,
    )


def make_score_defensibility(
    deps: NodeDeps,
) -> Callable[[CompanyState], Coroutine[Any, Any, dict[str, Any]]]:
    @node_guard("defensibility_scorer")
    async def score_defensibility(state: CompanyState) -> dict[str, Any]:
        profile = state.get("profile")
        if profile is None:
            raise ValueError("Score sem perfil.")

        conjunto = await deps.run_llm(
            LLMTask.DEFENSIBILITY_SCORER,
            AxisScoreSet,
            company_profile_json=dump(profile),
            classification_json=dump(state.get("classification")),
            signals_rubric=signals_rubric(),
            weights_version=deps.weights.version,
        )

        tco = _calcular_tco(state, deps)
        # O nome vem do perfil, não do modelo: o score é gravado por `company_id`
        # e um nome levemente diferente ("Acme" vs "Acme Tecnologia") criaria uma
        # segunda empresa no relatório consolidado.
        score = conjunto.model_copy(update={"company_name": profile.name}).to_defensibility_score(
            weights_version=deps.weights.version, tco=tco
        )

        classificacao = state.get("classification")
        prioridade = avaliar_prioridade(
            profile,
            score,
            maturity=classificacao.maturity if classificacao else None,
            weights=deps.weights,
            hoje=deps.clock(),
        )

        candidatas = score.candidate_technologies()

        log.info(
            "defensibilidade",
            empresa=profile.name,
            total=score.total,
            risco=score.commoditization_risk,
            confianca=score.global_confidence,
            eixo_fraco=score.weakest_axis.value,
            bucket=prioridade.bucket.value,
            urgencia=prioridade.urgency,
            candidatas=len(candidatas),
        )
        return {
            "defensibility": score,
            "priority": prioridade,
            "candidate_technologies": candidatas,
        }

    return score_defensibility
