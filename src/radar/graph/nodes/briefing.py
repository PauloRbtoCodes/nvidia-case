"""Briefing Agent: o documento que o gerente lê antes de falar com o founder.

Último nó do subgrafo. O modelo escreve apenas a prosa (`BriefingDraft`); score,
TCO, prioridade e recomendações entram como os objetos já validados. É a mesma
regra do Scorer: o que já foi calculado não volta a ser opinado, senão o
briefing pode divergir do que está no banco e ninguém saberá qual dos dois está
certo.

O markdown é renderizado aqui, em código, e não pedido ao modelo. Formatação
determinística garante que todo briefing tenha as mesmas seções na mesma ordem —
inclusive a de limitações, que é a que um gerador de texto tende a encurtar
quando não tem muito o que dizer, e é justamente a que não pode faltar.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine, Sequence
from typing import Any

import structlog

from radar.graph.nodes.base import dump, dump_many, node_guard
from radar.graph.nodes.deps import NodeDeps
from radar.graph.state import CompanyState
from radar.llm.model_registry import LLMTask
from radar.llm.schemas import BriefingDraft
from radar.models.company import AIMaturity
from radar.models.recommendation import Briefing, Recommendation
from radar.models.scoring import (
    AXIS_WEIGHTS,
    DefensibilityScore,
    PriorityAssessment,
    TCOScenario,
)

log = structlog.get_logger(__name__)

#: Rótulos em português dos eixos. Os valores do enum são em inglês por
#: compatibilidade com libs; o documento é lido por um brasileiro.
AXIS_LABELS = {
    "proprietary_data": "Dados proprietários",
    "workflow_depth": "Profundidade de workflow",
    "stack_ownership": "Domínio da stack",
    "distribution": "Distribuição",
}


def _lacunas_de_evidencia(state: CompanyState) -> str:
    """O que o sistema não sabe, em texto, para entrar no prompt do briefing.

    Vai para o modelo e não só para o markdown porque as lacunas precisam
    moldar o texto inteiro — um resumo executivo escrito sem saber que o eixo de
    dados não tem evidência sairá confiante onde não deveria.
    """
    partes: list[str] = list(state.get("validation_notes") or [])

    score = state.get("defensibility")
    if score is not None:
        fracos = [a for a in score.axes if not a.is_actionable]
        partes.extend(
            f"eixo '{AXIS_LABELS.get(a.axis.value, a.axis.value)}' com confiança "
            f"{a.confidence:.2f}: evidência insuficiente, não é nota baixa"
            for a in fracos
        )
        if not score.tco:
            partes.append(
                "TCO não calculado: nenhum provedor de API externa foi identificado "
                "publicamente, então não há custo atual com que comparar"
            )

    profile = state.get("profile")
    if profile is not None and profile.evidence_coverage < 0.6:
        partes.append(
            f"cobertura de evidência do perfil em {profile.evidence_coverage:.0%} — "
            "vários campos não puderam ser fundamentados em fonte pública"
        )

    if state.get("scrape_attempts", 0) > 1:
        partes.append(
            "houve re-coleta: os sinais desta empresa não estavam na primeira "
            "leitura do site"
        )

    return "\n".join(f"- {p}" for p in partes) if partes else "- nenhuma lacuna registrada"


def _secao_eixos(score: DefensibilityScore) -> str:
    linhas = [
        "| Eixo | Peso | Score | Confiança | Leitura |",
        "|---|---:|---:|---:|---|",
    ]
    for eixo in sorted(score.axes, key=lambda a: a.gap_severity, reverse=True):
        rotulo = AXIS_LABELS.get(eixo.axis.value, eixo.axis.value)
        leitura = (
            f"{eixo.score:.0f}/100"
            if eixo.is_actionable
            else "**evidência insuficiente** (não é nota baixa)"
        )
        linhas.append(
            f"| {rotulo} | {AXIS_WEIGHTS[eixo.axis]:.0%} | {eixo.score:.0f} | "
            f"{eixo.confidence:.2f} | {leitura} |"
        )
    return "\n".join(linhas)


def _secao_tco(score: DefensibilityScore) -> str:
    if not score.tco:
        return (
            "_Não calculado: nenhum provedor de API externa identificado publicamente, "
            "portanto não há custo atual com que comparar._"
        )

    linhas = [
        "| Cenário | Tokens/mês | Custo API | Stack NVIDIA | Economia | Migrar compensa? |",
        "|---|---:|---:|---:|---:|---|",
    ]
    ordem = {c: i for i, c in enumerate(TCOScenario)}
    for est in sorted(score.tco, key=lambda t: ordem[t.scenario]):
        veredito = "sim" if est.is_favorable else "**não, neste volume**"
        linhas.append(
            f"| {est.scenario.value} | {est.monthly_tokens_estimate / 1_000_000:.0f}M | "
            f"US$ {est.current_monthly_usd:,.0f} | US$ {est.nvidia_stack_monthly_usd:,.0f} | "
            f"US$ {est.monthly_savings_usd:,.0f} | {veredito} |"
        )

    premissas = "\n".join(f"- {p}" for p in score.tco[0].assumptions)
    return (
        "\n".join(linhas)
        + "\n\n**Estimativa a partir de sinais públicos, não medição.** Premissas:\n\n"
        + premissas
    )


def _secao_recomendacoes(recomendacoes: Sequence[Recommendation]) -> str:
    if not recomendacoes:
        return (
            "_Nenhuma recomendação foi emitida: não houve trecho da base NVIDIA que "
            "fundamentasse uma. O sistema bloqueia recomendação sem citação em vez de "
            "produzir uma plausível._"
        )

    blocos: list[str] = []
    for i, rec in enumerate(recomendacoes, start=1):
        citacoes = "\n".join(
            f"  - [{c.source_title or 'KB NVIDIA'}]({c.source_url})" for c in rec.kb_citations
        )
        eixo = AXIS_LABELS.get(rec.addresses_axis.value, rec.addresses_axis.value)
        blocos.append(
            f"### {i}. {rec.technology}\n\n"
            f"- **Endereça o eixo:** {eixo}\n"
            f"- **Prioridade:** {rec.priority.value} · **Complexidade:** {rec.complexity.value}\n\n"
            f"**Técnico.** {rec.technical_rationale}\n\n"
            f"**Negócio.** {rec.business_rationale}\n\n"
            f"**Próxima ação.** {rec.next_action}\n\n"
            f"- Fontes na base NVIDIA:\n{citacoes}"
        )
    return "\n\n".join(blocos)


def render_markdown(
    briefing: Briefing,
    score: DefensibilityScore,
    priority: PriorityAssessment,
) -> str:
    """Markdown determinístico. É o que a API exporta e o que vira PDF."""
    starters = "\n".join(f"- {q}" for q in briefing.conversation_starters) or "- (nenhuma)"
    caveats = "\n".join(f"- {c}" for c in briefing.caveats)
    inception = briefing.inception_fit or "_Sem encaixe evidente neste estágio._"

    return f"""# {briefing.company_name}

> Briefing gerado em {briefing.generated_at:%d/%m/%Y} · pesos `{score.weights_version}`
> · maturidade **{briefing.maturity.value}**

## Resumo executivo

{briefing.executive_summary}

## Defensibility Radar

- **Score de defensibilidade:** {score.total:.0f}/100
- **Risco de comoditização:** {score.commoditization_risk:.0f}/100
- **Confiança global do diagnóstico:** {score.global_confidence:.2f}

{_secao_eixos(score)}

## Prioridade

- **Bucket:** {priority.bucket.value} · **Urgência:** {priority.urgency:.0f}/100
- **Capacidade de agir:** {priority.capacity_to_act:.2f}
- {priority.capacity_rationale}
- **Próximo passo:** {priority.recommended_next_step}

## Custo de inferência (TCO estimado)

{_secao_tco(score)}

## Plano de fosso

{briefing.moat_plan}

## Recomendações NVIDIA

{_secao_recomendacoes(briefing.recommendations)}

## Perguntas para a primeira conversa

{starters}

## Encaixe no Inception

{inception}

## O que este diagnóstico não sabe

{caveats}
"""


def make_write_briefing(
    deps: NodeDeps,
) -> Callable[[CompanyState], Coroutine[Any, Any, dict[str, Any]]]:
    @node_guard("briefing")
    async def write_briefing(state: CompanyState) -> dict[str, Any]:
        profile = state.get("profile")
        score = state.get("defensibility")
        prioridade = state.get("priority")
        classificacao = state.get("classification")

        if profile is None or score is None or prioridade is None:
            raise ValueError("Briefing sem perfil, score ou prioridade.")

        recomendacoes = list(state.get("recommendations") or [])
        lacunas = _lacunas_de_evidencia(state)

        rascunho = await deps.run_llm(
            LLMTask.BRIEFING,
            BriefingDraft,
            company_profile_json=dump(profile),
            classification_json=dump(classificacao),
            defensibility_json=dump(score),
            priority_json=dump(prioridade),
            recommendations_json=dump_many(recomendacoes),
            evidence_gaps=lacunas,
        )

        briefing = Briefing(
            company_name=profile.name,
            executive_summary=rascunho.executive_summary,
            maturity=classificacao.maturity if classificacao else AIMaturity.INDETERMINADO,
            defensibility=score,
            priority=prioridade,
            recommendations=recomendacoes,
            moat_plan=rascunho.moat_plan,
            conversation_starters=list(rascunho.conversation_starters),
            inception_fit=rascunho.inception_fit,
            caveats=list(rascunho.caveats),
        )
        briefing = briefing.model_copy(
            update={"markdown": render_markdown(briefing, score, prioridade)}
        )

        log.info(
            "briefing",
            empresa=profile.name,
            recomendacoes=len(recomendacoes),
            citacoes=briefing.total_citations,
            caveats=len(briefing.caveats),
        )
        return {"briefing": briefing}

    return write_briefing
