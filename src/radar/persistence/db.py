"""Engine, sessões e healthcheck.

A URL sai sempre de `Settings.database_url` — nunca hardcoded, porque os testes
rodam em SQLite em memória e o LangGraph checkpointer precisa apontar para o
mesmo Postgres da aplicação.

Sync e async convivem de propósito: os repositórios e as migrations são
síncronos (simples, previsível, e o Alembic é síncrono de qualquer jeito),
enquanto a FastAPI ganha um caminho async para não bloquear o event loop ao
servir a fila de prioridade. O driver é o mesmo (`psycopg` 3), então não há
duplicação de dependência.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker

from radar.config import get_settings

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_async_engine: AsyncEngine | None = None
_async_session_factory: async_sessionmaker[AsyncSession] | None = None


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
    """SQLite ignora FOREIGN KEY por padrão.

    Sem este PRAGMA os testes passariam com integridade referencial que o
    Postgres de produção rejeitaria — exatamente o tipo de divergência que faz
    o teste em SQLite não valer nada.
    """
    if type(dbapi_connection).__module__.startswith("sqlite3"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def build_engine(url: str | None = None, *, echo: bool = False, **kwargs: Any) -> Engine:
    """Cria uma engine avulsa. Útil para testes e para as migrations."""
    url = url or get_settings().database_url
    options: dict[str, Any] = {"echo": echo, "future": True}

    if _is_sqlite(url):
        # StaticPool + check_same_thread mantêm o `:memory:` vivo entre sessões;
        # sem isso cada conexão nova abriria um banco vazio.
        from sqlalchemy.pool import StaticPool

        options |= {"connect_args": {"check_same_thread": False}, "poolclass": StaticPool}
    else:
        # pre_ping evita a primeira query falhar com conexão morta depois de o
        # container do Postgres reiniciar — cenário rotineiro em desenvolvimento.
        options |= {"pool_pre_ping": True, "pool_size": 5, "max_overflow": 10}

    options |= kwargs
    return create_engine(url, **options)


def get_engine() -> Engine:
    """Engine singleton do processo. Pool compartilhado é o ponto do pool."""
    global _engine
    if _engine is None:
        _engine = build_engine()
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _session_factory


def configure(engine: Engine) -> None:
    """Substitui a engine global — ponto de entrada dos testes e de scripts.

    `expire_on_commit=False` porque os repositórios devolvem objetos Pydantic
    construídos a partir do ORM; expirar atributos após o commit dispararia
    SELECTs extras (ou `DetachedInstanceError`) na conversão.
    """
    global _engine, _session_factory
    _engine = engine
    _session_factory = sessionmaker(bind=engine, expire_on_commit=False)


def reset() -> None:
    """Descarta engine e fábricas. Chamado entre testes para não vazar estado."""
    global _engine, _session_factory, _async_engine, _async_session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
    _async_engine = None
    _async_session_factory = None


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transação com commit no sucesso e rollback na exceção.

    Um lote de scraping que falha no meio não pode deixar meia empresa gravada:
    ou a startup entra completa com suas evidências, ou não entra.
    """
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def build_async_engine(url: str | None = None, *, echo: bool = False, **kwargs: Any) -> AsyncEngine:
    """Engine async para a API. Requer driver async (`psycopg` já é)."""
    url = url or get_settings().database_url
    return create_async_engine(url, echo=echo, pool_pre_ping=not _is_sqlite(url), **kwargs)


def get_async_engine() -> AsyncEngine:
    global _async_engine
    if _async_engine is None:
        _async_engine = build_async_engine()
    return _async_engine


def get_async_session_factory() -> async_sessionmaker[AsyncSession]:
    global _async_session_factory
    if _async_session_factory is None:
        _async_session_factory = async_sessionmaker(
            bind=get_async_engine(), expire_on_commit=False
        )
    return _async_session_factory


@asynccontextmanager
async def async_session_scope() -> AsyncIterator[AsyncSession]:
    session = get_async_session_factory()()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


def healthcheck(engine: Engine | None = None) -> bool:
    """`SELECT 1`. Falhar cedo e com mensagem clara vale mais que stack trace no meio do grafo."""
    try:
        target = engine or get_engine()
        with target.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except SQLAlchemyError:
        return False


def create_all(engine: Engine | None = None) -> None:
    """Cria o esquema direto do metadata.

    Atalho para testes e protótipos. Em Postgres o caminho oficial é
    `alembic upgrade head` — o esquema versionado é o que permite evoluir sem
    perder os dados já coletados.
    """
    from radar.persistence.tables import Base

    Base.metadata.create_all(engine or get_engine())


def drop_all(engine: Engine | None = None) -> None:
    from radar.persistence.tables import Base

    Base.metadata.drop_all(engine or get_engine())


def current_dialect(connection: Connection) -> str:
    return connection.dialect.name


__all__ = [
    "async_session_scope",
    "build_async_engine",
    "build_engine",
    "configure",
    "create_all",
    "current_dialect",
    "drop_all",
    "get_async_engine",
    "get_async_session_factory",
    "get_engine",
    "get_session_factory",
    "healthcheck",
    "reset",
    "session_scope",
]
