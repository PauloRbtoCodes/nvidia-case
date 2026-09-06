"""Testes do grafo: a costura entre as camadas.

Nada aqui toca a rede. O `NIMClient` é real — o backend de chat é que é dublê —,
e isso é deliberado: assim os testes exercitam a renderização dos prompts
versionados com as variáveis que os nós de fato passam. Uma variável renomeada
no nó e esquecida no front matter do prompt quebra aqui, e não em produção.

O que precisa ser verificado neste nível não é a qualidade do modelo (isso é a
suíte de avaliação), e sim as **arestas condicionais** e os **guardrails**: quem
sai cedo, quem volta para re-coleta, quando o teto de tentativas fecha o ciclo,
o que acontece com uma citação inventada e se a falha de uma empresa contamina
o lote.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from radar.config import Settings
from radar.graph.build import build_radar_graph, make_consolidate
from radar.graph.nodes import NodeDeps, podar_nao_literais, precisa_recoletar
from radar.graph.nodes.recommender import sanear_recomendacoes
from radar.graph.state import MAX_SCRAPE_ATTEMPTS
from radar.llm.client import NIMClient
from radar.llm.schemas import (
    AxisScoreSet,
    BriefingDraft,
    EvidenceAudit,
    RecommendationSet,
    SearchPlan,
    SearchQuery,
    SignalType,
)
from radar.models.company import (
    AIMaturity,
    Classification,
    CompanyProfile,
    Founder,
    FundingRound,
    InferenceProvider,
    Stage,
)
from radar.models.evidence import Evidence, EvidenceBackedField, SourceKind
from radar.models.recommendation import (
    Briefing,
    Complexity,
    Priority,
    Recommendation,
    RetrievedChunk,
)
from radar.models.scoring import (
    AxisScore,
    DefensibilityAxis,
    DefensibilityScore,
    PriorityAssessment,
    PriorityBucket,
)
from radar.scoring.delta import ChangeKind
from radar.scoring.priority import avaliar_prioridade
from radar.scoring.product import inferir_categoria_produto, inferir_chave_provider
from radar.scraping.fetch import FetchResult

# --------------------------------------------------------------------------- #
# Fontes falsas: HTML de onde saem as evidências literais
# --------------------------------------------------------------------------- #
HOME_URL = "https://acmesaude.com.br"
CARREIRAS_URL = "https://acmesaude.com.br/carreiras"
BLOG_URL = "https://acmesaude.com.br/blog"

HOME_HTML = """<!doctype html><html lang="pt-BR">
<head><title>Acme Saude - IA para prontuarios</title></head>
<body><header><nav>
<a href="/carreiras">Carreiras</a><a href="/blog">Blog de engenharia</a>
</nav></header>
<main><article>
<h1>Acme Saude</h1>
<p>A Acme Saude constroi modelos proprios treinados sobre prontuarios eletronicos
anonimizados de 42 hospitais parceiros, um conjunto de dados que levamos quatro anos
para montar e que nenhum laboratorio consegue comprar.</p>
<p>Nossa plataforma escreve diretamente no prontuario eletronico do hospital via
integracao homologada com Tasy e MV, e nao apenas sugere textos para o medico copiar
manualmente depois.</p>
<p>Atendemos hoje o Hospital Sao Lucas e a rede Vida Plena em contratos anuais com
SLA de disponibilidade e suporte dedicado.</p>
</article></main></body></html>"""

CARREIRAS_HTML = """<!doctype html><html lang="pt-BR">
<head><title>Carreiras - Acme Saude</title></head>
<body><main><article>
<h1>Trabalhe na Acme Saude</h1>
<p>Hoje servimos a maior parte das requisicoes pela API da OpenAI e estamos montando
o time que vai levar a inferencia para dentro de casa ao longo do proximo ano.</p>
<h2>Engenheiro de MLOps</h2>
<p>Procuramos um Engenheiro de MLOps para assumir a esteira de avaliacao de modelos e
a operacao de inferencia em producao.</p>
</article></main></body></html>"""

BLOG_HTML = """<!doctype html><html lang="pt-BR">
<head><title>Como cortamos a latencia - Blog Acme</title></head>
<body><main><article>
<h1>Como cortamos a latencia de inferencia</h1>
<p>Migramos parte da carga para GPU dedicada com Triton Inference Server e a latencia
media caiu de 3,2 segundos para 600 milissegundos nas requisicoes de resumo clinico.</p>
</article></main></body></html>"""

PAGINAS = {HOME_URL: HOME_HTML, CARREIRAS_URL: CARREIRAS_HTML, BLOG_URL: BLOG_HTML}

TRECHO_DADOS = (
    "A Acme Saude constroi modelos proprios treinados sobre prontuarios eletronicos "
    "anonimizados de 42 hospitais parceiros, um conjunto de dados que levamos quatro "
    "anos para montar e que nenhum laboratorio consegue comprar."
)
TRECHO_WORKFLOW = (
    "Nossa plataforma escreve diretamente no prontuario eletronico do hospital via "
    "integracao homologada com Tasy e MV, e nao apenas sugere textos para o medico "
    "copiar manualmente depois."
)
TRECHO_CLIENTES = (
    "Atendemos hoje o Hospital Sao Lucas e a rede Vida Plena em contratos anuais com "
    "SLA de disponibilidade e suporte dedicado."
)
TRECHO_PROVIDER = (
    "Hoje servimos a maior parte das requisicoes pela API da OpenAI e estamos montando "
    "o time que vai levar a inferencia para dentro de casa ao longo do proximo ano."
)


def evidencia(url: str, trecho: str, kind: SourceKind = SourceKind.SITE_OFICIAL) -> Evidence:
    return Evidence(url=url, excerpt=trecho, kind=kind)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Dublês de infraestrutura
# --------------------------------------------------------------------------- #
class FetcherFake:
    """Substitui o `HttpFetcher`: devolve HTML de um dicionário, sem rede."""

    def __init__(self, paginas: dict[str, str], *, bloqueadas: Sequence[str] = ()) -> None:
        self.paginas = paginas
        self.bloqueadas = set(bloqueadas)
        self.pedidos: list[str] = []
        self.forcados: list[str] = []
        """URLs pedidas com `force_refresh` — é o que a política de frescor promete."""

    async def fetch_many(
        self, urls: Any, *, force_refresh: bool = False
    ) -> list[FetchResult]:
        resultados: list[FetchResult] = []
        for url in urls:
            self.pedidos.append(url)
            if force_refresh:
                self.forcados.append(url)
            if url in self.bloqueadas:
                resultados.append(FetchResult(url=url, status_code=403, html=""))
                continue
            html = self.paginas.get(url)
            if html is None:
                continue
            resultados.append(FetchResult(url=url, status_code=200, html=html))
        return resultados


@dataclass
class CandidatoFake:
    url: str
    title: str = ""
    snippet: str = ""
    score: float = 1.0
    query: str = ""

    @property
    def domain(self) -> str:
        return self.url.split("//")[-1].split("/")[0].removeprefix("www.")


class SearchFake:
    def __init__(self, candidatos: Sequence[CandidatoFake]) -> None:
        self.candidatos = list(candidatos)
        self.chamadas: list[list[str]] = []

    async def search(self, queries: Any, **_: Any) -> list[CandidatoFake]:
        self.chamadas.append(list(queries))
        return self.candidatos


class RetrieverFake:
    def __init__(self, chunks: Sequence[RetrievedChunk]) -> None:
        self.chunks = list(chunks)
        self.consultas: list[str] = []

    def retrieve_chunks(self, query: str, **_: Any) -> list[RetrievedChunk]:
        self.consultas.append(query)
        return list(self.chunks)


class RoteadorDeChat:
    """Backend de chat que responde conforme o agente que fez a pergunta.

    A escolha é pelo texto do system prompt e não pelo modelo porque vários
    agentes compartilham o mesmo par (modelo, temperatura) — o `NIMClient`
    reaproveita o backend entre eles, então o dublê precisa se orientar pelo
    conteúdo, exatamente como um humano lendo o trace faria.
    """

    MARCADORES = {
        "search_planner": "You are the Search Planner",
        "extractor": "You are the Extractor",
        "evidence_validator": "You are the Evidence Validator",
        "classifier": "You are the Startup Classifier",
        "defensibility_scorer": "You are the Defensibility Scorer",
        "recommender": "You are the Recommendation Engine",
        "briefing": "You are the Briefing Agent",
    }

    def __init__(self, respostas: dict[str, Any]) -> None:
        self.respostas = respostas
        self.chamadas: list[str] = []

    def invoke(self, messages: Any, **_: Any) -> Any:
        system = str(messages[0].content)
        for agente, marcador in self.MARCADORES.items():
            if marcador not in system:
                continue
            self.chamadas.append(agente)
            resposta = self.respostas.get(agente)
            if resposta is None:
                raise AssertionError(f"agente {agente!r} chamado sem resposta configurada")
            if callable(resposta):
                resposta = resposta(len([c for c in self.chamadas if c == agente]))
            if isinstance(resposta, BaseException):
                raise resposta

            class _Resposta:
                content = resposta

            return _Resposta()
        raise AssertionError(f"prompt de agente desconhecido: {system[:120]!r}")


# --------------------------------------------------------------------------- #
# Respostas dos agentes, construídas a partir dos modelos do domínio
# --------------------------------------------------------------------------- #
def plano_json() -> str:
    return SearchPlan(
        interpreted_intent="Startups brasileiras de IA em saude.",
        sector_focus=["saude"],
        queries=[
            SearchQuery(
                query="startups brasileiras IA prontuario eletronico",
                signal_type=SignalType.DESCOBERTA,
                rationale="Descobrir empresas do setor.",
                priority=1,
            ),
            SearchQuery(
                query='"engenheiro de mlops" vaga startup saude brasil',
                signal_type=SignalType.STACK_HIRING,
                rationale="Vaga tecnica revela stack.",
                priority=2,
            ),
            SearchQuery(
                query="startup saude IA rodada de investimento 2026",
                signal_type=SignalType.FUNDING,
                rationale="Capital recente indica capacidade de agir.",
                priority=3,
            ),
        ],
        priority_domains=["distrito.me"],
        exclusions=["curso"],
    ).model_dump_json()


def perfil_completo() -> CompanyProfile:
    """Perfil bem fundamentado: quatro campos com citação literal das páginas."""
    return CompanyProfile(
        name="Acme Saude",
        website=HOME_URL,  # type: ignore[arg-type]
        description="Plataforma de IA para documentacao clinica.",
        sector=EvidenceBackedField[str](
            value="saude", evidences=[evidencia(HOME_URL, TRECHO_DADOS)]
        ),
        ai_use_description=EvidenceBackedField[str](
            value="geracao de resumo clinico a partir do prontuario",
            evidences=[evidencia(HOME_URL, TRECHO_WORKFLOW)],
        ),
        proprietary_data_claim=EvidenceBackedField[str](
            value="prontuarios anonimizados de 42 hospitais",
            evidences=[evidencia(HOME_URL, TRECHO_DADOS)],
        ),
        inference_provider=EvidenceBackedField[InferenceProvider](
            value=InferenceProvider.API_EXTERNA,
            evidences=[
                evidencia(CARREIRAS_URL, TRECHO_PROVIDER, SourceKind.PAGINA_CARREIRAS)
            ],
        ),
        enterprise_customers=EvidenceBackedField[list[str]](
            value=["Hospital Sao Lucas", "Vida Plena"],
            evidences=[evidencia(HOME_URL, TRECHO_CLIENTES)],
        ),
        founders=[Founder(name="Marina Alves", role="CTO", technical_background=True)],
        funding_rounds=[
            FundingRound(
                stage=Stage.SERIE_A,
                amount_brl=30_000_000,
                announced_at=date(2026, 3, 1),
            )
        ],
        stage=Stage.SERIE_A,
        headcount_estimate=40,
        all_evidences=[evidencia(HOME_URL, TRECHO_DADOS)],
    )


def perfil_magro() -> CompanyProfile:
    """Um campo fundamentado de sete: cobertura abaixo do piso de re-coleta."""
    return CompanyProfile(
        name="Acme Saude",
        website=HOME_URL,  # type: ignore[arg-type]
        sector=EvidenceBackedField[str](
            value="saude", evidences=[evidencia(HOME_URL, TRECHO_DADOS)]
        ),
        stage=Stage.SEED,
    )


def extracao_json(profile: CompanyProfile) -> str:
    return json.dumps({"profile": json.loads(profile.model_dump_json())}, ensure_ascii=False)


def auditoria_json(*, recoletar: bool = False, anulados: Sequence[str] = ()) -> str:
    return EvidenceAudit(
        company_name="Acme Saude",
        audits=[],
        unsupported_fields=list(anulados),
        grounding_ratio=0.9 if not anulados else 0.5,
        requires_recollection=recoletar,
        suggested_queries=["Acme Saude blog engenharia inferencia"] if recoletar else [],
    ).model_dump_json()


def classificacao_json(maturity: AIMaturity = AIMaturity.AI_NATIVE) -> str:
    return Classification(
        maturity=maturity,
        confidence=0.85,
        rationale="Modelo proprio treinado sobre dado clinico exclusivo.",
    ).model_dump_json()


def eixos_json() -> str:
    return AxisScoreSet(
        company_name="Acme Saude",
        axes=[
            AxisScore(
                axis=DefensibilityAxis.PROPRIETARY_DATA,
                score=82.0,
                confidence=0.8,
                positive_signals=["dataset de prontuarios de 42 hospitais"],
                rationale="Dado clinico proprio e de dificil replicacao.",
            ),
            AxisScore(
                axis=DefensibilityAxis.WORKFLOW_DEPTH,
                score=75.0,
                confidence=0.7,
                positive_signals=["escreve no prontuario via Tasy e MV"],
                rationale="Integracao de escrita no sistema do cliente.",
            ),
            AxisScore(
                axis=DefensibilityAxis.STACK_OWNERSHIP,
                score=25.0,
                confidence=0.8,
                negative_signals=["maior parte das requisicoes pela API da OpenAI"],
                rationale="Depende de API externa para o caminho principal.",
            ),
            AxisScore(
                axis=DefensibilityAxis.DISTRIBUTION,
                score=60.0,
                confidence=0.2,
                rationale="Poucos sinais publicos de canal.",
            ),
        ],
    ).model_dump_json()


CHUNK_NIM = RetrievedChunk(
    text=(
        "O NVIDIA NIM entrega microsservicos de inferencia em container com endpoint "
        "compativel com a API da OpenAI, o que reduz a migracao a uma troca de base_url."
    ),
    source_url="https://docs.nvidia.com/nim/",  # type: ignore[arg-type]
    source_title="NVIDIA NIM - visao geral",
    technology="NIM",
    rrf_score=0.9,
)
CHUNK_TRT = RetrievedChunk(
    text=(
        "O TensorRT-LLM compila o modelo para o hardware alvo e sustenta ganhos de "
        "throughput relevantes em lote quando a carga justifica GPU dedicada."
    ),
    source_url="https://docs.nvidia.com/tensorrt-llm/",  # type: ignore[arg-type]
    source_title="TensorRT-LLM",
    technology="TensorRT-LLM",
    rrf_score=0.7,
)


def recomendacao(tecnologia: str, citacoes: list[RetrievedChunk]) -> Recommendation:
    return Recommendation(
        technology=tecnologia,
        addresses_axis=DefensibilityAxis.STACK_OWNERSHIP,
        technical_rationale=(
            "A vaga de MLOps e a menção à migração de inferência indicam time capaz de "
            "operar stack propria; o endpoint compativel reduz a troca a uma base_url."
        ),
        business_rationale=(
            "Reduz a exposicao ao provedor unico e da controle sobre custo por token "
            "no volume atual."
        ),
        priority=Priority.ALTA,
        complexity=Complexity.BAIXA,
        next_action="Enviar o blueprint de inferencia e propor benchmark de latencia.",
        kb_citations=citacoes,
    )


def recomendacoes_json(citacoes: list[RetrievedChunk] | None = None) -> str:
    return RecommendationSet(
        company_name="Acme Saude",
        recommendations=[recomendacao("NVIDIA NIM", citacoes or [CHUNK_NIM])],
    ).model_dump_json()


def briefing_json() -> str:
    return BriefingDraft(
        executive_summary=(
            "A Acme Saude usa IA como produto sobre um conjunto de prontuarios proprio "
            "de 42 hospitais, o que sustenta o eixo de dados. O risco esta na stack: "
            "hoje a inferencia principal roda em API externa."
        ),
        moat_plan=(
            "O ativo real ja existe e e o dado clinico. O passo seguinte e trazer a "
            "inferencia para dentro, comecando pelos fluxos de maior volume, e so "
            "depois avaliar customizacao de modelo sobre o dataset proprio."
        ),
        conversation_starters=["Como voces avaliam a qualidade dos resumos hoje?"],
        inception_fit="Creditos e capacitacao tecnica em inferencia.",
        caveats=["Score baseado em fontes publicas; o founder pode corrigir em uma frase."],
    ).model_dump_json()


# --------------------------------------------------------------------------- #
# Montagem
# --------------------------------------------------------------------------- #
def _settings() -> Settings:
    # Sem chaves: o observador vira `NullObserver` e nada tenta sair da máquina.
    return Settings(
        nvidia_api_key="",
        langfuse_public_key="",
        langfuse_secret_key="",
        cohere_api_key="",
    )


@dataclass
class HistoricoFake:
    """Dublê de `ScoreHistoryPort`: o "antes" do nó `compare`, sem banco.

    `anterior=None` é a primeira execução da empresa. `erro` simula o banco
    caindo no meio do lote — o nó tem que registrar a falha e o briefing sair
    assim mesmo.
    """

    anterior: DefensibilityScore | None = None
    erro: Exception | None = None
    chamadas: list[str] = field(default_factory=list)

    def previous_score(self, company_name: str) -> DefensibilityScore | None:
        self.chamadas.append(company_name)
        if self.erro is not None:
            raise self.erro
        return self.anterior

    def seen_before(self, company_name: str) -> bool:
        if self.erro is not None:
            raise self.erro
        return self.anterior is not None


@dataclass
class Ambiente:
    deps: NodeDeps
    chat: RoteadorDeChat
    fetcher: FetcherFake
    search: SearchFake
    retriever: RetrieverFake | None
    historico: HistoricoFake | None = None
    chunks: list[RetrievedChunk] = field(default_factory=list)


def montar(
    *,
    respostas: dict[str, Any] | None = None,
    paginas: dict[str, str] | None = None,
    bloqueadas: Sequence[str] = (),
    chunks: Sequence[RetrievedChunk] | None = None,
    candidatos: Sequence[CandidatoFake] | None = None,
    hoje: date | None = None,
    historico: HistoricoFake | None = None,
) -> Ambiente:
    padrao: dict[str, Any] = {
        "search_planner": plano_json(),
        "extractor": extracao_json(perfil_completo()),
        "evidence_validator": auditoria_json(),
        "classifier": classificacao_json(),
        "defensibility_scorer": eixos_json(),
        "recommender": recomendacoes_json(),
        "briefing": briefing_json(),
    }
    padrao.update(respostas or {})

    chat = RoteadorDeChat(padrao)
    fetcher = FetcherFake(dict(paginas or PAGINAS), bloqueadas=bloqueadas)
    search = SearchFake(
        list(candidatos) if candidatos is not None else [CandidatoFake(HOME_URL, "Acme Saude")]
    )
    lista = list(chunks) if chunks is not None else [CHUNK_NIM, CHUNK_TRT]
    retriever = RetrieverFake(lista)

    deps = NodeDeps(
        llm=NIMClient(settings=_settings(), chat_factory=lambda **_: chat),
        search=search,  # type: ignore[arg-type]
        fetcher=fetcher,  # type: ignore[arg-type]
        retriever=retriever,  # type: ignore[arg-type]
        # Sem reranker: a ordem do RRF já vem do dublê e o cross-encoder real
        # exigiria chave da Cohere. O caminho de degradação tem teste próprio.
        reranker=None,
        score_history=historico,
        clock=lambda: hoje or date(2026, 8, 14),
    )
    return Ambiente(
        deps=deps, chat=chat, fetcher=fetcher, search=search,
        retriever=retriever, historico=historico,
    )


async def executar(ambiente: Ambiente, *, query: str = "startups de IA em saude") -> dict[str, Any]:
    grafo = build_radar_graph(ambiente.deps)
    return await grafo.ainvoke({"query": query, "max_companies": 5})


# --------------------------------------------------------------------------- #
# Caminho completo
# --------------------------------------------------------------------------- #
async def test_pipeline_completo_produz_briefing_fundamentado():
    ambiente = montar()
    resultado = await executar(ambiente)

    assert len(resultado["briefings"]) == 1
    briefing: Briefing = resultado["briefings"][0]

    assert briefing.company_name == "Acme Saude"
    assert briefing.maturity is AIMaturity.AI_NATIVE
    assert briefing.recommendations, "recomendação fundamentada deveria sobreviver"
    assert briefing.total_citations >= 1
    # A citação exibida é o chunk recuperado, com os scores da busca preservados.
    assert briefing.recommendations[0].kb_citations[0].rrf_score == CHUNK_NIM.rrf_score
    assert briefing.caveats, "caveats são obrigatórios"
    assert briefing.markdown and "Defensibility Radar" in briefing.markdown
    assert resultado["queue"] == ["Acme Saude"]
    assert not resultado.get("failures")


async def test_score_separa_confianca_e_dispara_o_gate_pelo_eixo_fraco():
    ambiente = montar()
    resultado = await executar(ambiente)
    briefing: Briefing = resultado["briefings"][0]
    score = briefing.defensibility

    # Distribuição tem score 60 e confiança 0,2: não pode virar gap acionável.
    distribuicao = next(a for a in score.axes if a.axis is DefensibilityAxis.DISTRIBUTION)
    assert not distribuicao.is_actionable
    assert DefensibilityAxis.DISTRIBUTION not in score.actionable_gaps

    assert score.weakest_axis is DefensibilityAxis.STACK_OWNERSHIP
    assert briefing.recommendations[0].addresses_axis is DefensibilityAxis.STACK_OWNERSHIP


async def test_tco_desfavoravel_aparece_no_briefing_em_vez_de_ser_omitido():
    ambiente = montar()
    resultado = await executar(ambiente)
    briefing: Briefing = resultado["briefings"][0]

    assert briefing.defensibility.tco, "provedor de API identificado deveria gerar TCO"
    assert "estimativa a partir de sinais públicos" in (briefing.markdown or "").lower()
    # O veredito de cada cenário aparece no documento, favorável ou não.
    for estimativa in briefing.defensibility.tco:
        esperado = "sim" if estimativa.is_favorable else "não, neste volume"
        assert esperado in (briefing.markdown or "")


async def test_paginas_de_carreira_e_blog_sao_seguidas_a_partir_da_home():
    ambiente = montar()
    await executar(ambiente)

    # Vaga é a fonte mais honesta de stack: o grafo precisa chegar nela sozinho.
    assert CARREIRAS_URL in ambiente.fetcher.pedidos
    assert BLOG_URL in ambiente.fetcher.pedidos


# --------------------------------------------------------------------------- #
# Gatilho temporal: o nó `compare`
# --------------------------------------------------------------------------- #
def _score_anterior(*, stack_score: float, stack_conf: float = 0.8) -> DefensibilityScore:
    """Um score "de semana passada" para a Acme Saude, alinhado ao `eixos_json`.

    Só o eixo de stack varia entre os testes — os outros três repetem os valores
    da execução atual, então qualquer mudança detectada é a que o teste montou.
    """
    return DefensibilityScore(
        company_name="Acme Saude",
        weights_version="0.1.0-anterior",
        axes=[
            AxisScore(
                axis=DefensibilityAxis.PROPRIETARY_DATA, score=82.0, confidence=0.8,
                rationale="igual à execução atual",
            ),
            AxisScore(
                axis=DefensibilityAxis.WORKFLOW_DEPTH, score=75.0, confidence=0.7,
                rationale="igual à execução atual",
            ),
            AxisScore(
                axis=DefensibilityAxis.STACK_OWNERSHIP, score=stack_score,
                confidence=stack_conf, rationale="o eixo que este teste move",
            ),
            AxisScore(
                axis=DefensibilityAxis.DISTRIBUTION, score=60.0, confidence=0.2,
                rationale="igual à execução atual",
            ),
        ],
    )


async def test_primeira_execucao_nao_emite_diff_e_o_grafo_segue():
    historico = HistoricoFake(anterior=None)
    ambiente = montar(historico=historico)
    resultado = await executar(ambiente)

    assert len(resultado["briefings"]) == 1
    assert historico.chamadas == ["Acme Saude"], "o nó compare precisa ter consultado o histórico"
    assert resultado["company_results"][0].get("score_delta") is None
    assert not resultado.get("failures")


async def test_segunda_execucao_emite_o_diff_da_piora_medida():
    # Stack caiu de 70 para 25 com a mesma confiança: piora real, não sumiço de sinal.
    historico = HistoricoFake(anterior=_score_anterior(stack_score=70.0))
    ambiente = montar(historico=historico)
    resultado = await executar(ambiente)

    delta = resultado["company_results"][0].get("score_delta")
    assert delta is not None and delta.has_changes
    manchete = delta.headline_axis
    assert manchete is not None
    assert manchete.axis is DefensibilityAxis.STACK_OWNERSHIP
    assert manchete.kind is ChangeKind.PIOROU
    # O diff é um extra: o briefing sai como sempre.
    assert len(resultado["briefings"]) == 1


async def test_ausencia_de_evidencia_nao_vira_piora_no_grafo():
    # Confiança caiu junto com o score: o invariante do ADR 0002 no eixo do tempo.
    historico = HistoricoFake(anterior=_score_anterior(stack_score=70.0, stack_conf=0.95))
    ambiente = montar(historico=historico)
    resultado = await executar(ambiente)

    delta = resultado["company_results"][0]["score_delta"]
    stack = next(a for a in delta.axes if a.axis is DefensibilityAxis.STACK_OWNERSHIP)
    assert stack.kind is ChangeKind.CONFIANCA_CAIU
    assert stack.kind is not ChangeKind.PIOROU


async def test_primeira_passada_sobre_uma_empresa_confia_no_cache():
    ambiente = montar(historico=HistoricoFake(anterior=None))
    await executar(ambiente)

    assert ambiente.fetcher.forcados == [], (
        "sem execução anterior não há mudança a detectar; forçar rede aqui só "
        "encareceria o lote de descoberta"
    )


async def test_passada_de_monitoramento_forca_so_as_fontes_de_sinal():
    """O TTL do cache é o que faria o diff dizer "nada mudou" sem ter olhado.

    Numa empresa já vista, carreiras e blog voltam para a rede — é onde a vaga
    nova aparece. A home fica no cache: institucional muda pouco, e pagar rede
    por ela em todo monitoramento é desperdício.
    """
    ambiente = montar(historico=HistoricoFake(anterior=_score_anterior(stack_score=70.0)))
    await executar(ambiente)

    assert CARREIRAS_URL in ambiente.fetcher.forcados
    assert BLOG_URL in ambiente.fetcher.forcados
    assert HOME_URL not in ambiente.fetcher.forcados


async def test_erro_ao_ler_o_historico_vira_falha_e_nao_derruba_o_briefing():
    historico = HistoricoFake(erro=RuntimeError("banco fora do ar no meio do lote"))
    ambiente = montar(historico=historico)
    resultado = await executar(ambiente)

    assert len(resultado["briefings"]) == 1, "o diff é opcional; o diagnóstico não"
    falhas = resultado.get("failures") or []
    compare = next((f for f in falhas if f["node"] == "compare"), None)
    assert compare is not None
    assert compare["kind"] == "mundo"
    assert resultado["company_results"][0].get("score_delta") is None


# --------------------------------------------------------------------------- #
# Arestas condicionais
# --------------------------------------------------------------------------- #
async def test_non_ai_encerra_antes_do_scorer():
    ambiente = montar(respostas={"classifier": classificacao_json(AIMaturity.NON_AI)})
    resultado = await executar(ambiente)

    assert "defensibility_scorer" not in ambiente.chat.chamadas
    assert "recommender" not in ambiente.chat.chamadas
    assert not resultado.get("briefings")
    assert resultado["skipped"][0]["reason"] == "classificada como non_ai"


async def test_indeterminado_segue_para_o_score_em_vez_de_sumir_da_fila():
    """O invariante de justiça: startup discreta não pode ser tratada como non_ai."""
    ambiente = montar(respostas={"classifier": classificacao_json(AIMaturity.INDETERMINADO)})
    resultado = await executar(ambiente)

    assert "defensibility_scorer" in ambiente.chat.chamadas
    assert len(resultado["briefings"]) == 1
    assert resultado["briefings"][0].priority.bucket is PriorityBucket.MONITORAR


async def test_evidencia_magra_dispara_recoleta_e_para_no_teto():
    """Duas coletas, não três: o orçamento fecha o único ciclo do subgrafo."""
    ambiente = montar(
        respostas={
            "extractor": extracao_json(perfil_magro()),
            "evidence_validator": auditoria_json(recoletar=True),
        }
    )
    await executar(ambiente)

    coletas = ambiente.chat.chamadas.count("extractor")
    assert coletas == MAX_SCRAPE_ATTEMPTS
    # A re-coleta usa as queries que o validador sugeriu.
    assert any("blog engenharia" in q for chamada in ambiente.search.chamadas for q in chamada)


async def test_site_inacessivel_nao_produz_perfil_inventado():
    ambiente = montar(bloqueadas=[HOME_URL], paginas={HOME_URL: HOME_HTML})
    resultado = await executar(ambiente)

    assert "extractor" not in ambiente.chat.chamadas
    assert not resultado.get("briefings")
    assert resultado["skipped"][0]["company"] == "Acme Saude"


async def test_falha_em_uma_empresa_nao_derruba_o_lote():
    """A razão de o subgrafo existir: isolamento por empresa."""
    outra = "https://betaclinica.com.br"
    ambiente = montar(
        paginas={**PAGINAS, outra: HOME_HTML},
        bloqueadas=[outra],
        candidatos=[CandidatoFake(HOME_URL, "Acme Saude"), CandidatoFake(outra, "Beta Clinica")],
    )
    resultado = await executar(ambiente)

    assert len(resultado["briefings"]) == 1
    assert resultado["briefings"][0].company_name == "Acme Saude"
    assert [s["company"] for s in resultado["skipped"]] == ["Beta Clinica"]


async def test_planner_degradado_ainda_executa_a_busca():
    ambiente = montar(respostas={"search_planner": RuntimeError("modelo fora do ar")})
    resultado = await executar(ambiente)

    assert len(resultado["briefings"]) == 1
    falhas = [f for f in resultado["failures"] if f["node"] == "search_planner"]
    assert falhas and falhas[0]["recoverable"] is True
    # O plano de emergência usa a busca crua do usuário.
    assert ambiente.search.chamadas[0] == ["startups de IA em saude"]


# --------------------------------------------------------------------------- #
# Guardrails de evidência
# --------------------------------------------------------------------------- #
async def test_citacao_inventada_derruba_a_recomendacao():
    inventado = RetrievedChunk(
        text=(
            "O Triton Inference Server reduz em 90% o custo de qualquer inferencia, "
            "segundo benchmark interno da NVIDIA de 2026."
        ),
        source_url="https://docs.nvidia.com/inventado/",  # type: ignore[arg-type]
        source_title="Documento que nunca foi recuperado",
    )
    ambiente = montar(respostas={"recommender": recomendacoes_json([inventado])})
    resultado = await executar(ambiente)

    briefing: Briefing = resultado["briefings"][0]
    assert briefing.recommendations == []
    assert any("descartada" in nota for nota in _notas(resultado))
    # O briefing sai mesmo assim: diagnóstico com lacuna declarada serve; texto
    # sem fonte, não.
    assert briefing.markdown and "bloqueia recomendação sem citação" in briefing.markdown


async def test_sem_chunk_da_kb_a_recomendacao_e_bloqueada_nao_degradada():
    ambiente = montar(chunks=[])
    resultado = await executar(ambiente)

    assert "recommender" not in ambiente.chat.chamadas
    briefing: Briefing = resultado["briefings"][0]
    assert briefing.recommendations == []
    assert briefing.markdown is not None


async def test_sinal_tecnico_deterministico_entra_com_evidencia_literal():
    """`Triton` está no vocabulário e sai do texto, não da memória do modelo."""
    ambiente = montar()
    resultado = await executar(ambiente)

    perfil = resultado["company_results"][0]["profile"]
    triton = [s for s in perfil.tech_signals if "triton" in s.technology.casefold()]
    assert triton, "menção a Triton no blog deveria virar TechSignal"
    assert triton[0].evidences
    assert "Triton Inference Server" in triton[0].evidences[0].excerpt


def _notas(resultado: dict[str, Any]) -> list[str]:
    return list(resultado["company_results"][0].get("validation_notes") or [])


# --------------------------------------------------------------------------- #
# Unidades: as regras que o grafo apenas encadeia
# --------------------------------------------------------------------------- #
def test_campo_com_citacao_nao_literal_e_anulado():
    perfil = perfil_completo().model_copy(
        update={
            "sector": EvidenceBackedField[str](
                value="saude",
                evidences=[
                    evidencia(
                        HOME_URL,
                        "A empresa desenvolve solucoes inovadoras de IA para o setor de saude.",
                    )
                ],
            )
        }
    )
    saneado, anulados = podar_nao_literais(perfil, {HOME_URL: TRECHO_DADOS + TRECHO_WORKFLOW})

    assert "sector" in anulados
    assert saneado.sector is None, "campo sem lastro vira desconhecido, não nota baixa"
    # O que era literal continua de pé.
    assert saneado.proprietary_data_claim is not None


@pytest.mark.parametrize(
    ("tentativas", "cobertura", "pediu", "esperado"),
    [
        (0, 0.9, True, True),
        (0, 0.1, False, True),
        (0, 0.9, False, False),
        (MAX_SCRAPE_ATTEMPTS, 0.0, True, False),
    ],
)
def test_precisa_recoletar_respeita_o_teto(
    tentativas: int, cobertura: float, pediu: bool, esperado: bool
):
    assert (
        precisa_recoletar(scrape_attempts=tentativas, coverage=cobertura, pediu=pediu) is esperado
    )


def test_recomendacao_com_citacao_quase_certa_e_reconciliada_com_o_chunk_real():
    """Modelo que corta o fim do trecho continua citando o mesmo documento."""
    parcial = CHUNK_NIM.model_copy(update={"text": CHUNK_NIM.text[:100], "rrf_score": None})
    aceitas, descartadas = sanear_recomendacoes(
        [recomendacao("NVIDIA NIM", [parcial])], [CHUNK_NIM, CHUNK_TRT]
    )

    assert not descartadas
    assert aceitas[0].kb_citations[0].text == CHUNK_NIM.text
    assert aceitas[0].kb_citations[0].rrf_score == CHUNK_NIM.rrf_score


# --------------------------------------------------------------------------- #
# Fila de prioridade
# --------------------------------------------------------------------------- #
def _score(total_baixo: bool = True, confianca: float = 0.8) -> DefensibilityScore:
    nota = 20.0 if total_baixo else 80.0
    return DefensibilityScore(
        company_name="Acme",
        weights_version="teste",
        axes=[
            AxisScore(axis=eixo, score=nota, confidence=confianca, rationale="teste")
            for eixo in DefensibilityAxis
        ],
    )


def test_vulneravel_com_capital_e_time_vira_abordar_agora():
    perfil = perfil_completo()
    avaliacao = avaliar_prioridade(
        perfil, _score(), maturity=AIMaturity.AI_NATIVE, hoje=date(2026, 8, 14)
    )

    assert avaliacao.bucket is PriorityBucket.ABORDAR_AGORA
    assert avaliacao.capacity_to_act > 0.5
    assert avaliacao.urgency > 0


def test_vulneravel_sem_capacidade_vira_nutrir_e_nao_fura_a_fila():
    perfil = CompanyProfile(name="Sem Capital", stage=Stage.BOOTSTRAPPED)
    avaliacao = avaliar_prioridade(perfil, _score(), maturity=AIMaturity.AI_NATIVE)

    assert avaliacao.bucket is PriorityBucket.NUTRIR
    assert avaliacao.urgency < 40


def test_confianca_baixa_vira_monitorar_em_vez_de_conversa():
    perfil = perfil_completo()
    avaliacao = avaliar_prioridade(perfil, _score(confianca=0.2), maturity=AIMaturity.AI_NATIVE)

    assert avaliacao.bucket is PriorityBucket.MONITORAR
    assert "Re-coletar" in avaliacao.recommended_next_step


def test_ja_defensavel_vira_case_potencial():
    avaliacao = avaliar_prioridade(
        perfil_completo(), _score(total_baixo=False), maturity=AIMaturity.AI_NATIVE
    )
    assert avaliacao.bucket is PriorityBucket.CASE_POTENCIAL


def test_rodada_antiga_nao_conta_como_capital_fresco():
    antiga = perfil_completo().model_copy(
        update={
            "funding_rounds": [
                FundingRound(
                    stage=Stage.SERIE_A,
                    announced_at=(datetime.now(UTC).date() - timedelta(days=1200)),
                )
            ]
        }
    )
    recente = avaliar_prioridade(perfil_completo(), _score(), hoje=date(2026, 8, 14))
    velha = avaliar_prioridade(antiga, _score(), hoje=date(2026, 8, 14))

    assert velha.capacity_to_act < recente.capacity_to_act


async def test_consolidate_ordena_a_fila_por_urgencia():
    ambiente = montar()

    def briefing_com(nome: str, urgencia: float) -> Briefing:
        return Briefing(
            company_name=nome,
            executive_summary="resumo",
            maturity=AIMaturity.AI_NATIVE,
            defensibility=_score(),
            priority=PriorityAssessment(
                bucket=PriorityBucket.ABORDAR_AGORA,
                urgency=urgencia,
                capacity_to_act=0.8,
                capacity_rationale="teste",
                recommended_next_step="teste",
            ),
            moat_plan="plano",
        )

    saida = await make_consolidate(ambiente.deps)(
        {"briefings": [briefing_com("Baixa", 10.0), briefing_com("Alta", 90.0)]}
    )
    assert saida["queue"] == ["Alta", "Baixa"]
    # A fila é índice, não cópia: a chave com reducer não é reescrita.
    assert "briefings" not in saida


# --------------------------------------------------------------------------- #
# Inferências determinísticas que alimentam o TCO
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("descricao", "esperado"),
    [
        ("plataforma de transcricao de consultas medicas", "voz_transcricao"),
        ("chatbot de atendimento no whatsapp", "atendimento_cliente"),
        ("analise automatica de contratos juridicos", "analise_documentos"),
        ("copiloto para engenheiros de campo", "copiloto_vertical"),
        ("consultoria empresarial", "desconhecido"),
    ],
)
def test_categoria_de_produto_sai_do_texto_do_perfil(descricao: str, esperado: str):
    perfil = CompanyProfile(name="Teste", description=descricao)
    assert inferir_categoria_produto(perfil) == esperado


def test_provider_nao_e_inventado_para_quem_ja_roda_inferencia_propria():
    perfil = CompanyProfile(
        name="Teste",
        description="usamos a API da OpenAI historicamente",
        inference_provider=EvidenceBackedField[InferenceProvider](
            value=InferenceProvider.SELF_HOSTED
        ),
    )
    # Sem custo de API atual não há comparação; forjar uma inverteria a conclusão.
    assert inferir_chave_provider(perfil) is None


def test_provider_identificado_pelo_nome_no_texto():
    perfil = CompanyProfile(
        name="Teste",
        description="Servimos tudo pela API da OpenAI hoje.",
        inference_provider=EvidenceBackedField[InferenceProvider](
            value=InferenceProvider.API_EXTERNA
        ),
    )
    assert inferir_chave_provider(perfil) == "openai_gpt_frontier"


# ------------------------------------- bug de programacao vs mundo hostil


def test_erro_de_programacao_e_marcado_como_bug():
    """`except Exception` engolia TypeError com a mesma cara de "site fora do ar".

    O lote terminava em "sucesso parcial" e ninguém procurava a causa no próprio
    código — que é o pior desfecho possível para um bug.
    """
    from radar.graph.nodes.base import classificar, falha

    for exc in (TypeError("x"), AttributeError("y"), NameError("z"), IndexError("i")):
        assert classificar(exc) == "bug", type(exc).__name__
        assert falha("no", exc)["kind"] == "bug"


def test_falha_do_mundo_externo_continua_sendo_mundo():
    from radar.graph.nodes.base import classificar, falha

    for exc in (TimeoutError("rede"), ValueError("json malformado"), ConnectionError("dns")):
        assert classificar(exc) == "mundo", type(exc).__name__
        assert falha("no", exc)["kind"] == "mundo"

    # Falha construída a partir de string (nó que reporta sem exceção) é mundo.
    assert falha("no", "robots.txt proibiu")["kind"] == "mundo"
