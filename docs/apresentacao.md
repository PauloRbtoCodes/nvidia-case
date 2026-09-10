# Roteiro — Apresentação (7 min + demo)

> NVIDIA Startup AI Radar · IA Academy × NVIDIA
>
> Dez slides, ~7 minutos. As duas demos são ao vivo; se a API cair, há
> screenshots de reserva na pasta `docs/demo/` (gere antes com o passo a passo
> no fim deste arquivo). O tempo de cada slide está no cabeçalho — o total
> fecha em ~6:55, com 5s de folga. Diferenciais e aprendizados ganharam mais
> tempo de fala nesta versão porque são os dois slides que a banca mais lembra
> depois — o resto foi enxugado para abrir espaço.
>
> Regra que atravessa os slides de **solução**, **arquitetura** e **pipeline**:
> cada um termina com o trade-off que foi aceito ali. A banca vai perguntar; é
> melhor eu levantar antes — e cada trade-off aqui é contado em termos de
> **negócio** (custo, confiabilidade, velocidade), não de jargão técnico.

---

## Slide 1 — O problema (0:40)

**No slide:**
- Título: *"O wrapper de LLM virou feature de keynote"*
- Uma linha do tempo curta: API de modelo → agente → busca → voz → produto final
- A pergunta, destacada: **"Quais startups brasileiras de IA estão expostas à comoditização — e o que a NVIDIA tem a oferecer para que deixem de estar?"**

**Roteiro:**
> Os grandes laboratórios subiram na cadeia de valor. O que era API de modelo
> fundacional agora é agente, é busca, é voz, é produto final. Uma startup que se
> posiciona só como wrapper de LLM pode ser substituída por uma funcionalidade
> lançada num keynote.
>
> O time de Startups e VCs da NVIDIA no Brasil roda o programa Inception — não
> vende licença, recruta e nutre startups. A pergunta que importa para essa
> pessoa não é "quais startups brasileiras usam IA". É: **quais estão sob ameaça,
> quão urgente é a conversa, e o que dizer sem parecer genérico.**

---

## Slide 2 — A solução, a partir do problema (1:00)

**No slide:**
- Título: *"Defensibility Radar — do diagnóstico para a decisão"*
- Os 4 eixos com peso: Dados proprietários 30% · Profundidade de workflow 25% · Domínio da stack 25% · Distribuição 20%
- Score 0–100 · complemento = risco de comoditização
- Uma frase: *classificar AI-native/AI-enabled descreve o presente; o score responde à pergunta do case*
- Rodapé — **Trade-off:** *score sem calibração empírica no prazo → mitigado por confiança separada do score e premissas em YAML versionado*

**Roteiro:**
> O enunciado já pede um classificador AI-native / AI-enabled / non-AI. Mas isso
> descreve o presente — não diz quem está sob ameaça nem o que fazer.
>
> Então o diferencial é um **score de defensibilidade** de 0 a 100, em quatro
> eixos ponderados. O complemento é o risco de comoditização: quanto da startup
> um lançamento da OpenAI conseguiria substituir. E ele faz três coisas que uma
> classificação não faz: ordena a carteira do gerente numa fila por urgência,
> torna a recomendação **causal** — a tecnologia sai do eixo mais fraco, não de
> uma regra por setor — e inverte a conversa: em vez de "sua startup é um
> wrapper", entrega "aqui está a rota para deixar de ser, e a NVIDIA cobre a
> parte técnica dela".
>
> **O trade-off que aceitei:** o score não tem calibração empírica dentro do mês.
> Mitigo de duas formas — a confiança é um número separado do score, e todas as
> premissas numéricas vivem num YAML versionado, então recalibrar é editar dado,
> não código.

---

## Slide 3 — Arquitetura: uma máquina que aguenta escala e troca de peça (0:45)

**No slide — o diagrama é o slide. Desenhe isto grande, sem texto ao redor:**

```
      "startups brasileiras de IA para saúde"
                      │
   ╔══════════════════▼═══════════════════════════════════╗
   ║  NÍVEL 1 — ORQUESTRADOR      decide QUEM investigar   ║
   ╠══════════════════════════════════════════════════════╣
   ║   [Planejar buscas]   frase solta → queries reais     ║
   ║          │                                            ║
   ║   [Descobrir]         busca web → filtra notícia,     ║
   ║          │            diretório, portal de mídia      ║
   ║   ═══ fan-out ═══     uma investigação por empresa    ║
   ╚═══╦════════╦════════╦═════════════════════════════════╝
       ║        ║        ║
   ┌───▼──┐ ┌───▼──┐ ┌───▼──┐   NÍVEL 2 — investigação isolada
   │ EMP.1│ │ EMP.2│ │ EMP.3│   estado próprio · retry próprio
   └───╥──┘ └───╥──┘ └───╥──┘   falha aqui NÃO contamina as vizinhas
       ║        ║        ║
       ╚════════╬════════╝
                ▼
        [Consolidar]  → fila ordenada por urgência
```

- Ao lado, a caixa das **portas** — pequena, mas é o que responde "e se trocar de fornecedor":

```
  NÓS (lógica de decisão)  ──►  PORTAS  ──►  MUNDO EXTERNO
   pontuar, classificar,       NodeDeps      NIM · Tavily
   recomendar                                Qdrant · Postgres
   "não sabem" quem está do outro lado ─────────────┘
```

- Rodapé — **Trade-off:** *organizei o código por tipo de trabalho (coleta, extração, pontuação), não por área de negócio → troco qualquer peça sem reescrever a lógica de decisão. Custo: entender uma funcionalidade de ponta a ponta exige olhar mais de uma pasta — resolvido com um mapa de navegação no README, não com refatoração no fim do prazo.*

**Roteiro:**
> Deixa eu explicar por que o desenho é esse, porque ele não é o óbvio.
>
> O óbvio seria uma fila: pega empresa, processa, pega a próxima. Eu descartei
> isso por um motivo prático — o trabalho é lento e falha com frequência. Cada
> empresa exige raspar site, ler vagas, chamar modelo várias vezes. Numa fila
> única, a empresa cujo site está fora do ar segura todas as outras atrás dela,
> e um erro no meio derruba o lote inteiro. Num lote de dez, isso significa
> perder nove diagnósticos bons por causa de um site quebrado.
>
> Então separei em dois níveis. Em cima, o orquestrador só decide **quem**
> investigar: ele traduz a frase que o gerente digitou em buscas reais, descobre
> candidatas e joga fora o que não é empresa — notícia, diretório, portal. Aí
> vem o fan-out: em vez de uma fila, ele dispara **uma investigação
> independente por empresa**. Cada uma tem estado próprio e orçamento de
> tentativas próprio. Se a empresa dois falha, a um e a três nem ficam sabendo.
> No fim, o consolidador junta o que voltou e ordena por urgência.
>
> E tem a segunda decisão, essa caixinha do lado. Nenhum nó do sistema fala
> direto com o mundo de fora. Quando o nó de pontuação precisa de um modelo, ele
> não sabe que existe NIM, não sabe que existe chave de API: ele pede pela
> porta. Isso parece burocracia até o dia em que você precisa trocar de
> fornecedor — e aí você troca o que está atrás da porta, e a lógica de decisão,
> que é a parte que dá valor, não é tocada. Hoje mesmo eu usei isso: troquei o
> modelo de uma tarefa por outro sem editar uma linha de lógica.
>
> **O trade-off:** organizei o repositório por tipo de trabalho — coleta,
> extração, pontuação — e não por fatia de negócio. Isso facilita trocar peças,
> mas custa navegabilidade: entender "defensibilidade" de ponta a ponta exige
> abrir mais de uma pasta. Resolvi com documentação — um mapa conceito→arquivo no
> README — e não com uma semana de refatoração no fim do prazo.

---

## Slide 4 — A pipeline: como a decisão nasce (0:45)

**No slide — de novo, o diagrama é o slide. Este é o que acontece DENTRO de uma empresa:**

```
   ┌──────── volta e coleta mais ────────┐   teto de tentativas:
   │        (só se faltou lastro)        │   nunca vira loop
   ▼                                     │
[COLETAR] ──► [EXTRAIR] ──► [VALIDAR EVIDÊNCIA] ──┘
 site, vagas,   quem é,       "isso tem fonte?"
 releases       o que faz            │ tem
                                     ▼
                              [CLASSIFICAR]     AI-native / enabled / non-AI
                                     │
                                     ▼
                              [PONTUAR]         4 eixos + custo de migrar (TCO)
                                     │
                                     ▼
                              [COMPARAR]  ◄──── execução anterior (Postgres)
                                     │          "o que mudou desde a última vez"
                                     ▼
                              [BUSCAR NA BASE NVIDIA]
                                     │          vetorial + BM25 → RRF → rerank
                                     ▼
                              [RECOMENDAR]  ⛔ sem citação verificada = BLOQUEIA
                                     │
                                     ▼
                              [BRIEFING]        o card dos 5 min antes da ligação
```

- Uma linha destacada embaixo: **"O único passo que pode dizer NÃO é o de recomendar. E ele diz."**
- Rodapé — **Trade-off:** *recomendação sem citação da base é **bloqueada**, não "melhor esforço" → menos recomendações, mais confiáveis. Três com fundamento valem mais numa reunião que oito plausíveis.*

**Roteiro:**
> Aqui eu construí de trás para frente, e vale explicar por quê. Eu comecei
> pela última caixa — o briefing que o gerente lê antes da ligação — e perguntei:
> o que precisa ser verdade para eu confiar nesse texto? Cada caixa dessa cadeia
> é uma resposta a essa pergunta.
>
> Começa coletando site, releases e vagas de emprego. Vaga é a fonte mais
> honesta que existe sobre stack: ninguém mente numa vaga técnica, porque quem
> lê é candidato e vai perceber. Daí sai o perfil da empresa.
>
> Aí vem a caixa que quase todo mundo pula: **validar evidência**. Antes de
> pontuar qualquer coisa, o sistema pergunta se o que ele extraiu tem fonte
> mesmo. Se não tem, ele volta e coleta mais — com um teto, senão vira loop
> infinito e queima cota. Só passa adiante o que tem lastro.
>
> Depois classifica, pontua os quatro eixos e calcula o custo de migrar. E então
> a caixa do gatilho temporal: compara com a execução anterior, que está no
> banco. É daí que sai o "o que mudou desde a última varredura".
>
> Chegando na recomendação, busca na base de conhecimento da NVIDIA — busca
> vetorial e busca por palavra exata juntas, porque "TensorRT-LLM" e "L40S" são
> nomes, e busca semântica é ruim com nome próprio.
>
> E aqui está a decisão de que mais me orgulho, essa linha embaixo: **o passo de
> recomendar é o único que pode dizer não — e ele diz.** O prompt manda copiar o
> trecho literal da fonte, e uma comparação de texto, em código, confirma que
> aquele trecho existe mesmo lá. Se não bate, ou se a busca não trouxe nada
> relevante, o sistema bloqueia a recomendação em vez de escrever algo plausível.
>
> **O trade-off:** o sistema prefere falhar a inventar. Bloquear em vez de
> degradar significa perder uma recomendação correta quando a busca não
> encontra a fonte certa. Aceitei conscientemente: alucinar sobre o que o
> Triton faz, na frente de um founder técnico, custa a credibilidade do
> programa inteiro — e o programa é o produto aqui, não o software.

---

## Slide 5 — Demo 1: a varredura ao vivo (0:55)

**No slide:** só o título *"Demo — descobrir e diagnosticar"* e o print da tela de varredura em segundo plano.

**O que fazer:**
1. Abrir `localhost:3100/busca`, digitar *"startups brasileiras de IA para saúde"*, **3 empresas** (não 5 — cada empresa custa ~1min no passo de recomendação, e o palco não tem esse tempo).
2. Apontar a **trilha de fases** no topo enquanto ela avança: Descobrir → Levantar lastro → Diagnosticar → Decidir. Ela é o mesmo desenho do slide 4, agora acontecendo.
3. Apontar o **log ao vivo**: cada linha é um nó do grafo terminando, com o nome da empresa e o que aquele passo produziu — "4 páginas coletadas", "evidência insuficiente, voltando a coletar", "score 50 · eixo mais fraco: dados proprietários".
4. **A linha de ouro, se aparecer:** *"Auditando evidências — evidência insuficiente, voltando a coletar"*. É o ciclo de re-coleta do slide 4 visível ao vivo. Se aparecer, pare e mostre.
5. Não esperar terminar — cortar para o slide 6 assim que a primeira empresa passar de "Diagnosticar".

**Roteiro:**
> Descrevo o setor como falaria com um colega. O sistema traduz isso em buscas
> reais, filtra o que é notícia ou diretório — só site de empresa passa — e
> começa a diagnosticar.
>
> E o que vocês estão vendo aqui não é uma barra de progresso decorativa: cada
> linha é um nó do grafo terminando de verdade, com o que ele produziu. Repare
> nessa linha — "evidência insuficiente, voltando a coletar". Aquele ciclo que
> eu mostrei no diagrama há um minuto está acontecendo agora, nesta empresa: o
> sistema achou que não tinha lastro suficiente e voltou para buscar mais.
>
> Isso é o Entregável 1, 2 e 3 rodando juntos.

---

## Slide 6 — Diferenciais: o que nenhum lead scoring comum entrega (1:05)

**No slide — quatro linhas, cada uma com o "por que importa" embutido:**
1. **Score ≠ confiança.** → Startup discreta produz pouca evidência pública. "Não achamos" nunca vira "é fraca" — o gerente não age sobre um julgamento injusto.
2. **Recomendação causal, não genérica.** → Sai do eixo mais fraco, ponderado por peso *e* confiança — nunca de uma regra fixa por setor. Toda recomendação é auditável até a evidência.
3. **Honestidade como instrumento de venda.** → O TCO diz "migrar ainda não compensa" quando é verdade. É isso que faz um founder técnico confiar no restante do relatório.
4. **Gatilho temporal — o "por que agora".** → O radar detecta *mudança* entre execuções, não só estado. É o que transforma um diagnóstico estático em motivo para ligar essa semana.

**Roteiro:**
> Quatro coisas que quase nenhum sistema de lead scoring tem — e cada uma existe
> porque resolve um jeito específico desses sistemas falharem na prática.
>
> Primeira: score e confiança são números separados. Uma startup sem blog de
> engenharia não é menos defensável, é menos **observável** — e misturar as duas
> gera injustiça com aparência de rigor. Isso importa porque é exatamente o tipo
> de erro que faz um gerente descartar uma startup promissora por falta de
> dados, não por falta de mérito.
>
> Segunda: a recomendação é rastreável até o diagnóstico. Dá para responder "por
> que TensorRT-LLM para esta empresa" apontando o eixo, a severidade e a
> evidência — não é uma sugestão genérica de catálogo, é uma conclusão que
> nasce do dado daquela empresa específica.
>
> Terceira: quando migrar para GPU dedicada não compensa, o sistema **diz isso**.
> Isso parece contraintuitivo — por que um produto de vendas diria "não compre
> ainda"? — mas é exatamente o que constrói credibilidade com um founder
> técnico, que sabe reconhecer quando está sendo enrolado.
>
> Quarta, e a que fecha o nome do produto: o gatilho temporal. Um score parado
> é uma foto; o radar mostra o filme. É a diferença entre "essa startup é
> vulnerável" e "essa startup ficou mais vulnerável nas últimas duas semanas,
> ligue agora".

---

## Slide 7 — Demo 2: o perfil, o radar e o gatilho (1:00)

**No slide:** título *"Demo — a decisão e a prova"*.

**O que fazer:**
1. Da fila, abrir uma empresa (ex.: Voa Health ou NeuralMed).
2. Mostrar o **radar de eixos**: a barra grossa é o valor, a fina é a evidência que sustenta. Num eixo de baixa confiança, o número some e a barra vira hachura — *"não sabemos", não "nota baixa"*.
3. **Clicar numa evidência** — abre a URL real com o trecho literal citado. Essa é a tese do projeto: nenhuma afirmação sem fonte.
4. Descer até **"O que mudou desde a última varredura"** (se houver histórico) ou explicá-la pelo slide: o diff eixo a eixo, com a evidência nova como manchete.
5. Fechar no invariante: *sinal que sumiu de uma coleta para a outra vira queda de **confiança**, nunca queda de **score** — página fora do ar não é regressão da empresa.*
6. Se sobrar tempo: clicar em **"Exportar PDF"** no rodapé do briefing — vira o card pronto pra anexar num e-mail pro founder, sem o gerente copiar e colar nada.

**Roteiro:**
> Este é o card dos cinco minutos antes da ligação. O radar mostra os quatro
> eixos, e cada um traz duas marcas: o valor e quanta evidência o sustenta.
>
> E aqui está a tese inteira do projeto num clique — cada evidência abre a URL
> real e o trecho literal. O gerente confere a fonte antes da reunião.
>
> A seção "o que mudou" compara esta execução com a anterior. Se apareceu uma
> vaga de MLOps pedindo vLLM e Triton, isso vira a primeira frase da conversa. E
> o cuidado central: se um sinal **sumiu** entre duas coletas, isso é queda de
> confiança, nunca de score. O site pode ter caído; a startup não regrediu.

---

## Slide 8 — O que aprendi (0:30)

**No slide — três decisões de arquitetura que eu levaria para o próximo projeto:**
- **Projetar para falha parcial muda o desenho inteiro.** Assumir que *alguma* empresa vai falhar — site fora do ar, extrator quebrando — leva ao paralelo com isolamento: cada uma com estado e orçamento de retry próprios. Uma fila simples entrega zero quando o primeiro item quebra; este desenho entrega nove de dez.
- **Separar quem julga de quem calcula.** A IA pontua os eixos (leitura, subjetivo); o código calcula TCO, prioridade e o diff (exato, auditável). Misturar os dois é o que produz número inventado exatamente onde se prometeu rastreabilidade.
- **Desacoplar não é elegância, é opção de troca.** Com todo acesso externo atrás de uma porta, trocar modelo, busca ou banco vetorial é configuração — não refatoração. Num campo onde o provedor certo muda a cada trimestre, essa é a decisão que envelhece melhor.

**Roteiro:**
> Três decisões de arquitetura que eu levaria para o próximo projeto — e as três
> vieram de errar antes de acertar.
>
> A primeira: projetar assumindo que **alguma coisa vai falhar** muda o desenho
> inteiro. O óbvio era uma fila: pega empresa, processa, próxima. Mas o trabalho
> é lento e falha com frequência — site fora do ar, página que derruba o
> extrator. Numa fila, o primeiro erro leva o lote junto. Assumindo a falha
> desde o começo, o desenho vira paralelo com isolamento: cada empresa com seu
> estado e seu orçamento de tentativas. A diferença prática, num lote de dez, é
> entregar nove em vez de zero.
>
> A segunda, e a mais importante do projeto: **separar quem julga de quem
> calcula.** Decidir se um dataset descrito num blog é fosso competitivo é
> leitura — é o que um modelo faz bem. Calcular quanto custa migrar, ou em que
> ordem ligar para a carteira, é aritmética — e tem que ser código, senão você
> tem um número inventado exatamente onde prometeu auditabilidade. Sistema de IA
> bom não é o que usa IA em tudo; é o que sabe onde não usar.
>
> A terceira: **desacoplar não é elegância, é opção de troca.** Com todo acesso
> externo atrás de uma porta, trocar de modelo ou de provedor de busca é
> configuração, não refatoração — e eu usei isso mais de uma vez durante o
> desenvolvimento. Num campo em que o provedor certo muda a cada trimestre, essa
> é a decisão que envelhece melhor.

---

## Slide 9 — Próximos passos (0:10)

**No slide:**
- Calibrar `weights.yaml` contra ~50 startups rotuladas à mão (`classifier_eval`)
- Loop de feedback: "concordo / discordo + motivo" no perfil → recalibração
- Visão de portfólio: onde a vulnerabilidade se concentra por setor no Brasil
- Registro de execuções fora da memória (auditoria de longo prazo)

**Roteiro:**
> O próximo passo é dar chão empírico ao score, contra startups rotuladas à mão.
> Depois, capturar a discordância do gerente como dado de recalibração. E subir
> um nível: um mapa de onde a vulnerabilidade se concentra por setor.

---

## Slide 10 — Agradecimento (0:05)

**No slide:**
- *Obrigado.*
- **IA Academy × NVIDIA** — programa Inception
- Repositório: `github.com/PauloRbtoCodes/nvidia-case`

**Roteiro:**
> Obrigado à IA Academy e à NVIDIA pelo desafio. O código, os ADRs e o histórico
> de commits estão no repositório.

---

## Preparação da demo (fazer antes)

```bash
make up            # infra
make migrate
make check         # confirma as 3 chaves e a conectividade
make ingest        # base NVIDIA no Qdrant (precisa da NVIDIA_API_KEY)
make api           # :8000
cd web && npm run dev -- -p 3100    # a demo roda em :3100
```

Telas: **`localhost:3100/busca`** (Demo 1) · **`localhost:3100/fila`** (Demo 2).

- **Abra o navegador com Ctrl+Shift+R na primeira vez.** Bundle antigo em cache
  já custou meia hora de depuração numa sessão — a tela abria, o stream
  conectava e nenhum evento aparecia, porque o JavaScript carregado era o de
  antes da correção.
- **Não reinicie a API depois de começar a preparar a demo.** O registro de
  execuções vive na memória do processo (decisão registrada em `api/runs.py`):
  reiniciar apaga toda varredura em andamento, e a tela fica tentando
  reconectar a uma execução que o servidor não conhece mais.
- Rode **uma varredura de verdade antes** para a fila já ter empresas — a Demo 2
  precisa de dados, e rodar a Demo 1 até o fim consome tempo demais no palco.
- Rode a **mesma varredura duas vezes** (com alguns minutos de intervalo) para a
  seção "o que mudou" ter o que mostrar.
- Screenshots de reserva: tela de varredura em progresso, a fila, um perfil com
  evidência aberta, a seção de diff, o PDF exportado. Guarde em `docs/demo/`.
- Plano B se a API cair no palco: apresentar as duas demos por screenshot, sem
  perder o fio — o roteiro falado é o mesmo.
- **Só um `npm run dev` por vez.** Dois servidores Next escrevendo no mesmo
  `web/.next` corrompem os chunks e a tela quebra com `ChunkLoadError` no meio
  da demo. Se isso acontecer: mate os processos extras, `rm -rf web/.next`,
  suba de novo — e confirme a porta certa antes de continuar.
- **`nim_fast_model` (planner e validador) tem que ser aferido no dia**, não
  presumido: o endpoint gratuito "rápido" da NVIDIA já se mostrou mais lento
  que o "grande" numa sessão anterior (34–50s contra <5s, com timeout incluído)
  — e como é a primeira chamada do grafo, isso trava a varredura inteira antes
  de mostrar qualquer coisa. `make check` não pega isso porque só confirma que
  a chave conecta, não a latência. Rode uma varredura de teste completa antes
  de subir no palco; se travar sem eventos por mais de ~15s depois de
  "Descobrindo empresas", é esse o sintoma.
