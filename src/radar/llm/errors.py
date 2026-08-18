"""Erros da camada de LLM.

Tipos separados porque cada um exige uma reação diferente: falha de schema é
recuperável reenviando com o erro anexado, cota estourada exige backoff longo,
prompt inexistente é bug de programação e deve estourar na hora.
"""

from __future__ import annotations


class LLMError(Exception):
    """Base — permite que o grafo capture a camada inteira num except só."""


class PromptNotFoundError(LLMError):
    """Prompt pedido por nome+versão não existe em disco.

    Falha alto e cedo: `Classification.prompt_version` grava qual prompt gerou a
    saída, então um fallback silencioso para outra versão corromperia a avaliação.
    """


class PromptRenderError(LLMError):
    """Variável declarada no prompt não foi fornecida (ou sobrou placeholder).

    Um `{{gap_axis}}` que vaza literalmente para o modelo produz saída plausível
    e errada — pior que exceção, porque passa despercebido.
    """


class SchemaValidationError(LLMError):
    """O modelo devolveu algo que não valida contra o schema Pydantic pedido.

    Carrega o texto cru e a mensagem de validação porque os dois voltam para o
    modelo na tentativa seguinte: reenviar com o erro anexado costuma corrigir.
    """

    def __init__(self, message: str, *, raw_output: str, schema_name: str) -> None:
        super().__init__(message)
        self.raw_output = raw_output
        self.schema_name = schema_name


class RateLimitError(LLMError):
    """429 do NIM. A cota gratuita é limitada — vale distinguir de erro de rede."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class BudgetExceededError(LLMError):
    """Teto de chamadas de LLM da execução foi atingido.

    Não é falha de infraestrutura: é o sistema se recusando a continuar gastando
    cota. Sobe como `NodeFailure` não-recuperável para que o relatório mostre
    quais empresas ficaram sem diagnóstico e por quê — retry aqui só queimaria o
    que sobrou.
    """

    def __init__(self, message: str, *, used: int, limit: int) -> None:
        super().__init__(message)
        self.used = used
        self.limit = limit
