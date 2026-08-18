"""Testes da camada de LLM.

Nenhuma chamada real ao NIM: o backend de chat é injetado. O que precisa ser
verificado aqui não é a qualidade do modelo (isso é a suíte de avaliação), e sim
que os mecanismos em volta dele se comportam — retry que corrige schema, prompts
resolvidos por versão, observabilidade que não derruba o fluxo e escolha de
modelo por tarefa.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import BaseMessage
from pydantic import BaseModel, Field

from radar.config import Settings
from radar.llm.client import NIMClient, extract_json
from radar.llm.errors import (
    PromptNotFoundError,
    PromptRenderError,
    RateLimitError,
    SchemaValidationError,
)
from radar.llm.model_registry import DEFAULT_FAST_MODEL, LLMTask, ModelRegistry, ModelTier
from radar.llm.observability import NullObserver, build_observer
from radar.llm.registry import PROMPTS_DIR, PromptRegistry
from radar.llm.rubric import signals_rubric
from radar.llm.schemas import AxisScoreSet, EvidenceAudit, SearchPlan

# ---------------------------------------------------------------- infraestrutura


class Pessoa(BaseModel):
    """Schema mínimo — o teste é do mecanismo de retry, não de um modelo do domínio."""

    nome: str
    idade: int = Field(ge=0)


class FakeChat:
    """Backend de chat com respostas roteirizadas. Guarda o que recebeu."""

    def __init__(self, respostas: list[str | Exception]) -> None:
        self.respostas = list(respostas)
        self.chamadas: list[list[BaseMessage]] = []

    def invoke(self, messages: Any, **kwargs: Any) -> Any:
        self.chamadas.append(list(messages))
        proxima = self.respostas.pop(0)
        if isinstance(proxima, Exception):
            raise proxima

        class _Resposta:
            content = proxima

        return _Resposta()


def build_client(respostas: list[str | Exception], **kwargs: Any) -> tuple[NIMClient, FakeChat]:
    fake = FakeChat(respostas)
    # Cache desligado: estes testes exercitam o caminho de chamada ao modelo
    # (retry, validação, 429). Com cache ligado a segunda chamada idêntica não
    # chegaria ao dublê e o teste passaria a medir o cache, não o cliente.
    settings = Settings(
        nvidia_api_key="",
        langfuse_public_key="",
        langfuse_secret_key="",
        llm_cache_enabled=False,
    )
    cliente = NIMClient(
        settings=settings,
        observer=NullObserver(),
        chat_factory=lambda **_: fake,
        **kwargs,
    )
    return cliente, fake


def texto_das_mensagens(mensagens: list[BaseMessage]) -> str:
    return "\n".join(str(m.content) for m in mensagens)


# ------------------------------------------------------- retry de validação


def test_retry_de_validacao_reenvia_com_o_erro_anexado_e_converge():
    """O caso que justifica não usar `with_structured_output`.

    A primeira resposta viola o schema; a segunda só pode ser melhor se o modelo
    souber o que errou — então o erro do Pydantic precisa chegar até ele.
    """
    cliente, fake = build_client(
        [
            json.dumps({"nome": "Startup X", "idade": "vinte e nove"}),
            json.dumps({"nome": "Startup X", "idade": 29}),
        ]
    )
    prompt = cliente.prompts.get("classifier").render(
        company_profile_json="{}", today="2026-08-13"
    )

    resultado = cliente.structured(Pessoa, prompt, LLMTask.CLASSIFIER)

    assert resultado == Pessoa(nome="Startup X", idade=29)
    assert len(fake.chamadas) == 2

    segunda = texto_das_mensagens(fake.chamadas[1])
    assert "rejeitada pelo validador" in segunda
    assert "campo `idade`" in segunda, "o erro do Pydantic precisa apontar o campo"
    assert "vinte e nove" in segunda, "o modelo precisa ver a própria saída inválida"


def test_retry_de_validacao_tem_limite_explicito():
    """Sem teto, um modelo teimoso queima a cota inteira em um único documento."""
    cliente, fake = build_client([json.dumps({"nome": "X"})] * 5, validation_attempts=3)
    prompt = cliente.prompts.get("classifier").render(
        company_profile_json="{}", today="2026-08-13"
    )

    with pytest.raises(SchemaValidationError):
        cliente.structured(Pessoa, prompt, LLMTask.CLASSIFIER)

    assert len(fake.chamadas) == 3


def test_json_malformado_tambem_dispara_correcao():
    cliente, fake = build_client(
        ["não sou JSON coisa nenhuma", json.dumps({"nome": "Y", "idade": 3})]
    )
    prompt = cliente.prompts.get("classifier").render(
        company_profile_json="{}", today="2026-08-13"
    )

    assert cliente.structured(Pessoa, prompt, LLMTask.CLASSIFIER).nome == "Y"
    assert len(fake.chamadas) == 2


def test_preambulo_e_cerca_de_codigo_nao_gastam_tentativa():
    """Modelo instruct insiste em ser educado; isso não pode custar cota."""
    bruto = 'Claro! Aqui está:\n```json\n{"nome": "Z", "idade": 1}\n```\nEspero ter ajudado.'
    cliente, fake = build_client([bruto])
    prompt = cliente.prompts.get("classifier").render(
        company_profile_json="{}", today="2026-08-13"
    )

    assert cliente.structured(Pessoa, prompt, LLMTask.CLASSIFIER).nome == "Z"
    assert len(fake.chamadas) == 1


def test_extract_json_sem_objeto_falha_como_erro_de_schema():
    with pytest.raises(SchemaValidationError):
        extract_json("nenhuma chave aqui")


def test_schema_json_vai_junto_do_pedido():
    """Schema gerado do Pydantic, não escrito à mão — prompt e validação não divergem."""
    cliente, fake = build_client([json.dumps({"nome": "A", "idade": 2})])
    prompt = cliente.prompts.get("classifier").render(
        company_profile_json="{}", today="2026-08-13"
    )
    cliente.structured(Pessoa, prompt, LLMTask.CLASSIFIER)

    primeira = texto_das_mensagens(fake.chamadas[0])
    assert '"idade"' in primeira and "JSON Schema" in primeira


# ------------------------------------------------------ retry de transporte / 429


def test_erro_429_vira_rate_limit_error_e_e_retentado(monkeypatch):
    """Backoff real tornaria o teste lento — o que importa é a classificação e o reenvio."""
    monkeypatch.setattr("radar.llm.client._transport_wait", lambda _state: 0.0)

    cliente, fake = build_client(
        [RuntimeError("429 Too Many Requests"), json.dumps({"nome": "B", "idade": 4})]
    )
    prompt = cliente.prompts.get("classifier").render(
        company_profile_json="{}", today="2026-08-13"
    )

    assert cliente.structured(Pessoa, prompt, LLMTask.CLASSIFIER).nome == "B"
    assert len(fake.chamadas) == 2


def test_rate_limit_persistente_estoura_como_rate_limit_error(monkeypatch):
    monkeypatch.setattr("radar.llm.client._transport_wait", lambda _state: 0.0)

    cliente, _ = build_client(
        [RuntimeError("rate limit exceeded")] * 4, transport_attempts=2, validation_attempts=1
    )
    prompt = cliente.prompts.get("classifier").render(
        company_profile_json="{}", today="2026-08-13"
    )

    with pytest.raises(RateLimitError):
        cliente.structured(Pessoa, prompt, LLMTask.CLASSIFIER)


def test_retry_de_transporte_reenvia_a_mesma_mensagem(monkeypatch):
    """Falha de rede não é falha de conteúdo: reescrever o prompt aqui seria bug."""
    monkeypatch.setattr("radar.llm.client._transport_wait", lambda _state: 0.0)

    cliente, fake = build_client(
        [TimeoutError("connection reset"), json.dumps({"nome": "C", "idade": 5})]
    )
    prompt = cliente.prompts.get("classifier").render(
        company_profile_json="{}", today="2026-08-13"
    )
    cliente.structured(Pessoa, prompt, LLMTask.CLASSIFIER)

    assert texto_das_mensagens(fake.chamadas[0]) == texto_das_mensagens(fake.chamadas[1])


# ----------------------------------------------------------- registry de prompts


def test_registry_carrega_prompt_por_versao():
    registry = PromptRegistry()
    template = registry.get("extractor", "v1")

    assert template.prompt_version == "extractor_v1"
    assert template.system and template.user
    assert "source_documents" in template.variables


def test_registry_falha_claramente_com_versao_inexistente():
    registry = PromptRegistry()

    with pytest.raises(PromptNotFoundError) as exc:
        registry.get("extractor", "v99")

    mensagem = str(exc.value)
    assert "extractor_v99" in mensagem
    assert "v1" in mensagem, "a mensagem precisa dizer quais versões existem"


def test_registry_falha_com_prompt_inexistente():
    with pytest.raises(PromptNotFoundError):
        PromptRegistry().get("nao_existe", "v1")


def test_latest_resolve_para_a_maior_versao(tmp_path: Path):
    for versao in ("v1", "v2", "v10"):
        (tmp_path / f"teste_{versao}.md").write_text(
            f"---\nname: teste\nversion: {versao}\nvariables: []\n---\n"
            "=== SYSTEM ===\nsys\n=== USER ===\nuser\n",
            encoding="utf-8",
        )

    # v10 > v2 exige ordenação numérica; ordenação lexicográfica devolveria v2.
    assert PromptRegistry(tmp_path).get("teste").version == "v10"


def test_front_matter_divergente_do_nome_do_arquivo_falha(tmp_path: Path):
    """Se o arquivo mente sobre a própria versão, `prompt_version` deixa de valer."""
    (tmp_path / "teste_v1.md").write_text(
        "---\nname: teste\nversion: v7\nvariables: []\n---\n"
        "=== SYSTEM ===\nsys\n=== USER ===\nuser\n",
        encoding="utf-8",
    )

    with pytest.raises(PromptNotFoundError):
        PromptRegistry(tmp_path).get("teste", "v1")


def test_variavel_faltando_falha_antes_de_gastar_cota():
    with pytest.raises(PromptRenderError):
        PromptRegistry().render("extractor", "v1", company_hint="Acme")


def test_placeholder_nao_resolvido_nunca_chega_ao_modelo(tmp_path: Path):
    """Um `{{gap_axis}}` vazando produz resposta plausível e errada — pior que exceção."""
    (tmp_path / "teste_v1.md").write_text(
        "---\nname: teste\nversion: v1\nvariables: [a]\n---\n"
        "=== SYSTEM ===\nsys {{a}}\n=== USER ===\nuser {{esquecido}}\n",
        encoding="utf-8",
    )

    with pytest.raises(PromptRenderError):
        PromptRegistry(tmp_path).render("teste", "v1", a="x")


def test_lista_vira_bullets_no_prompt():
    prompt = PromptRegistry().render(
        "recommender",
        "v1",
        company_profile_json="{}",
        defensibility_json="{}",
        weakest_axis="stack_ownership",
        candidate_technologies=["NIM", "TensorRT-LLM"],
        kb_chunks="chunk",
        today="2026-08-13",
    )
    assert "- NIM\n- TensorRT-LLM" in prompt.user


@pytest.mark.parametrize(
    "nome",
    [
        "search_planner",
        "extractor",
        "classifier",
        "evidence_validator",
        "defensibility_scorer",
        "recommender",
        "briefing",
    ],
)
def test_existe_prompt_para_cada_agente(nome: str):
    """Cada tarefa do `LLMTask` precisa ter prompt em disco, senão o nó quebra em runtime."""
    template = PromptRegistry().get(nome, "v1")
    assert template.prompt_version == f"{nome}_v1"


def test_nome_do_prompt_casa_com_a_tarefa():
    """`NIMClient.run` deriva o nome do prompt do valor de `LLMTask` — o acoplamento é real."""
    registry = PromptRegistry()
    for task in LLMTask:
        assert registry.get(task.value, "v1")


def test_prompts_versionados_estao_no_pacote():
    assert PROMPTS_DIR.is_dir()
    assert len(list(PROMPTS_DIR.glob("*_v*.md"))) >= len(LLMTask)


# ------------------------------------------------------------ registry de modelos


def test_selecao_de_modelo_por_tarefa():
    registry = ModelRegistry(fast_model="pequeno", reasoning_model="grande")

    # Planejar busca é barato e o erro é detectável adiante; extrair perfil não.
    assert registry.model_for(LLMTask.SEARCH_PLANNER) == "pequeno"
    assert registry.model_for(LLMTask.EVIDENCE_VALIDATOR) == "pequeno"
    assert registry.model_for(LLMTask.EXTRACTOR) == "grande"
    assert registry.model_for(LLMTask.DEFENSIBILITY_SCORER) == "grande"
    assert registry.model_for(LLMTask.BRIEFING) == "grande"

    assert registry.tier_for(LLMTask.SEARCH_PLANNER) is ModelTier.FAST
    assert registry.tier_for(LLMTask.EXTRACTOR) is ModelTier.REASONING


def test_override_por_tarefa_ganha_da_faixa():
    registry = ModelRegistry(
        fast_model="pequeno",
        reasoning_model="grande",
        task_overrides={LLMTask.BRIEFING: "nemotron"},
    )
    assert registry.model_for(LLMTask.BRIEFING) == "nemotron"
    assert registry.model_for(LLMTask.EXTRACTOR) == "grande"


def test_from_settings_usa_o_modelo_de_chat_como_faixa_de_raciocinio(monkeypatch):
    monkeypatch.delenv("NIM_FAST_MODEL", raising=False)
    registry = ModelRegistry.from_settings(Settings(nim_chat_model="meta/llama-3.3-70b-instruct"))

    assert registry.reasoning_model == "meta/llama-3.3-70b-instruct"
    assert registry.fast_model == DEFAULT_FAST_MODEL


def test_modelo_pequeno_configuravel_por_ambiente(monkeypatch):
    monkeypatch.setenv("NIM_FAST_MODEL", "meta/llama-3.2-3b-instruct")
    registry = ModelRegistry.from_settings(Settings())

    assert registry.model_for(LLMTask.SEARCH_PLANNER) == "meta/llama-3.2-3b-instruct"


def test_backends_sao_reaproveitados_por_modelo():
    """Criar um ChatNVIDIA por chamada abriria uma conexão nova a cada nó do grafo."""
    criados: list[dict[str, Any]] = []

    def factory(**kwargs: Any) -> FakeChat:
        criados.append(kwargs)
        return FakeChat([])

    cliente = NIMClient(
        settings=Settings(),
        observer=NullObserver(),
        models=ModelRegistry(fast_model="pequeno", reasoning_model="grande"),
        chat_factory=factory,
    )
    cliente.backend_for(LLMTask.EXTRACTOR)
    cliente.backend_for(LLMTask.CLASSIFIER)  # mesmo modelo e temperatura
    cliente.backend_for(LLMTask.SEARCH_PLANNER)

    assert [c["model"] for c in criados] == ["grande", "pequeno"]


# -------------------------------------------------------------- observabilidade


def test_langfuse_desabilitado_devolve_observador_nulo():
    settings = Settings(langfuse_public_key="", langfuse_secret_key="")

    assert settings.langfuse_enabled is False
    assert isinstance(build_observer(settings), NullObserver)


def test_fluxo_completo_funciona_sem_langfuse():
    """Observabilidade ausente não pode custar uma extração."""
    cliente, _ = build_client([json.dumps({"nome": "Sem trace", "idade": 7})])
    cliente.observer = build_observer(
        Settings(langfuse_public_key="", langfuse_secret_key="")
    )
    prompt = cliente.prompts.get("classifier").render(
        company_profile_json="{}", today="2026-08-13"
    )

    assert cliente.structured(Pessoa, prompt, LLMTask.CLASSIFIER).nome == "Sem trace"
    cliente.flush()  # não pode levantar


def test_langfuse_fora_do_ar_no_meio_do_lote_nao_derruba_a_chamada():
    """O container do Langfuse cair não pode custar uma extração já paga em cota."""
    from radar.llm.observability import LangfuseObserver

    class ClienteLangfuseQuebrado:
        def start_as_current_observation(self, **kwargs: Any):
            raise RuntimeError("connection refused")

        def flush(self) -> None:
            raise RuntimeError("connection refused")

    cliente, _ = build_client([json.dumps({"nome": "Com trace morto", "idade": 1})])
    cliente.observer = LangfuseObserver(ClienteLangfuseQuebrado())
    prompt = cliente.prompts.get("classifier").render(
        company_profile_json="{}", today="2026-08-13"
    )

    assert cliente.structured(Pessoa, prompt, LLMTask.CLASSIFIER).idade == 1
    cliente.flush()  # falha de flush também é engolida


def test_build_observer_tolera_falha_na_inicializacao(monkeypatch):
    import radar.llm.observability as obs

    class LangfuseQuebrado:
        def __init__(self, **kwargs: Any) -> None:
            raise RuntimeError("sem servidor")

    monkeypatch.setitem(
        __import__("sys").modules, "langfuse", type("m", (), {"Langfuse": LangfuseQuebrado})
    )
    observador = obs.build_observer(
        Settings(langfuse_public_key="pk", langfuse_secret_key="sk")
    )

    assert isinstance(observador, NullObserver)


# ------------------------------------------------ rubrica e contratos dos prompts


def test_rubrica_do_scorer_sai_do_weights_yaml():
    rubrica = signals_rubric()

    assert "proprietary_data" in rubrica
    assert "dataset próprio construído ao longo do tempo" in rubrica
    assert "'powered by GPT-4' como selo no rodapé" in rubrica
    assert "evidência insuficiente" in rubrica, "o limiar precisa chegar ao prompt"


def test_prompt_do_scorer_carrega_a_rubrica_e_o_invariante():
    prompt = PromptRegistry().render(
        "defensibility_scorer",
        "v1",
        company_profile_json="{}",
        classification_json="{}",
        signals_rubric=signals_rubric(),
        weights_version="0.1.0-inicial",
        today="2026-08-13",
    )

    assert "dataset próprio construído ao longo do tempo" in prompt.system
    assert "0.1.0-inicial" in prompt.system
    # O invariante do projeto precisa estar escrito, não subentendido.
    assert "never contaminate each other" in prompt.system
    assert "lowers `confidence`, never `score`" in prompt.system


def test_prompt_do_classificador_diz_que_non_ai_nao_e_o_default():
    prompt = PromptRegistry().render(
        "classifier", "v1", company_profile_json="{}", today="2026-08-13"
    )
    assert "`non_ai` is NOT the" in prompt.system


def test_prompt_do_extrator_proibe_parafrase():
    prompt = PromptRegistry().render(
        "extractor", "v1", company_hint="Acme", source_documents="...", today="2026-08-13"
    )
    assert "LITERALLY" in prompt.system
    assert "Do not summarize" in prompt.system


def test_prompt_do_briefing_exige_caveats_e_proibe_acusacao():
    prompt = PromptRegistry().render(
        "briefing",
        "v1",
        company_profile_json="{}",
        classification_json="{}",
        defensibility_json="{}",
        priority_json="{}",
        recommendations_json="[]",
        evidence_gaps="nenhuma",
        today="2026-08-13",
    )
    assert "mandatory, never empty" in prompt.system
    assert "wrapper" in prompt.system  # o enquadramento proibido está nomeado


def test_prompt_do_planner_usa_diretorios_brasileiros():
    prompt = PromptRegistry().render(
        "search_planner", "v1", user_query="startups de saúde", max_queries=8, today="2026-08-13"
    )
    for dominio in ("startse.com", "distrito.me", "cubo.network", "abstartups.com.br"):
        assert dominio in prompt.system
    assert "site:" in prompt.system


# ------------------------------------------- saída estruturada com schemas reais


def test_search_plan_valida_saida_do_planner():
    plano = {
        "interpreted_intent": "Startups brasileiras de saúde com IA",
        "sector_focus": ["saúde"],
        "priority_domains": ["distrito.me"],
        "exclusions": ["telemedicina genérica"],
        "queries": [
            {
                "query": '"startup" IA saúde site:distrito.me',
                "signal_type": "descoberta",
                "rationale": "Diretório com curadoria do ecossistema.",
                "target_domain": "distrito.me",
                "priority": 1,
            },
            {
                "query": '"engenheiro de machine learning" prontuário site:gupy.io',
                "signal_type": "stack_hiring",
                "rationale": "Vaga revela stack que o marketing esconde.",
                "priority": 2,
            },
            {
                "query": '"rodada seed" healthtech IA site:braziljournal.com',
                "signal_type": "funding",
                "rationale": "Capital recente indica capacidade de agir.",
                "priority": 3,
            },
        ],
    }
    cliente, _ = build_client([json.dumps(plano)])
    prompt = cliente.prompts.render(
        "search_planner", "v1", user_query="saúde", max_queries=8, today="2026-08-13"
    )

    resultado = cliente.structured(SearchPlan, prompt, LLMTask.SEARCH_PLANNER)
    assert len(resultado.queries) == 3
    assert resultado.queries[0].target_domain == "distrito.me"


def test_run_carrega_o_prompt_da_tarefa_e_devolve_objeto_validado():
    auditoria = {
        "company_name": "Acme",
        "audits": [
            {
                "field_path": "inference_provider",
                "verdict": "paraphrased",
                "explanation": "O trecho foi reescrito pelo extrator.",
                "offending_excerpt": "usamos inferência própria",
            }
        ],
        "unsupported_fields": ["inference_provider"],
        "grounding_ratio": 0.0,
        "requires_recollection": True,
        "suggested_queries": ["Acme carreiras engenheiro de machine learning"],
    }
    cliente, fake = build_client([json.dumps(auditoria)])

    resultado = cliente.run(
        LLMTask.EVIDENCE_VALIDATOR,
        EvidenceAudit,
        company_profile_json="{}",
        source_documents="...",
        today="2026-08-13",
    )

    assert resultado.requires_recollection is True
    assert "Audite campo a campo" in texto_das_mensagens(fake.chamadas[0])


def test_axis_score_set_preserva_o_invariante_score_versus_confianca():
    """Eixo sem evidência: score neutro e confiança baixa — nunca score baixo."""
    eixos = {
        "company_name": "Acme",
        "axes": [
            {
                "axis": axis,
                "score": 50.0,
                "confidence": 0.2,
                "positive_signals": [],
                "negative_signals": [],
                "evidences": [],
                "rationale": "Sem evidência pública sobre este eixo.",
            }
            for axis in (
                "proprietary_data",
                "workflow_depth",
                "stack_ownership",
                "distribution",
            )
        ],
    }
    cliente, _ = build_client([json.dumps(eixos)])
    prompt = cliente.prompts.render(
        "defensibility_scorer",
        "v1",
        company_profile_json="{}",
        classification_json="{}",
        signals_rubric=signals_rubric(),
        weights_version="0.1.0-inicial",
        today="2026-08-13",
    )

    resultado = cliente.structured(AxisScoreSet, prompt, LLMTask.DEFENSIBILITY_SCORER)
    score = resultado.to_defensibility_score(weights_version="0.1.0-inicial")

    assert score.total == 50.0
    assert all(not eixo.is_actionable for eixo in score.axes)
    # Confiança baixa não pode gerar recomendação: o gate fica vazio.
    assert score.candidate_technologies() == []
