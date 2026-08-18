"""Cota e cache do cliente de LLM.

Estes três controles existem para a execução contra a API real, e é por isso que
não existiam: enquanto a suíte rodava contra dublês, nada pressionava a cota.
Os testes aqui travam as propriedades que os tornam seguros — em especial a que
permite ligar o cache por padrão sem esconder mudança de sinal.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from radar.config import Settings
from radar.llm.cache import CachedCompletion, CompletionCache, fingerprint
from radar.llm.client import NIMClient
from radar.llm.errors import BudgetExceededError
from radar.llm.model_registry import LLMTask
from radar.llm.observability import NullObserver
from radar.llm.quota import ConcurrencyGate, LLMBudget


class Resposta(BaseModel):
    valor: str


class ChatContador:
    """Dublê que conta chamadas e registra concorrência máxima observada."""

    def __init__(self, saida: str = '{"valor": "ok"}', atraso: float = 0.0) -> None:
        self.saida = saida
        self.atraso = atraso
        self.chamadas = 0
        self.em_voo = 0
        self.pico = 0
        self._lock = threading.Lock()

    def invoke(self, messages: Any, **_: Any) -> Any:
        with self._lock:
            self.chamadas += 1
            self.em_voo += 1
            self.pico = max(self.pico, self.em_voo)
        try:
            if self.atraso:
                time.sleep(self.atraso)

            class _R:
                content = self.saida

            return _R()
        finally:
            with self._lock:
                self.em_voo -= 1


def montar(fake: ChatContador, tmp_path: Any, **kwargs: Any) -> NIMClient:
    settings = Settings(
        nvidia_api_key="",
        langfuse_public_key="",
        langfuse_secret_key="",
        llm_cache_enabled=False,
        llm_cache_dir=tmp_path / "llm",
    )
    return NIMClient(
        settings=settings,
        observer=NullObserver(),
        chat_factory=lambda **_: fake,
        **kwargs,
    )


def mensagens(texto: str) -> list[Any]:
    return [SystemMessage(content="sys"), HumanMessage(content=texto)]


# --------------------------------------------------------------- orçamento


def test_orcamento_interrompe_o_lote_em_vez_de_degradar_em_silencio(tmp_path):
    """Estourar cota tem que aparecer como falha, não como empresa sem diagnóstico.

    Degradar em silêncio produziria metade do lote sem briefing e nenhuma
    explicação — a lacuna não declarada que o projeto inteiro evita.
    """
    fake = ChatContador()
    cliente = montar(fake, tmp_path, budget=LLMBudget(max_calls=2))

    cliente._invoke(LLMTask.CLASSIFIER, mensagens("a"))
    cliente._invoke(LLMTask.CLASSIFIER, mensagens("b"))

    with pytest.raises(BudgetExceededError) as exc:
        cliente._invoke(LLMTask.CLASSIFIER, mensagens("c"))

    assert exc.value.used == 2
    assert exc.value.limit == 2
    assert fake.chamadas == 2, "a terceira nem deve chegar ao backend"


def test_orcamento_recusa_antes_de_ocupar_vaga_de_concorrencia(tmp_path):
    """Chamada proibida não pode bloquear thread esperando semáforo."""
    fake = ChatContador()
    gate = ConcurrencyGate(max_concurrency=1)
    cliente = montar(fake, tmp_path, budget=LLMBudget(max_calls=1), gate=gate)

    cliente._invoke(LLMTask.CLASSIFIER, mensagens("a"))
    with pytest.raises(BudgetExceededError):
        cliente._invoke(LLMTask.CLASSIFIER, mensagens("b"))

    # Se o orçamento tivesse sido checado depois do semáforo, a vaga teria ficado
    # presa e este `hold` travaria.
    with gate.hold():
        pass


def test_snapshot_do_orcamento_separa_chamada_de_acerto_de_cache():
    orcamento = LLMBudget(max_calls=10)
    orcamento.consume()
    orcamento.record_cache_hit()
    orcamento.record_cache_hit()

    assert orcamento.snapshot() == {
        "chamadas": 1,
        "teto": 10,
        "restante": 9,
        "cache_hits": 2,
    }


# ------------------------------------------------------------ concorrência


def test_semaforo_limita_chamadas_simultaneas(tmp_path):
    """O teto existe porque o fan-out `Send` dispara uma rajada por empresa."""
    fake = ChatContador(atraso=0.05)
    cliente = montar(fake, tmp_path, gate=ConcurrencyGate(max_concurrency=2))

    threads = [
        threading.Thread(target=cliente._invoke, args=(LLMTask.CLASSIFIER, mensagens(f"m{i}")))
        for i in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert fake.chamadas == 8
    assert fake.pico <= 2, f"pico de {fake.pico} chamadas simultâneas excede o teto"


# ------------------------------------------------------------------ cache


def test_cache_evita_a_segunda_chamada_identica(tmp_path):
    fake = ChatContador()
    cache = CompletionCache(tmp_path / "llm")
    cliente = montar(fake, tmp_path, cache=cache)

    primeira = cliente._invoke(LLMTask.CLASSIFIER, mensagens("mesma coisa"))
    segunda = cliente._invoke(LLMTask.CLASSIFIER, mensagens("mesma coisa"))

    assert primeira == segunda
    assert fake.chamadas == 1
    assert cliente.budget.used == 1, "acerto de cache não consome orçamento"
    assert cliente.budget.served_from_cache == 1


def test_prompt_diferente_e_chave_diferente(tmp_path):
    """**O invariante que torna o cache seguro neste projeto.**

    A evidência raspada entra no prompt. Se a página da empresa mudar, o prompt
    muda e a chave muda — o cache não consegue devolver o diagnóstico antigo e
    mascarar a mudança de sinal que o eixo temporal do radar precisa detectar.
    """
    fake = ChatContador()
    cliente = montar(fake, tmp_path, cache=CompletionCache(tmp_path / "llm"))

    cliente._invoke(LLMTask.CLASSIFIER, mensagens("vaga: engenheiro de dados"))
    cliente._invoke(LLMTask.CLASSIFIER, mensagens("vaga: engenheiro de MLOps, vLLM"))

    assert fake.chamadas == 2


def test_papel_da_mensagem_entra_na_chave():
    """Mesma frase como system ou como human não é a mesma chamada."""
    a = fingerprint("m", 0.0, [("system", "x"), ("human", "y")])
    b = fingerprint("m", 0.0, [("human", "x"), ("system", "y")])
    assert a != b


def test_temperatura_entra_na_chave():
    assert fingerprint("m", 0.0, [("human", "x")]) != fingerprint("m", 0.7, [("human", "x")])


def test_entrada_corrompida_nao_derruba_a_execucao(tmp_path):
    """O cache é otimização: o caminho sem ele é o caminho normal do sistema."""
    cache = CompletionCache(tmp_path / "llm")
    cache.directory.mkdir(parents=True, exist_ok=True)
    (cache.directory / "abc.json").write_text("{ isto não é json", "utf-8")

    assert cache.get("abc") is None


def test_ttl_expirado_devolve_none(tmp_path):
    from datetime import UTC, datetime, timedelta

    cache = CompletionCache(tmp_path / "llm", ttl_seconds=60)
    cache.set(
        "k",
        CachedCompletion(
            model="m",
            temperature=0.0,
            prompt_fingerprint="k",
            output="antigo",
            created_at=datetime.now(UTC) - timedelta(hours=2),
        ),
    )
    assert cache.get("k") is None
