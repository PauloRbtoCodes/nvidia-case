"""Isolamento da suíte contra os caches em disco.

Existe por causa de um bug real, encontrado ao ligar o cache de LLM: a suíte
gravou respostas dos dublês em `data/cache/llm` e, na execução seguinte, os
testes do grafo passaram a ler de lá. O efeito foi silencioso e perverso — um
teste que injeta `RuntimeError("modelo fora do ar")` no planner passou a receber
resposta válida do cache, e a falha que ele existia para verificar deixou de
acontecer.

Duas lições que valem para além do teste:

1. **Cache em disco é estado global.** Um teste que escreve nele contamina os
   seguintes e, pior, contamina a execução real do desenvolvedor.
2. **A configuração padrão do produto não é a configuração da suíte.** O cache é
   desejável em produção e nocivo aqui: quem testa retry, backoff e degradação
   precisa que a chamada aconteça de verdade.

O autouse abaixo desliga o cache de LLM e aponta o cache de scraping para um
diretório temporário em toda a suíte. Testes que querem exercitar o cache
constroem o objeto explicitamente — é o caso de `test_llm_cache.py`.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from radar.config import Settings, get_settings


@pytest.fixture(autouse=True)
def _isola_caches(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Nenhum teste escreve nos caches reais do projeto."""
    base: Path = tmp_path_factory.mktemp("radar-cache")

    monkeypatch.setenv("LLM_CACHE_ENABLED", "false")
    monkeypatch.setenv("LLM_CACHE_DIR", str(base / "llm"))
    monkeypatch.setenv("SCRAPER_CACHE_DIR", str(base / "http"))

    # `get_settings` é `lru_cache`: sem limpar, o primeiro teste a instanciar
    # Settings congelaria a configuração para toda a sessão.
    get_settings.cache_clear()
    Settings.model_config["env_file"] = None
    yield
    get_settings.cache_clear()
