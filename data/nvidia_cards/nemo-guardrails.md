---
title: "NeMo Guardrails — quando recomendar"
technology: "NeMo Guardrails"
category: governanca
url: https://github.com/NVIDIA/NeMo-Guardrails
---

# NeMo Guardrails — quando recomendar a uma startup

## O que resolve

O NeMo Guardrails é um kit aberto para programar **trilhos** em aplicações de
LLM: o que o sistema pode ou não discutir, o que fazer quando a pergunta sai do
escopo, quais verificações rodam antes de a resposta chegar ao usuário, e quais
ferramentas o modelo pode acionar. As regras ficam em arquivos versionados, não
espalhadas em condicionais dentro do código.

O problema que resolve é o que impede uma startup de ir do piloto ao contrato.
Um assistente que ocasionalmente responde fora do escopo, ou que pode ser
induzido a ignorar as próprias instruções, é uma demonstração interessante e um
risco jurídico inaceitável para um banco ou um hospital. O comprador corporativo
não pergunta "o modelo é bom?"; pergunta "o que impede ele de dizer o que não
deve, e como vocês provam isso?".

## Sinais de que esta startup precisa

- Assistente conversacional exposto ao usuário final, e não apenas interno.
- Setor regulado: saúde, financeiro, jurídico, seguros, educação, público.
- Menção pública a conformidade, LGPD, auditoria ou política de uso de IA.
- Human-in-the-loop descrito no produto — indica consciência de risco e um ponto
  natural onde os trilhos se encaixam.
- Agentes que executam ações, não só respondem: emitir, aprovar, cancelar,
  escrever no sistema do cliente. Quanto mais a ação é irreversível, mais o
  trilho vale.
- Vaga de segurança da informação, compliance ou *trust & safety*.
- Cliente enterprise nomeado no site: alguém já passou por questionário de risco.

## Quando NÃO recomendar

- **Quando o produto não expõe modelo ao usuário final.** Pipeline interno de
  extração de dados tem outros controles; trilhos de conversa não se aplicam.
- **Quando o problema é acurácia e não escopo.** Guardrails impede o assistente
  de falar do que não deve; não faz ele acertar mais no que deve.
- **Como substituto de avaliação.** Trilho sem conjunto de teste é sensação de
  segurança — e sensação de segurança é pior que insegurança conhecida.

## Pré-requisitos

Uma aplicação de LLM em produção ou perto disso, e uma definição — mesmo
informal — do que está dentro e fora do escopo do produto. Escrever essa
definição costuma ser a parte difícil, e é trabalho do time, não da ferramenta.

## Complexidade

**Média.** Semanas: escrever os trilhos, testá-los contra tentativas reais de
desvio, e integrar ao fluxo sem degradar a latência percebida.

## Eixo de defensibilidade

**Profundidade de workflow** (`workflow_depth`). Um assistente com escopo
governado e auditável deixa de ser um chat genérico e passa a ser um componente
que o cliente corporativo aceita dentro do processo dele — que é exatamente a
diferença entre raso e fundo neste eixo.

## Primeira ação sugerida

Pedir ao time os três piores casos já vistos em produção — a resposta fora de
escopo, o pedido que não deveria ter sido atendido, o vazamento de contexto — e
escrever o trilho correspondente a um deles junto com eles. Um caso real
resolvido convence mais que a documentação inteira.
