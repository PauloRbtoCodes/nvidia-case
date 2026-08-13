"""Estimativa de TCO: API externa versus stack NVIDIA auto-hospedada.

Quantifica o eixo STACK_OWNERSHIP do Defensibility Radar e dá ao gerente um
número concreto para abrir conversa com o founder.

Honestidade metodológica é requisito, não cortesia. Isto é uma estimativa a
partir de sinais públicos, não uma medição — e todo número sai acompanhado das
premissas que o produziram. Um TCO que finge precisão que não tem morre na
primeira pergunta difícil de um founder técnico, e leva junto a credibilidade
da conversa inteira.

Consequência direta desse princípio: quando migrar não compensa, a função diz
que não compensa. Recomendar GPU dedicada para quem consome 5M tokens/mês é
pior do que não recomendar nada.
"""

from __future__ import annotations

import math

from radar.models.scoring import TCOEstimate, TCOScenario
from radar.scoring.weights import ScoringWeights, get_weights

#: Horas em um mês comercial (730 = 365*24/12). Usado para converter preço/hora
#: de GPU em custo mensal de instância reservada continuamente.
HORAS_POR_MES = 730
SEGUNDOS_POR_MES = HORAS_POR_MES * 3600


def _capacidade_mensal_por_gpu(throughput_tps: int, utilizacao: float) -> float:
    """Tokens/mês que uma GPU entrega na prática.

    A utilização média entra aqui porque tráfego real é irregular: picos de
    horário comercial, vales à noite. Dimensionar pelo throughput de pico produz
    uma conta bonita e uma migração frustrada.
    """
    return throughput_tps * utilizacao * SEGUNDOS_POR_MES


def _melhor_configuracao_gpu(
    tokens_mes: int, weights: ScoringWeights
) -> tuple[str, int, float]:
    """Escolhe a GPU mais barata que dá conta do volume.

    Devolve (nome_da_gpu, quantidade, custo_mensal_usd). Escolher pelo menor
    custo total, e não pela GPU mais potente, evita o viés de recomendar sempre
    o hardware mais caro — que é justamente o que destruiria a confiança do
    founder no diagnóstico.
    """
    cfg = weights.tco
    melhor: tuple[str, int, float] | None = None

    for gpu, preco_hora in cfg.gpu_hourly_usd.items():
        capacidade = _capacidade_mensal_por_gpu(
            cfg.throughput_tokens_per_second[gpu], cfg.utilizacao_media
        )
        # Mínimo de uma GPU: não existe fração de instância dedicada.
        quantidade = max(1, math.ceil(tokens_mes / capacidade))
        custo = quantidade * preco_hora * HORAS_POR_MES

        if melhor is None or custo < melhor[2]:
            melhor = (gpu, quantidade, custo)

    assert melhor is not None, "weights.yaml precisa de ao menos uma GPU precificada"
    return melhor


def estimar_volume_mensal(
    categoria_produto: str,
    cenario: TCOScenario,
    weights: ScoringWeights,
    headcount: int | None = None,
) -> int:
    """Infere tokens/mês a partir de sinais públicos.

    É a premissa mais frágil da cadeia — nenhuma startup publica seu consumo de
    tokens. Por isso trabalhamos com três cenários em vez de um número único: a
    faixa comunica a incerteza que um ponto esconderia.

    O headcount ajusta modestamente (raiz quadrada, não linear) porque o consumo
    escala com uso de clientes, não com tamanho do time — mas time maior é proxy
    fraco de tração.
    """
    cfg = weights.tco
    base = cfg.volume_base_tokens_mes.get(
        categoria_produto, cfg.volume_base_tokens_mes["desconhecido"]
    )
    multiplicador = cfg.cenarios_volume_multiplicador[cenario.value]
    volume = base * multiplicador

    if headcount and headcount > 0:
        # Normalizado por uma startup de referência de 25 pessoas.
        volume *= math.sqrt(headcount / 25)

    return int(volume)


def estimar_tco(
    categoria_produto: str,
    cenario: TCOScenario,
    provider: str | None = None,
    headcount: int | None = None,
    weights: ScoringWeights | None = None,
) -> TCOEstimate:
    """Monta a comparação de custo para um cenário.

    `provider` é a chave em `api_pricing_usd_per_1m_tokens`; quando o scraper não
    identificou o provedor, cai em "desconhecido" e a premissa fica registrada.
    """
    w = weights or get_weights()
    cfg = w.tco

    tokens = estimar_volume_mensal(categoria_produto, cenario, w, headcount)

    chave_provider = provider if provider in cfg.api_pricing_usd_per_1m_tokens else "desconhecido"
    preco_1m = cfg.api_pricing_usd_per_1m_tokens[chave_provider]
    custo_api = (tokens / 1_000_000) * preco_1m

    gpu, qtd, custo_gpu = _melhor_configuracao_gpu(tokens, w)

    # Volume em que a GPU se paga, dado o preço de API deste provedor. É o número
    # mais acionável do relatório: diz ao founder o quanto precisa crescer para
    # que a conversa sobre stack própria faça sentido.
    break_even = int((custo_gpu / preco_1m) * 1_000_000)

    premissas = [
        f"Volume estimado de {tokens / 1_000_000:.0f}M tokens/mês "
        f"(categoria '{categoria_produto}', cenário {cenario.value}).",
        f"Preço de API: US$ {preco_1m:.2f} por 1M tokens ({chave_provider}), "
        f"mix aproximado de 3:1 entrada:saída.",
        f"Stack NVIDIA: {qtd}x {gpu.upper()} sob demanda a US$ "
        f"{cfg.gpu_hourly_usd[gpu]:.2f}/hora, {HORAS_POR_MES}h/mês.",
        f"Utilização média de {cfg.utilizacao_media:.0%} — tráfego real é irregular, "
        "dimensionar por pico superestima a economia.",
        f"Throughput de referência: {cfg.throughput_tokens_per_second[gpu]:,} tokens/s "
        "para modelo ~8B quantizado com TensorRT-LLM em lote. Ordem de grandeza, "
        "não benchmark medido nesta startup.",
        f"Break-even neste cenário: ~{break_even / 1_000_000:.0f}M tokens/mês.",
        f"Preços de referência atualizados em {cfg.precos_atualizados_em}. "
        "Instância reservada ou anual reduz o custo de GPU de forma relevante.",
        "Não inclui engenharia de migração, observabilidade nem operação — "
        "custos reais que pesam mais em time pequeno.",
    ]

    if tokens < cfg.break_even_minimo_tokens_mes:
        piso_m = cfg.break_even_minimo_tokens_mes / 1_000_000
        premissas.append(
            f"ATENÇÃO: volume abaixo do piso de {piso_m:.0f}M tokens/mês. Nesta faixa a "
            "conversa sobre stack própria deve ser motivada por latência, residência de "
            "dados ou privacidade — não por custo."
        )

    return TCOEstimate(
        scenario=cenario,
        monthly_tokens_estimate=tokens,
        current_provider=provider,
        current_monthly_usd=round(custo_api, 2),
        nvidia_stack_monthly_usd=round(custo_gpu, 2),
        gpu_assumption=f"{qtd}x {gpu.upper()} sob demanda",
        assumptions=premissas,
        weights_version=w.version,
    )


def estimar_todos_cenarios(
    categoria_produto: str,
    provider: str | None = None,
    headcount: int | None = None,
    weights: ScoringWeights | None = None,
) -> list[TCOEstimate]:
    """Os três cenários juntos. A faixa é o produto, não o número do meio."""
    return [
        estimar_tco(categoria_produto, cenario, provider, headcount, weights)
        for cenario in TCOScenario
    ]
