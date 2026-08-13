"""Estado do grafo LangGraph.

O sistema tem dois níveis de grafo, e a separação existe por uma razão concreta:
uma startup cujo site derruba o extrator não pode derrubar o lote inteiro.

- `RadarState` — grafo externo. Faz descoberta e distribui empresas via `Send`
  (map-reduce). Acumula resultados e falhas.
- `CompanyState` — subgrafo por empresa. Isolado, com seu próprio orçamento de
  retry e sua própria trilha de erro.

Reducers de lista usam `operator.add` para que o fan-out do `Send` consiga
mesclar resultados paralelos sem corrida de escrita.
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from radar.models.company import Classification, CompanyProfile
from radar.models.evidence import Evidence
from radar.models.recommendation import Briefing, Recommendation, RetrievedChunk
from radar.models.scoring import DefensibilityScore, PriorityAssessment


class SearchPlan(TypedDict, total=False):
    """Saída do Search Planner: consulta do usuário → plano de busca executável."""

    queries: list[str]
    """Da mais específica para a mais genérica — a ordem importa no dedupe por domínio."""

    priority_domains: list[str]
    """Diretórios e portais a privilegiar (Distrito, Cubo, Brazil Journal...)."""

    sector_hint: str | None
    max_companies: int


class NodeFailure(TypedDict):
    """Falha registrada sem interromper o lote.

    Guardamos falhas em vez de propagar exceção porque o valor do sistema está em
    processar N empresas: uma que falhe deve aparecer como lacuna explícita no
    relatório, não como execução abortada.
    """

    node: str
    company: str | None
    error: str
    recoverable: bool


class CompanyState(TypedDict, total=False):
    """Estado do subgrafo de uma única empresa."""

    # Entrada
    company_name: str
    seed_urls: list[str]
    search_plan: SearchPlan

    # Coleta e extração
    raw_pages: Annotated[list[dict], operator.add]
    evidences: Annotated[list[Evidence], operator.add]
    profile: CompanyProfile | None

    # Diagnóstico
    classification: Classification | None
    validation_notes: list[str]
    refetch_queries: list[str]
    """Queries que o Evidence Validator pediu para preencher lacunas."""

    defensibility: DefensibilityScore | None
    priority: PriorityAssessment | None

    # Recomendação
    candidate_technologies: list[str]
    """Gate determinístico vindo do score — restringe o RAG antes de consultá-lo."""

    rag_chunks: Annotated[list[RetrievedChunk], operator.add]
    recommendations: list[Recommendation]
    briefing: Briefing | None

    # Controle de fluxo
    scrape_attempts: int
    """Orçamento de re-coleta. Sem teto, evidência insuficiente vira loop infinito."""

    failures: Annotated[list[NodeFailure], operator.add]
    trace_id: str | None


class RadarState(TypedDict, total=False):
    """Estado do grafo externo."""

    # Entrada do usuário
    query: str
    max_companies: int

    # Planejamento e descoberta
    search_plan: SearchPlan
    discovered: list[dict]
    """Candidatas brutas da search API: url, título, snippet."""

    # Fan-out
    company_results: Annotated[list[CompanyState], operator.add]

    # Saída consolidada
    briefings: Annotated[list[Briefing], operator.add]
    skipped: Annotated[list[dict], operator.add]
    """Empresas descartadas cedo (non_ai) com o motivo — economiza RAG e explica a lacuna."""

    failures: Annotated[list[NodeFailure], operator.add]
    trace_id: str | None


#: Teto de re-coleta por empresa. Duas tentativas cobrem o caso comum (o site
#: principal não tinha o sinal, a página de carreiras tinha) sem transformar
#: evidência genuinamente ausente em consumo infinito de quota.
MAX_SCRAPE_ATTEMPTS = 2

#: Abaixo desta cobertura de evidência, o perfil volta para re-coleta em vez de
#: seguir para o scorer. Diagnosticar com quase nada produz score confiante e
#: errado, que é pior do que admitir a lacuna.
MIN_EVIDENCE_COVERAGE = 0.4
