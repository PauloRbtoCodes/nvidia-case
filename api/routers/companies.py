"""Leitura do que o grafo já produziu: fila de prioridade e perfil da empresa.

Estes endpoints não disparam nada. Lêem o Postgres, que é o que permite o
dashboard abrir instantaneamente com o resultado das execuções anteriores em vez
de exigir uma busca nova a cada visita.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Query

from api.deps import SessionDep
from api.schemas import CompanyDetailResponse, QueueItemResponse
from radar.models.scoring import PriorityBucket
from radar.persistence.repositories import (
    BriefingRepository,
    ClassificationRepository,
    CompanyRepository,
    RecommendationRepository,
    ScoreRepository,
    priority_queue,
)

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/companies", tags=["companies"])


@router.get("", response_model=list[QueueItemResponse])
async def listar_fila(
    session: SessionDep,
    bucket: Annotated[
        PriorityBucket | None, Query(description="Filtra pela ação da semana.")
    ] = None,
    min_risk: Annotated[
        float | None, Query(ge=0, le=100, description="Risco de comoditização mínimo.")
    ] = None,
    min_confidence: Annotated[
        float | None,
        Query(
            ge=0,
            le=1,
            description="Esconde diagnósticos com pouca evidência. Score baixo com "
            "confiança baixa é 'não sabemos', nunca 'startup fraca'.",
        ),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[QueueItemResponse]:
    """A fila de trabalho do gerente, ordenada por urgência."""
    itens = priority_queue(
        session,
        bucket=bucket,
        min_risk=min_risk,
        min_confidence=min_confidence,
        limit=limit,
        offset=offset,
    )
    # O gatilho ao lado da empresa: só as que mudaram desde a última execução.
    deltas = ScoreRepository.deltas_for(session, [item.company_id for item in itens])
    return [
        QueueItemResponse.from_queue_item(item, delta=deltas.get(item.company_id))
        for item in itens
    ]


@router.get("/{company_id}", response_model=CompanyDetailResponse)
async def obter_empresa(company_id: UUID, session: SessionDep) -> CompanyDetailResponse:
    """Perfil completo com o diagnóstico mais recente.

    Devolve os objetos de domínio inteiros, com as evidências dentro de cada
    `AxisScore` — é o que permite o radar de eixos ter trecho e URL clicáveis, e
    é o que separa a tela de uma ferramenta de trabalho de uma demo.
    """
    profile = CompanyRepository.get(session, company_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Empresa não encontrada.")

    score = ScoreRepository.latest(session, company_id)
    recomendacoes = [
        RecommendationRepository.to_pydantic(row)
        for row in RecommendationRepository.list_for_company(session, company_id)
    ]

    return CompanyDetailResponse(
        company_id=company_id,
        profile=profile,
        classification=ClassificationRepository.latest(session, company_id),
        defensibility=score,
        priority=ScoreRepository.latest_priority(session, company_id),
        recommendations=recomendacoes,
        has_briefing=BriefingRepository.latest_row(session, company_id) is not None,
        delta=ScoreRepository.delta(session, company_id),
    )
