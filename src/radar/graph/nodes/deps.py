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
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from radar.llm.client import NIMClient
from radar.llm.model_registry import LLMTask
from radar.models.scoring import DefensibilityScore
from radar.rag.hybrid import HybridRetriever
from radar.rag.rerank import Reranker
from radar.scoring.weights import ScoringWeights, get_weights
from radar.scraping.fetch import HttpFetcher
from radar.scraping.search import TavilySearch

T = TypeVar("T", bound=BaseModel)


class ScoreHistoryPort(Protocol):
    """Leitura do score anterior de uma empresa, para o nó `compare`.

    O grafo não importa `persistence/` — roda contra dublês, sem banco no ar e
    sem chave de API. Esta porta é o único ponto por onde o histórico entra no
    subgrafo: em produção, um adaptador sobre o Postgres
    (`radar.persistence.score_history.DbScoreHistory`); nos testes, um dublê; e
    `None` quando não há banco, caso em que o nó `compare` simplesmente não
    emite diff.

    É a inversão de dependência que o `docs/governanca-e-tradeoffs.md` §9 registra
    como trabalho de baixo custo e alta prioridade, aplicada ao primeiro nó que
    de fato precisa dela.
    """

    def previous_score(self, company_name: str) -> DefensibilityScore | None:
        """O score mais recente já persistido desta empresa, ou `None` na primeira vez.

        Chamado durante o grafo, antes de o score novo ser gravado — a
        persistência é um sink pós-grafo (`radar.persistence.sink`) —, então o
        "mais recente" é genuinamente a execução anterior, não a atual.
        """
        ...


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
    score_history: ScoreHistoryPort | None = None
    """Histórico de score para o nó `compare`. `None` sem banco — o nó não emite diff."""
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
    with_history: bool = True,
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
            # A dimensão vem do embedder, nunca do default: criar a coleção com
            # o tamanho errado só falha na primeira busca, depois de a ingestão
            # inteira ter sido paga.
            embedder = build_embedding_client()
            retriever = HybridRetriever(
                knowledge_base=build_knowledge_base(vector_size=embedder.dimension),
                bm25_index=BM25Index.load(),
                embedder=embedder,
            )
        except Exception:  # noqa: BLE001 - Qdrant fora do ar é caso esperado
            retriever = None

    score_history: ScoreHistoryPort | None = None
    if with_history:
        # O adaptador não abre conexão na construção: cada consulta abre a sua e
        # devolve `None` se o banco estiver fora do ar. Importado aqui, e não no
        # topo, para manter `graph/` sem dependência de import em `persistence/`.
        from radar.persistence.score_history import DbScoreHistory

        score_history = DbScoreHistory()

    return NodeDeps(
        llm=llm or NIMClient(),
        search=search,
        fetcher=HttpFetcher() if with_fetcher else None,
        retriever=retriever,
        reranker=Reranker(),
        score_history=score_history,
    )
