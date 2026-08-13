"""Defensibility Radar — o diferencial do projeto.

Responde à pergunta norteadora do case: dado que os grandes labs subiram na cadeia
de valor, quão exposta esta startup está a ser substituída por uma funcionalidade
nativa da OpenAI/Anthropic/Google — e o que ela precisaria construir para deixar
de estar.

Princípio de projeto: **score e confiança são números separados**. Uma startup
discreta, sem blog de engenharia, produz pouca evidência. Se misturarmos "não
encontramos sinal" com "o sinal é ruim", o sistema gera injustiça com aparência
de rigor. Todo AxisScore carrega os dois, e a UI é obrigada a distinguir.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, computed_field

from radar.models.evidence import Evidence


class DefensibilityAxis(StrEnum):
    """Os quatro eixos. O mais fraco define quais tecnologias NVIDIA entram no briefing."""

    PROPRIETARY_DATA = "proprietary_data"
    """Tem dado que o lab não tem e não consegue comprar?"""

    WORKFLOW_DEPTH = "workflow_depth"
    """Quão fundo no processo do cliente? Chat genérico é raso; escrever no ERP é fundo."""

    DISTRIBUTION = "distribution"
    """Canal, contratos e relacionamento que um lab não replica com um anúncio de feature."""

    STACK_OWNERSHIP = "stack_ownership"
    """Controla inferência, custo e latência — ou é repassador de API?"""


AXIS_WEIGHTS: dict[DefensibilityAxis, float] = {
    DefensibilityAxis.PROPRIETARY_DATA: 0.30,
    DefensibilityAxis.WORKFLOW_DEPTH: 0.25,
    DefensibilityAxis.STACK_OWNERSHIP: 0.25,
    DefensibilityAxis.DISTRIBUTION: 0.20,
}

#: Eixo fraco → famílias de tecnologia NVIDIA que endereçam aquele gap.
#: Serve de gate determinístico antes do RAG: filtra o universo de candidatas
#: para que o LLM só justifique dentro de um conjunto já pertinente.
AXIS_TO_NVIDIA_FAMILY: dict[DefensibilityAxis, list[str]] = {
    DefensibilityAxis.PROPRIETARY_DATA: ["NeMo Curator", "NeMo Customizer", "RAPIDS", "cuDF", "cuML"],
    DefensibilityAxis.WORKFLOW_DEPTH: ["NIM", "NeMo Guardrails", "NIM Agent Blueprints", "Riva"],
    DefensibilityAxis.STACK_OWNERSHIP: ["NIM", "TensorRT-LLM", "Triton Inference Server", "NVIDIA AI Enterprise"],
    DefensibilityAxis.DISTRIBUTION: ["NVIDIA Inception"],
}


class AxisScore(BaseModel):
    """Pontuação de um eixo, com evidência e confiança independentes."""

    axis: DefensibilityAxis
    score: float = Field(ge=0.0, le=100.0, description="0 = totalmente comoditizável, 100 = defensável.")
    confidence: float = Field(
        ge=0.0, le=1.0, description="Quanta evidência sustenta o score. NÃO é o score."
    )

    positive_signals: list[str] = Field(default_factory=list)
    negative_signals: list[str] = Field(default_factory=list)
    evidences: list[Evidence] = Field(default_factory=list)
    rationale: str

    @computed_field
    @property
    def is_actionable(self) -> bool:
        """Abaixo de 0,35 de confiança o eixo não sustenta uma conversa comercial.

        A UI mostra esses eixos como "evidência insuficiente", não como nota baixa.
        """
        return self.confidence >= 0.35

    @computed_field
    @property
    def gap_severity(self) -> float:
        """Quanto este eixo puxa o score para baixo, ponderado por peso e confiança.

        Multiplicar pela confiança evita que um eixo sem evidência dispare
        recomendações — o gate só considera gaps que realmente observamos.
        """
        return round((100.0 - self.score) * AXIS_WEIGHTS[self.axis] * self.confidence, 2)


class TCOScenario(StrEnum):
    CONSERVADOR = "conservador"
    MEDIO = "medio"
    AGRESSIVO = "agressivo"


class TCOEstimate(BaseModel):
    """Comparação de custo entre API externa e stack NVIDIA auto-hospedada.

    Quantifica o eixo STACK_OWNERSHIP e dá ao gerente um número para abrir conversa.

    IMPORTANTE: é estimativa a partir de sinais públicos, não medição. Todo output
    para o usuário precisa carregar as premissas junto. Um TCO que finge precisão
    que não tem destrói a credibilidade da conversa comercial na primeira pergunta
    difícil do founder.
    """

    scenario: TCOScenario
    monthly_tokens_estimate: int = Field(description="Volume inferido de tokens/mês.")

    current_provider: str | None = None
    current_monthly_usd: float = Field(ge=0.0)

    nvidia_stack_monthly_usd: float = Field(ge=0.0, description="GPU dedicada + NIM.")
    gpu_assumption: str = Field(description="ex.: 1x L40S sob demanda")

    assumptions: list[str] = Field(
        default_factory=list, description="Toda premissa usada, em texto legível."
    )
    weights_version: str = Field(description="Versão de scoring/weights.yaml usada.")

    @computed_field
    @property
    def monthly_savings_usd(self) -> float:
        return round(self.current_monthly_usd - self.nvidia_stack_monthly_usd, 2)

    @computed_field
    @property
    def savings_pct(self) -> float | None:
        if self.current_monthly_usd <= 0:
            return None
        return round(100.0 * self.monthly_savings_usd / self.current_monthly_usd, 1)

    @computed_field
    @property
    def is_favorable(self) -> bool:
        """Abaixo do break-even, migrar não compensa — e dizer isso constrói confiança.

        Um sistema que recomenda GPU dedicada para uma startup com 5M tokens/mês
        queima a credibilidade do programa inteiro.
        """
        return self.monthly_savings_usd > 0


class DefensibilityScore(BaseModel):
    """Score consolidado + plano de fosso."""

    company_name: str
    axes: list[AxisScore] = Field(min_length=4, max_length=4)
    tco: list[TCOEstimate] = Field(default_factory=list)
    weights_version: str

    @computed_field
    @property
    def total(self) -> float:
        """Média ponderada dos eixos. Alto = defensável."""
        return round(sum(a.score * AXIS_WEIGHTS[a.axis] for a in self.axes), 1)

    @computed_field
    @property
    def commoditization_risk(self) -> float:
        """O complemento — é este número que responde à pergunta do case."""
        return round(100.0 - self.total, 1)

    @computed_field
    @property
    def global_confidence(self) -> float:
        """Confiança do score inteiro, ponderada pelos mesmos pesos dos eixos."""
        return round(sum(a.confidence * AXIS_WEIGHTS[a.axis] for a in self.axes), 3)

    @computed_field
    @property
    def weakest_axis(self) -> DefensibilityAxis:
        """O gap que mais custa, considerando peso e confiança.

        É a raiz do motor de recomendação: a tecnologia NVIDIA sugerida sai daqui,
        e não de uma regra solta por setor.
        """
        return max(self.axes, key=lambda a: a.gap_severity).axis

    @computed_field
    @property
    def actionable_gaps(self) -> list[DefensibilityAxis]:
        """Eixos com gap real E evidência suficiente, do mais severo ao menos."""
        gaps = [a for a in self.axes if a.is_actionable and a.score < 60.0]
        return [a.axis for a in sorted(gaps, key=lambda a: a.gap_severity, reverse=True)]

    def candidate_technologies(self) -> list[str]:
        """Gate determinístico: universo de tecnologias antes de consultar o RAG."""
        seen: list[str] = []
        for axis in self.actionable_gaps:
            for tech in AXIS_TO_NVIDIA_FAMILY[axis]:
                if tech not in seen:
                    seen.append(tech)
        return seen


class PriorityBucket(StrEnum):
    """O que o gerente de Startups & VCs faz com esta empresa nesta semana."""

    ABORDAR_AGORA = "abordar_agora"
    """Vulnerável e com capital/time para reagir. A conversa tem urgência real."""

    NUTRIR = "nutrir"
    """Vulnerável, mas sem capacidade de agir agora. Comunidade, conteúdo, eventos."""

    CASE_POTENCIAL = "case_potencial"
    """Já defensável e com stack madura. Candidata a case de sucesso e referência."""

    MONITORAR = "monitorar"
    """Evidência insuficiente para decidir. Re-coletar antes de gastar tempo humano."""


class PriorityAssessment(BaseModel):
    """Ordena a fila de trabalho: risco não basta, precisa de capacidade de reagir."""

    bucket: PriorityBucket
    urgency: float = Field(ge=0.0, le=100.0)

    capacity_to_act: float = Field(
        ge=0.0, le=1.0,
        description="Funding recente, time técnico e estágio. Startup sem capital não migra stack.",
    )
    capacity_rationale: str
    recommended_next_step: str
