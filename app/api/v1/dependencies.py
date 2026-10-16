"""Dependency providers for the v1 routes (composition root of the API layer).

Routes only depend on `get_trace_service`; tests replace `get_trace_source` through
`app.dependency_overrides` to run without Langfuse.
"""

from functools import lru_cache
from typing import Annotated

import httpx2
from fastapi import Depends, Request

from app.application.trace_service import TraceService
from app.core.config import Settings
from app.domain.ports import TraceSource
from app.infrastructure.langfuse.client import LangfuseTraceSource


@lru_cache
def get_settings() -> Settings:
    """Read and validate the configuration once; later calls reuse the same instance."""
    return Settings()


def get_langfuse_http_client(request: Request) -> httpx2.AsyncClient:
    """Return the shared Langfuse HTTP client opened by the application lifespan."""
    client: httpx2.AsyncClient = request.app.state.langfuse_http_client
    return client


def get_trace_source(
    http_client: Annotated[httpx2.AsyncClient, Depends(get_langfuse_http_client)],
) -> TraceSource:
    """Return the trace source used by the application (Langfuse)."""
    return LangfuseTraceSource(http_client)


def get_trace_service(
    source: Annotated[TraceSource, Depends(get_trace_source)],
) -> TraceService:
    """Return the trace use cases wired to the configured trace source."""
    return TraceService(source)
