---
title: "Triton Inference Server — quando recomendar"
technology: "Triton Inference Server"
category: inferencia
url: https://developer.nvidia.com/triton-inference-server
---

# Triton Inference Server — quando recomendar a uma startup

## O que resolve

O Triton é um servidor de inferência aberto que atende **vários modelos e vários
frameworks no mesmo processo**: TensorRT, PyTorch, ONNX, backends em Python. Ele
faz batching dinâmico, executa modelos concorrentemente na mesma GPU, permite
compor *ensembles* (uma requisição atravessa pré-processamento, modelo e
pós-processamento como um pipeline só) e expõe métricas por modelo.

O problema que resolve não é servir *um* LLM — para isso o NIM é mais direto. É
servir **um zoológico**: quando a startup tem um LLM, um modelo de embedding, um
classificador, um OCR e um reranker, e cada um está numa GPU subutilizada ou num
serviço separado com sua própria operação.

## Sinais de que esta startup precisa

- Vários modelos citados no site, na documentação ou nas vagas — não só um LLM.
  Visão computacional junto com NLP é o caso clássico.
- Pipeline com etapas encadeadas: OCR → extração → classificação, ou ASR →
  diarização → resumo. Cada seta é uma chamada de rede que o Triton elimina.
- Modelo próprio treinado em PyTorch ou TensorFlow convivendo com LLM de
  terceiros: o Triton é o ponto onde os dois passam a ser operados igual.
- Menção a GPU ociosa, a custo de manter vários serviços de inferência, ou a
  filas separadas por modelo.
- Vaga de MLOps que mencione servir modelos, versionamento de modelo ou A/B de
  modelos em produção.

## Quando NÃO recomendar

- **Quando há um único LLM em jogo.** O NIM já entrega isso empacotado, com menos
  operação. Recomendar Triton aqui adiciona uma peça de infraestrutura para
  resolver um problema que a startup não tem.
- **Quando o time não opera Kubernetes nem container em produção.** O Triton
  pressupõe uma cultura de operação que nem toda startup em estágio inicial tem;
  sem ela, ele vira mais uma coisa quebrada às três da manhã.
- **Quando o gargalo é o modelo e não a serving.** Se a qualidade não está boa, um
  servidor melhor não muda nada.

## Pré-requisitos

Modelos exportáveis para um formato suportado, container e orquestração em
produção, e alguém responsável por dimensionar GPU. Repositório de modelos
versionado é fortemente recomendado — sem ele, o *model control* do Triton vira
deploy manual com passos extras.

## Complexidade

**Média.** Semanas: montar o repositório de modelos, definir configuração por
modelo, calibrar batching dinâmico e instrumentar as métricas. Sobe para alta
quando envolve ensembles com lógica de negócio em backend Python.

## Eixo de defensibilidade

**Domínio da stack** (`stack_ownership`). Consolidar a serving de vários modelos
é o que transforma um conjunto de scripts em plataforma — e plataforma é o que a
concorrência não copia num trimestre.

## Primeira ação sugerida

Mapear com o time quais modelos rodam hoje, em que hardware e com qual
utilização média. Se o mapa mostrar três ou mais modelos em GPUs separadas e
subutilizadas, propor uma prova de conceito consolidando dois deles no Triton com
batching dinâmico, medindo utilização antes e depois.
