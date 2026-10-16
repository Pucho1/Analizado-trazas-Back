"""Translation of domain errors into HTTP responses with an `ErrorResponse` body."""

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.api.v1.schemas import ErrorCode, ErrorDetail, ErrorResponse
from app.core.exceptions import TraceSourceAuthenticationError, TraceSourceError

logger = logging.getLogger(__name__)

# Documented in OpenAPI on every route that reads from the trace source.
TRACE_SOURCE_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_502_BAD_GATEWAY: {
        "model": ErrorResponse,
        "description": (
            "The trace source failed. `trace_source_auth_failed` means the configured "
            "credentials were rejected; `trace_source_unavailable` covers any other failure."
        ),
    },
}


def register_exception_handlers(app: FastAPI) -> None:
    """Attach the handlers that map domain errors to HTTP responses.

    Starlette resolves handlers by the exception's MRO, so the authentication error
    gets its own handler even though it subclasses `TraceSourceError`.
    """
    app.add_exception_handler(TraceSourceAuthenticationError, _handle_auth_error)
    app.add_exception_handler(TraceSourceError, _handle_source_error)


async def _handle_auth_error(request: Request, exc: Exception) -> JSONResponse:
    """Respond 502 when the trace source rejects the configured credentials."""
    logger.warning("Trace source rejected the credentials: %s", exc)
    return _error_response(
        status.HTTP_502_BAD_GATEWAY,
        ErrorCode.TRACE_SOURCE_AUTH_FAILED,
        "The trace source rejected the configured credentials.",
    )


async def _handle_source_error(request: Request, exc: Exception) -> JSONResponse:
    """Respond 502 for any other trace source failure."""
    logger.warning("Trace source failed: %s", exc, exc_info=exc)
    return _error_response(
        status.HTTP_502_BAD_GATEWAY,
        ErrorCode.TRACE_SOURCE_UNAVAILABLE,
        "The trace source is unavailable or returned invalid data.",
    )


def _error_response(status_code: int, code: ErrorCode, message: str) -> JSONResponse:
    """Build a JSON response whose body follows `ErrorResponse`.

    Messages are fixed so that internal details never reach the client.
    """
    body = ErrorResponse(error=ErrorDetail(code=code, message=message))
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))
