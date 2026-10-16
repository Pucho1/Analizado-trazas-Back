import base64
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
import pytest

from app.core.config import Settings
from app.core.constants import LANGFUSE_FETCH_MARGIN, LANGFUSE_OBSERVATIONS_PATH
from app.core.exceptions import TraceSourceAuthenticationError, TraceSourceError
from app.infrastructure.langfuse.client import LangfuseTraceSource, create_langfuse_http_client
from tests.unit.langfuse_samples import TRACE_ID, generation_json, root_json

pytestmark = pytest.mark.anyio

END = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
START = END - timedelta(days=1)
BASE_URL = "https://langfuse.test"

Handler = Callable[[httpx2.Request], httpx2.Response]


def page(data: list[dict[str, Any]], cursor: str | None = None) -> httpx2.Response:
    return httpx2.Response(200, json={"data": data, "meta": {"cursor": cursor}})


def make_source(handler: Handler) -> LangfuseTraceSource:
    client = httpx2.AsyncClient(base_url=BASE_URL, transport=httpx2.MockTransport(handler))
    return LangfuseTraceSource(client)


async def test_requests_v2_observations_with_margin_before_window() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return page([])

    await make_source(handler).list_traces(START, END)

    (request,) = requests
    assert request.url.path == LANGFUSE_OBSERVATIONS_PATH
    assert request.url.params["fromStartTime"] == "2026-10-08T11:00:00Z"
    assert request.url.params["toStartTime"] == "2026-10-09T12:00:00Z"
    assert set(request.url.params["fields"].split(",")) >= {"core", "basic", "usage"}
    assert "cursor" not in request.url.params


async def test_follows_cursor_across_pages() -> None:
    cursors: list[str | None] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        cursor = request.url.params.get("cursor")
        cursors.append(cursor)
        if cursor is None:
            return page([root_json()], cursor="next-page")
        return page([generation_json()])

    (trace,) = await make_source(handler).list_traces(START, END)

    assert cursors == [None, "next-page"]
    assert trace.id == TRACE_ID
    assert trace.total_tokens == 839


async def test_keeps_only_traces_that_start_inside_window() -> None:
    before_window = (START - LANGFUSE_FETCH_MARGIN / 2).isoformat()
    early = root_json(id="early", traceId="early-trace", startTime=before_window, endTime=None)

    def handler(request: httpx2.Request) -> httpx2.Response:
        return page([root_json(), early])

    traces = await make_source(handler).list_traces(START, END)

    assert [trace.id for trace in traces] == [TRACE_ID]


@pytest.mark.parametrize("status", [401, 403])
async def test_rejected_credentials_raise_authentication_error(status: int) -> None:
    source = make_source(lambda request: httpx2.Response(status, json={"message": "nope"}))

    with pytest.raises(TraceSourceAuthenticationError):
        await source.list_traces(START, END)


@pytest.mark.parametrize("status", [400, 429, 500, 503])
async def test_other_http_errors_raise_source_error(status: int) -> None:
    source = make_source(lambda request: httpx2.Response(status))

    with pytest.raises(TraceSourceError) as raised:
        await source.list_traces(START, END)
    assert not isinstance(raised.value, TraceSourceAuthenticationError)


async def test_network_failure_raises_source_error() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectTimeout("timed out", request=request)

    source = make_source(handler)

    with pytest.raises(TraceSourceError):
        await source.list_traces(START, END)


async def test_unexpected_payload_raises_source_error() -> None:
    source = make_source(lambda request: httpx2.Response(200, json={"items": []}))

    with pytest.raises(TraceSourceError):
        await source.list_traces(START, END)


async def test_inconsistent_trace_data_raises_source_error() -> None:
    source = make_source(lambda request: page([root_json(), generation_json(totalCost=-1)]))

    with pytest.raises(TraceSourceError):
        await source.list_traces(START, END)


async def test_http_client_uses_configured_host_and_basic_auth() -> None:
    settings = Settings.model_validate(
        {
            "langfuse_public_key": "pk-test",
            "langfuse_secret_key": "sk-test",
            "langfuse_host": BASE_URL,
        }
    )
    captured: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured.append(request)
        return page([])

    transport = httpx2.MockTransport(handler)
    async with create_langfuse_http_client(settings, transport=transport) as client:
        await LangfuseTraceSource(client).list_traces(START, END)

    (request,) = captured
    assert str(request.url).startswith(f"{BASE_URL}{LANGFUSE_OBSERVATIONS_PATH}")
    expected = base64.b64encode(b"pk-test:sk-test").decode()
    assert request.headers["Authorization"] == f"Basic {expected}"
