"""Evidence Validator: audita se o perfil se sustenta nas fontes.

Segunda barreira do invariante "nenhuma afirmação sem evidência". A primeira
(no Extractor) é mecânica e pergunta *o trecho existe na fonte?*. Esta pergunta
é outra: *o trecho sustenta o que o campo afirma?* — que é julgamento sobre
linguagem e precisa de modelo.

O nó também decide se vale voltar ao Scraper. Essa aresta é o único ciclo do
subgrafo, e tem teto (`MAX_SCRAPE_ATTEMPTS`): sem ele, uma startup que
simplesmente não publica nada sobre stack consumiria quota para sempre.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

import structlog

from radar.graph.nodes.base import dump, format_documents, node_guard
from radar.graph.nodes.deps import NodeDeps
from radar.graph.state import (
    MAX_SCRAPE_ATTEMPTS,
    MIN_EVIDENCE_COVERAGE,
    CompanyState,
)
from radar.llm.model_registry import LLMTask
from radar.llm.schemas import EvidenceAudit
from radar.models.company import CompanyProfile

log = structlog.get_logger(__name__)


def _anular_campos(profile: CompanyProfile, campos: list[str]) -> CompanyProfile:
    """Zera os campos que o auditor considerou sem lastro.

    Anular e não rebaixar: um campo cuja citação não o sustenta precisa voltar a
    ser desconhecido. Mantê-lo com confiança menor produziria exatamente o que o
    projeto proíbe — uma afirmação com aparência de fundamento.
    """
    dados = profile.model_dump()
    mexidos = False
    for campo in campos:
        # Só campos que existem e ainda estão preenchidos; o auditor às vezes
        # nomeia um caminho aninhado (`tech_signals[2]`) que não tratamos aqui.
        if campo in dados and dados[campo] is not None:
            dados[campo] = None
            mexidos = True
    return CompanyProfile.model_validate(dados) if mexidos else profile


def precisa_recoletar(*, scrape_attempts: int, coverage: float, pediu: bool) -> bool:
    """Decide o ciclo de re-coleta, respeitando o orçamento.

    Duas condições disparam: o auditor pediu explicitamente, ou a cobertura de
    evidência ficou abaixo do piso. A segunda existe porque o auditor julga o que
    *está* no perfil — ele não reclama de um campo que o Extractor nunca chegou a
    preencher, e um perfil quase vazio passaria pela auditoria sem uma ressalva.

    O teto vem antes das duas: orçamento estourado encerra o ciclo mesmo com
    lacuna aberta. A lacuna vira `caveat` no briefing, que é uma saída honesta;
    o loop infinito não é saída nenhuma.
    """
    if scrape_attempts >= MAX_SCRAPE_ATTEMPTS:
        return False
    return pediu or coverage < MIN_EVIDENCE_COVERAGE


def make_validate_evidence(
    deps: NodeDeps,
) -> Callable[[CompanyState], Coroutine[Any, Any, dict[str, Any]]]:
    @node_guard("evidence_validator")
    async def validate_evidence(state: CompanyState) -> dict[str, Any]:
        profile = state.get("profile")
        paginas = state.get("raw_pages") or []
        if profile is None:
            raise ValueError("Auditoria sem perfil extraído.")

        audit = await deps.run_llm(
            LLMTask.EVIDENCE_VALIDATOR,
            EvidenceAudit,
            company_profile_json=dump(profile),
            source_documents=format_documents(paginas),
        )

        saneado = _anular_campos(profile, list(audit.unsupported_fields))
        notas = [*(state.get("validation_notes") or [])]
        notas.extend(
            f"{a.field_path}: {a.verdict.value} — {a.explanation}"
            for a in audit.audits
            if a.verdict.value != "supported"
        )

        recoletar = precisa_recoletar(
            scrape_attempts=state.get("scrape_attempts", 0),
            coverage=saneado.evidence_coverage,
            pediu=audit.requires_recollection,
        )

        log.info(
            "auditoria",
            empresa=profile.name,
            grounding=audit.grounding_ratio,
            anulados=len(audit.unsupported_fields),
            cobertura=saneado.evidence_coverage,
            tentativa=state.get("scrape_attempts", 0),
            recoletar=recoletar,
        )
        return {
            "profile": saneado,
            "validation_notes": notas,
            "refetch_queries": list(audit.suggested_queries),
            "grounding_ratio": audit.grounding_ratio,
            "requires_recollection": recoletar,
        }

    return validate_evidence
