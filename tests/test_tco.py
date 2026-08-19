"""Testes do motor de TCO.

O valor destes testes não é conferir aritmética — é impedir que o estimador
adquira viés otimista. Um TCO que sempre favorece a NVIDIA é propaganda, e um
founder técnico percebe na primeira pergunta.
"""

from __future__ import annotations

import pytest

from radar.models.scoring import TCOScenario
from radar.scoring.tco import (
    HORAS_POR_MES,
    estimar_tco,
    estimar_todos_cenarios,
    estimar_volume_mensal,
)
from radar.scoring.weights import get_weights, load_weights


@pytest.fixture(scope="module")
def w():
    return get_weights()


def test_weights_yaml_carrega_e_valida(w):
    """Falha aqui significa weights.yaml inconsistente — bloqueia todo o scoring."""
    assert w.version
    assert sum(w.axis_weights.values()) == pytest.approx(1.0)
    assert set(w.signals) == {
        "proprietary_data",
        "workflow_depth",
        "stack_ownership",
        "distribution",
    }


def test_weights_rejeita_pesos_que_nao_somam_um(tmp_path):
    """Se os pesos não somam 1.0, o total deixa de ser escala 0-100 e o número mente."""
    original = (
        get_weights().model_dump()
    )
    original["axis_weights"] = {
        "proprietary_data": 0.5,
        "workflow_depth": 0.5,
        "stack_ownership": 0.5,
        "distribution": 0.5,
    }

    import yaml

    caminho = tmp_path / "weights.yaml"
    caminho.write_text(yaml.safe_dump(original), encoding="utf-8")

    with pytest.raises(ValueError, match="somar 1.0"):
        load_weights(caminho)


def test_cenarios_ordenam_volume(w):
    """Conservador < médio < agressivo. A faixa é o produto, não o ponto médio."""
    volumes = [
        estimar_volume_mensal("atendimento_cliente", c, w) for c in TCOScenario
    ]
    assert volumes == sorted(volumes)


def test_categoria_desconhecida_nao_quebra(w):
    """Scraper frequentemente não identifica a categoria — cair no default é normal."""
    v = estimar_volume_mensal("categoria_que_nao_existe", TCOScenario.MEDIO, w)
    assert v == w.tco.volume_base_tokens_mes["desconhecido"]


def test_volume_alto_favorece_gpu():
    """Em volume de call center, stack própria compensa — é o caso que motiva a conversa."""
    est = estimar_tco("voz_transcricao", TCOScenario.AGRESSIVO, provider="openai_gpt_frontier")
    assert est.is_favorable
    assert est.savings_pct is not None and est.savings_pct > 0


def test_volume_baixo_admite_que_nao_compensa():
    """O teste mais importante do arquivo.

    Se o estimador nunca disser 'não migre', ele é uma peça de marketing. Busca
    interna em cenário conservador é volume pequeno: GPU dedicada perde para API,
    e o sistema precisa dizer isso.
    """
    est = estimar_tco("busca_interna", TCOScenario.CONSERVADOR, provider="openai_gpt_mini")
    assert not est.is_favorable
    assert est.monthly_savings_usd < 0


def test_aviso_de_piso_aparece_em_volume_pequeno():
    est = estimar_tco("busca_interna", TCOScenario.CONSERVADOR)
    assert any("ATENÇÃO" in p for p in est.assumptions)
    assert any("latência" in p for p in est.assumptions)


def test_escolhe_gpu_mais_barata_que_atende(w):
    """Sem este critério, o estimador tenderia a sugerir sempre o hardware maior."""
    est = estimar_tco("busca_interna", TCOScenario.CONSERVADOR, weights=w)

    custo_minimo_possivel = min(
        preco * HORAS_POR_MES for preco in w.tco.gpu_hourly_usd.values()
    )
    assert est.nvidia_stack_monthly_usd == pytest.approx(custo_minimo_possivel, rel=0.01)


def test_premissas_sempre_acompanham_o_numero():
    """Número sem premissa é número que não sobrevive a uma reunião."""
    est = estimar_tco("copiloto_vertical", TCOScenario.MEDIO, provider="anthropic_claude_frontier")
    assert len(est.assumptions) >= 5
    assert any("Break-even" in p for p in est.assumptions)
    assert any("não benchmark medido" in p for p in est.assumptions)
    assert est.weights_version


def test_provider_desconhecido_registra_premissa():
    """Quando o scraper não identifica o provedor, isso precisa ficar visível."""
    est = estimar_tco("copiloto_vertical", TCOScenario.MEDIO, provider=None)
    assert any("desconhecido" in p for p in est.assumptions)


def test_headcount_maior_aumenta_volume_de_forma_sublinear(w):
    """Consumo escala com uso de clientes, não com tamanho do time."""
    pequeno = estimar_volume_mensal("copiloto_vertical", TCOScenario.MEDIO, w, headcount=25)
    grande = estimar_volume_mensal("copiloto_vertical", TCOScenario.MEDIO, w, headcount=100)

    assert grande > pequeno
    assert grande < pequeno * 4  # linear seria 4x; sublinear precisa ficar abaixo


def test_todos_cenarios_devolve_tres():
    ests = estimar_todos_cenarios("analise_documentos", provider="google_gemini_pro")
    assert len(ests) == 3
    assert {e.scenario for e in ests} == set(TCOScenario)


# ------------------------------------- chaves de product.py ↔ weights.yaml


def test_categorias_de_produto_casam_com_os_volumes_do_yaml():
    """Mesma armadilha dos cards, no motor de TCO.

    `inferir_categoria_produto` devolve uma chave que o TCO usa em
    `volume_base_tokens_mes.get(categoria, ...["desconhecido"])` — com **fallback
    silencioso**. Uma categoria renomeada no YAML não quebra nada: vira
    `desconhecido` e o volume base cai de 250M para 50M tokens/mês. Erro de 5×
    na premissa mais frágil da cadeia, sem uma linha de log.

    A trava é nas duas direções, como em `test_cards.py`: chave órfã no código e
    chave órfã no YAML são ambas bug.
    """
    from radar.scoring.product import CATEGORY_KEYWORDS, DEFAULT_CATEGORY

    no_codigo = {categoria for categoria, _ in CATEGORY_KEYWORDS} | {DEFAULT_CATEGORY}
    no_yaml = set(get_weights().tco.volume_base_tokens_mes)

    assert no_codigo == no_yaml, (
        f"só no código: {sorted(no_codigo - no_yaml)}; só no yaml: {sorted(no_yaml - no_codigo)}"
    )


def test_provedores_casam_com_a_tabela_de_precos_do_yaml():
    """Aqui o fallback não é silencioso — é `KeyError` — mas quebrar em produção
    por chave renomeada continua sendo evitável em CI."""
    from radar.scoring.product import DEFAULT_PROVIDER_KEY, PROVIDER_KEYWORDS

    no_codigo = {chave for chave, _ in PROVIDER_KEYWORDS} | {DEFAULT_PROVIDER_KEY}
    no_yaml = set(get_weights().tco.api_pricing_usd_per_1m_tokens)

    assert no_codigo == no_yaml, (
        f"só no código: {sorted(no_codigo - no_yaml)}; só no yaml: {sorted(no_yaml - no_codigo)}"
    )
