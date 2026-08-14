"""Testes da API.

Banco SQLite em arquivo temporário, grafo substituído por dublê, nenhuma chamada
de rede. O que se verifica aqui é o que a camada HTTP acrescenta ao que já foi
testado embaixo dela:

- que `POST /searches` **não** bloqueia esperando o grafo;
- que o SSE entrega o histórico a quem conecta depois do início — o caso real,
  já que a UI faz o POST e só então abre o stream;
- que a fila de prioridade chega ordenada e filtrável;
- que gerar briefing sem score é 409, e não um texto inventado sobre uma empresa
  da qual não se sabe nada.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from api import deps as api_deps
from api.main import create_app
from api.runs import RunEvent, RunRegistry, RunStatus, SearchRun
from radar.models.company import AIMaturity, Classification, CompanyProfile, Stage
from radar.models.recommendation import Briefing
from radar.models.scoring import (
    AxisScore,
    DefensibilityAxis,
    DefensibilityScore,
    PriorityAssessment,
    PriorityBucket,
)
from radar.persistence import tables
from radar.persistence.repositories import BriefingRepository, CompanyRepository

# --------------------------------------------------------------------------- #
# Infraestrutura de teste
# --------------------------------------------------------------------------- #


@pytest.fixture
def engine(tmp_path):
    """SQLite em arquivo: o `session_scope` do sink abre conexão própria.

    Em memória, cada conexão veria um banco vazio e as escritas do sink
    desapareceriam — que é exatamente o bug que este arranjo evita.
    """
    url = f"sqlite+pysqlite:///{tmp_path / 'radar.db'}"
    eng = create_engine(url, future=True)
    tables.Base.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def session_factory(engine):
    return sessionmaker(engine, expire_on_commit=False, future=True)


@pytest.fixture
def registry() -> Iterator[RunRegistry]:
    reg = RunRegistry()
    try:
        yield reg
    finally:
        reg.clear()


@pytest.fixture
def client(session_factory, registry, monkeypatch) -> Iterator[TestClient]:
    """App com sessão, registro e dependências do grafo injetados."""
    from radar.config import Settings
    from radar.graph.nodes import NodeDeps
    from radar.llm.client import NIMClient

    def _sessao() -> Iterator[Session]:
        sessao = session_factory()
        try:
            yield sessao
        finally:
            sessao.close()

    # O `session_scope` usado nas escritas resolve a factory global.
    monkeypatch.setattr("radar.persistence.db.get_session_factory", lambda: session_factory)
    monkeypatch.setattr("radar.persistence.db.healthcheck", lambda *a, **k: True)

    node_deps = NodeDeps(
        llm=NIMClient(
            settings=Settings(langfuse_public_key="", langfuse_secret_key=""),
            chat_factory=lambda **_: _ChatIndisponivel(),
        ),
        search=None,
        fetcher=None,
        retriever=None,
        reranker=None,
    )

    app = create_app()
    app.dependency_overrides[api_deps.get_session] = _sessao
    app.dependency_overrides[api_deps.get_registry] = lambda: registry
    app.dependency_overrides[api_deps.get_node_deps] = lambda: node_deps

    api_deps.set_node_deps(node_deps)
    with TestClient(app) as cliente:
        yield cliente
    api_deps.set_node_deps(None)


class _ChatIndisponivel:
    """Qualquer chamada de modelo neste arquivo é um erro de teste, não de código."""

    def invoke(self, *_: Any, **__: Any) -> Any:
        raise AssertionError("nenhum teste da API deve chamar o modelo")


# --------------------------------------------------------------------------- #
# Dados
# --------------------------------------------------------------------------- #
def _score(nome: str, *, risco_alto: bool = True, confianca: float = 0.8) -> DefensibilityScore:
    nota = 20.0 if risco_alto else 85.0
    return DefensibilityScore(
        company_name=nome,
        weights_version="teste",
        axes=[
            AxisScore(
                axis=eixo,
                score=nota,
                confidence=confianca,
                rationale=f"eixo {eixo.value} de {nome}",
            )
            for eixo in DefensibilityAxis
        ],
    )


def _prioridade(urgencia: float, bucket: PriorityBucket) -> PriorityAssessment:
    return PriorityAssessment(
        bucket=bucket,
        urgency=urgencia,
        capacity_to_act=0.7,
        capacity_rationale="teste",
        recommended_next_step="teste",
    )


def _briefing(nome: str, score: DefensibilityScore, prioridade: PriorityAssessment) -> Briefing:
    return Briefing(
        company_name=nome,
        executive_summary=f"Resumo executivo de {nome}.",
        maturity=AIMaturity.AI_NATIVE,
        defensibility=score,
        priority=prioridade,
        moat_plan="Plano de fosso.",
        caveats=["Diagnóstico a partir de fontes públicas."],
        markdown=f"# {nome}\n\nBriefing completo.\n",
    )


def _semear(
    session_factory,
    nome: str,
    *,
    urgencia: float,
    bucket: PriorityBucket = PriorityBucket.ABORDAR_AGORA,
    confianca: float = 0.8,
    com_briefing: bool = True,
):
    """Grava uma empresa diagnosticada e devolve o id."""
    score = _score(nome, confianca=confianca)
    prioridade = _prioridade(urgencia, bucket)
    with session_factory() as sessao:
        empresa = CompanyRepository.upsert(
            sessao, CompanyProfile(name=nome, stage=Stage.SERIE_A)
        )
        if com_briefing:
            BriefingRepository.save(sessao, empresa.id, _briefing(nome, score, prioridade))
        else:
            from radar.persistence.repositories import ScoreRepository

            ScoreRepository.add(sessao, empresa.id, score, prioridade)
        sessao.commit()
        return empresa.id


# --------------------------------------------------------------------------- #
# Meta
# --------------------------------------------------------------------------- #
def test_health_reporta_degradado_sem_infra_opcional(client: TestClient):
    corpo = client.get("/health").json()

    assert corpo["status"] == "degradado"
    assert corpo["database"] is True
    assert corpo["knowledge_base"] is False
    # Degradado precisa dizer o que se perde e o que fazer, senão vira alarme sem ação.
    assert "make ingest" in corpo["details"]["knowledge_base"]
    assert "guardrail de citação" in corpo["details"]["knowledge_base"]


def test_openapi_expoe_os_endpoints_do_plano(client: TestClient):
    caminhos = client.get("/openapi.json").json()["paths"]
    for esperado in (
        "/searches",
        "/searches/{run_id}/stream",
        "/companies",
        "/companies/{company_id}",
        "/companies/{company_id}/briefing",
        "/briefings/{briefing_id}.md",
    ):
        assert esperado in caminhos


# --------------------------------------------------------------------------- #
# Fila de prioridade
# --------------------------------------------------------------------------- #
def test_fila_sai_ordenada_por_urgencia(client: TestClient, session_factory):
    _semear(session_factory, "Baixa Urgencia", urgencia=12.0)
    _semear(session_factory, "Alta Urgencia", urgencia=88.0)

    fila = client.get("/companies").json()
    assert [item["company_name"] for item in fila] == ["Alta Urgencia", "Baixa Urgencia"]


def test_fila_filtra_por_bucket(client: TestClient, session_factory):
    _semear(session_factory, "Para Abordar", urgencia=70.0)
    _semear(session_factory, "Para Nutrir", urgencia=20.0, bucket=PriorityBucket.NUTRIR)

    fila = client.get("/companies", params={"bucket": "nutrir"}).json()
    assert [item["company_name"] for item in fila] == ["Para Nutrir"]


def test_fila_esconde_diagnostico_sem_evidencia_quando_pedido(
    client: TestClient, session_factory
):
    """Confiança baixa é 'não sabemos', e o gerente precisa poder filtrar isso."""
    _semear(session_factory, "Bem Medida", urgencia=50.0, confianca=0.9)
    _semear(session_factory, "Mal Medida", urgencia=60.0, confianca=0.1)

    todas = client.get("/companies").json()
    filtradas = client.get("/companies", params={"min_confidence": 0.5}).json()

    assert len(todas) == 2
    assert [item["company_name"] for item in filtradas] == ["Bem Medida"]


def test_detalhe_da_empresa_traz_evidencias_dos_eixos(client: TestClient, session_factory):
    """O radar clicável depende de o `AxisScore` chegar inteiro ao frontend."""
    company_id = _semear(session_factory, "Acme", urgencia=50.0)

    corpo = client.get(f"/companies/{company_id}").json()
    assert corpo["profile"]["name"] == "Acme"
    assert corpo["has_briefing"] is True
    eixos = corpo["defensibility"]["axes"]
    assert len(eixos) == 4
    assert all("confidence" in eixo and "evidences" in eixo for eixo in eixos)
    # `is_actionable` e `gap_severity` são computed fields do domínio — se
    # sumirem da serialização, a UI perde a distinção entre "nota baixa" e
    # "evidência insuficiente".
    assert all("is_actionable" in eixo and "gap_severity" in eixo for eixo in eixos)


def test_empresa_inexistente_e_404(client: TestClient):
    resposta = client.get("/companies/00000000-0000-0000-0000-000000000000")
    assert resposta.status_code == 404


# --------------------------------------------------------------------------- #
# Briefings
# --------------------------------------------------------------------------- #
def test_exporta_markdown_com_content_disposition(client: TestClient, session_factory):
    company_id = _semear(session_factory, "Acme Saude", urgencia=50.0)
    briefing = client.get(f"/companies/{company_id}/briefing").json()

    resposta = client.get(briefing["markdown_url"])
    assert resposta.status_code == 200
    assert "text/markdown" in resposta.headers["content-type"]
    assert "acme-saude" in resposta.headers["content-disposition"]
    assert resposta.text.startswith("# Acme Saude")


def test_gerar_briefing_sem_score_e_409_e_nao_texto_inventado(
    client: TestClient, session_factory
):
    with session_factory() as sessao:
        empresa = CompanyRepository.upsert(sessao, CompanyProfile(name="Sem Score"))
        sessao.commit()
        company_id = empresa.id

    resposta = client.post(f"/companies/{company_id}/briefing")
    assert resposta.status_code == 409
    assert "sem score não há diagnóstico" in resposta.json()["detail"]


def test_briefing_ausente_e_404(client: TestClient, session_factory):
    company_id = _semear(session_factory, "So Score", urgencia=10.0, com_briefing=False)
    assert client.get(f"/companies/{company_id}/briefing").status_code == 404


# --------------------------------------------------------------------------- #
# Buscas e SSE
# --------------------------------------------------------------------------- #
def test_post_searches_responde_202_sem_esperar_o_grafo(
    client: TestClient, registry: RunRegistry, monkeypatch
):
    """A requisição não pode bloquear: uma execução leva minutos."""
    liberar = asyncio.Event()

    async def _grafo_lento(run: SearchRun, deps: Any) -> None:
        run.status = RunStatus.EXECUTANDO
        await liberar.wait()
        run.finish(result={})

    monkeypatch.setattr("api.routers.searches.executar_busca", _grafo_lento)

    resposta = client.post("/searches", json={"query": "startups de IA em saude"})
    assert resposta.status_code == 202

    corpo = resposta.json()
    assert corpo["status"] in ("pendente", "executando")
    assert corpo["stream_url"] == f"/searches/{corpo['id']}/stream"
    assert registry.get(corpo["id"]) is not None

    liberar.set()


def test_query_curta_demais_e_rejeitada(client: TestClient):
    assert client.post("/searches", json={"query": "ia"}).status_code == 422


def test_busca_inexistente_e_404(client: TestClient):
    assert client.get("/searches/naoexiste").status_code == 404
    assert client.get("/searches/naoexiste/stream").status_code == 404


def test_stream_entrega_historico_a_quem_conecta_depois(
    client: TestClient, registry: RunRegistry
):
    """O caso real: a UI faz o POST e só então abre o stream.

    Sem histórico, todo evento emitido nesse intervalo sumiria e a tela abriria
    com o progresso já pela metade.
    """
    run = registry.create("consulta", 5)
    run.emit(RunEvent(type="node", node="Planejando as buscas"))
    run.emit(RunEvent(type="node", node="Descobrindo empresas", detail="3 candidatas"))
    run.finish(result={"queue": ["Acme"], "briefings": []})

    with client.stream("GET", f"/searches/{run.id}/stream") as resposta:
        assert resposta.status_code == 200
        eventos = [
            json.loads(linha[len("data: ") :])
            for linha in resposta.iter_lines()
            if linha.startswith("data: ")
        ]

    assert [e["node"] for e in eventos if e["type"] == "node"] == [
        "Planejando as buscas",
        "Descobrindo empresas",
    ]
    assert eventos[-1] == {
        **eventos[-1],
        "type": "status",
        "detail": "concluida",
    }


def test_status_da_busca_resume_o_resultado(client: TestClient, registry: RunRegistry):
    run = registry.create("consulta", 5)
    run.finish(
        result={
            "discovered": [{"url": "a"}, {"url": "b"}],
            "briefings": [
                _briefing("Acme", _score("Acme"), _prioridade(50.0, PriorityBucket.NUTRIR))
            ],
            "skipped": [{"company": "Beta"}],
            "failures": [{"node": "scraper"}],
            "queue": ["Acme"],
        }
    )

    corpo = client.get(f"/searches/{run.id}").json()
    assert corpo["status"] == "concluida"
    assert corpo["companies_found"] == 2
    assert corpo["briefings_ready"] == 1
    assert corpo["skipped"] == 1
    assert corpo["failures"] == 1
    assert corpo["queue"] == ["Acme"]


# --------------------------------------------------------------------------- #
# Registro de execuções
# --------------------------------------------------------------------------- #
async def test_subscribe_nao_duplica_nem_perde_evento():
    """A ordem de inscrição em `SearchRun.subscribe` é o que garante isso."""
    run = SearchRun(id="x", query="q", max_companies=1)
    run.emit(RunEvent(type="node", node="antes"))

    recebidos: list[str] = []

    async def _consumir() -> None:
        async for evento in run.subscribe():
            recebidos.append(evento.node or evento.detail or "")

    consumidor = asyncio.create_task(_consumir())
    await asyncio.sleep(0)  # deixa o consumidor se inscrever e drenar o histórico

    run.emit(RunEvent(type="node", node="depois"))
    run.finish(result={})
    await asyncio.wait_for(consumidor, timeout=2)

    assert recebidos == ["antes", "depois", "concluida"]


async def test_execucao_que_termina_antes_da_inscricao_nao_trava_o_stream():
    """`_FIM` já foi distribuído; sem a checagem de `finished`, o leitor esperaria para sempre."""
    run = SearchRun(id="y", query="q", max_companies=1)
    run.emit(RunEvent(type="node", node="unico"))
    run.finish(result={})

    recebidos = [evento.type async for evento in run.subscribe()]
    assert recebidos == ["node", "status"]


def test_registro_poda_terminadas_antigas_mas_nunca_as_vivas():
    """O teto conta terminadas; execução viva sobrevive a qualquer volume."""
    reg = RunRegistry(max_runs=2)
    viva = reg.create("viva", 1)
    for i in range(5):
        reg.create(f"terminada-{i}", 1).finish(result={})
    reg.create("gatilho-da-poda", 1)

    assert reg.get(viva.id) is not None, "execução em andamento não pode ser podada"

    terminadas = [r for r in reg.list() if r.finished]
    assert len(terminadas) == 2
    # As mais recentes é que ficam: o operador olha o que acabou de rodar.
    assert [r.query for r in terminadas] == ["terminada-4", "terminada-3"]


def test_evento_serializa_data_em_iso():
    evento = RunEvent(type="node", node="x", at=datetime(2026, 8, 14, 12, 0, tzinfo=UTC))
    assert evento.to_dict()["at"] == "2026-08-14T12:00:00+00:00"


def test_classification_do_detalhe_vem_do_dominio(client: TestClient, session_factory):
    """A API não redefine `Classification`; ela devolve o modelo do domínio."""
    from radar.persistence.repositories import ClassificationRepository

    company_id = _semear(session_factory, "Com Classe", urgencia=30.0)
    with session_factory() as sessao:
        ClassificationRepository.add(
            sessao,
            company_id,
            Classification(
                maturity=AIMaturity.AI_NATIVE,
                confidence=0.9,
                rationale="modelo proprio",
            ),
        )
        sessao.commit()

    corpo = client.get(f"/companies/{company_id}").json()
    assert corpo["classification"]["maturity"] == "ai_native"
    assert corpo["classification"]["rationale"] == "modelo proprio"
