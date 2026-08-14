"""Motor de recomendação: terceiro estágio do híbrido regra → RAG → LLM.

Os dois primeiros estágios já rodaram: o gate determinístico restringiu o
universo de tecnologias a partir do eixo mais fraco, e o RAG trouxe os trechos
da KB. Aqui o modelo só redige — e redige *dentro* do que foi recuperado.

O nó aplica a trava que o prompt não consegue garantir sozinho: **reconciliar
cada citação contra os chunks realmente recuperados**. `Recommendation` já
rejeita citação vazia, mas nada nele impede o modelo de escrever um chunk
plausível que nunca existiu — e um trecho inventado sobre o que o Triton faz,
lido por um founder técnico, custa a credibilidade do programa inteiro.

Recomendação cuja citação não sobrevive à reconciliação é **descartada**, não
degradada. Três recomendações fundamentadas valem mais numa reunião que oito
plausíveis.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine, Sequence
from typing import Any

import structlog

from radar.graph.nodes.base import dump, node_guard
from radar.graph.nodes.deps import NodeDeps
from radar.graph.state import CompanyState
from radar.llm.model_registry import LLMTask
from radar.llm.schemas import RecommendationSet
from radar.models.recommendation import Recommendation, RetrievedChunk
from radar.rag.cite import format_context

log = structlog.get_logger(__name__)

#: Quantos caracteres do trecho precisam coincidir para aceitarmos a citação
#: como sendo aquele chunk. Curto o bastante para tolerar o corte de espaços que
#: todo modelo faz, longo o bastante para que dois chunks distintos da mesma
#: página não se confundam.
PREFIXO_DE_CASAMENTO = 80


def _normalize(texto: str) -> str:
    return " ".join(texto.split()).casefold()


def reconciliar_citacao(
    citada: RetrievedChunk, recuperados: Sequence[RetrievedChunk]
) -> RetrievedChunk | None:
    """Troca a citação escrita pelo modelo pelo chunk real correspondente.

    Casamos primeiro por URL — é o campo que o modelo copia com mais fidelidade —
    e depois por sobreposição de texto. Devolvemos o objeto **recuperado**, não o
    do modelo: assim os scores de busca e rerank chegam ao briefing e a citação
    exibida é literalmente a que foi indexada.
    """
    alvo = _normalize(citada.text)
    if not alvo:
        return None

    mesma_url = [c for c in recuperados if str(c.source_url) == str(citada.source_url)]
    for candidato in mesma_url or recuperados:
        texto = _normalize(candidato.text)
        prefixo = alvo[:PREFIXO_DE_CASAMENTO]
        if prefixo and (prefixo in texto or texto[:PREFIXO_DE_CASAMENTO] in alvo):
            return candidato

    # URL certa mas texto irreconhecível: o modelo misturou trechos. Só aceitamos
    # quando a página recuperada é única, caso em que a fonte segue rastreável.
    if len(mesma_url) == 1:
        return mesma_url[0]
    return None


def sanear_recomendacoes(
    recomendacoes: Sequence[Recommendation], recuperados: Sequence[RetrievedChunk]
) -> tuple[list[Recommendation], list[str]]:
    """Aplica a reconciliação e descarta o que não sobreviver.

    Devolve também os nomes das tecnologias descartadas, que viram nota de
    validação e depois `caveat`: silenciar o descarte esconderia do gerente que
    o sistema quase recomendou algo sem fundamento.
    """
    aceitas: list[Recommendation] = []
    descartadas: list[str] = []

    for rec in recomendacoes:
        reais: list[RetrievedChunk] = []
        vistos: set[str] = set()
        for citacao in rec.kb_citations:
            real = reconciliar_citacao(citacao, recuperados)
            if real is None:
                continue
            chave = f"{real.source_url}|{real.text[:120]}"
            if chave in vistos:
                continue
            vistos.add(chave)
            reais.append(real)

        if not reais:
            descartadas.append(rec.technology)
            continue
        aceitas.append(rec.model_copy(update={"kb_citations": reais}))

    return aceitas, descartadas


def make_recommend_technologies(
    deps: NodeDeps,
) -> Callable[[CompanyState], Coroutine[Any, Any, dict[str, Any]]]:
    @node_guard("recommender")
    async def recommend_technologies(state: CompanyState) -> dict[str, Any]:
        score = state.get("defensibility")
        profile = state.get("profile")
        chunks = state.get("rag_chunks") or []

        if score is None or profile is None:
            raise ValueError("Recomendação sem score ou sem perfil.")
        if not chunks:
            # Guardrail do enunciado: sem evidência recuperada, bloquear em vez de
            # degradar. O briefing sai mesmo assim — com o diagnóstico e a lacuna
            # declarada, que é mais útil que uma recomendação sem lastro.
            log.warning("recomendacao_bloqueada_sem_kb", empresa=profile.name)
            return {
                "recommendations": [],
                "validation_notes": [
                    *(state.get("validation_notes") or []),
                    "nenhum trecho da KB NVIDIA recuperado: recomendações bloqueadas",
                ],
            }

        conjunto = await deps.run_llm(
            LLMTask.RECOMMENDER,
            RecommendationSet,
            company_profile_json=dump(profile),
            defensibility_json=dump(score),
            weakest_axis=score.weakest_axis.value,
            candidate_technologies=state.get("candidate_technologies") or [],
            kb_chunks=format_context(chunks),
        )

        aceitas, descartadas = sanear_recomendacoes(conjunto.recommendations, chunks)

        notas = [*(state.get("validation_notes") or [])]
        notas.extend(
            f"recomendação de '{tech}' descartada: citação não corresponde a nenhum "
            "trecho recuperado da KB"
            for tech in descartadas
        )

        log.info(
            "recomendacoes",
            empresa=profile.name,
            propostas=len(conjunto.recommendations),
            aceitas=len(aceitas),
            descartadas=descartadas,
        )
        return {"recommendations": aceitas, "validation_notes": notas}

    return recommend_technologies
