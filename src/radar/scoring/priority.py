"""Fila de prioridade: risco de comoditização ponderado por capacidade de agir.

O produto final para o gerente de Startups & VCs não é uma lista de empresas, é
uma **fila**. Uma startup vulnerável que acabou de levantar rodada e tem time
técnico é uma conversa desta semana; a mesma vulnerabilidade sem capital nenhum
é trabalho de comunidade, não de reunião.

Por que este cálculo é determinístico e não sai do LLM: `urgency` ordena a
agenda de uma pessoa real. Se o número variasse entre execuções sobre os mesmos
dados, a fila perderia a função — e ninguém conseguiria explicar por que a
empresa X subiu três posições sem nenhum sinal novo. O LLM pontua os eixos
(julgamento sobre texto); a aritmética que ordena a semana fica aqui.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from radar.models.company import AIMaturity, CompanyProfile, Stage
from radar.models.scoring import (
    DefensibilityScore,
    PriorityAssessment,
    PriorityBucket,
)
from radar.scoring.weights import ScoringWeights, get_weights

#: Confiança global abaixo da qual não decidimos nada sobre a empresa: o score
#: existe, mas repousa em evidência fina demais para mandar alguém à reunião.
#: É o mesmo princípio do `is_actionable` por eixo, aplicado ao conjunto.
MIN_GLOBAL_CONFIDENCE = 0.35

#: Acima deste score a empresa já é defensável: deixa de ser alvo de resgate
#: técnico e vira candidata a case de sucesso do Inception.
DEFENSIBLE_THRESHOLD = 65.0


def _meses_desde(quando: date, *, hoje: date | None = None) -> float:
    referencia = hoje or datetime.now(UTC).date()
    return (referencia - quando).days / 30.44


def _sinal_funding(profile: CompanyProfile, weights: ScoringWeights, hoje: date | None) -> float:
    """Capital fresco. Rodada antiga não conta: dinheiro de 2022 já foi gasto.

    Decaímos linearmente dentro da janela em vez de usar um corte binário —
    uma rodada de 17 meses e outra de 19 não deveriam produzir capacidades
    opostas por um mês de diferença.
    """
    janela = weights.capacity_to_act.funding_recency_months
    melhor = 0.0
    for rodada in profile.funding_rounds:
        if rodada.announced_at is None:
            # Rodada sem data é sinal fraco: sabemos que houve, não sabemos quando.
            melhor = max(melhor, 0.3)
            continue
        idade = _meses_desde(rodada.announced_at, hoje=hoje)
        if idade < 0 or idade > janela:
            continue
        melhor = max(melhor, 1.0 - (idade / janela))
    return round(melhor, 3)


def _sinal_founder_tecnico(profile: CompanyProfile) -> float:
    """Founder técnico decide migração de stack sem intermediário."""
    if not profile.founders:
        return 0.0
    if any(f.technical_background for f in profile.founders):
        return 1.0
    # `None` em todos significa que não apuramos, não que o time é não-técnico.
    if all(f.technical_background is None for f in profile.founders):
        return 0.3
    return 0.0


def _sinal_contratacao(profile: CompanyProfile) -> float:
    """Vaga técnica aberta é intenção declarada de internalizar engenharia."""
    if profile.has_ml_hiring_signal:
        return 1.0
    return 0.4 if profile.open_engineering_roles else 0.0


def _sinal_estagio(profile: CompanyProfile, weights: ScoringWeights) -> float:
    scores = weights.capacity_to_act.stage_scores
    return scores.get(profile.stage.value, scores.get(Stage.DESCONHECIDO.value, 0.3))


def capacity_to_act(
    profile: CompanyProfile,
    *,
    weights: ScoringWeights | None = None,
    hoje: date | None = None,
) -> tuple[float, str]:
    """Quanto esta startup consegue reagir, de 0 a 1, com a justificativa em texto.

    A justificativa vai para o briefing: um número sozinho não sobrevive à
    pergunta "por quê?" na primeira reunião de carteira.
    """
    w = weights or get_weights()
    pesos = w.capacity_to_act.weights

    componentes = {
        "capital recente": (_sinal_funding(profile, w, hoje), pesos.recent_funding),
        "founder técnico": (_sinal_founder_tecnico(profile), pesos.technical_founder),
        "contratação de engenharia": (_sinal_contratacao(profile), pesos.engineering_hiring),
        "maturidade do estágio": (_sinal_estagio(profile, w), pesos.stage_maturity),
    }

    total = sum(valor * peso for valor, peso in componentes.values())
    detalhe = ", ".join(
        f"{nome} {valor:.2f} (peso {peso:.0%})" for nome, (valor, peso) in componentes.items()
    )
    return round(min(1.0, max(0.0, total)), 3), detalhe


def _bucket(
    score: DefensibilityScore,
    capacidade: float,
    maturity: AIMaturity | None,
) -> tuple[PriorityBucket, str]:
    """Traduz risco × capacidade na ação da semana."""
    if maturity is AIMaturity.INDETERMINADO or score.global_confidence < MIN_GLOBAL_CONFIDENCE:
        return (
            PriorityBucket.MONITORAR,
            "Re-coletar evidência antes de gastar tempo humano: a confiança global do "
            f"score é {score.global_confidence:.2f}, abaixo do mínimo de "
            f"{MIN_GLOBAL_CONFIDENCE:.2f} para sustentar uma conversa.",
        )

    if score.total >= DEFENSIBLE_THRESHOLD:
        return (
            PriorityBucket.CASE_POTENCIAL,
            "Convidar para case de referência e avaliar co-marketing: a empresa já mostra "
            f"fosso (score {score.total:.0f}) e a conversa técnica é entre pares.",
        )

    if capacidade >= 0.5:
        return (
            PriorityBucket.ABORDAR_AGORA,
            "Agendar conversa técnica nas próximas duas semanas: há gap real de "
            f"defensibilidade (risco {score.commoditization_risk:.0f}) e a empresa tem "
            "capital e time para agir sobre ele.",
        )

    return (
        PriorityBucket.NUTRIR,
        "Incluir em trilha de comunidade e conteúdo técnico: o gap existe, mas sem "
        "capital ou time a empresa não migra stack neste trimestre — cobrar decisão "
        "agora só queima a relação.",
    )


def avaliar_prioridade(
    profile: CompanyProfile,
    score: DefensibilityScore,
    *,
    maturity: AIMaturity | None = None,
    weights: ScoringWeights | None = None,
    hoje: date | None = None,
) -> PriorityAssessment:
    """`urgencia = risco_comoditizacao × capacidade_de_agir`, como no plano.

    O produto (e não a soma) é deliberado: risco alto com capacidade zero **não**
    é urgente, é trabalho de nutrição. Somar os dois produziria uma fila cheia de
    empresas que ninguém consegue mover, que é exatamente o modo de falha de toda
    lista de leads que o gerente já ignora hoje.
    """
    capacidade, detalhe = capacity_to_act(profile, weights=weights, hoje=hoje)
    bucket, proximo_passo = _bucket(score, capacidade, maturity)

    # Ponderamos a urgência pela confiança global: um risco medido sobre evidência
    # fina não pode furar a fila de um risco medido sobre evidência sólida.
    urgencia = score.commoditization_risk * capacidade * max(score.global_confidence, 0.1)

    return PriorityAssessment(
        bucket=bucket,
        urgency=round(min(100.0, max(0.0, urgencia)), 1),
        capacity_to_act=capacidade,
        capacity_rationale=(
            f"Capacidade de agir {capacidade:.2f} — {detalhe}. "
            f"Urgência = risco {score.commoditization_risk:.0f} × capacidade {capacidade:.2f} "
            f"× confiança {score.global_confidence:.2f}."
        ),
        recommended_next_step=proximo_passo,
    )
