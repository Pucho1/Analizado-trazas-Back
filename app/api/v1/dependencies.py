"""Dependency providers for the v1 routes.

The application lifespan builds the shared objects (repositories, sync service) and
keeps them in `app.state`; these providers hand them to the routes. Tests replace
`get_trace_service` and `get_sync_service` through `app.dependency_overrides`.
"""

from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Request

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


def get_sync_service(request: Request) -> SyncService:
    """Return the single sync service (shared, so that only one sync runs at a time)."""
    service: SyncService = request.app.state.sync_service
    return service
