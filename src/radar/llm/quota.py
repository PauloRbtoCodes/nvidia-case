"""Controle de pressão sobre a API do NIM: concorrência e orçamento de lote.

Os dois problemas que este módulo resolve só aparecem contra a API real, e por
isso passaram despercebidos enquanto a suíte rodava contra dublês.

**Concorrência.** O grafo externo faz fan-out com `Send` para N empresas, e cada
subgrafo dispara suas chamadas de LLM em paralelo (os nós são `async` e empurram
a chamada síncrona para uma thread). Sem teto, N empresas viram N rajadas
simultâneas contra a cota gratuita. O 429 chega para todas ao mesmo tempo, cada
uma entra em backoff, e o lote fica mais lento do que se tivesse sido serializado
— com a cota já queimada nas tentativas perdidas.

**Orçamento.** O retry existe justamente porque falha é esperada, mas retry sem
teto de lote transforma um dia ruim da API em cota inteira consumida sem nenhum
diagnóstico produzido. O orçamento existe para que o lote termine com resultado
parcial explicável em vez de quota zerada e nada no banco.

Ambos são aplicados no ponto mais estreito possível (`NIMClient._invoke`, a
chamada crua), e não por nó. Uma empresa não sabe quantas outras estão em voo;
só o cliente compartilhado sabe.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

import structlog

from radar.llm.errors import BudgetExceededError

log = structlog.get_logger(__name__)

#: Teto de chamadas simultâneas ao NIM. Quatro é conservador de propósito: a cota
#: gratuita não documenta o limite exato de concorrência, e descobrir o teto real
#: por tentativa e erro custa a própria cota que estamos tentando preservar.
DEFAULT_MAX_CONCURRENCY = 4

#: Teto de chamadas por execução do grafo. Dimensionado para o caso de uso real:
#: ~12 empresas × 6 nós de LLM × margem para retry de validação. Passar disso
#: significa que algo está em laço, não que o lote é grande.
DEFAULT_MAX_CALLS_PER_RUN = 400


class ConcurrencyGate:
    """Semáforo compartilhado por todas as chamadas de LLM da execução.

    Semáforo de `threading`, não de `asyncio`, porque quem chama já está fora do
    event loop — `NodeDeps.run_llm` empurra para `asyncio.to_thread`. Um semáforo
    de asyncio aqui não limitaria nada: as threads não passam pelo loop.
    """

    def __init__(self, max_concurrency: int = DEFAULT_MAX_CONCURRENCY) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency precisa ser >= 1")
        self._max = max_concurrency
        self._sem = threading.BoundedSemaphore(max_concurrency)

    @property
    def max_concurrency(self) -> int:
        return self._max

    @contextmanager
    def hold(self) -> Iterator[None]:
        self._sem.acquire()
        try:
            yield
        finally:
            self._sem.release()


class LLMBudget:
    """Contador de chamadas por execução, com teto.

    Conta **chamadas**, não tokens. Contar token exigiria tokenizar o prompt
    antes de cada envio — custo real de CPU no caminho quente, para uma precisão
    que não muda a decisão: o que precisamos saber é "o lote saiu do controle?",
    e número de chamadas responde isso.

    O contador é atômico porque as chamadas vêm de threads distintas do fan-out.
    """

    def __init__(self, max_calls: int = DEFAULT_MAX_CALLS_PER_RUN) -> None:
        if max_calls < 1:
            raise ValueError("max_calls precisa ser >= 1")
        self._max = max_calls
        self._used = 0
        self._served_from_cache = 0
        self._lock = threading.Lock()

    @property
    def max_calls(self) -> int:
        return self._max

    @property
    def used(self) -> int:
        with self._lock:
            return self._used

    @property
    def remaining(self) -> int:
        with self._lock:
            return max(self._max - self._used, 0)

    @property
    def served_from_cache(self) -> int:
        """Acertos de cache. Não consomem orçamento — não houve chamada."""
        with self._lock:
            return self._served_from_cache

    def record_cache_hit(self) -> None:
        with self._lock:
            self._served_from_cache += 1

    def consume(self, *, amount: int = 1) -> int:
        """Reserva `amount` chamadas ou levanta `BudgetExceededError`.

        Levanta em vez de degradar silenciosamente: uma execução que parou por
        orçamento precisa aparecer como falha no relatório. Degradar em silêncio
        produziria um lote com metade das empresas sem diagnóstico e nenhuma
        explicação — exatamente o tipo de lacuna não declarada que o projeto
        inteiro tenta evitar.
        """
        with self._lock:
            if self._used + amount > self._max:
                raise BudgetExceededError(
                    f"Orçamento de LLM esgotado: {self._used}/{self._max} chamadas "
                    "nesta execução.",
                    used=self._used,
                    limit=self._max,
                )
            self._used += amount
            restante = self._max - self._used
            return restante

    def snapshot(self) -> dict[str, int]:
        """Para o relatório de fim de execução e para o trace."""
        with self._lock:
            return {
                "chamadas": self._used,
                "teto": self._max,
                "restante": max(self._max - self._used, 0),
                "cache_hits": self._served_from_cache,
            }
