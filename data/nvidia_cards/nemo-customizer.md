---
title: "NeMo Customizer — quando recomendar"
technology: "NeMo Customizer"
category: customizacao_modelos
url: https://www.nvidia.com/en-us/ai-data-science/products/nemo/
---

# NeMo Customizer — quando recomendar a uma startup

## O que resolve

O NeMo Customizer é o microsserviço de **customização de modelo**: fine-tuning
supervisionado e adaptadores LoRA sobre modelos abertos, expostos por API em vez
de por scripts de treino. Ele transforma "fine-tuning" de projeto de pesquisa em
etapa de pipeline — submete-se um dataset, recebe-se um modelo customizado
servível pelo NIM.

O problema que resolve é converter dado proprietário em **vantagem que aparece no
produto**. Uma startup pode ter o melhor dataset do setor e ainda assim entregar
a mesma resposta que qualquer concorrente, se o dado só é usado como contexto de
RAG. O fine-tuning é o mecanismo que faz o dado virar comportamento do modelo:
vocabulário do domínio, formato de saída, tom, convenções que nenhum prompt
genérico reproduz.

## Sinais de que esta startup precisa

- Dataset proprietário **já curado** — este card vem depois do NeMo Curator.
- Domínio com linguagem própria que modelos genéricos erram: jurídico brasileiro,
  codificação médica, contabilidade fiscal, terminologia industrial.
- Loop de feedback em produção: usuários corrigindo saídas, revisores aprovando
  ou rejeitando. É dado de preferência pronto, e é o mais valioso que existe.
- Prompts gigantes descritos publicamente, ou menção a "engenharia de prompt"
  como diferencial: sintoma de que o modelo está sendo instruído a cada
  requisição sobre algo que deveria ter aprendido uma vez.
- Necessidade de formato de saída rígido — JSON com esquema fixo, laudo
  estruturado, campos obrigatórios — onde o modelo genérico erra com frequência.
- Vaga de ML engineer ou pesquisador aplicado.

## Quando NÃO recomendar

- **Quando não há dado proprietário.** Fine-tuning sem dado próprio é a forma mais
  rápida de parecer que não se leu o site da empresa. É o erro mais caro que este
  sistema pode cometer.
- **Quando o problema é conhecimento factual e não comportamento.** Fato que muda
  toda semana pertence ao RAG, não aos pesos. Fine-tunar para "saber" o catálogo
  de produtos cria um modelo desatualizado no mês seguinte.
- **Quando não existe conjunto de avaliação.** Sem medir, ninguém sabe se o
  fine-tuning melhorou ou piorou — e modelos customizados regridem em
  capacidades gerais de formas silenciosas.
- **Quando o modelo base ainda não foi testado a sério.** Muita gente fine-tuna
  antes de descobrir que um prompt melhor e alguns exemplos resolviam.

## Pré-requisitos

Dataset curado com base legal para uso, conjunto de avaliação rotulado (mesmo
pequeno, mesmo manual), e um lugar para servir o modelo resultante — na prática,
NIM.

## Complexidade

**Alta.** Meses até estar em produção com confiança: preparar os dados, treinar,
avaliar contra o baseline, decidir sobre regressões, e montar o processo de
retreino. LoRA reduz o custo computacional, não o custo de avaliação — que é onde
o tempo realmente vai.

## Eixo de defensibilidade

**Dados proprietários** (`proprietary_data`). É o card que fecha o ciclo do eixo:
o dado deixa de ser um ativo declarado e passa a ser uma diferença que o cliente
percebe na resposta.

## Primeira ação sugerida

Antes de qualquer treino, propor a construção de um conjunto de avaliação de
50 a 100 casos reais do domínio, rotulados pelo time. Rodar o modelo base contra
ele e medir. Esse número é o que decide se fine-tuning faz sentido — e, se fizer,
é o mesmo número que vai provar que funcionou.
