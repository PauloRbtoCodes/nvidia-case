"""Camada de LLM: cliente NIM, registry de modelos e prompts versionados.

Ponto de entrada único dos nós do grafo — nenhum nó deve importar
`langchain_nvidia_ai_endpoints` diretamente, para que trocar de provedor ou de
modelo continue sendo uma mudança local.
"""

from radar.llm.client import NIMClient
from radar.llm.errors import (
    LLMError,
    PromptNotFoundError,
    PromptRenderError,
    RateLimitError,
    SchemaValidationError,
)
from radar.llm.model_registry import (
    DEFAULT_TASK_TIERS,
    LLMTask,
    ModelRegistry,
    ModelTier,
)
from radar.llm.observability import NullObserver, build_observer
from radar.llm.registry import (
    PromptRegistry,
    PromptTemplate,
    RenderedPrompt,
    get_prompt_registry,
)
from radar.llm.rubric import signals_rubric, weights_version
from radar.llm.schemas import (
    AxisScoreSet,
    EvidenceAudit,
    FieldAudit,
    GroundingVerdict,
    SearchPlan,
    SearchQuery,
    SignalType,
)

__all__ = [
    "AxisScoreSet",
    "DEFAULT_TASK_TIERS",
    "EvidenceAudit",
    "FieldAudit",
    "GroundingVerdict",
    "LLMError",
    "LLMTask",
    "ModelRegistry",
    "ModelTier",
    "NIMClient",
    "NullObserver",
    "PromptNotFoundError",
    "PromptRegistry",
    "PromptRenderError",
    "PromptTemplate",
    "RateLimitError",
    "RenderedPrompt",
    "SchemaValidationError",
    "SearchPlan",
    "SearchQuery",
    "SignalType",
    "build_observer",
    "get_prompt_registry",
    "signals_rubric",
    "weights_version",
]
