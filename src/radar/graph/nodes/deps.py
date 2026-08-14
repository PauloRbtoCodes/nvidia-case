"""Dependências dos nós, num único objeto injetável.

Todo nó do grafo recebe `NodeDeps` por closure em vez de instanciar clientes por
conta própria. Duas razões concretas:

1. **A suíte roda sem rede.** Substituindo `llm`, `search`, `fetcher` e
   `retriever` por dublês, o grafo inteiro é exercitável sem chave de API, sem
   Qdrant no ar e sem navegador — que é o que permite testar as arestas
   condicionais, que são a parte do sistema onde os bugs de verdade moram.
2. **Um cliente por execução, não por nó.** `NIMClient` reaproveita backends por
   (modelo, temperatura) e o `HttpFetcher` mantém pool de conexões e cache em
   disco; recriá-los a cada nó jogaria os dois fora a cada passo.

Os nós de LLM são `async` e empurram a chamada síncrona para uma thread. Sem
isso, o fan-out `Send` do grafo externo processaria as empresas em série: uma
chamada bloqueante de 20s trava o event loop e as outras N-1 empresas esperam.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, TypeVar

from pydantic import BaseModel

from radar.llm.client import NIMClient
from radar.llm.model_registry import LLMTask
from radar.rag.hybrid import HybridRetriever
from radar.rag.rerank import Reranker
from radar.scoring.weights import ScoringWeights, get_weights
from radar.scraping.fetch import HttpFetcher
from radar.scraping.search import TavilySearch

T = TypeVar("T", bound=BaseModel)


def _hoje() -> date:
    return datetime.now(UTC).date()


@dataclass
class NodeDeps:
    """Tudo que os nós precisam do mundo externo.

    Os campos opcionais são `None` quando a dependência não foi configurada (sem
    `TAVILY_API_KEY`, sem Qdrant no ar). O nó correspondente registra uma
    `NodeFailure` explícita em vez de estourar — o grafo precisa conseguir
    diagnosticar uma empresa a partir de URLs semente mesmo sem busca.
    """

    llm: NIMClient
    search: TavilySearch | None = None
    fetcher: HttpFetcher | None = None
    retriever: HybridRetriever | None = None
    reranker: Reranker | None = None
    weights: ScoringWeights = field(default_factory=get_weights)
    clock: Callable[[], date] = _hoje

    @property
    def today(self) -> str:
        """Data injetada nos prompts — todos declaram a variável `today`.

        Vai no prompt porque quase toda decisão do sistema depende de idade de
        evidência: "fine-tuning próprio" publicado em 2022 não é o mesmo sinal
        que publicado no mês passado, e o modelo não sabe que dia é hoje.
        """
        return self.clock().isoformat()

    async def run_llm(self, task: LLMTask, schema: type[T], **variables: Any) -> T:
        """`NIMClient.run` fora do event loop, com `today` já preenchido."""
        variables.setdefault("today", self.today)
        return await asyncio.to_thread(self.llm.run, task, schema, **variables)


def build_deps(
    *,
    llm: NIMClient | None = None,
    with_search: bool = True,
    with_fetcher: bool = True,
    with_retriever: bool = True,
) -> NodeDeps:
    """Fábrica padrão de produção.

    Cada dependência é construída dentro de um `try`: infra ausente vira
    dependência `None`, e o nó que precisa dela reporta a lacuna. Derrubar a
    montagem inteira porque o Qdrant não subiu impediria até o diagnóstico
    parcial, que é justamente o que o grafo foi desenhado para entregar.
    """
    from radar.rag.bm25 import BM25Index
    from radar.rag.embed import build_embedding_client
    from radar.rag.store import build_knowledge_base

    search: TavilySearch | None = None
    if with_search:
        try:
            search = TavilySearch()
        except Exception:  # noqa: BLE001 - chave ausente é caso esperado
            search = None

    retriever: HybridRetriever | None = None
    if with_retriever:
        try:
            retriever = HybridRetriever(
                knowledge_base=build_knowledge_base(),
                bm25_index=BM25Index.load(),
                embedder=build_embedding_client(),
            )
        except Exception:  # noqa: BLE001 - Qdrant fora do ar é caso esperado
            retriever = None

    return NodeDeps(
        llm=llm or NIMClient(),
        search=search,
        fetcher=HttpFetcher() if with_fetcher else None,
        retriever=retriever,
        reranker=Reranker(),
    )
