"""Testes que travam a semântica do Defensibility Radar.

Cada teste corresponde a uma decisão de projeto documentada em
docs/plano-arquitetura.md, seção 7. Se um destes quebrar, é sinal de que o
comportamento mudou de forma que afeta a justiça do diagnóstico — não de que
o teste precisa ser afrouxado.
"""

from __future__ import annotations

import pytest

from radar.models.scoring import (
    AXIS_WEIGHTS,
    AxisScore,
    DefensibilityAxis,
    DefensibilityScore,
    TCOEstimate,
    TCOScenario,
)


def _axis(
    axis: DefensibilityAxis, score: float, confidence: float = 0.8
) -> AxisScore:
    return AxisScore(
        axis=axis,
        score=score,
        confidence=confidence,
        rationale="fixture de teste",
    )


def _score(**overrides: AxisScore) -> DefensibilityScore:
    """Startup razoavelmente defensável, com eixos sobrescrevíveis por nome."""
    defaults = {
        "proprietary_data": _axis(DefensibilityAxis.PROPRIETARY_DATA, 70.0),
        "workflow_depth": _axis(DefensibilityAxis.WORKFLOW_DEPTH, 70.0),
        "stack_ownership": _axis(DefensibilityAxis.STACK_OWNERSHIP, 70.0),
        "distribution": _axis(DefensibilityAxis.DISTRIBUTION, 70.0),
    }
    defaults.update(overrides)
    return DefensibilityScore(
        company_name="Fixture AI",
        axes=list(defaults.values()),
        weights_version="teste",
    )


def test_pesos_somam_um():
    """Pesos precisam somar 1.0, senão o total deixa de ser uma escala 0-100."""
    assert sum(AXIS_WEIGHTS.values()) == pytest.approx(1.0)


def test_risco_e_complemento_do_total():
    s = _score()
    assert s.total + s.commoditization_risk == pytest.approx(100.0)


def test_wrapper_classico_tem_risco_alto():
    """Sem dado próprio, workflow raso e stack de terceiros = perfil substituível."""
    s = _score(
        proprietary_data=_axis(DefensibilityAxis.PROPRIETARY_DATA, 10.0),
        workflow_depth=_axis(DefensibilityAxis.WORKFLOW_DEPTH, 15.0),
        stack_ownership=_axis(DefensibilityAxis.STACK_OWNERSHIP, 5.0),
        distribution=_axis(DefensibilityAxis.DISTRIBUTION, 30.0),
    )
    assert s.commoditization_risk > 80.0
    assert len(s.actionable_gaps) == 4


def test_eixo_sem_evidencia_nao_e_acionavel():
    """A distinção central do projeto: 'não encontramos' != 'é ruim'.

    Um eixo com score baixo e confiança baixa não pode gerar recomendação nem
    aparecer como fraqueza afirmada — senão o sistema pune startups discretas.
    """
    fraco_sem_prova = _axis(DefensibilityAxis.PROPRIETARY_DATA, 10.0, confidence=0.1)
    assert not fraco_sem_prova.is_actionable

    s = _score(proprietary_data=fraco_sem_prova)
    assert DefensibilityAxis.PROPRIETARY_DATA not in s.actionable_gaps


def test_gap_severity_pondera_por_confianca():
    """Mesmo gap, menos evidência, menos severidade — evita recomendação no escuro."""
    certo = _axis(DefensibilityAxis.STACK_OWNERSHIP, 20.0, confidence=0.9)
    incerto = _axis(DefensibilityAxis.STACK_OWNERSHIP, 20.0, confidence=0.4)
    assert certo.gap_severity > incerto.gap_severity


def test_eixo_mais_fraco_considera_peso_nao_so_nota():
    """Nota menor num eixo leve pode pesar menos que nota média num eixo pesado.

    Distribuição (0.20) em 25 gera severidade 15.0 * 0.8 = 12.0;
    dados proprietários (0.30) em 40 gera 60 * 0.30 * 0.8 = 14.4 — este vence.
    """
    s = _score(
        distribution=_axis(DefensibilityAxis.DISTRIBUTION, 25.0),
        proprietary_data=_axis(DefensibilityAxis.PROPRIETARY_DATA, 40.0),
    )
    assert s.weakest_axis == DefensibilityAxis.PROPRIETARY_DATA


def test_candidatas_saem_dos_gaps_e_nao_de_regra_por_setor():
    """O gate determinístico deriva do diagnóstico — é o que torna a recomendação causal."""
    s = _score(stack_ownership=_axis(DefensibilityAxis.STACK_OWNERSHIP, 10.0))
    candidatas = s.candidate_technologies()

    assert "TensorRT-LLM" in candidatas
    assert "Triton Inference Server" in candidatas
    # Eixos saudáveis não injetam tecnologia irrelevante no briefing.
    assert "RAPIDS" not in candidatas


def test_startup_defensavel_nao_gera_gaps():
    s = _score(
        proprietary_data=_axis(DefensibilityAxis.PROPRIETARY_DATA, 85.0),
        workflow_depth=_axis(DefensibilityAxis.WORKFLOW_DEPTH, 90.0),
        stack_ownership=_axis(DefensibilityAxis.STACK_OWNERSHIP, 80.0),
        distribution=_axis(DefensibilityAxis.DISTRIBUTION, 75.0),
    )
    assert s.actionable_gaps == []
    assert s.candidate_technologies() == []


def test_exige_exatamente_quatro_eixos():
    with pytest.raises(ValueError):
        DefensibilityScore(
            company_name="Incompleta",
            axes=[_axis(DefensibilityAxis.PROPRIETARY_DATA, 50.0)],
            weights_version="teste",
        )


# ---------------------------------------------------------------------------
# TCO
# ---------------------------------------------------------------------------


def _tco(current: float, nvidia: float) -> TCOEstimate:
    return TCOEstimate(
        scenario=TCOScenario.MEDIO,
        monthly_tokens_estimate=100_000_000,
        current_monthly_usd=current,
        nvidia_stack_monthly_usd=nvidia,
        gpu_assumption="1x L40S sob demanda",
        weights_version="teste",
    )


def test_tco_calcula_economia():
    t = _tco(current=6000.0, nvidia=1500.0)
    assert t.monthly_savings_usd == 4500.0
    assert t.savings_pct == 75.0
    assert t.is_favorable


def test_tco_admite_quando_migrar_nao_compensa():
    """Volume baixo torna GPU dedicada pior. O sistema precisa dizer isso.

    Forçar recomendação favorável destrói a credibilidade na primeira pergunta
    difícil do founder.
    """
    t = _tco(current=200.0, nvidia=800.0)
    assert not t.is_favorable
    assert t.monthly_savings_usd < 0


def test_tco_sem_custo_atual_nao_inventa_percentual():
    t = _tco(current=0.0, nvidia=800.0)
    assert t.savings_pct is None
