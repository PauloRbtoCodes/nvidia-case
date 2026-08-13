"""Vector store da KB NVIDIA (Qdrant).

Duas decisões carregam este módulo:

1. **Id determinístico.** O id do ponto é um UUIDv5 derivado do hash do conteúdo,
   não um contador. Reingerir a mesma página sobrescreve o mesmo ponto em vez de
   criar um gêmeo — idempotência sem passo de limpeza prévia, que é o que evita
   uma KB com três cópias do mesmo parágrafo enviesando o ranking.

2. **Payload index em `technology` e `category`.** Sem índice, filtrar por
   categoria no Qdrant é varredura; com índice, o filtro acontece *antes* da
   busca vetorial. Isso é o que permite a regra do case: gap de latência restringe
   a recuperação a documentos de `inferencia`, em vez de torcer para que o
   ranking semântico não traga um artigo de Omniverse no top-5.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import structlog
from qdrant_client import models as qmodels

from radar.config import get_settings
from radar.rag.chunking import Chunk

log = structlog.get_logger(__name__)

#: Namespace fixo para o UUIDv5. Mudá-lo invalida a KB inteira — não mude.
_POINT_NAMESPACE = uuid.UUID("6f1b4a0e-3c1c-4a35-9f0d-2a0f4b3c9d11")

#: Campos com payload index. Só o que é realmente usado como filtro pré-busca:
#: índice em campo que ninguém filtra custa memória e não devolve nada.
INDEXED_PAYLOAD_FIELDS = ("technology", "category", "doc_type", "chunk_id")


def point_id_for(chunk_id: str) -> str:
    """Id estável do ponto. Qdrant só aceita UUID ou inteiro, daí o UUIDv5."""
    return str(uuid.uuid5(_POINT_NAMESPACE, chunk_id))


@dataclass(frozen=True)
class DenseHit:
    """Resultado da busca vetorial, antes da fusão."""

    chunk: Chunk
    score: float


class QdrantLike(Protocol):
    """Subconjunto do `QdrantClient` que usamos — o resto é ruído para o teste."""

    def collection_exists(self, collection_name: str) -> bool: ...
    def create_collection(self, collection_name: str, vectors_config: Any) -> Any: ...
    def create_payload_index(
        self, collection_name: str, field_name: str, field_schema: Any
    ) -> Any: ...
    def upsert(self, collection_name: str, points: Any) -> Any: ...
    def query_points(self, collection_name: str, **kwargs: Any) -> Any: ...
    def retrieve(self, collection_name: str, ids: Any, **kwargs: Any) -> Any: ...


def build_filter(
    technology: str | Sequence[str] | None = None,
    category: str | Sequence[str] | None = None,
    doc_type: str | None = None,
) -> qmodels.Filter | None:
    """Filtro pré-busca. `None` quando não há restrição — não filtrar é diferente
    de filtrar por nada."""
    conditions: list[qmodels.FieldCondition] = []

    for field, value in (("technology", technology), ("category", category)):
        if value is None:
            continue
        match: qmodels.MatchValue | qmodels.MatchAny
        if isinstance(value, str):
            match = qmodels.MatchValue(value=value)
        else:
            values = list(value)
            if not values:
                continue
            match = qmodels.MatchAny(any=values)
        conditions.append(qmodels.FieldCondition(key=field, match=match))

    if doc_type is not None:
        conditions.append(
            qmodels.FieldCondition(key="doc_type", match=qmodels.MatchValue(value=doc_type))
        )

    return qmodels.Filter(must=conditions) if conditions else None


class QdrantKnowledgeBase:
    """Fachada sobre o Qdrant para a coleção `nvidia_kb`."""

    def __init__(
        self,
        client: QdrantLike,
        *,
        collection: str | None = None,
        vector_size: int = 1024,
        distance: qmodels.Distance = qmodels.Distance.COSINE,
    ) -> None:
        self.client = client
        self.collection = collection or get_settings().qdrant_collection
        self.vector_size = vector_size
        self.distance = distance

    def ensure_collection(self) -> None:
        """Criação idempotente: coleção + índices de payload.

        `create_payload_index` é idempotente no Qdrant, então é seguro chamar em
        toda ingestão — o que garante que uma coleção criada por uma versão antiga
        do código ganhe os índices novos sem migração manual.
        """
        if not self.client.collection_exists(self.collection):
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config=qmodels.VectorParams(
                    size=self.vector_size, distance=self.distance
                ),
            )
            log.info("colecao_criada", colecao=self.collection, dim=self.vector_size)

        for field in INDEXED_PAYLOAD_FIELDS:
            self.client.create_payload_index(
                collection_name=self.collection,
                field_name=field,
                field_schema=qmodels.PayloadSchemaType.KEYWORD,
            )

    def upsert_chunks(
        self,
        chunks: Sequence[Chunk],
        vectors: Sequence[Sequence[float]],
        *,
        batch_size: int = 64,
    ) -> int:
        """Grava chunks e vetores em lote. Retorna quantos pontos foram enviados."""
        if len(chunks) != len(vectors):
            raise ValueError(
                f"{len(chunks)} chunks para {len(vectors)} vetores — "
                "o alinhamento por posição foi quebrado em algum ponto do pipeline."
            )

        total = 0
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            batch_vectors = vectors[start : start + batch_size]
            points = [
                qmodels.PointStruct(
                    id=point_id_for(chunk.chunk_id),
                    vector=list(vector),
                    payload=chunk.to_payload(),
                )
                for chunk, vector in zip(batch, batch_vectors, strict=True)
            ]
            self.client.upsert(collection_name=self.collection, points=points)
            total += len(points)

        log.info("chunks_indexados", colecao=self.collection, pontos=total)
        return total

    def existing_chunk_ids(self, chunk_ids: Iterable[str]) -> set[str]:
        """Quais desses chunks já estão na coleção — usado só para relatório.

        A idempotência não depende desta checagem (o id determinístico já
        garante), mas saber quantos chunks são realmente novos é o que distingue
        "a fonte mudou" de "rodei duas vezes".
        """
        ids = list(chunk_ids)
        if not ids:
            return set()

        found = self.client.retrieve(
            collection_name=self.collection,
            ids=[point_id_for(cid) for cid in ids],
            with_payload=True,
            with_vectors=False,
        )
        return {
            payload["chunk_id"]
            for point in found
            if (payload := getattr(point, "payload", None)) and payload.get("chunk_id")
        }

    def search(
        self,
        vector: Sequence[float],
        *,
        top_k: int | None = None,
        technology: str | Sequence[str] | None = None,
        category: str | Sequence[str] | None = None,
        doc_type: str | None = None,
    ) -> list[DenseHit]:
        """Busca densa com filtro opcional aplicado antes do percurso do índice."""
        limit = top_k or get_settings().hybrid_top_k
        response = self.client.query_points(
            collection_name=self.collection,
            query=list(vector),
            limit=limit,
            query_filter=build_filter(technology, category, doc_type),
            with_payload=True,
        )
        points = getattr(response, "points", response)
        return [
            DenseHit(chunk=Chunk.from_payload(point.payload), score=float(point.score))
            for point in points
            if point.payload
        ]


def build_knowledge_base(
    client: QdrantLike | None = None, *, vector_size: int = 1024
) -> QdrantKnowledgeBase:
    """Fábrica padrão. Importa o cliente real só aqui, para que testes não precisem
    de um Qdrant no ar apenas para importar o módulo."""
    if client is None:
        from qdrant_client import QdrantClient

        client = QdrantClient(url=get_settings().qdrant_url)
    return QdrantKnowledgeBase(client, vector_size=vector_size)
