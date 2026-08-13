"""Instrumentação Langfuse — opcional por construção.

Regra: observabilidade nunca derruba a pipeline. Sem chaves configuradas, sem
Docker rodando, ou com o SDK falhando na inicialização, a camada degrada para um
observador nulo e o fluxo segue. O contrário — perder um lote de scraping porque
o container do Langfuse caiu — seria trocar diagnóstico por fragilidade.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Protocol

import structlog

from radar.config import Settings, get_settings

log = structlog.get_logger(__name__)


class Observation(Protocol):
    """Alça mínima que o cliente usa — mantém o Langfuse fora do client.py."""

    def update(self, **kwargs: Any) -> Any: ...


class _NullObservation:
    def update(self, **kwargs: Any) -> None:  # noqa: D102 - no-op deliberado
        return None


class Observer(Protocol):
    @contextmanager
    def generation(
        self, *, name: str, model: str, prompt_input: Any, metadata: dict[str, Any] | None = None
    ) -> Iterator[Observation]: ...

    def flush(self) -> None: ...


class NullObserver:
    """Usado quando `Settings.langfuse_enabled` é falso. Custo zero, API idêntica."""

    @contextmanager
    def generation(
        self, *, name: str, model: str, prompt_input: Any, metadata: dict[str, Any] | None = None
    ) -> Iterator[Observation]:
        yield _NullObservation()

    def flush(self) -> None:
        return None


class LangfuseObserver:
    """Envolve o cliente Langfuse v3+ (API de spans OTel)."""

    def __init__(self, client: Any) -> None:
        self._client = client

    @contextmanager
    def generation(
        self, *, name: str, model: str, prompt_input: Any, metadata: dict[str, Any] | None = None
    ) -> Iterator[Observation]:
        try:
            manager = self._client.start_as_current_observation(
                name=name,
                as_type="generation",
                input=prompt_input,
                model=model,
                metadata=metadata or {},
            )
        except Exception as exc:  # pragma: no cover - depende do servidor
            # Falha ao abrir o span não pode interromper a chamada ao modelo.
            log.warning("langfuse_span_falhou", name=name, error=str(exc))
            yield _NullObservation()
            return

        with manager as span:
            yield span

    def flush(self) -> None:
        try:
            self._client.flush()
        except Exception as exc:  # pragma: no cover
            log.warning("langfuse_flush_falhou", error=str(exc))


def build_observer(settings: Settings | None = None) -> Observer:
    """Fábrica única: decide entre Langfuse e nulo, e nunca propaga exceção."""
    settings = settings or get_settings()

    if not settings.langfuse_enabled:
        log.debug("langfuse_desabilitado", motivo="chaves ausentes em Settings")
        return NullObserver()

    try:
        from langfuse import Langfuse

        client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
    except Exception as exc:
        # Import quebrado ou servidor fora do ar: seguimos sem traces.
        log.warning("langfuse_indisponivel", error=str(exc))
        return NullObserver()

    log.info("langfuse_ativo", host=settings.langfuse_host)
    return LangfuseObserver(client)
