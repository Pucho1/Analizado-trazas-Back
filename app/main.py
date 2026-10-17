from collections.abc import AsyncIterator, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import timedelta
from zoneinfo import ZoneInfo

import httpx2
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1.dependencies import get_settings
from app.api.v1.errors import register_exception_handlers
from app.api.v1.routes import alerts, profiles, reports, spend, sync, traces
from app.application.event_service import EventService
from app.application.monitoring_service import MonitoringService
from app.application.profile_service import ProfileService
from app.application.reports.data import ReportDataBuilder
from app.application.reports.presenters import EmailPresenter, TelegramPresenter
from app.application.reports.registry import default_registry
from app.application.reports.service import ReportService
from app.application.spend_service import SpendService
from app.application.sync_service import AfterSyncHook, SyncService
from app.core.config import Settings
from app.core.constants import (
    API_V1_PREFIX,
    CORS_ALLOWED_METHODS,
    SYNC_OVERLAP,
    SYNC_SOURCE_LANGFUSE,
)
from app.domain.reporting import Channel
from app.infrastructure.database.monitoring_repository import SqlAlchemyMonitoringRepository
from app.infrastructure.database.profile_repository import SqlAlchemyProfileRepository
from app.infrastructure.database.reporting_repository import (
    SqlAlchemyEventMappingRepository,
    SqlAlchemyReportSettingsRepository,
    SqlAlchemySpendRepository,
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

    Opens the database engine and the Langfuse HTTP client, builds the repositories, the
    profile and monitoring services and the single sync service, and starts the scan
    scheduler when sync is enabled.
    """
    settings: Settings = app.state.settings
    async with AsyncExitStack() as stack:
        engine = create_engine(database_url(settings))
        stack.push_async_callback(engine.dispose)
        session_factory = create_session_factory(engine)
        http_client = await stack.enter_async_context(create_langfuse_http_client(settings))

        timezone = ZoneInfo(settings.monitoring_timezone)
        profiles = SqlAlchemyProfileRepository(session_factory)
        monitoring = SqlAlchemyMonitoringRepository(session_factory)
        app.state.trace_repository = SqlAlchemyTraceRepository(session_factory)
        app.state.profile_service = ProfileService(profiles)
        app.state.monitoring_service = MonitoringService(profiles, monitoring, timezone)
        _build_reporting(app, session_factory, profiles, monitoring, timezone)
        # After each sync: first classify the new traces, then judge them.
        app.state.sync_service = _build_sync_service(
            settings,
            session_factory,
            http_client,
            after_sync=[
                app.state.profile_service.reclassify_after_sync,
                app.state.monitoring_service.after_sync,
            ],
        )

        if settings.sync_enabled:
            scheduler = create_sync_scheduler(
                app.state.sync_service,
                settings.sync_schedule,
                ZoneInfo(settings.sync_timezone),
            )
            scheduler.start()
            stack.callback(scheduler.shutdown, wait=False)
        yield


def _build_reporting(
    app: FastAPI,
    session_factory: async_sessionmaker[AsyncSession],
    profiles: SqlAlchemyProfileRepository,
    monitoring: SqlAlchemyMonitoringRepository,
    timezone: ZoneInfo,
) -> None:
    """Spend, event mapping and report services, stored in `app.state`."""
    spend = SqlAlchemySpendRepository(session_factory)
    mappings = SqlAlchemyEventMappingRepository(session_factory)
    spend_service = SpendService(spend, profiles, timezone)
    app.state.spend_service = spend_service
    app.state.event_service = EventService(profiles, mappings, spend)
    app.state.report_service = ReportService(
        registry=default_registry(),
        settings=SqlAlchemyReportSettingsRepository(session_factory),
        data=ReportDataBuilder(
            profiles=profiles,
            spend=spend,
            monitoring=monitoring,
            mappings=mappings,
            spend_service=spend_service,
            timezone=timezone,
        ),
        presenters={Channel.EMAIL: EmailPresenter(), Channel.TELEGRAM: TelegramPresenter()},
        spend=spend_service,
    )


def _build_sync_service(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    http_client: httpx2.AsyncClient,
    *,
    after_sync: Sequence[AfterSyncHook],
) -> SyncService:
    """Sync service reading from Langfuse and writing to PostgreSQL."""
    return SyncService(
        source_name=SYNC_SOURCE_LANGFUSE,
        source=LangfuseTraceSource(http_client),
        traces=SqlAlchemyTraceRepository(session_factory),
        runs=SqlAlchemySyncRunRepository(session_factory),
        overlap=SYNC_OVERLAP,
        initial_lookback=timedelta(days=settings.sync_initial_lookback_days),
        after_sync=after_sync,
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
    app.include_router(profiles.router, prefix=API_V1_PREFIX)
    app.include_router(alerts.router, prefix=API_V1_PREFIX)
    app.include_router(spend.router, prefix=API_V1_PREFIX)
    app.include_router(reports.router, prefix=API_V1_PREFIX)
    register_exception_handlers(app)
    return app


app = create_app()
