"""Connection to PostgreSQL: URL, async engine and session factory."""

from sqlalchemy import URL
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings
from app.core.constants import DATABASE_DRIVER


def database_url(settings: Settings) -> URL:
    """Build the connection URL from the `POSTGRES_*` settings (password escaped safely)."""
    return URL.create(
        DATABASE_DRIVER,
        username=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
    )


def create_engine(url: URL | str) -> AsyncEngine:
    """Async engine with a connection pool that discards dead connections before use."""
    return create_async_engine(url, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Session factory whose objects stay readable after commit."""
    return async_sessionmaker(engine, expire_on_commit=False)
