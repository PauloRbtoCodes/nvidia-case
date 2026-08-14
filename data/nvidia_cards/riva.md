---
title: "Riva — quando recomendar"
technology: "Riva"
category: voz
url: https://developer.nvidia.com/riva
---

# Riva — quando recomendar a uma startup

## O que resolve

O Riva é o SDK de IA de fala da NVIDIA: reconhecimento automático de fala (ASR),
síntese de voz (TTS) e tradução, acelerados em GPU e implantáveis no ambiente do
cliente — nuvem, on-premises ou borda. Suporta português brasileiro, o que no
mercado nacional deixa de ser detalhe e vira requisito.

Dois problemas concretos. O primeiro é **latência em conversa**: num atendimento
por voz, o usuário percebe o atraso entre parar de falar e ouvir a resposta, e
esse orçamento de tempo precisa caber transcrição, raciocínio e síntese. Um ASR
hospedado longe consome boa parte do orçamento antes de o modelo pensar.

O segundo é **residência de dados**. Gravação de ligação contém dado pessoal, às
vezes sensível — ligação de cobrança, triagem médica, atendimento bancário.
Poder rodar o reconhecimento dentro do ambiente do cliente muda o que a startup
consegue vender e para quem.

## Sinais de que esta startup precisa

- Produto de voz explícito: call center, telefonia, cobrança, agendamento,
  drive-thru, assistente por voz.
- Transcrição de reunião, de consulta médica, de audiência ou de aula.
- Menção a Whisper, Deepgram, AssemblyAI, Google Speech ou "speech-to-text" no
  site, nas vagas ou no blog.
- Cliente em setor regulado com dado de voz — a gravação é dado pessoal, e a LGPD
  se aplica com todo o peso.
- Reclamação pública sobre latência de resposta em voz, ou menção a "conversa
  natural" e "tempo real" como diferencial de produto.
- Necessidade de vocabulário específico do domínio: nomes de medicamentos,
  termos jurídicos, códigos de produto, sotaques regionais.

## Quando NÃO recomendar

- **Quando não há voz no produto.** Óbvio, e ainda assim é o erro que uma regra
  por setor cometeria — nem toda startup de saúde transcreve consulta.
- **Quando o volume de áudio é baixo e não há exigência de residência de dados.**
  Uma API de transcrição resolve com menos operação, e insistir em self-hosting
  aqui é criar trabalho para o time.
- **Quando a qualidade em português ainda não foi testada no domínio da startup.**
  Nenhum ASR é uniformemente melhor; a recomendação honesta passa por medir com o
  áudio real da empresa antes de qualquer migração.

## Pré-requisitos

Áudio real do domínio para avaliar, GPU para a implantação, e uma métrica de
qualidade acordada — taxa de erro de palavra sobre um conjunto próprio, não sobre
benchmark público.

## Complexidade

**Média.** Semanas: implantar, avaliar qualidade contra a solução atual, adaptar
vocabulário do domínio e integrar ao fluxo de telefonia, que costuma ser a parte
mais chata e mais subestimada.

## Eixo de defensibilidade

**Profundidade de workflow** (`workflow_depth`), quando destrava atendimento
dentro do ambiente regulado do cliente. Toca também **domínio da stack**
(`stack_ownership`) pelo controle de latência e custo por minuto de áudio, que em
produto de voz é a métrica que define a margem.

## Primeira ação sugerida

Propor uma avaliação cega com 30 a 60 minutos de áudio real da startup —
sotaques, ruído e vocabulário do domínio incluídos — comparando a solução atual
com o Riva na mesma métrica de erro. Se o português do domínio não melhorar, a
recomendação cai, e dizer isso é o que torna a próxima recomendação confiável.
