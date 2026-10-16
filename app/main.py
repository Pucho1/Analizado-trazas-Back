from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1.dependencies import get_settings
from app.api.v1.errors import register_exception_handlers
from app.api.v1.routes import traces
from app.core.constants import API_V1_PREFIX
from app.infrastructure.langfuse.client import create_langfuse_http_client


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the shared Langfuse HTTP client at startup and close it at shutdown.

    Settings are validated here, so a missing or invalid `.env` stops the server early.
    """
    async with create_langfuse_http_client(get_settings()) as http_client:
        app.state.langfuse_http_client = http_client
        yield


def create_app() -> FastAPI:
    """Build the FastAPI application with its routes and error handlers."""
    app = FastAPI(title="Observa API", version="0.1.0", lifespan=lifespan)
    app.include_router(traces.router, prefix=API_V1_PREFIX)
    register_exception_handlers(app)
    return app


app = create_app()
