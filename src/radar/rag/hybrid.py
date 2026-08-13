"""Fusão denso + lexical por Reciprocal Rank Fusion.

RRF combina *posições*, não scores. Isso importa porque similaridade de cosseno
(0–1, distribuição estreita) e score BM25 (ilimitado, dependente do corpus) não
são comparáveis em escala nenhuma: qualquer soma ponderada exigiria normalizar,
e normalizar exigiria calibrar um peso que mudaria a cada nova fonte ingerida.
Rank é adimensional — o documento que aparece em 2º lugar nas duas listas ganha
de quem aparece em 1º em uma só, e não há hiperparâmetro para tunar.

A constante `k` (60, o valor do paper original) amortece o topo: sem ela, o 1º
colocado valeria o dobro do 2º e a fusão viraria "quem venceu na lista mais
sortuda".
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import structlog

from radar.config import get_settings
from radar.models.recommendation import RetrievedChunk
from radar.rag.bm25 import BM25Index, LexicalHit
from radar.rag.chunking import Chunk
from radar.rag.embed import EmbeddingClient
from radar.rag.store import DenseHit, QdrantKnowledgeBase

log = structlog.get_logger(__name__)

RRF_K = 60


@dataclass
class FusedCandidate:
    """Candidato após a fusão, com a proveniência de cada sinal preservada.

    Guardar `dense_score` e `bm25_score` separados não é enfeite: quando uma
    recomendação sai estranha, saber se o chunk entrou por similaridade ou por
    match exato de nome de produto é a diferença entre depurar em minutos e
    depurar no escuro.
    """

    chunk: Chunk
    rrf_score: float
    dense_score: float | None = None
    bm25_score: float | None = None
    dense_rank: int | None = None
    bm25_rank: int | None = None

    def to_retrieved_chunk(self) -> RetrievedChunk:
        return self.chunk.to_retrieved_chunk(
            dense_score=self.dense_score,
            bm25_score=self.bm25_score,
            rrf_score=self.rrf_score,
        )


def reciprocal_rank_fusion(
    dense_hits: Sequence[DenseHit],
    lexical_hits: Sequence[LexicalHit],
    *,
    top_k: int | None = None,
    k: int = RRF_K,
) -> list[FusedCandidate]:
    """Funde as duas listas: `score = Σ 1 / (k + posição)`, posição base 1."""
    limit = top_k or get_settings().hybrid_top_k
    candidates: dict[str, FusedCandidate] = {}

    for rank, hit in enumerate(dense_hits, start=1):
        candidate = candidates.setdefault(
            hit.chunk.chunk_id, FusedCandidate(chunk=hit.chunk, rrf_score=0.0)
        )
        candidate.dense_score = hit.score
        candidate.dense_rank = rank
        candidate.rrf_score += 1.0 / (k + rank)

    for rank, hit in enumerate(lexical_hits, start=1):
        candidate = candidates.setdefault(
            hit.chunk.chunk_id, FusedCandidate(chunk=hit.chunk, rrf_score=0.0)
        )
        candidate.bm25_score = hit.score
        candidate.bm25_rank = rank
        candidate.rrf_score += 1.0 / (k + rank)

    # Desempate por chunk_id mantém a ordem determinística entre execuções — sem
    # isso, dois chunks empatados alternam de posição e qualquer avaliação
    # automática (RAGAS) passa a oscilar sem que nada tenha mudado.
    ordered = sorted(candidates.values(), key=lambda c: (-c.rrf_score, c.chunk.chunk_id))
    return ordered[:limit]


def _matches_filter(
    chunk: Chunk,
    technology: str | Sequence[str] | None,
    category: str | Sequence[str] | None,
    doc_type: str | None,
) -> bool:
    """Replica no BM25 o filtro que o Qdrant aplica no lado denso.

    O índice lexical não tem filtro nativo. Se ele ignorasse a restrição, o filtro
    de categoria vazaria pela metade da busca híbrida — e o gap de latência
    receberia um chunk sobre Omniverse com aparência de resultado legítimo.
    """
    for value, actual in ((technology, chunk.technology), (category, chunk.category)):
        if value is None:
            continue
        allowed = {value} if isinstance(value, str) else set(value)
        if allowed and actual not in allowed:
            return False
    return doc_type is None or chunk.doc_type == doc_type


class HybridRetriever:
    """Orquestra embedding da query, busca densa, busca lexical e fusão."""

    def __init__(
        self,
        knowledge_base: QdrantKnowledgeBase,
        bm25_index: BM25Index,
        embedder: EmbeddingClient,
    ) -> None:
        self.knowledge_base = knowledge_base
        self.bm25_index = bm25_index
        self.embedder = embedder

    def retrieve(
        self,
        query: str,
        *,
        top_k: int | None = None,
        technology: str | Sequence[str] | None = None,
        category: str | Sequence[str] | None = None,
        doc_type: str | None = None,
    ) -> list[FusedCandidate]:
        limit = top_k or get_settings().hybrid_top_k

        # `embed_query` e não `embed_passages`: o encoder é assimétrico (ver embed.py).
        vector = self.embedder.embed_query(query)
        dense_hits = self.knowledge_base.search(
            vector,
            top_k=limit,
            technology=technology,
            category=category,
            doc_type=doc_type,
        )

        # Buscamos mais que o limite no lado lexical porque o filtro é aplicado
        # depois: sem folga, um índice grande devolveria só chunks descartados.
        lexical_hits = [
            hit
            for hit in self.bm25_index.search(query, top_k=limit * 3)
            if _matches_filter(hit.chunk, technology, category, doc_type)
        ][:limit]

        fused = reciprocal_rank_fusion(dense_hits, lexical_hits, top_k=limit)
        log.debug(
            "busca_hibrida",
            query=query,
            densos=len(dense_hits),
            lexicais=len(lexical_hits),
            fundidos=len(fused),
        )
        return fused

    def retrieve_chunks(
        self,
        query: str,
        *,
        top_k: int | None = None,
        technology: str | Sequence[str] | None = None,
        category: str | Sequence[str] | None = None,
        doc_type: str | None = None,
    ) -> list[RetrievedChunk]:
        """Mesma busca, já no contrato público (`RetrievedChunk`)."""
        candidates = self.retrieve(
            query,
            top_k=top_k,
            technology=technology,
            category=category,
            doc_type=doc_type,
        )
        return [c.to_retrieved_chunk() for c in candidates]
