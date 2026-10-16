from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.dependencies import get_settings
from app.api.v1.errors import register_exception_handlers
from app.api.v1.routes import traces
from app.core.config import Settings
from app.core.constants import API_V1_PREFIX, CORS_ALLOWED_METHODS
from app.infrastructure.langfuse.client import create_langfuse_http_client


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the shared Langfuse HTTP client at startup and close it at shutdown."""
    settings: Settings = app.state.settings
    async with create_langfuse_http_client(settings) as http_client:
        app.state.langfuse_http_client = http_client
        yield


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
    register_exception_handlers(app)
    return app


app = create_app()
