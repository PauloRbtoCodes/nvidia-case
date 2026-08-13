"""Chunking semântico guiado pela estrutura do documento.

Por que não fatiar por janela fixa de caracteres: a KB é documentação técnica, e
documentação técnica tem hierarquia. Um chunk que começa no meio da seção
"Deployment" e termina no meio de "Performance" não responde nem uma pergunta
nem a outra — o contexto que tornaria o trecho útil ficou de fora. Aqui a
unidade primária é a seção (delimitada por títulos markdown); a janela de tokens
só entra quando a seção sozinha é grande demais.

O caminho de headings viaja junto com o chunk porque "Limitações" isolado não diz
nada, mas "Triton Inference Server > Model Ensembles > Limitações" diz — e esse
caminho entra no texto enviado ao modelo de embedding (`contextualized_text`),
não só no metadado.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from math import ceil
from typing import Any

import structlog
from pydantic import BaseModel, Field, HttpUrl

from radar.config import get_settings
from radar.models.recommendation import RetrievedChunk

log = structlog.get_logger(__name__)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")
# Quebra de sentença tolerante: documentação mistura pt/en e listas com ";".
_SENTENCE_RE = re.compile(r"(?<=[.!?;:])\s+")


def estimate_tokens(text: str) -> int:
    """Estimativa de tokens sem carregar um tokenizer.

    Heurística: `max(caracteres / 4, palavras * 1.3)`.

    O primeiro termo cobre texto denso em símbolos (blocos de código, flags de
    CLI, nomes tipo `--max-batch-size`), onde o tokenizador BPE fragmenta muito;
    o segundo cobre prosa em português, cuja morfologia rica gera mais de um
    token por palavra. Tomamos o máximo de propósito: superestimar produz chunks
    um pouco menores que o alvo (inofensivo), subestimar produz chunks que
    estouram o limite do encoder e são truncados em silêncio (custoso).

    Precisão de ±15% é suficiente aqui — o alvo de 768 tokens não é uma fronteira
    dura, é um ponto de equilíbrio entre contexto e precisão de recuperação.
    """
    stripped = text.strip()
    if not stripped:
        return 0
    return max(ceil(len(stripped) / 4), ceil(len(stripped.split()) * 1.3))


class Chunk(BaseModel):
    """Unidade indexada da KB NVIDIA: texto + procedência + posição na hierarquia."""

    text: str
    source_url: str
    source_title: str | None = None

    technology: str | None = Field(
        default=None, description="Produto NVIDIA. Vira payload index no Qdrant."
    )
    category: str | None = Field(
        default=None, description="Categoria de `nvidia_sources.yaml`. Vira payload index."
    )
    doc_type: str = Field(
        default="documentacao",
        description="`documentacao` (fonte oficial) ou `card` (enriquecimento manual). "
        "O card é o que responde 'quando recomendar', a doc responde 'o que é'.",
    )

    section_title: str | None = None
    heading_path: list[str] = Field(default_factory=list)
    position: int = Field(default=0, description="Ordem do chunk dentro do documento.")
    token_estimate: int = 0

    @property
    def content_hash(self) -> str:
        """Identidade do chunk = (url, texto).

        É o que torna a reingestão idempotente: o mesmo trecho da mesma página
        gera sempre o mesmo id, então o upsert sobrescreve em vez de duplicar.
        """
        return hashlib.sha256(f"{self.source_url}|{self.text}".encode()).hexdigest()[:24]

    @property
    def chunk_id(self) -> str:
        return self.content_hash

    @property
    def contextualized_text(self) -> str:
        """Texto enviado ao embedding e ao BM25, com a hierarquia embutida.

        Sem isso, um chunk chamado "Pré-requisitos" compete com dezenas de outros
        idênticos em forma; com o caminho, o vetor carrega de qual produto ele fala.
        """
        header_parts = [p for p in (self.source_title, *self.heading_path) if p]
        if not header_parts:
            return self.text
        return f"{' > '.join(header_parts)}\n\n{self.text}"

    def to_payload(self) -> dict[str, Any]:
        """Payload do Qdrant. Campos planos porque índice de payload não indexa aninhado."""
        return {
            "chunk_id": self.chunk_id,
            "text": self.text,
            "source_url": self.source_url,
            "source_title": self.source_title,
            "technology": self.technology,
            "category": self.category,
            "doc_type": self.doc_type,
            "section_title": self.section_title,
            "heading_path": self.heading_path,
            "position": self.position,
            "token_estimate": self.token_estimate,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> Chunk:
        known = set(cls.model_fields)
        return cls(**{k: v for k, v in payload.items() if k in known})

    def to_retrieved_chunk(
        self,
        *,
        dense_score: float | None = None,
        bm25_score: float | None = None,
        rrf_score: float | None = None,
        rerank_score: float | None = None,
    ) -> RetrievedChunk:
        """Converte para o contrato público de saída do RAG (`models/recommendation.py`)."""
        return RetrievedChunk(
            text=self.text,
            source_url=HttpUrl(self.source_url),
            source_title=self.section_title or self.source_title,
            technology=self.technology,
            dense_score=dense_score,
            bm25_score=bm25_score,
            rrf_score=rrf_score,
            rerank_score=rerank_score,
        )


@dataclass(frozen=True)
class Section:
    """Trecho contíguo do documento sob um mesmo caminho de títulos."""

    heading_path: tuple[str, ...]
    body: str

    @property
    def title(self) -> str | None:
        return self.heading_path[-1] if self.heading_path else None

    @property
    def token_estimate(self) -> int:
        return estimate_tokens(self.body)


def split_sections(markdown: str) -> list[Section]:
    """Quebra o documento em seções, mantendo a pilha de títulos.

    Um heading de nível N fecha todos os headings de nível >= N — é assim que a
    hierarquia real do documento é reconstruída a partir de marcação plana.
    """
    sections: list[Section] = []
    stack: list[tuple[int, str]] = []
    buffer: list[str] = []

    def flush() -> None:
        body = "\n".join(buffer).strip()
        buffer.clear()
        if body:
            sections.append(Section(heading_path=tuple(t for _, t in stack), body=body))

    for line in markdown.splitlines():
        match = _HEADING_RE.match(line.strip())
        if match is None:
            buffer.append(line)
            continue
        flush()
        level = len(match.group(1))
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, match.group(2).strip()))

    flush()
    return sections


def _merge_sibling_sections(sections: list[Section], target_tokens: int) -> list[Section]:
    """Funde seções irmãs pequenas para não gerar chunks de duas linhas.

    Só funde quem compartilha o mesmo pai: juntar "Instalação" com "Limitações"
    do mesmo produto preserva o sentido; juntar seções de produtos diferentes o
    destruiria. Seções minúsculas isoladas poluem o ranking — recuperam alto por
    match superficial e não trazem informação suficiente para fundamentar nada.
    """
    merged: list[Section] = []
    for section in sections:
        if not merged:
            merged.append(section)
            continue

        previous = merged[-1]
        same_parent = previous.heading_path[:-1] == section.heading_path[:-1]
        fits = previous.token_estimate + section.token_estimate <= target_tokens
        if same_parent and fits and previous.heading_path and section.heading_path:
            title = section.heading_path[-1]
            merged[-1] = Section(
                heading_path=previous.heading_path,
                body=f"{previous.body}\n\n## {title}\n{section.body}",
            )
        else:
            merged.append(section)
    return merged


def _split_units(body: str, max_tokens: int) -> list[str]:
    """Menores blocos indivisíveis: parágrafo → sentença → janela de palavras."""
    units: list[str] = []
    for paragraph in (p.strip() for p in re.split(r"\n\s*\n", body)):
        if not paragraph:
            continue
        if estimate_tokens(paragraph) <= max_tokens:
            units.append(paragraph)
            continue

        for sentence in (s.strip() for s in _SENTENCE_RE.split(paragraph)):
            if not sentence:
                continue
            if estimate_tokens(sentence) <= max_tokens:
                units.append(sentence)
                continue
            # Último recurso (tabela gigante, bloco de código sem pontuação):
            # janela de palavras. Aqui o corte é arbitrário mesmo.
            words = sentence.split()
            step = max(1, int(max_tokens / 1.3))
            units.extend(" ".join(words[i : i + step]) for i in range(0, len(words), step))
    return units


def _overlap_tail(units: list[str], overlap_tokens: int) -> list[str]:
    """Últimas unidades do chunk anterior que cabem no orçamento de overlap.

    O overlap existe para que uma resposta que atravessa a fronteira do chunk
    ainda apareça inteira em pelo menos um deles. Repetimos unidades completas
    (sentenças/parágrafos), nunca meio período — trecho cortado ao meio não serve
    como citação no briefing.
    """
    if overlap_tokens <= 0 or len(units) < 2:
        return []

    tail: list[str] = []
    total = 0
    for unit in reversed(units[1:]):  # nunca devolve o chunk inteiro
        cost = estimate_tokens(unit)
        if total + cost > overlap_tokens and tail:
            break
        tail.insert(0, unit)
        total += cost
        if total >= overlap_tokens:
            break
    return tail


def chunk_markdown(
    text: str,
    *,
    source_url: str,
    source_title: str | None = None,
    technology: str | None = None,
    category: str | None = None,
    doc_type: str = "documentacao",
    target_tokens: int | None = None,
    overlap_ratio: float | None = None,
) -> list[Chunk]:
    """Converte um documento markdown em chunks prontos para indexação.

    O alvo (`Settings.chunk_size_tokens`) e o overlap (`Settings.chunk_overlap_ratio`)
    são teto e não meta: uma seção de 200 tokens vira um chunk de 200 tokens, sem
    ser esticada com conteúdo alheio para "encher" a janela.
    """
    settings = get_settings()
    target = target_tokens or settings.chunk_size_tokens
    ratio = settings.chunk_overlap_ratio if overlap_ratio is None else overlap_ratio
    overlap_tokens = int(target * ratio)

    sections = _merge_sibling_sections(split_sections(text), target)
    chunks: list[Chunk] = []

    def add(body_units: list[str], section: Section) -> None:
        body = "\n\n".join(body_units).strip()
        if not body:
            return
        chunks.append(
            Chunk(
                text=body,
                source_url=source_url,
                source_title=source_title,
                technology=technology,
                category=category,
                doc_type=doc_type,
                section_title=section.title,
                heading_path=list(section.heading_path),
                position=len(chunks),
                token_estimate=estimate_tokens(body),
            )
        )

    for section in sections:
        units = _split_units(section.body, target)
        current: list[str] = []
        current_tokens = 0

        for unit in units:
            cost = estimate_tokens(unit)
            if current and current_tokens + cost > target:
                add(current, section)
                current = _overlap_tail(current, overlap_tokens)
                current_tokens = sum(estimate_tokens(u) for u in current)
            current.append(unit)
            current_tokens += cost

        add(current, section)

    log.debug(
        "chunking_concluido",
        url=source_url,
        secoes=len(sections),
        chunks=len(chunks),
        alvo_tokens=target,
    )
    return chunks
