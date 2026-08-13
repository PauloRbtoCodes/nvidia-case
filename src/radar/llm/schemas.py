"""Contratos de entrada/saída que existem só dentro da camada de LLM.

`src/radar/models/` é a fonte da verdade do *domínio* (empresa, evidência, score,
recomendação). Plano de busca e auditoria de evidência não são estado de domínio:
são artefatos intermediários de dois agentes. Ficam aqui para não inflar a
camada congelada — se o `RadarState` do grafo passar a persistir qualquer um dos
dois, eles devem ser promovidos para `models/` em vez de duplicados.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from radar.models.scoring import AxisScore, DefensibilityScore, TCOEstimate


class SignalType(StrEnum):
    """Que tipo de sinal a query persegue — o Scraper prioriza por aqui."""

    DESCOBERTA = "descoberta"
    """Achar empresas que ainda não conhecemos."""

    STACK = "stack"
    STACK_HIRING = "stack_hiring"
    """Vaga técnica: a fonte mais honesta de stack, marketing esconde."""

    FUNDING = "funding"
    CLIENTES = "clientes"
    DADOS_PROPRIETARIOS = "dados_proprietarios"


class SearchQuery(BaseModel):
    """Uma query pronta para a Search API, com o porquê explícito."""

    query: str = Field(description="String literal a enviar para a Search API, com operadores.")
    signal_type: SignalType
    rationale: str = Field(description="Qual sinal esta query pretende revelar.")
    target_domain: str | None = Field(
        default=None, description="Domínio do operador site:, quando houver."
    )
    priority: int = Field(ge=1, le=5, description="1 = executar primeiro.")


class SearchPlan(BaseModel):
    """Saída do Search Planner Agent."""

    interpreted_intent: str = Field(description="Como o agente entendeu o pedido do usuário.")
    sector_focus: list[str] = Field(default_factory=list)
    queries: list[SearchQuery] = Field(min_length=3)
    priority_domains: list[str] = Field(
        default_factory=list, description="Diretórios e portais a raspar antes dos demais."
    )
    exclusions: list[str] = Field(
        default_factory=list, description="Termos que trazem ruído e devem ser evitados."
    )


class GroundingVerdict(StrEnum):
    SUPPORTED = "supported"
    """O trecho citado sustenta literalmente a afirmação."""

    PARAPHRASED = "paraphrased"
    """O excerpt foi reescrito pelo modelo — perde a função de prova."""

    UNSUPPORTED = "unsupported"
    """Nenhum trecho sustenta o campo. O campo deve ser anulado."""

    STALE = "stale"
    """Sustentado, mas por fonte antiga demais para sinal técnico."""


class FieldAudit(BaseModel):
    field_path: str = Field(description="ex.: inference_provider, tech_signals[2]")
    verdict: GroundingVerdict
    explanation: str
    offending_excerpt: str | None = Field(
        default=None, description="Trecho problemático, quando o veredito não é supported."
    )


class EvidenceAudit(BaseModel):
    """Saída do Evidence Validator Agent.

    Existe porque o Extractor, mesmo instruído, ocasionalmente parafraseia. O
    validador é a segunda barreira: sinaliza o campo em vez de deixar passar uma
    afirmação sem lastro para o briefing.
    """

    company_name: str
    audits: list[FieldAudit] = Field(default_factory=list)
    unsupported_fields: list[str] = Field(
        default_factory=list, description="Campos que devem ser anulados no perfil."
    )
    grounding_ratio: float = Field(
        ge=0.0, le=1.0, description="Fração dos campos auditados com veredito supported."
    )
    requires_recollection: bool = Field(
        description="Se True, o grafo volta ao Scraper com as queries sugeridas."
    )
    suggested_queries: list[str] = Field(
        default_factory=list, description="Buscas para fechar as lacunas encontradas."
    )


class AxisScoreSet(BaseModel):
    """Saída do Defensibility Scorer: só os quatro eixos.

    O LLM não devolve `DefensibilityScore` inteiro de propósito. O TCO é cálculo
    determinístico sobre as premissas de `weights.yaml` e a versão dos pesos é
    fato do sistema, não opinião do modelo — pedir os dois ao LLM abriria espaço
    para número inventado exatamente onde o projeto promete auditabilidade.
    """

    company_name: str
    axes: list[AxisScore] = Field(min_length=4, max_length=4)

    def to_defensibility_score(
        self, *, weights_version: str, tco: list[TCOEstimate] | None = None
    ) -> DefensibilityScore:
        """Fecha o contrato de domínio com os pedaços calculados fora do LLM."""
        return DefensibilityScore(
            company_name=self.company_name,
            axes=self.axes,
            tco=list(tco or []),
            weights_version=weights_version,
        )
