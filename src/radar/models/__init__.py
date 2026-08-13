"""Contratos Pydantic — fonte da verdade do sistema.

Nós do grafo, tabelas do Postgres e schemas da API derivam destes modelos.
Nada de tipos duplicados divergindo entre camadas.
"""

from radar.models.company import (
    AIMaturity,
    Classification,
    CompanyProfile,
    Founder,
    FundingRound,
    InferenceProvider,
    Stage,
    TechSignal,
)
from radar.models.evidence import (
    SOURCE_TRUST,
    Evidence,
    EvidenceBackedField,
    SourceKind,
)
from radar.models.recommendation import (
    Briefing,
    Complexity,
    Priority,
    Recommendation,
    RetrievedChunk,
)
from radar.models.scoring import (
    AXIS_TO_NVIDIA_FAMILY,
    AXIS_WEIGHTS,
    AxisScore,
    DefensibilityAxis,
    DefensibilityScore,
    PriorityAssessment,
    PriorityBucket,
    TCOEstimate,
    TCOScenario,
)

__all__ = [
    "AIMaturity",
    "AXIS_TO_NVIDIA_FAMILY",
    "AXIS_WEIGHTS",
    "AxisScore",
    "Briefing",
    "Classification",
    "CompanyProfile",
    "Complexity",
    "DefensibilityAxis",
    "DefensibilityScore",
    "Evidence",
    "EvidenceBackedField",
    "Founder",
    "FundingRound",
    "InferenceProvider",
    "Priority",
    "PriorityAssessment",
    "PriorityBucket",
    "Recommendation",
    "RetrievedChunk",
    "SOURCE_TRUST",
    "SourceKind",
    "Stage",
    "TCOEstimate",
    "TCOScenario",
    "TechSignal",
]
