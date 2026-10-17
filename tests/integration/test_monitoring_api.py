from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.dependencies import get_monitoring_service, get_profile_service
from app.application.monitoring_service import MonitoringService
from app.application.profile_service import ProfileService
from app.domain.models import ProfileRule
from app.main import create_app
from tests.unit.fakes import (
    InMemoryMonitoringRepository,
    InMemoryProfileRepository,
    make_profile,
    make_trace,
)

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
PROFILES = "/api/v1/profiles"


@pytest.fixture
def repositories() -> tuple[InMemoryProfileRepository, InMemoryMonitoringRepository]:
    """Profile 1 owns 120 normal traces over the 10 previous days and a slow one today."""
    traces = [
        make_trace(
            id=f"h{day}-{i}",
            timestamp=NOW.replace(hour=10, minute=i) - timedelta(days=day),
            environment="production",
            name="agent",
            latency=1.0 + (i % 3) / 10,
            cost=Decimal("0.02"),
        )
        for day in range(1, 11)
        for i in range(12)
    ]
    traces.append(
        make_trace(
            id="slow",
            timestamp=NOW - timedelta(hours=1),
            environment="production",
            name="agent",
            latency=10.0,
        )
    )
    profiles = InMemoryProfileRepository(traces)
    profiles.profiles[1] = make_profile(
        id=1,
        name="Agente",
        rule=ProfileRule(environment="production", trace_name="agent"),
        created_at=NOW - timedelta(days=30),
    )
    profiles.profile_of.update(dict.fromkeys(profiles.traces, 1))
    return profiles, InMemoryMonitoringRepository(profiles)


@pytest.fixture
def services(
    repositories: tuple[InMemoryProfileRepository, InMemoryMonitoringRepository],
) -> tuple[ProfileService, MonitoringService]:
    """Services over the in-memory repositories with a fixed clock."""
    profiles, monitoring = repositories
    return (
        ProfileService(profiles, clock=lambda: NOW),
        MonitoringService(profiles, monitoring, ZoneInfo("Europe/Madrid"), clock=lambda: NOW),
    )


@pytest.fixture
def client(services: tuple[ProfileService, MonitoringService]) -> Iterator[TestClient]:
    """Client wired to the services."""
    app: FastAPI = create_app()
    profile_service, monitoring_service = services
    app.dependency_overrides[get_profile_service] = lambda: profile_service
    app.dependency_overrides[get_monitoring_service] = lambda: monitoring_service
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_baseline_of_a_ready_profile(client: TestClient) -> None:
    body = client.get(f"{PROFILES}/1/baseline").json()

    assert body["status"] == "ready"
    assert (body["trace_count"], body["learning_target"]) == (121, 100)
    assert body["daily_monitoring"] is True
    assert body["day_sample_size"] == 10
    latency = next(m for m in body["trace_metrics"] if m["metric"] == "latency")
    assert latency["median"] == 1.1
    assert {m["metric"] for m in body["day_metrics"]} == {"query_count", "cost"}


def test_relearn_resets_the_baseline(client: TestClient) -> None:
    profile = client.post(f"{PROFILES}/1/relearn").json()
    body = client.get(f"{PROFILES}/1/baseline").json()

    assert profile["baseline_since"] == "2026-10-17T12:00:00Z"
    assert (body["status"], body["trace_count"], body["trace_metrics"]) == ("learning", 0, [])
    assert body["baseline_since"] == "2026-10-17T12:00:00Z"


def test_patch_thresholds(client: TestClient) -> None:
    response = client.patch(
        f"{PROFILES}/1/thresholds", json={"daily_budget": 1.5, "silence_hours": 6}
    )
    cleared = client.patch(f"{PROFILES}/1/thresholds", json={"daily_budget": None})

    assert response.status_code == 200
    assert response.json()["thresholds"]["daily_budget"] == 1.5
    assert response.json()["thresholds"]["silence_hours"] == 6
    assert cleared.json()["thresholds"]["daily_budget"] is None
    assert cleared.json()["thresholds"]["silence_hours"] == 6


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"warning_factor": None},
        {"warning_factor": 0},
        {"daily_budget": -1},
        {"min_trace_cost": -0.01},
        {"silence_hours": 0},
    ],
)
def test_invalid_threshold_bodies_are_rejected(client: TestClient, body: dict[str, object]) -> None:
    assert client.patch(f"{PROFILES}/1/thresholds", json=body).status_code == 422


def test_inconsistent_thresholds_are_a_bad_request(client: TestClient) -> None:
    response = client.patch(f"{PROFILES}/1/thresholds", json={"warning_factor": 2.5})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_thresholds"


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/99/baseline", None),
        ("POST", "/99/relearn", None),
        ("PATCH", "/99/thresholds", {"silence_hours": 2}),
    ],
)
def test_unknown_profile(
    client: TestClient, method: str, path: str, body: dict[str, int] | None
) -> None:
    response = client.request(method, f"{PROFILES}{path}", json=body)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "profile_not_found"


@pytest.mark.anyio
async def test_alerts_after_an_evaluation(
    client: TestClient, services: tuple[ProfileService, MonitoringService]
) -> None:
    await services[1].evaluate(NOW - timedelta(hours=6), NOW)

    body = client.get("/api/v1/alerts").json()

    (alert,) = body["alerts"]
    assert alert == {
        "id": 1,
        "profile_id": 1,
        "kind": "anomaly",
        "scope": "trace",
        "metric": "latency",
        "severity": "critical",
        "observed": 10.0,
        "threshold": 2.4,
        "baseline_p95": 1.2,
        "day": None,
        "affected_count": 1,
        "worst_trace_id": "slow",
        "created_at": "2026-10-17T12:00:00Z",
        "notified_at": None,
    }
    assert client.get("/api/v1/alerts", params={"profile_id": 2}).json() == {"alerts": []}
    since = {"since": "2026-10-17T12:00:01Z"}
    assert client.get("/api/v1/alerts", params=since).json() == {"alerts": []}


@pytest.mark.parametrize("limit", [0, 501])
def test_alert_limit_is_bounded(client: TestClient, limit: int) -> None:
    assert client.get("/api/v1/alerts", params={"limit": limit}).status_code == 422


def test_openapi_documents_monitoring_endpoints(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    assert set(paths[f"{PROFILES}/{{profile_id}}/thresholds"]["patch"]["responses"]) == {
        "200",
        "400",
        "404",
        "422",
        "503",
    }
    assert "get" in paths[f"{PROFILES}/{{profile_id}}/baseline"]
    assert "post" in paths[f"{PROFILES}/{{profile_id}}/relearn"]
    assert set(paths["/api/v1/alerts"]["get"]["responses"]) == {"200", "422", "503"}
