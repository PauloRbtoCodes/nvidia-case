"""Testes dos cards manuais da KB.

Não testam o *conteúdo* dos cards — isso é julgamento humano e é o que a suíte de
avaliação mede contra o golden dataset. Testam o **contrato** entre os cards e o
gate determinístico do score, que é onde uma falha some sem deixar rastro:

o filtro de payload do Qdrant usa como `technology` exatamente as strings de
`AXIS_TO_NVIDIA_FAMILY`. Um card escrito com `technology: NIM Microservices` em
vez de `NIM` é invisível para a busca. O RAG devolve zero chunks, o guardrail de
citação bloqueia a recomendação — corretamente — e o briefing sai dizendo que não
havia fundamento na KB. Tudo funcionando como projetado, e a resposta errada.
"""

from __future__ import annotations

import pytest

from radar.models.scoring import AXIS_TO_NVIDIA_FAMILY, DefensibilityAxis
from radar.rag.chunking import Chunk
from radar.rag.ingest import DEFAULT_CARDS_DIR, collect_chunks, load_cards

#: Toda tecnologia que o gate determinístico é capaz de liberar.
TECNOLOGIAS_DO_GATE = sorted({t for familia in AXIS_TO_NVIDIA_FAMILY.values() for t in familia})

#: Seções que dão ao card sua função. Sem "quando NÃO recomendar" o card vira
#: material de marketing e o motor perde a capacidade de dizer "não migre ainda",
#: que é o que protege a credibilidade da conversa comercial.
SECOES_OBRIGATORIAS = (
    "## O que resolve",
    "## Sinais de que esta startup precisa",
    "## Quando NÃO recomendar",
    "## Pré-requisitos",
    "## Complexidade",
    "## Eixo de defensibilidade",
    "## Primeira ação sugerida",
)


@pytest.fixture(scope="module")
def cards():
    return load_cards()


@pytest.fixture(scope="module")
def chunks(cards) -> list[Chunk]:
    return collect_chunks(cards)


def test_existe_card_para_toda_tecnologia_do_gate(cards):
    """Sem card, o gate libera uma tecnologia que o RAG não sabe recuperar."""
    cobertas = {c.source.technology for c in cards}
    faltando = [t for t in TECNOLOGIAS_DO_GATE if t not in cobertas]
    assert not faltando, (
        f"sem card para: {faltando}. O gate vai liberá-las e a busca não terá o que "
        "devolver, bloqueando a recomendação sem explicar por quê."
    )


def test_nenhum_card_aponta_para_tecnologia_que_o_gate_nunca_libera(cards):
    """Card órfão é trabalho manual que nunca chega a um briefing."""
    orfaos = sorted(
        {
            c.source.technology
            for c in cards
            if c.source.technology and c.source.technology not in TECNOLOGIAS_DO_GATE
        }
    )
    assert not orfaos, (
        f"cards sem eixo correspondente: {orfaos}. Acrescente a tecnologia em "
        "AXIS_TO_NVIDIA_FAMILY ou remova o card."
    )


def test_todo_eixo_tem_ao_menos_um_card(cards):
    """Um eixo sem card produz gap acionável e briefing sem recomendação."""
    cobertas = {c.source.technology for c in cards}
    for eixo in DefensibilityAxis:
        familia = AXIS_TO_NVIDIA_FAMILY[eixo]
        assert cobertas & set(familia), f"eixo {eixo.value} sem nenhum card"


@pytest.mark.parametrize("secao", SECOES_OBRIGATORIAS)
def test_cards_tem_as_secoes_que_os_tornam_uteis(cards, secao: str):
    faltando = [
        c.source.title for c in cards if secao not in c.markdown
    ]
    assert not faltando, f"cards sem a seção {secao!r}: {faltando}"


def test_cards_sao_ingeridos_como_doc_type_card(cards):
    """`doc_type` separa card de documentação oficial no payload do Qdrant."""
    assert cards, "nenhum card carregado"
    assert all(c.source.doc_type == "card" for c in cards)


def test_cards_citam_url_oficial_em_vez_do_sentinela(cards):
    """A citação vai para o briefing: precisa levar o founder a algum lugar real.

    O sentinela `kb.local.invalid` existe como rede de segurança do loader, não
    como destino aceitável — um briefing que cita uma URL inválida é pior que um
    que não cita.
    """
    sem_url = [c.source.title for c in cards if "local.invalid" in c.source.url]
    assert not sem_url, f"cards sem URL canônica no front matter: {sem_url}"


def test_readme_do_diretorio_nao_vira_chunk_da_kb(chunks):
    """Guia de escrita competindo na busca responderia à pergunta errada."""
    assert (DEFAULT_CARDS_DIR / "README.md").exists(), "o guia de escrita sumiu"
    assert not [c for c in chunks if "Como ler e escrever" in (c.source_title or "")]


def test_chunks_de_card_preservam_tecnologia_para_o_filtro(chunks):
    """O filtro de payload é por chunk, não por documento."""
    assert chunks
    sem_tecnologia = [c.chunk_id for c in chunks if not c.technology]
    assert not sem_tecnologia, "chunk de card sem `technology` é invisível para o gate"


@pytest.mark.parametrize("eixo", list(DefensibilityAxis))
def test_query_do_eixo_alcanca_os_cards_daquele_eixo(chunks, eixo: DefensibilityAxis):
    """As perguntas de `retriever.AXIS_QUERIES` precisam casar com o texto escrito.

    Duas coisas evoluem em arquivos distintos e por motivos distintos: as queries
    por eixo, em `graph/nodes/retriever.py`, e a redação dos cards, aqui. Nada
    além deste teste amarra as duas — e o desalinhamento é invisível em produção,
    porque o filtro de payload garante que *algum* chunk da família volte mesmo
    quando a query não descreve o problema que o card resolve. O resultado seria
    uma recomendação fundamentada no card errado da família certa.

    Só o lado lexical entra aqui: BM25 roda sem rede, e se o casamento por termo
    já funciona, a busca densa em produção só melhora.
    """
    from radar.graph.nodes.retriever import AXIS_QUERIES
    from radar.rag.bm25 import BM25Index

    hits = BM25Index(chunks).search(AXIS_QUERIES[eixo], top_k=3)
    assert hits, f"nenhum card recuperado para o eixo {eixo.value}"

    familia = set(AXIS_TO_NVIDIA_FAMILY[eixo])
    assert hits[0].chunk.technology in familia, (
        f"a query do eixo {eixo.value} rankeia '{hits[0].chunk.technology}' em primeiro, "
        f"que não pertence à família {sorted(familia)}"
    )
