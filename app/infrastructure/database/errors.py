"""Translation of driver and SQLAlchemy errors into the domain's `RepositoryError`."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.exc import SQLAlchemyError

from app.core.exceptions import RepositoryError


@asynccontextmanager
async def database_errors_as_repository_errors() -> AsyncIterator[None]:
    """Re-raise any database failure inside the block as `RepositoryError`.

    `OSError` covers the connection being refused or dropped, which asyncpg raises
    without SQLAlchemy wrapping it.
    """
    try:
        yield
    except (SQLAlchemyError, OSError) as error:
        raise RepositoryError("The database is unavailable") from error
