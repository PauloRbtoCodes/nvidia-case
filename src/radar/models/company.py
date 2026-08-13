"""Perfil da startup — saída do Extractor Agent, entrada do Classifier e do Scorer."""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field, HttpUrl

from radar.models.evidence import Evidence, EvidenceBackedField


class AIMaturity(StrEnum):
    """Classificação pedida no enunciado (Startup Classifier Agent)."""

    AI_NATIVE = "ai_native"
    """IA é o produto: dado proprietário, modelo próprio ou workflow que só existe com IA."""

    AI_ENABLED = "ai_enabled"
    """Produto pré-existente com IA acoplada. Funcionaria (pior) sem ela."""

    NON_AI = "non_ai"
    """Sem uso relevante de IA. Sai da pipeline cedo para não gastar RAG."""

    INDETERMINADO = "indeterminado"
    """Evidência insuficiente. NÃO é o mesmo que non_ai."""


class Stage(StrEnum):
    PRE_SEED = "pre_seed"
    SEED = "seed"
    SERIE_A = "serie_a"
    SERIE_B_PLUS = "serie_b_plus"
    BOOTSTRAPPED = "bootstrapped"
    DESCONHECIDO = "desconhecido"


class InferenceProvider(StrEnum):
    """Como a startup serve seus modelos. Sinal direto do eixo de domínio da stack."""

    API_EXTERNA = "api_externa"
    """OpenAI, Anthropic, Gemini via API. Máxima exposição à comoditização."""

    OPEN_WEIGHTS_HOSPEDADO = "open_weights_hospedado"
    """Llama/Mistral em Bedrock, Together, Groq. Menos lock-in, ainda sem controle de stack."""

    SELF_HOSTED = "self_hosted"
    """Inferência própria em GPU. Indica time de infra e controle de custo."""

    MODELO_PROPRIO = "modelo_proprio"
    """Treino ou fine-tuning próprio. Sinal mais forte de defensibilidade técnica."""

    DESCONHECIDO = "desconhecido"


class Founder(BaseModel):
    """Apenas informação profissional pública e relevante ao diagnóstico técnico.

    LGPD: não coletamos dado pessoal sensível, contato privado ou qualquer
    informação não publicada pela própria pessoa em contexto profissional.
    """

    name: str
    role: str | None = None
    technical_background: bool | None = Field(
        default=None, description="Formação/experiência técnica — pesa na capacidade de agir."
    )
    previous_companies: list[str] = Field(default_factory=list)
    evidences: list[Evidence] = Field(default_factory=list)


class FundingRound(BaseModel):
    stage: Stage
    amount_brl: float | None = None
    announced_at: date | None = None
    investors: list[str] = Field(default_factory=list)
    evidences: list[Evidence] = Field(default_factory=list)


class TechSignal(BaseModel):
    """Menção concreta a uma tecnologia, com onde foi vista.

    Vaga de engenharia é a fonte mais honesta de stack: marketing esconde,
    job description precisa ser específica para atrair o candidato certo.
    """

    technology: str
    category: str | None = Field(
        default=None, description="ex.: llm_provider, vector_db, orquestracao, infra, dados"
    )
    evidences: list[Evidence] = Field(default_factory=list)


class CompanyProfile(BaseModel):
    """Retrato da startup montado a partir de fontes públicas."""

    # Identidade
    name: str
    website: HttpUrl | None = None
    description: str | None = None
    founded_year: int | None = None
    hq_city: str | None = None
    hq_state: str | None = None

    # Campos inferidos — todos carregam suas próprias evidências
    sector: EvidenceBackedField[str] | None = None
    target_market: EvidenceBackedField[str] | None = Field(
        default=None, description="B2B, B2C, B2B2C, governo."
    )
    ai_use_description: EvidenceBackedField[str] | None = None
    inference_provider: EvidenceBackedField[InferenceProvider] | None = None
    proprietary_data_claim: EvidenceBackedField[str] | None = None
    named_integrations: EvidenceBackedField[list[str]] | None = Field(
        default=None, description="Sistemas de cliente integrados — proxy de profundidade."
    )
    enterprise_customers: EvidenceBackedField[list[str]] | None = None

    # Sinais estruturados
    tech_signals: list[TechSignal] = Field(default_factory=list)
    founders: list[Founder] = Field(default_factory=list)
    funding_rounds: list[FundingRound] = Field(default_factory=list)
    open_engineering_roles: list[str] = Field(
        default_factory=list, description="Títulos de vagas técnicas abertas."
    )

    stage: Stage = Stage.DESCONHECIDO
    headcount_estimate: int | None = None

    # Procedência da coleta
    source_urls: list[HttpUrl] = Field(default_factory=list)
    all_evidences: list[Evidence] = Field(default_factory=list)

    @property
    def has_ml_hiring_signal(self) -> bool:
        """Vaga de ML/infra indica intenção de internalizar a stack."""
        keywords = ("machine learning", "ml engineer", "mlops", "ai engineer", "infra", "dados")
        return any(k in role.lower() for role in self.open_engineering_roles for k in keywords)

    @property
    def evidence_coverage(self) -> float:
        """Fração dos campos inferidos que têm ao menos uma evidência.

        Alimenta a confiança global do perfil: cobertura baixa vira aviso na UI
        em vez de score silenciosamente baixo.
        """
        fields = [
            self.sector,
            self.target_market,
            self.ai_use_description,
            self.inference_provider,
            self.proprietary_data_claim,
            self.named_integrations,
            self.enterprise_customers,
        ]
        grounded = sum(1 for f in fields if f is not None and f.is_grounded)
        return round(grounded / len(fields), 3)


class Classification(BaseModel):
    """Saída do Startup Classifier Agent."""

    maturity: AIMaturity
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    evidences: list[Evidence] = Field(default_factory=list)
    model_used: str | None = None
    prompt_version: str | None = None
