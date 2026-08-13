"""Testes da camada de persistência.

Rodam em SQLite em memória: não há Docker nesta máquina, e um esquema que só
sobe no Postgres é um esquema que ninguém verifica antes do deploy. As colunas
JSON usam `with_variant(JSONB, "postgresql")` justamente para que o mesmo
`metadata` valha nos dois bancos — o que os testes aqui exercitam é o esquema
real, não uma versão simplificada dele.

O que está fora de alcance do SQLite (concorrência real no `ON CONFLICT`,
tipagem JSONB, `SET NULL` sob carga) fica marcado com `skipif`.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from radar.config import PROJECT_ROOT
from radar.models.company import (
    AIMaturity,
    Classification,
    CompanyProfile,
    Founder,
    InferenceProvider,
    Stage,
    TechSignal,
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
    TCOEstimate,
    TCOScenario,
)
from radar.persistence import db, tables
from radar.persistence.repositories import (
    BriefingRepository,
    ClassificationRepository,
    CompanyRepository,
    EvidenceRepository,
    RecommendationRepository,
    ScoreRepository,
    normalize_name,
    normalize_website,
    priority_queue,
)

POSTGRES_URL = os.getenv("TEST_DATABASE_URL")
requires_postgres = pytest.mark.skipif(
    POSTGRES_URL is None,
    reason="Precisa de Postgres no ar (defina TEST_DATABASE_URL). Sem Docker nesta máquina.",
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def session() -> Iterator[Session]:
    engine = db.build_engine("sqlite://")
    db.configure(engine)
    db.create_all(engine)
    with db.session_scope() as s:
        yield s
    db.reset()


def _ev(path: str, excerpt: str, kind: SourceKind = SourceKind.SITE_OFICIAL) -> Evidence:
    return Evidence(url=f"https://acme.com.br/{path}", kind=kind, excerpt=excerpt)


EV_DADOS = _ev(
    "sobre",
    "Treinamos modelos com a base de laudos anonimizados dos nossos hospitais parceiros.",
    SourceKind.BLOG_TECNICO,
)
EV_VAGA = _ev(
    "carreiras",
    "Buscamos ML Engineer para otimizar inferencia em GPU com TensorRT e Triton.",
    SourceKind.PAGINA_CARREIRAS,
)
EV_STACK = _ev(
    "blog/infra",
    "Servimos nosso modelo proprietario em GPUs dedicadas, sem depender de API externa.",
    SourceKind.BLOG_TECNICO,
)


def _profile(
    name: str = "Acme Saúde Ltda", website: str | None = "https://acme.com.br"
) -> CompanyProfile:
    return CompanyProfile(
        name=name,
        website=website,
        description="Copiloto clínico para hospitais.",
        founded_year=2021,
        hq_city="São Paulo",
        hq_state="SP",
        stage=Stage.SEED,
        headcount_estimate=28,
        sector=EvidenceBackedField[str](
            value="healthtech", evidences=[EV_DADOS], reasoning="Descrito no próprio site."
        ),
        inference_provider=EvidenceBackedField[InferenceProvider](
            value=InferenceProvider.SELF_HOSTED, evidences=[EV_STACK]
        ),
        named_integrations=EvidenceBackedField[list[str]](
            value=["Tasy", "MV"], evidences=[EV_DADOS]
        ),
        tech_signals=[
            TechSignal(technology="Triton", category="infra", evidences=[EV_VAGA]),
        ],
        founders=[
            Founder(name="Joana Lima", role="CTO", technical_background=True, evidences=[EV_VAGA]),
        ],
        open_engineering_roles=["ML Engineer", "Data Engineer"],
        source_urls=["https://acme.com.br"],
        all_evidences=[EV_DADOS, EV_VAGA, EV_STACK],
    )


def _score(weights_version: str = "0.1.0-inicial", stack: float = 30.0) -> DefensibilityScore:
    scores = {
        DefensibilityAxis.PROPRIETARY_DATA: (72.0, 0.9),
        DefensibilityAxis.WORKFLOW_DEPTH: (58.0, 0.7),
        DefensibilityAxis.STACK_OWNERSHIP: (stack, 0.8),
        DefensibilityAxis.DISTRIBUTION: (40.0, 0.5),
    }
    return DefensibilityScore(
        company_name="Acme Saúde Ltda",
        weights_version=weights_version,
        axes=[
            AxisScore(
                axis=axis,
                score=value,
                confidence=conf,
                positive_signals=["dado clínico próprio"] if value > 50 else [],
                negative_signals=[] if value > 50 else ["depende de API externa"],
                evidences=[EV_DADOS],
                rationale=f"Rationale do eixo {axis.value}.",
            )
            for axis, (value, conf) in scores.items()
        ],
        tco=[
            TCOEstimate(
                scenario=TCOScenario.MEDIO,
                monthly_tokens_estimate=120_000_000,
                current_provider="OpenAI",
                current_monthly_usd=4200.0,
                nvidia_stack_monthly_usd=2100.0,
                gpu_assumption="1x L40S sob demanda",
                assumptions=["Preço de USD 0,035 por 1M tokens de entrada."],
                weights_version=weights_version,
            )
        ],
    )


def _recommendation() -> Recommendation:
    return Recommendation(
        technology="NVIDIA NIM",
        addresses_axis=DefensibilityAxis.STACK_OWNERSHIP,
        technical_rationale="Padroniza o serving do modelo próprio com TensorRT-LLM embutido.",
        business_rationale="Reduz o custo mensal de inferência e tira o teto de margem.",
        priority=Priority.ALTA,
        complexity=Complexity.MEDIA,
        next_action="Rodar um benchmark do modelo atual em NIM no API Catalog.",
        kb_citations=[
            RetrievedChunk(
                text="NIM entrega microsserviços de inferência otimizados por TensorRT-LLM.",
                source_url="https://docs.nvidia.com/nim/",
                source_title="NIM Overview",
                technology="NIM",
                rrf_score=0.031,
                rerank_score=0.94,
            )
        ],
        company_evidences=[EV_STACK],
    )


# --------------------------------------------------------------------------- #
# Esquema
# --------------------------------------------------------------------------- #


def test_schema_cria_todas_as_tabelas(session: Session) -> None:
    """O esquema precisa subir inteiro fora do Postgres, senão ninguém o testa."""
    from sqlalchemy import inspect

    existing = set(inspect(session.get_bind()).get_table_names())
    esperadas = {
        "companies",
        "evidences",
        "classifications",
        "defensibility_scores",
        "tco_estimates",
        "priority_assessments",
        "recommendations",
        "recommendation_evidences",
        "recommendation_kb_citations",
        "briefings",
    }
    assert esperadas <= existing


def test_healthcheck_responde(session: Session) -> None:
    assert db.healthcheck(session.get_bind()) is True


def test_indices_exigidos_existem(session: Session) -> None:
    """Índices da fila de prioridade e da busca por empresa não são opcionais."""
    from sqlalchemy import inspect

    inspector = inspect(session.get_bind())

    def nomes(tabela: str) -> set[str]:
        return {i["name"] for i in inspector.get_indexes(tabela) if i["name"]}

    assert "ix_companies_name" in nomes("companies")
    assert "ix_companies_website" in nomes("companies")
    assert "ix_evidences_company_id" in nomes("evidences")
    assert "ix_defensibility_scores_company_id" in nomes("defensibility_scores")


# --------------------------------------------------------------------------- #
# Normalização e upsert
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [
        ("Acme Saúde Ltda", "acme saude"),
        ("ACME  SAUDE", "acme saude"),
        ("Acme Saúde S.A.", "acme saude"),
    ],
)
def test_normalizacao_de_nome_colapsa_variacoes(bruto: str, esperado: str) -> None:
    assert normalize_name(bruto) == esperado


@pytest.mark.parametrize(
    "bruto",
    ["https://acme.com.br", "http://www.acme.com.br/", "https://ACME.com.br"],
)
def test_normalizacao_de_site_colapsa_variacoes(bruto: str) -> None:
    assert normalize_website(bruto) == "acme.com.br"


def test_upsert_nao_duplica_mesma_empresa_por_caminhos_diferentes(session: Session) -> None:
    """Mesma startup achada pelo diretório e pelo site é uma linha, não duas.

    Duplicata aqui não é detalhe de higiene de dados: ela apareceria duas vezes
    na fila do gerente, cada metade com um score diferente.
    """
    primeiro = CompanyRepository.upsert(session, _profile())
    segundo = CompanyRepository.upsert(
        session, _profile(name="ACME SAUDE", website="http://www.acme.com.br/")
    )
    session.flush()

    assert primeiro.id == segundo.id
    assert session.query(tables.Company).count() == 1


def test_upsert_casa_por_nome_quando_o_site_e_desconhecido(session: Session) -> None:
    """Diretório de startups frequentemente lista o nome sem o domínio."""
    original = CompanyRepository.upsert(session, _profile())
    sem_site = CompanyRepository.upsert(session, _profile(name="Acme Saúde", website=None))
    assert original.id == sem_site.id


def test_upsert_nao_apaga_campo_ja_conhecido_com_valor_nulo(session: Session) -> None:
    """Segunda passada mais pobre não pode regredir o perfil.

    Se o scraper só conseguiu ler a página de carreiras, ele sabe menos — não
    sabe o contrário.
    """
    CompanyRepository.upsert(session, _profile())
    magro = CompanyProfile(name="Acme Saúde Ltda", website="https://acme.com.br")
    row = CompanyRepository.upsert(session, magro)
    session.flush()

    assert row.description == "Copiloto clínico para hospitais."
    assert row.hq_state == "SP"
    assert row.sector is not None


# --------------------------------------------------------------------------- #
# Dedupe de evidência
# --------------------------------------------------------------------------- #


def test_dedupe_de_evidencia_por_content_hash(session: Session) -> None:
    """O mesmo trecho recuperado duas vezes é uma prova, não duas.

    Contá-lo em dobro inflaria a confiança agregada do eixo — o sistema
    fabricaria certeza a partir de repetição.
    """
    company = CompanyRepository.upsert(session, _profile())
    session.flush()
    antes = EvidenceRepository.count(session, company.id)

    EvidenceRepository.bulk_upsert(session, company.id, [EV_DADOS, EV_DADOS, EV_VAGA])
    assert EvidenceRepository.count(session, company.id) == antes

    novo = _ev("imprensa", "A empresa anunciou parceria com a rede hospitalar nacional.")
    EvidenceRepository.bulk_upsert(session, company.id, [novo, novo])
    assert EvidenceRepository.count(session, company.id) == antes + 1


def test_dedupe_sobrevive_a_recoleta_do_mesmo_site(session: Session) -> None:
    """Re-scraping semanal não pode multiplicar as evidências existentes."""
    company = CompanyRepository.upsert(session, _profile())
    session.flush()
    for _ in range(3):
        CompanyRepository.upsert(session, _profile())
        session.flush()

    assert EvidenceRepository.count(session, company.id) == 3


def test_url_diferente_com_mesmo_trecho_nao_e_duplicata(session: Session) -> None:
    """O hash é `url|excerpt`: a mesma frase em duas fontes são dois sinais."""
    company = CompanyRepository.upsert(session, _profile())
    session.flush()
    antes = EvidenceRepository.count(session, company.id)

    outra_fonte = Evidence(
        url="https://noticia.com.br/acme",
        kind=SourceKind.NOTICIA,
        excerpt=EV_DADOS.excerpt,
    )
    EvidenceRepository.bulk_upsert(session, company.id, [outra_fonte])
    assert EvidenceRepository.count(session, company.id) == antes + 1


# --------------------------------------------------------------------------- #
# Round-trip Pydantic → ORM → Pydantic
# --------------------------------------------------------------------------- #


def test_round_trip_do_perfil_preserva_dados(session: Session) -> None:
    """Ida e volta pelo banco não pode perder nada do contrato Pydantic.

    Perder a evidência de um campo inferido seria pior do que perder o campo: o
    valor continuaria aparecendo na UI, agora sem a prova que o sustenta.
    """
    original = _profile()
    row = CompanyRepository.upsert(session, original)
    session.flush()

    voltou = CompanyRepository.get(session, row.id)
    assert voltou is not None

    assert voltou.name == original.name
    assert str(voltou.website) == str(original.website)
    assert voltou.description == original.description
    assert voltou.founded_year == original.founded_year
    assert (voltou.hq_city, voltou.hq_state) == (original.hq_city, original.hq_state)
    assert voltou.stage is original.stage
    assert voltou.headcount_estimate == original.headcount_estimate
    assert voltou.open_engineering_roles == original.open_engineering_roles

    assert voltou.sector is not None
    assert voltou.sector.value == "healthtech"
    assert voltou.sector.reasoning == original.sector.reasoning
    assert [e.content_hash for e in voltou.sector.evidences] == [EV_DADOS.content_hash]

    assert voltou.inference_provider is not None
    assert voltou.inference_provider.value is InferenceProvider.SELF_HOSTED
    assert voltou.named_integrations is not None
    assert voltou.named_integrations.value == ["Tasy", "MV"]

    assert [s.technology for s in voltou.tech_signals] == ["Triton"]
    assert [f.name for f in voltou.founders] == ["Joana Lima"]
    assert voltou.founders[0].technical_background is True
    assert {e.content_hash for e in voltou.all_evidences} == {
        EV_DADOS.content_hash,
        EV_VAGA.content_hash,
        EV_STACK.content_hash,
    }


def test_round_trip_preserva_confianca_calculada(session: Session) -> None:
    """A confiança é derivada das evidências — se elas voltam certas, ela também.

    É o invariante que importa: confiança recalculada a partir do banco tem que
    bater com a que o extrator produziu, senão o histórico fica incomparável.
    """
    original = _profile()
    row = CompanyRepository.upsert(session, original)
    session.flush()
    voltou = CompanyRepository.get(session, row.id)

    assert voltou is not None
    assert voltou.sector is not None
    assert voltou.sector.confidence == original.sector.confidence
    assert voltou.evidence_coverage == original.evidence_coverage


def test_round_trip_do_score_preserva_eixos_e_tco(session: Session) -> None:
    company = CompanyRepository.upsert(session, _profile())
    session.flush()
    original = _score()
    ScoreRepository.add(session, company.id, original)
    session.flush()

    voltou = ScoreRepository.latest(session, company.id)
    assert voltou is not None
    assert voltou.total == original.total
    assert voltou.commoditization_risk == original.commoditization_risk
    assert voltou.global_confidence == original.global_confidence
    assert voltou.weakest_axis is original.weakest_axis
    assert voltou.weights_version == original.weights_version
    assert {a.axis for a in voltou.axes} == {a.axis for a in original.axes}
    assert voltou.candidate_technologies() == original.candidate_technologies()

    assert len(voltou.tco) == 1
    assert voltou.tco[0].monthly_savings_usd == original.tco[0].monthly_savings_usd
    assert voltou.tco[0].assumptions == original.tco[0].assumptions


def test_round_trip_da_classificacao(session: Session) -> None:
    company = CompanyRepository.upsert(session, _profile())
    session.flush()
    original = Classification(
        maturity=AIMaturity.AI_NATIVE,
        confidence=0.82,
        rationale="Modelo próprio treinado com dado clínico proprietário.",
        evidences=[EV_DADOS, EV_STACK],
        model_used="meta/llama-3.3-70b-instruct",
        prompt_version="classifier-v1",
    )
    ClassificationRepository.add(session, company.id, original)
    session.flush()

    voltou = ClassificationRepository.latest(session, company.id)
    assert voltou is not None
    assert voltou.maturity is AIMaturity.AI_NATIVE
    assert voltou.confidence == original.confidence
    assert voltou.model_used == original.model_used
    assert voltou.prompt_version == original.prompt_version
    assert {e.content_hash for e in voltou.evidences} == {
        EV_DADOS.content_hash,
        EV_STACK.content_hash,
    }


def test_round_trip_do_briefing_com_recomendacoes(session: Session) -> None:
    company = CompanyRepository.upsert(session, _profile())
    session.flush()
    original = Briefing(
        company_name="Acme Saúde Ltda",
        executive_summary="Dado clínico proprietário forte, stack de inferência exposta.",
        maturity=AIMaturity.AI_NATIVE,
        defensibility=_score(),
        priority=PriorityAssessment(
            bucket=PriorityBucket.ABORDAR_AGORA,
            urgency=78.0,
            capacity_to_act=0.7,
            capacity_rationale="Seed recente e CTO técnica.",
            recommended_next_step="Convidar para o Inception e propor benchmark de inferência.",
        ),
        recommendations=[_recommendation()],
        moat_plan="Internalizar a inferência para converter dado clínico em vantagem de custo.",
        conversation_starters=["Qual a latência p95 hoje no fluxo de laudo?"],
        caveats=["Volume de tokens estimado a partir de headcount."],
        markdown="# Acme Saúde\n\nResumo...",
    )
    row = BriefingRepository.save(session, company.id, original)
    session.flush()

    voltou = BriefingRepository.latest(session, company.id)
    assert voltou is not None
    assert voltou.executive_summary == original.executive_summary
    assert voltou.maturity is AIMaturity.AI_NATIVE
    assert voltou.markdown == original.markdown
    assert voltou.caveats == original.caveats
    assert voltou.priority.bucket is PriorityBucket.ABORDAR_AGORA
    assert voltou.total_citations == original.total_citations

    rec = voltou.recommendations[0]
    assert rec.technology == "NVIDIA NIM"
    assert rec.addresses_axis is DefensibilityAxis.STACK_OWNERSHIP
    assert rec.kb_citations[0].rerank_score == 0.94
    assert [e.content_hash for e in rec.company_evidences] == [EV_STACK.content_hash]
    assert row.score_id is not None


def test_recomendacao_liga_evidencia_a_linha_canonica(session: Session) -> None:
    """A associação aponta para `evidences`, não copia o trecho.

    É o que garante que clicar no score na UI leve sempre à mesma URL, mesmo
    depois de a mesma evidência ser citada por três recomendações.
    """
    company = CompanyRepository.upsert(session, _profile())
    session.flush()
    RecommendationRepository.add_many(session, company.id, [_recommendation()])
    session.flush()

    link = session.query(tables.RecommendationEvidence).one()
    evidencia = session.get(tables.Evidence, link.evidence_id)
    assert evidencia is not None
    assert evidencia.content_hash == EV_STACK.content_hash
    assert evidencia.company_id == company.id


# --------------------------------------------------------------------------- #
# Histórico versionado
# --------------------------------------------------------------------------- #


def test_score_mais_recente_vence_o_historico(session: Session) -> None:
    """Reavaliar acrescenta linha; a leitura padrão devolve a última.

    Sem histórico não dá para mostrar evolução — e evolução é o argumento da
    conversa de acompanhamento com a startup.
    """
    company = CompanyRepository.upsert(session, _profile())
    session.flush()

    antigo = ScoreRepository.add(session, company.id, _score("0.1.0-inicial", stack=30.0))
    session.flush()
    # Datas explícitas: dois inserts no mesmo teste caem no mesmo instante e o
    # empate deixaria o teste dependente da ordem de inserção.
    antigo.created_at = datetime.now(UTC) - timedelta(days=30)

    recente = ScoreRepository.add(session, company.id, _score("0.2.0-calibrado", stack=68.0))
    session.flush()

    assert len(ScoreRepository.history(session, company.id)) == 2

    ultimo = ScoreRepository.latest_row(session, company.id)
    assert ultimo is not None
    assert ultimo.id == recente.id
    assert ultimo.weights_version == "0.2.0-calibrado"
    assert ultimo.stack_ownership_score == 68.0

    pydantic = ScoreRepository.latest(session, company.id)
    assert pydantic is not None
    assert pydantic.weights_version == "0.2.0-calibrado"


def test_weights_version_permite_recalcular_sem_re_scraping(session: Session) -> None:
    """Duas versões de pesos convivem sobre as mesmas evidências.

    É o ponto do campo: recalibrar não pode exigir re-scraping nem misturar
    escalas incompatíveis no mesmo gráfico.
    """
    company = CompanyRepository.upsert(session, _profile())
    session.flush()
    ScoreRepository.add(session, company.id, _score("0.1.0-inicial"))
    ScoreRepository.add(session, company.id, _score("0.2.0-calibrado"))
    session.flush()

    versoes = {row.weights_version for row in ScoreRepository.history(session, company.id)}
    assert versoes == {"0.1.0-inicial", "0.2.0-calibrado"}
    assert EvidenceRepository.count(session, company.id) == 3  # nenhuma re-coleta


def test_classificacao_mais_recente_vence_o_historico(session: Session) -> None:
    company = CompanyRepository.upsert(session, _profile())
    session.flush()

    primeira = ClassificationRepository.add(
        session,
        company.id,
        Classification(
            maturity=AIMaturity.INDETERMINADO, confidence=0.2, rationale="Pouca evidência."
        ),
    )
    session.flush()
    primeira.created_at = datetime.now(UTC) - timedelta(days=10)

    ClassificationRepository.add(
        session,
        company.id,
        Classification(
            maturity=AIMaturity.AI_NATIVE, confidence=0.9, rationale="Blog técnico encontrado."
        ),
    )
    session.flush()

    ultima = ClassificationRepository.latest(session, company.id)
    assert ultima is not None
    assert ultima.maturity is AIMaturity.AI_NATIVE


# --------------------------------------------------------------------------- #
# Fila de prioridade
# --------------------------------------------------------------------------- #


def _company_com_score(
    session: Session,
    nome: str,
    site: str,
    *,
    stack: float,
    urgency: float | None,
    bucket: PriorityBucket = PriorityBucket.ABORDAR_AGORA,
) -> tables.Company:
    company = CompanyRepository.upsert(session, _profile(name=nome, website=site))
    session.flush()
    prioridade = (
        None
        if urgency is None
        else PriorityAssessment(
            bucket=bucket,
            urgency=urgency,
            capacity_to_act=0.6,
            capacity_rationale="Rodada recente.",
            recommended_next_step="Agendar diagnóstico técnico.",
        )
    )
    ScoreRepository.add(session, company.id, _score(stack=stack), prioridade)
    session.flush()
    return company


def test_fila_ordena_por_urgencia(session: Session) -> None:
    _company_com_score(session, "Alfa", "https://alfa.com.br", stack=30.0, urgency=40.0)
    _company_com_score(session, "Beta", "https://beta.com.br", stack=20.0, urgency=90.0)
    _company_com_score(session, "Gama", "https://gama.com.br", stack=50.0, urgency=65.0)

    fila = priority_queue(session)
    assert [item.company_name for item in fila] == ["Beta", "Gama", "Alfa"]
    assert fila[0].urgency == 90.0
    assert fila[0].bucket == PriorityBucket.ABORDAR_AGORA.value


def test_fila_usa_o_score_mais_recente_de_cada_empresa(session: Session) -> None:
    """Uma empresa aparece uma vez, com a avaliação atual — não uma vez por avaliação."""
    company = _company_com_score(session, "Alfa", "https://alfa.com.br", stack=10.0, urgency=95.0)
    antigo = ScoreRepository.latest_row(session, company.id)
    assert antigo is not None
    antigo.created_at = datetime.now(UTC) - timedelta(days=60)

    ScoreRepository.add(
        session,
        company.id,
        _score("0.2.0-calibrado", stack=90.0),
        PriorityAssessment(
            bucket=PriorityBucket.CASE_POTENCIAL,
            urgency=12.0,
            capacity_to_act=0.9,
            capacity_rationale="Stack madura.",
            recommended_next_step="Convidar para case público.",
        ),
    )
    session.flush()

    fila = priority_queue(session)
    assert len(fila) == 1
    assert fila[0].bucket == PriorityBucket.CASE_POTENCIAL.value
    assert fila[0].urgency == 12.0
    assert fila[0].weights_version == "0.2.0-calibrado"


def test_fila_inclui_empresa_avaliada_sem_priorizacao(session: Session) -> None:
    """Sem urgência calculada, o risco assume o lugar — some da fila seria pior."""
    _company_com_score(session, "Alfa", "https://alfa.com.br", stack=10.0, urgency=None)
    fila = priority_queue(session)
    assert len(fila) == 1
    assert fila[0].urgency is None
    assert fila[0].commoditization_risk > 0


def test_fila_filtra_por_bucket_e_por_confianca(session: Session) -> None:
    _company_com_score(session, "Alfa", "https://alfa.com.br", stack=30.0, urgency=40.0)
    _company_com_score(
        session,
        "Beta",
        "https://beta.com.br",
        stack=20.0,
        urgency=90.0,
        bucket=PriorityBucket.NUTRIR,
    )

    somente_nutrir = priority_queue(session, bucket=PriorityBucket.NUTRIR)
    assert [i.company_name for i in somente_nutrir] == ["Beta"]

    assert priority_queue(session, min_confidence=0.99) == []
    assert len(priority_queue(session, min_confidence=0.1)) == 2


def test_empresa_sem_score_nao_entra_na_fila(session: Session) -> None:
    """A fila é de decisões possíveis. Sem avaliação, não há o que decidir."""
    CompanyRepository.upsert(session, _profile())
    session.flush()
    assert priority_queue(session) == []


# --------------------------------------------------------------------------- #
# Integridade
# --------------------------------------------------------------------------- #


def test_apagar_empresa_leva_junto_evidencias_e_scores(session: Session) -> None:
    """Cascata explícita: evidência órfã seria uma prova sem sujeito."""
    company = CompanyRepository.upsert(session, _profile())
    session.flush()
    ScoreRepository.add(session, company.id, _score())
    session.flush()

    session.delete(company)
    session.flush()

    assert session.query(tables.Evidence).count() == 0
    assert session.query(tables.DefensibilityScore).count() == 0
    assert session.query(tables.TCOEstimate).count() == 0


def test_migration_inicial_reproduz_o_metadata(tmp_path) -> None:  # noqa: ANN001
    """A migration precisa produzir exatamente o esquema que o código espera.

    O modo de falha real não é a migration quebrar, é ela silenciosamente
    divergir: alguém acrescenta uma coluna em `tables.py`, esquece a revisão, e
    o erro só aparece no Postgres em produção. Aplicar a migration num SQLite
    descartável e comparar coluna a coluna com o metadata pega isso no commit.
    """
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    from radar.config import get_settings

    db_path = tmp_path / "migrada.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    get_settings.cache_clear()
    try:
        cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
        command.upgrade(cfg, "head")

        engine = db.build_engine(f"sqlite:///{db_path}")
        inspector = inspect(engine)
        migrado = {
            nome: {c["name"] for c in inspector.get_columns(nome)}
            for nome in inspector.get_table_names()
            if nome != "alembic_version"
        }
        esperado = {
            nome: {c.name for c in tabela.columns}
            for nome, tabela in tables.Base.metadata.tables.items()
        }
        assert migrado == esperado
        engine.dispose()
    finally:
        os.environ.pop("DATABASE_URL", None)
        get_settings.cache_clear()


@requires_postgres
def test_jsonb_e_usado_no_postgres() -> None:
    """Só o Postgres tem JSONB — em SQLite a variante cai para JSON.

    Este teste confirma que a variante resolve para o tipo certo no banco real,
    o que o SQLite não consegue provar.
    """
    engine = db.build_engine(POSTGRES_URL)
    db.configure(engine)
    db.create_all(engine)
    try:
        from sqlalchemy import inspect

        colunas = {c["name"]: c["type"] for c in inspect(engine).get_columns("companies")}
        assert type(colunas["sector"]).__name__ == "JSONB"
    finally:
        db.drop_all(engine)
        db.reset()
