"""Testes da camada de RAG (Entregável 3).

Nenhum teste toca a rede ou o Docker: cliente de embedding, Qdrant e Cohere são
injetados e substituídos por fakes. Isso não é só conveniência de CI — é o que
prova que as dependências externas estão de fato desacopladas do pipeline.

O que está travado aqui são as propriedades que, se quebrarem, degradam a
recuperação em silêncio: chunk que ignora a estrutura do documento, fusão que
desempata errado, BM25 que perde nome de produto, citação inventada e ingestão
que duplica a KB a cada rodada.
"""

from __future__ import annotations

import math
from typing import Any

import pytest
import yaml
from pydantic import HttpUrl

from radar.config import Settings
from radar.models.recommendation import RetrievedChunk
from radar.rag.bm25 import BM25Index, LexicalHit, tokenize
from radar.rag.chunking import Chunk, chunk_markdown, estimate_tokens, split_sections
from radar.rag.cite import (
    CitationError,
    build_grounded_prompt,
    cited_chunks,
    enforce_citations,
    format_context,
    validate_citations,
)
from radar.rag.embed import EmbeddingClient, InputType
from radar.rag.hybrid import HybridRetriever, reciprocal_rank_fusion
from radar.rag.ingest import (
    RawDocument,
    SourceDocument,
    ingest_knowledge_base,
    load_cards,
    load_sources,
)
from radar.rag.rerank import Reranker, RerankPolicy, RerankUnavailableError
from radar.rag.store import DenseHit, QdrantKnowledgeBase, point_id_for

# --------------------------------------------------------------------------- #
# Fakes                                                                        #
# --------------------------------------------------------------------------- #


class FakeEmbeddingBackend:
    """Embedding determinístico por saco de palavras com hashing.

    Textos que compartilham vocabulário ficam próximos no cosseno, o que basta
    para exercitar ordenação. Registra `input_type` de cada chamada porque a
    assimetria query/passage é justamente o que não pode regredir sem alarme.
    """

    dim = 64

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], InputType]] = []

    def embed(self, texts, input_type):  # type: ignore[no-untyped-def]
        self.calls.append((list(texts), input_type))
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self.dim
            for token in tokenize(text):
                vector[hash(token) % self.dim] += 1.0
            norm = math.sqrt(sum(v * v for v in vector)) or 1.0
            vectors.append([v / norm for v in vector])
        return vectors

    @property
    def embedded_texts(self) -> list[str]:
        return [t for texts, _ in self.calls for t in texts]


class _FakePoint:
    def __init__(self, payload: dict[str, Any], score: float = 0.0) -> None:
        self.payload = payload
        self.score = score


class _FakeResponse:
    def __init__(self, points: list[_FakePoint]) -> None:
        self.points = points


class FakeQdrantClient:
    """Qdrant em memória, com busca por cosseno e filtro de payload."""

    def __init__(self) -> None:
        self.points: dict[str, tuple[list[float], dict[str, Any]]] = {}
        self.collections: set[str] = set()
        self.indexed_fields: list[str] = []
        self.upsert_calls = 0

    def collection_exists(self, collection_name: str) -> bool:
        return collection_name in self.collections

    def create_collection(self, collection_name: str, vectors_config: Any) -> None:
        self.collections.add(collection_name)
        self.vectors_config = vectors_config

    def create_payload_index(
        self, collection_name: str, field_name: str, field_schema: Any
    ) -> None:
        self.indexed_fields.append(field_name)

    def upsert(self, collection_name: str, points: Any) -> None:
        self.upsert_calls += 1
        for point in points:
            self.points[str(point.id)] = (list(point.vector), dict(point.payload))

    def retrieve(self, collection_name: str, ids: Any, **kwargs: Any) -> list[_FakePoint]:
        return [_FakePoint(self.points[str(i)][1]) for i in ids if str(i) in self.points]

    def query_points(self, collection_name: str, **kwargs: Any) -> _FakeResponse:
        query = kwargs["query"]
        query_filter = kwargs.get("query_filter")
        limit = kwargs.get("limit", 10)

        scored: list[_FakePoint] = []
        for vector, payload in self.points.values():
            if not _payload_matches(payload, query_filter):
                continue
            score = sum(a * b for a, b in zip(query, vector, strict=False))
            scored.append(_FakePoint(payload, score))
        scored.sort(key=lambda p: p.score, reverse=True)
        return _FakeResponse(scored[:limit])


def _payload_matches(payload: dict[str, Any], query_filter: Any) -> bool:
    if query_filter is None:
        return True
    for condition in query_filter.must or []:
        actual = payload.get(condition.key)
        match = condition.match
        if hasattr(match, "value") and actual != match.value:
            return False
        if hasattr(match, "any") and actual not in match.any:
            return False
    return True


class FakeRerankBackend:
    """Cross-encoder de mentira: pontua pela contagem de termos da query."""

    def __init__(self) -> None:
        self.calls = 0

    def rerank(self, query, documents, top_n):  # type: ignore[no-untyped-def]
        self.calls += 1
        termos = set(tokenize(query))
        pontuados = [
            (i, len(termos & set(tokenize(doc))) / (len(termos) or 1))
            for i, doc in enumerate(documents)
        ]
        pontuados.sort(key=lambda pair: pair[1], reverse=True)
        return pontuados[:top_n]


def _chunk(text: str, *, url: str = "https://docs.nvidia.com/a", **kwargs: Any) -> Chunk:
    return Chunk(text=text, source_url=url, **kwargs)


def _retrieved(
    text: str, *, rrf: float | None = None, url: str = "https://x.com/a"
) -> RetrievedChunk:
    return RetrievedChunk(text=text, source_url=HttpUrl(url), rrf_score=rrf)


# --------------------------------------------------------------------------- #
# Chunking                                                                     #
# --------------------------------------------------------------------------- #

DOC_TECNICO = """\
# Triton Inference Server

O Triton serve modelos em producao com suporte a multiplos backends. TRITONMARK
identifica esta secao nos testes.

## Instalacao do Triton

Suba o container e aponte para o repositorio de modelos. TRITONMARK aparece aqui
tambem porque a secao pertence ao mesmo produto.

# TensorRT-LLM

Compila modelos de linguagem para kernels otimizados. TRTMARK identifica esta
outra secao, de um produto diferente.

## Quantizacao no TensorRT-LLM

Suporta INT8 e FP8 com calibracao. TRTMARK segue presente aqui.
"""


def test_split_sections_reconstroi_hierarquia():
    """Heading de nivel N fecha os de nivel >= N — sem isso o caminho vira lixo."""
    secoes = split_sections(DOC_TECNICO)
    caminhos = [s.heading_path for s in secoes]

    assert ("Triton Inference Server",) in caminhos
    assert ("Triton Inference Server", "Instalacao do Triton") in caminhos
    assert ("TensorRT-LLM", "Quantizacao no TensorRT-LLM") in caminhos


def test_chunk_nunca_mistura_produtos_diferentes():
    """A propriedade que justifica o chunking semantico.

    Um chunk que atravessa a fronteira entre Triton e TensorRT-LLM responderia
    mal as duas perguntas e ainda contaminaria a citacao do briefing.
    """
    chunks = chunk_markdown(DOC_TECNICO, source_url="https://docs.nvidia.com/x")

    assert chunks
    for chunk in chunks:
        assert not ("TRITONMARK" in chunk.text and "TRTMARK" in chunk.text)
        assert chunk.heading_path, "todo chunk carrega o caminho de headings"
        assert chunk.section_title == chunk.heading_path[-1]


def test_chunk_herda_metadados_da_fonte():
    chunks = chunk_markdown(
        DOC_TECNICO,
        source_url="https://developer.nvidia.com/triton-inference-server",
        source_title="NVIDIA Triton",
        technology="Triton Inference Server",
        category="inferencia",
    )
    primeiro = chunks[0]

    assert primeiro.technology == "Triton Inference Server"
    assert primeiro.category == "inferencia"
    # O caminho entra no texto enviado ao encoder, nao so no metadado.
    assert "NVIDIA Triton" in primeiro.contextualized_text
    assert "Triton Inference Server" in primeiro.contextualized_text


def test_secao_longa_e_dividida_com_overlap():
    """Overlap existe para que resposta a cavaleiro da fronteira nao se perca."""
    paragrafos = [
        f"Paragrafo {i:02d} descreve um aspecto especifico da configuracao "
        f"de deployment do servidor de inferencia em producao."
        for i in range(20)
    ]
    doc = "# Secao unica e longa\n\n" + "\n\n".join(paragrafos)

    chunks = chunk_markdown(
        doc, source_url="https://docs.nvidia.com/long", target_tokens=100, overlap_ratio=0.3
    )

    assert len(chunks) >= 3, "a secao deveria ter sido dividida"

    for anterior, seguinte in zip(chunks, chunks[1:], strict=False):
        primeiro_paragrafo = seguinte.text.split("\n\n")[0]
        assert primeiro_paragrafo in anterior.text, "o chunk seguinte repete a cauda do anterior"

    # A repeticao nao pode inflar o chunk muito acima do alvo.
    assert all(c.token_estimate <= 100 * 1.35 for c in chunks)


def test_secao_pequena_nao_e_esticada():
    """Alvo de tokens e teto, nao meta: nao enchemos chunk com conteudo alheio."""
    chunks = chunk_markdown("# Titulo\n\nUma frase curta.", source_url="https://a.com/b")
    assert len(chunks) == 1
    assert chunks[0].text == "Uma frase curta."


def test_chunk_id_e_estavel_e_sensivel_ao_conteudo():
    """Base da idempotencia: mesmo conteudo, mesmo id; conteudo diferente, id diferente."""
    a = _chunk("mesmo texto exatamente igual")
    b = _chunk("mesmo texto exatamente igual")
    c = _chunk("texto alterado depois da atualizacao da pagina")

    assert a.chunk_id == b.chunk_id
    assert a.chunk_id != c.chunk_id


def test_estimativa_de_tokens_nunca_subestima_grosseiramente():
    """Subestimar trunca o chunk no encoder sem aviso — o erro caro."""
    prosa = " ".join(["palavra"] * 100)
    assert estimate_tokens(prosa) >= 100
    assert estimate_tokens("") == 0


# --------------------------------------------------------------------------- #
# BM25                                                                         #
# --------------------------------------------------------------------------- #


def test_tokenizacao_preserva_nome_de_produto_e_dobra_acento():
    tokens = tokenize("TensorRT-LLM acelera a inferência com FP8")

    assert "tensorrt-llm" in tokens, "o token composto precisa sobreviver inteiro"
    assert "tensorrt" in tokens, "e tambem em partes, para casar com a query curta"
    assert "inferencia" in tokens, "acento dobrado: a pergunta em pt raramente vem acentuada"
    assert "fp8" in tokens


def test_bm25_encontra_nome_exato_que_o_denso_perderia():
    """Justificativa do indice lexical.

    Os tres documentos falam de inferencia otimizada — sao vizinhos no espaco
    semantico. So o match exato do identificador separa o certo dos outros.
    """
    corpus = [
        _chunk("Otimize a latencia de inferencia com compilacao de kernels na GPU."),
        _chunk("TensorRT-LLM compila modelos de linguagem para execucao otimizada."),
        _chunk("Servico de inferencia com batching dinamico e multiplos backends."),
    ]
    index = BM25Index(corpus)

    hits = index.search("TensorRT-LLM", top_k=3)

    assert hits, "match lexical exato nao pode voltar vazio"
    assert "TensorRT-LLM" in hits[0].chunk.text


def test_bm25_ignora_documento_sem_nenhum_termo_da_query():
    index = BM25Index([_chunk("Omniverse simula ambientes tridimensionais.")])
    assert index.search("quantizacao fp8") == []


def test_bm25_persiste_e_recarrega(tmp_path):
    """O indice vive em disco entre a ingestao e a consulta — processos distintos."""
    index = BM25Index([_chunk("Triton Inference Server suporta model ensembles.")])
    index.save(tmp_path)

    recarregado = BM25Index.load(tmp_path)

    assert len(recarregado) == 1
    assert recarregado.search("model ensembles")[0].chunk.text == index.chunks[0].text


def test_bm25_ausente_nao_quebra():
    """Antes do primeiro `make ingest` o indice nao existe — e isso e normal."""
    assert len(BM25Index.load("/tmp/diretorio-que-nao-existe-radar")) == 0


def test_bm25_upsert_nao_duplica():
    chunk = _chunk("NeMo Curator prepara dados para fine-tuning.")
    index = BM25Index([chunk])

    novos = index.upsert([chunk])

    assert novos == 0
    assert len(index) == 1


# --------------------------------------------------------------------------- #
# Fusão (RRF)                                                                  #
# --------------------------------------------------------------------------- #


def test_rrf_premia_consenso_entre_listas_divergentes():
    """O documento mediano nas duas listas vence o primeiro colocado de uma so.

    E o comportamento que torna a fusao util: concordancia entre sinais
    independentes vale mais que confianca isolada de um deles. Aqui `doc b` e
    apenas 2o em ambas as listas e ainda assim ganha de `doc a` (1o no denso,
    ausente no lexical) e de `doc d` (1o no lexical, ausente no denso).
    """
    a, b, c, d = _chunk("doc a"), _chunk("doc b"), _chunk("doc c"), _chunk("doc d")

    densos = [
        DenseHit(chunk=a, score=0.9),
        DenseHit(chunk=b, score=0.8),
        DenseHit(chunk=c, score=0.7),
    ]
    lexicais = [LexicalHit(chunk=d, score=12.0), LexicalHit(chunk=b, score=9.0)]

    fundidos = reciprocal_rank_fusion(densos, lexicais, top_k=10)
    ordem = [f.chunk.text for f in fundidos]

    assert ordem[0] == "doc b"
    assert set(ordem[1:3]) == {"doc a", "doc d"}, "empatados: 1o em uma lista so"
    assert ordem[3] == "doc c"


def test_rrf_preenche_a_procedencia_de_cada_sinal():
    a, b = _chunk("doc a"), _chunk("doc b")
    fundidos = reciprocal_rank_fusion(
        [DenseHit(chunk=a, score=0.42)], [LexicalHit(chunk=b, score=7.5)], top_k=10
    )
    por_texto = {f.chunk.text: f for f in fundidos}

    assert por_texto["doc a"].dense_score == 0.42
    assert por_texto["doc a"].bm25_score is None
    assert por_texto["doc b"].bm25_score == 7.5
    assert all(f.rrf_score > 0 for f in fundidos)


def test_rrf_respeita_o_limite_e_e_deterministico():
    chunks = [_chunk(f"doc {i}") for i in range(10)]
    densos = [DenseHit(chunk=c, score=1.0 - i / 10) for i, c in enumerate(chunks)]

    primeira = reciprocal_rank_fusion(densos, [], top_k=3)
    segunda = reciprocal_rank_fusion(densos, [], top_k=3)

    assert len(primeira) == 3
    assert [f.chunk.chunk_id for f in primeira] == [f.chunk.chunk_id for f in segunda]


# --------------------------------------------------------------------------- #
# Busca híbrida ponta a ponta (com fakes)                                      #
# --------------------------------------------------------------------------- #


def _kb_com_chunks(chunks: list[Chunk]) -> tuple[QdrantKnowledgeBase, EmbeddingClient, BM25Index]:
    backend = FakeEmbeddingBackend()
    embedder = EmbeddingClient(backend, model="fake", batch_size=2)
    kb = QdrantKnowledgeBase(FakeQdrantClient(), collection="teste", vector_size=backend.dim)
    kb.ensure_collection()
    kb.upsert_chunks(chunks, embedder.embed_passages([c.contextualized_text for c in chunks]))
    return kb, embedder, BM25Index(chunks)


def test_busca_hibrida_recupera_com_filtro_de_categoria():
    """Filtro pre-busca: gap de latencia nao pode receber documento de robotica."""
    chunks = [
        _chunk("Triton faz batching dinamico de requisicoes.", category="inferencia",
               technology="Triton Inference Server"),
        _chunk("Isaac simula robos em ambiente virtual.", category="robotica",
               technology="Isaac", url="https://docs.nvidia.com/b"),
    ]
    kb, embedder, index = _kb_com_chunks(chunks)
    retriever = HybridRetriever(kb, index, embedder)

    resultados = retriever.retrieve("como reduzir latencia de inferencia", category="inferencia")

    assert resultados
    assert all(c.chunk.category == "inferencia" for c in resultados), (
        "o filtro precisa valer tambem no lado lexical, senao vaza pela metade"
    )


def test_busca_hibrida_usa_input_type_de_query():
    """Regressao silenciosa mais cara do modulo de embeddings."""
    chunks = [_chunk("NIM entrega microservicos de inferencia.")]
    kb, embedder, index = _kb_com_chunks(chunks)
    backend = embedder.backend

    HybridRetriever(kb, index, embedder).retrieve("o que e NIM")

    tipos = [tipo for _, tipo in backend.calls]  # type: ignore[attr-defined]
    assert tipos[0] is InputType.PASSAGE, "a indexacao usa passage"
    assert tipos[-1] is InputType.QUERY, "a consulta usa query"


def test_busca_hibrida_devolve_contrato_publico():
    chunks = [_chunk("NeMo Guardrails aplica politicas de seguranca ao dialogo.")]
    kb, embedder, index = _kb_com_chunks(chunks)

    recuperados = HybridRetriever(kb, index, embedder).retrieve_chunks("guardrails")

    assert isinstance(recuperados[0], RetrievedChunk)
    assert recuperados[0].rrf_score is not None
    assert recuperados[0].rerank_score is None, "ainda nao passou pelo reranker"


# --------------------------------------------------------------------------- #
# Embeddings                                                                   #
# --------------------------------------------------------------------------- #


def test_cache_evita_reembeddar_o_mesmo_texto(tmp_path):
    from radar.rag.embed import DiskEmbeddingCache

    backend = FakeEmbeddingBackend()
    cache = DiskEmbeddingCache(tmp_path)
    client = EmbeddingClient(backend, model="fake", cache=cache)

    primeiro = client.embed_passages(["texto identico"])
    segundo = client.embed_passages(["texto identico"])

    assert primeiro == segundo
    assert len(backend.embedded_texts) == 1, "a segunda chamada saiu do cache"


def test_cache_separa_query_de_passage(tmp_path):
    """Chave do cache inclui input_type: reaproveitar entre tipos anularia a assimetria."""
    from radar.rag.embed import DiskEmbeddingCache

    backend = FakeEmbeddingBackend()
    client = EmbeddingClient(backend, model="fake", cache=DiskEmbeddingCache(tmp_path))

    client.embed_passages(["mesmo texto"])
    client.embed_queries(["mesmo texto"])

    assert len(backend.calls) == 2


def test_batching_respeita_o_tamanho_do_lote():
    backend = FakeEmbeddingBackend()
    client = EmbeddingClient(backend, model="fake", batch_size=2)

    vetores = client.embed_passages([f"texto {i}" for i in range(5)])

    assert len(vetores) == 5
    assert len(backend.calls) == 3


# --------------------------------------------------------------------------- #
# Store                                                                        #
# --------------------------------------------------------------------------- #


def test_colecao_e_criada_com_indices_de_payload():
    """Sem payload index, o filtro por categoria vira varredura."""
    client = FakeQdrantClient()
    kb = QdrantKnowledgeBase(client, collection="nvidia_kb", vector_size=8)

    kb.ensure_collection()
    kb.ensure_collection()  # idempotente

    assert client.collections == {"nvidia_kb"}
    assert "technology" in client.indexed_fields
    assert "category" in client.indexed_fields


def test_upsert_do_mesmo_chunk_sobrescreve_o_mesmo_ponto():
    client = FakeQdrantClient()
    kb = QdrantKnowledgeBase(client, collection="nvidia_kb", vector_size=2)
    kb.ensure_collection()
    chunk = _chunk("mesmo conteudo em duas rodadas")

    kb.upsert_chunks([chunk], [[1.0, 0.0]])
    kb.upsert_chunks([chunk], [[1.0, 0.0]])

    assert len(client.points) == 1
    assert point_id_for(chunk.chunk_id) in client.points


def test_desalinhamento_entre_chunks_e_vetores_falha_alto():
    kb = QdrantKnowledgeBase(FakeQdrantClient(), collection="c", vector_size=2)
    with pytest.raises(ValueError):
        kb.upsert_chunks([_chunk("a"), _chunk("b")], [[1.0, 0.0]])


# --------------------------------------------------------------------------- #
# Rerank                                                                       #
# --------------------------------------------------------------------------- #


def test_rerank_preenche_o_score_e_corta_no_top_n():
    chunks = [
        _retrieved("Triton faz batching dinamico para inferencia", rrf=0.1),
        _retrieved("Omniverse renderiza cenas 3D", rrf=0.9),
        _retrieved("Riva transcreve audio", rrf=0.5),
    ]
    reranker = Reranker(FakeRerankBackend(), top_n=2)

    resultado = reranker.rerank("batching dinamico de inferencia", chunks)

    assert len(resultado) == 2
    assert resultado[0].rerank_score is not None
    assert "Triton" in resultado[0].text, "o cross-encoder deve subir o relevante"


def test_sem_chave_a_politica_estrita_falha_alto(monkeypatch):
    """Nunca fingir que reranqueou: sem chave, ou erro explicito ou fallback rotulado."""
    monkeypatch.setattr(
        "radar.rag.rerank.get_settings", lambda: Settings(cohere_api_key="", rerank_top_n=5)
    )
    reranker = Reranker(policy=RerankPolicy.STRICT)

    with pytest.raises(RerankUnavailableError):
        reranker.rerank("qualquer pergunta", [_retrieved("um trecho", rrf=0.2)])


def test_fallback_mantem_ordem_rrf_e_nao_inventa_score(monkeypatch):
    monkeypatch.setattr(
        "radar.rag.rerank.get_settings", lambda: Settings(cohere_api_key="", rerank_top_n=5)
    )
    chunks = [_retrieved("menos relevante", rrf=0.1), _retrieved("mais relevante", rrf=0.9)]

    resultado = Reranker(policy=RerankPolicy.FALLBACK_RRF, top_n=2).rerank("pergunta", chunks)

    assert [c.text for c in resultado] == ["mais relevante", "menos relevante"]
    assert all(c.rerank_score is None for c in resultado), (
        "score de rerank preenchido sem rerank seria numero com procedencia falsa"
    )


def test_indice_fora_da_faixa_do_reranker_falha():
    """Associar score ao chunk errado e pior que erro — a citacao apontaria para outra fonte."""

    class BackendMaluco:
        def rerank(self, query, documents, top_n):  # type: ignore[no-untyped-def]
            return [(99, 0.9)]

    with pytest.raises(RerankUnavailableError):
        Reranker(BackendMaluco(), top_n=1).rerank("q", [_retrieved("a", rrf=0.1)])


# --------------------------------------------------------------------------- #
# Citações                                                                     #
# --------------------------------------------------------------------------- #


def _tres_chunks() -> list[RetrievedChunk]:
    return [
        _retrieved("O Triton suporta batching dinamico.", url="https://a.com/1"),
        _retrieved("O TensorRT-LLM compila kernels.", url="https://a.com/2"),
        _retrieved("O NIM expoe endpoints compativeis com OpenAI.", url="https://a.com/3"),
    ]


def test_contexto_numera_os_trechos_a_partir_de_um():
    contexto = format_context(_tres_chunks())
    assert contexto.startswith("[1] ")
    assert "[3] " in contexto
    assert "https://a.com/2" in contexto


def test_prompt_sem_trecho_e_bloqueado():
    """Gerar sem contexto produz texto plausivel sem fonte — exatamente o proibido."""
    with pytest.raises(CitationError):
        build_grounded_prompt("qual tecnologia recomendar?", [])


def test_validacao_pega_citacao_inexistente():
    """Anti-alucinacao de fonte: [7] com tres trechos e invencao."""
    resposta = "O Triton faz batching dinamico e reduz o custo por requisicao [7]."

    report = validate_citations(resposta, _tres_chunks())

    assert report.invalid_indices == [7]
    assert not report.is_valid
    with pytest.raises(CitationError, match="7"):
        enforce_citations(resposta, _tres_chunks())


def test_validacao_pega_afirmacao_sem_fonte():
    resposta = (
        "O Triton suporta batching dinamico [1]. "
        "A startup vai economizar oitenta por cento do custo de inferencia."
    )

    report = validate_citations(resposta, _tres_chunks())

    assert report.invalid_indices == []
    assert len(report.unsupported_sentences) == 1
    assert "oitenta por cento" in report.unsupported_sentences[0]


def test_resposta_bem_fundamentada_passa():
    resposta = "O Triton suporta batching dinamico [1]. O TensorRT-LLM compila kernels [2]."

    report = enforce_citations(resposta, _tres_chunks())

    assert report.cited_indices == [1, 2]
    assert report.uncited_chunk_indices == [3]
    assert report.coverage == pytest.approx(2 / 3)


def test_apenas_os_trechos_citados_viram_evidencia():
    """Anexar os cinco recuperados inflaria a aparencia de fundamentacao."""
    resposta = "Vale usar o NIM como primeiro passo, pelo endpoint compativel [3]."

    citados = cited_chunks(resposta, _tres_chunks())

    assert len(citados) == 1
    assert "NIM" in citados[0].text


# --------------------------------------------------------------------------- #
# Ingestão                                                                     #
# --------------------------------------------------------------------------- #

MARKDOWN_FONTE = """\
# NVIDIA NIM

Microservicos de inferencia empacotados como container. MARCADOR-NIM.

## Pre-requisitos do NIM

Precisa de GPU compativel e de uma chave do API Catalog.
"""


class FakeFetcher:
    """Coletor sem rede. Conta acessos para provar que a reingestao nao re-baixa."""

    def __init__(self, conteudo: dict[str, str]) -> None:
        self.conteudo = conteudo
        self.fetches: list[str] = []

    def fetch(self, url: str) -> str | None:
        self.fetches.append(url)
        return self.conteudo.get(url)


@pytest.fixture
def sources_yaml(tmp_path):
    path = tmp_path / "nvidia_sources.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "version": "0.1.0",
                "inferencia": [
                    {
                        "url": "https://docs.nvidia.com/nim",
                        "title": "NVIDIA NIM",
                        "category": "inferencia",
                        "technology": "NIM",
                    }
                ],
                "cards_manuais": {"path": "data/nvidia_cards/"},
            }
        ),
        encoding="utf-8",
    )
    return path


def test_load_sources_achata_secoes_e_ignora_metadados(sources_yaml):
    fontes = load_sources(sources_yaml)

    assert len(fontes) == 1
    assert fontes[0].technology == "NIM"
    assert fontes[0].section == "inferencia"


def test_load_sources_le_o_arquivo_real_do_projeto():
    """Guarda contra o YAML da KB quebrar sem ninguem perceber."""
    fontes = load_sources()

    assert len(fontes) > 10
    assert any(f.technology == "TensorRT-LLM" for f in fontes)
    assert all(f.url.startswith("http") for f in fontes)


def test_cards_manuais_ausentes_nao_quebram(tmp_path):
    """Estado atual do repo: os cards ainda nao existem."""
    assert load_cards(tmp_path / "nao-existe") == []


def test_card_manual_e_ingerido_com_doc_type_proprio(tmp_path):
    (tmp_path / "triton.md").write_text(
        "---\ntechnology: Triton Inference Server\ncategory: inferencia\n---\n"
        "# Quando recomendar o Triton\n\nStartup com varios modelos em producao.\n",
        encoding="utf-8",
    )

    cards = load_cards(tmp_path)

    assert len(cards) == 1
    assert cards[0].source.doc_type == "card"
    assert cards[0].source.technology == "Triton Inference Server"
    # Sem URL canonica, o card recebe host sentinela — a citacao continua rastreavel.
    assert cards[0].source.url.startswith("https://kb.local.invalid/")


def test_ingestao_e_idempotente(tmp_path, sources_yaml):
    """Rodar duas vezes nao pode duplicar chunk nem gastar quota de embedding.

    E a garantia que sustenta o desenvolvimento iterativo: mexer no chunking e
    reingerir precisa ser barato e seguro.
    """
    fetcher = FakeFetcher({"https://docs.nvidia.com/nim": MARKDOWN_FONTE})
    backend = FakeEmbeddingBackend()
    embedder = EmbeddingClient(backend, model="fake")
    client = FakeQdrantClient()
    kb = QdrantKnowledgeBase(client, collection="nvidia_kb", vector_size=backend.dim)
    index = BM25Index()

    comum = dict(
        knowledge_base=kb,
        embedder=embedder,
        bm25_index=index,
        fetcher=fetcher,
        sources_path=sources_yaml,
        cards_dir=tmp_path / "sem-cards",
        bm25_dir=tmp_path / "bm25",
    )

    primeira = ingest_knowledge_base(**comum)
    pontos_apos_primeira = len(client.points)
    embeddings_apos_primeira = len(backend.embedded_texts)

    segunda = ingest_knowledge_base(**comum)

    assert primeira.chunks_new > 0
    assert segunda.chunks_total == primeira.chunks_total
    assert segunda.chunks_new == 0, "nada novo na segunda rodada"
    assert len(client.points) == pontos_apos_primeira, "a KB nao pode crescer sozinha"
    assert len(backend.embedded_texts) == embeddings_apos_primeira, "quota nao pode ser gasta a toa"
    assert len(index) == pontos_apos_primeira


def test_ingestao_registra_fonte_indisponivel(tmp_path, sources_yaml):
    """Uma fonte fora do ar nao derruba o lote, mas precisa aparecer no relatorio."""
    backend = FakeEmbeddingBackend()
    kb = QdrantKnowledgeBase(FakeQdrantClient(), collection="c", vector_size=backend.dim)

    report = ingest_knowledge_base(
        knowledge_base=kb,
        embedder=EmbeddingClient(backend, model="fake"),
        bm25_index=BM25Index(),
        fetcher=FakeFetcher({}),
        sources_path=sources_yaml,
        cards_dir=tmp_path / "sem-cards",
        bm25_dir=tmp_path / "bm25",
    )

    assert report.documents_failed == 1
    assert report.failed_urls == ["https://docs.nvidia.com/nim"]
    assert report.is_empty


def test_chunks_identicos_de_urls_diferentes_sao_deduplicados(tmp_path):
    """Boilerplate repetido entre paginas da NVIDIA ocuparia vagas do top-N."""
    from radar.rag.ingest import collect_chunks

    corpo = "# Inception\n\nO programa oferece creditos de nuvem e suporte tecnico."
    documentos = [
        RawDocument(source=SourceDocument(url="https://nvidia.com/a"), markdown=corpo),
        RawDocument(source=SourceDocument(url="https://nvidia.com/a"), markdown=corpo),
        RawDocument(source=SourceDocument(url="https://nvidia.com/b"), markdown=corpo),
    ]

    chunks = collect_chunks(documentos)

    # Mesma URL + mesmo texto = mesmo chunk. URL diferente permanece, porque a
    # procedencia faz parte da evidencia.
    assert len(chunks) == 2
