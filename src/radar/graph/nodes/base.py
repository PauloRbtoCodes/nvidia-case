"""Utilidades compartilhadas pelos nós: falha registrada, serialização, páginas.

O princípio que organiza este arquivo está no `state.py`: **falha de um nó vira
dado, não exceção**. O valor do sistema está em processar N empresas e explicar
as lacunas; uma exceção propagada derruba o lote inteiro e transforma um
diagnóstico parcial (útil) em nenhum diagnóstico (inútil).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from functools import wraps
from typing import Any

import structlog
from pydantic import BaseModel

from radar.graph.state import NodeFailure
from radar.models.evidence import SourceKind
from radar.scraping.extract import ExtractedPage

log = structlog.get_logger(__name__)

#: Teto de caracteres por documento injetado no prompt. Página institucional
#: relevante cabe com folga; o que passa disso costuma ser rodapé, menu e termos
#: de uso — texto que só dilui o contexto e gasta cota.
MAX_DOC_CHARS = 12_000

#: Teto de documentos por prompt. Mais que isso e o extrator começa a misturar
#: empresas quando o scraper trouxe uma página de diretório com vários perfis.
MAX_DOCS_IN_PROMPT = 12


#: Excecoes que so acontecem por erro de programacao. Nenhuma delas descreve o
#: mundo externo se comportando mal — todas descrevem este codigo escrito errado.
ERROS_DE_PROGRAMACAO: tuple[type[BaseException], ...] = (
    TypeError,
    AttributeError,
    NameError,
    ImportError,
    IndexError,
    UnboundLocalError,
    NotImplementedError,
)


def classificar(exc: BaseException | str) -> str:
    """`bug` para erro de programacao, `mundo` para o resto."""
    if isinstance(exc, str):
        return "mundo"
    return "bug" if isinstance(exc, ERROS_DE_PROGRAMACAO) else "mundo"


def falha(
    node: str,
    exc: BaseException | str,
    *,
    company: str | None = None,
    recoverable: bool = False,
    kind: str | None = None,
) -> NodeFailure:
    """Constrói a falha que o nó devolve no estado."""
    return NodeFailure(
        node=node,
        company=company,
        error=str(exc)[:500],
        recoverable=recoverable,
        kind=kind or classificar(exc),
    )


def node_guard[S: dict[str, Any]](
    name: str,
    *,
    company_key: str | None = "company_name",
) -> Callable[[Callable[[S], Awaitable[dict[str, Any]]]], Callable[[S], Awaitable[dict[str, Any]]]]:
    """Envolve um nó para que qualquer exceção vire `failures` no estado.

    Deliberadamente captura `Exception` inteira: o nó lida com HTML de terceiros,
    JSON de LLM e rede, e a lista de exceções possíveis não é enumerável. O que
    importa é que o nome do nó e a empresa cheguem ao relatório final.

    **Mas nem toda exceção é o mundo falhando.** `TypeError` e `AttributeError`
    são este código escrito errado, e engolir os dois com a mesma cara faz um bug
    sair no relatório como "falha recuperável" — o lote termina em "sucesso
    parcial" e ninguém procura a causa aqui dentro. Por isso a falha carrega
    `kind`, e só `bug` sobe como `log.error` com traceback.
    """

    def decorator(
        fn: Callable[[S], Awaitable[dict[str, Any]]],
    ) -> Callable[[S], Awaitable[dict[str, Any]]]:
        @wraps(fn)
        async def wrapper(state: S) -> dict[str, Any]:
            empresa = state.get(company_key) if company_key else None
            try:
                return await fn(state)
            except Exception as exc:  # noqa: BLE001 - ver docstring
                tipo = classificar(exc)
                # `bug` sobe como error e com o traceback: erro de programacao
                # escondido atras de "falha recuperavel" e como o lote termina em
                # "sucesso parcial" sem ninguem procurar a causa no proprio codigo.
                registrar = log.error if tipo == "bug" else log.warning
                registrar(
                    "no_falhou",
                    node=name,
                    empresa=empresa,
                    kind=tipo,
                    excecao=type(exc).__name__,
                    erro=str(exc)[:300],
                    exc_info=tipo == "bug",
                )
                return {"failures": [falha(name, exc, company=empresa, kind=tipo)]}

        return wrapper

    return decorator


def dump(model: BaseModel | None) -> str:
    """Modelo Pydantic → JSON legível para injetar no prompt.

    `mode="json"` porque `HttpUrl`, `datetime` e `StrEnum` não são serializáveis
    pelo `json` puro — e `ensure_ascii=False` porque o texto é português e um
    prompt cheio de `\\u00e7` desperdiça tokens e confunde o casamento literal de
    evidência.
    """
    if model is None:
        return "null"
    return json.dumps(model.model_dump(mode="json"), ensure_ascii=False, indent=2)


def dump_many(models: Sequence[BaseModel]) -> str:
    return json.dumps(
        [m.model_dump(mode="json") for m in models], ensure_ascii=False, indent=2
    )


# --------------------------------------------------------------------------- #
# Páginas coletadas: `ExtractedPage` ↔ dict no estado
# --------------------------------------------------------------------------- #
def page_to_dict(page: ExtractedPage) -> dict[str, Any]:
    """O estado do grafo é serializado pelo checkpointer, então guarda dict.

    O `html` fica de fora de propósito: é volumoso, já foi consumido pelas
    extrações estruturadas no nó de coleta, e persistir HTML bruto em cada
    checkpoint inflaria o banco sem servir a nada adiante.
    """
    return {
        "url": page.url,
        "kind": page.kind.value,
        "text": page.text,
        "title": page.title,
        "published_at": page.published_at.isoformat() if page.published_at else None,
    }


def dict_to_page(data: dict[str, Any]) -> ExtractedPage:
    publicado = data.get("published_at")
    return ExtractedPage(
        url=data["url"],
        kind=SourceKind(data.get("kind", SourceKind.OUTRO.value)),
        text=data.get("text", ""),
        title=data.get("title"),
        published_at=datetime.fromisoformat(publicado) if publicado else None,
    )


def format_documents(
    pages: Sequence[dict[str, Any]],
    *,
    max_docs: int = MAX_DOCS_IN_PROMPT,
    max_chars: int = MAX_DOC_CHARS,
) -> str:
    """Documentos numerados para o prompt do Extractor e do Validator.

    A URL vai no cabeçalho de cada bloco porque a `Evidence` exige a URL de
    origem: sem ela no contexto, o modelo teria que adivinhar de qual página
    veio o trecho que está citando.
    """
    blocos: list[str] = []
    for i, page in enumerate(pages[:max_docs], start=1):
        texto = (page.get("text") or "")[:max_chars]
        if not texto.strip():
            continue
        cabecalho = " · ".join(
            parte
            for parte in (page.get("title"), page.get("kind"), page.get("url"))
            if parte
        )
        blocos.append(f"[DOC {i}] {cabecalho}\n{texto}")
    return "\n\n---\n\n".join(blocos) if blocos else "(nenhum documento coletado)"


def all_texts(pages: Sequence[dict[str, Any]]) -> dict[str, str]:
    """URL → texto coletado. Usado para conferir se um excerpt é mesmo literal."""
    return {page["url"]: page.get("text", "") for page in pages if page.get("url")}
