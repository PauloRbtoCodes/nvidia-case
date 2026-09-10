# Pendências — o que falta, e por quê

> Estado em 2026-09-10. Este arquivo existe para que a lacuna seja **declarada**,
> não descoberta pela banca. A mesma regra que o produto aplica a si mesmo:
> lacuna explícita vale mais que silêncio com aparência de completude.
>
> Ordenado por impacto na conversa da apresentação, não por esforço.

---

## 1. Talk track — o item 3 do escopo, não entregue

**O que é:** o roteiro de conversa que o gerente leva para a ligação — primeira
frase, o que perguntar, o que evitar dizer.

**Estado:** não implementado. O briefing atual entrega diagnóstico, score,
recomendação e evidência, mas não o roteiro falado.

**O que já existe pronto para ele:** `CompanyState.score_delta` (o diff contra a
execução anterior) já está no estado antes do briefing, e foi colocado ali
justamente para isto — o nó `compare` fica entre `score` e `rag` por essa razão,
documentada em `graph/nodes/compare.py`. A primeira frase do talk track sai do
`headline_axis` do delta ("vi que vocês abriram vaga de MLOps pedindo vLLM").

**Por que não foi feito:** priorizei o grounding verificado e o gatilho temporal.
Entre um talk track sem lastro e nenhum talk track, a escolha é coerente com o
resto do projeto.

**Como responder se perguntarem:** "não entreguei; o input dele já está montado
no estado, e a razão de ter ficado por último é que ele depende do delta, que era
o pré-requisito mais caro."

---

## 2. Calibração empírica do score

**O que falta:** `weights.yaml` não foi calibrado contra startups rotuladas à
mão. Os pesos (30/25/25/20) são defensáveis por argumento, não por medição.

**Mitigação já no código:** a confiança é um número separado do score, e todas as
premissas numéricas vivem em YAML versionado — recalibrar é editar dado, não
código. `classifier_eval` existe para receber os ~50 labels manuais.

**Este é o trade-off do slide 2.** Já está declarado na apresentação.

---

## 3. Filtro de descoberta — cobertura incompleta por natureza

**O que acontece:** o porteiro determinístico (`graph/nodes/discovery.py`) recusa
agregador, caminho de conteúdo, manchete, listicle numérico e título de assunto.
Ainda assim escapam casos novos — blogs corporativos de domínio desconhecido são
os mais frequentes (`blog.cubo.itau` foi um, corrigido).

**Por que não tem solução final:** a lista de domínios nunca fica completa —
portais regionais e blogs de nicho aparecem toda semana. As heurísticas de
*forma* (profundidade de caminho, verbo no título, capitalização) sobrevivem
melhor que o inventário, e são o que sustenta o filtro entre atualizações.

**Sintoma quando escapa:** uma "empresa" na fila cujo nome é uma frase
("Inteligência artificial na saúde") e cujo site está vazio. Cosmético: ela sai
com confiança baixa e cai em `MONITORAR`, não contamina o diagnóstico das outras.

**Caminho se virar prioridade:** um classificador barato de uma chamada só sobre
os candidatos que passarem pelo filtro determinístico — pagando cota apenas
naquilo que a heurística não resolveu.

---

## 4. Latência: o passo de recomendação domina

**Medido em 2026-09-10, por empresa:** planejar + descobrir ~1–2s; coletar,
extrair, validar, classificar, pontuar e comparar somam poucos segundos;
**redigir recomendação leva 35–55s**. É a chamada de raciocínio mais pesada —
lê os trechos recuperados e escreve com citação verificável.

**Consequência prática para a demo:** 3 empresas ≈ 1,5 a 2 minutos até a
primeira ficar pronta. Por isso o roteiro manda rodar uma varredura antes e não
esperar a Demo 1 terminar no palco.

**Onde atacaria primeiro, se houvesse tempo:** as empresas já rodam em paralelo
pelo fan-out, mas o teto de concorrência de LLM (`LLM_MAX_CONCURRENCY=2` no
`.env` desta máquina) serializa boa parte disso. Subir o teto é uma linha — o
risco é 429 na cota gratuita, que é justamente o que o teto existe para evitar.
É uma troca de latência por robustez, e a escolha atual privilegia terminar.

---

## 5. Artefatos versionados que deveriam sair do git

`data/` carrega briefings gerados e `embed_cache`, e `web/tsconfig.tsbuildinfo`
está versionado. São saídas de execução e de build, não fonte. Não afetam a
demo; sujam o diff. Decisão pendente do autor.

---

## 6. Registro de execuções vive só na memória

`api/runs.py` guarda as execuções em um dicionário do processo. Reiniciar a API
apaga toda varredura em andamento, e com mais de um worker o `POST` e o
`GET /stream` podem cair em processos diferentes.

**Já está documentado como limitação assumida** no próprio módulo, com o custo da
alternativa (checkpointer do LangGraph no Postgres + pub/sub). Para um dashboard
interno com um operador por vez, é adequado.

**A tela já lida com isso:** quando o stream devolve 404, ela para de reconectar
e diz que a execução não existe mais, em vez de fingir que ainda está rodando.

---

## 7. Exportação em PDF é impressão do navegador

Não há biblioteca HTML→PDF: WeasyPrint e wkhtmltopdf exigem cairo/pango, que esta
máquina não instala sem sudo. O botão "Exportar PDF" usa `window.print()` com uma
folha de estilo de impressão dedicada — as URLs de evidência saem por extenso no
papel, que é o que o relatório precisa preservar.

**Consequência:** o usuário passa pelo diálogo de impressão do navegador. É um
desvio consciente, registrado no topo de `api/routers/briefings.py`.
