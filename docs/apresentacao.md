# Roteiro — Apresentação (7 min + demo)

> NVIDIA Startup AI Radar · IA Academy × NVIDIA
>
> Dez slides, ~7 minutos. As duas demos são ao vivo; se a API cair, há
> screenshots de reserva na pasta `docs/demo/` (gere antes com o passo a passo
> no fim deste arquivo). O tempo de cada slide está no cabeçalho — o total
> fecha em ~6:50, com 10s de folga.
>
> Regra que atravessa os slides de **solução**, **arquitetura** e **pipeline**:
> cada um termina com o trade-off que foi aceito ali. A banca vai perguntar; é
> melhor eu levantar antes.

---

## Slide 1 — O problema (0:45)

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

## Slide 3 — Arquitetura (0:50)

**No slide:**
- Diagrama de dois níveis: grafo externo (plan → discover → `Send` fan-out → consolidate) e o subgrafo por empresa
- Etiquetas: LangGraph · estado tipado · arestas condicionais · retry em dois níveis
- Caixa "portas": `NodeDeps` injeta `llm`, `search`, `fetcher`, `retriever` — dublês nos testes
- Rodapé — **Trade-off:** *corte horizontal (models/scraping/rag/llm/scoring), não fatias verticais → preserva a fronteira "toca a rede / não toca", que é o que permite 346 testes offline. Custo: um conceito se espalha por várias pastas — compensado pelo mapa "conceito → arquivos" no README.*

**Roteiro:**
> São dois níveis de grafo. O externo descobre empresas e distribui via `Send`,
> que é map-reduce. Cada empresa roda num subgrafo isolado, com seu próprio
> orçamento de retry — uma startup cujo site derruba o extrator não pode derrubar
> o lote inteiro.
>
> A peça que mais me salvou foi a injeção de dependências: todo nó recebe um
> objeto `NodeDeps` com o cliente de LLM, o scraper, o retriever. Nos testes eu
> troco os quatro por dublês — e é por isso que a suíte inteira roda sem rede,
> sem chave de API e sem Docker.
>
> **O trade-off:** cortei o repositório por preocupação técnica — `models`,
> `scraping`, `rag`, `scoring` — e não por fatia de negócio. Isso preserva a
> fronteira "toca a rede / não toca": `scoring` é aritmética pura, testável sem
> mock nenhum. O custo é que entender "defensibilidade" de ponta a ponta exige
> abrir cinco pastas. A compensação foi documentação — um mapa conceito→arquivo
> no README — e não uma semana de refatoração no fim do prazo.

---

## Slide 4 — A pipeline (0:50)

**No slide:**
- A cadeia do subgrafo, com o ciclo de re-coleta marcado:
  `collect ⇄ extract → validate` → `classify` → `score + TCO` → **`compare`** → `RAG → rerank` → `recommend` → `briefing`
- Marcadores: *evidência obrigatória* · *grounding verificado em código* · *teto de re-coleta*
- Rodapé — **Trade-off:** *recomendação sem citação da base é **bloqueada**, não "melhor esforço" → menos recomendações, mais confiáveis. Três com fundamento valem mais numa reunião que oito plausíveis.*

**Roteiro:**
> Por empresa: coleta o site e as fontes de sinal — vaga de emprego é a fonte
> mais honesta de stack —, extrai o perfil, e um validador de evidência decide se
> vale voltar ao scraper, com teto para não virar loop.
>
> Aí entra o que separa este projeto de um wrapper de LLM sofisticado: **o
> grounding é verificado em código, não confiado ao prompt.** O prompt manda
> copiar o trecho literal; comparação de string confirma que ele existe mesmo na
> fonte. E a recomendação sem citação recuperada da base NVIDIA levanta erro —
> não degrada para "melhor esforço".
>
> **O trade-off:** o sistema falha para menos. Bloquear em vez de degradar
> significa perder uma recomendação correta quando o RAG não recupera. Aceitei
> conscientemente: alucinar sobre o que o Triton faz, diante de um founder
> técnico, custa a credibilidade do programa inteiro.

---

## Slide 5 — Demo 1: a varredura ao vivo (1:00)

**No slide:** só o título *"Demo — descobrir e diagnosticar"* e o print da tela de varredura em segundo plano.

**O que fazer:**
1. Abrir `/varredura`, digitar *"startups brasileiras de IA para saúde"*, 5 empresas.
2. Apontar o **stream ao vivo**: cada linha é um nó do grafo executando — planejar, descobrir, coletar, extrair, auditar, pontuar.
3. Enquanto roda, dizer a frase de baixo. Não esperar terminar — cortar para o slide 6 quando as primeiras empresas aparecerem.

**Roteiro:**
> Descrevo o setor como falaria com um colega. O modelo traduz isso em buscas
> reais, filtra o que é notícia e diretório — só site de empresa passa — e
> começa a diagnosticar. Cada linha aqui é um agente do grafo executando de
> verdade; o progresso vem por Server-Sent Events direto do LangGraph. Isso é o
> Entregável 1, 2 e 3 rodando juntos.

---

## Slide 6 — Diferenciais (0:55)

**No slide — quatro linhas, sem sub-bullets:**
1. **Score ≠ confiança.** Startup discreta produz pouca evidência. "Não achamos" nunca vira "é fraca".
2. **Recomendação causal.** Sai do eixo mais fraco ponderado por peso *e* confiança — não de regra por setor.
3. **Honestidade como instrumento.** O TCO diz "migrar ainda não compensa" quando é verdade.
4. **Gatilho temporal.** Radar detecta *mudança* — é o "por que a conversa é agora".

**Roteiro:**
> Quatro coisas que quase nenhum sistema de lead scoring tem.
>
> Primeira: score e confiança são números separados. Uma startup sem blog de
> engenharia não é menos defensável, é menos observável — e misturar as duas
> gera injustiça com aparência de rigor.
>
> Segunda: a recomendação é rastreável até o diagnóstico. Dá para responder "por
> que TensorRT-LLM para esta empresa" apontando o eixo, a severidade e a
> evidência.
>
> Terceira: quando migrar para GPU dedicada não compensa, o sistema **diz isso**.
> Honestidade calibrada é o que ganha credibilidade com founder técnico.
>
> Quarta, e a que fecha o nome do produto: o gatilho temporal.

---

## Slide 7 — Demo 2: o perfil, o radar e o gatilho (1:10)

**No slide:** título *"Demo — a decisão e a prova"*.

**O que fazer:**
1. Da fila, abrir uma empresa (ex.: Voa Health ou NeuralMed).
2. Mostrar o **radar de eixos**: a barra grossa é o valor, a fina é a evidência que sustenta. Num eixo de baixa confiança, o número some e a barra vira hachura — *"não sabemos", não "nota baixa"*.
3. **Clicar numa evidência** — abre a URL real com o trecho literal citado. Essa é a tese do projeto: nenhuma afirmação sem fonte.
4. Descer até **"O que mudou desde a última varredura"** (se houver histórico) ou explicá-la pelo slide: o diff eixo a eixo, com a evidência nova como manchete.
5. Fechar no invariante: *sinal que sumiu de uma coleta para a outra vira queda de **confiança**, nunca queda de **score** — página fora do ar não é regressão da empresa.*

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

## Slide 8 — O que aprendi (0:20)

**No slide — três linhas:**
- Construir contra API indisponível **forçou** a arquitetura de portas — a restrição virou 346 testes offline.
- **Trilha de auditoria que mente é pior que trilha ausente** — o bug em que `weights_version` gravava a versão errada.
- Prompt é instrução, não garantia — o Nemotron vazava raciocínio no `content` e o `max_tokens` cortava o JSON no meio.

**Roteiro:**
> Três lições. A falta de acesso às APIs reais me obrigou a injetar tudo — e essa
> restrição virou a maior força do projeto, a suíte que roda em qualquer lugar.
> Aprendi que uma trilha de auditoria que mente é pior que não ter nenhuma. E
> aprendi na prática que prompt é instrução, não garantia: o modelo obedecia o
> formato e ainda assim quebrava, por raciocínio vazado ou resposta cortada.

---

## Slide 9 — Próximos passos (0:15)

**No slide:**
- Calibrar `weights.yaml` contra ~50 startups rotuladas à mão (`classifier_eval`)
- Loop de feedback: "concordo / discordo + motivo" no perfil → recalibração
- Visão de portfólio: onde a vulnerabilidade se concentra por setor no Brasil
- Export PDF · registro de execuções fora da memória

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
make web           # :3000
```

- Rode **uma varredura de verdade antes** para a fila já ter empresas — a Demo 2
  precisa de dados, e rodar a Demo 1 até o fim consome tempo demais no palco.
- Rode a **mesma varredura duas vezes** (com alguns minutos de intervalo) para a
  seção "o que mudou" ter o que mostrar.
- Screenshots de reserva: tela de varredura em progresso, a fila, um perfil com
  evidência aberta, a seção de diff. Guarde em `docs/demo/`.
- Plano B se a API cair no palco: apresentar as duas demos por screenshot, sem
  perder o fio — o roteiro falado é o mesmo.
