"""Cache em disco das respostas do LLM.

Espelha `scraping/cache.py` — mesmo formato, mesma motivação — mas resolve um
problema que só existe do lado do modelo: **reexecutar um lote paga tudo de
novo**. Iterar num prompt, ajustar um peso e rodar de novo é o ciclo normal de
trabalho, e sem cache cada volta consome cota gratuita que não se recupera.

A chave é o hash de `(modelo, temperatura, mensagens)`. Isso tem uma consequência
que vale explicitar, porque é ela que torna o cache seguro num sistema cuja tese
é rastreabilidade:

**Mudou a evidência raspada → mudou o prompt → mudou a chave.** Um acerto de
cache só acontece quando a entrada é byte a byte idêntica. O cache portanto não
consegue mascarar mudança de sinal: se a página da empresa mudou, o texto no
prompt muda e a resposta é recalculada. Isso é o que permite ligá-lo por padrão
sem conflitar com o eixo temporal do radar.

Efeito colateral desejado: reexecução vira determinística. Com temperatura > 0 o
modelo varia entre chamadas, e ao apresentar o case isso significaria números
diferentes a cada demonstração. Com cache, a mesma entrada devolve a mesma saída
— e `weights_version` continua sendo o que explica mudança de score, como manda
o ADR 0002.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

#: Uma semana, igual ao cache de scraping. A resposta do modelo para uma entrada
#: idêntica não "envelhece" de verdade; o TTL existe para o disco não crescer
#: sem limite e para absorver troca de versão de modelo sob o mesmo nome.
DEFAULT_TTL_SECONDS: float = 7 * 24 * 3600


@dataclass(slots=True)
class CachedCompletion:
    """Resposta crua do modelo, com o suficiente para auditar de onde veio."""

    model: str
    temperature: float
    prompt_fingerprint: str
    output: str
    created_at: datetime

    def age_seconds(self, *, now: datetime | None = None) -> float:
        return ((now or datetime.now(UTC)) - self.created_at).total_seconds()

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "temperature": self.temperature,
            "prompt_fingerprint": self.prompt_fingerprint,
            "output": self.output,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CachedCompletion:
        criado = datetime.fromisoformat(data["created_at"])
        if criado.tzinfo is None:
            criado = criado.replace(tzinfo=UTC)
        return cls(
            model=str(data["model"]),
            temperature=float(data["temperature"]),
            prompt_fingerprint=str(data["prompt_fingerprint"]),
            output=str(data["output"]),
            created_at=criado,
        )


def fingerprint(
    model: str,
    temperature: float,
    messages: list[tuple[str, str]],
    *,
    params: dict[str, Any] | None = None,
) -> str:
    """Identidade da chamada: modelo, parâmetros de geração e mensagens.

    O papel de cada mensagem entra no hash porque a mesma frase como `system` ou
    como `human` não é a mesma chamada — e o retry de validação reenvia a
    conversa acrescida da resposta inválida, que precisa ser uma chave distinta.

    `params` entra por causa de um bug real, custo de uma execução inteira: o
    `max_tokens` estava em 1024 e truncava o JSON do extractor; a resposta
    truncada foi cacheada; ao subir o teto para 8192, o cache continuou servindo
    a resposta cortada, porque nada em volta do conteúdo da mensagem tinha
    mudado. **Parâmetro que altera a saída é parte da identidade da chamada.**
    """
    h = hashlib.sha256()
    h.update(model.encode())
    h.update(f"|{temperature:.4f}|".encode())
    for chave in sorted(params or {}):
        h.update(f"{chave}={params[chave]!r};".encode())
    for papel, conteudo in messages:
        h.update(papel.encode())
        h.update(b"\x00")
        h.update(conteudo.encode())
        h.update(b"\x01")
    return h.hexdigest()


class CompletionCache:
    """Um JSON por chamada, nomeado pelo fingerprint. Legível a olho nu.

    Formato texto de propósito, pelo mesmo motivo do cache de scraping: quando a
    saída do modelo surpreende, o primeiro passo é abrir o que ele devolveu.
    """

    def __init__(self, cache_dir: Path, *, ttl_seconds: float = DEFAULT_TTL_SECONDS) -> None:
        self._dir = cache_dir
        self._ttl = ttl_seconds

    @property
    def directory(self) -> Path:
        return self._dir

    @property
    def ttl_seconds(self) -> float:
        return self._ttl

    def _path_for(self, key: str) -> Path:
        return self._dir / f"{key}.json"

    def get(self, key: str) -> CachedCompletion | None:
        caminho = self._path_for(key)
        if not caminho.exists():
            return None
        try:
            entrada = CachedCompletion.from_dict(json.loads(caminho.read_text("utf-8")))
        except (OSError, ValueError, KeyError) as exc:
            # Entrada corrompida não pode derrubar a execução: o cache é uma
            # otimização, e o caminho sem ele é o caminho normal do sistema.
            log.warning("llm_cache_corrompido", key=key, erro=str(exc)[:200])
            return None

        if self._ttl >= 0 and entrada.age_seconds() > self._ttl:
            log.debug("llm_cache_expirado", key=key, idade=round(entrada.age_seconds()))
            return None
        return entrada

    def set(self, key: str, entrada: CachedCompletion) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            self._path_for(key).write_text(
                json.dumps(entrada.to_dict(), ensure_ascii=False, indent=2), "utf-8"
            )
        except OSError as exc:  # disco cheio, permissão — nunca fatal
            log.warning("llm_cache_nao_gravado", key=key, erro=str(exc)[:200])
