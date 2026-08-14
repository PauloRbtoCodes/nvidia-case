"""Aplicação FastAPI. Ponto de entrada de `make api`.

Duas decisões de montagem:

**As dependências do grafo são construídas no lifespan, não na primeira
requisição.** `build_deps` carrega o índice BM25 do disco e abre cliente do
Qdrant; fazer isso durante o primeiro `POST /searches` faria a requisição
inaugural pagar segundos de inicialização e, pior, mascararia um erro de
infraestrutura como erro daquela busca.

**A montagem não falha por infra ausente.** `build_deps` devolve `None` para o
que não subiu e cada nó reporta a lacuna. Uma API que se recusa a iniciar porque
o Qdrant não está no ar impediria até `GET /companies`, que só precisa do
Postgres — e impediria o `/health` que diria o que está faltando.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

from api import deps
from api.routers import briefings, companies, searches
from api.schemas import HealthResponse
from radar.config import get_settings

log = structlog.get_logger(__name__)

#: O front do projeto roda em :3000 (Next.js). Lista fechada em vez de `*`
#: porque a API expõe dados de empresas e o dashboard é interno.
ORIGENS_PERMITIDAS = ("http://localhost:3000", "http://127.0.0.1:3000")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    faltando = settings.missing_required_keys()
    if faltando:
        # Aviso e não erro: a API de leitura funciona sem nenhuma dessas chaves.
        log.warning("chaves_ausentes", chaves=faltando)

    node_deps = deps.get_node_deps()
    log.info(
        "api_iniciada",
        busca=node_deps.search is not None,
        base_nvidia=node_deps.retriever is not None,
        pesos=node_deps.weights.version,
    )
    try:
        yield
    finally:
        if node_deps.fetcher is not None:
            await node_deps.fetcher.aclose()
        node_deps.llm.flush()
        deps.set_node_deps(None)


def create_app() -> FastAPI:
    app = FastAPI(
        title="NVIDIA Startup AI Radar",
        version="0.1.0",
        summary="Mapeia startups brasileiras AI-native, diagnostica defensibilidade "
        "e recomenda tecnologias NVIDIA com evidência rastreável.",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(ORIGENS_PERMITIDAS),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(searches.router)
    app.include_router(companies.router)
    app.include_router(briefings.router)

    @app.exception_handler(OperationalError)
    async def banco_indisponivel(_: Request, exc: OperationalError) -> JSONResponse:
        """Postgres fora do ar é 503, não 500.

        Sem isto, `GET /companies` sem banco devolve um 500 com stack trace de
        driver — que o frontend não consegue distinguir de bug da API, e que
        manda o operador procurar no lugar errado. O 503 diz o que fazer.
        """
        log.warning("banco_indisponivel", erro=str(exc.orig)[:200])
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Banco de dados indisponível. Suba a infraestrutura com "
                "`make up` e aplique as migrations com `make migrate`."
            },
        )

    @app.get("/health", response_model=HealthResponse, tags=["meta"])
    async def health() -> HealthResponse:
        """Mesmo diagnóstico do `radar.cli check`, em JSON.

        `status` é `degradado` e não `erro` quando falta infra opcional: a API
        continua servindo o que consegue, e quem monitora precisa distinguir
        "sem base de conhecimento" de "fora do ar".
        """
        from radar.persistence.db import healthcheck

        settings = get_settings()
        node_deps = deps.get_node_deps()

        banco = healthcheck()
        busca = node_deps.search is not None
        llm = bool(settings.nvidia_api_key)

        # Retriever construído não significa base utilizável: o cliente do Qdrant
        # não conecta na construção e o índice BM25 carrega vazio quando ainda não
        # houve ingestão. Reportar "ok" aqui esconderia justamente a causa de o
        # sistema não recomendar nada.
        kb = node_deps.retriever is not None and len(node_deps.retriever.bm25_index) > 0

        detalhes: dict[str, str] = {}
        if not banco:
            detalhes["database"] = "sem Postgres: a fila e o histórico ficam indisponíveis"
        if not kb:
            detalhes["knowledge_base"] = (
                "base NVIDIA não ingerida: rode `make ingest`. Até lá, as recomendações "
                "serão bloqueadas pelo guardrail de citação"
            )
        if not busca:
            detalhes["search"] = "sem TAVILY_API_KEY: a descoberta não retorna candidatas"
        if not llm:
            detalhes["llm"] = "sem NVIDIA_API_KEY: nenhum agente executa"

        return HealthResponse(
            status="ok" if all((banco, kb, busca, llm)) else "degradado",
            database=banco,
            knowledge_base=kb,
            search=busca,
            llm=llm,
            details=detalhes,
        )

    return app


app = create_app()
