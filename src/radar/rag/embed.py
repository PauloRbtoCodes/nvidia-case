"""Embeddings via NVIDIA NIM (`nvidia/nv-embedqa-e5-v5`).

O ponto não óbvio deste módulo é `input_type`. Modelos da família *embedqa* são
treinados com dois encoders assimétricos: a pergunta ("query") e o documento
("passage") são projetados de formas diferentes no mesmo espaço. Indexar a KB
com `input_type="query"` não levanta erro nenhum — o vetor sai, o Qdrant aceita,
a busca retorna resultados. Só que a recuperação fica pior e nada no sistema
denuncia. É exatamente o tipo de bug que sobrevive até a demo.

Por isso aqui não existe um `embed(texts)` genérico: existem `embed_passages` e
`embed_query`, e o tipo entra na chave do cache para que um lote indexado com o
tipo errado nunca seja reaproveitado silenciosamente.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

import httpx
import structlog
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from radar.config import PROJECT_ROOT, get_settings

log = structlog.get_logger(__name__)

#: Dimensões conhecidas. Criar a coleção do Qdrant com a dimensão errada só
#: falha na primeira busca, bem depois da ingestão inteira ter sido paga.
EMBEDDING_DIMENSIONS: dict[str, int] = {
    "nvidia/nv-embedqa-e5-v5": 1024,
    "nvidia/nv-embed-v1": 4096,
    "nvidia/llama-3.2-nv-embedqa-1b-v2": 2048,
    "baai/bge-m3": 1024,
}
DEFAULT_DIMENSION = 1024


class InputType(StrEnum):
    """Assimetria query/passage do encoder de QA-retrieval."""

    QUERY = "query"
    PASSAGE = "passage"


class EmbeddingError(RuntimeError):
    """Falha irrecuperável ao gerar embeddings."""


def _is_transient(exc: BaseException) -> bool:
    """Só reenvia o que tem chance de dar certo na próxima tentativa.

    429 (quota) e 5xx são transitórios e frequentes no free tier do NIM. Já um
    400 significa payload inválido — repeti-lo quatro vezes com backoff só atrasa
    a mensagem de erro que o desenvolvedor precisa ler.
    """
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or status >= 500
    return False


class EmbeddingBackend(Protocol):
    """Contrato mínimo do provedor — é o ponto de injeção do fake nos testes."""

    def embed(self, texts: Sequence[str], input_type: InputType) -> list[list[float]]: ...


class NIMEmbeddingBackend:
    """Cliente HTTP direto para o endpoint `/embeddings` do NIM.

    Optamos por HTTP cru em vez de `langchain_nvidia_ai_endpoints.NVIDIAEmbeddings`
    porque o wrapper esconde `input_type` atrás de `embed_query`/`embed_documents`,
    e este é justamente o parâmetro que precisamos manter visível e testável.
    O payload é o mesmo aceito pelo API Catalog.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
        client: httpx.Client | None = None,
    ) -> None:
        settings = get_settings()
        self.api_key = api_key if api_key is not None else settings.nvidia_api_key
        self.base_url = (base_url or settings.nim_base_url).rstrip("/")
        self.model = model or settings.nim_embed_model
        self.timeout = timeout
        self._client = client

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout)
        return self._client

    @retry(
        retry=retry_if_exception(lambda exc: _is_transient(exc)),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        reraise=True,
    )
    def embed(self, texts: Sequence[str], input_type: InputType) -> list[list[float]]:
        if not self.api_key:
            raise EmbeddingError(
                "NVIDIA_API_KEY vazia — sem chave não há embedding. "
                "Injete um EmbeddingBackend alternativo para rodar offline."
            )

        response = self._http().post(
            f"{self.base_url}/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "input": list(texts),
                "input_type": str(input_type),
                "encoding_format": "float",
                # Documento longo demais gera 400 no NIM; truncar no fim preserva
                # o começo da seção, que é onde está o contexto que importa.
                "truncate": "END",
            },
        )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()

        # A API não garante a ordem de `data`; `index` é a fonte da verdade.
        items = sorted(payload["data"], key=lambda item: item["index"])
        return [item["embedding"] for item in items]


class EmbeddingCache(Protocol):
    def get(self, key: str) -> list[float] | None: ...
    def set(self, key: str, vector: list[float]) -> None: ...


class DiskEmbeddingCache:
    """Cache em disco por hash de (modelo, input_type, texto).

    Reingerir a KB é rotina durante o desenvolvimento (mudou o chunking, mudou um
    card). Sem cache, cada rodada consome quota do NIM para recalcular vetores
    idênticos. Shard de dois caracteres evita diretório com dezenas de milhares
    de arquivos.
    """

    def __init__(self, directory: Path | str = PROJECT_ROOT / "data" / "embed_cache") -> None:
        self.directory = Path(directory)

    def _path(self, key: str) -> Path:
        return self.directory / key[:2] / f"{key}.json"

    def get(self, key: str) -> list[float] | None:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # Cache corrompido não pode derrubar ingestão: recalcula.
            log.warning("cache_embedding_corrompido", chave=key)
            return None

    def set(self, key: str, vector: list[float]) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(vector), encoding="utf-8")


class EmbeddingClient:
    """Batching + cache sobre um `EmbeddingBackend`.

    Toda a camada acima (ingestão, busca híbrida) depende desta classe e não do
    backend, então trocar NIM por um fake em teste é uma linha.
    """

    def __init__(
        self,
        backend: EmbeddingBackend,
        *,
        model: str | None = None,
        batch_size: int = 32,
        cache: EmbeddingCache | None = None,
    ) -> None:
        self.backend = backend
        self.model = model or get_settings().nim_embed_model
        self.batch_size = batch_size
        self.cache = cache

    @property
    def dimension(self) -> int:
        return EMBEDDING_DIMENSIONS.get(self.model, DEFAULT_DIMENSION)

    def _cache_key(self, text: str, input_type: InputType) -> str:
        raw = f"{self.model}|{input_type}|{text}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def embed(self, texts: Sequence[str], input_type: InputType) -> list[list[float]]:
        """Gera vetores preservando a ordem de entrada, com cache por item."""
        vectors: list[list[float] | None] = [None] * len(texts)
        pending: list[int] = []

        for i, text in enumerate(texts):
            cached = self.cache.get(self._cache_key(text, input_type)) if self.cache else None
            if cached is None:
                pending.append(i)
            else:
                vectors[i] = cached

        for start in range(0, len(pending), self.batch_size):
            batch_idx = pending[start : start + self.batch_size]
            batch = [texts[i] for i in batch_idx]
            result = self.backend.embed(batch, input_type)
            if len(result) != len(batch):
                raise EmbeddingError(
                    f"NIM devolveu {len(result)} vetores para {len(batch)} textos — "
                    "alinhar texto e vetor pela posição deixou de ser seguro."
                )
            for i, vector in zip(batch_idx, result, strict=True):
                vectors[i] = vector
                if self.cache:
                    self.cache.set(self._cache_key(texts[i], input_type), vector)

        if any(v is None for v in vectors):
            raise EmbeddingError("Vetor ausente após a geração — lote inconsistente.")
        return [v for v in vectors if v is not None]

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        """Para o que vai ao índice. Nunca use isto para a pergunta do usuário."""
        return self.embed(texts, InputType.PASSAGE)

    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        return self.embed(texts, InputType.QUERY)

    def embed_query(self, text: str) -> list[float]:
        return self.embed_queries([text])[0]


def build_embedding_client(*, use_cache: bool = True) -> EmbeddingClient:
    """Fábrica padrão para produção/CLI, lendo tudo de `Settings`."""
    return EmbeddingClient(
        NIMEmbeddingBackend(),
        cache=DiskEmbeddingCache() if use_cache else None,
    )
