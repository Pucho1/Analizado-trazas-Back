from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import timedelta
from zoneinfo import ZoneInfo

import httpx2
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1.dependencies import get_settings
from app.api.v1.errors import register_exception_handlers
from app.api.v1.routes import sync, traces
from app.application.sync_service import SyncService
from app.core.config import Settings
from app.core.constants import (
    API_V1_PREFIX,
    CORS_ALLOWED_METHODS,
    SYNC_OVERLAP,
    SYNC_SOURCE_LANGFUSE,
)
from app.infrastructure.database.session import (
    create_engine,
    create_session_factory,
    database_url,
)
from app.infrastructure.database.sync_run_repository import SqlAlchemySyncRunRepository
from app.infrastructure.database.trace_repository import SqlAlchemyTraceRepository
from app.infrastructure.langfuse.client import LangfuseTraceSource, create_langfuse_http_client
from app.infrastructure.scheduler import create_sync_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Wire the shared objects at startup and release them at shutdown.

    Opens the database engine and the Langfuse HTTP client, builds the repositories and
    the single sync service, and starts the scan scheduler when sync is enabled.
    """
    settings: Settings = app.state.settings
    async with AsyncExitStack() as stack:
        engine = create_engine(database_url(settings))
        stack.push_async_callback(engine.dispose)
        session_factory = create_session_factory(engine)
        http_client = await stack.enter_async_context(create_langfuse_http_client(settings))

        app.state.trace_repository = SqlAlchemyTraceRepository(session_factory)
        app.state.sync_service = _build_sync_service(settings, session_factory, http_client)

        if settings.sync_enabled:
            scheduler = create_sync_scheduler(
                app.state.sync_service,
                settings.sync_schedule,
                ZoneInfo(settings.sync_timezone),
            )
            scheduler.start()
            stack.callback(scheduler.shutdown, wait=False)
        yield


def _build_sync_service(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    http_client: httpx2.AsyncClient,
) -> SyncService:
    """Sync service reading from Langfuse and writing to PostgreSQL."""
    return SyncService(
        source_name=SYNC_SOURCE_LANGFUSE,
        source=LangfuseTraceSource(http_client),
        traces=SqlAlchemyTraceRepository(session_factory),
        runs=SqlAlchemySyncRunRepository(session_factory),
        overlap=SYNC_OVERLAP,
        initial_lookback=timedelta(days=settings.sync_initial_lookback_days),
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application with its middleware, routes and error handlers.

    `settings` defaults to the validated `.env`, so a missing or invalid configuration
    stops the server at startup; tests pass their own.
    """
    settings = settings or get_settings()
    app = FastAPI(title="Observa API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_methods=list(CORS_ALLOWED_METHODS),
        allow_credentials=False,
    )
    app.include_router(traces.router, prefix=API_V1_PREFIX)
    app.include_router(sync.router, prefix=API_V1_PREFIX)
    register_exception_handlers(app)
    return app


app = create_app()
