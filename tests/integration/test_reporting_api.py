"""Spend, event mapping and report endpoints with in-memory services."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.dependencies import get_event_service, get_report_service, get_spend_service
from app.application.event_service import EventService
from app.application.reports.data import ReportDataBuilder
from app.application.reports.presenters import EmailPresenter, TelegramPresenter
from app.application.reports.registry import default_registry
from app.application.reports.service import ReportService
from app.application.spend_service import SpendService
from app.domain.models import ObservationLevel, ProfileRule
from app.domain.reporting import Channel
from app.main import create_app
from tests.unit.fakes import (
    InMemoryEventMappingRepository,
    InMemoryMonitoringRepository,
    InMemoryProfileRepository,
    InMemoryReportSettingsRepository,
    InMemorySpendRepository,
    make_observation,
    make_profile,
    make_trace,
)

MADRID = ZoneInfo("Europe/Madrid")
NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
YESTERDAY_NOON = NOW - timedelta(days=1)


@pytest.fixture
def client() -> Iterator[TestClient]:
    """App wired to in-memory services over one profile with two traces yesterday."""
    traces = [
        make_trace(
            id=f"t{i}",
            timestamp=YESTERDAY_NOON,
            environment="production",
            name="web",
            cost=Decimal("0.0002"),
            observations=(
                make_observation(id=f"t{i}-a", name="manipulation-attempt"),
                make_observation(id=f"t{i}-b", name="router", level=ObservationLevel.ERROR),
            ),
        )
        for i in range(2)
    ]
    traces.append(make_trace(id="loose", timestamp=NOW - timedelta(hours=1), cost=Decimal("0.1")))
    profiles = InMemoryProfileRepository(traces)
    profiles.profiles[1] = make_profile(
        id=1, name="Web", rule=ProfileRule(environment="production", trace_name="web")
    )
    profiles.profile_of.update({"t0": 1, "t1": 1})
    spend_repository = InMemorySpendRepository(profiles)
    mappings = InMemoryEventMappingRepository()
    spend = SpendService(spend_repository, profiles, MADRID, lambda: NOW)
    reports = ReportService(
        registry=default_registry(),
        settings=InMemoryReportSettingsRepository(),
        data=ReportDataBuilder(
            profiles=profiles,
            spend=spend_repository,
            monitoring=InMemoryMonitoringRepository(profiles),
            mappings=mappings,
            spend_service=spend,
            timezone=MADRID,
        ),
        presenters={Channel.EMAIL: EmailPresenter(), Channel.TELEGRAM: TelegramPresenter()},
        spend=spend,
    )
    app: FastAPI = create_app()
    app.dependency_overrides[get_spend_service] = lambda: spend
    app.dependency_overrides[get_event_service] = lambda: EventService(
        profiles, mappings, spend_repository
    )
    app.dependency_overrides[get_report_service] = lambda: reports
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_spend_summary(client: TestClient) -> None:
    body = client.get("/api/v1/spend", params={"range": "7d"}).json()

    assert body["time_range"] == "7d"
    (web,) = body["profiles"]
    assert (web["profile_id"], web["name"], web["totals"]["queries"]) == (1, "Web", 2)
    assert web["totals"]["cost"] == 0.0004
    assert web["cost_share"] == pytest.approx(0.0004 / 0.1004)
    assert body["unclassified"]["queries"] == 1
    assert body["total"]["queries"] == 3


def test_comparisons(client: TestClient) -> None:
    body = client.get("/api/v1/spend/comparisons", params={"profile_id": 1}).json()

    assert body["day"] == "2026-10-16"
    day = body["comparisons"][0]
    assert (day["period"], day["current_first_day"], day["previous_first_day"]) == (
        "day",
        "2026-10-16",
        "2026-10-15",
    )
    assert (day["current"]["queries"], day["cost_change"], day["cost_driver"]) == (2, None, None)
    assert [c["period"] for c in body["comparisons"]] == ["day", "week", "month"]


def test_comparisons_of_unknown_profile(client: TestClient) -> None:
    response = client.get("/api/v1/spend/comparisons", params={"profile_id": 9})

    assert response.status_code == 404


def test_step_names_and_event_mappings(client: TestClient) -> None:
    steps = client.get("/api/v1/profiles/1/observation-names").json()["steps"]
    assert steps == [
        {"name": "manipulation-attempt", "level": "DEFAULT", "occurrences": 2, "queries": 2},
        {"name": "router", "level": "ERROR", "occurrences": 2, "queries": 2},
    ]

    assert client.get("/api/v1/profiles/1/event-mappings").json() == {"mappings": []}
    body = {"mappings": [{"observation_name": "manipulation-attempt", "label": " Intentos "}]}
    saved = client.put("/api/v1/profiles/1/event-mappings", json=body).json()
    assert saved == {
        "mappings": [{"observation_name": "manipulation-attempt", "label": "Intentos"}]
    }


@pytest.mark.parametrize(
    "mappings",
    [
        [{"observation_name": "a", "label": "x"}, {"observation_name": "a", "label": "y"}],
        [{"observation_name": "a", "label": " "}],
        [{"observation_name": "", "label": "x"}],
    ],
)
def test_invalid_event_mappings(client: TestClient, mappings: list[dict[str, str]]) -> None:
    response = client.put("/api/v1/profiles/1/event-mappings", json={"mappings": mappings})

    assert response.status_code == 422


@pytest.mark.parametrize("path", ["observation-names", "event-mappings"])
def test_event_endpoints_of_unknown_profile(client: TestClient, path: str) -> None:
    assert client.get(f"/api/v1/profiles/9/{path}").status_code == 404


def test_sections_and_settings(client: TestClient) -> None:
    sections = client.get("/api/v1/reports/sections").json()["sections"]
    assert sections[0] == {"key": "overview", "title": "Resumen"}

    telegram = client.get("/api/v1/reports/settings/telegram").json()
    assert telegram == {
        "channel": "telegram",
        "enabled": True,
        "sections": ["overview", "alerts", "business_events"],
        "alerts_enabled": True,
        "scan_summary_enabled": False,
    }

    body = {"enabled": False, "sections": ["alerts"]}
    assert client.put("/api/v1/reports/settings/telegram", json=body).json()["enabled"] is False

    unknown = client.put(
        "/api/v1/reports/settings/email", json={"enabled": True, "sections": ["x"]}
    )
    assert unknown.status_code == 400
    assert unknown.json()["error"]["code"] == "unknown_report_section"
    repeated = {"enabled": True, "sections": ["alerts", "alerts"]}
    assert client.put("/api/v1/reports/settings/email", json=repeated).status_code == 422
    assert client.get("/api/v1/reports/settings/sms").status_code == 422


def test_daily_preview(client: TestClient) -> None:
    email = client.get("/api/v1/reports/daily").json()
    telegram = client.get(
        "/api/v1/reports/daily", params={"channel": "telegram", "day": "2026-10-16"}
    ).json()

    assert (email["channel"], email["day"]) == ("email", "2026-10-16")
    assert email["subject"] == "[A vigilar] Observa · viernes 16 de octubre de 2026"
    assert email["body"].startswith("<!doctype html>")
    assert "«Web», viernes 16: 2 consultas, 0,0004 $." in telegram["body"]
    assert "Errores técnicos: <b>2</b>" in telegram["body"]
