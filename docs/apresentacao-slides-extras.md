# Slides extras — contexto do problema e stack

> NVIDIA Startup AI Radar · IA Academy × NVIDIA
>
> Dois slides no formato do roteiro principal (`docs/apresentacao.md`). O Slide A
> entra depois do Slide 1, para aprofundar o problema antes de apresentar a
> solução. O Slide B entra depois do Slide 3 (arquitetura), quando a banca já
> entendeu o desenho e quer saber com o que ele foi feito. Somam ~1:30 — se o
> tempo estiver curto, o Slide B é o que a banca técnica mais valoriza.

---

## Slide A — Por que a janela está fechando (0:40)

**No slide:**
- Título: *"O fosso não é o modelo — o modelo virou commodity"*
- Três linhas de mercado, curtas:
  - O que era diferencial em 2023 (chamar uma API boa) hoje é infraestrutura de prateleira
  - Cada camada que o laboratório absorve — agente, busca, voz, memória — apaga uma categoria inteira de produto
  - Sobra fosso onde o laboratório não alcança: **dado que só a empresa tem, workflow que só ela conhece, stack que ela mesma opera, canal que ela já construiu**
- A dor de quem decide, destacada: *"O gerente do Inception tem carteira, não tem fila. Ele sabe quem está na base — não sabe quem está sob ameaça, nem quem ligar primeiro."*

**Roteiro:**
> Vale separar duas coisas que costumam ser ditas juntas. Comoditização do modelo
> não é ameaça a toda startup de IA — é ameaça ao que estava construído em cima
> do modelo e só em cima dele. Onde ainda existe fosso, ele está em quatro
> lugares: dado que só aquela empresa consegue coletar, workflow que ela conhece
> melhor que qualquer generalista, stack que ela opera de verdade e não aluga, e
> distribuição que ela já ganhou. É exatamente por isso que o score tem esses
> quatro eixos — eles não são categorias arbitrárias, são os lugares onde o
> laboratório não chega.
>
> E tem o lado operacional, que é onde o produto vive. O gerente do Inception no
> Brasil não tem problema de lista: ele tem a carteira, tem o CRM, tem o site das
> empresas. O que ele não tem é ordenação. Ele não sabe quais dessas startups
> estão a um keynote de virar redundantes, quão urgente é cada conversa, e — o
> mais difícil — o que dizer numa ligação para não soar como um vendedor genérico
> falando de GPU para quem ainda não tem esse problema.
>
> Descobrir startup é a parte fácil e já resolvida. O que falta é o diagnóstico
> que transforma uma lista numa fila, e a fila numa conversa que se sustenta na
> frente de um founder técnico.

---

## Slide B — A stack, e o motivo real de cada peça (0:50)

**No slide — cada linha é a escolha e o benefício, uma por linha:**
- **LangGraph** → estado tipado e fan-out por `Send`: uma empresa que falha não derruba o lote
- **Nemotron 3 Super 120B + 3.5 Lightning 30B (NVIDIA NIM)** → modelo grande onde o erro é caro, modelo pequeno onde o erro é barato e detectável
- **`nvidia/nemotron-3-embed` (NIM)** → embeddings assimétricos: indexar como *passage*, buscar como *query*
- **Qdrant** → payload index em `technology`/`category`: filtra antes da busca vetorial, não depois
- **BM25 + fusão RRF** → nome de produto ("TensorRT-LLM", "L40S") é match exato, não semântico
- **Cohere Rerank v3.5** → maior ganho de precisão por esforço no pipeline inteiro; sem chave, **bloqueia** em vez de fingir
- **Tavily** → descoberta feita para agente; sem chave, falha alto em vez de devolver "nenhuma startup"
- **FastAPI + SSE** → a varredura leva minutos: o progresso é evento real por nó, não barra decorativa
- **Next.js (App Router)** → o entregável é uma tela que o gerente usa, não um notebook
- **Postgres + SQLAlchemy/Alembic** → sem histórico não existe "o que mudou desde a última varredura"
- **Langfuse self-hosted** → custo e latência por nó; opcional por construção, nunca derruba a pipeline
- Rodapé — **Trade-off:** *cada peça externa entra por uma porta com política de falha explícita — bloquear, degradar avisando, ou seguir sem. Nenhuma degradação silenciosa.*

**Roteiro:**
> Vou pelos motivos que são específicos deste projeto, porque "é rápido e
> escalável" cabe em qualquer stack.
>
> **LangGraph** porque o trabalho é naturalmente um grafo com fan-out: o
> orquestrador descobre as empresas e o `Send` dispara um subgrafo por empresa,
> cada um com estado tipado e limite de tentativas próprio. Os nós de modelo são
> assíncronos e empurram a chamada para uma thread — sem isso, uma chamada
> bloqueante de vinte segundos travaria o event loop e as outras nove empresas
> ficariam na fila em série.
>
> **Nemotron, e não Llama**, foi decisão forçada pelo ambiente e está registrada:
> em setembro o `llama-3.3-70b` perdeu o endpoint gratuito e o `llama-3.1-8b` foi
> deprecado; os substitutos gratuitos do NIM são todos Nemotron — e, num case para
> a NVIDIA, é também a narrativa coerente. São dois modelos de propósito: o Super
> 120B onde o erro é caro — extração, pontuação, recomendação — e o Lightning 30B
> no planejamento de query e na validação de evidência, onde o erro é barato e
> detectado adiante. Gastar o modelo grande em planejar busca é queimar cota de
> quem precisa raciocinar.
>
> Dois ajustes que vieram de bug real contra a API, não de manual. O teto de saída
> está em 8192 tokens porque o padrão da biblioteca, 1024, cortava o JSON do
> extractor no meio e matava duas de cada três empresas — e o modo de falha
> enganava, parecia erro de schema. E o raciocínio explícito do Nemotron está
> desligado, porque com ele ligado o modelo despeja o próprio raciocínio dentro do
> campo de conteúdo e toda saída estruturada falha na validação. Cada falha dessas
> custa uma chamada da cota: gasta e não entrega. Por isso também existe teto de
> concorrência e teto de chamadas por execução — o fan-out vira rajada, e sem teto
> o 429 chega para todas de uma vez.
>
> Na recuperação, **Qdrant** carrega índice de payload em tecnologia e categoria,
> então o filtro acontece antes da busca vetorial — é o que permite a regra do
> case: quando o gap é latência, a busca só olha documentos de inferência, em vez
> de torcer para o ranking semântico não trazer um artigo de Omniverse. **BM25 ao
> lado** porque embedding é bom em paráfrase e ruim em identificador: "TensorRT-LLM"
> e "L40S" são símbolos arbitrários, e busca semântica traz meia página de
> inferência genérica antes do documento certo. A fusão é RRF, que combina
> posições e não scores — cosseno e BM25 não são comparáveis em escala nenhuma, e
> qualquer soma ponderada exigiria calibrar um peso que mudaria a cada fonte nova.
> Depois vem o **rerank da Cohere**, que é o maior ganho de precisão por unidade de
> esforço em todo o pipeline: o retriever otimiza recall sobre trinta candidatos e
> não julga relevância fina; o cross-encoder lê a pergunta e o documento juntos e
> julga.
>
> **Tavily** na descoberta, com uma regra: sem chave ele falha alto. Busca vazia
> por falta de credencial se parece com "nenhuma startup encontrada", e um
> relatório que conclui zero por engano é pior que um sistema que quebra.
>
> Na ponta, **FastAPI com SSE** porque uma varredura leva minutos e nenhum cliente
> HTTP espera isso: o POST dispara e devolve um id, o progresso sai por stream, nó
> a nó — é o que vocês viram na demo. **Next.js** porque o entregável é uma tela
> que o gerente abre cinco minutos antes da ligação. **Postgres** porque o gatilho
> temporal depende de comparar esta execução com a anterior — sem histórico
> persistido, o radar seria uma foto. E **Langfuse** self-hosted para custo e
> latência por nó, montado como camada opcional: sem chave, sem Docker, ou com o
> SDK falhando, ele degrada para um observador nulo e o fluxo segue. Perder um
> lote de scraping porque o container de observabilidade caiu seria trocar
> diagnóstico por fragilidade.
>
> **O trade-off que atravessa a lista:** toda peça externa entra por uma porta com
> política de falha declarada. Sem chave da Cohere, o rerank levanta erro por
> padrão — e no modo alternativo devolve a ordem do RRF com o campo de score
> vazio, porque preencher aquele campo com outro número seria mentir sobre a
> procedência dele. É mais trabalho por integração; em troca, nenhum resultado
> deste sistema é pior do que parece ser.
