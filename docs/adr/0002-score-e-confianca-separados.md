# ADR 0002 — Score e confiança são números independentes

- **Status:** aceito
- **Data:** 2026-08-13
- **Relacionado:** ADR 0001, ADR 0004

## Contexto

O Defensibility Radar pontua empresas reais a partir de informação pública. Nem
toda startup comunica igual: algumas mantêm blog de engenharia detalhado, página
de carreiras rica e cases publicados; outras têm uma landing page de três seções
e nada mais.

A startup discreta não é menos defensável. Ela é menos **observável**.

Se um único número carregar ao mesmo tempo "quão defensável a empresa é" e
"quanto conseguimos apurar", os dois casos colapsam: a empresa sobre a qual nada
encontramos recebe nota baixa, indistinguível da empresa que investigamos a fundo
e cujos sinais são de fato fracos.

O resultado seria injustiça com aparência de rigor — e, pior, um sistema que
sistematicamente penaliza quem não faz marketing técnico. Numa ferramenta que vai
orientar quem o time da NVIDIA procura, esse viés tem custo real.

## Decisão

Todo `AxisScore` carrega **dois** números independentes:

- `score` (0–100): quão defensável, dado o que foi observado.
- `confidence` (0–1): quanta evidência sustenta essa leitura.

Regras derivadas, implementadas em código e cobertas por teste:

1. `is_actionable` exige `confidence >= 0.35`. Abaixo disso o eixo não sustenta
   conversa comercial.
2. `gap_severity` — que decide qual eixo puxa a recomendação — multiplica pela
   confiança. Gap não observado não dispara recomendação.
3. `actionable_gaps` filtra por `is_actionable` antes de considerar o score.
4. A UI é **obrigada** a renderizar eixo de baixa confiança como "evidência
   insuficiente", nunca como nota baixa.
5. O prompt do Scorer instrui explicitamente: pouca evidência significa
   confiança baixa, **não** score baixo.

O mesmo princípio se repete na classificação de maturidade, onde
`AIMaturity.INDETERMINADO` existe justamente para que `NON_AI` nunca seja o
destino da dúvida.

## Consequências

**Positivas**

- O sistema distingue "não encontramos" de "é ruim" em toda a cadeia.
- Empresas mal cobertas caem no bucket `MONITORAR` (re-coletar) em vez de serem
  descartadas ou abordadas com diagnóstico errado.
- Dá ao gerente informação honesta sobre onde o sistema não sabe — o que protege
  a credibilidade dele na reunião.

**Negativas**

- Dobra a superfície de UI: cada eixo precisa comunicar dois números sem virar
  poluição visual.
- Exige disciplina em todo agente novo que produza score. É a razão de o
  invariante estar travado em `tests/test_scoring.py`, e não apenas documentado.
