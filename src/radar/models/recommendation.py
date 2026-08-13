"""Saída final: recomendações de tecnologia NVIDIA e briefing executivo."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, HttpUrl, model_validator

from radar.models.company import AIMaturity
from radar.models.evidence import Evidence
from radar.models.scoring import DefensibilityAxis, DefensibilityScore, PriorityAssessment


class Priority(StrEnum):
    ALTA = "alta"
    MEDIA = "media"
    BAIXA = "baixa"


class Complexity(StrEnum):
    BAIXA = "baixa"
    """Dias. Trocar endpoint para NIM, testar um modelo do API Catalog."""

    MEDIA = "media"
    """Semanas. Subir Triton, converter modelo com TensorRT-LLM."""

    ALTA = "alta"
    """Meses. Fine-tuning com NeMo, pipeline de curadoria de dados própria."""


class RetrievedChunk(BaseModel):
    """Trecho da KB NVIDIA recuperado pelo RAG, após reranking."""

    text: str
    source_url: HttpUrl
    source_title: str | None = None
    technology: str | None = None

    dense_score: float | None = None
    bm25_score: float | None = None
    rrf_score: float | None = None
    rerank_score: float | None = Field(default=None, description="Cohere Rerank — ordenação final.")


class Recommendation(BaseModel):
    """Uma tecnologia NVIDIA recomendada, no formato exigido pelo enunciado."""

    technology: str
    addresses_axis: DefensibilityAxis = Field(
        description="Qual gap do Defensibility Score esta tecnologia endereça. "
        "Torna a recomendação causal em vez de associativa."
    )

    technical_rationale: str
    business_rationale: str

    priority: Priority
    complexity: Complexity
    next_action: str = Field(
        description="Ação concreta para o time NVIDIA — não 'agendar reunião'."
    )

    kb_citations: list[RetrievedChunk] = Field(
        default_factory=list, description="Trechos da KB NVIDIA que fundamentam a recomendação."
    )
    company_evidences: list[Evidence] = Field(
        default_factory=list, description="Sinais da startup que motivaram a recomendação."
    )

    @model_validator(mode="after")
    def _require_grounding(self) -> Recommendation:
        """Guardrail: recomendação sem fundamento na KB é bloqueada, não degradada.

        Três recomendações fundamentadas valem mais numa reunião do que oito
        plausíveis — e uma alucinação sobre o que o Triton faz, diante de um
        founder técnico, custa a credibilidade do programa inteiro.
        """
        if not self.kb_citations:
            raise ValueError(
                f"Recomendação de '{self.technology}' sem citação da base NVIDIA. "
                "Recupere evidência da KB ou descarte a recomendação."
            )
        return self


class Briefing(BaseModel):
    """Relatório executivo para o gerente de Startups & VCs (Briefing Agent)."""

    company_name: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    executive_summary: str = Field(description="Três a cinco frases. É o que será lido de fato.")
    maturity: AIMaturity
    defensibility: DefensibilityScore
    priority: PriorityAssessment
    recommendations: list[Recommendation] = Field(default_factory=list)

    moat_plan: str = Field(
        description="O caminho da startup para deixar de ser substituível, e onde a NVIDIA "
        "cobre a parte técnica dele. É o ângulo que inverte a conversa: não "
        "'você é um wrapper', mas 'aqui está a rota de saída'."
    )
    conversation_starters: list[str] = Field(
        default_factory=list,
        description="Perguntas técnicas específicas desta startup, para o primeiro contato.",
    )

    inception_fit: str | None = Field(
        default=None, description="Quais benefícios do Inception são relevantes para este perfil."
    )
    caveats: list[str] = Field(
        default_factory=list,
        description="Limites do diagnóstico: lacunas de evidência, premissas do TCO, dados "
        "antigos. Vai no relatório, não em nota de rodapé — quem vai à reunião precisa "
        "saber o que o sistema não sabe.",
    )

    markdown: str | None = None

    @property
    def total_citations(self) -> int:
        return sum(len(r.kb_citations) for r in self.recommendations)
