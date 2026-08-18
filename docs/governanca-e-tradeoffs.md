# Governança do produto e decisões de trade-off

- **Status:** vigente
- **Data:** 2026-09-09
- **Relacionado:** ADR 0001–0005, `docs/plano-arquitetura.md`, `CLAUDE.md`

> Os ADRs registram decisões **técnicas**: por que Qdrant, por que score e confiança
> separados. Este documento registra as decisões de **produto e escopo** — a quem o
> sistema serve, o que ele deliberadamente não faz, e o que estamos trocando por quê.
> Um ADR responde "como construímos". Este arquivo responde "por que vale construir,
> e o que aceitamos perder".

---

## 1. Correção de premissa: quem é o cliente

O plano original descrevia o destinatário como "time de Startups & VCs da NVIDIA".
Na prática esse time foi tratado, no desenho dos entregáveis, como se fosse um time
de vendas de licença — e não é.

**O Inception é um programa gratuito de recrutamento e nutrição de startups.** Não há
contrato para fechar no fim da conversa. Isso muda o que o sistema precisa produzir:

| Se a persona fosse… | O sistema entregaria… |
|---|---|
| Vendedor de licença | Proposta comercial, TCO como argumento de fechamento |
| **Gerente de programa (real)** | **A quem falar, por que agora, e o que dizer sem parecer genérico** |

A métrica dessa pessoa não é receita direta. É volume e qualidade de startups
relevantes entrando no programa, adotando a stack, e — no horizonte longo —
consumindo GPU via nuvem ou parceiro. O produto do sistema, portanto, **não é um
documento. É uma decisão de a quem dedicar a próxima hora.**

### Consequência de desenho

O briefing executivo deixa de ser o entregável-estrela e passa a ser a **camada de
auditoria**: a prova de que toda afirmação tem fonte. Continua sendo essencial — é o
que sustenta a tese de "nenhuma alucinação" — mas é prova de rigor, não o artefato
que a persona consome diariamente.

---

## 2. Lacunas identificadas na proposta

Levantadas em revisão de produto, ordenadas por severidade.

### 2.1 O nome é "Radar", o comportamento é "foto" — **crítico**

O sistema roda um lote, produz uma lista, e termina. Radar detecta **mudança**: a
startup levantou rodada, abriu três vagas pedindo CUDA, publicou post dizendo que a
conta de API estourou. É a mudança que cria o *momento* da conversa.

Sem eixo temporal, o gerente roda uma vez, tem uma lista, e na semana seguinte o
sistema não tem função. É a diferença entre uma ferramenta e um relatório.

### 2.2 O score não tem chão empírico — **alto**

`weights.yaml` é uma opinião bem argumentada, não uma calibração. `eval/` está vazio.
"O que significa 72?" é a primeira pergunta hostil de qualquer avaliação, e hoje a
resposta é conceitual.

### 2.3 O julgamento humano nunca entra — **alto**

O gerente vai discordar do sistema em parte dos casos, e essa discordância é o dado
mais valioso disponível. Hoje ela se perde. Como os pesos já vivem em YAML
versionado, o custo de capturá-la é baixo.

### 2.4 Não há estimativa de tamanho da oportunidade — **médio**

O TCO calcula o que a **startup** economiza. Não diz o que ela representa para a
NVIDIA. Duas empresas com score idêntico, uma consumindo 5M tokens/mês e outra 800M,
ocupam hoje a mesma posição na fila. Não deveriam.

### 2.5 Cobertura desconhecida — **médio**

O sistema acha N startups de um universo cujo tamanho ninguém mede. Sem denominador,
não se sabe se o radar varre o ecossistema ou uma esquina dele.

### 2.6 Nada rodou contra API real — **crítico, e já conhecido**

Registrado no `CLAUDE.md` desde 14/08. Em termos de produto é o risco número um: um
sistema que não roda ao vivo não convence, por melhor que seja a arquitetura.

### 2.7 Risco de leitura "isso é uma planilha cara" — **transversal**

A crítica mais perigosa que o projeto pode receber. Ela só é respondida por
capacidades que planilha não tem: detecção de mudança ao longo do tempo e conversa
personalizada com fonte citada. Nenhuma das duas existe hoje.

---

## 3. Decisões tomadas

### 3.1 ENTRA — Gatilho temporal (o "por que agora")

O sistema passa a comparar execuções e emitir o **diff**, não só o estado:

> 3 mudanças esta semana — a Fintech X publicou vaga de MLOps citando inferência
> própria; isso move o eixo de stack de `terceirizado` para `em transição`.

**Por que esta primeiro:** é a única mudança que dá ao sistema utilidade na segunda
semana de uso, e honra o nome do produto. A infraestrutura já existe em ~70%
(scraping com cache, persistência, histórico de score); o que falta é o eixo de
tempo no modelo de dados e um nó de comparação.

### 3.2 ENTRA — Talk track, com "o que NÃO pitchar"

O briefing descreve. O gerente precisa da **conversa**: abertura ancorada numa
evidência específica, as duas objeções prováveis do founder, e o que deliberadamente
não recomendar.

Os 13 cards em `data/nvidia_cards/` já têm a seção "Quando NÃO recomendar" — hoje ela
serve ao guardrail interno. Virada para fora, é o ativo mais forte do projeto:

> "Pelo volume de vocês, migrar ainda não compensa. Quando passarem de X, é outra
> conversa."

Um gerente que abre assim ganha credibilidade com founder técnico de imediato.
**Honestidade calibrada como instrumento de relacionamento** é o diferencial que
quase nenhum sistema de lead scoring tem, e ele já está meio construído aqui.

### 3.3 ENTRA (barato) — Oportunidade na fila de prioridade

A fila hoje é `urgência = risco × capacidade de agir`. Passa a considerar um terceiro
fator de **tamanho** (estimativa grosseira de volume de inferência, derivada dos
sinais já coletados). Mesmo imprecisa, muda a ordem de forma defensável, e reaproveita
o motor de TCO que já existe.

### 3.4 FICA PARA DEPOIS — Loop de feedback humano

Desenho definido, implementação fora do escopo do mês: botão "concordo / discordo +
motivo" no perfil, alimentando recalibração de `weights.yaml`.

**Por que fica de fora:** só produz valor com uso real acumulado, que não haverá
dentro do prazo. Entra como trabalho futuro documentado — o que já responde
parcialmente à crítica 2.3 sem custar semana de implementação.

### 3.5 FICA PARA DEPOIS — Visão de portfólio do ecossistema

Mapa agregado: onde a vulnerabilidade se concentra por setor, quais eixos são fracos
no Brasil. Valor estratégico alto (serve ao nível acima da persona), mas é uma
**quarta tela** num frontend que ainda tem zero telas prontas.

### 3.6 REBAIXADO — Briefing longo como entregável-estrela

Continua no escopo e continua obrigatório. Muda de posição narrativa: passa a ser
apresentado como camada de auditoria e rastreabilidade, não como o produto.

---

## 4. Trade-offs explícitos

| Escolha | O que ganhamos | O que abrimos mão | Quando reconsiderar |
|---|---|---|---|
| Gatilho temporal antes de novas telas | O sistema tem uso recorrente e o nome se justifica | Frontend fica mais apertado no cronograma | Se o eixo temporal não couber em ~4 dias, corta-se para diff de score apenas, sem narrativa |
| Talk track antes de calibração do score | Responde à crítica "é uma planilha cara" | O score segue sem validação empírica no mês | Se sobrar semana, `classifier_eval.py` contra os ~50 labels manuais vem antes de qualquer feature nova |
| Execução real com 5 empresas antes de qualquer feature nova | Demo ao vivo, risco número um eliminado | 3–4 dias que não viram funcionalidade | Não reconsiderar. É pré-requisito, não opção |
| Estimativa grosseira de oportunidade | Fila ordenada de forma mais defensável | Número impreciso exposto na UI | Mitigado exibindo faixa (ex.: `10M–50M tokens/mês`) e as premissas, nunca ponto único |
| Feedback humano fora do escopo | Foco no que demonstra dentro do prazo | Crítica 2.3 fica só documentada | Primeira coisa a entrar se o projeto tiver vida além do case |
| Portfólio fora do escopo | Frontend entrega 3 telas boas em vez de 4 medianas | Perde-se o argumento "serve ao chefe da persona" | Entra se as 3 telas ficarem prontas com folga |
| Briefing rebaixado a camada de auditoria | Narrativa fica alinhada com a persona real | Perde-se o entregável mais "visível" como estrela | Não reconsiderar. O entregável continua existindo |

### Trade-off que NÃO fazemos

Nenhuma das mudanças acima pode quebrar os três invariantes travados em `tests/`
(score ≠ confiança, recomendação causal, nenhuma afirmação sem evidência). O gatilho
temporal em particular tem uma armadilha: **a ausência de um sinal numa execução
posterior não é evidência de que o sinal deixou de existir** — pode ser scraping
falho, página fora do ar, layout mudado. Diff de score só é emitido quando há
evidência nova positiva; sumiço de evidência gera queda de confiança, nunca queda de
score. É o invariante 1 aplicado ao eixo do tempo.

---

## 5. Escopo congelado e ordem de execução

Prazo remanescente curto. Ordem, com critério de corte:

1. **Execução real ponta a ponta com 5 empresas.** Depende de Docker (`sudo`) e das
   três chaves. Pré-requisito absoluto.
2. **Gatilho temporal** — eixo de tempo no modelo, nó de comparação, diff no output.
3. **Talk track** — extensão do nó de briefing, consumindo a seção "Quando NÃO
   recomendar" dos cards.
4. **Frontend, 3 telas** — busca com progresso ao vivo (SSE), fila de prioridade com
   o gatilho ao lado, perfil com radar de 4 eixos e evidências clicáveis.
5. **Oportunidade na fila** — se couber.
6. **`eval/`** — o que couber; `classifier_eval.py` tem prioridade sobre RAGAS por ser
   mais barato e mais defensável numa banca.

Se o cronograma apertar, corta-se de baixo para cima. Os itens 1–3 não são
negociáveis: são eles que respondem à crítica 2.7.

---

## 6. As quatro camadas de entrega

O que a persona consome, em ordem de frequência de uso:

| Camada | Formato | Momento de uso |
|---|---|---|
| Fila da semana | Lista ranqueada, gatilho ao lado | "A quem ligo hoje" |
| Card de preparação | Uma tela: score, eixo fraco, evidência, oportunidade | Os 5 min antes da call |
| Talk track | Abertura + objeções + o que não pitchar | Durante a conversa |
| Briefing exportável | Markdown | Anexo, registro, repasse, auditoria |

---

## 7. Riscos residuais assumidos conscientemente

- **Score sem validação empírica no prazo.** Aceito. Mitigado por transparência: as
  premissas estão em YAML versionado e a UI mostra confiança separada do score.
- **Cobertura do ecossistema não medida.** Aceito. Declarar o limite é melhor que
  fingir exaustividade — o sistema reporta quantas fontes varreu, não estima quantas
  existem.
- **Estimativa de oportunidade imprecisa.** Aceito, mitigado por faixa em vez de ponto
  e premissas visíveis.
- **Registro de execuções em memória** (`api/runs.py`): quebra com múltiplos workers.
  Adequado para dashboard interno de um worker. Já documentado.
- **Exportação em Markdown, não PDF.** Dependência de sistema que exige `sudo`.

---

## 8. Critério de pronto

O projeto está pronto quando um gerente hipotético consegue, em uma sessão:

1. Rodar uma busca e ver o progresso ao vivo.
2. Abrir a fila e entender **por que** a primeira empresa é a primeira.
3. Abrir o perfil e clicar numa evidência que abre a URL real com o trecho citado.
4. Ler o talk track e saber a primeira frase da ligação — e o que evitar dizer.
5. Rodar de novo dias depois e ver **o que mudou**, não a mesma lista.

Se os cinco acontecem contra APIs reais, o case está entregue. Nenhuma feature
adicional compensa a falha de qualquer um dos cinco.

---

## 9. Alternativas de arquitetura consideradas

Registro honesto: **o corte atual não foi escolhido contra alternativas.** A seção
3 do `plano-arquitetura.md` apresenta a estrutura já pronta e justifica apenas a
regra de `models/` como fonte da verdade. O layout horizontal (`models/`,
`scraping/`, `rag/`, `llm/`, `persistence/`) é a convenção default do ecossistema
Python, reforçada pelo layout que a própria documentação do LangGraph usa
(`state.py`, `nodes/`, `build.py`).

Ele se provou adequado *a posteriori*, por três forças que só ficaram visíveis
depois. A avaliação formal abaixo foi feita em 2026-09-09, e é ela que permite
dizer "escolhemos" em vez de "seguimos a corrente".

### Por que o corte horizontal se sustenta

1. **Ausência de acesso às APIs reais durante o desenvolvimento.** Sem Docker e
   sem chaves, a única forma de progredir era construir contra dublês — o que
   exige capacidades injetáveis. `NodeDeps` não é purismo; é o que viabilizou
   287 testes e um mês de trabalho sem rede.
2. **A tese do projeto é auditabilidade.** "Nenhuma afirmação sem evidência" só é
   crível se cada peça for verificável isoladamente. Separação vira pré-requisito
   de argumento, não de organização.
3. **O histórico de commits é avaliado.** Camadas independentes produzem um
   commit coerente por vez.

### As alternativas, e o que cada uma custaria

| Alternativa | Ganharia | Perderia | Veredito |
|---|---|---|---|
| Script linear num arquivo | Velocidade; roda no dia 1 | Intestável sem rede (fatal aqui); nenhum argumento de rigor | Certo para um protótipo de uma semana. Não é o caso |
| **Fatias verticais** (`descoberta/`, `diagnostico/`, `recomendacao/`) | Coesão: um conceito, uma pasta | A fronteira "toca rede / não toca"; `models/` como fonte única tende a virar 3 cópias; infra duplicada | **Rejeitada** — ver abaixo |
| Hexagonal / Ports & Adapters completo | Direção de dependência explícita e verificável | Cerimônia | **Parcialmente adotada** — ver abaixo |
| Orientado a eventos (Celery/Kafka) | Escala horizontal; resolveria `api/runs.py` em memória | Infra indisponível; debug muito pior; volume não justifica | Over-engineering. Citável como "o caminho se virasse produção" |
| Agente ReAct único com ferramentas | Muito menos código | **Mata o projeto**: ordem não determinística impede garantir que o validador rodou antes do scorer, e os três invariantes deixam de ser demonstráveis | Rejeitada. É o anti-padrão que o próprio case critica |
| Data pipeline / ELT (dbt) | Histórico temporal quase de graça; linhagem nativa | LLM e RAG ficam desconfortáveis; perde a narrativa multi-agente do enunciado | Rejeitada como arquitetura; **ideia aproveitada** na tabela `defensibility_scores` append-only com `weights_version` |
| Microsserviços | Nada, nesta escala | Tudo | Rejeitada |

### Fatias verticais — a decisão detalhada

É a alternativa séria, e resolve uma dor real: hoje, para entender defensibilidade
de ponta a ponta, é preciso abrir cinco diretórios (`models/scoring.py`,
`scoring/weights.*`, `graph/nodes/scorer.py`, `llm/prompts/defensibility_scorer_v1.md`,
`persistence/tables.py`). Isso é custo de compreensão real num projeto cujo
repositório será lido por um avaliador.

**Rejeitada assim mesmo**, por quatro motivos:

1. **Erosão da fronteira rede / não-rede.** `scoring/` hoje é aritmética pura.
   Numa fatia `diagnostico/`, o cálculo do peso e a chamada de LLM que gera o
   rationale ficam vizinhos, e a primeira pessoa que precisar testar a regra de
   peso vai mockar rede. A propriedade se perde por erosão, não por decisão.
2. **Racha o `models/` único.** Cada fatia tende a ganhar seu próprio `models.py`,
   e `Evidence` passa a existir em três versões levemente diferentes — o que
   destrói a rastreabilidade, que é a tese do projeto.
3. **Duplica infraestrutura.** Cliente NIM, retry, cache, gate de robots são
   usados por várias fatias. Ou vira `shared/` (horizontal de novo) ou vira cópia
   divergente.
4. **Custo do momento.** Reorganizar o repositório na semana em que é preciso
   rodar contra API real e construir três telas, com 287 testes atravessando.

**Mitigação escolhida:** o custo de compreensão se resolve com documentação, não
com pastas — o mapa "conceito → arquivos" no README. Vinte minutos contra uma
semana de refatoração.

### Hexagonal — o que foi adotado e o que não

O projeto já está a ~70% de Ports & Adapters sem ter nomeado: `NodeDeps` é o
container de portas, `scraping/`/`rag/`/`llm/` são adaptadores, e a suíte roda
offline porque a inversão de dependência já existe de fato.

Adotar o formalismo restante (declarar `Protocol` para cada dependência) tem custo
quase zero e **um ganho prático, não retórico**: hoje nada impede um nó de
importar `tavily` direto e furar o `NodeDeps`. Funcionaria, passaria no lint, e só
quebraria contra a rede real. Como nada nunca rodou contra API real, esse é um
risco vivo. Fica como trabalho de baixo custo e alta prioridade.

**O que hexagonal NÃO resolve:** a dispersão de conceito. Ele também é um corte
horizontal — acrescenta uma camada de interfaces. Fatia resolve *coesão*;
hexagonal resolve *direção de dependência*. São problemas diferentes.

---

## 10. Preparação para a execução real (implementado em 2026-09-09)

A fundação estava mais preparada para rede hostil do que o discurso do projeto
sugeria. Já existia, verificado em código: `RobotsCache` com `crawl_delay`,
`DomainRateLimiter` por domínio, timeouts de settings, retry com backoff
exponencial em erro de transporte, fallback Playwright, **dois níveis de retry no
LLM** (transporte/429 honrando `Retry-After`, e validação reenviando o erro de
schema), `node_guard` transformando exceção em `NodeFailure`, e cache de resposta
HTTP.

O que faltava não era arquitetura — era **operação**. Três lacunas, agora fechadas
em `src/radar/llm/quota.py` e `src/radar/llm/cache.py`:

### 10.1 Teto de concorrência (`ConcurrencyGate`)

O `Send` faz fan-out para N empresas, e os nós são `async` empurrando a chamada
síncrona para thread. Sem teto, N empresas viram N rajadas simultâneas: o 429
chega para todas ao mesmo tempo, cada uma entra em backoff, e o lote fica mais
lento do que se tivesse sido serializado — com a cota queimada nas tentativas
perdidas.

Semáforo de `threading`, não de `asyncio`, porque quem chama já está fora do event
loop. Padrão 4, conservador de propósito: a cota gratuita não documenta o limite
de concorrência, e descobri-lo por tentativa e erro custa a própria cota.

**Trade-off:** lote mais lento em troca de previsível. Aceito — o gargalo real do
projeto é cota, não tempo de parede.

### 10.2 Orçamento por execução (`LLMBudget`)

Retry existe porque falha é esperada; retry sem teto transforma um dia ruim da API
em cota inteira consumida sem nenhum diagnóstico. O orçamento **levanta
`BudgetExceededError` em vez de degradar em silêncio**: um lote que parou por cota
precisa aparecer como falha no relatório, não como metade das empresas sem
briefing e nenhuma explicação — a lacuna não declarada que o projeto evita.

Conta **chamadas, não tokens**. Tokenizar antes de cada envio custaria CPU no
caminho quente para uma precisão que não muda a decisão: a pergunta é "o lote saiu
do controle?", e número de chamadas responde. Padrão 400 ≈ 12 empresas × 6 nós de
LLM × margem de retry.

O consumo é logado em `NIMClient.flush()`, ao fim do lote. Um lote que terminou em
380/400 passou raspando, e o próximo estoura.

### 10.3 Cache de resposta do LLM (`CompletionCache`)

O scraping tinha cache; a chamada de modelo não. Reexecutar o mesmo lote pagava
tudo de novo — justamente na fase de iteração contra API real.

**A propriedade que torna seguro ligar por padrão:** a chave é o hash de
`(modelo, temperatura, mensagens)`. A evidência raspada entra no prompt, então
página mudou → prompt mudou → chave mudou. **O cache não consegue mascarar mudança
de sinal**, o que era o risco de conflito com o eixo temporal do radar. Travado em
`test_prompt_diferente_e_chave_diferente`.

Efeito colateral desejado: reexecução vira determinística. Com temperatura > 0 o
modelo varia entre chamadas, e cada demonstração do case mostraria números
diferentes. Com cache, mesma entrada → mesma saída, e `weights_version` continua
sendo o que explica mudança de score (ADR 0002).

### 10.4 Isolamento de cache na suíte (`tests/conftest.py`)

Bug real encontrado ao ligar o cache: a suíte gravou respostas dos dublês em
`data/cache/llm` e, na execução seguinte, os testes do grafo leram de lá. O efeito
foi silencioso e perverso — um teste que injeta `RuntimeError("modelo fora do ar")`
no planner passou a receber resposta válida do cache, e a falha que ele existia
para verificar deixou de acontecer.

Duas lições registradas: **cache em disco é estado global**, e **a configuração
padrão do produto não é a da suíte** (cache é desejável em produção e nocivo em
teste de retry e degradação).

### 10.5 Política de frescor — decidida, implementação adiada

O TTL de 7 dias do cache de scraping **colide com o gatilho temporal**: rodar hoje
e de novo em três dias devolve a página cacheada, e o diff conclui "nada mudou"
sem ter olhado. `force_refresh` existe em `fetch()`, mas nada no grafo decide
quando usá-lo.

É decisão de produto disfarçada de parâmetro. **Política definida:** a primeira
passada sobre uma empresa usa cache livremente; a passada de *monitoramento* força
refresh nas fontes de sinal (carreiras, blog técnico) e mantém cache no
institucional, que muda pouco. Implementação entra junto com o nó de comparação —
antes disso não há passada de monitoramento para configurar.
