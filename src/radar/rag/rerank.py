"""Reranking com Cohere Rerank v3.5.

O rerank é o passo com maior ganho de precisão por unidade de esforço em todo o
pipeline: o retriever otimiza recall sobre 30 candidatos e não sabe julgar
relevância fina; o cross-encoder lê query e documento juntos e sabe. É o que
separa "top-5 sobre inferência" de "top-5 que responde esta pergunta".

**Degradação sem chave.** Duas políticas explícitas, nenhuma delas silenciosa:

- `STRICT` (padrão): sem `COHERE_API_KEY`, levanta `RerankUnavailableError`.
  Coerente com o resto do projeto — bloquear em vez de degradar.
- `FALLBACK_RRF`: devolve o top-N pela ordem de RRF, com `rerank_score` deixado
  em `None` e um warning no log.

O `rerank_score is None` do fallback é proposital: quem consumir o resultado
consegue distinguir "reranqueado" de "apenas fundido". Preencher esse campo com
o score do RRF seria mentir sobre a procedência do número, e um número com
procedência errada é pior que campo vazio.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import Any, Protocol

import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from radar.config import get_settings
from radar.models.recommendation import RetrievedChunk

log = structlog.get_logger(__name__)


class RerankUnavailableError(RuntimeError):
    """Rerank exigido mas indisponível (sem chave ou provedor fora do ar)."""


class RerankPolicy(StrEnum):
    STRICT = "strict"
    FALLBACK_RRF = "fallback_rrf"


class RerankBackend(Protocol):
    """Contrato: recebe query e documentos, devolve `(índice_original, score)`."""

    def rerank(
        self, query: str, documents: Sequence[str], top_n: int
    ) -> list[tuple[int, float]]: ...


class CohereRerankBackend:
    """Adaptador do SDK da Cohere (`ClientV2.rerank`)."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        client: Any | None = None,
    ) -> None:
        settings = get_settings()
        self.api_key = api_key if api_key is not None else settings.cohere_api_key
        self.model = model or settings.cohere_rerank_model
        self._client = client

    def _cohere(self) -> Any:
        if self._client is None:
            if not self.api_key:
                raise RerankUnavailableError("COHERE_API_KEY vazia.")
            import cohere

            self._client = cohere.ClientV2(api_key=self.api_key)
        return self._client

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=1, max=10),
           reraise=True)
    def rerank(self, query: str, documents: Sequence[str], top_n: int) -> list[tuple[int, float]]:
        response = self._cohere().rerank(
            model=self.model,
            query=query,
            documents=list(documents),
            top_n=top_n,
        )
        return [(result.index, float(result.relevance_score)) for result in response.results]


class Reranker:
    """Aplica o rerank sobre `RetrievedChunk`s vindos da fusão híbrida."""

    def __init__(
        self,
        backend: RerankBackend | None = None,
        *,
        policy: RerankPolicy = RerankPolicy.STRICT,
        top_n: int | None = None,
    ) -> None:
        self.backend = backend
        self.policy = policy
        self.top_n = top_n or get_settings().rerank_top_n

    @property
    def available(self) -> bool:
        return self.backend is not None or bool(get_settings().cohere_api_key)

    def rerank(
        self, query: str, chunks: Sequence[RetrievedChunk], *, top_n: int | None = None
    ) -> list[RetrievedChunk]:
        limit = top_n or self.top_n
        if not chunks:
            return []

        backend = self.backend
        if backend is None:
            if not get_settings().cohere_api_key:
                return self._degrade(chunks, limit, motivo="cohere_api_key_ausente")
            backend = CohereRerankBackend()

        try:
            ranking = backend.rerank(query, [c.text for c in chunks], limit)
        except RerankUnavailableError:
            return self._degrade(chunks, limit, motivo="backend_indisponivel")

        reranked: list[RetrievedChunk] = []
        for index, score in ranking:
            if not 0 <= index < len(chunks):
                # Índice fora da faixa significa que a resposta não corresponde à
                # lista enviada; associar o score ao chunk errado seria pior que falhar.
                raise RerankUnavailableError(
                    f"Rerank devolveu índice {index} para {len(chunks)} documentos."
                )
            chunk = chunks[index].model_copy(update={"rerank_score": score})
            reranked.append(chunk)

        log.info("rerank_aplicado", candidatos=len(chunks), selecionados=len(reranked))
        return reranked[:limit]

    def _degrade(
        self, chunks: Sequence[RetrievedChunk], limit: int, *, motivo: str
    ) -> list[RetrievedChunk]:
        if self.policy is RerankPolicy.STRICT:
            raise RerankUnavailableError(
                f"Rerank indisponível ({motivo}) e política é STRICT. "
                "Configure COHERE_API_KEY ou use RerankPolicy.FALLBACK_RRF de forma explícita."
            )

        log.warning("rerank_ignorado_fallback_rrf", motivo=motivo, candidatos=len(chunks))
        # `rerank_score` permanece None de propósito: o consumidor precisa saber
        # que esta ordem é de RRF, não de cross-encoder.
        ordered = sorted(chunks, key=lambda c: c.rrf_score or 0.0, reverse=True)
        return list(ordered[:limit])
