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
from radar.scoring.delta import ScoreDelta


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
    kind: str
    """`mundo` ou `bug`.

    O grafo trata falha como dado justamente porque o mundo externo e hostil —
    site atras de Cloudflare, JSON malformado do LLM, 429 da API. Mas o mesmo
    `except Exception` engolia `TypeError` e `AttributeError`, e um bug de
    programacao saia no relatorio com a mesma cara de "o mundo falhou": o lote
    terminava com "sucesso parcial" e ninguem procurava a causa no proprio codigo.

    A distincao nao muda o fluxo — o no continua devolvendo estado incompleto e o
    roteador continua saindo para END. Muda quem le o relatorio: `bug` e para
    corrigir aqui, `mundo` e para tolerar.
    """


class CompanyState(TypedDict, total=False):
    """Estado do subgrafo de uma única empresa."""

    # Entrada
    company_name: str
    seed_urls: list[str]
    search_plan: SearchPlan

    # Coleta e extração
    raw_pages: Annotated[list[dict], operator.add]
    evidences: Annotated[list[Evidence], operator.add]

    job_titles: list[str]
    """Vagas técnicas lidas do HTML pelo Scraper, antes de qualquer LLM.

    Fica fora do perfil porque o Extractor ainda não rodou quando são coletadas.
    Sem reducer de propósito: o nó de coleta funde a lista nova com a antiga, e
    `operator.add` acumularia o mesmo título a cada re-coleta.
    """

    profile: CompanyProfile | None

    # Diagnóstico
    classification: Classification | None
    validation_notes: list[str]
    refetch_queries: list[str]
    """Queries que o Evidence Validator pediu para preencher lacunas."""

    grounding_ratio: float | None
    """Fração dos campos auditados com veredito `supported`. Vai para os caveats."""

    requires_recollection: bool
    """Decisão do validador já resolvida contra o orçamento de retry.

    A aresta condicional só lê este booleano em vez de refazer a conta: a regra
    de parada mora num lugar só (`nodes.validator.precisa_recoletar`), senão o
    teto de tentativas passa a existir em duas versões que divergem na primeira
    manutenção.
    """

    defensibility: DefensibilityScore | None
    priority: PriorityAssessment | None

    score_delta: ScoreDelta | None
    """Diff contra a execução anterior desta empresa, quando há uma.

    Escrito pelo nó `compare` (entre `score` e `rag`). `None` na primeira vez que
    a empresa é vista, sem banco, ou se a leitura do histórico falhou — nenhum
    desses casos interrompe o subgrafo. O briefing consome isto para o talk
    track; a fila da API deriva o seu próprio diff do histórico persistido.
    """

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

    queue: list[str]
    """Nomes das empresas em ordem decrescente de urgência — a fila de trabalho.

    Índice sobre `briefings`, não cópia: aquela chave usa `operator.add` para
    receber o fan-out, então reescrevê-la ordenada a concatenaria consigo mesma.
    Guardar a ordem separada da lista custa uma linha e evita duplicar cada
    briefing no checkpoint.
    """

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
