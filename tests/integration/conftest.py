"""Fixtures for the tests that run against a real PostgreSQL (`observa_test`).

They need `TEST_DATABASE_URL` (in the environment or `.env`) and the docker-compose
database running; otherwise the tests that use them are skipped with the reason. The
schema is created by running the Alembic migrations, so the migrations are tested too.
"""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.constants import DEFAULT_EMAIL_SECTIONS, DEFAULT_TELEGRAM_SECTIONS
from app.infrastructure.database.session import create_engine

ROOT = Path(__file__).resolve().parents[2]


class _TestDatabaseSettings(BaseSettings):
    """Reads only `TEST_DATABASE_URL`, never the application's own database settings."""

    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_ignore_empty=True, extra="ignore")

    test_database_url: str | None = None


@pytest.fixture(scope="session")
def database_url() -> str:
    """URL of a reachable test database with every migration applied."""
    url = _TestDatabaseSettings().test_database_url
    if url is None:
        pytest.skip("TEST_DATABASE_URL is not set (see README: PostgreSQL)")
    try:
        asyncio.run(_ping(url))
    except OSError as error:
        pytest.skip(f"Test database unreachable ({error}); run `docker compose up -d`")
    config = Config(ROOT / "alembic.ini")
    config.attributes["database_url"] = url
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    return url


async def _ping(url: str) -> None:
    """Open and close one connection, raising `OSError` if the server is down."""
    engine = create_engine(url)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    finally:
        await engine.dispose()


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    """Engine on the test database, reset before and after each test."""
    engine = create_engine(database_url)
    await _reset(engine)
    yield engine
    await _reset(engine)
    await engine.dispose()


async def _reset(engine: AsyncEngine) -> None:
    """Empty every data table and put the report settings back as the migration seeds them."""
    async with engine.begin() as connection:
        await connection.execute(
            text("TRUNCATE profiles, traces, sync_runs, deliveries, report_settings CASCADE")
        )
        await connection.execute(
            text(
                "INSERT INTO report_settings (channel, enabled, sections) "
                "VALUES ('email', true, :email), ('telegram', true, :telegram)"
            ),
            {
                "email": list(DEFAULT_EMAIL_SECTIONS),
                "telegram": list(DEFAULT_TELEGRAM_SECTIONS),
            },
        )
