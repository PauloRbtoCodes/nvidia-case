"""Registro das execuções do grafo em andamento, e o canal de eventos do SSE.

`POST /searches` não pode bloquear: uma execução percorre N empresas, cada uma
com várias chamadas de modelo, e nenhum cliente HTTP espera isso. O endpoint
registra a execução, dispara o grafo em segundo plano e devolve um id; o
progresso sai por `GET /searches/{id}/stream`.

**Limitação assumida e explícita:** o registro vive na memória do processo. Com
mais de um worker, o `POST` cai num processo e o `GET .../stream` pode cair em
outro, que não conhece a execução. Para o caso de uso — um dashboard interno,
um operador por vez — isso é adequado, e a alternativa (checkpointer do LangGraph
no Postgres mais um canal de pub/sub) custa uma infraestrutura que o projeto
ainda não precisa. A API é servida com um worker; está registrado aqui para que
a decisão seja encontrada por quem for escalar, e não redescoberta em produção.

O histórico de eventos é mantido junto com a fila viva porque a UI conecta ao
stream **depois** do POST. Sem histórico, todo evento emitido nesse intervalo
sumiria, e a tela abriria já com o progresso pela metade.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import structlog

log = structlog.get_logger(__name__)

#: Quantas execuções **terminadas** o registro guarda. Um dashboard aberto o dia
#: inteiro dispara dezenas de buscas, cada uma segurando o resultado do lote em
#: memória; sem teto, o processo cresce sem limite. Execuções em andamento não
#: entram nesta conta e nunca são descartadas.
MAX_RUNS_RETIDAS = 50

#: Sentinela de fim de stream. Um objeto próprio, e não `None`, porque `None` é
#: um valor plausível vindo do grafo e confundiria fim com evento vazio.
_FIM = object()


class RunStatus(StrEnum):
    PENDENTE = "pendente"
    EXECUTANDO = "executando"
    CONCLUIDA = "concluida"
    FALHOU = "falhou"


@dataclass
class RunEvent:
    """Um passo do grafo, no formato que o SSE entrega."""

    type: str
    """`node` para progresso, `status` para transição, `error` para falha."""

    node: str | None = None
    company: str | None = None
    detail: str | None = None
    at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "node": self.node,
            "company": self.company,
            "detail": self.detail,
            "at": self.at.isoformat(),
        }


@dataclass
class SearchRun:
    """Uma execução do grafo, com seu histórico e seus assinantes."""

    id: str
    query: str
    max_companies: int
    status: RunStatus = RunStatus.PENDENTE
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None

    events: list[RunEvent] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None

    _subscribers: list[asyncio.Queue[Any]] = field(default_factory=list, repr=False)
    _tasks: set[asyncio.Task[Any]] = field(default_factory=set, repr=False)

    @property
    def finished(self) -> bool:
        return self.status in (RunStatus.CONCLUIDA, RunStatus.FALHOU)

    def track(self, task: asyncio.Task[Any]) -> asyncio.Task[Any]:
        """Guarda referência forte à task.

        `asyncio` só mantém referência fraca ao que está agendado: uma task sem
        dono pode ser coletada no meio da execução, e o sintoma é uma busca que
        simplesmente para de progredir sem erro nenhum no log.
        """
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def emit(self, event: RunEvent) -> None:
        """Guarda no histórico e empurra para quem estiver ouvindo."""
        self.events.append(event)
        for fila in self._subscribers:
            fila.put_nowait(event)

    def finish(self, *, result: dict[str, Any] | None = None, error: str | None = None) -> None:
        self.status = RunStatus.FALHOU if error else RunStatus.CONCLUIDA
        self.result = result
        self.error = error
        self.finished_at = datetime.now(UTC)
        self.emit(RunEvent(type="status", detail=self.status.value))
        for fila in self._subscribers:
            fila.put_nowait(_FIM)

    async def subscribe(self) -> AsyncIterator[RunEvent]:
        """Histórico primeiro, depois os eventos vivos.

        Quem conecta tarde recebe tudo o que já aconteceu antes de entrar na
        fila — é o que faz a tela abrir com o progresso correto em vez de
        começar do meio.

        A ordem das duas primeiras linhas é o que evita perder ou duplicar
        evento: inscrever **antes** de fotografar o histórico garante que nada
        emitido no intervalo escape (não há `await` entre elas, então não há
        intervalo), e a foto conter só o passado garante que a fila não repita o
        que o histórico já entregou.
        """
        fila: asyncio.Queue[Any] = asyncio.Queue()
        self._subscribers.append(fila)
        historico = list(self.events)

        try:
            for evento in historico:
                yield evento

            # Corrida real: a execução pode ter terminado antes da inscrição, e
            # nesse caso o `_FIM` já foi distribuído e nunca chegará nesta fila.
            if self.finished:
                return

            while True:
                item = await fila.get()
                if item is _FIM:
                    return
                yield item
        finally:
            if fila in self._subscribers:
                self._subscribers.remove(fila)

    def cancel(self) -> None:
        for task in list(self._tasks):
            if not task.done():
                task.cancel()


class RunRegistry:
    """Índice das execuções, com poda por idade de inserção."""

    def __init__(self, max_runs: int = MAX_RUNS_RETIDAS) -> None:
        self._runs: dict[str, SearchRun] = {}
        self._max = max_runs

    def create(self, query: str, max_companies: int) -> SearchRun:
        run = SearchRun(id=uuid.uuid4().hex, query=query, max_companies=max_companies)
        self._runs[run.id] = run
        self._podar()
        return run

    def get(self, run_id: str) -> SearchRun | None:
        return self._runs.get(run_id)

    def list(self) -> list[SearchRun]:
        return sorted(self._runs.values(), key=lambda r: r.created_at, reverse=True)

    def _podar(self) -> None:
        """Mantém no máximo `max_runs` execuções **terminadas**, as mais recentes.

        O teto conta só as terminadas: execução viva nunca é descartada, porque
        descartá-la deixaria o cliente com um id que o `stream` não reconhece no
        meio do progresso.

        A poda roda na criação. Uma execução que acabou de terminar sobrevive até
        a próxima busca — irrelevante para memória, e mantém a regra num lugar só
        em vez de espalhá-la entre `create` e `finish`.
        """
        terminadas = [r for r in self.list() if r.finished]
        for run in terminadas[self._max :]:
            self._runs.pop(run.id, None)

    def clear(self) -> None:
        for run in self._runs.values():
            run.cancel()
        self._runs.clear()


#: Registro do processo. Injetado nos routers por `api.deps.get_registry`, para
#: que os testes possam trocá-lo sem estado vazando entre casos.
registry = RunRegistry()
