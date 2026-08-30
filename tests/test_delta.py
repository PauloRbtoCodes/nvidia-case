"""O gatilho temporal: o que separa radar de foto.

O teste mais importante deste arquivo não é o caso feliz — é o invariante que o
módulo existe para proteger: ausência de evidência numa execução posterior
nunca pode ser lida como "a empresa piorou". O mundo é hostil (site fora do ar,
scraping falho), e confundir "paramos de achar evidência" com "o sinal
regrediu" seria exatamente a injustiça com aparência de rigor que o ADR 0002
já bloqueia no espaço, e que este módulo estende ao tempo.
"""

from __future__ import annotations

import pytest

from radar.models.evidence import Evidence, SourceKind
from radar.models.scoring import AxisScore, DefensibilityAxis, DefensibilityScore
from radar.scoring.delta import ChangeKind, comparar_scores


def _ev(excerpt: str, url: str = "https://x.com.br/pagina") -> Evidence:
    return Evidence(url=url, kind=SourceKind.SITE_OFICIAL, excerpt=excerpt)


def _axis(
    axis: DefensibilityAxis,
    score: float,
    confidence: float = 0.8,
    evidences: list[Evidence] | None = None,
) -> AxisScore:
    return AxisScore(
        axis=axis, score=score, confidence=confidence,
        evidences=evidences or [], rationale="fixture",
    )


def _score(weights_version: str = "0.1.0-inicial", **overrides: AxisScore) -> DefensibilityScore:
    defaults = {
        "proprietary_data": _axis(DefensibilityAxis.PROPRIETARY_DATA, 50.0),
        "workflow_depth": _axis(DefensibilityAxis.WORKFLOW_DEPTH, 50.0),
        "stack_ownership": _axis(DefensibilityAxis.STACK_OWNERSHIP, 50.0),
        "distribution": _axis(DefensibilityAxis.DISTRIBUTION, 50.0),
    }
    defaults.update(overrides)
    return DefensibilityScore(
        company_name="Fixture AI", axes=list(defaults.values()), weights_version=weights_version,
    )


# ------------------------------------------------------------------ o invariante


def test_evidencia_que_sumiu_nunca_vira_score_pior():
    """O caso central: a segunda coleta não repetiu a evidência (site fora do
    ar, robots.txt mudou). O score não pode cair por isso — o eixo tem que
    virar `confianca_caiu`, nunca `piorou`."""
    antes = _score(
        proprietary_data=_axis(
            DefensibilityAxis.PROPRIETARY_DATA, 70.0, confidence=0.8,
            evidences=[_ev("dataset proprietário construído ao longo de 3 anos" + "x" * 20)],
        ),
    )
    depois = _score(
        proprietary_data=_axis(
            DefensibilityAxis.PROPRIETARY_DATA, 70.0, confidence=0.4, evidences=[],
        ),
    )
    d = comparar_scores(antes, depois)
    eixo = next(a for a in d.axes if a.axis == DefensibilityAxis.PROPRIETARY_DATA)
    assert eixo.kind == ChangeKind.CONFIANCA_CAIU
    assert eixo.kind != ChangeKind.PIOROU


def test_confianca_caiu_e_score_tambem_caiu_ainda_e_confianca_caiu_nao_piora():
    """Quando os dois caem juntos, a leitura honesta é "perdemos o rastro do
    sinal" — não "o eixo regrediu". O score de 70 pra 55 aqui é ruído da perda
    de evidência, não uma piora medida."""
    antes = _score(
        distribution=_axis(
            DefensibilityAxis.DISTRIBUTION, 70.0, confidence=0.75,
            evidences=[_ev("contrato exclusivo de distribuição regional" + "y" * 20)],
        ),
    )
    depois = _score(distribution=_axis(DefensibilityAxis.DISTRIBUTION, 55.0, confidence=0.30))
    d = comparar_scores(antes, depois)
    eixo = next(a for a in d.axes if a.axis == DefensibilityAxis.DISTRIBUTION)
    assert eixo.kind == ChangeKind.CONFIANCA_CAIU


# ---------------------------------------------------------------- casos felizes


def test_nova_evidencia_e_o_sinal_de_maior_prioridade():
    """Mesmo quando o score não muda, evidência nova é o gatilho mais valioso —
    é o que vira a frase do talk track."""
    velha = _ev("vaga de engenheiro de dados publicada em janeiro" + "z" * 15)
    nova = _ev("abriu vaga de MLOps pedindo experiência com vLLM e Triton" + "w" * 10)
    antes = _score(
        stack_ownership=_axis(DefensibilityAxis.STACK_OWNERSHIP, 50.0, evidences=[velha]),
    )
    depois = _score(
        stack_ownership=_axis(DefensibilityAxis.STACK_OWNERSHIP, 50.0, evidences=[velha, nova]),
    )
    d = comparar_scores(antes, depois)
    eixo = next(a for a in d.axes if a.axis == DefensibilityAxis.STACK_OWNERSHIP)
    assert eixo.kind == ChangeKind.NOVA_EVIDENCIA
    assert len(eixo.new_evidences) == 1
    assert "vLLM" in eixo.new_evidences[0].excerpt


def test_score_sobe_alem_do_ruido_com_confianca_mantida_e_melhora():
    antes = _score(workflow_depth=_axis(DefensibilityAxis.WORKFLOW_DEPTH, 40.0, confidence=0.6))
    depois = _score(workflow_depth=_axis(DefensibilityAxis.WORKFLOW_DEPTH, 60.0, confidence=0.65))
    eixo = comparar_scores(antes, depois).axes[1]
    assert eixo.kind == ChangeKind.MELHOROU
    assert eixo.score_change == pytest.approx(20.0)


def test_score_cai_alem_do_ruido_com_confianca_mantida_e_piora():
    """Só é `piorou` quando a confiança não caiu — a mesma evidência (ou mais)
    sustenta um score pior, o que é sinal real de regressão."""
    ev = _ev("empresa migrou de modelo próprio para API da OpenAI" + "a" * 10)
    antes = _score(
        stack_ownership=_axis(
            DefensibilityAxis.STACK_OWNERSHIP, 80.0, confidence=0.7, evidences=[ev]
        ),
    )
    depois = _score(
        stack_ownership=_axis(
            DefensibilityAxis.STACK_OWNERSHIP, 55.0, confidence=0.7, evidences=[ev]
        ),
    )
    d = comparar_scores(antes, depois)
    eixo = next(a for a in d.axes if a.axis == DefensibilityAxis.STACK_OWNERSHIP)
    assert eixo.kind == ChangeKind.PIOROU


def test_variacao_dentro_do_ruido_e_estavel():
    antes = _score(distribution=_axis(DefensibilityAxis.DISTRIBUTION, 50.0, confidence=0.6))
    depois = _score(distribution=_axis(DefensibilityAxis.DISTRIBUTION, 51.5, confidence=0.61))
    eixo = comparar_scores(antes, depois).axes[3]
    assert eixo.kind == ChangeKind.ESTAVEL
    assert not eixo.is_actionable


# --------------------------------------------------------------------- agregados


def test_headline_axis_prioriza_evidencia_nova_sobre_piora():
    ev_nova = _ev("captou rodada Série A liderada por fundo internacional" + "b" * 10)
    antes = _score(
        proprietary_data=_axis(DefensibilityAxis.PROPRIETARY_DATA, 80.0, 0.7, [
            _ev("dado proprietário mantido" + "c" * 20)
        ]),
        stack_ownership=_axis(DefensibilityAxis.STACK_OWNERSHIP, 80.0, 0.7, [
            _ev("stack própria mantida" + "d" * 20)
        ]),
    )
    depois = _score(
        proprietary_data=_axis(DefensibilityAxis.PROPRIETARY_DATA, 40.0, 0.7, [
            _ev("dado proprietário mantido" + "c" * 20)
        ]),  # piorou
        stack_ownership=_axis(DefensibilityAxis.STACK_OWNERSHIP, 80.0, 0.7, [
            _ev("stack própria mantida" + "d" * 20), ev_nova,
        ]),  # nova evidência
    )
    d = comparar_scores(antes, depois)
    manchete = d.headline_axis
    assert manchete is not None
    assert manchete.kind == ChangeKind.NOVA_EVIDENCIA


def test_sem_mudanca_alguma_nao_ha_manchete():
    fixo = _score()
    d = comparar_scores(fixo, fixo)
    assert not d.has_changes
    assert d.headline_axis is None


def test_weights_version_diferente_e_preservada_na_saida():
    """O módulo não recusa comparar entre versões de pesos diferentes — só
    registra as duas, para quem consome decidir se a comparação é justa.
    A recusa (ou recálculo) é decisão de produto, não deste módulo aritmético."""
    antes = _score(weights_version="0.1.0-inicial")
    depois = _score(weights_version="0.2.0-calibrado")
    d = comparar_scores(antes, depois)
    assert d.weights_version_before == "0.1.0-inicial"
    assert d.weights_version_after == "0.2.0-calibrado"
