"""Schemas de entrada e saída da API.

Regra do projeto: `src/radar/models/` é a fonte da verdade. Aqui **não** se
redefine `CompanyProfile`, `DefensibilityScore`, `Recommendation` ou `Briefing` —
eles são reexportados e aninhados como estão. Um `CompanyProfileResponse` que
repetisse os campos divergiria do domínio na primeira mudança, e a divergência
apareceria como campo faltando no frontend semanas depois.

O que existe aqui é o que **não** é domínio:

- corpos de requisição (`SearchRequest`), que são entrada de usuário;
- projeções achatadas para listagem (`QueueItemResponse`), que evitam carregar
  todas as evidências de 200 startups para desenhar uma tabela de quatro colunas;
- envelopes de execução (`SearchResponse`), que descrevem o processo, não a
  empresa.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from radar.models.company import Classification, CompanyProfile
from radar.models.recommendation import Briefing, Recommendation
from radar.models.scoring import DefensibilityScore, PriorityAssessment
from radar.persistence.repositories import QueueItem
from radar.scoring.delta import ScoreDelta

# --------------------------------------------------------------------------- #
# Busca
# --------------------------------------------------------------------------- #


class SearchRequest(BaseModel):
    """Disparo de uma execução do grafo."""

    query: str = Field(
        min_length=3,
        max_length=500,
        description="Pedido em linguagem natural. Ex.: 'startups de IA em saúde no Sudeste'.",
    )
    max_companies: int = Field(
        default=12,
        ge=1,
        le=50,
        description="Teto de empresas processadas. Cada empresa custa várias chamadas de modelo.",
    )


class SearchResponse(BaseModel):
    """Estado de uma execução. Devolvido no POST e no GET de status."""

    id: str
    query: str
    status: str
    created_at: datetime
    finished_at: datetime | None = None
    error: str | None = None

    companies_found: int = 0
    briefings_ready: int = 0
    skipped: int = 0
    failures: int = 0

    queue: list[str] = Field(
        default_factory=list, description="Nomes em ordem decrescente de urgência."
    )
    stream_url: str | None = Field(
        default=None, description="Endpoint SSE com o progresso nó a nó."
    )


class SearchEventResponse(BaseModel):
    """Um evento do stream, no formato do `data:` do SSE."""

    type: str
    node: str | None = None
    company: str | None = None
    detail: str | None = None
    at: datetime


# --------------------------------------------------------------------------- #
# Empresas
# --------------------------------------------------------------------------- #


class QueueItemResponse(BaseModel):
    """Uma linha da fila de prioridade — o read model da tela inicial."""

    company_id: UUID
    company_name: str
    website: str | None = None
    stage: str

    total: float
    commoditization_risk: float
    global_confidence: float
    weakest_axis: str
    weights_version: str
    scored_at: datetime

    bucket: str | None = None
    urgency: float | None = None

    delta: ScoreDelta | None = Field(
        default=None,
        description="O que mudou desde a execução anterior — o gatilho ao lado da "
        "empresa na fila. Ausente quando a empresa só foi avaliada uma vez ou "
        "nada mudou além do ruído.",
    )

    @property
    def is_actionable(self) -> bool:
        return self.global_confidence >= 0.35

    @classmethod
    def from_queue_item(
        cls, item: QueueItem, *, delta: ScoreDelta | None = None
    ) -> QueueItemResponse:
        return cls(
            company_id=item.company_id,
            company_name=item.company_name,
            website=item.website,
            stage=item.stage,
            total=item.total,
            commoditization_risk=item.commoditization_risk,
            global_confidence=item.global_confidence,
            weakest_axis=item.weakest_axis,
            weights_version=item.weights_version,
            scored_at=item.scored_at,
            bucket=item.bucket,
            urgency=item.urgency,
            delta=delta,
        )


class CompanyDetailResponse(BaseModel):
    """Perfil completo com o diagnóstico mais recente.

    Os objetos de domínio entram inteiros. O radar de eixos com evidência
    clicável — o que faz a tela parecer ferramenta e não demo — depende de o
    frontend receber `AxisScore.evidences` com URL e trecho; achatar aqui
    destruiria exatamente essa capacidade.
    """

    company_id: UUID
    profile: CompanyProfile
    classification: Classification | None = None
    defensibility: DefensibilityScore | None = None
    priority: PriorityAssessment | None = None
    recommendations: list[Recommendation] = Field(default_factory=list)
    has_briefing: bool = False

    delta: ScoreDelta | None = Field(
        default=None,
        description="Diff eixo a eixo contra a execução anterior. Alimenta a seção "
        "'o que mudou' do perfil; ausente na primeira avaliação da empresa.",
    )


class BriefingResponse(BaseModel):
    briefing_id: UUID
    company_id: UUID
    briefing: Briefing
    markdown_url: str


class HealthResponse(BaseModel):
    """Diagnóstico de dependências. Espelha o `radar.cli check`, em JSON."""

    status: str
    database: bool
    knowledge_base: bool
    search: bool
    llm: bool
    details: dict[str, Any] = Field(default_factory=dict)
