"""Traduz o perfil público em duas chaves que o TCO exige: categoria e provedor.

O motor de TCO (`scoring/tco.py`) trabalha com chaves de `weights.yaml` —
`atendimento_cliente`, `openai_gpt_frontier` e assim por diante. Alguém precisa
mapear o texto que o scraper trouxe para essas chaves, e esse alguém não pode
ser o LLM: o volume estimado de tokens é a premissa mais frágil da cadeia, e um
modelo escolhendo "voz_transcricao" em vez de "busca_interna" muda a conta em
quase uma ordem de grandeza sem deixar rastro de por quê.

Casamento por palavra-chave é grosseiro de propósito. É auditável em uma linha,
sempre devolve a mesma chave para o mesmo texto, e quando não reconhece nada cai
em `desconhecido` — que é uma premissa registrada no briefing, não um chute
disfarçado.
"""

from __future__ import annotations

import unicodedata

from radar.models.company import CompanyProfile, InferenceProvider

#: Categoria de produto → termos que a revelam. A ordem importa: a primeira
#: categoria com acerto vence, e as mais específicas vêm antes das genéricas
#: (uma startup de transcrição médica é `voz_transcricao`, não `copiloto_vertical`).
CATEGORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "voz_transcricao",
        ("transcri", "call center", "voz", "audio", "fala", "speech", "telefon"),
    ),
    (
        "atendimento_cliente",
        ("atendimento", "suporte ao cliente", "chatbot", "sac", "whatsapp", "helpdesk"),
    ),
    (
        "analise_documentos",
        ("documento", "contrato", "juridic", "laudo", "nota fiscal", "peticao", "ocr"),
    ),
    (
        "busca_interna",
        ("busca", "search", "base de conhecimento", "knowledge base", "pesquisa interna"),
    ),
    (
        "geracao_conteudo",
        ("conteudo", "copywriting", "marketing", "criativo", "geracao de texto", "video"),
    ),
    (
        "copiloto_vertical",
        ("copiloto", "copilot", "assistente", "agente", "automatiza", "workflow", "vertical"),
    ),
)

#: Provedor de inferência → chave de preço em `weights.yaml`. Só o que o texto
#: nomeia explicitamente conta; inferir provedor por vibe é como inventar preço.
PROVIDER_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("anthropic_claude_frontier", ("claude", "anthropic", "sonnet", "opus")),
    ("openai_gpt_mini", ("gpt-4o mini", "gpt-4.1 mini", "gpt-4o-mini", "gpt mini", "o4-mini")),
    ("openai_gpt_frontier", ("openai", "gpt-4", "gpt-5", "chatgpt", "azure openai")),
    ("google_gemini_pro", ("gemini", "vertex ai", "google ai")),
)

DEFAULT_CATEGORY = "desconhecido"
DEFAULT_PROVIDER_KEY = "desconhecido"


def _fold(text: str) -> str:
    """Minúsculas sem acento: o texto vem de fonte brasileira e a grafia varia."""
    normalized = unicodedata.normalize("NFD", text.casefold())
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")


def _texto_do_perfil(profile: CompanyProfile) -> str:
    """Concatena os campos que descrevem o que a empresa faz.

    Inclui a descrição do uso de IA e o setor porque a categoria de produto é
    sobre *o que consome token*, não sobre o mercado atendido — e é no uso de IA
    que essa informação aparece.
    """
    partes: list[str] = [profile.name, profile.description or ""]
    for campo in (profile.sector, profile.ai_use_description, profile.target_market):
        if campo is not None:
            partes.append(str(campo.value))
    partes.extend(sinal.technology for sinal in profile.tech_signals)
    partes.extend(profile.open_engineering_roles)
    return _fold(" ".join(p for p in partes if p))


def inferir_categoria_produto(profile: CompanyProfile) -> str:
    """Chave de `tco.volume_base_tokens_mes` que melhor descreve o produto."""
    texto = _texto_do_perfil(profile)
    for categoria, termos in CATEGORY_KEYWORDS:
        if any(termo in texto for termo in termos):
            return categoria
    return DEFAULT_CATEGORY


def inferir_chave_provider(profile: CompanyProfile) -> str | None:
    """Chave de `tco.api_pricing_usd_per_1m_tokens`, ou `None` quando não sabemos.

    `None` (e não `desconhecido`) quando a startup **não** usa API externa:
    quem já é `self_hosted` ou treina modelo próprio não tem custo de API para
    comparar, e fabricar um faria o TCO recomendar migração para onde a empresa
    já está.
    """
    provider = profile.inference_provider.value if profile.inference_provider else None
    if provider in (InferenceProvider.SELF_HOSTED, InferenceProvider.MODELO_PROPRIO):
        return None

    texto = _texto_do_perfil(profile)
    for chave, termos in PROVIDER_KEYWORDS:
        if any(termo in texto for termo in termos):
            return chave

    if provider in (InferenceProvider.API_EXTERNA, InferenceProvider.OPEN_WEIGHTS_HOSPEDADO):
        return DEFAULT_PROVIDER_KEY
    return None
