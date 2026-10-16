from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.dependencies import get_trace_service
from app.application.trace_service import TraceService
from app.core.exceptions import TraceSourceAuthenticationError, TraceSourceError
from app.domain.models import TimeRange
from app.domain.ports import TraceSource
from app.main import create_app
from tests.unit.fakes import FailingTraceSource, InMemoryTraceSource, make_trace

NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)

OLDER = make_trace(
    id="older",
    timestamp=NOW - timedelta(hours=3),
    name="planner",
    latency=2.0,
    cost=Decimal("0.0125"),
    input_tokens=100,
    output_tokens=50,
)
NEWER = make_trace(
    id="newer",
    timestamp=NOW - timedelta(hours=1),
    name="search-tool",
    latency=None,
    cost=Decimal("0.0025"),
    input_tokens=20,
    output_tokens=10,
)


@pytest.fixture
def app() -> Iterator[FastAPI]:
    application = create_app()
    yield application
    application.dependency_overrides.clear()


def use_source(app: FastAPI, source: TraceSource) -> TestClient:
    """Wire `source` into the app with a fixed clock and return a client (no lifespan)."""
    app.dependency_overrides[get_trace_service] = lambda: TraceService(source, clock=lambda: NOW)
    return TestClient(app)


def test_list_traces_returns_serialized_traces_most_recent_first(app: FastAPI) -> None:
    client = use_source(app, InMemoryTraceSource([OLDER, NEWER]))

    response = client.get("/api/v1/traces", params={"range": "24h"})

    assert response.status_code == 200
    body = response.json()
    assert body["time_range"] == "24h"
    assert [trace["id"] for trace in body["traces"]] == ["newer", "older"]
    assert body["traces"][1] == {
        "id": "older",
        "timestamp": "2026-01-15T09:00:00Z",
        "name": "planner",
        "latency": 2.0,
        "cost": 0.0125,
        "input_tokens": 100,
        "output_tokens": 50,
        "total_tokens": 150,
    }
    assert body["traces"][0]["latency"] is None


def test_summary_returns_aggregated_metrics(app: FastAPI) -> None:
    client = use_source(app, InMemoryTraceSource([OLDER, NEWER]))

    response = client.get("/api/v1/traces/summary", params={"range": "7d"})

    assert response.status_code == 200
    assert response.json() == {
        "time_range": "7d",
        "trace_count": 2,
        "total_cost": 0.015,
        "total_tokens": 180,
        "average_latency": 2.0,
    }


def test_summary_of_empty_range(app: FastAPI) -> None:
    client = use_source(app, InMemoryTraceSource())

    response = client.get("/api/v1/traces/summary")

    assert response.status_code == 200
    assert response.json() == {
        "time_range": "24h",
        "trace_count": 0,
        "total_cost": 0.0,
        "total_tokens": 0,
        "average_latency": None,
    }


@pytest.mark.parametrize("path", ["/api/v1/traces", "/api/v1/traces/summary"])
def test_range_defaults_to_last_24_hours(app: FastAPI, path: str) -> None:
    source = InMemoryTraceSource()
    client = use_source(app, source)

    response = client.get(path)

    assert response.status_code == 200
    assert response.json()["time_range"] == "24h"
    assert source.calls == [(NOW - timedelta(hours=24), NOW)]


@pytest.mark.parametrize("time_range", list(TimeRange))
@pytest.mark.parametrize("path", ["/api/v1/traces", "/api/v1/traces/summary"])
def test_range_selects_window_ending_now(app: FastAPI, path: str, time_range: TimeRange) -> None:
    source = InMemoryTraceSource()
    client = use_source(app, source)

    client.get(path, params={"range": time_range.value})

    assert source.calls == [(NOW - time_range.duration, NOW)]


@pytest.mark.parametrize("path", ["/api/v1/traces", "/api/v1/traces/summary"])
def test_invalid_range_is_rejected(app: FastAPI, path: str) -> None:
    source = InMemoryTraceSource()
    client = use_source(app, source)

    response = client.get(path, params={"range": "1y"})

    assert response.status_code == 422
    assert source.calls == []


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (TraceSourceAuthenticationError("bad keys pk-lf-123"), "trace_source_auth_failed"),
        (TraceSourceError("Langfuse responded with HTTP 500"), "trace_source_unavailable"),
    ],
)
@pytest.mark.parametrize("path", ["/api/v1/traces", "/api/v1/traces/summary"])
def test_trace_source_errors_map_to_bad_gateway(
    app: FastAPI, path: str, error: Exception, code: str
) -> None:
    client = use_source(app, FailingTraceSource(error))

    response = client.get(path)

    assert response.status_code == 502
    detail = response.json()["error"]
    assert detail["code"] == code
    assert str(error) not in detail["message"]


def test_openapi_documents_trace_endpoints(app: FastAPI) -> None:
    schema = TestClient(app).get("/openapi.json").json()

    for path in ("/api/v1/traces", "/api/v1/traces/summary"):
        operation = schema["paths"][path]["get"]
        (parameter,) = operation["parameters"]
        assert parameter["name"] == "range"
        assert {"200", "422", "502"} <= set(operation["responses"])
