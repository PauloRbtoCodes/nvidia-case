# NVIDIA Startup AI Radar — Plano de Arquitetura

> Documento vivo. Toda decisão relevante vira um ADR em `docs/adr/`.
> Última revisão: 2026-08-13

---

## 0. Decisões travadas

| Área | Decisão | Alternativas descartadas |
|---|---|---|
| Infra | Docker Compose local (Qdrant + Postgres + Langfuse) | Cloud gerenciado, Chroma embedded |
| LLM / Embeddings | NVIDIA NIM API (`build.nvidia.com`) | OpenAI, Anthropic |
| Reranking | Cohere Rerank v3 | Reranker NIM, cross-encoder local |
| Frontend | FastAPI + Next.js | Streamlit, HTMX |
| Descoberta | Search API (Tavily/Brave) + scraping alvo | Crawler dedicado por diretório |
| Repo | Monorepo Python com `apps/web` | Dois repositórios |
| Observabilidade | Langfuse self-hosted | LangSmith |
| Avaliação | RAGAS + golden dataset + labels manuais do classificador | Só inspeção manual |
| **Diferencial** | **Defensibility Radar** (score de comoditização com TCO embutido) | Radar temporal, grafo de ecossistema |

---

## 1. Bootstrap do ambiente (bloqueadores)

Estado atual da máquina: Python 3.12.3, git 2.43. **Sem** `pip`, **sem** `venv` funcional, **sem** Docker, **sem** Node, **sem** GPU local.

Três passos precisam ser executados antes de qualquer código:

```bash
# 1. uv — gerencia Python e venvs sem depender de pip/sudo
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Node via nvm — sem sudo
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
nvm install --lts

# 3. Docker — ÚNICO passo que exige sudo (rode você, com senha)
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2
sudo usermod -aG docker $USER   # requer relogin
```

**Ausência de GPU local não é um problema de projeto.** O sistema *recomenda* tecnologias NVIDIA; não precisa executá-las. A inferência roda no NIM hospedado (`build.nvidia.com`), que é justamente o caminho que uma startup em estágio inicial seguiria — o que reforça a narrativa do case em vez de enfraquecê-la.

---

## 2. Arquitetura geral

```
                        ┌──────────────── Next.js (dashboard) ────────────────┐
                        │  busca · fila de prioridade · perfil · briefing PDF │
                        └───────────────────────┬─────────────────────────────┘
                                                │ REST + SSE (stream do grafo)
                        ┌───────────────────────▼─────────────────────────────┐
                        │                   FastAPI                           │
                        └───────────────────────┬─────────────────────────────┘
                                                │
   ┌────────────────────────────── LangGraph (grafo com estado) ──────────────────────────────┐
   │                                                                                          │
   │   [Search Planner] → [Scraper] → [Extractor] ──┐                                         │
   │                          ▲                     │  Send() fan-out por empresa             │
   │                          │ retry c/ queries    ▼                                         │
   │                          │ refinadas    ┌─── subgrafo por empresa ───────────────┐       │
   │                          └──────────────┤ [Classifier] → [Evidence Validator]    │       │
   │                                         │        │              │ confiança baixa│       │
   │                                         │        ▼              └────────────────┼──┐    │
   │                                         │ [Defensibility Scorer + TCO]           │  │    │
   │                                         │        ▼                               │  │    │
   │                                         │ [NVIDIA RAG] → [Reranker]              │  │    │
   │                                         │        ▼                               │  │    │
   │                                         │ [Recommendation] → [Briefing]          │  │    │
   │                                         └────────────────────────────────────────┘  │    │
   └───────────────────────────────────────────────────────────────────────────────┬─────┘    │
                                                                                   └──────────┘
        ┌──────────────┬────────────────┬─────────────────┐
        │  Postgres    │    Qdrant      │    Langfuse     │
        │  empresas,   │  KB NVIDIA     │  traces, custo, │
        │  evidências, │  (vetores)     │  latência       │
        │  checkpoints │                │                 │
        └──────────────┴────────────────┴─────────────────┘
```

Dois níveis de grafo: o externo faz descoberta e distribui empresas via `Send` (map-reduce); o subgrafo processa cada empresa de forma isolada, para que a falha em uma não derrube o lote.

---

## 3. Estrutura do monorepo

```
nvidia-startup-radar/
├─ docker-compose.yml           # qdrant, postgres, langfuse
├─ pyproject.toml               # gerenciado por uv
├─ Makefile                     # make up / ingest / run / eval / test
├─ .env.example
├─ docs/
│  ├─ plano-arquitetura.md      # este arquivo
│  ├─ adr/                      # 1 ADR por decisão de arquitetura
│  └─ diagramas/
├─ src/radar/
│  ├─ config.py                 # Pydantic Settings
│  ├─ models/                   # contratos Pydantic (fonte da verdade)
│  │  ├─ company.py             #   CompanyProfile, Founder, FundingRound
│  │  ├─ evidence.py            #   Evidence(url, trecho, data, confiança)
│  │  ├─ scoring.py             #   DefensibilityScore, AxisScore, TCOEstimate
│  │  └─ recommendation.py      #   Recommendation, Briefing
│  ├─ graph/
│  │  ├─ state.py               # RadarState (TypedDict + reducers)
│  │  ├─ build.py               # montagem do grafo e das arestas condicionais
│  │  └─ nodes/                 # 1 arquivo por agente
│  ├─ scraping/
│  │  ├─ search.py              # Tavily/Brave
│  │  ├─ fetch.py               # httpx → fallback Playwright
│  │  ├─ extract.py             # trafilatura + BeautifulSoup
│  │  ├─ politeness.py          # robots.txt, rate limit, User-Agent
│  │  └─ cache.py               # cache em disco (dev sem custo de rede)
│  ├─ rag/
│  │  ├─ ingest.py  chunking.py  embed.py
│  │  ├─ store.py               # Qdrant
│  │  ├─ hybrid.py              # denso + BM25 → RRF
│  │  ├─ rerank.py              # Cohere
│  │  └─ cite.py                # resposta com citações rastreáveis
│  ├─ scoring/
│  │  ├─ axes.py   tco.py
│  │  └─ weights.yaml           # pesos e premissas versionados
│  ├─ persistence/              # SQLAlchemy + Alembic + repositórios
│  ├─ llm/                      # cliente NIM, registry de modelos, prompts/
│  └─ eval/                     # ragas_suite.py, classifier_eval.py, golden/
├─ api/                         # FastAPI (routers, schemas, SSE)
├─ web/                         # Next.js (App Router, Tailwind)
├─ data/
│  ├─ nvidia_sources.yaml       # URLs da KB (seção 8 do enunciado)
│  └─ golden/                   # perguntas de referência + labels de startups
└─ tests/
```

**Regra:** `src/radar/models/` é a fonte da verdade. Nós do grafo, tabelas do Postgres e schemas da API derivam dele — nada de tipos duplicados divergindo.

---

## 4. Modelo de dados

**Postgres** (estruturado, com histórico):

- `companies` — identidade, site, setor, estágio, headcount estimado
- `evidences` — `(company_id, url, trecho, seletor, coletado_em, hash)` — toda afirmação do sistema aponta para uma linha aqui
- `classifications` — AI-native / AI-enabled / non-AI, confiança, modelo e prompt usados
- `defensibility_scores` — 4 eixos + total + versão dos pesos (permite recalcular sem re-scraping)
- `tco_estimates` — premissas + cenários conservador/médio/agressivo
- `recommendations` — tecnologia, prioridade, complexidade, justificativas, evidências
- `briefings` — markdown renderizado + metadados
- `langgraph_checkpoints` — checkpointer do LangGraph (retomada de execução)

**Qdrant** (`nvidia_kb`): chunk, embedding, e payload com `{fonte, url, produto, categoria, tipo_doc, data}` — o payload permite filtro pré-busca (ex.: só documentos de inferência quando o gap é latência).

---

## 5. Pipeline de scraping (Entregável 1)

Três camadas, da mais barata para a mais cara:

1. **Descoberta** — Search API com queries geradas pelo Search Planner (`site:` nos diretórios da seção 7, termos de setor, sinais como "fine-tuning", "inferência", "modelo proprietário").
2. **Coleta** — `httpx` + `trafilatura` no caminho padrão; `Playwright` apenas quando a página é vazia sem JS. Playwright é lento e frágil: usar como exceção, não como regra.
3. **Sinais específicos** — páginas de carreira (vaga de MLE/infra é o sinal mais forte de stack própria), blog de engenharia, `/pricing`, changelog.

Não-negociáveis: respeitar `robots.txt`, rate limit por domínio, `User-Agent` identificável, cache em disco (não re-raspar o mesmo site em desenvolvimento), e **nenhuma evidência sem URL + trecho literal**.

**LGPD:** coletamos dados institucionais públicos. Sobre founders, apenas informação profissional pública e diretamente relevante (cargo, formação técnica, empresa anterior). Nada de dado pessoal sensível, nada de dado que não esteja publicamente publicado pela própria pessoa em contexto profissional. Registrar a base legal em ADR.

---

## 6. RAG NVIDIA (Entregável 3)

Pipeline conforme o enunciado, com as escolhas concretas:

| Etapa | Escolha | Motivo |
|---|---|---|
| Ingestão | `nvidia_sources.yaml` + Firecrawl/trafilatura | Fontes versionadas, ingestão reprodutível |
| Chunking | Semântico por estrutura (títulos markdown), 512–1024 tokens, overlap 15% | Doc técnico tem hierarquia — respeitar seções preserva contexto |
| Embeddings | `nvidia/nv-embedqa-e5-v5` (NIM) | Treinado para QA/retrieval; coerente com o case |
| Vector store | Qdrant com payload index | Filtro por produto/categoria antes da busca vetorial |
| Lexical | BM25 (`rank_bm25`, índice em disco) | Nome de produto ("TensorRT-LLM") é match lexical exato, não semântico |
| Fusão | Reciprocal Rank Fusion, top-30 | Simples, sem tuning de pesos |
| Rerank | Cohere Rerank v3 → top-5 | Ganho de precisão maior que qualquer ajuste de chunking |
| Geração | NIM (Llama/Nemotron) com citação obrigatória por sentença | Sem citação, a recomendação não é auditável |

**Enriquecimento crítico da KB:** documentação oficial diz *o que* cada tecnologia faz, não *quando recomendá-la*. Para cada produto, escrevemos um card curto — problema resolvido, sinais de que a startup precisa disso, pré-requisitos, complexidade — e ingerimos junto. É isso que faz o RAG responder "qual tecnologia para este gap" em vez de "o que é o Triton".

---

## 7. Diferencial: Defensibility Radar (Entregável 6)

Responde diretamente à pergunta norteadora. Score **0–100**, onde alto = defensável e baixo = vulnerável à comoditização pelos labs.

### Os quatro eixos

| Eixo | Peso | Sinais coletados | Se estiver fraco → recomendação NVIDIA |
|---|---|---|---|
| **Dados proprietários** | 30 | Dataset próprio, integração com sistemas do cliente (ERP/PDV/EMR), dado regulado, loop de feedback, vagas de data engineer | NeMo Curator/Customizer, RAPIDS + cuDF para o pipeline de dados |
| **Profundidade de workflow** | 25 | Integrações nomeadas, certificações, SLA, human-in-the-loop, ações de escrita no sistema do cliente (não só chat) | NIM + agentes, NeMo Guardrails, Blueprints |
| **Distribuição** | 20 | Logos enterprise, marketplace, parcerias de canal, contratos públicos, comunidade | Inception: GTM, co-marketing, conexão com VCs |
| **Domínio da stack** | 25 | Modelo fine-tunado, self-hosting, menção a otimização de inferência, vagas de MLE/infra, "powered by GPT-4" no rodapé (sinal negativo) | NIM, TensorRT-LLM, Triton, benchmark de custo/latência |

Cada eixo carrega **score + confiança separados**. Score 30 com confiança 0,2 significa "não encontramos evidência", não "a startup é fraca" — e a UI precisa deixar isso explícito, senão o sistema produz injustiça com aparência de rigor.

### TCO embutido (quantifica o eixo de stack)

Estima o volume de inferência a partir de sinais públicos (categoria de produto, nº de clientes, headcount), projeta três cenários e compara:

- **Custo atual** — preço por 1M tokens do provedor identificado × volume estimado
- **Custo alternativo** — GPU dedicada com NIM/TensorRT-LLM, usando throughput de referência publicado
- **Saída** — break-even em tokens/mês, economia projetada, ponto em que migrar passa a compensar

Todas as premissas ficam em `scoring/weights.yaml`, versionadas. O output **sempre** rotula os números como estimativa com premissas explícitas — um TCO que finge precisão que não tem destrói a credibilidade da conversa comercial.

### Fila de prioridade

O produto final para o gerente não é uma lista, é uma fila ordenada:

```
urgência = risco_comoditização × capacidade_de_agir
onde capacidade_de_agir = f(funding recente, time técnico, estágio)
```

Startup vulnerável **e** capaz de reagir = conversa urgente. Vulnerável e sem capital = nutrir via comunidade. Já defensável = candidata a case de sucesso.

### Por que isso é o diferencial certo

Classificar AI-native/AI-enabled/non-AI descreve o presente. O score de comoditização responde à pergunta do case, prioriza a carteira do gerente, e transforma o motor de recomendação em algo causal — a tecnologia recomendada sai do eixo mais fraco, não de uma regra solta. E inverte a conversa comercial: em vez de "sua startup é um wrapper", entrega *"aqui está o caminho para deixar de ser, e a NVIDIA cobre a parte técnica dele"*.

---

## 8. Motor de recomendação (Entregável 4)

Híbrido em três estágios, para não depender só do LLM nem só de regras:

1. **Gate por regras** — eixo fraco + setor + sinais técnicos filtram o universo de tecnologias candidatas. Determinístico e auditável.
2. **RAG** — recupera evidência da KB sobre cada candidata (o que resolve, pré-requisitos, complexidade).
3. **LLM** — redige justificativa técnica e de negócio *ancorada* nos trechos recuperados, com citação obrigatória.

Saída por recomendação, conforme o enunciado: tecnologia · justificativa técnica · justificativa de negócio · prioridade · complexidade de implementação · próxima ação sugerida · evidências.

Guardrail: recomendação sem evidência recuperada é bloqueada, não "melhor esforço". É melhor recomendar três tecnologias com fundamento do que oito com achismo.

---

## 9. API e Frontend (Entregável 5)

**FastAPI:** `POST /searches` (dispara o grafo) · `GET /searches/{id}/stream` (SSE com o progresso nó a nó) · `GET /companies` (fila de prioridade, filtros) · `GET /companies/{id}` · `POST /companies/{id}/briefing` · `GET /briefings/{id}.pdf`

**Next.js:** quatro telas — busca com progresso ao vivo dos agentes · fila de prioridade ordenada por urgência · perfil da empresa com o radar de 4 eixos e evidências clicáveis · briefing exportável.

O radar de eixos com evidência clicável é o que faz o projeto parecer ferramenta de trabalho e não demo — o gerente clica no score e vê a URL e o trecho que o justificam.

---

## 10. Avaliação (as três frentes escolhidas)

1. **RAGAS** sobre golden dataset de ~30 perguntas NVIDIA — faithfulness, context precision, answer relevancy. Roda em CI, regressão quebra o build.
2. **Golden dataset versionado** em `data/golden/` — perguntas + respostas de referência + fontes esperadas.
3. **Labels manuais de ~50 startups** — classificação e eixos rotulados à mão para medir precisão do classificador e calibrar os pesos do score. É o que dá credibilidade ao diferencial: sem isso o Defensibility Score é opinião do LLM com aparência de métrica.

Langfuse traça cada execução do grafo com custo e latência por nó — útil para a banca e para achar o gargalo real.

---

## 11. Cronograma (4 semanas)

| Semana | Foco | Entregável |
|---|---|---|
| **1** | Bootstrap, compose, modelos Pydantic, migrations, cliente NIM, pipeline de scraping + Search Planner/Scraper/Extractor | **E1** |
| **2** | Ingestão da KB NVIDIA, cards de recomendação, busca híbrida, rerank, citações, RAGAS + golden dataset | **E3** |
| **3** | Grafo LangGraph completo, Classifier, Evidence Validator, Defensibility Scorer + TCO, motor de recomendação, Briefing | **E2, E4, E6** |
| **4** | FastAPI + Next.js, export PDF, labels manuais e calibração, Langfuse, README e documentação | **E5** |

Commits diários com escopo pequeno. Cada decisão de arquitetura vira um ADR no mesmo commit da implementação — o histórico do repo é parte da avaliação.

---

## 12. Riscos e mitigações

| Risco | Mitigação |
|---|---|
| Anti-bot / mudança de layout nos diretórios | Search API como caminho primário; seed list de fallback; cache em disco |
| Extração alucinada pelo LLM | Extração estruturada com Pydantic + validação de que todo campo tem evidência com URL |
| Rate limit / cota do NIM | Cache de embeddings, batching, circuit breaker, modelo menor para tarefas simples |
| Score com viés (startup discreta parece fraca) | Confiança separada do score; UI diferencia "sem evidência" de "evidência negativa" |
| TCO com falsa precisão | Três cenários, premissas versionadas e visíveis, rótulo de estimativa |
| Escopo do frontend consumir a semana 4 | API pronta na semana 3; UI com 4 telas fixas, sem expansão de escopo |
| LGPD sobre dados de founders | Só dado profissional público; base legal em ADR; sem dado sensível |

---

## 13. Decisões ainda abertas

1. Search API definitiva: **Tavily** (melhor para agentes, free tier generoso) ou **Brave** (mais barato em escala).
2. Modelo NIM para os agentes: `llama-3.3-70b-instruct` (mais forte em extração estruturada) vs `nemotron` (narrativa NVIDIA mais coesa).
3. Escopo geográfico e setorial do primeiro lote de startups.
4. Se o radar temporal (re-scraping agendado + alertas) entra como extensão caso sobre tempo na semana 4.
