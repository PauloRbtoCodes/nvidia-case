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

## Arquitetura

```
Consulta → Search Planner → Scraper → Extractor → [Postgres]
                                          │
                    ┌─────────── por empresa (LangGraph Send) ───────────┐
                    │  Classifier → Evidence Validator → Defensibility   │
                    │       Scorer + TCO → RAG NVIDIA → Reranker →       │
                    │       Recommendation → Briefing                    │
                    └────────────────────────────────────────────────────┘
                                          ↓
                              FastAPI (SSE) → Next.js
```

Plano completo em [`docs/plano-arquitetura.md`](docs/plano-arquitetura.md). Decisões de arquitetura em [`docs/adr/`](docs/adr/).

## Stack

| Camada | Tecnologia |
|---|---|
| Orquestração | LangGraph (estado, checkpoints, arestas condicionais, retry) |
| LLM e embeddings | NVIDIA NIM (`build.nvidia.com`) — Llama 3.3 70B, `nv-embedqa-e5-v5` |
| Reranking | Cohere Rerank v3.5 |
| Vector DB | Qdrant (busca híbrida densa + BM25, fusão RRF) |
| Estruturado | PostgreSQL + SQLAlchemy + Alembic |
| Scraping | Tavily (descoberta) · httpx + trafilatura · Playwright (fallback JS) |
| API / Web | FastAPI + SSE · Next.js |
| Observabilidade | Langfuse self-hosted |
| Avaliação | RAGAS · golden dataset · labels manuais do classificador |

## Como rodar

**Pré-requisitos:** Python 3.12+, [uv](https://docs.astral.sh/uv/), Docker, Node 20+.

```bash
cp .env.example .env      # preencha NVIDIA_API_KEY, COHERE_API_KEY, TAVILY_API_KEY
make setup                # dependências Python + Playwright
make up                   # Postgres, Qdrant, Langfuse
make check                # valida chaves e conectividade
make ingest               # popula a base de conhecimento NVIDIA
make run Q="startups brasileiras de IA para saúde"
make api                  # http://localhost:8000/docs
make web                  # http://localhost:3000
```

Chaves gratuitas: [build.nvidia.com](https://build.nvidia.com) (créditos NIM), [cohere.com](https://dashboard.cohere.com/api-keys), [tavily.com](https://app.tavily.com).

> Não é necessária GPU local. O sistema *recomenda* tecnologias NVIDIA; a inferência roda no NIM hospedado — o mesmo caminho que uma startup em estágio inicial seguiria.

## Ética e conformidade

- Somente informação **pública**, com `robots.txt` respeitado, rate limit por domínio e `User-Agent` identificável.
- Nenhuma afirmação sobre uma empresa existe sem `Evidence` apontando para URL e trecho literal.
- **LGPD:** sobre founders, apenas informação profissional pública e diretamente relevante ao diagnóstico técnico. Sem dado sensível, sem contato privado.
- O TCO é **estimativa** a partir de sinais públicos, sempre acompanhada das premissas.

## Entregáveis

| # | Entregável | Onde |
|---|---|---|
| 1 | Pipeline de scraping | `src/radar/scraping/` |
| 2 | Sistema multiagente LangGraph | `src/radar/graph/` |
| 3 | RAG NVIDIA com reranking | `src/radar/rag/` |
| 4 | Motor de recomendação | `src/radar/graph/nodes/recommender.py` |
| 5 | Interface web | `api/` + `web/` |
| 6 | **Diferencial — Defensibility Radar** | `src/radar/scoring/` |
