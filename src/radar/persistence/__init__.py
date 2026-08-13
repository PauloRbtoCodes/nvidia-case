"""Camada de persistência: esquema relacional, sessões e repositórios.

Projeção de `radar.models` no Postgres. O resto do sistema importa daqui e nunca
monta SQL ou instancia ORM por conta própria — é o que mantém a conversão
Pydantic ↔ tabela em um lugar só.
"""

from radar.persistence.db import (
    async_session_scope,
    build_engine,
    configure,
    create_all,
    drop_all,
    get_engine,
    get_session_factory,
    healthcheck,
    reset,
    session_scope,
)
from radar.persistence.repositories import (
    BriefingRepository,
    ClassificationRepository,
    CompanyRepository,
    EvidenceRepository,
    QueueItem,
    RecommendationRepository,
    ScoreRepository,
    normalize_name,
    normalize_website,
    priority_queue,
)
from radar.persistence.tables import Base

__all__ = [
    "Base",
    "BriefingRepository",
    "ClassificationRepository",
    "CompanyRepository",
    "EvidenceRepository",
    "QueueItem",
    "RecommendationRepository",
    "ScoreRepository",
    "async_session_scope",
    "build_engine",
    "configure",
    "create_all",
    "drop_all",
    "get_engine",
    "get_session_factory",
    "healthcheck",
    "normalize_name",
    "normalize_website",
    "priority_queue",
    "reset",
    "session_scope",
]
