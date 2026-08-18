"""Os cortes da fila de prioridade vivem em weights.yaml, não em literais."""

from __future__ import annotations

from radar.scoring.weights import get_weights


def test_cortes_da_fila_vem_do_yaml_e_nao_do_codigo():
    """Os três cortes da fila saíram de literais em `priority.py` para o YAML.

    Sem isto, a calibração da semana 4 mexeria nos números mais consequentes da
    fila por mudança de código, fora de `weights_version` — e o histórico ficaria
    incomparável sem nada registrando a diferença.
    """
    import inspect

    from radar.scoring import priority

    fonte = inspect.getsource(priority)
    assert "MIN_GLOBAL_CONFIDENCE = " not in fonte
    assert "DEFENSIBLE_THRESHOLD = " not in fonte
    assert "capacidade >= 0.5" not in fonte

    cortes = get_weights().priority
    assert 0.0 < cortes.min_global_confidence < 1.0
    assert 0.0 < cortes.capacity_threshold < 1.0
