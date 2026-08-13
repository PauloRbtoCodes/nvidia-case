"""Contexto citável e validação das citações geradas.

A regra do projeto — nenhuma afirmação sem evidência — só é real se alguém
verificar. LLM cita fonte inexistente com a mesma fluência com que cita a certa:
"[7]" numa resposta construída sobre 5 chunks é sintaticamente indistinguível de
"[3]". Sem a checagem deste módulo, o `Recommendation.kb_citations` passaria a
validação do Pydantic (a lista não está vazia) enquanto o texto aponta para um
trecho que nunca existiu.

O contrato com o LLM é um só: os chunks chegam numerados `[1]..[n]`, e toda
sentença afirmativa precisa terminar com pelo menos um desses marcadores.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import structlog
from pydantic import BaseModel, Field

from radar.models.recommendation import RetrievedChunk

log = structlog.get_logger(__name__)

_CITATION_RE = re.compile(r"\[(\d{1,3})\]")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

#: Sentenças mais curtas que isto são tratadas como conectivo ou título
#: ("Em resumo:", "Próxima ação"), não como afirmação técnica que exija fonte.
_MIN_CLAIM_CHARS = 40

CITATION_INSTRUCTIONS = """\
Regras de citação (obrigatórias):
- Use APENAS as informações dos trechos numerados abaixo.
- Toda frase que afirme um fato deve terminar com o marcador do trecho que a
  sustenta, no formato [n]. Uma frase pode citar mais de um trecho: [1][3].
- Se os trechos não respondem à pergunta, diga isso explicitamente. Não complete
  com conhecimento próprio.
- Nunca cite um número que não apareça na lista de trechos.
"""


class CitationError(ValueError):
    """Resposta com citação inválida ou sem fundamento rastreável."""


class CitationReport(BaseModel):
    """Diagnóstico das citações de uma resposta gerada."""

    cited_indices: list[int] = Field(default_factory=list)
    invalid_indices: list[int] = Field(
        default_factory=list, description="Citações que apontam para chunk inexistente."
    )
    uncited_chunk_indices: list[int] = Field(
        default_factory=list,
        description="Chunks recuperados que a resposta não usou. Não é erro — é sinal "
        "de que o top-N pode estar largo demais.",
    )
    unsupported_sentences: list[str] = Field(
        default_factory=list, description="Afirmações sem nenhum marcador de fonte."
    )

    @property
    def is_valid(self) -> bool:
        return not self.invalid_indices and not self.unsupported_sentences

    @property
    def coverage(self) -> float:
        """Fração dos chunks recuperados efetivamente usada na resposta."""
        total = len(self.cited_indices) + len(self.uncited_chunk_indices)
        return len(self.cited_indices) / total if total else 0.0


def format_context(chunks: Sequence[RetrievedChunk]) -> str:
    """Numera os trechos para o prompt.

    Título e URL entram no bloco porque o modelo tende a produzir justificativas
    melhores quando sabe se está lendo documentação oficial ou um card interno —
    e porque isso deixa a citação legível para quem audita o prompt depois.
    """
    blocos: list[str] = []
    for i, chunk in enumerate(chunks, start=1):
        cabecalho = " · ".join(
            part for part in (chunk.source_title, chunk.technology, str(chunk.source_url)) if part
        )
        blocos.append(f"[{i}] {cabecalho}\n{chunk.text.strip()}")
    return "\n\n".join(blocos)


def build_grounded_prompt(
    question: str,
    chunks: Sequence[RetrievedChunk],
    *,
    task_instructions: str | None = None,
) -> str:
    """Monta o prompt completo: instruções + trechos numerados + pergunta."""
    if not chunks:
        raise CitationError(
            "Prompt sem trechos recuperados. Gerar resposta aqui produziria texto "
            "plausível sem fonte, que é exatamente o que o guardrail proíbe."
        )

    partes = [CITATION_INSTRUCTIONS]
    if task_instructions:
        partes.append(task_instructions.strip())
    partes.append(f"TRECHOS DA BASE NVIDIA:\n\n{format_context(chunks)}")
    partes.append(f"PERGUNTA: {question.strip()}")
    return "\n\n".join(partes)


def extract_citations(answer: str) -> list[int]:
    """Índices citados, na ordem de aparição, sem repetição."""
    vistos: list[int] = []
    for match in _CITATION_RE.finditer(answer):
        valor = int(match.group(1))
        if valor not in vistos:
            vistos.append(valor)
    return vistos


def validate_citations(
    answer: str,
    chunks: Sequence[RetrievedChunk],
    *,
    require_every_sentence: bool = True,
) -> CitationReport:
    """Confere se cada citação existe e se cada afirmação tem citação.

    `require_every_sentence=False` afrouxa a segunda checagem para textos
    narrativos (o resumo executivo do briefing, por exemplo), onde exigir fonte
    em cada frase de ligação produziria ruído sem ganho de auditabilidade.
    """
    validos = set(range(1, len(chunks) + 1))
    citados = extract_citations(answer)

    invalidos = [i for i in citados if i not in validos]
    usados = {i for i in citados if i in validos}

    sem_fonte: list[str] = []
    if require_every_sentence:
        for sentenca in _SENTENCE_SPLIT_RE.split(answer.strip()):
            texto = sentenca.strip()
            if len(texto) < _MIN_CLAIM_CHARS or texto.startswith(("#", "-", "*")):
                continue
            if not _CITATION_RE.search(texto):
                sem_fonte.append(texto)

    report = CitationReport(
        cited_indices=sorted(usados),
        invalid_indices=sorted(set(invalidos)),
        uncited_chunk_indices=sorted(validos - usados),
        unsupported_sentences=sem_fonte,
    )
    if not report.is_valid:
        log.warning(
            "citacao_invalida",
            inexistentes=report.invalid_indices,
            sem_fonte=len(report.unsupported_sentences),
        )
    return report


def enforce_citations(
    answer: str,
    chunks: Sequence[RetrievedChunk],
    *,
    require_every_sentence: bool = True,
) -> CitationReport:
    """Igual a `validate_citations`, mas levanta em vez de reportar.

    Usado no caminho que alimenta `Recommendation`: alucinar a fonte de uma
    recomendação sobre o Triton diante de um founder técnico custa mais do que
    devolver uma recomendação a menos.
    """
    report = validate_citations(answer, chunks, require_every_sentence=require_every_sentence)
    if report.invalid_indices:
        raise CitationError(
            f"Resposta cita os trechos {report.invalid_indices}, que não existem "
            f"entre os {len(chunks)} recuperados."
        )
    if report.unsupported_sentences:
        raise CitationError(
            f"{len(report.unsupported_sentences)} afirmação(ões) sem citação: "
            f"{report.unsupported_sentences[0][:120]!r}"
        )
    return report


def cited_chunks(answer: str, chunks: Sequence[RetrievedChunk]) -> list[RetrievedChunk]:
    """Só os chunks realmente citados — é o que vai para `kb_citations`.

    Anexar os cinco recuperados infla a aparência de fundamentação: o briefing
    exibiria evidências que a justificativa nunca usou.
    """
    return [chunks[i - 1] for i in extract_citations(answer) if 1 <= i <= len(chunks)]
