"""Startup Classifier: ai_native / ai_enabled / non_ai / indeterminado.

Este nó é o portão de custo do subgrafo. `non_ai` encerra o processamento antes
do Scorer, do RAG e do Briefing — três chamadas de modelo de raciocínio e uma
busca vetorial que não teriam a quem servir.

`indeterminado` **não** encerra. É a distinção que o prompt insiste e que o
resto do sistema depende: "não encontramos sinal" segue para o score, sai com
confiança baixa e cai no bucket `MONITORAR`. Tratá-lo como `non_ai` faria a
startup discreta desaparecer da fila — exatamente o viés que o projeto existe
para não cometer.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

import structlog

from radar.graph.nodes.base import dump, node_guard
from radar.graph.nodes.deps import NodeDeps
from radar.graph.state import CompanyState
from radar.llm.model_registry import LLMTask
from radar.models.company import Classification

log = structlog.get_logger(__name__)

PROMPT_VERSION = "v1"


def make_classify_company(
    deps: NodeDeps,
) -> Callable[[CompanyState], Coroutine[Any, Any, dict[str, Any]]]:
    @node_guard("classifier")
    async def classify_company(state: CompanyState) -> dict[str, Any]:
        profile = state.get("profile")
        if profile is None:
            raise ValueError("Classificação sem perfil.")

        classificacao = await deps.run_llm(
            LLMTask.CLASSIFIER,
            Classification,
            company_profile_json=dump(profile),
        )

        # Modelo e versão do prompt são gravados junto da classificação porque a
        # avaliação da semana 4 compara redações e modelos contra os ~50 labels
        # manuais; sem esses dois campos, o resultado não é atribuível a nada.
        carimbada = classificacao.model_copy(
            update={
                "model_used": deps.llm.models.model_for(LLMTask.CLASSIFIER),
                "prompt_version": PROMPT_VERSION,
            }
        )

        log.info(
            "classificacao",
            empresa=profile.name,
            maturidade=carimbada.maturity.value,
            confianca=carimbada.confidence,
        )
        return {"classification": carimbada}

    return classify_company
