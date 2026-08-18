"""Configuração central, carregada de .env via Pydantic Settings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # NVIDIA NIM
    nvidia_api_key: str = Field(default="")
    nim_base_url: str = "https://integrate.api.nvidia.com/v1"
    nim_chat_model: str = "meta/llama-3.3-70b-instruct"
    nim_fast_model: str = "meta/llama-3.1-8b-instruct"
    """Modelo pequeno para tarefas onde o erro é barato e detectável adiante
    (search planner, evidence validator). A quota gratuita do NIM é limitada, e
    gastar o modelo grande em planejamento de query desperdiça o orçamento das
    tarefas que realmente precisam de raciocínio."""

    nim_embed_model: str = "nvidia/nv-embedqa-e5-v5"

    # Reranking e busca
    cohere_api_key: str = Field(default="")
    cohere_rerank_model: str = "rerank-v3.5"
    tavily_api_key: str = Field(default="")

    # Infraestrutura
    database_url: str = "postgresql+psycopg://radar:radar@localhost:5432/radar"
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "nvidia_kb"

    # Observabilidade
    langfuse_public_key: str = Field(default="")
    langfuse_secret_key: str = Field(default="")
    langfuse_host: str = "http://localhost:3001"

    # Scraping
    scraper_user_agent: str = "NVIDIAStartupRadar/0.1 (projeto academico)"
    scraper_rate_limit_seconds: float = 2.0
    scraper_respect_robots: bool = True
    scraper_cache_dir: Path = Path("data/cache")
    scraper_timeout_seconds: float = 20.0

    # Governança de cota do LLM
    llm_max_concurrency: int = 4
    """Chamadas simultâneas ao NIM. O fan-out `Send` do grafo dispara uma rajada
    por empresa; sem teto, N empresas viram N rajadas e o 429 chega para todas."""

    llm_max_calls_per_run: int = 400
    """Teto de chamadas por execução do grafo. Evita que um dia ruim da API
    transforme retry legítimo em cota inteira consumida sem diagnóstico."""

    llm_cache_enabled: bool = True
    llm_cache_dir: Path = Path("data/cache/llm")
    llm_cache_ttl_seconds: float = 7 * 24 * 3600
    """Cache de resposta do modelo, chaveado pelo conteúdo exato do prompt.
    Mudou a evidência raspada, muda a chave — por isso não mascara mudança de
    sinal e pode ficar ligado por padrão."""

    # RAG
    chunk_size_tokens: int = 768
    chunk_overlap_ratio: float = 0.15
    hybrid_top_k: int = 30
    rerank_top_n: int = 5

    # Scoring
    weights_path: Path = Path("src/radar/scoring/weights.yaml")

    @property
    def cache_dir(self) -> Path:
        path = self.scraper_cache_dir
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def llm_cache_path(self) -> Path:
        path = self.llm_cache_dir
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def langfuse_enabled(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)

    def missing_required_keys(self) -> list[str]:
        """Checagem explícita para o `make check` — falhar cedo com mensagem clara."""
        required = {
            "NVIDIA_API_KEY": self.nvidia_api_key,
            "COHERE_API_KEY": self.cohere_api_key,
            "TAVILY_API_KEY": self.tavily_api_key,
        }
        return [name for name, value in required.items() if not value]


@lru_cache
def get_settings() -> Settings:
    return Settings()
