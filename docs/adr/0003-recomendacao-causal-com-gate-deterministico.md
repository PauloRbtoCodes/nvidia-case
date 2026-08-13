# ADR 0003 — Recomendação causal com gate determinístico antes do RAG

- **Status:** aceito
- **Data:** 2026-08-13
- **Relacionado:** ADR 0001, ADR 0004

## Contexto

O Entregável 4 pede um motor que recomende tecnologias NVIDIA a partir do perfil
da startup. O enunciado sugere regras do tipo "se a startup faz voz, recomendar
Riva e NIM".

Duas implementações ingênuas são tentadoras e ambas falham:

**Só regras por setor.** Produz recomendação previsível e rasa. Toda startup de
saúde recebe Clara; toda de voz recebe Riva. Não diferencia a que tem time de ML
e inferência própria daquela que chama uma API e repassa — que são conversas
completamente diferentes.

**Só LLM sobre o perfil.** Produz texto convincente e recomendação instável. O
modelo escolhe pela plausibilidade da narrativa, cita tecnologia que não recuperou,
e não há como auditar por que aquela tecnologia e não outra.

## Decisão

Três estágios, cada um com uma função distinta:

**1. Gate determinístico.** `DefensibilityScore.candidate_technologies()` mapeia
os eixos com gap acionável para famílias de tecnologia NVIDIA
(`AXIS_TO_NVIDIA_FAMILY`). É código puro, sem LLM, e produz o universo de
candidatas. Eixo saudável não injeta tecnologia no briefing.

**2. RAG.** Recupera da base NVIDIA os trechos que descrevem cada candidata — o
que resolve, pré-requisitos, complexidade. Busca híbrida com reranking.

**3. LLM.** Redige as justificativas técnica e de negócio, **ancorado** nos
trechos recuperados. Não escolhe a tecnologia; explica a escolha.

O campo `Recommendation.addresses_axis` é obrigatório e amarra cada recomendação
ao gap que a motivou.

## Consequências

**Positivas**

- A recomendação é rastreável até o diagnóstico: dá para responder "por que
  TensorRT-LLM para esta empresa" apontando para o eixo, a severidade e as
  evidências.
- O LLM opera no que faz bem (redigir com contexto) e não no que faz mal
  (escolher sob incerteza sem critério explícito).
- O gate é testável sem chamar modelo — `test_candidatas_saem_dos_gaps_e_nao_de_regra_por_setor`.
- Recalibrar o mapa eixo→tecnologia é edição de dado, não de prompt.

**Negativas**

- `AXIS_TO_NVIDIA_FAMILY` é curadoria manual e pode ficar desatualizado quando a
  NVIDIA lança produto novo. Aceito: é uma constante pequena e revisável.
- O gate pode ser conservador demais e omitir uma tecnologia pertinente por
  caminho indireto. Preferimos omitir a alucinar.
