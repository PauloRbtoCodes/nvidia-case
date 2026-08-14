---
title: "Como ler e escrever os cards de recomendação"
category: meta
---

# Cards de recomendação — o enriquecimento manual da KB

A documentação oficial da NVIDIA explica **o que** cada tecnologia faz. Ela nunca
explica **quando recomendá-la a uma startup específica**, porque não é para isso
que ela existe. Sem estes cards, o RAG responde muito bem "o que é o Triton" e
falha exatamente na pergunta que o sistema precisa responder: "qual tecnologia
serve para esta startup, dado este gap".

Os cards são ingeridos junto com as URLs oficiais, com `doc_type: card`.

## Estrutura obrigatória

O campo `technology` do front matter **precisa casar exatamente** com o valor em
`AXIS_TO_NVIDIA_FAMILY` (`src/radar/models/scoring.py`). O gate determinístico
usa aquelas strings como filtro de payload no Qdrant: um card com
`technology: NIM Microservices` em vez de `NIM` é invisível para a busca, e a
recomendação é bloqueada pelo guardrail de citação sem que ninguém entenda por
quê. `tests/test_cards.py` trava essa correspondência.

Seções, na ordem:

| Seção | Por que existe |
|---|---|
| **O que resolve** | O problema, não a lista de features |
| **Sinais de que esta startup precisa** | **Observáveis pelo scraper.** É o que casa com o perfil |
| **Quando NÃO recomendar** | A seção mais valiosa. Ver abaixo |
| **Pré-requisitos** | O que precisa existir antes; alimenta `complexity` |
| **Complexidade** | `baixa` (dias) · `media` (semanas) · `alta` (meses) |
| **Eixo de defensibilidade** | Qual gap do score isto endereça — torna a recomendação causal |
| **Primeira ação sugerida** | Ação concreta para o time NVIDIA, não "agendar reunião" |

## Por que "Quando NÃO recomendar" é a seção mais importante

Um sistema que só sabe recomendar recomenda sempre. Recomendar GPU dedicada para
uma startup com 5M tokens/mês, ou fine-tuning para quem não tem dado proprietário,
queima a conversa inteira no primeiro contato — e queima com um founder técnico,
que é exatamente quem o programa mais quer atrair.

Estas seções são o que permite ao motor produzir a resposta honesta: *"pelo seu
volume, migrar ainda não compensa; a conversa aqui é sobre latência ou residência
de dados, não sobre custo"*. Essa frase vale mais que três recomendações.

## Regra de escrita

**Nenhum número de desempenho inventado.** Os cards viram citação no briefing;
um throughput fabricado aqui chega ao founder como se fosse dado da NVIDIA. Onde
número importa, ele vive em `src/radar/scoring/weights.yaml`, versionado e com
data — nunca no texto de um card.
