"""`POST /searches` e o stream de progresso.

O grafo roda em segundo plano e o progresso sai por SSE. A tradução de eventos
do LangGraph para eventos do stream acontece aqui, e é onde mora a decisão mais
importante deste arquivo: **o que é progresso legível para um humano**.

`astream` do LangGraph emite uma atualização por nó concluído, com o estado
parcial. Repassar isso cru significaria mandar `CompanyProfile` inteiro, com
todas as evidências, a cada passo — dezenas de kilobytes por evento, para uma
barra de progresso. O tradutor extrai o que a tela usa: qual nó, de qual
empresa, e uma frase de detalhe.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request, status
from sse_starlette.sse import EventSourceResponse

from api.deps import NodeDepsDep, RegistryDep
from api.runs import RunEvent, RunStatus, SearchRun
from api.schemas import SearchRequest, SearchResponse
from radar.graph.build import build_radar_graph
from radar.graph.nodes import NodeDeps

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/searches", tags=["searches"])

#: Intervalo do keep-alive do SSE. Proxies e balanceadores derrubam conexão
#: ociosa, e uma empresa demorada produz silêncio longo o bastante para isso.
PING_SEGUNDOS = 15

#: Rótulos legíveis por nó. O nome técnico do nó não diz nada a quem olha a tela.
ROTULOS: dict[str, str] = {
    "plan": "Planejando as buscas",
    "discover": "Descobrindo empresas",
    "process_company": "Diagnosticando empresa",
    "consolidate": "Consolidando a fila",
    "collect": "Coletando páginas",
    "extract": "Extraindo perfil",
    "validate": "Auditando evidências",
    "classify": "Classificando maturidade",
    "score": "Pontuando defensibilidade",
    "compare": "Comparando com a execução anterior",
    "rag": "Buscando na base NVIDIA",
    "recommend": "Redigindo recomendações",
    "briefing": "Escrevendo o briefing",
}


def _detalhe(node: str, update: dict[str, Any]) -> str | None:
    """Uma frase sobre o que aquele nó produziu.

    Cada ramo aqui existe porque a informação útil de um nó não é a mesma dos
    outros: do discover interessa quantas empresas entraram, do briefing
    interessa o nome de quem ficou pronto.
    """
    if not isinstance(update, dict):
        return None

    if node == "discover":
        return f"{len(update.get('discovered') or [])} empresas candidatas"
    if node == "consolidate":
        fila = update.get("queue") or []
        return f"fila com {len(fila)} empresa(s)"
    if node == "process_company":
        briefings = update.get("briefings") or []
        if briefings:
            return f"briefing pronto: {briefings[0].company_name}"
        descartadas = update.get("skipped") or []
        if descartadas:
            item = descartadas[0]
            return f"{item.get('company')} descartada — {item.get('reason')}"
    return None


def _empresa(update: dict[str, Any]) -> str | None:
    if not isinstance(update, dict):
        return None
    for briefing in update.get("briefings") or []:
        return str(briefing.company_name)
    for item in update.get("skipped") or []:
        return str(item.get("company"))
    return None


async def executar_busca(run: SearchRun, deps: NodeDeps) -> None:
    """Roda o grafo empurrando progresso para o stream da execução."""
    run.status = RunStatus.EXECUTANDO
    run.emit(RunEvent(type="status", detail=RunStatus.EXECUTANDO.value))

    grafo = build_radar_graph(deps)
    estado: dict[str, Any] = {}

    try:
        async for passo in grafo.astream(
            {"query": run.query, "max_companies": run.max_companies}
        ):
            for node, update in passo.items():
                run.emit(
                    RunEvent(
                        type="node",
                        node=ROTULOS.get(node, node),
                        company=_empresa(update),
                        detail=_detalhe(node, update),
                    )
                )
                if isinstance(update, dict):
                    _acumular(estado, update)

        run.finish(result=estado)
        _persistir_em_segundo_plano(run, estado)

    except asyncio.CancelledError:
        run.finish(error="execução cancelada")
        raise
    except Exception as exc:  # noqa: BLE001 - a falha vira estado da execução
        log.warning("busca_falhou", run=run.id, erro=str(exc)[:300])
        run.emit(RunEvent(type="error", detail=str(exc)[:300]))
        run.finish(error=str(exc)[:500])


def _acumular(estado: dict[str, Any], update: dict[str, Any]) -> None:
    """Reconstrói o estado final a partir das atualizações por nó.

    `astream` no modo padrão entrega o *delta* de cada nó, não o estado inteiro.
    As chaves com reducer `operator.add` no `RadarState` precisam ser
    concatenadas aqui pelo mesmo critério, senão o resultado final teria só o
    que o último nó produziu.
    """
    for chave, valor in update.items():
        if isinstance(valor, list) and isinstance(estado.get(chave), list):
            estado[chave] = [*estado[chave], *valor]
        else:
            estado[chave] = valor


def _persistir_em_segundo_plano(run: SearchRun, estado: dict[str, Any]) -> None:
    """Grava o lote sem bloquear o fim da execução.

    A persistência é síncrona (SQLAlchemy) e vai para uma thread. Falha aqui não
    invalida a execução: os briefings já existem em memória e já foram
    entregues pelo stream — o que se perde é a consulta futura, não o resultado.
    """
    from radar.persistence.sink import persist_batch

    async def _rodar() -> None:
        try:
            relatorio = await asyncio.to_thread(
                persist_batch, estado.get("company_results") or []
            )
            if relatorio.unavailable:
                log.warning("lote_nao_persistido", run=run.id, motivo=relatorio.unavailable)
        except Exception as exc:  # noqa: BLE001
            log.warning("persistencia_falhou", run=run.id, erro=str(exc)[:300])

    run.track(asyncio.get_running_loop().create_task(_rodar()))


def _resposta(run: SearchRun) -> SearchResponse:
    resultado = run.result or {}
    return SearchResponse(
        id=run.id,
        query=run.query,
        status=run.status.value,
        created_at=run.created_at,
        finished_at=run.finished_at,
        error=run.error,
        companies_found=len(resultado.get("discovered") or []),
        briefings_ready=len(resultado.get("briefings") or []),
        skipped=len(resultado.get("skipped") or []),
        failures=len(resultado.get("failures") or []),
        queue=list(resultado.get("queue") or []),
        stream_url=f"/searches/{run.id}/stream",
    )


@router.post("", response_model=SearchResponse, status_code=status.HTTP_202_ACCEPTED)
async def criar_busca(
    payload: SearchRequest,
    registry: RegistryDep,
    deps: NodeDepsDep,
) -> SearchResponse:
    """Dispara o grafo e devolve imediatamente o id da execução.

    202, não 201: nada foi criado ainda. O recurso que interessa — os briefings —
    só existe quando a execução termina, e o cliente acompanha pelo stream.
    """
    run = registry.create(payload.query, payload.max_companies)
    run.track(asyncio.create_task(executar_busca(run, deps)))
    log.info("busca_criada", run=run.id, query=payload.query[:120])
    return _resposta(run)


@router.get("", response_model=list[SearchResponse])
async def listar_buscas(registry: RegistryDep) -> list[SearchResponse]:
    return [_resposta(run) for run in registry.list()]


@router.get("/{run_id}", response_model=SearchResponse)
async def obter_busca(
    run_id: str, registry: RegistryDep
) -> SearchResponse:
    run = registry.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Execução não encontrada.")
    return _resposta(run)


@router.get("/{run_id}/stream")
async def transmitir_progresso(
    run_id: str,
    request: Request,
    registry: RegistryDep,
) -> EventSourceResponse:
    """Progresso nó a nó, em Server-Sent Events.

    Quem conecta depois do início recebe o histórico antes dos eventos vivos —
    ver `SearchRun.subscribe`. Sem isso, abrir a tela dois segundos após o POST
    mostraria um progresso que começa no meio.
    """
    run = registry.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Execução não encontrada.")

    async def gerar():
        async for evento in run.subscribe():
            if await request.is_disconnected():
                break
            yield {
                "event": evento.type,
                "data": json.dumps(evento.to_dict(), ensure_ascii=False),
            }

    return EventSourceResponse(gerar(), ping=PING_SEGUNDOS)


@router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancelar_busca(run_id: str, registry: RegistryDep) -> None:
    run = registry.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Execução não encontrada.")
    run.cancel()
