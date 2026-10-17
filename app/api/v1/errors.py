"""Translation of domain errors into HTTP responses with an `ErrorResponse` body."""

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.api.v1.schemas import ErrorCode, ErrorDetail, ErrorResponse
from app.core.exceptions import (
    ChannelNotConfiguredError,
    InvalidThresholdsError,
    NotificationError,
    ProfileNameTakenError,
    ProfileNotFoundError,
    ReportAlreadySentError,
    RepositoryError,
    SyncAlreadyRunningError,
    TraceSourceAuthenticationError,
    TraceSourceError,
    UnknownReportSectionError,
)
from app.domain.notifications import NotificationFailure

logger = logging.getLogger(__name__)

# OpenAPI documentation of the errors each group of routes can return.
DATABASE_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_503_SERVICE_UNAVAILABLE: {
        "model": ErrorResponse,
        "description": "`database_unavailable`: Observa's database cannot be reached.",
    },
}
SYNC_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    **DATABASE_ERROR_RESPONSES,
    status.HTTP_409_CONFLICT: {
        "model": ErrorResponse,
        "description": "`sync_already_running`: another synchronisation is in progress.",
    },
    status.HTTP_502_BAD_GATEWAY: {
        "model": ErrorResponse,
        "description": (
            "Langfuse failed. `trace_source_auth_failed` means the configured "
            "credentials were rejected; `trace_source_unavailable` covers any other failure. "
            "The failed run is still recorded (see `GET /sync`)."
        ),
    },
}


PROFILE_NOT_FOUND_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {
        "model": ErrorResponse,
        "description": "`profile_not_found`: no profile has that id.",
    },
}
PROFILE_NAME_TAKEN_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_409_CONFLICT: {
        "model": ErrorResponse,
        "description": "`profile_name_taken`: another profile has that name (ignoring case).",
    },
}


INVALID_THRESHOLDS_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_400_BAD_REQUEST: {
        "model": ErrorResponse,
        "description": "`invalid_thresholds`: the result would be inconsistent "
        "(e.g. warning_factor not below critical_factor).",
    },
}


UNKNOWN_SECTION_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_400_BAD_REQUEST: {
        "model": ErrorResponse,
        "description": "`unknown_report_section`: a section key is not registered "
        "(see GET /reports/sections).",
    },
}


NOTIFICATION_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_409_CONFLICT: {
        "model": ErrorResponse,
        "description": "`channel_not_configured`: the channel's variables are missing from "
        "`.env`; `report_already_sent`: that day's report already reached the channel.",
    },
    status.HTTP_502_BAD_GATEWAY: {
        "model": ErrorResponse,
        "description": "`notification_failed`: the provider refused or could not be reached; "
        "the message says why (credentials, recipient, network). Recorded as a delivery.",
    },
}

_NOTIFICATION_MESSAGES = {
    NotificationFailure.AUTH_FAILED: "The provider rejected the credentials in .env "
    "(SMTP password or Telegram bot token).",
    NotificationFailure.REJECTED: "The provider refused the message: check the recipient "
    "(EMAIL_TO, TELEGRAM_CHAT_ID) and that you wrote to the bot once.",
    NotificationFailure.UNAVAILABLE: "The provider could not be reached; try again later.",
    NotificationFailure.NOT_CONFIGURED: "The channel is not fully configured in .env.",
}


def register_exception_handlers(app: FastAPI) -> None:
    """Attach the handlers that map domain errors to HTTP responses.

    Starlette resolves handlers by the exception's MRO, so the authentication error
    gets its own handler even though it subclasses `TraceSourceError`.
    """
    app.add_exception_handler(TraceSourceAuthenticationError, _handle_auth_error)
    app.add_exception_handler(TraceSourceError, _handle_source_error)
    app.add_exception_handler(RepositoryError, _handle_repository_error)
    app.add_exception_handler(SyncAlreadyRunningError, _handle_sync_already_running)
    app.add_exception_handler(ProfileNotFoundError, _handle_profile_not_found)
    app.add_exception_handler(ProfileNameTakenError, _handle_profile_name_taken)
    app.add_exception_handler(InvalidThresholdsError, _handle_invalid_thresholds)
    app.add_exception_handler(UnknownReportSectionError, _handle_unknown_section)
    app.add_exception_handler(ChannelNotConfiguredError, _handle_channel_not_configured)
    app.add_exception_handler(ReportAlreadySentError, _handle_report_already_sent)
    app.add_exception_handler(NotificationError, _handle_notification_failed)


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


async def _handle_repository_error(request: Request, exc: Exception) -> JSONResponse:
    """Respond 503 when the database cannot be used."""
    logger.error("Database unavailable: %s", exc, exc_info=exc)
    return _error_response(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        ErrorCode.DATABASE_UNAVAILABLE,
        "The database is unavailable.",
    )


async def _handle_sync_already_running(request: Request, exc: Exception) -> JSONResponse:
    """Respond 409 when a sync is requested while another one runs."""
    return _error_response(
        status.HTTP_409_CONFLICT,
        ErrorCode.SYNC_ALREADY_RUNNING,
        "A synchronisation is already running.",
    )


async def _handle_profile_not_found(request: Request, exc: Exception) -> JSONResponse:
    """Respond 404 when the requested profile does not exist."""
    return _error_response(
        status.HTTP_404_NOT_FOUND, ErrorCode.PROFILE_NOT_FOUND, "The profile does not exist."
    )


async def _handle_profile_name_taken(request: Request, exc: Exception) -> JSONResponse:
    """Respond 409 when another profile already has the requested name."""
    return _error_response(
        status.HTTP_409_CONFLICT,
        ErrorCode.PROFILE_NAME_TAKEN,
        "Another profile already has that name.",
    )


async def _handle_invalid_thresholds(request: Request, exc: Exception) -> JSONResponse:
    """Respond 400 when the requested thresholds would be inconsistent."""
    return _error_response(
        status.HTTP_400_BAD_REQUEST,
        ErrorCode.INVALID_THRESHOLDS,
        "The thresholds are inconsistent: 0 < warning_factor < critical_factor.",
    )


async def _handle_unknown_section(request: Request, exc: Exception) -> JSONResponse:
    """Respond 400 when report settings name an unregistered section."""
    return _error_response(
        status.HTTP_400_BAD_REQUEST,
        ErrorCode.UNKNOWN_REPORT_SECTION,
        "Unknown report section; see GET /api/v1/reports/sections.",
    )


async def _handle_channel_not_configured(request: Request, exc: Exception) -> JSONResponse:
    """Respond 409 when a channel lacks its `.env` variables."""
    return _error_response(
        status.HTTP_409_CONFLICT,
        ErrorCode.CHANNEL_NOT_CONFIGURED,
        "The channel is not configured: add its variables to .env and restart.",
    )


async def _handle_report_already_sent(request: Request, exc: Exception) -> JSONResponse:
    """Respond 409 when that day's report already reached the channel."""
    return _error_response(
        status.HTTP_409_CONFLICT,
        ErrorCode.REPORT_ALREADY_SENT,
        "That day's report already reached this channel.",
    )


async def _handle_notification_failed(request: Request, exc: Exception) -> JSONResponse:
    """Respond 502 when the provider refused or could not be reached."""
    reason = getattr(exc, "reason", NotificationFailure.UNAVAILABLE.value)
    message = _NOTIFICATION_MESSAGES.get(
        NotificationFailure(reason), _NOTIFICATION_MESSAGES[NotificationFailure.UNAVAILABLE]
    )
    return _error_response(status.HTTP_502_BAD_GATEWAY, ErrorCode.NOTIFICATION_FAILED, message)


def _error_response(status_code: int, code: ErrorCode, message: str) -> JSONResponse:
    """Build a JSON response whose body follows `ErrorResponse`.

    Messages are fixed so that internal details never reach the client.
    """
    body = ErrorResponse(error=ErrorDetail(code=code, message=message))
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))
