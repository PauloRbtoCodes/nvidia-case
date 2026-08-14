---
title: "RAPIDS — quando recomendar"
technology: "RAPIDS"
category: pipeline_dados
url: https://rapids.ai/
---

# RAPIDS — quando recomendar a uma startup

## O que resolve

RAPIDS é o conjunto de bibliotecas de ciência de dados aceleradas em GPU —
cuDF para DataFrames, cuML para aprendizado de máquina clássico, cuGraph para
grafos — com APIs deliberadamente parecidas com as do ecossistema pandas e
scikit-learn.

O problema que resolve é o **tempo de ciclo**. Numa startup de IA, o gargalo
raramente é treinar o modelo grande; é o pipeline que roda antes: agregar,
juntar, deduplicar, gerar features, avaliar. Quando esse pipeline leva seis
horas, o time testa uma hipótese por dia. Quando leva vinte minutos, testa vinte.
A diferença não é de infraestrutura, é de velocidade de aprendizado do produto —
e essa velocidade é defensabilidade.

## Sinais de que esta startup precisa

- Volume de dados tabulares ou de eventos alto: telemetria, transações,
  logs de uso, séries temporais de sensores.
- Menção a Spark, Databricks, Airflow, dbt ou "pipeline de dados" no site ou nas
  vagas — há pipeline, e onde há pipeline há espera.
- Produto que combina modelo de linguagem com sinal estruturado: scoring de
  crédito, detecção de fraude, previsão de demanda, otimização logística.
- Blog ou vaga citando tempo de processamento, janela de batch noturno, ou
  "reprocessamento" como problema.
- Vaga de engenheiro de dados ou cientista de dados com menção a pandas,
  scikit-learn ou grandes volumes.
- Loop de feedback que exige recomputar features com frequência.

## Quando NÃO recomendar

- **Quando o dado cabe confortavelmente na memória e o pipeline roda em minutos.**
  Não há problema a resolver, e propor GPU aqui parece solução procurando
  problema.
- **Quando o gargalo é de I/O ou de arquitetura, não de computação.** Se o
  pipeline espera por uma API externa ou por um banco mal indexado, a GPU fica
  ociosa junto.
- **Quando o time é só de engenharia de software, sem ninguém em dados.** A
  adoção exige alguém que entenda o pipeline atual para saber o que acelerar.

## Pré-requisitos

Acesso a GPU (local, nuvem ou notebook gerenciado) e um pipeline existente em
Python que sirva de baseline. Sem baseline não há como demonstrar ganho — e o
ganho é todo o argumento.

## Complexidade

**Média.** Semanas, e frequentemente menos: a proximidade de API com pandas e
scikit-learn faz boa parte do código migrar com pouca alteração. O tempo vai em
validar que os resultados batem com o pipeline antigo, que é o passo que ninguém
pode pular.

## Eixo de defensibilidade

**Dados proprietários** (`proprietary_data`). Não porque cria dado, mas porque
encurta o ciclo entre coletar dado e extrair valor dele — que é o que faz um
dataset proprietário virar produto melhor em vez de custo de armazenamento.

## Primeira ação sugerida

Escolher com o time a etapa mais lenta do pipeline atual, cronometrá-la como está
e reescrevê-la com cuDF/cuML numa sessão conjunta. O ganho medido nessa etapa
única é o que justifica (ou não) estender ao resto — e a medição é da startup.
