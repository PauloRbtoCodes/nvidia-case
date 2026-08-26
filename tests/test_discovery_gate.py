"""O porteiro entre "isto é uma empresa" e "isto fala de empresas".

Os casos abaixo saíram da primeira execução real contra Tavily. O nó tratava
toda query igual, e a fila de prioridade terminou com uma vaga do LinkedIn e uma
manchete de portal no lugar de startups.

A causa raiz era uma contradição entre o prompt e o filtro: o `search_planner_v1`
mandava o planner buscar com `site:distrito.me`, `site:exame.com` e afins, e o
`AGGREGATOR_HINTS` descartava exatamente esses domínios. A família de query que
existe para descobrir empresas era jogada fora inteira, e sobravam as duas
famílias que procuram evidência — vagas e notícias.
"""

from __future__ import annotations

import pytest

from radar.graph.nodes.discovery import _parece_empresa
from radar.scraping.search import SearchCandidate


def cand(url: str, title: str = "Acme") -> SearchCandidate:
    return SearchCandidate(url=url, title=title)


# ------------------------------------------------- casos reais da execução


@pytest.mark.parametrize(
    ("url", "titulo", "motivo"),
    [
        (
            "https://br.linkedin.com/jobs/view/engenheiro-de-software-com-ia-senior-4461191050",
            "Desenvolvimento de Programas e Software com IA Sênior na ...",
            "agregador",
        ),
        (
            "https://agenciasebrae.com.br/inovacao-e-tecnologia/afroempreendedorismo-aposta-em-redes/",
            "Afroempreendedorismo aposta em redes e capacitação",
            "agregador",
        ),
        (
            "https://exame.com/inteligencia-artificial/os-4-setores-mais-promissores/",
            "Os 4 setores mais promissores para uso de Inteligência Artificial no Brasil",
            "agregador",
        ),
    ],
)
def test_resultados_que_poluiram_a_fila_sao_recusados(url, titulo, motivo):
    ok, m = _parece_empresa(cand(url, titulo))
    assert not ok
    assert m == motivo


# ------------------------------------------------------------ por forma da URL


@pytest.mark.parametrize(
    "url",
    [
        "https://portalqualquer.com.br/2026/09/09/startup-de-saude-capta-rodada",
        "https://blogdenicho.com.br/blog/como-a-ia-muda-a-saude",
        "https://empresa.com.br/vagas/engenheiro-de-machine-learning",
        "https://site.com.br/noticias/tecnologia/ia-no-brasil",
        "https://x.com.br/a/b/c/d/e",
    ],
)
def test_url_com_forma_de_conteudo_e_recusada(url):
    """A lista de domínios nunca fica completa — portal regional e blog de nicho
    aparecem toda semana. A forma da URL sobrevive melhor que o inventário."""
    ok, motivo = _parece_empresa(cand(url))
    assert not ok
    assert motivo == "caminho_de_conteudo"


# ---------------------------------------------------------- por forma do título


@pytest.mark.parametrize(
    "titulo",
    [
        "Startup Sofya usa Inteligência Artificial para otimizar raciocínio clínico",
        "Fintech brasileira capta R$ 50 milhões em rodada Série A",
        "Conheça as 10 melhores startups de saúde do Brasil",
        "Como a inteligência artificial está mudando o diagnóstico médico",
    ],
)
def test_titulo_com_cara_de_manchete_e_recusado(titulo):
    """Nome de empresa é sintagma nominal curto; manchete é oração com verbo."""
    ok, motivo = _parece_empresa(cand("https://empresadesconhecida.com.br/", titulo))
    assert not ok
    assert motivo == "titulo_de_manchete"


# ------------------------------------------------------------------- aceitos


@pytest.mark.parametrize(
    ("url", "titulo"),
    [
        ("https://sofya.ai/", "Sofya | Inteligência artificial para saúde"),
        ("https://www.nuvemshop.com.br/", "Nuvemshop"),
        ("https://empresa.com.br/produto", "Empresa — Plataforma de prontuário eletrônico"),
    ],
)
def test_site_proprio_de_empresa_passa(url, titulo):
    ok, motivo = _parece_empresa(cand(url, titulo))
    assert ok, f"recusado por {motivo}"


def test_home_de_empresa_com_nome_que_contem_verbo_comum_nao_e_falso_positivo():
    """`_titulo_de_manchete` casa palavra cercada de espaço, não substring solta —
    senão uma empresa chamada 'Vailu' ou 'Usare' seria recusada pelo próprio nome."""
    ok, _ = _parece_empresa(cand("https://vailu.com.br/", "Vailu"))
    assert ok
    ok, _ = _parece_empresa(cand("https://usare.com.br/", "Usare | Gestão de frotas"))
    assert ok
