"""Registry de modelos por tarefa.

Motivo de existir: a cota gratuita do NIM é o recurso mais escasso do projeto.
Planejar buscas ou auditar se um trecho sustenta uma afirmação são tarefas
mecânicas que um modelo pequeno resolve; extrair perfil estruturado, classificar
e pontuar defensibilidade são tarefas de raciocínio onde o modelo grande paga
por si. Amarrar a escolha à *tarefa* (e não ao ponto de chamada) mantém essa
decisão em um lugar só e auditável.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from radar.config import Settings, get_settings

#: Fallback do modelo pequeno. A fonte real é `Settings.nim_fast_model`
#: (variável `NIM_FAST_MODEL`); este valor só entra se a configuração vier vazia.
#: Espelha o default de `Settings.nim_fast_model` em `radar/config.py` —
#: mesmo motivo lá: medido contra a API real em 2026-09-10, o "lightning"
#: respondia em 34-50s (com timeout) contra 0.7-4.8s do "super" no mesmo
#: prompt, e por ser a primeira chamada do grafo isso travava a varredura
#: inteira. Os dois lugares precisam mudar juntos até o endpoint do lightning
#: voltar a responder rápido.
DEFAULT_FAST_MODEL = "nvidia/nemotron-3-super-120b-a12b"


class ModelTier(StrEnum):
    """Duas faixas bastam: mais que isso vira tuning sem dado que o justifique."""

    FAST = "fast"
    REASONING = "reasoning"


class LLMTask(StrEnum):
    """Uma tarefa por agente do grafo. O valor casa com o nome do arquivo de prompt."""

    SEARCH_PLANNER = "search_planner"
    EXTRACTOR = "extractor"
    CLASSIFIER = "classifier"
    EVIDENCE_VALIDATOR = "evidence_validator"
    DEFENSIBILITY_SCORER = "defensibility_scorer"
    RECOMMENDER = "recommender"
    BRIEFING = "briefing"


#: Faixa padrão por tarefa.
#:
#: FAST onde o erro é barato e detectável adiante: o planner só gera queries (uma
#: query ruim custa uma busca) e o validador de evidência faz casamento de texto
#: contra a fonte, que é quase mecânico.
#: REASONING onde o erro contamina tudo o que vem depois: extração estruturada,
#: classificação (medida contra os labels manuais), score de defensibilidade,
#: recomendação e briefing — as saídas que chegam ao leitor humano.
DEFAULT_TASK_TIERS: dict[LLMTask, ModelTier] = {
    LLMTask.SEARCH_PLANNER: ModelTier.FAST,
    LLMTask.EXTRACTOR: ModelTier.REASONING,
    LLMTask.CLASSIFIER: ModelTier.REASONING,
    LLMTask.EVIDENCE_VALIDATOR: ModelTier.FAST,
    LLMTask.DEFENSIBILITY_SCORER: ModelTier.REASONING,
    LLMTask.RECOMMENDER: ModelTier.REASONING,
    LLMTask.BRIEFING: ModelTier.REASONING,
}

#: Temperatura por tarefa. Extração e pontuação precisam ser reprodutíveis para
#: a avaliação fazer sentido; o briefing é texto para humano e fica levemente
#: mais solto, senão sai um relatório robótico que ninguém lê.
DEFAULT_TASK_TEMPERATURES: dict[LLMTask, float] = {
    LLMTask.SEARCH_PLANNER: 0.4,
    LLMTask.EXTRACTOR: 0.0,
    LLMTask.CLASSIFIER: 0.0,
    LLMTask.EVIDENCE_VALIDATOR: 0.0,
    LLMTask.DEFENSIBILITY_SCORER: 0.1,
    LLMTask.RECOMMENDER: 0.2,
    LLMTask.BRIEFING: 0.3,
}


@dataclass(frozen=True)
class ModelRegistry:
    """Resolve tarefa → modelo concreto.

    `task_overrides` existe para comparar modelos por agente (ex.: um Nemotron
    maior no scorer, um menor no resto) sem mudança de código.
    """

    fast_model: str = DEFAULT_FAST_MODEL
    reasoning_model: str = "nvidia/nemotron-3-super-120b-a12b"
    task_tiers: dict[LLMTask, ModelTier] = field(default_factory=lambda: dict(DEFAULT_TASK_TIERS))
    task_overrides: dict[LLMTask, str] = field(default_factory=dict)

    @classmethod
    def from_settings(
        cls,
        settings: Settings | None = None,
        *,
        task_overrides: dict[LLMTask, str] | None = None,
    ) -> ModelRegistry:
        settings = settings or get_settings()
        return cls(
            fast_model=settings.nim_fast_model or DEFAULT_FAST_MODEL,
            reasoning_model=settings.nim_chat_model,
            task_overrides=dict(task_overrides or {}),
        )

    def tier_for(self, task: LLMTask) -> ModelTier:
        return self.task_tiers.get(task, ModelTier.REASONING)

    def model_for(self, task: LLMTask) -> str:
        """Override explícito ganha da faixa — é o que permite calibrar por agente."""
        if task in self.task_overrides:
            return self.task_overrides[task]
        if self.tier_for(task) is ModelTier.FAST:
            return self.fast_model
        return self.reasoning_model

    def temperature_for(self, task: LLMTask) -> float:
        return DEFAULT_TASK_TEMPERATURES.get(task, 0.0)
