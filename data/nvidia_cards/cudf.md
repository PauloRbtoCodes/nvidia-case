---
title: "cuDF — quando recomendar"
technology: "cuDF"
category: pipeline_dados
url: https://docs.rapids.ai/api/cudf/stable/
---

# cuDF — quando recomendar a uma startup

## O que resolve

O cuDF é a biblioteca de DataFrames em GPU do RAPIDS, com API modelada na do
pandas. O detalhe que importa para uma startup é o modo acelerador
(`cudf.pandas`): código pandas existente passa a executar em GPU sem reescrita,
caindo de volta para a CPU nas operações ainda não suportadas.

O problema que resolve é **o custo de adoção**. A objeção real de um time pequeno
nunca é "GPU não ajudaria"; é "não vou reescrever seis meses de notebooks e ETL
para descobrir se ajuda". O acelerador remove essa objeção: a decisão vira um
experimento de uma tarde.

## Sinais de que esta startup precisa

- pandas mencionado explicitamente em vagas, blog técnico ou repositório público.
- ETL, feature engineering ou preparação de dataset descritos como parte do
  trabalho de engenharia.
- Volume de dados tabulares que já força *chunking*, amostragem ou processamento
  noturno — sinal de que a memória e o tempo já apertam.
- Notebooks como parte do fluxo de trabalho do time de dados.
- Cientista de dados no time, mas nenhum engenheiro de infraestrutura de dados: é
  exatamente o perfil que se beneficia de ganho sem reescrita.

## Quando NÃO recomendar

- **Quando o pipeline já é distribuído e roda bem em Spark.** Aí a conversa é
  outra, e não passa por trocar a biblioteca de DataFrame.
- **Quando o volume é pequeno.** pandas em CPU resolve, e a transferência de dados
  para a GPU pode custar mais do que a operação economiza.
- **Quando o processamento é dominado por lógica em Python puro linha a linha.**
  O ganho vem de operações vetorizadas; um laço `for` sobre linhas continua lento
  em qualquer lugar, e a conversa útil ali é sobre reescrever o laço.

## Pré-requisitos

Código em pandas que sirva de baseline, acesso a GPU, e um resultado conhecido
para comparar — a validação de que os números batem é obrigatória, não opcional.

## Complexidade

**Baixa.** Dias, às vezes horas, pelo modo acelerador. É o card de menor esforço
de adoção do eixo de dados, e por isso costuma ser a melhor porta de entrada
técnica para uma startup que ainda não tem relação com a NVIDIA.

## Eixo de defensibilidade

**Dados proprietários** (`proprietary_data`), pelo mesmo mecanismo do RAPIDS:
encurtar o ciclo de experimentação sobre o dado próprio.

## Primeira ação sugerida

Propor uma sessão curta com o notebook mais lento do time: ativar o acelerador do
cuDF, rodar de novo e comparar tempo e resultado. É uma demonstração que cabe
numa reunião e cujo resultado pertence à startup, não à apresentação.
