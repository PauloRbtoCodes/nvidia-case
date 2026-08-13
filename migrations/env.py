"""Ambiente do Alembic.

A URL do banco vem de `Settings.database_url` — a mesma que a aplicação usa.
Ler de duas fontes diferentes é como se aplica migration no banco errado.

`target_metadata` aponta para o metadata real de `radar.persistence.tables`, e
não para uma cópia: o autogenerate compara o banco com o código que está em
produção, e qualquer divergência aparece como diff em vez de virar surpresa.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from radar.config import get_settings
from radar.persistence.tables import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# A URL só é injetada aqui, nunca no alembic.ini versionado.
config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def _include_object(_object, name: str, type_: str, _reflected, _compare_to) -> bool:
    """Ignora as tabelas do checkpointer do LangGraph.

    Elas são criadas e migradas pela própria biblioteca (`PostgresSaver.setup()`).
    Se entrassem no autogenerate, o Alembic tentaria dropá-las a cada revisão por
    não as encontrar no nosso metadata.
    """
    return not (type_ == "table" and name.startswith(("checkpoint", "writes")))


def run_migrations_offline() -> None:
    """Gera SQL sem conectar — útil para revisar o DDL antes de aplicar em produção."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # compare_type detecta mudança de tipo de coluna; compare_server_default
            # evita diffs falsos com o default que o próprio banco reescreve.
            compare_type=True,
            compare_server_default=True,
            include_object=_include_object,
            # SQLite não faz ALTER TABLE de verdade; sem batch mode nenhuma
            # migration futura rodaria no banco usado pelos testes.
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
