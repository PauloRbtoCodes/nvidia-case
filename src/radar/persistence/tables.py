"""Esquema relacional derivado de `radar.models`.

Regra do projeto: `src/radar/models/` é a fonte da verdade. Este módulo é uma
*projeção* daqueles contratos Pydantic em tabelas — nunca o contrário. Quando um
campo muda lá, muda aqui; quando divergem, o Pydantic vence.

Três decisões estruturais valem explicação, porque não são óbvias no diff:

1. **Histórico versionado, não linha mutável.** `classifications`,
   `defensibility_scores` e `priority_assessments` são séries temporais: a mesma
   startup é reavaliada quando os pesos mudam ou quando novas evidências chegam.
   Sobrescrever destruiria a capacidade de mostrar "esta empresa era vulnerável
   em março e deixou de ser em julho" — que é o argumento comercial mais forte
   que o radar consegue produzir. Cada avaliação insere uma linha nova, carimbada
   com `created_at` e `weights_version`.

2. **Evidência é entidade canônica, referenciada por hash.** Os campos inferidos
   (`EvidenceBackedField`) vão para JSON guardando `evidence_hashes` em vez do
   trecho inteiro. Assim o texto literal existe em exatamente um lugar — a tabela
   `evidences`, com UNIQUE em `content_hash` — e a mesma citação recuperada por
   três caminhos diferentes não vira três verdades ligeiramente distintas.

3. **Enums viajam como texto, não como tipo nativo do Postgres.** Adicionar um
   valor a `Stage` ou `SourceKind` não pode exigir `ALTER TYPE` numa migration.
   A validação continua existindo — acontece no Pydantic, na fronteira de
   entrada, que é onde ela é útil.

Colunas denormalizadas (`total`, `commoditization_risk`, `global_confidence`)
duplicam propriedades computadas do Pydantic de propósito: a fila de prioridade
precisa de `ORDER BY` em SQL, e não dá para ordenar por `@computed_field`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

#: JSONB no Postgres, JSON puro em qualquer outro dialeto. Existe para que o mesmo
#: esquema suba em SQLite nos testes — sem Docker na máquina de desenvolvimento,
#: um esquema que só nasce no Postgres é um esquema que ninguém testa.
JSONVariant = JSON().with_variant(JSONB(astext_type=Text()), "postgresql")

#: Nomes determinísticos de constraint. Sem isso, o Alembic gera migrations com
#: nomes inventados pelo banco e o `downgrade` quebra em ambientes diferentes.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> uuid.UUID:
    return uuid.uuid4()


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    type_annotation_map = {  # noqa: RUF012 - contrato do SQLAlchemy, não estado mutável
        dict[str, Any]: JSONVariant,
        list[Any]: JSONVariant,
    }


class TimestampedMixin:
    """`created_at` em toda tabela porque toda linha aqui é um fato datado.

    Diagnóstico de startup envelhece: sem saber *quando* a evidência foi coletada,
    não dá para dizer se o retrato ainda vale.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )


class Company(TimestampedMixin, Base):
    """Identidade da startup + os campos inferidos pelo Extractor.

    `normalized_name` e `normalized_website` existem para o upsert: a mesma
    empresa aparece na busca como "Acme", "Acme Tecnologia Ltda" e
    "https://www.acme.com.br/" — três caminhos, uma startup. Sem chave
    normalizada, a fila de prioridade do gerente vira uma lista com repetição.
    """

    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_id)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    website: Mapped[str | None] = mapped_column(String(500))
    normalized_website: Mapped[str | None] = mapped_column(String(500), unique=True)

    description: Mapped[str | None] = mapped_column(Text)
    founded_year: Mapped[int | None] = mapped_column(Integer)
    hq_city: Mapped[str | None] = mapped_column(String(120))
    hq_state: Mapped[str | None] = mapped_column(String(2))

    stage: Mapped[str] = mapped_column(String(32), nullable=False, default="desconhecido")
    headcount_estimate: Mapped[int | None] = mapped_column(Integer)

    # Campos inferidos: `{"value": ..., "reasoning": ..., "evidence_hashes": [...]}`.
    # JSON porque nenhum deles é filtrado relacionalmente — a UI lê o perfil inteiro.
    sector: Mapped[dict[str, Any] | None] = mapped_column(JSONVariant)
    target_market: Mapped[dict[str, Any] | None] = mapped_column(JSONVariant)
    ai_use_description: Mapped[dict[str, Any] | None] = mapped_column(JSONVariant)
    inference_provider: Mapped[dict[str, Any] | None] = mapped_column(JSONVariant)
    proprietary_data_claim: Mapped[dict[str, Any] | None] = mapped_column(JSONVariant)
    named_integrations: Mapped[dict[str, Any] | None] = mapped_column(JSONVariant)
    enterprise_customers: Mapped[dict[str, Any] | None] = mapped_column(JSONVariant)

    # Coleções aninhadas: estrutura irregular, consultadas sempre junto do perfil.
    tech_signals: Mapped[list[Any]] = mapped_column(JSONVariant, default=list)
    founders: Mapped[list[Any]] = mapped_column(JSONVariant, default=list)
    funding_rounds: Mapped[list[Any]] = mapped_column(JSONVariant, default=list)
    open_engineering_roles: Mapped[list[Any]] = mapped_column(JSONVariant, default=list)
    source_urls: Mapped[list[Any]] = mapped_column(JSONVariant, default=list)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    evidences: Mapped[list[Evidence]] = relationship(
        back_populates="company", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        Index("ix_companies_name", "name"),
        Index("ix_companies_website", "website"),
    )


class Evidence(TimestampedMixin, Base):
    """Trecho literal com procedência. Toda afirmação do sistema aponta para cá.

    O UNIQUE em `content_hash` é o mecanismo de dedupe: `hash(url|excerpt)`, o
    mesmo cálculo do Pydantic. Descobrir a mesma frase pelo site e pelo blog não
    pode inflar artificialmente a confiança de um eixo — evidências repetidas
    contadas como independentes fabricariam certeza que não existe.
    """

    __tablename__ = "evidences"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_id)
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )

    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="outro")
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[str | None] = mapped_column(Text)

    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    company: Mapped[Company] = relationship(back_populates="evidences")

    __table_args__ = (Index("ix_evidences_company_id", "company_id"),)


class Classification(TimestampedMixin, Base):
    """Saída do Classifier. Série temporal: modelo e prompt mudam, o veredito também.

    Guardar `model_used` e `prompt_version` é o que permite responder "esta
    classificação saiu de qual versão do sistema?" quando a avaliação manual
    discordar — sem isso, não há como medir regressão do classificador.
    """

    __tablename__ = "classifications"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_id)
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )

    maturity: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)

    model_used: Mapped[str | None] = mapped_column(String(120))
    prompt_version: Mapped[str | None] = mapped_column(String(60))

    evidence_hashes: Mapped[list[Any]] = mapped_column(JSONVariant, default=list)

    __table_args__ = (
        Index("ix_classifications_company_id", "company_id"),
        Index("ix_classifications_company_created", "company_id", "created_at"),
    )


class DefensibilityScore(TimestampedMixin, Base):
    """Uma execução do Defensibility Radar.

    `weights_version` é o campo mais importante da tabela. Os pesos dos eixos são
    hipóteses a calibrar contra as startups rotuladas à mão; sem registrar qual
    versão produziu cada linha, recalibrar significaria ou re-raspar tudo ou
    misturar escalas incompatíveis no mesmo gráfico histórico. Com ele, basta
    recalcular a partir dos sinais já guardados.

    Os quatro eixos aparecem duas vezes: achatados em colunas (para `WHERE` e
    `ORDER BY` na fila de prioridade) e completos em `axes_detail` (sinais,
    rationale e hashes de evidência, que só a tela de perfil consome).
    """

    __tablename__ = "defensibility_scores"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_id)
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )

    weights_version: Mapped[str] = mapped_column(String(60), nullable=False)

    total: Mapped[float] = mapped_column(Float, nullable=False)
    commoditization_risk: Mapped[float] = mapped_column(Float, nullable=False)
    global_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    weakest_axis: Mapped[str] = mapped_column(String(32), nullable=False)

    proprietary_data_score: Mapped[float] = mapped_column(Float, nullable=False)
    proprietary_data_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    workflow_depth_score: Mapped[float] = mapped_column(Float, nullable=False)
    workflow_depth_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    stack_ownership_score: Mapped[float] = mapped_column(Float, nullable=False)
    stack_ownership_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    distribution_score: Mapped[float] = mapped_column(Float, nullable=False)
    distribution_confidence: Mapped[float] = mapped_column(Float, nullable=False)

    axes_detail: Mapped[list[Any]] = mapped_column(JSONVariant, default=list)

    tco_estimates: Mapped[list[TCOEstimate]] = relationship(
        back_populates="score", cascade="all, delete-orphan", lazy="selectin"
    )
    priority: Mapped[PriorityAssessment | None] = relationship(
        back_populates="score", cascade="all, delete-orphan", uselist=False, lazy="selectin"
    )

    __table_args__ = (
        Index("ix_defensibility_scores_company_id", "company_id"),
        Index("ix_defensibility_scores_company_created", "company_id", "created_at"),
    )


class TCOEstimate(TimestampedMixin, Base):
    """Cenário de custo (conservador/médio/agressivo) de uma execução de score.

    `assumptions` é JSON e não tabela: premissa é texto para leitura humana no
    briefing, nunca critério de busca. O que importa é que ela viaje *junto* do
    número — um TCO sem premissas visíveis finge uma precisão que não tem.
    """

    __tablename__ = "tco_estimates"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_id)
    score_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("defensibility_scores.id", ondelete="CASCADE"), nullable=False
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )

    scenario: Mapped[str] = mapped_column(String(20), nullable=False)
    monthly_tokens_estimate: Mapped[int] = mapped_column(Integer, nullable=False)

    current_provider: Mapped[str | None] = mapped_column(String(120))
    current_monthly_usd: Mapped[float] = mapped_column(Float, nullable=False)
    nvidia_stack_monthly_usd: Mapped[float] = mapped_column(Float, nullable=False)
    gpu_assumption: Mapped[str] = mapped_column(String(255), nullable=False)

    assumptions: Mapped[list[Any]] = mapped_column(JSONVariant, default=list)
    weights_version: Mapped[str] = mapped_column(String(60), nullable=False)

    score: Mapped[DefensibilityScore] = relationship(back_populates="tco_estimates")

    __table_args__ = (
        UniqueConstraint("score_id", "scenario", name="uq_tco_estimates_score_scenario"),
        Index("ix_tco_estimates_company_id", "company_id"),
    )


class PriorityAssessment(TimestampedMixin, Base):
    """Fila de trabalho do gerente: urgência = risco × capacidade de agir.

    Tabela separada do score porque responde a outra pergunta — o score diz
    "quão exposta"; isto diz "o que fazer nesta semana". A relação 1:1 com a
    execução do score (UNIQUE em `score_id`) mantém as duas coisas coerentes:
    uma priorização sempre pertence à avaliação que a originou.
    """

    __tablename__ = "priority_assessments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_id)
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    score_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("defensibility_scores.id", ondelete="CASCADE"), unique=True
    )

    bucket: Mapped[str] = mapped_column(String(32), nullable=False)
    urgency: Mapped[float] = mapped_column(Float, nullable=False)
    capacity_to_act: Mapped[float] = mapped_column(Float, nullable=False)
    capacity_rationale: Mapped[str] = mapped_column(Text, nullable=False)
    recommended_next_step: Mapped[str] = mapped_column(Text, nullable=False)

    score: Mapped[DefensibilityScore | None] = relationship(back_populates="priority")

    __table_args__ = (
        Index("ix_priority_assessments_company_id", "company_id"),
        Index("ix_priority_assessments_urgency", "urgency"),
    )


class Briefing(TimestampedMixin, Base):
    """Relatório executivo renderizado, com o markdown já pronto para exportar.

    Guardamos o texto final e não só os insumos: o briefing é o artefato que vai
    para a reunião, e precisa ser idêntico ao que o gerente leu — mesmo que os
    pesos sejam recalibrados depois.
    """

    __tablename__ = "briefings"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_id)
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    score_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("defensibility_scores.id", ondelete="SET NULL")
    )

    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    maturity: Mapped[str] = mapped_column(String(32), nullable=False)
    executive_summary: Mapped[str] = mapped_column(Text, nullable=False)
    moat_plan: Mapped[str] = mapped_column(Text, nullable=False)
    inception_fit: Mapped[str | None] = mapped_column(Text)

    conversation_starters: Mapped[list[Any]] = mapped_column(JSONVariant, default=list)
    caveats: Mapped[list[Any]] = mapped_column(JSONVariant, default=list)

    markdown: Mapped[str | None] = mapped_column(Text)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    recommendations: Mapped[list[Recommendation]] = relationship(
        back_populates="briefing", lazy="selectin", order_by="Recommendation.position"
    )

    __table_args__ = (
        Index("ix_briefings_company_id", "company_id"),
        Index("ix_briefings_company_created", "company_id", "created_at"),
    )


class Recommendation(TimestampedMixin, Base):
    """Tecnologia NVIDIA sugerida, sempre amarrada ao eixo que ela endereça.

    `addresses_axis` não é metadado decorativo: é o que torna a recomendação
    causal em vez de associativa. Guardá-lo permite auditar, meses depois, se o
    motor recomendou Triton porque o eixo de stack estava fraco ou porque o LLM
    gostou do nome.

    `briefing_id` é opcional porque o grafo produz recomendações antes de
    redigir o briefing — e uma execução pode falhar no meio sem perder o trabalho.
    """

    __tablename__ = "recommendations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_id)
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    briefing_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("briefings.id", ondelete="CASCADE")
    )

    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    technology: Mapped[str] = mapped_column(String(120), nullable=False)
    addresses_axis: Mapped[str] = mapped_column(String(32), nullable=False)

    technical_rationale: Mapped[str] = mapped_column(Text, nullable=False)
    business_rationale: Mapped[str] = mapped_column(Text, nullable=False)

    priority: Mapped[str] = mapped_column(String(16), nullable=False)
    complexity: Mapped[str] = mapped_column(String(16), nullable=False)
    next_action: Mapped[str] = mapped_column(Text, nullable=False)

    briefing: Mapped[Briefing | None] = relationship(back_populates="recommendations")
    kb_citations: Mapped[list[RecommendationKBCitation]] = relationship(
        back_populates="recommendation",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="RecommendationKBCitation.position",
    )
    evidence_links: Mapped[list[RecommendationEvidence]] = relationship(
        back_populates="recommendation", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        Index("ix_recommendations_company_id", "company_id"),
        Index("ix_recommendations_briefing_id", "briefing_id"),
    )


class RecommendationEvidence(Base):
    """Associação recomendação ↔ evidência da startup.

    Muitos-para-muitos de verdade: a mesma vaga de MLOps sustenta a recomendação
    de Triton e a de TensorRT-LLM. Apontar para a linha canônica de `evidences`
    (em vez de copiar o trecho) é o que garante que clicar no score na UI leve
    sempre à mesma URL.
    """

    __tablename__ = "recommendation_evidences"

    recommendation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recommendations.id", ondelete="CASCADE"), primary_key=True
    )
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidences.id", ondelete="CASCADE"), primary_key=True
    )

    recommendation: Mapped[Recommendation] = relationship(back_populates="evidence_links")
    evidence: Mapped[Evidence] = relationship(lazy="selectin")


class RecommendationKBCitation(Base):
    """Trecho da KB NVIDIA que fundamenta a recomendação, com os scores do RAG.

    Tabela própria e não JSON porque estes números respondem a uma pergunta
    recorrente de avaliação: o rerank do Cohere está de fato reordenando algo, ou
    o RRF já entregava o mesmo top-5? Guardar `rrf_score` e `rerank_score` lado a
    lado torna isso uma query, não um experimento novo.

    O trecho é copiado e não referenciado: o chunk vive no Qdrant, que é um
    índice reconstruível. Uma citação precisa sobreviver à reingestão da KB.
    """

    __tablename__ = "recommendation_kb_citations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_new_id)
    recommendation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recommendations.id", ondelete="CASCADE"), nullable=False
    )

    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    text: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    source_title: Mapped[str | None] = mapped_column(String(500))
    technology: Mapped[str | None] = mapped_column(String(120))

    dense_score: Mapped[float | None] = mapped_column(Float)
    bm25_score: Mapped[float | None] = mapped_column(Float)
    rrf_score: Mapped[float | None] = mapped_column(Float)
    rerank_score: Mapped[float | None] = mapped_column(Float)

    recommendation: Mapped[Recommendation] = relationship(back_populates="kb_citations")

    __table_args__ = (Index("ix_recommendation_kb_citations_rec_id", "recommendation_id"),)


__all__ = [
    "Base",
    "Briefing",
    "Classification",
    "Company",
    "DefensibilityScore",
    "Evidence",
    "JSONVariant",
    "PriorityAssessment",
    "Recommendation",
    "RecommendationEvidence",
    "RecommendationKBCitation",
    "TCOEstimate",
]
