"""Pipeline de ingestão da KB NVIDIA: fontes → texto limpo → chunks → índices.

Idempotência é requisito, não conveniência. A ingestão roda várias vezes durante
o desenvolvimento (mudou o chunking, entrou um card novo, uma página foi
atualizada) e uma KB com o mesmo parágrafo em triplicata enviesa o ranking:
três cópias ocupam três das cinco vagas do top-N e expulsam informação
complementar. A garantia vem do id determinístico por hash de conteúdo — ver
`store.point_id_for` — e não de um `drop` prévio da coleção.

Duas fontes alimentam a base:

1. `data/nvidia_sources.yaml` — documentação oficial. Responde "o que é".
2. `data/nvidia_cards/*.md` — cards escritos à mão. Respondem "quando recomendar".

Só a segunda faz o motor de recomendação funcionar, e ela ainda não existe no
repositório: a ausência do diretório é estado normal e não pode quebrar a rodada.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import structlog
import yaml
from pydantic import BaseModel, Field

from radar.config import PROJECT_ROOT, get_settings
from radar.rag.bm25 import DEFAULT_INDEX_DIR, BM25Index
from radar.rag.chunking import Chunk, chunk_markdown
from radar.rag.embed import EmbeddingClient
from radar.rag.store import QdrantKnowledgeBase

log = structlog.get_logger(__name__)

DEFAULT_SOURCES_PATH = PROJECT_ROOT / "data" / "nvidia_sources.yaml"
DEFAULT_CARDS_DIR = PROJECT_ROOT / "data" / "nvidia_cards"

#: Chaves de `nvidia_sources.yaml` que não são listas de fontes.
_NON_SOURCE_KEYS = frozenset({"version", "cards_manuais"})

#: Host sentinela para card sem URL canônica. `RetrievedChunk.source_url` é
#: `HttpUrl` obrigatório, e `file://` não passa — mas a citação precisa apontar
#: para algum lugar identificável, e um host reservado deixa óbvio na UI que a
#: fonte é interna, não uma página da NVIDIA.
CARD_URL_SENTINEL = "https://kb.local.invalid/nvidia_cards"


@dataclass(frozen=True)
class SourceDocument:
    """Uma entrada de `nvidia_sources.yaml`, já achatada."""

    url: str
    title: str | None = None
    category: str | None = None
    technology: str | None = None
    section: str | None = None
    doc_type: str = "documentacao"


@dataclass
class RawDocument:
    """Documento já com conteúdo, pronto para chunking."""

    source: SourceDocument
    markdown: str


class Fetcher(Protocol):
    """Coleta o conteúdo de uma URL como markdown. `None` = falhou/vazio."""

    def fetch(self, url: str) -> str | None: ...


class TrafilaturaFetcher:
    """Caminho padrão do projeto: `httpx` + `trafilatura` em saída markdown.

    Markdown importa aqui: é o que preserva os títulos que o chunker usa para
    respeitar a estrutura do documento. Extração em texto puro achataria a
    hierarquia e o chunking degradaria para janela fixa.
    """

    def __init__(self, *, timeout: float | None = None, user_agent: str | None = None) -> None:
        settings = get_settings()
        self.timeout = timeout or settings.scraper_timeout_seconds
        self.user_agent = user_agent or settings.scraper_user_agent

    def fetch(self, url: str) -> str | None:
        import httpx
        import trafilatura

        try:
            response = httpx.get(
                url,
                timeout=self.timeout,
                follow_redirects=True,
                headers={"User-Agent": self.user_agent},
            )
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001 - uma fonte fora do ar não derruba o lote
            log.warning("fonte_indisponivel", url=url, erro=str(exc))
            return None

        extracted = trafilatura.extract(
            response.text,
            output_format="markdown",
            include_tables=True,
            include_comments=False,
            favor_recall=True,
            url=url,
        )
        if not extracted or not extracted.strip():
            log.warning("extracao_vazia", url=url)
            return None
        return extracted


class IngestReport(BaseModel):
    """Resultado da rodada. É o que o CLI imprime e o que o teste inspeciona."""

    documents_ok: int = 0
    documents_failed: int = 0
    cards_ingested: int = 0
    chunks_total: int = 0
    chunks_new: int = 0
    chunks_skipped: int = Field(
        default=0, description="Já presentes na coleção (reingestão) ou duplicados na rodada."
    )
    failed_urls: list[str] = Field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return self.chunks_total == 0


def load_sources(path: Path | str = DEFAULT_SOURCES_PATH) -> list[SourceDocument]:
    """Achata o YAML por seções numa lista única.

    A seção (`inferencia`, `dados`, ...) vira metadado, mas o filtro do RAG usa
    `category`, que é mais fino: dentro de `modelos` convivem `customizacao_modelos`
    e `governanca`, e recomendar Guardrails quando o gap é fine-tuning seria errado.
    """
    data: dict[str, Any] = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    documents: list[SourceDocument] = []

    for section, entries in data.items():
        if section in _NON_SOURCE_KEYS or not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict) or "url" not in entry:
                continue
            documents.append(
                SourceDocument(
                    url=str(entry["url"]),
                    title=entry.get("title"),
                    category=entry.get("category"),
                    technology=entry.get("technology"),
                    section=section,
                )
            )
    return documents


def _parse_front_matter(text: str) -> tuple[dict[str, Any], str]:
    """Front matter YAML opcional delimitado por `---`."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        log.warning("front_matter_invalido")
        return {}, text
    return (meta if isinstance(meta, dict) else {}), parts[2].lstrip("\n")


def load_cards(directory: Path | str = DEFAULT_CARDS_DIR) -> list[RawDocument]:
    """Lê os cards manuais. Diretório ausente devolve lista vazia, sem erro.

    Os cards ainda não foram escritos; o pipeline precisa rodar antes deles para
    que a KB oficial já esteja indexada e testável.
    """
    path = Path(directory)
    if not path.is_dir():
        log.info("cards_manuais_ausentes", caminho=str(path))
        return []

    documents: list[RawDocument] = []
    for card_path in sorted(path.glob("*.md")):
        raw = card_path.read_text(encoding="utf-8")
        meta, body = _parse_front_matter(raw)
        if not body.strip():
            log.warning("card_vazio", arquivo=card_path.name)
            continue

        slug = card_path.stem
        url = str(meta.get("url") or f"{CARD_URL_SENTINEL}/{slug}")
        if not meta.get("url"):
            log.info("card_sem_url_canonica", arquivo=card_path.name)

        title = meta.get("title") or _first_heading(body) or slug.replace("-", " ").title()
        documents.append(
            RawDocument(
                source=SourceDocument(
                    url=url,
                    title=str(title),
                    category=str(meta.get("category") or "card_recomendacao"),
                    technology=(str(meta["technology"]) if meta.get("technology") else None),
                    section="cards_manuais",
                    doc_type="card",
                ),
                markdown=body,
            )
        )
    return documents


def _first_heading(markdown: str) -> str | None:
    match = re.search(r"^#\s+(.+)$", markdown, flags=re.MULTILINE)
    return match.group(1).strip() if match else None


@dataclass
class _Collected:
    chunks: list[Chunk] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)
    skipped: int = 0

    def add(self, chunks: Iterable[Chunk]) -> None:
        for chunk in chunks:
            # Dedupe intra-rodada: URLs distintas servindo o mesmo boilerplate
            # ("NVIDIA Inception oferece créditos...") são comuns no site da NVIDIA.
            if chunk.chunk_id in self.seen:
                self.skipped += 1
                continue
            self.seen.add(chunk.chunk_id)
            self.chunks.append(chunk)


def collect_chunks(
    documents: Sequence[RawDocument],
    *,
    target_tokens: int | None = None,
    overlap_ratio: float | None = None,
) -> list[Chunk]:
    """Chunking de todos os documentos, com deduplicação por hash de conteúdo."""
    collected = _Collected()
    for document in documents:
        collected.add(
            chunk_markdown(
                document.markdown,
                source_url=document.source.url,
                source_title=document.source.title,
                technology=document.source.technology,
                category=document.source.category,
                doc_type=document.source.doc_type,
                target_tokens=target_tokens,
                overlap_ratio=overlap_ratio,
            )
        )
    return collected.chunks


def ingest_documents(
    documents: Sequence[RawDocument],
    *,
    knowledge_base: QdrantKnowledgeBase,
    embedder: EmbeddingClient,
    bm25_index: BM25Index,
    skip_existing: bool = True,
) -> IngestReport:
    """Indexa documentos já coletados. Separado de `ingest_knowledge_base` para
    que o teste exercite o pipeline inteiro sem tocar a rede."""
    report = IngestReport()
    chunks = collect_chunks(documents)
    report.chunks_total = len(chunks)
    if not chunks:
        return report

    knowledge_base.ensure_collection()

    pendentes = chunks
    if skip_existing:
        # Reembeddar o que já está indexado é gasto puro de quota do NIM: o texto
        # é idêntico (o hash é derivado dele), logo o vetor também seria.
        existentes = knowledge_base.existing_chunk_ids(c.chunk_id for c in chunks)
        pendentes = [c for c in chunks if c.chunk_id not in existentes]
        report.chunks_skipped = len(chunks) - len(pendentes)

    report.chunks_new = len(pendentes)
    if pendentes:
        vetores = embedder.embed_passages([c.contextualized_text for c in pendentes])
        knowledge_base.upsert_chunks(pendentes, vetores)

    # O BM25 recebe todos os chunks, inclusive os já indexados no Qdrant: o índice
    # lexical é local e pode ter sido apagado (`make clean`) sem que a coleção fosse.
    bm25_index.upsert(chunks)

    log.info(
        "ingestao_concluida",
        chunks=report.chunks_total,
        novos=report.chunks_new,
        ja_indexados=report.chunks_skipped,
    )
    return report


def ingest_knowledge_base(
    *,
    knowledge_base: QdrantKnowledgeBase,
    embedder: EmbeddingClient,
    bm25_index: BM25Index | None = None,
    fetcher: Fetcher | None = None,
    sources_path: Path | str = DEFAULT_SOURCES_PATH,
    cards_dir: Path | str | None = DEFAULT_CARDS_DIR,
    bm25_dir: Path | str = DEFAULT_INDEX_DIR,
    skip_existing: bool = True,
    persist_bm25: bool = True,
) -> IngestReport:
    """Rodada completa: YAML + cards → Qdrant + BM25.

    Todas as dependências externas entram por parâmetro. É o que permite testar o
    pipeline sem rede nem container, e é também o que torna possível ingerir a
    partir de um cache local sem reescrever o fluxo.
    """
    fetcher = fetcher or TrafilaturaFetcher()
    bm25_index = bm25_index if bm25_index is not None else BM25Index.load(bm25_dir)

    documents: list[RawDocument] = []
    falhas: list[str] = []

    for source in load_sources(sources_path):
        markdown = fetcher.fetch(source.url)
        if not markdown:
            falhas.append(source.url)
            continue
        documents.append(RawDocument(source=source, markdown=markdown))

    cards = load_cards(cards_dir) if cards_dir else []
    documents.extend(cards)

    report = ingest_documents(
        documents,
        knowledge_base=knowledge_base,
        embedder=embedder,
        bm25_index=bm25_index,
        skip_existing=skip_existing,
    )
    report.documents_ok = len(documents) - len(cards)
    report.documents_failed = len(falhas)
    report.failed_urls = falhas
    report.cards_ingested = len(cards)

    if persist_bm25 and len(bm25_index):
        bm25_index.save(bm25_dir)

    if not cards:
        # Aviso deliberado: sem os cards, o RAG responde "o que é o Triton" e
        # falha em "qual tecnologia para esta startup" — que é a pergunta do case.
        log.warning("kb_sem_cards_manuais", impacto="motor de recomendacao sem sinal de 'quando'")

    return report
