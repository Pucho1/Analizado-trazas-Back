from collections.abc import Sequence
from datetime import UTC, datetime

import httpx2
from pydantic import ValidationError

from app.core.config import Settings
from app.core.constants import (
    LANGFUSE_FETCH_MARGIN,
    LANGFUSE_OBSERVATION_FIELDS,
    LANGFUSE_OBSERVATIONS_PATH,
    LANGFUSE_PAGE_SIZE,
    LANGFUSE_REQUEST_TIMEOUT_SECONDS,
)
from app.core.exceptions import TraceSourceAuthenticationError, TraceSourceError
from app.domain.models import Trace
from app.infrastructure.langfuse.mapper import (
    LangfuseObservation,
    LangfuseObservationsPage,
    map_traces,
)

_AUTH_ERROR_STATUSES = frozenset({401, 403})


def create_langfuse_http_client(
    settings: Settings, transport: httpx2.AsyncBaseTransport | None = None
) -> httpx2.AsyncClient:
    """HTTP client pointed at the configured Langfuse host, authenticated with Basic Auth."""
    return httpx2.AsyncClient(
        base_url=str(settings.langfuse_host),
        auth=httpx2.BasicAuth(
            settings.langfuse_public_key, settings.langfuse_secret_key.get_secret_value()
        ),
        timeout=LANGFUSE_REQUEST_TIMEOUT_SECONDS,
        transport=transport,
    )


class LangfuseTraceSource:
    """`TraceSource` backed by the Langfuse public API (v2 observations)."""

    def __init__(self, http_client: httpx2.AsyncClient) -> None:
        """Use an HTTP client already pointed at Langfuse and authenticated."""
        self._http_client = http_client

    async def list_traces(self, start: datetime, end: datetime) -> Sequence[Trace]:
        """Return the traces that started within [start, end] (see `TraceSource`)."""
        observations = await self._fetch_observations(start - LANGFUSE_FETCH_MARGIN, end)
        try:
            traces = map_traces(observations)
        except ValueError as error:
            raise TraceSourceError("Langfuse returned inconsistent trace data") from error
        return [trace for trace in traces if start <= trace.timestamp <= end]

    async def _fetch_observations(
        self, start: datetime, end: datetime
    ) -> list[LangfuseObservation]:
        """Fetch every observation that started in [start, end], following the cursor."""
        observations: list[LangfuseObservation] = []
        cursor: str | None = None
        while True:
            page = await self._fetch_page(start, end, cursor)
            observations.extend(page.data)
            cursor = page.meta.cursor
            if not cursor:
                return observations

    async def _fetch_page(
        self, start: datetime, end: datetime, cursor: str | None
    ) -> LangfuseObservationsPage:
        """Fetch one page of observations, translating HTTP failures into domain errors."""
        params = {
            "fields": LANGFUSE_OBSERVATION_FIELDS,
            "fromStartTime": _to_iso(start),
            "toStartTime": _to_iso(end),
            "limit": str(LANGFUSE_PAGE_SIZE),
        }
        if cursor:
            params["cursor"] = cursor
        try:
            response = await self._http_client.get(LANGFUSE_OBSERVATIONS_PATH, params=params)
        except httpx2.HTTPError as error:
            raise TraceSourceError("Could not reach Langfuse") from error
        if response.status_code in _AUTH_ERROR_STATUSES:
            raise TraceSourceAuthenticationError("Langfuse rejected the configured credentials")
        if response.is_error:
            raise TraceSourceError(f"Langfuse responded with HTTP {response.status_code}")
        try:
            return LangfuseObservationsPage.model_validate_json(response.content)
        except ValidationError as error:
            raise TraceSourceError("Langfuse returned an unexpected response") from error


def _to_iso(moment: datetime) -> str:
    """Format a datetime as ISO 8601 in UTC with a `Z` suffix, as Langfuse expects."""
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")
