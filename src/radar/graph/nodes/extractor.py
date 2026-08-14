"""Extractor Agent: texto coletado → `CompanyProfile` com evidência literal.

O prompt manda o modelo copiar o trecho caractere a caractere. Prompt não é
garantia: este nó confere, campo a campo, se cada `excerpt` **realmente** ocorre
no texto que foi entregue ao modelo, e descarta o que não ocorre. Um campo que
perde todas as evidências volta a ser `None` — desconhecido, nunca falso.

A checagem é mecânica de propósito. O Evidence Validator (LLM) audita o
*significado* — se o trecho sustenta o que o campo afirma; aqui verificamos só a
*literalidade*, que é comparação de string e não precisa de modelo nenhum. Gastar
uma chamada de LLM para descobrir que uma citação foi inventada seria pagar caro
por algo que `in` resolve.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine, Sequence
from typing import Any

import structlog
from pydantic import BaseModel

from radar.graph.nodes.base import all_texts, format_documents, node_guard
from radar.graph.nodes.deps import NodeDeps
from radar.graph.state import CompanyState
from radar.llm.model_registry import LLMTask
from radar.models.company import CompanyProfile, TechSignal
from radar.models.evidence import Evidence
from radar.scraping.extract import evidences_for_keywords, extract_tech_mentions

log = structlog.get_logger(__name__)

#: Campos do perfil que carregam `EvidenceBackedField` e por isso passam pela
#: checagem de literalidade. Listado explicitamente para que um campo novo em
#: `CompanyProfile` apareça aqui numa revisão, e não passe despercebido.
EVIDENCE_BACKED_FIELDS: tuple[str, ...] = (
    "sector",
    "target_market",
    "ai_use_description",
    "inference_provider",
    "proprietary_data_claim",
    "named_integrations",
    "enterprise_customers",
)


def _normalize(value: str) -> str:
    return " ".join(value.split()).casefold()


def _e_literal(excerpt: str, corpus: Sequence[str]) -> bool:
    alvo = _normalize(excerpt)
    return any(alvo in texto for texto in corpus)


def _filtrar_evidencias(evidencias: list[Evidence], corpus: Sequence[str]) -> list[Evidence]:
    return [ev for ev in evidencias if _e_literal(ev.excerpt, corpus)]


def podar_nao_literais(
    profile: CompanyProfile, textos: dict[str, str]
) -> tuple[CompanyProfile, list[str]]:
    """Remove toda evidência cujo trecho não aparece nas páginas coletadas.

    Devolve o perfil saneado e a lista de campos anulados — que segue para os
    `caveats` do briefing: o gerente precisa saber que "setor" ficou vazio porque
    a extração não se sustentou, e não porque a empresa não tem setor.
    """
    corpus = [_normalize(t) for t in textos.values() if t]
    dados = profile.model_dump()
    anulados: list[str] = []

    for nome in EVIDENCE_BACKED_FIELDS:
        campo = getattr(profile, nome, None)
        if campo is None:
            continue
        validas = _filtrar_evidencias(list(campo.evidences), corpus)
        if validas:
            dados[nome] = {**dados[nome], "evidences": [ev.model_dump() for ev in validas]}
        else:
            dados[nome] = None
            anulados.append(nome)

    # Sinais técnicos, founders e rodadas: a evidência inválida cai, o item fica.
    # Perder a menção a "Triton" porque o modelo parafraseou o parágrafo em volta
    # descartaria um sinal que o próprio scraper consegue reconstruir adiante.
    for chave in ("tech_signals", "founders", "funding_rounds"):
        for item in dados.get(chave) or []:
            item["evidences"] = [
                ev
                for ev in item.get("evidences") or []
                if _e_literal(str(ev.get("excerpt", "")), corpus)
            ]

    dados["all_evidences"] = [
        ev
        for ev in dados.get("all_evidences") or []
        if _e_literal(str(ev.get("excerpt", "")), corpus)
    ]

    if anulados:
        log.info("campos_anulados_por_citacao_nao_literal", empresa=profile.name, campos=anulados)
    return CompanyProfile.model_validate(dados), anulados


def _sinais_tecnicos_deterministicos(
    paginas: Sequence[dict[str, Any]], existentes: Sequence[TechSignal]
) -> list[TechSignal]:
    """Menções técnicas encontradas por vocabulário fixo, com evidência real.

    Roda *além* do LLM, não no lugar dele: o vocabulário de `TECH_VOCABULARY` é
    curto e específico (Triton, vLLM, TensorRT), e cada acerto vem acompanhado do
    parágrafo literal que o contém — evidência que nasce grounded por construção,
    sem depender de o modelo ter copiado direito.
    """
    from radar.graph.nodes.base import dict_to_page

    ja_vistas = {s.technology.casefold() for s in existentes}
    novos: dict[str, TechSignal] = {}

    for bruta in paginas:
        pagina = dict_to_page(bruta)
        for tecnologia in extract_tech_mentions(pagina.text):
            chave = tecnologia.casefold()
            if chave in ja_vistas or chave in novos:
                continue
            evidencias = evidences_for_keywords(pagina, [tecnologia], max_evidences=2)
            if not evidencias:
                continue
            novos[chave] = TechSignal(technology=tecnologia, evidences=evidencias)

    return list(novos.values())


class _ProfileEnvelope(BaseModel):
    """Casca para o modelo devolver um objeto único.

    `CompanyProfile` é grande; pedir o objeto nu funciona, mas alguns modelos
    embrulham a resposta em uma chave assim mesmo. Tornar o envelope explícito
    elimina uma classe inteira de retry de validação.
    """

    profile: CompanyProfile


def make_extract_profile(
    deps: NodeDeps,
) -> Callable[[CompanyState], Coroutine[Any, Any, dict[str, Any]]]:
    @node_guard("extractor")
    async def extract_profile(state: CompanyState) -> dict[str, Any]:
        paginas = state.get("raw_pages") or []
        if not paginas:
            raise ValueError("Extração sem páginas coletadas.")

        dica = state.get("company_name") or ""
        seeds = state.get("seed_urls") or []
        if seeds:
            dica = f"{dica} ({seeds[0]})".strip()

        envelope = await deps.run_llm(
            LLMTask.EXTRACTOR,
            _ProfileEnvelope,
            company_hint=dica,
            source_documents=format_documents(paginas),
        )

        textos = all_texts(paginas)
        profile, anulados = podar_nao_literais(envelope.profile, textos)

        # Sinais determinísticos entram depois da poda: eles já nascem literais e
        # não devem ser filtrados por ela.
        extras = _sinais_tecnicos_deterministicos(paginas, profile.tech_signals)
        vagas = list(
            dict.fromkeys([*profile.open_engineering_roles, *(state.get("job_titles") or [])])
        )
        # `model_validate` e não `model_copy`: `source_urls` é `list[HttpUrl]` e
        # `model_copy` não valida — as URLs entrariam como `str` e quebrariam a
        # serialização do estado adiante, longe daqui.
        profile = CompanyProfile.model_validate(
            {
                **profile.model_dump(),
                "tech_signals": [*profile.tech_signals, *extras],
                "open_engineering_roles": vagas,
                "source_urls": list(textos.keys()),
            }
        )

        notas = [f"campo sem citação literal, anulado: {campo}" for campo in anulados]
        log.info(
            "perfil_extraido",
            empresa=profile.name,
            cobertura=profile.evidence_coverage,
            sinais_llm=len(profile.tech_signals) - len(extras),
            sinais_deterministicos=len(extras),
            anulados=len(anulados),
        )
        return {
            "profile": profile,
            "evidences": profile.all_evidences,
            "validation_notes": notas,
        }

    return extract_profile
