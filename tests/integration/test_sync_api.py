from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.dependencies import get_sync_service
from app.application.sync_service import SyncService
from app.core.exceptions import (
    RepositoryError,
    SyncAlreadyRunningError,
    TraceSourceAuthenticationError,
    TraceSourceError,
)
from app.domain.models import SyncRun, SyncTrigger
from app.domain.ports import SyncRunRepository, TraceRepository, TraceSource
from app.main import create_app
from tests.unit.fakes import (
    FailingTraces,
    InMemorySyncRunRepository,
    InMemoryTraceRepository,
    InMemoryTraceSource,
    make_trace,
)

NOW = datetime(2026, 10, 17, 14, 0, tzinfo=UTC)
PATH = "/api/v1/sync"


@pytest.fixture
def app() -> Iterator[FastAPI]:
    application = create_app()
    yield application
    application.dependency_overrides.clear()


def use_sync_service(
    app: FastAPI,
    *,
    source: TraceSource | None = None,
    traces: TraceRepository | None = None,
    runs: SyncRunRepository | None = None,
) -> TestClient:
    """Wire a sync service with in-memory collaborators and return a client."""
    service = SyncService(
        source_name="langfuse",
        source=source or InMemoryTraceSource(),
        traces=traces or InMemoryTraceRepository(),
        runs=runs or InMemorySyncRunRepository(),
        overlap=timedelta(hours=1),
        initial_lookback=timedelta(days=30),
        clock=lambda: NOW,
    )
    app.dependency_overrides[get_sync_service] = lambda: service
    return TestClient(app)


def test_sync_now_stores_traces_and_returns_the_run(app: FastAPI) -> None:
    trace = make_trace(timestamp=NOW - timedelta(hours=2))
    traces = InMemoryTraceRepository()
    client = use_sync_service(app, source=InMemoryTraceSource([trace]), traces=traces)

    response = client.post(PATH)

    assert response.status_code == 200
    assert response.json() == {
        "trigger": "manual",
        "status": "succeeded",
        "started_at": "2026-10-17T14:00:00Z",
        "finished_at": "2026-10-17T14:00:00Z",
        "window_start": "2026-09-17T14:00:00Z",
        "window_end": "2026-10-17T14:00:00Z",
        "trace_count": 1,
        "error_code": None,
    }
    assert traces.traces == [trace]


@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (TraceSourceAuthenticationError("pk-lf-123"), 502, "trace_source_auth_failed"),
        (TraceSourceError("HTTP 500"), 502, "trace_source_unavailable"),
    ],
)
def test_sync_now_reports_source_failures_and_records_them(
    app: FastAPI, error: Exception, status_code: int, code: str
) -> None:
    runs = InMemorySyncRunRepository()
    client = use_sync_service(app, source=FailingTraces(error), runs=runs)

    response = client.post(PATH)

    assert response.status_code == status_code
    assert response.json()["error"]["code"] == code
    assert str(error) not in response.text
    state = client.get(PATH).json()
    assert state["last_run"]["status"] == "failed"
    assert state["last_run"]["error_code"] == code
    assert state["last_successful_run"] is None


def test_sync_now_reports_database_failures(app: FastAPI) -> None:
    client = use_sync_service(
        app,
        source=InMemoryTraceSource([make_trace(timestamp=NOW)]),
        traces=FailingTraces(RepositoryError("refused")),
    )

    response = client.post(PATH)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_unavailable"


def test_sync_now_while_running_is_a_conflict(app: FastAPI) -> None:
    class RunningService:
        async def sync(self, trigger: SyncTrigger) -> SyncRun:
            """Always busy."""
            raise SyncAlreadyRunningError("busy")

    app.dependency_overrides[get_sync_service] = RunningService

    response = TestClient(app).post(PATH)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "sync_already_running"


def test_state_when_never_synced(app: FastAPI) -> None:
    client = use_sync_service(app)

    response = client.get(PATH)

    assert response.status_code == 200
    assert response.json() == {"running": False, "last_run": None, "last_successful_run": None}


def test_state_after_a_successful_sync(app: FastAPI) -> None:
    client = use_sync_service(app)
    run = client.post(PATH).json()

    response = client.get(PATH)

    assert response.json() == {"running": False, "last_run": run, "last_successful_run": run}


def test_state_reports_database_failures(app: FastAPI) -> None:
    class DownRuns(InMemorySyncRunRepository):
        async def latest(self, source: str) -> SyncRun | None:
            """The database is down."""
            raise RepositoryError("refused")

    client = use_sync_service(app, runs=DownRuns())

    response = client.get(PATH)

    assert response.status_code == 503


def test_openapi_documents_sync_endpoints(app: FastAPI) -> None:
    paths = TestClient(app).get("/openapi.json").json()["paths"]

    assert set(paths[PATH]["post"]["responses"]) == {"200", "409", "502", "503"}
    assert set(paths[PATH]["get"]["responses"]) == {"200", "503"}
