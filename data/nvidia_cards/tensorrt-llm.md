---
title: "TensorRT-LLM — quando recomendar"
technology: "TensorRT-LLM"
category: inferencia
url: https://github.com/NVIDIA/TensorRT-LLM
---

# TensorRT-LLM — quando recomendar a uma startup

## O que resolve

O TensorRT-LLM é uma biblioteca aberta que compila modelos de linguagem para o
hardware NVIDIA alvo, produzindo um motor de inferência otimizado. Ele traz
técnicas que separam uma inferência caseira de uma inferência de produção:
quantização, *in-flight batching* (requisições novas entram no lote sem esperar
as anteriores terminarem), cache de KV paginado, e paralelismo de tensor e de
pipeline para modelos que não cabem numa GPU só.

O problema que resolve é **custo unitário sob carga**. Uma startup que já
self-hospeda modelo com um servidor genérico costuma estar pagando por GPU
ociosa entre requisições e por memória desperdiçada em cache de contexto.

## Sinais de que esta startup precisa

- A empresa **já** roda inferência própria — este card só faz sentido depois do
  self-hosting existir. É otimização, não porta de entrada.
- Menção pública a vLLM, SGLang, Ollama, TGI ou "servidor de inferência próprio".
- Volume alto e contínuo: atendimento, transcrição, moderação, qualquer produto
  em que o modelo roda a cada interação do usuário final, e não sob demanda.
- Vaga de engenharia de ML/infraestrutura que cite CUDA, quantização, throughput,
  otimização de inferência ou "performance de modelos".
- Blog de engenharia com post sobre latência, batching ou custo de GPU: é o
  perfil que lê a documentação antes da reunião e valoriza a conversa técnica.
- Modelo próprio ou fine-tunado, que precisa ser servido com eficiência e não
  tem um endpoint gerenciado equivalente.

## Quando NÃO recomendar

- **Quando a startup ainda usa API externa.** O passo anterior é o NIM. Sugerir
  compilação de modelo a quem nunca serviu um modelo pula duas etapas e soa
  desconectado da realidade do time.
- **Quando o volume é baixo ou intermitente.** Otimizar throughput de uma carga
  que não existe é engenharia sem retorno; o gargalo real ali provavelmente é
  produto, não inferência.
- **Quando o time não tem ninguém com afinidade com infraestrutura de GPU.** O
  ganho é real, mas a compilação e o ajuste consomem tempo de engenharia que uma
  equipe pequena raramente tem sobrando. NIM entrega parte do benefício já
  empacotada, sem esse custo.

## Pré-requisitos

Inferência própria já em produção, GPU NVIDIA compatível com o nível de
quantização pretendido, e um pipeline de avaliação capaz de detectar perda de
qualidade — quantizar sem medir troca custo por regressão silenciosa.

## Complexidade

**Média a alta.** Semanas: converter o modelo, validar qualidade após
quantização, ajustar o dimensionamento de lote e de memória, e integrar ao
servidor. Alta quando envolve modelo customizado ou paralelismo entre GPUs.

## Eixo de defensibilidade

**Domínio da stack** (`stack_ownership`). É o card que mais fundo vai nesse eixo:
controlar custo e latência da própria inferência é precisamente a capacidade que
um repassador de API não tem.

## Primeira ação sugerida

Propor um benchmark comparativo com a carga real da startup — o servidor atual
contra o mesmo modelo compilado com TensorRT-LLM — medindo tokens por segundo,
latência de primeiro token e custo por milhão de tokens. Oferecer apoio técnico
na conversão e deixar o resultado com a empresa, inclusive se ele não justificar
a migração.
