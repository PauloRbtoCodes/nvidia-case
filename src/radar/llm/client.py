"""Cliente NVIDIA NIM com saída estruturada validada por Pydantic.

Peça central da camada: Extractor, Classifier, Scorer, Recommender e Briefing
todos entregam um modelo de `radar.models` já validado, ou levantam exceção.
Nada de dicionário meio preenchido circulando pelo grafo.

Três decisões que explicam o desenho:

1. **Validação própria em vez de `with_structured_output`.** Precisamos ver o
   texto cru que falhou para devolvê-lo ao modelo junto do erro de validação —
   o wrapper do LangChain esconde isso. Como os schemas daqui são exigentes de
   propósito (`Evidence.excerpt` com `min_length=20`, `Recommendation` que
   levanta erro sem citação), reenviar com o erro anexado é o caminho normal de
   correção, não uma exceção rara.

2. **Dois níveis de retry.** Falha de transporte/429 é uma coisa (mesma
   requisição, esperar mais); falha de schema é outra (requisição *diferente*,
   com o erro anexado). Misturar os dois desperdiça cota reenviando prompt
   idêntico para um modelo que já mostrou como errou.

3. **Backend injetável.** `chat_factory` permite testar tudo sem chave de API e
   sem tocar a rede.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol, TypeVar

import structlog
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
)

from radar.config import Settings, get_settings
from radar.llm.cache import CachedCompletion, CompletionCache, fingerprint
from radar.llm.errors import LLMError, RateLimitError, SchemaValidationError
from radar.llm.model_registry import LLMTask, ModelRegistry
from radar.llm.observability import Observer, build_observer
from radar.llm.quota import ConcurrencyGate, LLMBudget
from radar.llm.registry import PromptRegistry, RenderedPrompt, get_prompt_registry

log = structlog.get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

#: Tentativas por tipo de falha. Separadas de propósito — ver docstring do módulo.
DEFAULT_TRANSPORT_ATTEMPTS = 4
DEFAULT_VALIDATION_ATTEMPTS = 3

#: Backoff. O 429 do NIM na cota gratuita costuma exigir espera na casa de
#: dezenas de segundos; erro de rede resolve em poucos segundos.
TRANSPORT_BACKOFF_SECONDS = (1.0, 3.0, 8.0)
RATE_LIMIT_BACKOFF_SECONDS = (15.0, 45.0, 90.0)

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)
_RATE_LIMIT_HINTS = ("429", "rate limit", "too many requests", "quota")


class ChatBackend(Protocol):
    """Superfície mínima usada do `ChatNVIDIA` — o resto do projeto não precisa mais."""

    def invoke(self, messages: Sequence[BaseMessage], **kwargs: Any) -> Any: ...


ChatFactory = Callable[..., ChatBackend]


def _is_rate_limit(exc: BaseException) -> bool:
    """Detecta 429 sem depender do tipo concreto de exceção do SDK.

    `ChatNVIDIA` propaga erros de HTTP em formatos diferentes conforme a camada
    que falhou; casar por status e por texto é feio, mas é o que sobrevive a
    atualização de versão do SDK.
    """
    status = getattr(exc, "status_code", None) or getattr(
        getattr(exc, "response", None), "status_code", None
    )
    if status == 429:
        return True
    texto = str(exc).lower()
    return any(hint in texto for hint in _RATE_LIMIT_HINTS)


def _retry_after(exc: BaseException) -> float | None:
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    try:
        valor = headers.get("retry-after") or headers.get("Retry-After")
        return float(valor) if valor is not None else None
    except (TypeError, ValueError):
        return None


def _transport_wait(state: RetryCallState) -> float:
    """Espera longa para 429, curta para o resto — e respeita `Retry-After`."""
    exc = state.outcome.exception() if state.outcome else None
    indice = min(max(state.attempt_number - 1, 0), len(TRANSPORT_BACKOFF_SECONDS) - 1)

    if isinstance(exc, RateLimitError):
        if exc.retry_after:
            return exc.retry_after
        return RATE_LIMIT_BACKOFF_SECONDS[
            min(indice, len(RATE_LIMIT_BACKOFF_SECONDS) - 1)
        ]
    return TRANSPORT_BACKOFF_SECONDS[indice]


def extract_json(raw: str) -> str:
    """Isola o objeto JSON de uma resposta que pode vir com preâmbulo ou cerca.

    Modelos instruct teimam em escrever "Aqui está o JSON:" antes do bloco,
    mesmo instruídos a não fazê-lo. Rejeitar a resposta inteira por isso gastaria
    uma tentativa de cota por educação do modelo.
    """
    fence = _JSON_FENCE.search(raw)
    candidato = fence.group(1) if fence else raw

    inicio = candidato.find("{")
    fim = candidato.rfind("}")
    if inicio == -1 or fim == -1 or fim < inicio:
        raise SchemaValidationError(
            "Resposta do modelo não contém objeto JSON.",
            raw_output=raw,
            schema_name="?",
        )
    return candidato[inicio : fim + 1]


def _format_validation_error(exc: ValidationError) -> str:
    """Erros compactos: caminho do campo + mensagem. O JSON completo do Pydantic
    ocupa contexto sem ajudar o modelo a corrigir."""
    linhas = []
    for erro in exc.errors(include_url=False)[:15]:
        caminho = ".".join(str(p) for p in erro["loc"]) or "(raiz)"
        linhas.append(f"- campo `{caminho}`: {erro['msg']}")
    return "\n".join(linhas)


def _default_cache(settings: Settings) -> CompletionCache | None:
    """Cache ligado por padrão, desligável por configuração.

    Ligado porque o ciclo de trabalho é iterar sobre o mesmo lote; a chave inclui
    o conteúdo do prompt, então nenhum sinal novo é escondido por ele.
    """
    if not settings.llm_cache_enabled:
        return None
    return CompletionCache(
        settings.llm_cache_path, ttl_seconds=settings.llm_cache_ttl_seconds
    )


class NIMClient:
    """Wrapper sobre `ChatNVIDIA` com saída estruturada, retry e traces."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        models: ModelRegistry | None = None,
        prompts: PromptRegistry | None = None,
        observer: Observer | None = None,
        chat_factory: ChatFactory | None = None,
        transport_attempts: int = DEFAULT_TRANSPORT_ATTEMPTS,
        validation_attempts: int = DEFAULT_VALIDATION_ATTEMPTS,
        gate: ConcurrencyGate | None = None,
        budget: LLMBudget | None = None,
        cache: CompletionCache | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.models = models or ModelRegistry.from_settings(self.settings)
        self.prompts = prompts or get_prompt_registry()
        self.observer = observer or build_observer(self.settings)
        self.transport_attempts = transport_attempts
        self.validation_attempts = validation_attempts

        self._chat_factory = chat_factory or self._default_chat_factory
        self._backends: dict[tuple[str, float], ChatBackend] = {}

        # Cota e cache são do cliente, não do nó: só quem é compartilhado pela
        # execução inteira sabe quantas chamadas estão em voo e quantas já foram.
        self.gate = gate or ConcurrencyGate(self.settings.llm_max_concurrency)
        self.budget = budget or LLMBudget(self.settings.llm_max_calls_per_run)
        self.cache = cache if cache is not None else _default_cache(self.settings)

    # ------------------------------------------------------------------ backend

    def _default_chat_factory(self, *, model: str, temperature: float) -> ChatBackend:
        """Importado aqui dentro para que testes sem chave de API nem carreguem o SDK."""
        from langchain_nvidia_ai_endpoints import ChatNVIDIA

        extras: dict[str, Any] = {}
        if self.settings.nim_disable_thinking:
            # Kwarg direto, e nao `extra_body`: o wrapper move extra_body para
            # model_kwargs e a API responde 400. Ver Settings.nim_disable_thinking.
            extras["chat_template_kwargs"] = {"enable_thinking": False}

        return ChatNVIDIA(
            model=model,
            api_key=self.settings.nvidia_api_key,
            base_url=self.settings.nim_base_url,
            temperature=temperature,
            **extras,
        )

    def backend_for(self, task: LLMTask) -> ChatBackend:
        """Um backend por (modelo, temperatura), reaproveitado entre chamadas."""
        model = self.models.model_for(task)
        temperature = self.models.temperature_for(task)
        chave = (model, temperature)
        if chave not in self._backends:
            self._backends[chave] = self._chat_factory(model=model, temperature=temperature)
        return self._backends[chave]

    # ------------------------------------------------------------- chamada crua

    @staticmethod
    def _as_pairs(messages: Sequence[BaseMessage]) -> list[tuple[str, str]]:
        return [(m.type, str(m.content)) for m in messages]

    def _invoke(self, task: LLMTask, messages: list[BaseMessage]) -> str:
        """Uma chamada, com cache, cota e retry de transporte/429.

        A ordem importa e não é arbitrária:

        1. **Cache primeiro.** Acerto não consome orçamento nem ocupa vaga de
           concorrência — não houve chamada. Contabilizar cache como gasto
           esvaziaria o orçamento sem pressionar a API.
        2. **Orçamento antes do semáforo.** Recusar cedo evita que uma thread
           fique bloqueada esperando vaga para uma chamada que já está proibida.
        3. **Semáforo em volta do retry inteiro**, não de cada tentativa. Quem
           está em backoff de 429 continua ocupando vaga de propósito: liberar a
           vaga durante a espera deixaria outra thread entrar e tomar o mesmo
           429, que é exatamente o efeito que o teto existe para evitar.
        """
        backend = self.backend_for(task)
        model = self.models.model_for(task)
        temperature = self.models.temperature_for(task)

        chave: str | None = None
        if self.cache is not None:
            chave = fingerprint(model, temperature, self._as_pairs(messages))
            entrada = self.cache.get(chave)
            if entrada is not None:
                self.budget.record_cache_hit()
                log.debug("llm_cache_hit", task=task.value, model=model, key=chave[:12])
                return entrada.output

        restante = self.budget.consume()
        if restante <= 10:
            log.warning("orcamento_llm_no_fim", restante=restante, teto=self.budget.max_calls)

        def _once() -> str:
            try:
                resposta = backend.invoke(messages)
            except Exception as exc:
                if _is_rate_limit(exc):
                    raise RateLimitError(str(exc), retry_after=_retry_after(exc)) from exc
                raise
            conteudo = getattr(resposta, "content", resposta)
            if isinstance(conteudo, list):
                # Alguns modelos devolvem conteúdo em blocos; concatenar preserva o JSON.
                conteudo = "".join(
                    bloco.get("text", "") if isinstance(bloco, dict) else str(bloco)
                    for bloco in conteudo
                )
            return str(conteudo)

        with self.gate.hold():
            for tentativa in Retrying(
                stop=stop_after_attempt(self.transport_attempts),
                wait=_transport_wait,
                reraise=True,
            ):
                with tentativa:
                    saida = _once()
                    if self.cache is not None and chave is not None:
                        self.cache.set(
                            chave,
                            CachedCompletion(
                                model=model,
                                temperature=temperature,
                                prompt_fingerprint=chave,
                                output=saida,
                                created_at=datetime.now(UTC),
                            ),
                        )
                    return saida

        raise LLMError("Retry de transporte terminou sem resultado.")  # pragma: no cover

    # ------------------------------------------------------- saída estruturada

    def structured(
        self,
        schema: type[T],
        prompt: RenderedPrompt,
        task: LLMTask,
        *,
        trace_name: str | None = None,
    ) -> T:
        """Chama o modelo e devolve uma instância validada de `schema`.

        O retry de validação reenvia a conversa inteira acrescida da resposta
        inválida e do erro do Pydantic. Na prática o modelo corrige na segunda
        tentativa, porque o erro aponta o campo exato — é bem mais barato que
        recomeçar do zero com o mesmo prompt.
        """
        model_id = self.models.model_for(task)

        base_messages: list[BaseMessage] = [
            SystemMessage(content=prompt.system),
            HumanMessage(content=f"{prompt.user}\n\n{_schema_block(schema)}"),
        ]
        messages = list(base_messages)

        with self.observer.generation(
            name=trace_name or f"{task.value}:{prompt.prompt_version}",
            model=model_id,
            prompt_input={"system": prompt.system, "user": prompt.user},
            metadata={
                "task": task.value,
                "prompt_version": prompt.prompt_version,
                "schema": schema.__name__,
                "tier": self.models.tier_for(task).value,
            },
        ) as span:
            for tentativa in Retrying(
                stop=stop_after_attempt(self.validation_attempts),
                retry=retry_if_exception_type(SchemaValidationError),
                reraise=True,
            ):
                with tentativa:
                    numero = tentativa.retry_state.attempt_number
                    raw = self._invoke(task, messages)
                    try:
                        objeto = _validate(schema, raw)
                    except SchemaValidationError as exc:
                        log.warning(
                            "schema_invalido",
                            task=task.value,
                            prompt_version=prompt.prompt_version,
                            schema=schema.__name__,
                            tentativa=numero,
                            erro=str(exc)[:300],
                        )
                        # A próxima tentativa vê o que escreveu e por que falhou.
                        messages = [
                            *base_messages,
                            AIMessage(content=exc.raw_output),
                            HumanMessage(content=_repair_instruction(exc)),
                        ]
                        span.update(level="WARNING", status_message=str(exc)[:300])
                        raise

                    span.update(output=objeto.model_dump(mode="json"))
                    log.debug(
                        "saida_estruturada_ok",
                        task=task.value,
                        prompt_version=prompt.prompt_version,
                        schema=schema.__name__,
                        tentativas=numero,
                    )
                    return objeto

        raise LLMError("Retry de validação terminou sem resultado.")  # pragma: no cover

    def run(
        self,
        task: LLMTask,
        schema: type[T],
        *,
        version: str = "latest",
        prompt_name: str | None = None,
        **variables: Any,
    ) -> T:
        """Atalho usado pelos nós do grafo: carrega o prompt da tarefa e executa.

        O nome do prompt casa com o valor de `LLMTask`, então o nó só precisa
        passar as variáveis. `version` fica exposto para a avaliação comparar
        redações (`extractor_v1` vs `extractor_v2`) sem mexer no código do nó.
        """
        prompt = self.prompts.render(prompt_name or task.value, version, **variables)
        return self.structured(schema, prompt, task)

    def complete(self, prompt: RenderedPrompt, task: LLMTask) -> str:
        """Texto livre. Usado só onde não há schema (ex.: markdown do briefing)."""
        messages: list[BaseMessage] = [
            SystemMessage(content=prompt.system),
            HumanMessage(content=prompt.user),
        ]
        with self.observer.generation(
            name=f"{task.value}:{prompt.prompt_version}",
            model=self.models.model_for(task),
            prompt_input={"system": prompt.system, "user": prompt.user},
            metadata={"task": task.value, "prompt_version": prompt.prompt_version},
        ) as span:
            saida = self._invoke(task, messages)
            span.update(output=saida)
            return saida

    def flush(self) -> None:
        """Descarrega traces e reporta o consumo de cota da execução.

        O consumo sai no log de fim de lote porque é a única forma de saber, sem
        abrir o painel do NIM, se o lote passou perto do teto. Um lote que
        terminou em 380/400 chamadas passou raspando e o próximo, com uma empresa
        a mais, vai estourar.
        """
        log.info("cota_llm", **self.budget.snapshot())
        self.observer.flush()


def _validate[M: BaseModel](schema: type[M], raw: str) -> M:
    try:
        return schema.model_validate_json(extract_json(raw))
    except SchemaValidationError as exc:
        exc.schema_name = schema.__name__
        raise
    except ValidationError as exc:
        raise SchemaValidationError(
            f"Saída não valida contra {schema.__name__}:\n{_format_validation_error(exc)}",
            raw_output=raw,
            schema_name=schema.__name__,
        ) from exc
    except ValueError as exc:  # JSON malformado
        raise SchemaValidationError(
            f"JSON malformado para {schema.__name__}: {exc}",
            raw_output=raw,
            schema_name=schema.__name__,
        ) from exc


def _schema_block(schema: type[BaseModel]) -> str:
    """Anexa o JSON Schema ao pedido.

    Mandar o schema gerado pelo Pydantic (e não uma descrição escrita à mão)
    garante que prompt e validação nunca divirjam quando `models/` mudar.
    """
    esquema = json.dumps(schema.model_json_schema(), ensure_ascii=False, indent=2)
    return (
        "Responda com UM único objeto JSON válido, aderente ao JSON Schema abaixo.\n"
        "Sem markdown, sem cercas de código, sem texto antes ou depois.\n"
        "Omita campos opcionais em vez de inventar valor.\n\n"
        f"```json\n{esquema}\n```"
    )


def _repair_instruction(exc: SchemaValidationError) -> str:
    """Mensagem de correção — curta e apontando o campo, não o schema inteiro."""
    return (
        "Sua resposta anterior foi rejeitada pelo validador:\n\n"
        f"{exc}\n\n"
        "Corrija APENAS o que foi apontado e devolva o objeto JSON completo outra vez. "
        "Não invente conteúdo para preencher campo obrigatório: se falta evidência, "
        "omita o campo opcional ou use o valor que representa 'desconhecido'. "
        "Responda somente com o JSON."
    )
