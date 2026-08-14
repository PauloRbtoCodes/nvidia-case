---
title: "NeMo Curator — quando recomendar"
technology: "NeMo Curator"
category: pipeline_dados
url: https://www.nvidia.com/en-us/ai-data-science/products/nemo/
---

# NeMo Curator — quando recomendar a uma startup

## O que resolve

O NeMo Curator é uma biblioteca para **curadoria de dados em escala** antes do
treino ou da customização de modelo: extração de texto de formatos brutos,
limpeza, identificação de idioma, filtros de qualidade, deduplicação exata,
difusa e semântica, e remoção de informação pessoal identificável. As etapas
pesadas são aceleradas em GPU.

O problema que resolve é o que separa "temos muito dado" de "temos um dataset".
Uma startup que acumulou três anos de documentos de clientes tem um passivo de
armazenamento, não um ativo — enquanto ninguém deduplicou, filtrou lixo e
removeu dado pessoal. A deduplicação semântica costuma ser a surpresa: em corpus
de domínio (contratos, laudos, chamados de suporte), a repetição quase-idêntica é
enorme, e treinar sobre ela desperdiça computação e enviesa o modelo.

## Sinais de que esta startup precisa

- Alegação pública de dado proprietário — "milhões de documentos", "anos de
  histórico", "maior base de X do Brasil" — **sem** menção a como esse dado é
  tratado. A alegação sem o pipeline é exatamente o gap.
- Dado que chega de integração com sistema do cliente: ERP, PDV, prontuário, CRM.
  Volume alto e qualidade heterogênea por construção.
- Domínio regulado em que remoção de dado pessoal não é opcional: saúde,
  jurídico, financeiro, RH.
- Vaga aberta de engenheiro de dados, de "data quality" ou de curadoria.
- Intenção declarada de treinar ou fine-tunar modelo próprio — a curadoria é o
  passo anterior, e é o passo que costuma ser subestimado.
- Loop de feedback com rotulagem por usuários: gera dado valioso e desorganizado
  na mesma proporção.

## Quando NÃO recomendar

- **Quando o dado não é da startup.** Se o produto opera apenas sobre o que o
  usuário cola no prompt, ou sobre conteúdo público, não há dataset a curar — e
  esse é o diagnóstico honesto, não uma oportunidade de recomendação.
- **Quando o volume é pequeno.** Alguns milhares de documentos se tratam com
  ferramentas comuns; trazer pipeline acelerado em GPU para isso é
  desproporcional e o founder percebe.
- **Quando a startup ainda não sabe o que vai fazer com o dataset.** Curadoria é
  meio. Sem um objetivo — fine-tuning, avaliação, RAG de qualidade — o projeto
  não termina.

## Pré-requisitos

Dado acessível em volume, base legal clara para usá-lo (contrato com o cliente,
consentimento, anonimização — no Brasil, LGPD), e infraestrutura para processá-lo.

## Complexidade

**Alta.** Meses, quando feito de verdade: mapear as fontes, definir critérios de
qualidade do domínio, validar a remoção de dado pessoal, e estabelecer o processo
recorrente. É o card de maior esforço e também o de maior retorno em
defensibilidade.

## Eixo de defensibilidade

**Dados proprietários** (`proprietary_data`), que é o eixo de maior peso do
score. É o que um laboratório de fronteira não consegue replicar com um anúncio
de feature: ele pode copiar a interface, não o dado de um cliente que não é dele.

## Primeira ação sugerida

Propor um diagnóstico do dataset atual sobre uma amostra: qual a taxa de
duplicação exata e semântica, quanto sobra depois de filtros de qualidade, e onde
há dado pessoal a remover. É um trabalho de dias que costuma mudar a conversa —
normalmente o dataset útil é uma fração do que a empresa imagina ter, e saber
disso vale mais que uma recomendação de produto.
