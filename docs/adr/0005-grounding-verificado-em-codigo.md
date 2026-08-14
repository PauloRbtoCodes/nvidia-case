# ADR 0005 — Grounding verificado em código, não confiado ao prompt

- **Status:** aceito
- **Data:** 2026-08-14
- **Relacionado:** ADR 0004 (evidência obrigatória), ADR 0003 (recomendação causal)

## Contexto

O ADR 0004 estabeleceu que nenhuma afirmação existe sem citação, e o `Recommendation`
já levanta `ValidationError` quando `kb_citations` está vazio. Ao montar o grafo,
ficou evidente que isso cobre metade do problema.

Os prompts do Extractor e do Recommender mandam, em letras maiúsculas, copiar o
trecho literalmente. Prompt é instrução, não garantia. Os dois modos de falha que
sobram passam por toda a validação existente:

1. **O Extractor "melhora" a citação.** Reescreve o parágrafo do site de forma mais
   fluente e o anexa como `excerpt`. O `Evidence` é válido, o campo é
   `is_grounded`, o `evidence_coverage` sobe — e o briefing exibe uma frase que a
   startup nunca escreveu, com uma URL ao lado dando aparência de prova.
2. **O Recommender inventa o chunk.** Devolve uma `Recommendation` com
   `kb_citations` preenchido por um trecho plausível de documentação NVIDIA que
   nunca foi recuperado. O validador do modelo aprova: há citação. Ninguém checou
   se ela veio da busca.

O segundo é o caro. Uma alucinação sobre o que o Triton faz, dita a um founder
técnico numa reunião do Inception, custa a credibilidade do programa — que é
exatamente o risco que o ADR 0004 se propôs a eliminar.

Descobrir isso com uma segunda chamada de LLM seria pagar caro por algo que
comparação de string resolve.

## Decisão

Cada saída de modelo que carrega citação é **reconciliada contra a fonte real**,
em código, antes de entrar no estado do grafo.

### No Extractor — literalidade

`podar_nao_literais` percorre os campos `EvidenceBackedField` e descarta toda
`Evidence` cujo `excerpt`, normalizado por espaços em branco e caixa, não ocorra
no texto que foi entregue ao modelo. Campo que perde todas as evidências volta a
`None` — **desconhecido, nunca falso**, coerente com o ADR 0002.

Os campos anulados viram nota de validação e chegam aos `caveats` do briefing: o
gerente precisa saber que "setor" está vazio porque a extração não se sustentou,
e não porque a empresa não tem setor.

Sinais técnicos, founders e rodadas perdem a evidência inválida mas sobrevivem
como item — descartar a menção a "Triton" porque o modelo parafraseou o parágrafo
em volta jogaria fora um sinal que o próprio scraper reconstrói adiante, por
vocabulário fixo e com o parágrafo literal anexado.

### No Recommender — procedência

`sanear_recomendacoes` troca cada citação escrita pelo modelo pelo objeto
`RetrievedChunk` que de fato saiu da busca, casando por URL e depois por
sobreposição de prefixo de texto. Recomendação cujas citações não sobrevivem à
reconciliação é **descartada**, não degradada.

Devolver o chunk recuperado (e não o do modelo) tem um efeito colateral desejado:
os scores de RRF e de rerank chegam intactos ao briefing, e a citação exibida é
literalmente a que foi indexada.

### Divisão de trabalho com o Evidence Validator

O Evidence Validator (LLM) continua existindo e não foi substituído. Os dois
fazem perguntas diferentes:

| Barreira | Pergunta | Custo |
|---|---|---|
| Poda mecânica (código) | O trecho **existe** na fonte? | comparação de string |
| Evidence Validator (LLM) | O trecho **sustenta** o que o campo afirma? | uma chamada rápida |

A segunda pergunta exige julgamento sobre linguagem. A primeira não, e gastar
cota de modelo nela seria desperdício num projeto cuja quota é grátis e limitada.

## Consequências

**Positivas**

- O invariante do ADR 0004 passa a ser imposto por mecanismo, não por redação de
  prompt. Trocar o modelo ou a versão do prompt não afrouxa a garantia.
- A regressão fica testável sem rede: `test_campo_com_citacao_nao_literal_e_anulado`
  e `test_citacao_inventada_derruba_a_recomendacao` cobrem os dois modos de falha
  com dublês.
- O descarte é registrado, nunca silencioso. Silenciar esconderia do gerente que
  o sistema quase recomendou algo sem fundamento.

**Negativas**

- Recall menor no Extractor: uma citação correta que o modelo normalizou de forma
  agressiva (troca de aspas, remoção de hífen) é descartada junto com as
  inventadas. A normalização atual cobre só espaço em branco e caixa; ampliá-la
  aumentaria o recall e afrouxaria a garantia na mesma proporção. Preferimos
  errar para o lado de perder um campo.
- O casamento por prefixo do Recommender aceita um chunk cuja URL bate e cujo
  texto é irreconhecível **quando aquela URL tem um único chunk recuperado**. É
  uma concessão consciente: nesse caso a fonte continua rastreável, que é o que o
  ADR 0004 exige.
- Duas barreiras significam duas chances de descartar informação boa. O
  contrapeso está no fluxo, não aqui: o validador pede re-coleta, e o orçamento
  de `MAX_SCRAPE_ATTEMPTS` dá ao sistema uma segunda tentativa antes de declarar
  a lacuna.
