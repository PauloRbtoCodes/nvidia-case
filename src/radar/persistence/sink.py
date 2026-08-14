"""Grava o resultado do grafo no Postgres.

Ponte entre `CompanyState` (saída do subgrafo) e os repositórios. Existe como
módulo próprio porque tem dois consumidores — a CLI e a API — e duplicar a ordem
de escrita entre eles é o tipo de divergência que só aparece quando um dos dois
grava pela metade e ninguém sabe qual.

A ordem importa e não é arbitrária: evidências antes de tudo que as referencia,
porque classificações, scores e recomendações guardam `content_hash` e não o
objeto. Gravar na ordem errada produz linhas com referência pendente.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy.orm import Session

from radar.persistence import db
from radar.persistence.repositories import (
    BriefingRepository,
    ClassificationRepository,
    CompanyRepository,
    EvidenceRepository,
    ScoreRepository,
    collect_evidences,
)

log = structlog.get_logger(__name__)


@dataclass
class PersistReport:
    """O que entrou, o que não entrou e por quê."""

    saved: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    unavailable: str | None = None
    """Preenchido quando o banco inteiro está fora do ar — distinto de falha por empresa."""

    @property
    def ok(self) -> bool:
        return self.unavailable is None and not self.errors


def persist_company_state(session: Session, state: dict[str, Any]) -> bool:
    """Grava uma empresa dentro da transação de quem chamou.

    Devolve `False` quando não havia perfil — empresa cuja coleta falhou não tem
    nada a persistir, e isso não é erro: a lacuna já está registrada em
    `skipped`/`failures` no estado do lote.
    """
    profile = state.get("profile")
    if profile is None:
        return False

    empresa = CompanyRepository.upsert(session, profile)
    EvidenceRepository.bulk_upsert(session, empresa.id, collect_evidences(profile))

    classificacao = state.get("classification")
    if classificacao is not None:
        ClassificationRepository.add(session, empresa.id, classificacao)

    # `BriefingRepository.save` grava o score junto e reamarra as recomendações a
    # ele; chamar o ScoreRepository antes duplicaria a linha de score.
    briefing = state.get("briefing")
    score = state.get("defensibility")
    if briefing is not None:
        BriefingRepository.save(session, empresa.id, briefing)
    elif score is not None:
        ScoreRepository.add(session, empresa.id, score, state.get("priority"))

    return True


def persist_batch(results: Sequence[dict[str, Any]]) -> PersistReport:
    """Grava o lote inteiro, uma transação por empresa.

    Cada empresa isolada de propósito: um perfil que viole uma constraint não
    pode levar junto as outras onze que estavam corretas. É a mesma lógica do
    subgrafo isolado no fan-out, aplicada à escrita.
    """
    report = PersistReport()

    # Chamada pelo módulo, e não por nome importado: o binding local congelaria no
    # import e um dublê instalado depois (teste, ou a API decidindo rodar sem
    # banco) não teria efeito.
    if not db.healthcheck():
        report.unavailable = "banco inacessível"
        log.warning("persistencia_indisponivel")
        return report

    for estado in results:
        nome = estado.get("company_name") or "?"
        profile = estado.get("profile")
        if profile is not None:
            nome = profile.name
        try:
            with db.session_scope() as sessao:
                gravou = persist_company_state(sessao, estado)
            (report.saved if gravou else report.skipped).append(nome)
        except Exception as exc:  # noqa: BLE001 - uma empresa não derruba o lote
            log.warning("empresa_nao_persistida", empresa=nome, erro=str(exc)[:300])
            report.errors.append((nome, str(exc)[:200]))

    log.info(
        "persistencia_concluida",
        gravadas=len(report.saved),
        sem_perfil=len(report.skipped),
        erros=len(report.errors),
    )
    return report
