"""Busca lexical BM25, persistida em disco.

Por que manter um índice lexical ao lado do vetorial: embedding é bom em
paráfrase e ruim em identificador. "TensorRT-LLM", "cuDF", "NIM", "L40S" são
strings arbitrárias — o modelo não aprendeu nada sobre elas que as separe de
"TensorRT" ou de "Triton", e uma busca por "TensorRT-LLM" costuma trazer meia
página sobre inferência genérica antes do documento certo. BM25 acerta isso por
construção, porque compara símbolos, não sentidos.

Persistência: guardamos o corpus tokenizado (JSON), não o objeto `BM25Okapi`
serializado. Pickle de objeto de biblioteca quebra em upgrade de versão e o
custo de reconstruir o índice a partir dos tokens é irrisório na escala da KB
(centenas de chunks, não milhões).
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import structlog
from rank_bm25 import BM25Okapi

from radar.config import PROJECT_ROOT
from radar.rag.chunking import Chunk

log = structlog.get_logger(__name__)

DEFAULT_INDEX_DIR = PROJECT_ROOT / "data" / "bm25_index"
_CORPUS_FILE = "corpus.json"

# Token técnico: sequência alfanumérica que pode conter hífen, ponto ou underscore
# internos. Preserva "tensorrt-llm", "nv-embedqa-e5-v5", "cudf.pandas", "h100".
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[-_./][a-z0-9]+)*")

#: Stopwords pt/en. Lista curta de propósito: em corpus técnico, palavra rara é
#: sinal, e o IDF do próprio BM25 já cuida das frequentes. Removemos só o que é
#: puro ruído estrutural.
_PT_STOPWORDS = """a o e de da do das dos em no na nos nas um uma uns umas para por com
sem sobre que se ao aos as os como mais menos ja nao sim ou entre este esta isso aquele"""
_EN_STOPWORDS = """the of and to in for on with without as at by is are be from this
that it its"""
_STOPWORDS = frozenset(_PT_STOPWORDS.split() + _EN_STOPWORDS.split())


def _fold_accents(text: str) -> str:
    """Dobra acentos para que "inferência" e "inferencia" caiam no mesmo token.

    Documentação NVIDIA é em inglês, a pergunta do analista sai em português e
    quase nunca acentuada. Sem o folding, metade dos matches lexicais em pt se
    perde por causa de um til.
    """
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def tokenize(text: str) -> list[str]:
    """Tokenização para texto técnico pt/en.

    Além do token inteiro, emitimos suas partes quando ele é composto: quem
    pergunta "TensorRT" precisa encontrar "TensorRT-LLM", e quem pergunta
    "TensorRT-LLM" ganha o match do token completo, que pesa mais por ser raro.
    """
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(_fold_accents(text.lower())):
        if raw in _STOPWORDS:
            continue
        tokens.append(raw)
        if any(sep in raw for sep in "-_./"):
            parts = [p for p in re.split(r"[-_./]", raw) if p and p not in _STOPWORDS]
            tokens.extend(parts)
    return tokens


@dataclass(frozen=True)
class LexicalHit:
    chunk: Chunk
    score: float


class BM25Index:
    """Índice lexical sobre os mesmos chunks indexados no Qdrant."""

    def __init__(self, chunks: Sequence[Chunk] | None = None) -> None:
        self._chunks: list[Chunk] = list(chunks or [])
        self._corpus: list[list[str]] = [tokenize(c.contextualized_text) for c in self._chunks]
        self._bm25: BM25Okapi | None = None
        self._token_sets: list[set[str]] = []
        self._rebuild()

    def _rebuild(self) -> None:
        # BM25Okapi calcula IDF na construção: qualquer mudança no corpus exige
        # reconstruir. Não existe update incremental na lib, e fingir que existe
        # produziria scores calculados com IDF desatualizado.
        self._bm25 = BM25Okapi(self._corpus) if self._corpus else None
        self._token_sets = [set(tokens) for tokens in self._corpus]

    def __len__(self) -> int:
        return len(self._chunks)

    @property
    def chunks(self) -> list[Chunk]:
        return list(self._chunks)

    def upsert(self, chunks: Sequence[Chunk]) -> int:
        """Insere ou substitui por `chunk_id`. Retorna o número de chunks novos."""
        by_id = {c.chunk_id: i for i, c in enumerate(self._chunks)}
        added = 0
        for chunk in chunks:
            tokens = tokenize(chunk.contextualized_text)
            index = by_id.get(chunk.chunk_id)
            if index is None:
                by_id[chunk.chunk_id] = len(self._chunks)
                self._chunks.append(chunk)
                self._corpus.append(tokens)
                added += 1
            else:
                self._chunks[index] = chunk
                self._corpus[index] = tokens
        self._rebuild()
        return added

    def search(self, query: str, *, top_k: int = 30) -> list[LexicalHit]:
        if self._bm25 is None:
            return []

        tokens = tokenize(query)
        if not tokens:
            return []

        alvo = set(tokens)
        scores = self._bm25.get_scores(tokens)

        # O corte é por sobreposição de termos, não por score positivo: o IDF do
        # BM25Okapi fica negativo quando o termo aparece em quase todo o corpus
        # (caso comum numa KB pequena e monotemática como esta), e filtrar por
        # `score > 0` descartaria justamente o match exato que motivou o índice.
        candidatos = [
            (i, float(score))
            for i, score in enumerate(scores)
            if self._token_sets[i] & alvo
        ]
        candidatos.sort(key=lambda pair: pair[1], reverse=True)
        return [LexicalHit(chunk=self._chunks[i], score=score) for i, score in candidatos[:top_k]]

    def save(self, directory: Path | str = DEFAULT_INDEX_DIR) -> Path:
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        target = path / _CORPUS_FILE
        target.write_text(
            json.dumps(
                {
                    "version": 1,
                    "documents": [
                        {"chunk": chunk.model_dump(), "tokens": tokens}
                        for chunk, tokens in zip(self._chunks, self._corpus, strict=True)
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        log.info("bm25_persistido", caminho=str(target), documentos=len(self._chunks))
        return target

    @classmethod
    def load(cls, directory: Path | str = DEFAULT_INDEX_DIR) -> BM25Index:
        """Carrega o índice; devolve índice vazio se ainda não houve ingestão.

        Índice ausente não é erro: é o estado normal antes do primeiro `make
        ingest`. Quem consome trata `len(index) == 0` como "sem sinal lexical".
        """
        target = Path(directory) / _CORPUS_FILE
        if not target.exists():
            log.warning("bm25_ausente", caminho=str(target))
            return cls()

        data = json.loads(target.read_text(encoding="utf-8"))
        index = cls()
        index._chunks = [Chunk(**doc["chunk"]) for doc in data["documents"]]
        index._corpus = [list(doc["tokens"]) for doc in data["documents"]]
        index._rebuild()
        return index
