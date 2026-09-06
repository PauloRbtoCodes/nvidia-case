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
   **Concluído:** `computed_at` no modelo, `scoring/delta.py` (comparador puro), nó
   `compare` entre `score` e `rag`, política de frescor no `collector` (§10.5),
   `ScoreDelta` no perfil e ao lado da empresa na fila da API, e a seção "o que
   mudou" nas duas telas.
3. **Talk track** — extensão do nó de briefing, consumindo a seção "Quando NÃO
   recomendar" dos cards. O `ScoreDelta` já chega ao briefing pelo estado
   (`CompanyState.score_delta`): `NOVA_EVIDENCIA` é a frase de abertura.
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
institucional, que muda pouco.

**Implementado.** `ScoreHistoryPort.seen_before` responde "já diagnosticamos esta
empresa?" com uma consulta barata (a linha do score, sem hidratar o modelo nem o
mapa de evidências), e o `collector` usa isso para decidir o frescor.

A fronteira caiu exatamente onde a coleta já a desenhava, sem precisar classificar
URL por URL: a **primeira onda** são as sementes (home, institucional — cache
serve), a **segunda onda** são os links de sinal que a home revela (carreiras,
blog de engenharia, preços, clientes — onde a mudança aparece). Numa passada de
monitoramento só a segunda onda volta para a rede, via
`fetch_many(..., force_refresh=True)`.

Banco fora do ar devolve `False`: na dúvida, tratar como primeira passada confia
no cache, que é o lado barato do erro. Travado em
`test_passada_de_monitoramento_forca_so_as_fontes_de_sinal` e
`test_primeira_passada_sobre_uma_empresa_confia_no_cache`.

---

## 11. Auditoria da camada de contratos (2026-09-09)

Revisão de `src/radar/models/` — a camada que o projeto declara como fonte da
verdade. Três achados, dois corrigidos aqui e um deixado como trabalho futuro.

### 11.1 Os pesos estavam duplicados, e a duplicação quebrava uma promessa — **corrigido**

`AXIS_WEIGHTS` está hardcoded em `models/scoring.py`, e os mesmos valores vivem em
`scoring/weights.yaml`. Mas `total`, `global_confidence` e `gap_severity` usam o
**dict hardcoded** — o YAML não participava da conta.

Consequência: **recalibrar `weights.yaml` não mudava score nenhum**, contra a
promessa registrada no `CLAUDE.md` ("premissas numéricas vivem em `weights.yaml`,
versionadas — recalibrar não pode exigir re-scraping nem corromper histórico").

E pior que não funcionar: `DefensibilityScore.weights_version` grava a versão do
YAML. Depois de uma recalibração, o banco registraria `weights_version: "0.2.0"`
num score calculado com os pesos de `0.1.0`. **Trilha de auditoria que mente é
pior que trilha ausente** — é exatamente o modo de falha que o projeto inteiro
existe para evitar.

Os testes não pegavam: um verificava que o dict soma 1,0, outro que o YAML soma
1,0, e nenhum que os dois são iguais. O mesmo valia para
`actionable_confidence_threshold` (0,35) e `gap_score_threshold` (60,0), ambos
literais inline.

**Decisão: manter a duplicação, travar a divergência.**

A alternativa — injetar os pesos e tirar o cálculo do modelo — resolveria a
duplicação mas custaria caro: `total`, `weakest_axis` e `gap_severity` deixariam
de ser `computed_field`, e com isso some a garantia de que *o eixo mais fraco é
calculado igual em todo o sistema*. Essa garantia é o que impede uma segunda
implementação divergente aparecer num serviço qualquer, e vale mais que a
elegância de ter um único literal.

O preço da escolha é pago por `test_pesos_do_modelo_espelham_o_yaml` e
`test_limiares_do_modelo_espelham_o_yaml`, que falham com a mensagem exata do
valor divergente. Verificado na prática: alterar o YAML quebra o CI.

Os dois limiares viraram constantes nomeadas (`ACTIONABLE_CONFIDENCE_THRESHOLD`,
`GAP_SCORE_THRESHOLD`) em vez de literais inline, para que a trava tenha o que
comparar.

### 11.2 Nada garantia que os quatro eixos eram distintos — **corrigido**

`axes: list[AxisScore] = Field(min_length=4, max_length=4)` validava comprimento,
não unicidade. Quatro `AxisScore` com o mesmo eixo passavam — e o estrago era
silencioso: `total` somaria o mesmo peso quatro vezes, devolvendo número acima de
100 com aparência de score válido, e `weakest_axis` viraria arbitrário.

Não é hipótese remota: repetir um eixo e omitir outro é erro comum de geração
estruturada por LLM, e o schema aceitava. `model_validator` de unicidade fecha o
caso, com o eixo duplicado nomeado na mensagem.

### 11.3 `computed_at` no `DefensibilityScore` — **adicionado**

Pré-requisito do gatilho temporal, e a decisão não é óbvia: o timestamp já existe
na coluna da tabela (`TimestampedMixin`), então por que duplicá-lo no modelo?

**Porque a comparação entre duas execuções é aritmética, e portanto mora em
`scoring/` — que não pode importar `persistence/` sem furar a direção de
dependência da arquitetura.** Sem o campo no modelo, comparar dois scores exigiria
carregar as datas por fora e mantê-las pareadas na mão, o que é frágil e move a
lógica para onde ela não pertence. É também o que permite a saída dizer "mudou
desde 12/08" em vez de apenas "mudou".

Campo com `default_factory`, logo não quebra nada já construído.

### 11.4 Campos que decidem sem carregar procedência — **trabalho futuro**

`stage`, `headcount_estimate` e `founded_year` são tipos crus, não
`EvidenceBackedField`. Mas `stage` alimenta `capacity_to_act`, que ordena a fila
de prioridade — ou seja, **um campo sem procedência influencia a quem o gerente
liga primeiro**, contra a regra do projeto de que todo campo inferido carrega suas
evidências.

Adiado porque toca extractor, prompt e tabela ao mesmo tempo, e a execução real
tem precedência. Registrado para não se perder.

Na mesma categoria: a restrição LGPD do `Founder` é docstring, não código — nada
impede alguém acrescentar um campo de contato. Meio-termo barato quando for a hora:
`extra="forbid"` no modelo, que ao menos impede campo novo entrar por acidente.

### 11.5 O que foi considerado e recusado nesta camada

| Mudança | Por que não |
|---|---|
| Mover `total` para `scoring/` | Perde a garantia de cálculo único do `weakest_axis`, que é o valor central da decisão de usar `computed_field` |
| `axes` como `dict[Axis, AxisScore]` | Daria unicidade de graça, mas quebra serialização já gravada e os schemas da API. O validador resolve por muito menos |
| `frozen=True` em todos os modelos | Construção incremental em alguns nós ficaria travada. Vale só para `Evidence`, cujo valor inteiro é ser imutável — fica como futuro |

---

## 12. Auditoria da camada de capacidades (2026-09-09)

Varredura motivada pelo achado da camada 1: se um espelho hardcoded existiu uma
vez, costuma existir duas. Encontrou um padrão **irmão, não idêntico**.

### 12.1 Cortes da fila fora do arquivo de calibração — corrigido

`priority.py` tinha três números decidindo a fila, nenhum deles em `weights.yaml`:
`MIN_GLOBAL_CONFIDENCE = 0.35`, `DEFENSIBLE_THRESHOLD = 65.0` e o literal sem
nome `capacidade >= 0.5`.

Não é o mesmo bug da camada 1 (espelho divergente) e sim outro: **premissa de
calibração que nunca esteve no arquivo de calibração**, contra a regra do
`CLAUDE.md`. O efeito prático é o mesmo, porém: a calibração da semana 4 quer
ajustar exatamente esses cortes, e sem eles no YAML isso exigiria mudança de
código, ficaria fora de `weights_version`, e produziria histórico incomparável
sem nada registrando a diferença entre as duas escalas.

O corte de capacidade era o pior: um literal anônimo dentro de um `if`, decidindo
se a empresa é conversa desta semana ou trilha de comunidade — o número mais
consequente da fila inteira.

### 12.2 Chaves de `product.py` sem trava contra o YAML — corrigido

`inferir_categoria_produto` devolve uma chave consumida pelo TCO como
`volume_base_tokens_mes.get(categoria, ...["desconhecido"])` — **fallback
silencioso**. Categoria renomeada no YAML não quebra nada: vira `desconhecido`, e
o volume base cai de 250M para 50M tokens/mês. **Erro de 5× na premissa mais
frágil da cadeia, sem uma linha de log.**

É exatamente o padrão que `test_cards.py` já travava para
`AXIS_TO_NVIDIA_FAMILY` — reconhecido uma vez e não aplicado aqui. A trava agora
é nas duas direções, para categorias e provedores.

Assimetria que ficou registrada: a chave de categoria falha em silêncio, a de
provedor levanta `KeyError`. Duas políticas para o mesmo tipo de erro no mesmo
módulo. Não uniformizado agora porque mudar o fallback da categoria muda
comportamento de produto (uma categoria desconhecida legítima deve mesmo cair em
`desconhecido`); o que faltava era a trava de CI, não a mudança de política.

### 12.3 Degradação silenciosa do BM25 — corrigido

`make ingest` popula o Qdrant dentro do laço e salva o BM25 só no fim. Processo
que morre entre as duas coisas deixa o Qdrant à frente do índice lexical, e a
fusão RRF vira cópia do ranking denso. Match exato de nome de produto — "Triton",
"TensorRT-LLM" — é o que o sinal lexical carrega, e é o primeiro a se perder.

`radar.cli check` já avisava o operador; faltava cobrir a execução que não passa
por ele. Aviso adicionado na construção do `HybridRetriever`.

Não corrigida a causa raiz (ordem de escrita entre Qdrant e BM25): exigiria
transacionar dois sistemas que não compartilham transação. Tornar visível é a
resposta proporcional ao risco num projeto deste porte.

---

## 13. Primeira execução contra infraestrutura real (2026-09-09)

**O bloqueador de Docker caiu.** O `CHECKPOINT` de 14/08 registrava Docker como
pendente por exigir `sudo`; ele está funcionando.

Consequência imediata: metade da "execução real" deixou de ser hipótese.

| Verificado de verdade | Resultado |
|---|---|
| `docker compose up -d` | 4 contêineres no ar (Postgres, Qdrant, Langfuse + banco próprio) |
| `alembic upgrade head` contra Postgres real | 10 tabelas + `alembic_version` criadas |
| Colunas JSONB | 17 colunas, JSONB de fato — não TEXT com JSON dentro |
| Suíte com `TEST_DATABASE_URL` | **305 passed, 0 skipped** |
| `radar.cli check` | funciona e reporta com precisão o que falta |

O teste `test_jsonb_e_usado_no_postgres`, skipado desde o início do projeto,
rodou. A camada de persistência deixou de ser validada apenas contra dublê
SQLite — que era a maior incerteza depois das chaves de API.

### O que continua bloqueado

Só uma coisa, agora: **as três chaves de API.** Não há `.env` na máquina.

- `NVIDIA_API_KEY` — bloqueante. Sem ela nenhum agente roda, e `make ingest` não
  popula o Qdrant (embeddings vêm do NIM).
- `TAVILY_API_KEY` — descoberta indisponível; o grafo ainda roda a partir de URLs
  semente.
- `COHERE_API_KEY` — rerank cai para a ordem do RRF, degradação já tratada.

Com a chave do NIM, o caminho completo abre na mesma sessão: `make ingest` →
`make run` com 5 empresas. Os três controles de cota implementados em §10 existem
precisamente para essa primeira execução não queimar o crédito gratuito.
