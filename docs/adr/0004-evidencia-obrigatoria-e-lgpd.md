# ADR 0004 — Evidência obrigatória, coleta ética e LGPD

- **Status:** aceito
- **Data:** 2026-08-13
- **Relacionado:** ADR 0002, ADR 0003

## Contexto

O sistema produz afirmações sobre empresas reais, que um gerente da NVIDIA levará
para uma conversa com founders. Duas falhas seriam graves e ambas são o modo de
falha natural de uma pipeline com LLM:

1. **Afirmação sem lastro.** O modelo infere que a startup "usa apenas API externa"
   a partir de nada, o gerente repete isso na reunião, e o founder — que mantém
   inferência própria há um ano — perde a confiança na conversa e no programa.
2. **Alucinação sobre a própria NVIDIA.** O sistema descreve uma capacidade do
   Triton que não existe. Diante de um founder técnico, isso custa a credibilidade
   do Inception inteiro, não só daquela reunião.

Há ainda a dimensão legal: coletamos informação sobre pessoas (founders), o que
atrai a LGPD.

## Decisão

### Evidência como primitiva estrutural

`Evidence` (url, `excerpt` literal, tipo de fonte, datas, hash) é a unidade
atômica do sistema. Regras impostas em código, não por convenção:

- `excerpt` tem mínimo de 20 caracteres e **precisa ser literal**. Parafrasear
  destrói a função de prova. Está escrito no prompt do Extractor como regra dura.
- Todo campo inferido usa `EvidenceBackedField[T]`, que expõe `confidence`
  derivada das fontes e `is_grounded`. Campo sem evidência é **desconhecido**,
  jamais falso.
- `Recommendation` levanta `ValidationError` quando não há citação da base NVIDIA.
  **Bloquear, não degradar** — três recomendações fundamentadas valem mais numa
  reunião do que oito plausíveis.
- Confiança de fonte é tipada (blog de engenharia > portal de notícia, que
  costuma repetir o release) e decai com a idade da publicação: sinal técnico
  envelhece rápido.
- Evidência sem data não é penalizada — assumir obsolescência sem base seria
  inventar informação na direção oposta.

### Coleta

- `robots.txt` respeitado, rate limit por domínio, `User-Agent` identificável
  com contato.
- Cache em disco: não re-raspar o mesmo site durante desenvolvimento.
- Somente conteúdo público. Nenhuma tentativa de contornar autenticação, paywall
  ou proteção anti-bot.

### LGPD

- Sobre founders, coletamos apenas informação **profissional pública e
  diretamente relevante** ao diagnóstico técnico: cargo, formação técnica,
  empresas anteriores. É o mínimo necessário para avaliar capacidade técnica do
  time, que entra na `capacity_to_act`.
- Nada de dado sensível, contato privado ou informação não publicada pela própria
  pessoa em contexto profissional.
- Base legal: legítimo interesse para prospecção B2B, com minimização de dados.
- O modelo `Founder` documenta essa restrição no próprio docstring, para que a
  regra viaje junto com o código.

## Consequências

**Positivas**

- Todo briefing é auditável até a URL e o trecho. O gerente pode verificar antes
  da reunião.
- O sistema falha para menos: sem evidência, não afirma. É o comportamento
  correto para a finalidade.
- Conformidade tratada como requisito de projeto, não como aviso legal no rodapé.

**Negativas**

- Cobertura menor. Startups discretas geram perfis esparsos — endereçado pelo
  ADR 0002 e pelo bucket `MONITORAR`.
- O guardrail de citação pode bloquear recomendação correta quando o RAG falha em
  recuperar. Trade-off aceito conscientemente.
