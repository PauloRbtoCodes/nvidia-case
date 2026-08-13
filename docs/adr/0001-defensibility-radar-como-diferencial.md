# ADR 0001 — Defensibility Radar como diferencial do projeto

- **Status:** aceito
- **Data:** 2026-08-13

## Contexto

O Entregável 6 pede algo único, para diferenciação competitiva. O enunciado já
exige um Startup Classifier Agent que rotula a empresa como AI-native, AI-enabled
ou non-AI.

Mas a pergunta norteadora do case não é "quais startups brasileiras usam IA". É:
*como a NVIDIA identifica, atrai e nutre startups AI-native num contexto em que os
grandes labs ameaçam quem depende apenas de wrapper de LLM?*

A classificação em três rótulos descreve o presente. Ela não diz quem está sob
ameaça, quão urgente é a conversa, nem o que a NVIDIA tem a oferecer para mudar
o quadro.

## Alternativas consideradas

1. **Radar temporal** — re-scraping agendado com alertas de mudança de sinal.
   Justifica o nome do produto, mas é infraestrutura, não inteligência: responde
   "o que mudou", não "por que importa". Caro para o prazo de um mês.
2. **Grafo de ecossistema** — founders, investidores e aceleradoras conectados,
   para achar caminhos de *warm intro*. Útil comercialmente, porém periférico à
   pergunta do case e dependente de dados de relacionamento difíceis de obter
   publicamente com qualidade.
3. **Simulador de TCO isolado** — calculadora de custo API versus GPU. Argumento
   comercial forte, mas estreito: só fala com startups cujo gargalo já é custo de
   inferência.

## Decisão

Construir o **Defensibility Radar**: score 0–100 de defensibilidade (cujo
complemento é o risco de comoditização), medido em quatro eixos ponderados —
dados proprietários (30%), profundidade de workflow (25%), domínio da stack (25%)
e distribuição (20%).

O simulador de TCO entra **dentro** do eixo de domínio da stack, como o
instrumento que o quantifica, e não como módulo paralelo.

## Consequências

**Positivas**

- Responde diretamente à pergunta norteadora, em vez de tangenciá-la.
- Ordena o trabalho do gerente: `urgência = risco × capacidade de agir`. Startup
  vulnerável e capitalizada é conversa urgente; vulnerável e sem capital é
  nutrição via comunidade; já defensável é candidata a case de sucesso.
- Torna o motor de recomendação **causal**. A tecnologia sugerida deriva do eixo
  mais fraco, não de uma tabela de regras por setor. Ver ADR 0003.
- Inverte o enquadramento comercial. O output não é "sua startup é um wrapper",
  é "aqui está a rota para deixar de ser, e a NVIDIA cobre a parte técnica dela".

**Negativas e riscos**

- Um score numérico sobre empresas reais carrega responsabilidade. Mitigação em
  ADR 0002 (score e confiança separados) e ADR 0004 (evidência obrigatória).
- Os pesos iniciais são hipótese, não verdade calibrada. Mitigação: `weights.yaml`
  versionado e calibração na semana 4 contra ~50 startups rotuladas à mão.
- Aumenta o escopo em relação a apenas classificar. Aceito: é o diferencial.
