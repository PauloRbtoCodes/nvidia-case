---
title: "cuML — quando recomendar"
technology: "cuML"
category: pipeline_dados
url: https://docs.rapids.ai/api/cuml/stable/
---

# cuML — quando recomendar a uma startup

## O que resolve

O cuML traz algoritmos clássicos de aprendizado de máquina executados em GPU —
regressão, árvores e florestas, gradient boosting, k-means, DBSCAN, redução de
dimensionalidade, vizinhos mais próximos — com API próxima da do scikit-learn e
um modo acelerador que cobre código existente sem reescrita.

O problema que resolve é lembrar que **nem todo problema de IA é um LLM**. Muita
startup brasileira AI-native tem um LLM na interface e um modelo tabular no
núcleo do valor: o LLM conversa, o gradient boosting decide o limite de crédito.
O segundo é o que o cliente paga e o que a concorrência não copia, porque depende
do dado histórico da empresa.

Também é a peça que torna viável o que sustenta um RAG sério: clusterização e
redução de dimensionalidade sobre embeddings, para deduplicação semântica,
detecção de tópicos e análise de cobertura da base.

## Sinais de que esta startup precisa

- scikit-learn, XGBoost, LightGBM ou "modelos preditivos" citados em vagas, blog
  ou documentação.
- Produto com decisão numérica no núcleo: risco de crédito, precificação,
  previsão de demanda, churn, detecção de anomalia, manutenção preditiva.
- Base vetorial em uso (Qdrant, pgvector, Pinecone, Weaviate) com volume grande —
  há embeddings sobre os quais clusterizar e deduplicar.
- Menção a retreino periódico, validação cruzada ou busca de hiperparâmetros:
  todas são operações que se repetem muitas vezes e onde tempo vira dinheiro.
- Vaga de cientista de dados com foco em modelagem preditiva, não em NLP.

## Quando NÃO recomendar

- **Quando o produto é só interface sobre LLM.** Não há modelo clássico no
  caminho, e o gap real da empresa está em outro eixo.
- **Quando o dataset é pequeno.** Modelos tabulares treinam em segundos em CPU;
  acelerar segundos não muda decisão nenhuma.
- **Quando o problema é qualidade de feature, não tempo de treino.** GPU faz
  treinar mais rápido um modelo ruim.

## Pré-requisitos

Pipeline de modelagem existente em Python, dado histórico rotulado, e acesso a
GPU. Como no cuDF, comparar resultado com o baseline é parte obrigatória.

## Complexidade

**Baixa a média.** Dias no caminho do acelerador; semanas quando envolve
reorganizar o pipeline de treino e a busca de hiperparâmetros para aproveitar o
ganho de verdade.

## Eixo de defensibilidade

**Dados proprietários** (`proprietary_data`). O modelo tabular treinado sobre o
histórico da empresa é, com frequência, o ativo mais defensável que ela tem — e o
que menos aparece no material de marketing, porque não é o que está na moda.

## Primeira ação sugerida

Perguntar ao time quanto tempo leva um ciclo completo de retreino e avaliação do
modelo preditivo principal, e quantas vezes por mês ele roda. Se o número for
"horas" e "poucas vezes", propor acelerar esse ciclo específico — o argumento é
quantas hipóteses a mais o time consegue testar por trimestre, não a velocidade
em si.
