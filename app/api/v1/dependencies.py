"""Dependency providers for the v1 routes.

The application lifespan builds the shared objects (repositories, sync service) and
keeps them in `app.state`; these providers hand them to the routes. Tests replace
`get_trace_service` and `get_sync_service` through `app.dependency_overrides`.
"""

from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Request

from app.application.event_service import EventService
from app.application.monitoring_service import MonitoringService
from app.application.notification_service import NotificationService
from app.application.profile_service import ProfileService
from app.application.reports.service import ReportService
from app.application.spend_service import SpendService
from app.application.sync_service import SyncService
from app.application.trace_service import TraceService
from app.core.config import Settings
from app.domain.ports import TraceRepository


@lru_cache
def get_settings() -> Settings:
    """Read and validate the configuration once; later calls reuse the same instance."""
    return Settings()


def get_trace_repository(request: Request) -> TraceRepository:
    """Return the trace repository created by the application lifespan."""
    repository: TraceRepository = request.app.state.trace_repository
    return repository


def get_trace_service(
    repository: Annotated[TraceRepository, Depends(get_trace_repository)],
) -> TraceService:
    """Return the trace use cases, reading from the repository."""
    return TraceService(repository)


def get_profile_service(request: Request) -> ProfileService:
    """Return the profile use cases created by the application lifespan."""
    service: ProfileService = request.app.state.profile_service
    return service


def get_monitoring_service(request: Request) -> MonitoringService:
    """Return the baseline and alert use cases created by the application lifespan."""
    service: MonitoringService = request.app.state.monitoring_service
    return service


def get_spend_service(request: Request) -> SpendService:
    """Return the spend use cases created by the application lifespan."""
    service: SpendService = request.app.state.spend_service
    return service


def get_event_service(request: Request) -> EventService:
    """Return the event mapping use cases created by the application lifespan."""
    service: EventService = request.app.state.event_service
    return service


def get_report_service(request: Request) -> ReportService:
    """Return the report use cases created by the application lifespan."""
    service: ReportService = request.app.state.report_service
    return service


def get_notification_service(request: Request) -> NotificationService:
    """Return the notification use cases created by the application lifespan."""
    service: NotificationService = request.app.state.notification_service
    return service


def get_sync_service(request: Request) -> SyncService:
    """Return the single sync service (shared, so that only one sync runs at a time)."""
    service: SyncService = request.app.state.sync_service
    return service
