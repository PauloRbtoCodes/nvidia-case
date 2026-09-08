# NVIDIA Startup AI Radar

Plataforma multi-agente que mapeia startups brasileiras com potencial AI-native, diagnostica a maturidade técnica delas a partir de fontes públicas e recomenda tecnologias NVIDIA adequadas ao perfil de cada empresa — gerando um briefing executivo para o time de Startups & VCs da NVIDIA no Brasil.

## O problema

Os grandes labs de IA subiram na cadeia de valor. O que era API de modelo fundacional virou agente, busca, voz, código e produto final para empresas. Startups que se posicionam apenas como *wrappers* de LLM podem ser substituídas por uma funcionalidade nativa lançada num keynote.

A pergunta que este projeto responde não é "quais startups brasileiras usam IA" — é **"quais estão expostas à comoditização, quão urgente é a conversa, e o que a NVIDIA pode oferecer para que deixem de estar"**.

## O diferencial: Defensibility Radar

Classificar uma empresa como AI-native / AI-enabled / non-AI descreve o presente. O **Defensibility Score** mede o risco de comoditização em quatro eixos e transforma o diagnóstico em ação:

| Eixo | Peso | Pergunta | Se fraco → NVIDIA |
|---|---|---|---|
| Dados proprietários | 30% | Tem dado que o lab não consegue comprar? | NeMo Curator/Customizer, RAPIDS, cuDF |
| Profundidade de workflow | 25% | Chat genérico ou escreve no ERP do cliente? | NIM, NeMo Guardrails, Blueprints |
| Domínio da stack | 25% | Controla custo e latência ou repassa API? | NIM, TensorRT-LLM, Triton |
| Distribuição | 20% | Canal que um anúncio de feature não replica? | Inception (GTM, VCs, comunidade) |

Três propriedades que sustentam o desenho:

1. **Score e confiança são números separados.** Startup discreta produz pouca evidência. Misturar "não encontramos sinal" com "o sinal é ruim" gera injustiça com aparência de rigor — por isso a UI mostra "evidência insuficiente" em vez de nota baixa.
2. **A recomendação é causal.** A tecnologia sugerida sai do eixo mais fraco (ponderado por peso *e* confiança), não de uma regra solta por setor.
3. **O TCO quantifica o eixo técnico.** Comparação entre custo em API externa e stack NVIDIA auto-hospedada, em três cenários, com todas as premissas visíveis. Quando migrar não compensa, o sistema diz isso — recomendar GPU dedicada para quem usa 5M tokens/mês queima a credibilidade do programa.

E a fila de prioridade ordena o trabalho do gerente: `urgência = risco × capacidade de agir`. Vulnerável e capitalizada é conversa urgente; vulnerável e sem capital é nutrição via comunidade.

### Gatilho temporal — o que separa radar de foto

O nome é "radar", e radar detecta **mudança**. O sistema compara cada execução com a anterior da mesma empresa e emite o diff, não só o estado: *"a Fintech X publicou vaga de MLOps citando inferência própria — o eixo de stack move de terceirizado para em transição"*. É o que dá utilidade recorrente e o que vira a primeira frase da conversa comercial.

O invariante do ADR 0002 estende-se ao eixo do tempo: **a ausência de um sinal numa execução posterior nunca é prova de que ele deixou de existir** — pode ser scraping falho, página fora do ar, layout mudado. Sumiço de evidência gera queda de *confiança*, nunca queda de *score*. Uma re-execução de monitoramento força refresh nas fontes de sinal (carreiras, blog técnico) e mantém cache no institucional, que muda pouco.

## Arquitetura

```
Consulta → Search Planner → Scraper → Extractor → [Postgres]
                                          │
                    ┌─────────── por empresa (LangGraph Send) ────────────┐
                    │  Classifier → Evidence Validator → Defensibility    │
                    │     Scorer + TCO → Compare (diff temporal) →        │
                    │     RAG NVIDIA → Reranker → Recommendation →        │
                    │     Briefing                                        │
                    └────────────────────────────────────────────────────┘
                                          ↓
                              FastAPI (SSE) → Next.js
```

Dois níveis de grafo: o externo faz descoberta e distribui empresas via `Send` (map-reduce); o subgrafo processa cada empresa isolada, para que a falha em uma não derrube o lote. Plano completo em [`docs/plano-arquitetura.md`](docs/plano-arquitetura.md); decisões técnicas em [`docs/adr/`](docs/adr/); decisões de produto, escopo e trade-offs em [`docs/governanca-e-tradeoffs.md`](docs/governanca-e-tradeoffs.md).

## Stack

| Camada | Tecnologia |
|---|---|
| Orquestração | LangGraph (estado, checkpoints, arestas condicionais, retry em dois níveis) |
| LLM e embeddings | NVIDIA NIM (`build.nvidia.com`) — Nemotron 3 Super 120B (raciocínio) · Nemotron 3.5 Lightning 30B (tarefas mecânicas) · `nemotron-3-embed-1b` |
| Reranking | Cohere Rerank v3.5 |
| Vector DB | Qdrant (busca híbrida densa + BM25, fusão RRF) |
| Estruturado | PostgreSQL + SQLAlchemy + Alembic |
| Scraping | Tavily (descoberta) · httpx + trafilatura · Playwright (fallback JS) |
| API / Web | FastAPI + SSE · Next.js (App Router) |
| Observabilidade | Langfuse self-hosted |
| Avaliação | golden dataset + labels manuais do classificador (scaffold; RAGAS planejado) |

> **Por que Nemotron e não Llama:** em 2026-09-09, `llama-3.3-70b` perdeu o endpoint gratuito do NIM e `llama-3.1-8b` / `nv-embedqa-e5-v5` foram deprecados. Os substitutos gratuitos são todos Nemotron — que, num case para a NVIDIA, também é a narrativa mais coerente. O `nim_disable_thinking` desliga o raciocínio explícito do Nemotron, que sem isso despeja o próprio pensamento dentro de `content` e quebra toda saída estruturada.

## Mapa: conceito → arquivos

A estrutura é cortada por **preocupação técnica** (`models/`, `scraping/`, `rag/`,
`llm/`, `scoring/`, `persistence/`), não por fatia de negócio. A fronteira que isso
preserva é "toca a rede / não toca" — `scoring/` é aritmética pura e testável sem
mock nenhum, o que é o que permite a suíte rodar offline. O custo é dispersão: um
conceito atravessa várias pastas. Este mapa é a compensação escolhida (a análise
completa está em [`docs/governanca-e-tradeoffs.md`](docs/governanca-e-tradeoffs.md), §9).

| Conceito | Contrato | Lógica | Nó do grafo | Prompt | Tabela |
|---|---|---|---|---|---|
| Evidência | `models/evidence.py` | — | `graph/nodes/validator.py` | `evidence_validator_v1.md` | `evidences` |
| Perfil da empresa | `models/company.py` | `scraping/extract.py` | `graph/nodes/extractor.py` | `extractor_v1.md` | `companies` |
| Classificação AI-native | `models/company.py` | — | `graph/nodes/classifier.py` | `classifier_v1.md` | `classifications` |
| **Defensibility Score** | `models/scoring.py` | `scoring/weights.py` + `weights.yaml` | `graph/nodes/scorer.py` | `defensibility_scorer_v1.md` | `defensibility_scores` |
| TCO | `models/scoring.py` | `scoring/tco.py` | `graph/nodes/scorer.py` | — | `tco_estimates` |
| Fila de prioridade | `models/scoring.py` | `scoring/priority.py` | `graph/build.py` (consolidate) | — | `priority_assessments` |
| Recomendação | `models/recommendation.py` | `rag/hybrid.py` + `rag/rerank.py` | `graph/nodes/recommender.py` | `recommender_v1.md` | `recommendations` |
| Briefing | `models/recommendation.py` | `graph/nodes/briefing.py` (markdown determinístico) | `graph/nodes/briefing.py` | `briefing_v1.md` | `briefings` |
| **Gatilho temporal** | `scoring/delta.py` (`ScoreDelta`) | `scoring/delta.py` (comparador puro) | `graph/nodes/compare.py` | — | (derivado do histórico de `defensibility_scores`) |
| Busca e descoberta | — | `scraping/search.py` | `planner.py`, `discovery.py` | `search_planner_v1.md` | — |
| Cota e cache de LLM | — | `llm/quota.py`, `llm/cache.py` | (em `llm/client.py`) | — | — |

Regra que atravessa a tabela: **`models/` é a fonte da verdade.** Tabelas do
Postgres e schemas da API derivam dele; nada de tipo duplicado divergindo.

## Como rodar

**Pré-requisitos:** Python 3.12+, [uv](https://docs.astral.sh/uv/), Docker, Node 20+.

```bash
cp .env.example .env      # preencha NVIDIA_API_KEY, COHERE_API_KEY, TAVILY_API_KEY
make setup                # dependências Python + Playwright
make up                   # Postgres, Qdrant, Langfuse (docker compose)
make migrate              # cria o schema no Postgres
make check                # valida .env e conectividade com a infra
make ingest               # popula a base de conhecimento NVIDIA no Qdrant
make run Q="startups brasileiras de IA para saúde"
make api                  # http://localhost:8000/docs
make web                  # http://localhost:3000
make test                 # 346 testes, todos offline (dublês injetados)
```

Chaves gratuitas: [build.nvidia.com](https://build.nvidia.com) (créditos NIM), [cohere.com](https://dashboard.cohere.com/api-keys), [tavily.com](https://app.tavily.com). Sem `COHERE_API_KEY` o rerank cai para a ordem do RRF; sem `TAVILY_API_KEY` a descoberta fica indisponível e o grafo roda a partir de URLs semente. A API sobe em modo degradado e o `make check` diz exatamente o que falta.

> Não é necessária GPU local. O sistema *recomenda* tecnologias NVIDIA; a inferência roda no NIM hospedado — o mesmo caminho que uma startup em estágio inicial seguiria.

**A suíte roda 100% offline.** `llm`, `search`, `fetcher` e `retriever` são injetados via `NodeDeps` e substituídos por dublês nos testes — foi o que permitiu construir contra as APIs reais indisponíveis durante o desenvolvimento (a análise dessa escolha está em [`docs/governanca-e-tradeoffs.md`](docs/governanca-e-tradeoffs.md) §9).

## Ética e conformidade

- Somente informação **pública**, com `robots.txt` respeitado, rate limit por domínio e `User-Agent` identificável.
- Nenhuma afirmação sobre uma empresa existe sem `Evidence` apontando para URL e trecho literal.
- **LGPD:** sobre founders, apenas informação profissional pública e diretamente relevante ao diagnóstico técnico. Sem dado sensível, sem contato privado.
- O TCO é **estimativa** a partir de sinais públicos, sempre acompanhada das premissas.

## Entregáveis

| # | Entregável | Onde | Estado |
|---|---|---|---|
| 1 | Pipeline de scraping | `src/radar/scraping/` | completo |
| 2 | Sistema multiagente LangGraph | `src/radar/graph/` (dois níveis, fan-out por empresa) | completo |
| 3 | RAG NVIDIA com reranking | `src/radar/rag/` (denso + BM25 → RRF → Cohere) | completo |
| 4 | Motor de recomendação | `src/radar/graph/nodes/recommender.py` (gate → RAG → LLM) | completo |
| 5 | Interface web | `api/` (FastAPI + SSE) · `web/` (Next.js, 3 telas) | completo |
| 6 | **Diferencial — Defensibility Radar + gatilho temporal** | `src/radar/scoring/` | completo |
| — | Suíte de avaliação | `data/golden/` (scaffold), `src/radar/eval/` | planejado — ver [governança §5](docs/governanca-e-tradeoffs.md) |

## Estado

346 testes passando (1 skip: o teste de JSONB que exige `TEST_DATABASE_URL`), `ruff check` limpo. Backend, RAG, grafo, API e as três telas do frontend rodam de ponta a ponta. O histórico de commits — parte da avaliação do case — registra a evolução ao longo do mês, com um commit por etapa e ADRs no mesmo commit da implementação que os justifica.

Limitações assumidas conscientemente (detalhadas em [`docs/governanca-e-tradeoffs.md`](docs/governanca-e-tradeoffs.md) §7): o score não tem calibração empírica dentro do prazo (mitigado pela confiança separada e pelas premissas em YAML versionado); o registro de execuções da API vive em memória (adequado a um worker); a exportação é markdown, não PDF.
