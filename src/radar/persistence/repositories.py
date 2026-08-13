"""Repositórios: a única fronteira onde Pydantic vira ORM e vice-versa.

Se a conversão vazar para os nós do grafo ou para a API, cada camada acaba
inventando seu próprio jeito de gravar uma evidência — e o contrato Pydantic
deixa de ser fonte da verdade na prática, mesmo continuando a ser no papel.
Todo `model_dump` e todo `model_validate` que toca no banco mora aqui.

Convenção: os métodos recebem a `Session` explicitamente em vez de abrir a sua.
Quem chama decide a fronteira da transação — salvar empresa, evidências e score
de uma execução do grafo precisa ser atômico, e isso só é possível se o
repositório não fizer commit sozinho.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from radar.models.company import (
    AIMaturity,
    Classification,
    CompanyProfile,
    Founder,
    FundingRound,
    InferenceProvider,
    Stage,
    TechSignal,
)
from radar.models.evidence import Evidence
from radar.models.recommendation import Briefing, Recommendation, RetrievedChunk
from radar.models.scoring import (
    AxisScore,
    DefensibilityAxis,
    DefensibilityScore,
    PriorityAssessment,
    PriorityBucket,
    TCOEstimate,
)
from radar.persistence import tables

# --------------------------------------------------------------------------- #
# Normalização de identidade
# --------------------------------------------------------------------------- #

#: Sufixos societários não distinguem startups: "Acme Ltda" e "Acme" são a mesma
#: empresa encontrada por caminhos diferentes (Receita Federal vs. site).
_LEGAL_SUFFIXES = frozenset({"ltda", "sa", "eireli", "me", "epp", "inc", "llc", "corp"})


def normalize_name(name: str) -> str:
    """Chave de dedupe por nome: sem acento, sem pontuação, sem sufixo societário."""
    folded = unicodedata.normalize("NFKD", name.casefold())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    tokens = [t for t in "".join(c if c.isalnum() else " " for c in folded).split() if t]

    while tokens:
        if tokens[-1] in _LEGAL_SUFFIXES:
            tokens.pop()
        elif len(tokens) > 2 and tokens[-2:] == ["s", "a"]:
            # "S.A." vira dois tokens de uma letra ao tirar a pontuação. Exigir um
            # terceiro token evita comer o nome inteiro de uma empresa chamada "S A".
            del tokens[-2:]
        else:
            break

    return " ".join(tokens) or name.casefold().strip()


def normalize_website(website: Any) -> str | None:
    """Chave de dedupe por domínio, que é o identificador mais confiável que temos.

    Nome de startup muda e se repete; domínio raramente. Descartamos esquema,
    `www.`, porta padrão e barra final — variações puramente cosméticas que
    fariam a mesma empresa entrar duas vezes na fila do gerente.
    """
    if website is None:
        return None
    raw = str(website).strip().casefold()
    if not raw:
        return None
    for prefix in ("https://", "http://"):
        raw = raw.removeprefix(prefix)
    raw = raw.removeprefix("www.").rstrip("/")
    return raw or None


def _utc(value: datetime | None) -> datetime | None:
    """Reata o fuso perdido no SQLite.

    SQLite não guarda timezone; devolver um datetime naive faria `Evidence.trust`
    estourar ao subtrair de um `datetime.now(UTC)`. Como só gravamos UTC, reatar
    é seguro — e o teste em SQLite continua exercitando o mesmo código do Postgres.
    """
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Evidência ↔ referência por hash
# --------------------------------------------------------------------------- #


def _evidence_to_pydantic(row: tables.Evidence) -> Evidence:
    return Evidence(
        url=row.url,
        kind=row.kind,  # type: ignore[arg-type]  # StrEnum valida na entrada
        excerpt=row.excerpt,
        context=row.context,
        collected_at=_utc(row.collected_at) or datetime.now(UTC),
        published_at=_utc(row.published_at),
    )


def _dump_with_refs(model: BaseModel) -> dict[str, Any]:
    """Serializa um modelo trocando suas evidências por hashes.

    O trecho literal fica só na tabela `evidences`. Duplicá-lo dentro do JSON
    criaria duas cópias que divergem no primeiro reprocessamento — e a evidência
    só cumpre sua função de prova enquanto for uma única versão.
    """
    data = model.model_dump(mode="json")
    evidences = getattr(model, "evidences", [])
    data.pop("evidences", None)
    data["evidence_hashes"] = [ev.content_hash for ev in evidences]
    return data


def _load_with_refs[T: BaseModel](
    model_cls: type[T], data: dict[str, Any], by_hash: dict[str, Evidence]
) -> T:
    """Reidrata o modelo resolvendo os hashes contra as evidências da empresa.

    Hash órfão é ignorado em silêncio: evidência apagada por retenção não deve
    impedir a leitura do perfil, apenas reduzir a confiança calculada — que é
    exatamente o comportamento correto do `EvidenceBackedField`.
    """
    payload = dict(data)
    hashes = payload.pop("evidence_hashes", []) or []
    payload["evidences"] = [by_hash[h].model_dump(mode="json") for h in hashes if h in by_hash]
    return model_cls.model_validate(payload)


#: Campos inferidos do perfil e o tipo do seu `value` — necessário para reidratar
#: o genérico `EvidenceBackedField[T]` com o parâmetro certo.
_INFERRED_FIELDS: dict[str, Any] = {
    "sector": str,
    "target_market": str,
    "ai_use_description": str,
    "inference_provider": InferenceProvider,
    "proprietary_data_claim": str,
    "named_integrations": list[str],
    "enterprise_customers": list[str],
}


def collect_evidences(profile: CompanyProfile) -> list[Evidence]:
    """Todas as evidências do perfil, das aninhadas às de topo, sem repetição.

    O extrator preenche `all_evidences`, mas nem todo caminho do grafo passa por
    ele — varrer as coleções aninhadas garante que nenhum trecho citado por um
    campo inferido fique fora da tabela canônica.
    """
    seen: dict[str, Evidence] = {}

    def _add(evidences: Iterable[Evidence]) -> None:
        for ev in evidences:
            seen.setdefault(ev.content_hash, ev)

    _add(profile.all_evidences)
    for field_name in _INFERRED_FIELDS:
        field = getattr(profile, field_name)
        if field is not None:
            _add(field.evidences)
    for item in (*profile.founders, *profile.tech_signals, *profile.funding_rounds):
        _add(item.evidences)
    return list(seen.values())


# --------------------------------------------------------------------------- #
# Empresas
# --------------------------------------------------------------------------- #


class CompanyRepository:
    """Upsert idempotente + leitura do perfil completo."""

    @staticmethod
    def find(session: Session, profile: CompanyProfile) -> tables.Company | None:
        """Procura por domínio e, só depois, por nome normalizado.

        A ordem importa: domínio é forte, nome é fraco. Casar por nome primeiro
        fundiria duas startups homônimas de setores diferentes.
        """
        site = normalize_website(profile.website)
        if site:
            found = session.scalar(
                select(tables.Company).where(tables.Company.normalized_website == site)
            )
            if found is not None:
                return found
        return session.scalar(
            select(tables.Company).where(
                tables.Company.normalized_name == normalize_name(profile.name)
            )
        )

    @staticmethod
    def upsert(session: Session, profile: CompanyProfile) -> tables.Company:
        """Insere ou atualiza a empresa e sincroniza suas evidências.

        Política de merge: valor novo não-nulo sobrescreve; valor nulo preserva o
        que já havia. Uma segunda passada que só conseguiu ler a página de
        carreiras não pode apagar o setor descoberto na primeira.
        """
        row = CompanyRepository.find(session, profile)
        if row is None:
            row = tables.Company(
                name=profile.name,
                normalized_name=normalize_name(profile.name),
            )
            session.add(row)

        row.name = profile.name
        row.normalized_name = normalize_name(profile.name)
        if profile.website is not None:
            row.website = str(profile.website)
            row.normalized_website = normalize_website(profile.website)

        for attr in ("description", "founded_year", "hq_city", "hq_state", "headcount_estimate"):
            value = getattr(profile, attr)
            if value is not None:
                setattr(row, attr, value)

        if profile.stage is not Stage.DESCONHECIDO or row.stage is None:
            row.stage = profile.stage.value

        for field_name in _INFERRED_FIELDS:
            field = getattr(profile, field_name)
            if field is not None:
                setattr(row, field_name, _dump_with_refs(field))

        if profile.tech_signals:
            row.tech_signals = [_dump_with_refs(s) for s in profile.tech_signals]
        if profile.founders:
            row.founders = [_dump_with_refs(f) for f in profile.founders]
        if profile.funding_rounds:
            row.funding_rounds = [_dump_with_refs(r) for r in profile.funding_rounds]
        if profile.open_engineering_roles:
            row.open_engineering_roles = list(profile.open_engineering_roles)
        if profile.source_urls:
            row.source_urls = [str(u) for u in profile.source_urls]

        session.flush()  # precisamos do id antes de amarrar as evidências
        EvidenceRepository.bulk_upsert(session, row.id, collect_evidences(profile))
        return row

    @staticmethod
    def get(session: Session, company_id: UUID) -> CompanyProfile | None:
        row = session.get(tables.Company, company_id)
        return None if row is None else CompanyRepository.to_pydantic(session, row)

    @staticmethod
    def get_by_website(session: Session, website: str) -> tables.Company | None:
        site = normalize_website(website)
        if site is None:
            return None
        return session.scalar(
            select(tables.Company).where(tables.Company.normalized_website == site)
        )

    @staticmethod
    def get_by_name(session: Session, name: str) -> tables.Company | None:
        return session.scalar(
            select(tables.Company).where(tables.Company.normalized_name == normalize_name(name))
        )

    @staticmethod
    def list(
        session: Session,
        *,
        name_contains: str | None = None,
        stage: Stage | None = None,
        hq_state: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[tables.Company]:
        stmt: Select[tuple[tables.Company]] = select(tables.Company)
        if name_contains:
            alvo = normalize_name(name_contains)
            stmt = stmt.where(tables.Company.normalized_name.contains(alvo))
        if stage is not None:
            stmt = stmt.where(tables.Company.stage == stage.value)
        if hq_state:
            stmt = stmt.where(tables.Company.hq_state == hq_state)
        stmt = stmt.order_by(tables.Company.name).limit(limit).offset(offset)
        return list(session.scalars(stmt))

    @staticmethod
    def to_pydantic(session: Session, row: tables.Company) -> CompanyProfile:
        """ORM → Pydantic, resolvendo os hashes contra as evidências da empresa."""
        by_hash = EvidenceRepository.map_by_hash(session, row.id)

        inferred: dict[str, Any] = {}
        for field_name, value_type in _INFERRED_FIELDS.items():
            stored = getattr(row, field_name)
            if stored:
                from radar.models.evidence import EvidenceBackedField

                inferred[field_name] = _load_with_refs(
                    EvidenceBackedField[value_type],  # type: ignore[valid-type]
                    stored,
                    by_hash,
                )

        return CompanyProfile(
            name=row.name,
            website=row.website,
            description=row.description,
            founded_year=row.founded_year,
            hq_city=row.hq_city,
            hq_state=row.hq_state,
            stage=Stage(row.stage),
            headcount_estimate=row.headcount_estimate,
            tech_signals=[_load_with_refs(TechSignal, s, by_hash) for s in row.tech_signals or []],
            founders=[_load_with_refs(Founder, f, by_hash) for f in row.founders or []],
            funding_rounds=[
                _load_with_refs(FundingRound, r, by_hash) for r in row.funding_rounds or []
            ],
            open_engineering_roles=list(row.open_engineering_roles or []),
            source_urls=list(row.source_urls or []),
            all_evidences=list(by_hash.values()),
            **inferred,
        )


# --------------------------------------------------------------------------- #
# Evidências
# --------------------------------------------------------------------------- #


class EvidenceRepository:
    """Escrita em lote com dedupe por `content_hash`."""

    @staticmethod
    def bulk_upsert(
        session: Session, company_id: UUID, evidences: Sequence[Evidence]
    ) -> list[tables.Evidence]:
        """Grava só o que ainda não existe.

        Dedupe em duas frentes porque a duplicata vem de duas origens: dentro do
        mesmo lote (o scraper acha o mesmo trecho no `/sobre` e no `/blog`) e
        entre execuções (re-coleta semanal do mesmo site). O `ON CONFLICT DO
        NOTHING` fecha a corrida entre empresas processadas em paralelo pelo
        fan-out do grafo, onde a checagem prévia não seria suficiente.
        """
        if not evidences:
            return []

        unique: dict[str, Evidence] = {ev.content_hash: ev for ev in evidences}
        rows = [
            {
                "company_id": company_id,
                "url": str(ev.url),
                "kind": ev.kind.value,
                "excerpt": ev.excerpt,
                "context": ev.context,
                "collected_at": ev.collected_at,
                "published_at": ev.published_at,
                "content_hash": digest,
            }
            for digest, ev in unique.items()
        ]

        dialect = session.get_bind().dialect.name
        if dialect in ("postgresql", "sqlite"):
            if dialect == "postgresql":
                from sqlalchemy.dialects.postgresql import insert
            else:
                from sqlalchemy.dialects.sqlite import insert

            stmt = insert(tables.Evidence).values(rows)
            session.execute(stmt.on_conflict_do_nothing(index_elements=["content_hash"]))
        else:  # pragma: no cover - dialeto sem UPSERT nativo
            existing = EvidenceRepository._existing_hashes(session, list(unique))
            novel = [r for r in rows if r["content_hash"] not in existing]
            if novel:
                session.execute(tables.Evidence.__table__.insert(), novel)

        session.flush()
        return EvidenceRepository.by_hashes(session, list(unique))

    @staticmethod
    def _existing_hashes(session: Session, hashes: Sequence[str]) -> set[str]:
        stmt = select(tables.Evidence.content_hash).where(
            tables.Evidence.content_hash.in_(hashes)
        )
        return set(session.scalars(stmt))

    @staticmethod
    def by_hashes(session: Session, hashes: Sequence[str]) -> list[tables.Evidence]:
        if not hashes:
            return []
        stmt = select(tables.Evidence).where(tables.Evidence.content_hash.in_(hashes))
        return list(session.scalars(stmt))

    @staticmethod
    def list_for_company(session: Session, company_id: UUID) -> list[tables.Evidence]:
        stmt = select(tables.Evidence).where(tables.Evidence.company_id == company_id)
        return list(session.scalars(stmt))

    @staticmethod
    def map_by_hash(session: Session, company_id: UUID) -> dict[str, Evidence]:
        """Índice hash → Evidence usado por toda reidratação de campo inferido."""
        return {
            row.content_hash: _evidence_to_pydantic(row)
            for row in EvidenceRepository.list_for_company(session, company_id)
        }

    @staticmethod
    def count(session: Session, company_id: UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(tables.Evidence)
            .where(tables.Evidence.company_id == company_id)
        )
        return int(session.scalar(stmt) or 0)


# --------------------------------------------------------------------------- #
# Classificação
# --------------------------------------------------------------------------- #


class ClassificationRepository:
    """Histórico de classificações — nunca sobrescreve, sempre acrescenta."""

    @staticmethod
    def add(
        session: Session, company_id: UUID, classification: Classification
    ) -> tables.Classification:
        row = tables.Classification(
            company_id=company_id,
            maturity=classification.maturity.value,
            confidence=classification.confidence,
            rationale=classification.rationale,
            model_used=classification.model_used,
            prompt_version=classification.prompt_version,
            evidence_hashes=[ev.content_hash for ev in classification.evidences],
        )
        session.add(row)
        session.flush()
        return row

    @staticmethod
    def latest_row(session: Session, company_id: UUID) -> tables.Classification | None:
        stmt = (
            select(tables.Classification)
            .where(tables.Classification.company_id == company_id)
            .order_by(tables.Classification.created_at.desc(), tables.Classification.id.desc())
            .limit(1)
        )
        return session.scalar(stmt)

    @staticmethod
    def latest(session: Session, company_id: UUID) -> Classification | None:
        row = ClassificationRepository.latest_row(session, company_id)
        if row is None:
            return None
        by_hash = EvidenceRepository.map_by_hash(session, company_id)
        return Classification(
            maturity=AIMaturity(row.maturity),
            confidence=row.confidence,
            rationale=row.rationale,
            model_used=row.model_used,
            prompt_version=row.prompt_version,
            evidences=[by_hash[h] for h in row.evidence_hashes or [] if h in by_hash],
        )

    @staticmethod
    def history(session: Session, company_id: UUID, limit: int = 20) -> list[tables.Classification]:
        stmt = (
            select(tables.Classification)
            .where(tables.Classification.company_id == company_id)
            .order_by(tables.Classification.created_at.desc())
            .limit(limit)
        )
        return list(session.scalars(stmt))


# --------------------------------------------------------------------------- #
# Score de defensibilidade
# --------------------------------------------------------------------------- #

#: Eixo → prefixo das colunas achatadas. Existe porque a fila de prioridade
#: precisa filtrar por eixo em SQL, e JSON não indexa de graça.
_AXIS_COLUMNS: dict[DefensibilityAxis, str] = {
    DefensibilityAxis.PROPRIETARY_DATA: "proprietary_data",
    DefensibilityAxis.WORKFLOW_DEPTH: "workflow_depth",
    DefensibilityAxis.STACK_OWNERSHIP: "stack_ownership",
    DefensibilityAxis.DISTRIBUTION: "distribution",
}


class ScoreRepository:
    """Séries temporais de score, TCO e priorização de uma mesma empresa."""

    @staticmethod
    def add(
        session: Session,
        company_id: UUID,
        score: DefensibilityScore,
        priority: PriorityAssessment | None = None,
    ) -> tables.DefensibilityScore:
        """Insere uma nova avaliação. Reavaliar não apaga o retrato anterior.

        O histórico é o que sustenta a conversa de acompanhamento: "seu eixo de
        stack subiu de 30 para 55 depois que vocês internalizaram a inferência".
        """
        row = tables.DefensibilityScore(
            company_id=company_id,
            weights_version=score.weights_version,
            total=score.total,
            commoditization_risk=score.commoditization_risk,
            global_confidence=score.global_confidence,
            weakest_axis=score.weakest_axis.value,
            axes_detail=[_dump_with_refs(a) for a in score.axes],
        )
        for axis in score.axes:
            prefix = _AXIS_COLUMNS[axis.axis]
            setattr(row, f"{prefix}_score", axis.score)
            setattr(row, f"{prefix}_confidence", axis.confidence)

        session.add(row)
        session.flush()

        for tco in score.tco:
            session.add(
                tables.TCOEstimate(
                    score_id=row.id,
                    company_id=company_id,
                    scenario=tco.scenario.value,
                    monthly_tokens_estimate=tco.monthly_tokens_estimate,
                    current_provider=tco.current_provider,
                    current_monthly_usd=tco.current_monthly_usd,
                    nvidia_stack_monthly_usd=tco.nvidia_stack_monthly_usd,
                    gpu_assumption=tco.gpu_assumption,
                    assumptions=list(tco.assumptions),
                    weights_version=tco.weights_version,
                )
            )

        if priority is not None:
            session.add(
                tables.PriorityAssessment(
                    company_id=company_id,
                    score_id=row.id,
                    bucket=priority.bucket.value,
                    urgency=priority.urgency,
                    capacity_to_act=priority.capacity_to_act,
                    capacity_rationale=priority.capacity_rationale,
                    recommended_next_step=priority.recommended_next_step,
                )
            )

        session.flush()
        return row

    @staticmethod
    def latest_row(session: Session, company_id: UUID) -> tables.DefensibilityScore | None:
        """A avaliação mais recente.

        Desempate por `id` além de `created_at`: duas execuções no mesmo segundo
        (fan-out do grafo) empatariam no timestamp, e a fila precisa ser estável.
        """
        stmt = (
            select(tables.DefensibilityScore)
            .where(tables.DefensibilityScore.company_id == company_id)
            .order_by(
                tables.DefensibilityScore.created_at.desc(),
                tables.DefensibilityScore.id.desc(),
            )
            .limit(1)
        )
        return session.scalar(stmt)

    @staticmethod
    def latest(session: Session, company_id: UUID) -> DefensibilityScore | None:
        row = ScoreRepository.latest_row(session, company_id)
        return None if row is None else ScoreRepository.to_pydantic(session, row)

    @staticmethod
    def history(
        session: Session, company_id: UUID, limit: int = 20
    ) -> list[tables.DefensibilityScore]:
        stmt = (
            select(tables.DefensibilityScore)
            .where(tables.DefensibilityScore.company_id == company_id)
            .order_by(
                tables.DefensibilityScore.created_at.desc(),
                tables.DefensibilityScore.id.desc(),
            )
            .limit(limit)
        )
        return list(session.scalars(stmt))

    @staticmethod
    def latest_priority(session: Session, company_id: UUID) -> PriorityAssessment | None:
        stmt = (
            select(tables.PriorityAssessment)
            .where(tables.PriorityAssessment.company_id == company_id)
            .order_by(
                tables.PriorityAssessment.created_at.desc(),
                tables.PriorityAssessment.id.desc(),
            )
            .limit(1)
        )
        row = session.scalar(stmt)
        if row is None:
            return None
        return PriorityAssessment(
            bucket=PriorityBucket(row.bucket),
            urgency=row.urgency,
            capacity_to_act=row.capacity_to_act,
            capacity_rationale=row.capacity_rationale,
            recommended_next_step=row.recommended_next_step,
        )

    @staticmethod
    def to_pydantic(session: Session, row: tables.DefensibilityScore) -> DefensibilityScore:
        by_hash = EvidenceRepository.map_by_hash(session, row.company_id)
        company = session.get(tables.Company, row.company_id)
        return DefensibilityScore(
            company_name=company.name if company else "",
            axes=[_load_with_refs(AxisScore, a, by_hash) for a in row.axes_detail or []],
            tco=[
                TCOEstimate.model_validate(
                    {
                        "scenario": t.scenario,
                        "monthly_tokens_estimate": t.monthly_tokens_estimate,
                        "current_provider": t.current_provider,
                        "current_monthly_usd": t.current_monthly_usd,
                        "nvidia_stack_monthly_usd": t.nvidia_stack_monthly_usd,
                        "gpu_assumption": t.gpu_assumption,
                        "assumptions": list(t.assumptions or []),
                        "weights_version": t.weights_version,
                    }
                )
                for t in sorted(row.tco_estimates, key=lambda t: t.scenario)
            ],
            weights_version=row.weights_version,
        )


# --------------------------------------------------------------------------- #
# Recomendações
# --------------------------------------------------------------------------- #


class RecommendationRepository:
    @staticmethod
    def add_many(
        session: Session,
        company_id: UUID,
        recommendations: Sequence[Recommendation],
        briefing_id: UUID | None = None,
    ) -> list[tables.Recommendation]:
        """Grava recomendações com suas citações da KB e evidências da startup.

        A ordem é preservada em `position` porque prioridade de leitura é parte
        da recomendação: o gerente lê a primeira e talvez a segunda.
        """
        existentes = EvidenceRepository.list_for_company(session, company_id)
        by_hash = {row.content_hash: row for row in existentes}
        created: list[tables.Recommendation] = []

        for index, rec in enumerate(recommendations):
            row = tables.Recommendation(
                company_id=company_id,
                briefing_id=briefing_id,
                position=index,
                technology=rec.technology,
                addresses_axis=rec.addresses_axis.value,
                technical_rationale=rec.technical_rationale,
                business_rationale=rec.business_rationale,
                priority=rec.priority.value,
                complexity=rec.complexity.value,
                next_action=rec.next_action,
            )
            session.add(row)
            session.flush()

            for pos, chunk in enumerate(rec.kb_citations):
                session.add(
                    tables.RecommendationKBCitation(
                        recommendation_id=row.id,
                        position=pos,
                        text=chunk.text,
                        source_url=str(chunk.source_url),
                        source_title=chunk.source_title,
                        technology=chunk.technology,
                        dense_score=chunk.dense_score,
                        bm25_score=chunk.bm25_score,
                        rrf_score=chunk.rrf_score,
                        rerank_score=chunk.rerank_score,
                    )
                )

            # Evidência ainda não gravada é gravada agora: a recomendação não pode
            # ficar sem o sinal que a motivou só porque o extrator não a listou.
            missing = [ev for ev in rec.company_evidences if ev.content_hash not in by_hash]
            if missing:
                for saved in EvidenceRepository.bulk_upsert(session, company_id, missing):
                    by_hash[saved.content_hash] = saved

            for ev in rec.company_evidences:
                target = by_hash.get(ev.content_hash)
                if target is not None:
                    session.add(
                        tables.RecommendationEvidence(
                            recommendation_id=row.id, evidence_id=target.id
                        )
                    )

            created.append(row)

        session.flush()
        return created

    @staticmethod
    def list_for_company(session: Session, company_id: UUID) -> list[tables.Recommendation]:
        stmt = (
            select(tables.Recommendation)
            .where(tables.Recommendation.company_id == company_id)
            .order_by(tables.Recommendation.created_at.desc(), tables.Recommendation.position)
        )
        return list(session.scalars(stmt))

    @staticmethod
    def to_pydantic(row: tables.Recommendation) -> Recommendation:
        return Recommendation(
            technology=row.technology,
            addresses_axis=DefensibilityAxis(row.addresses_axis),
            technical_rationale=row.technical_rationale,
            business_rationale=row.business_rationale,
            priority=row.priority,  # type: ignore[arg-type]
            complexity=row.complexity,  # type: ignore[arg-type]
            next_action=row.next_action,
            kb_citations=[
                RetrievedChunk(
                    text=c.text,
                    source_url=c.source_url,
                    source_title=c.source_title,
                    technology=c.technology,
                    dense_score=c.dense_score,
                    bm25_score=c.bm25_score,
                    rrf_score=c.rrf_score,
                    rerank_score=c.rerank_score,
                )
                for c in row.kb_citations
            ],
            company_evidences=[_evidence_to_pydantic(link.evidence) for link in row.evidence_links],
        )


# --------------------------------------------------------------------------- #
# Briefings
# --------------------------------------------------------------------------- #


class BriefingRepository:
    @staticmethod
    def save(
        session: Session,
        company_id: UUID,
        briefing: Briefing,
        score_id: UUID | None = None,
    ) -> tables.Briefing:
        """Persiste o briefing e reamarra suas recomendações a ele.

        O score é gravado junto (quando ainda não existir linha) porque um
        briefing sem o score que o gerou é um relatório sem lastro — e o
        `weights_version` daquele score é o que permite reproduzi-lo depois.
        """
        if score_id is None:
            score_row = ScoreRepository.add(
                session, company_id, briefing.defensibility, briefing.priority
            )
            score_id = score_row.id

        row = tables.Briefing(
            company_id=company_id,
            score_id=score_id,
            company_name=briefing.company_name,
            maturity=briefing.maturity.value,
            executive_summary=briefing.executive_summary,
            moat_plan=briefing.moat_plan,
            inception_fit=briefing.inception_fit,
            conversation_starters=list(briefing.conversation_starters),
            caveats=list(briefing.caveats),
            markdown=briefing.markdown,
            generated_at=briefing.generated_at,
        )
        session.add(row)
        session.flush()

        RecommendationRepository.add_many(
            session, company_id, briefing.recommendations, briefing_id=row.id
        )
        session.flush()
        return row

    @staticmethod
    def latest_row(session: Session, company_id: UUID) -> tables.Briefing | None:
        stmt = (
            select(tables.Briefing)
            .where(tables.Briefing.company_id == company_id)
            .order_by(tables.Briefing.generated_at.desc(), tables.Briefing.id.desc())
            .limit(1)
        )
        return session.scalar(stmt)

    @staticmethod
    def latest(session: Session, company_id: UUID) -> Briefing | None:
        row = BriefingRepository.latest_row(session, company_id)
        if row is None:
            return None

        score_row = (
            session.get(tables.DefensibilityScore, row.score_id) if row.score_id else None
        )
        priority = ScoreRepository.latest_priority(session, company_id)
        if score_row is None or priority is None:
            # `Briefing` exige score e priorização; devolver um objeto meio montado
            # seria pior que devolver nada — a UI mostraria um relatório sem lastro.
            return None
        score = ScoreRepository.to_pydantic(session, score_row)

        return Briefing(
            company_name=row.company_name,
            generated_at=_utc(row.generated_at) or datetime.now(UTC),
            executive_summary=row.executive_summary,
            maturity=AIMaturity(row.maturity),
            defensibility=score,
            priority=priority,
            recommendations=[
                RecommendationRepository.to_pydantic(r) for r in row.recommendations
            ],
            moat_plan=row.moat_plan,
            conversation_starters=list(row.conversation_starters or []),
            inception_fit=row.inception_fit,
            caveats=list(row.caveats or []),
            markdown=row.markdown,
        )


# --------------------------------------------------------------------------- #
# Fila de prioridade (leitura para o dashboard)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class QueueItem:
    """Linha da fila de trabalho do gerente, já achatada para a UI.

    Não é um modelo Pydantic de domínio: é um *read model*. Devolver
    `CompanyProfile` inteiro para pintar uma lista de 200 startups carregaria
    todas as evidências de todas elas para exibir quatro números.
    """

    company_id: UUID
    company_name: str
    website: str | None
    stage: str
    total: float
    commoditization_risk: float
    global_confidence: float
    weakest_axis: str
    weights_version: str
    scored_at: datetime
    bucket: str | None
    urgency: float | None


def priority_queue(
    session: Session,
    *,
    bucket: PriorityBucket | None = None,
    min_risk: float | None = None,
    min_confidence: float | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[QueueItem]:
    """Empresas ordenadas por urgência, cada uma com seu score mais recente.

    A janela `row_number()` resolve o "mais recente por empresa" em uma passada,
    sem N+1 — importante porque esta é a query da tela inicial do dashboard.

    Ordenar por urgência e cair para risco quando ela não existe é deliberado:
    empresa avaliada mas ainda não priorizada precisa aparecer na fila, não
    sumir dela. `min_confidence` deixa o cliente esconder o que é ruído — score
    baixo com pouca evidência é "não sabemos", nunca "startup fraca".
    """
    scores = tables.DefensibilityScore
    ranked = (
        select(
            scores.id.label("score_id"),
            scores.company_id,
            scores.total,
            scores.commoditization_risk,
            scores.global_confidence,
            scores.weakest_axis,
            scores.weights_version,
            scores.created_at,
            func.row_number()
            .over(
                partition_by=scores.company_id,
                order_by=(scores.created_at.desc(), scores.id.desc()),
            )
            .label("rank"),
        )
        .subquery()
    )

    prio = tables.PriorityAssessment
    stmt = (
        select(
            tables.Company.id,
            tables.Company.name,
            tables.Company.website,
            tables.Company.stage,
            ranked.c.total,
            ranked.c.commoditization_risk,
            ranked.c.global_confidence,
            ranked.c.weakest_axis,
            ranked.c.weights_version,
            ranked.c.created_at,
            prio.bucket,
            prio.urgency,
        )
        .join(ranked, ranked.c.company_id == tables.Company.id)
        .outerjoin(prio, prio.score_id == ranked.c.score_id)
        .where(ranked.c.rank == 1)
    )

    if bucket is not None:
        stmt = stmt.where(prio.bucket == bucket.value)
    if min_risk is not None:
        stmt = stmt.where(ranked.c.commoditization_risk >= min_risk)
    if min_confidence is not None:
        stmt = stmt.where(ranked.c.global_confidence >= min_confidence)

    # coalesce em vez de NULLS LAST: portátil entre Postgres e SQLite.
    stmt = stmt.order_by(
        func.coalesce(prio.urgency, ranked.c.commoditization_risk).desc(),
        ranked.c.created_at.desc(),
    ).limit(limit).offset(offset)

    return [
        QueueItem(
            company_id=r[0],
            company_name=r[1],
            website=r[2],
            stage=r[3],
            total=r[4],
            commoditization_risk=r[5],
            global_confidence=r[6],
            weakest_axis=r[7],
            weights_version=r[8],
            scored_at=_utc(r[9]) or datetime.now(UTC),
            bucket=r[10],
            urgency=r[11],
        )
        for r in session.execute(stmt).all()
    ]


__all__ = [
    "BriefingRepository",
    "ClassificationRepository",
    "CompanyRepository",
    "EvidenceRepository",
    "QueueItem",
    "RecommendationRepository",
    "ScoreRepository",
    "collect_evidences",
    "normalize_name",
    "normalize_website",
    "priority_queue",
]
