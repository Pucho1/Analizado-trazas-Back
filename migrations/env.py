"""Alembic environment: runs migrations against PostgreSQL with the async engine.

The URL comes from `config.attributes["database_url"]` when a caller provides it (the
tests do, to target the test database); otherwise it is built from the `.env` settings.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import URL, pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.infrastructure.database.session import database_url
from app.infrastructure.database.tables import Base

config = context.config

# Only configure logging from alembic.ini when run from the command line, so that
# programmatic callers (tests) keep their own logging setup.
if config.config_file_name is not None and not config.attributes.get("database_url"):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> URL | str:
    """Database URL given by the caller, or the one configured in `.env`."""
    given: URL | str | None = config.attributes.get("database_url")
    return given if given is not None else database_url(Settings())


def run_migrations_offline() -> None:
    """Emit the migration SQL to stdout instead of running it (`alembic upgrade --sql`)."""
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run the pending migrations on an open connection."""
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Open a single async connection and run the migrations through it."""
    engine = create_async_engine(_url(), poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    """Run the migrations against the live database."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
