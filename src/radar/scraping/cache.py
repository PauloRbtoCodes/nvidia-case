"""Cache em disco das respostas HTTP.

Motivo de existir antes de qualquer extrator: iterar na heurística de extração
exige rodar dezenas de vezes sobre as mesmas páginas. Sem cache, cada iteração
custa tempo, quota de busca e paciência do servidor alheio — e o resultado do
extrator deixa de ser comparável entre execuções, porque o HTML muda embaixo.

Formato: um JSON por URL, nomeado pelo hash da URL. Legível a olho nu de
propósito — quando a extração erra, o primeiro passo é abrir o HTML que gerou o
erro, e um formato binário atrapalharia isso.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

#: Uma semana. Sinal técnico de startup não muda de um dia para o outro, e o
#: objetivo aqui é desenvolvimento sem custo de rede, não frescor de produção.
DEFAULT_TTL_SECONDS: float = 7 * 24 * 3600


@dataclass(slots=True)
class CachedResponse:
    """Resposta HTTP persistida: corpo, cabeçalhos e quando foi coletada."""

    url: str
    status_code: int
    body: str
    headers: dict[str, str] = field(default_factory=dict)
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    final_url: str | None = None
    rendered: bool = False
    """True quando o corpo veio do Playwright — HTML renderizado não é o mesmo
    artefato que o HTML estático, e confundir os dois esconde bugs do fallback."""

    def age_seconds(self, *, now: datetime | None = None) -> float:
        reference = now or datetime.now(UTC)
        return (reference - self.fetched_at).total_seconds()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["fetched_at"] = self.fetched_at.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CachedResponse:
        fetched_at = datetime.fromisoformat(data["fetched_at"])
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=UTC)
        return cls(
            url=data["url"],
            status_code=int(data["status_code"]),
            body=data["body"],
            headers=dict(data.get("headers") or {}),
            fetched_at=fetched_at,
            final_url=data.get("final_url"),
            rendered=bool(data.get("rendered", False)),
        )


class ResponseCache:
    """Cache chaveado por hash da URL, com TTL.

    Operações são síncronas apesar do resto da camada ser async: são leituras de
    poucos KB em disco local, e envolvê-las em threads adicionaria complexidade
    maior que o custo que evitaria.
    """

    def __init__(self, cache_dir: Path, *, ttl_seconds: float = DEFAULT_TTL_SECONDS) -> None:
        self._dir = Path(cache_dir)
        self._ttl = ttl_seconds

    @property
    def directory(self) -> Path:
        return self._dir

    @property
    def ttl_seconds(self) -> float:
        return self._ttl

    @staticmethod
    def key_for(url: str) -> str:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()

    def path_for(self, url: str) -> Path:
        key = self.key_for(url)
        # Shard pelos dois primeiros caracteres: diretório único com milhares de
        # arquivos fica desagradável de inspecionar e lento em alguns filesystems.
        return self._dir / key[:2] / f"{key}.json"

    def get(self, url: str, *, ttl_seconds: float | None = None) -> CachedResponse | None:
        path = self.path_for(url)
        if not path.exists():
            return None

        try:
            entry = CachedResponse.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError) as exc:
            # Arquivo corrompido (ex.: escrita interrompida) é descartado em
            # silêncio: cache é otimização, nunca fonte da verdade.
            log.warning("cache_entry_invalid", url=url, path=str(path), error=str(exc))
            path.unlink(missing_ok=True)
            return None

        ttl = self._ttl if ttl_seconds is None else ttl_seconds
        if ttl >= 0 and entry.age_seconds() > ttl:
            log.debug("cache_expired", url=url, age=round(entry.age_seconds()))
            return None

        log.debug("cache_hit", url=url)
        return entry

    def set(self, entry: CachedResponse) -> Path:
        path = self.path_for(entry.url)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Escrita atômica via arquivo temporário: uma interrupção no meio deixaria
        # JSON truncado que seria lido como cache válido na próxima execução.
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(entry.to_dict(), ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        log.debug("cache_store", url=entry.url, path=str(path))
        return path

    def delete(self, url: str) -> None:
        self.path_for(url).unlink(missing_ok=True)

    def clear(self) -> int:
        removed = 0
        for path in self._dir.rglob("*.json"):
            path.unlink(missing_ok=True)
            removed += 1
        return removed
