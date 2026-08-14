"""Briefings: consulta, regeração e exportação.

`POST /companies/{id}/briefing` reexecuta **apenas** o nó de briefing sobre o
diagnóstico já gravado. Não re-raspa, não re-pontua, não re-recomenda. É o
caminho para quando o texto saiu ruim, ou quando a versão do prompt mudou e vale
reescrever — sem gastar de novo a coleta e as quatro chamadas de raciocínio que
produziram o score.

A exportação é markdown, não PDF. O plano previa PDF; as bibliotecas maduras de
HTML→PDF (WeasyPrint, wkhtmltopdf) exigem bibliotecas de sistema — cairo, pango —
que esta máquina não instala sem sudo. Markdown é fiel ao que foi gerado, versiona
bem, abre em qualquer lugar e imprime pelo navegador. Registrado como desvio
consciente, não como esquecimento.
"""

from __future__ import annotations

from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from api.deps import NodeDepsDep, SessionDep
from api.schemas import BriefingResponse
from radar.graph.nodes import make_write_briefing
from radar.models.recommendation import Briefing
from radar.persistence.db import session_scope
from radar.persistence.repositories import (
    BriefingRepository,
    ClassificationRepository,
    CompanyRepository,
    RecommendationRepository,
    ScoreRepository,
)

log = structlog.get_logger(__name__)

router = APIRouter(tags=["briefings"])


def _briefing_mais_recente(session: Session, company_id: UUID) -> Briefing:
    briefing = BriefingRepository.latest(session, company_id)
    if briefing is None:
        raise HTTPException(
            status_code=404,
            detail="Nenhum briefing gerado para esta empresa. Use POST /companies/"
            f"{company_id}/briefing para gerar.",
        )
    return briefing


@router.get("/companies/{company_id}/briefing", response_model=BriefingResponse)
async def obter_briefing(
    company_id: UUID, session: SessionDep
) -> BriefingResponse:
    row = BriefingRepository.latest_row(session, company_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Nenhum briefing para esta empresa.")
    return BriefingResponse(
        briefing_id=row.id,
        company_id=company_id,
        briefing=_briefing_mais_recente(session, company_id),
        markdown_url=f"/briefings/{row.id}.md",
    )


@router.post("/companies/{company_id}/briefing", response_model=BriefingResponse)
async def gerar_briefing(
    company_id: UUID,
    session: SessionDep,
    deps: NodeDepsDep,
) -> BriefingResponse:
    """Reescreve o briefing a partir do diagnóstico gravado.

    Exige score: sem ele não há o que relatar, e gerar texto sobre uma empresa
    apenas classificada produziria exatamente a prosa sem lastro que o projeto
    inteiro existe para evitar.
    """
    profile = CompanyRepository.get(session, company_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Empresa não encontrada.")

    score = ScoreRepository.latest(session, company_id)
    priority = ScoreRepository.latest_priority(session, company_id)
    if score is None or priority is None:
        raise HTTPException(
            status_code=409,
            detail="Empresa sem Defensibility Score gravado. Rode uma busca antes de "
            "pedir o briefing — sem score não há diagnóstico a relatar.",
        )

    recomendacoes = [
        RecommendationRepository.to_pydantic(row)
        for row in RecommendationRepository.list_for_company(session, company_id)
    ]

    # O nó do grafo é reaproveitado inteiro, com o mesmo prompt versionado e o
    # mesmo guardrail. Reescrever a montagem do briefing aqui criaria uma segunda
    # versão da regra que divergiria da primeira.
    write_briefing = make_write_briefing(deps)
    resultado = await write_briefing(
        {
            "company_name": profile.name,
            "profile": profile,
            "classification": ClassificationRepository.latest(session, company_id),
            "defensibility": score,
            "priority": priority,
            "recommendations": recomendacoes,
            "validation_notes": [],
            "scrape_attempts": 0,
        }
    )

    briefing = resultado.get("briefing")
    if briefing is None:
        falhas = resultado.get("failures") or [{}]
        raise HTTPException(
            status_code=502,
            detail=f"Geração do briefing falhou: {falhas[0].get('error', 'erro desconhecido')}",
        )

    with session_scope() as escrita:
        row = BriefingRepository.save(escrita, company_id, briefing)
        briefing_id = row.id

    log.info("briefing_regerado", empresa=profile.name, briefing=str(briefing_id))
    return BriefingResponse(
        briefing_id=briefing_id,
        company_id=company_id,
        briefing=briefing,
        markdown_url=f"/briefings/{briefing_id}.md",
    )


@router.get("/briefings/{briefing_id}.md", response_class=PlainTextResponse)
async def exportar_markdown(
    briefing_id: UUID, session: SessionDep
) -> PlainTextResponse:
    """Markdown pronto para download. Ver o desvio de PDF no topo do módulo."""
    from radar.persistence import tables

    row = session.get(tables.Briefing, briefing_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Briefing não encontrado.")
    if not row.markdown:
        raise HTTPException(
            status_code=409, detail="Briefing gravado sem markdown renderizado."
        )

    nome = row.company_name.replace(" ", "-").lower()
    return PlainTextResponse(
        row.markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="briefing-{nome}.md"'},
    )
