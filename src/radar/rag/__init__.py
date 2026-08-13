"""Camada de RAG sobre a base de conhecimento NVIDIA (Entregável 3).

Fluxo: `ingest` popula Qdrant + BM25 → `hybrid` funde denso e lexical por RRF →
`rerank` corta para o top-N → `cite` monta o contexto e valida as citações da
resposta. Todos os clientes externos (NIM, Qdrant, Cohere) são injetados, então
qualquer etapa roda em teste sem rede.
"""

from __future__ import annotations

from radar.rag.bm25 import BM25Index, LexicalHit, tokenize
from radar.rag.chunking import Chunk, chunk_markdown, estimate_tokens, split_sections
from radar.rag.cite import (
    CitationError,
    CitationReport,
    build_grounded_prompt,
    cited_chunks,
    enforce_citations,
    format_context,
    validate_citations,
)
from radar.rag.embed import EmbeddingClient, InputType, NIMEmbeddingBackend
from radar.rag.hybrid import FusedCandidate, HybridRetriever, reciprocal_rank_fusion
from radar.rag.ingest import IngestReport, ingest_documents, ingest_knowledge_base, load_sources
from radar.rag.rerank import Reranker, RerankPolicy, RerankUnavailableError
from radar.rag.store import DenseHit, QdrantKnowledgeBase

__all__ = [
    "BM25Index",
    "Chunk",
    "CitationError",
    "CitationReport",
    "DenseHit",
    "EmbeddingClient",
    "FusedCandidate",
    "HybridRetriever",
    "IngestReport",
    "InputType",
    "LexicalHit",
    "NIMEmbeddingBackend",
    "QdrantKnowledgeBase",
    "RerankPolicy",
    "RerankUnavailableError",
    "Reranker",
    "build_grounded_prompt",
    "chunk_markdown",
    "cited_chunks",
    "enforce_citations",
    "estimate_tokens",
    "format_context",
    "ingest_documents",
    "ingest_knowledge_base",
    "load_sources",
    "reciprocal_rank_fusion",
    "split_sections",
    "tokenize",
    "validate_citations",
]
