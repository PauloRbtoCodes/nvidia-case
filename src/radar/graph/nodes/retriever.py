"""NVIDIA RAG + Reranker: busca a evidência que fundamenta a recomendação.

A consulta não é "quais tecnologias NVIDIA existem". É "o que resolve **este**
gap, para **esta** startup" — e por isso a query é montada a partir do eixo
fraco, do setor e dos sinais técnicos observados, com o gate determinístico
(`candidate_technologies`) restringindo o filtro de payload antes da busca.

Sem essa restrição, a busca por "reduzir custo de inferência" traz documentação
de Omniverse com aparência de resultado legítimo: o índice é grande e a
similaridade cosseno não sabe que a pergunta era sobre servir modelos.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Sequence
from typing import Any

import structlog

from radar.graph.nodes.base import falha, node_guard
from radar.graph.nodes.deps import NodeDeps
from radar.graph.state import CompanyState
from radar.models.recommendation import RetrievedChunk
from radar.models.scoring import DefensibilityAxis, DefensibilityScore
from radar.rag.rerank import RerankUnavailableError

log = structlog.get_logger(__name__)

#: Como se pergunta à KB sobre cada eixo. Frase em linguagem natural e não uma
#: lista de produtos: os cards manuais da KB são escritos em torno do *problema*
#: ("quando uma startup precisa de inferência própria"), e é com eles que a busca
#: densa precisa casar. Buscar pelo nome do produto é trabalho do BM25, que já
#: roda em paralelo na fusão híbrida.
AXIS_QUERIES: dict[DefensibilityAxis, str] = {
    DefensibilityAxis.PROPRIETARY_DATA: (
        "como construir e curar dataset proprietário, pipeline de dados e "
        "customização de modelo com dado do cliente"
    ),
    DefensibilityAxis.WORKFLOW_DEPTH: (
        "agentes que executam ações em sistemas do cliente, guardrails de segurança "
        "e blueprints para workflows corporativos"
    ),
    DefensibilityAxis.STACK_OWNERSHIP: (
        "reduzir custo e latência de inferência com self-hosting, otimização de "
        "modelos e servir LLM em produção"
    ),
    DefensibilityAxis.DISTRIBUTION: (
        "programa Inception: benefícios, créditos, apoio de go-to-market e conexão "
        "com investidores para startups"
    ),
}

#: Candidatos trazidos da fusão híbrida antes do rerank. O reranker corta para
#: `rerank_top_n`; alimentá-lo com pouco desperdiça a etapa que mais melhora
#: precisão no pipeline inteiro.
CANDIDATOS_POR_EIXO = 20

#: Eixos consultados por empresa. Dois cobrem o gap principal e o secundário sem
#: transformar o briefing num catálogo — recomendação demais não prioriza nada.
MAX_EIXOS = 2


def _contexto_da_empresa(state: CompanyState) -> str:
    """Setor e stack observada, para a query não ser genérica."""
    profile = state.get("profile")
    if profile is None:
        return ""
    partes: list[str] = []
    if profile.sector is not None:
        partes.append(str(profile.sector.value))
    if profile.inference_provider is not None:
        partes.append(str(profile.inference_provider.value).replace("_", " "))
    partes.extend(sinal.technology for sinal in profile.tech_signals[:5])
    return " ".join(partes)


def _eixos_a_consultar(score: DefensibilityScore) -> list[DefensibilityAxis]:
    """Os gaps acionáveis; na falta deles, o eixo mais fraco.

    O fallback importa: uma empresa sem nenhum gap *acionável* (evidência fina em
    tudo) ainda merece uma leitura técnica. O briefing dirá nos `caveats` que a
    recomendação repousa em evidência limitada — que é diferente de não haver
    briefing.
    """
    acionaveis = score.actionable_gaps[:MAX_EIXOS]
    return acionaveis or [score.weakest_axis]


def _dedupe(chunks: Sequence[RetrievedChunk]) -> list[RetrievedChunk]:
    vistos: set[tuple[str, str]] = set()
    unicos: list[RetrievedChunk] = []
    for chunk in chunks:
        chave = (str(chunk.source_url), chunk.text[:120])
        if chave in vistos:
            continue
        vistos.add(chave)
        unicos.append(chunk)
    return unicos


def make_retrieve_kb(
    deps: NodeDeps,
) -> Callable[[CompanyState], Coroutine[Any, Any, dict[str, Any]]]:
    @node_guard("nvidia_rag")
    async def retrieve_kb(state: CompanyState) -> dict[str, Any]:
        score = state.get("defensibility")
        if score is None:
            raise ValueError("RAG sem score: não há gap para consultar.")
        if deps.retriever is None:
            raise RuntimeError(
                "Base de conhecimento indisponível (Qdrant ou índice BM25 ausente). "
                "Rode `make ingest` antes de executar o grafo."
            )

        candidatas = state.get("candidate_technologies") or []
        contexto = _contexto_da_empresa(state)
        falhas: list[Any] = []
        recuperados: list[RetrievedChunk] = []

        for eixo in _eixos_a_consultar(score):
            query = f"{AXIS_QUERIES[eixo]} {contexto}".strip()
            # Embedding da query e busca no Qdrant são chamadas de rede síncronas:
            # fora da thread, travariam o event loop e serializariam o fan-out.
            brutos = await asyncio.to_thread(
                deps.retriever.retrieve_chunks,
                query,
                top_k=CANDIDATOS_POR_EIXO,
                # O gate determinístico entra como filtro de payload: o RAG só vê
                # o universo que o score já autorizou.
                technology=candidatas or None,
            )
            if not brutos:
                continue

            reranker = deps.reranker
            if reranker is None:
                recuperados.extend(brutos)
                continue
            try:
                recuperados.extend(await asyncio.to_thread(reranker.rerank, query, brutos))
            except RerankUnavailableError as exc:
                # Seguir com a ordem do RRF é aceitável e a perda é de precisão,
                # não de fundamento — mas precisa aparecer no relatório, senão o
                # briefing sai como se tivesse passado pelo cross-encoder.
                falhas.append(
                    falha(
                        "nvidia_rag",
                        f"rerank indisponível, ordem do RRF mantida: {exc}",
                        company=score.company_name,
                        recoverable=True,
                    )
                )
                recuperados.extend(brutos[: len(brutos) // 2 or 1])

        chunks = _dedupe(recuperados)
        log.info(
            "rag",
            empresa=score.company_name,
            eixos=[e.value for e in _eixos_a_consultar(score)],
            candidatas=len(candidatas),
            chunks=len(chunks),
        )
        return {"rag_chunks": chunks, "failures": falhas}

    return retrieve_kb
