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
