"""Notification endpoints with a service over fakes."""

from collections.abc import Iterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.dependencies import get_notification_service, get_report_service
from app.application.notification_service import ChannelStatus
from app.core.exceptions import (
    ChannelNotConfiguredError,
    NotificationError,
    ReportAlreadySentError,
)
from app.domain.notifications import (
    Delivery,
    DeliveryKind,
    DeliveryStatus,
    NotificationFailure,
    TelegramChat,
)
from app.domain.reporting import Channel, ChannelSettings
from app.main import create_app

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
SENT = Delivery(Channel.TELEGRAM, DeliveryKind.TEST, DeliveryStatus.SENT, NOW, id=7)


@pytest.fixture
def service() -> AsyncMock:
    """A notification service whose answers each test sets."""
    return AsyncMock()


@pytest.fixture
def client(service: AsyncMock) -> Iterator[TestClient]:
    """App wired to the mocked service."""
    app: FastAPI = create_app()
    app.dependency_overrides[get_notification_service] = lambda: service
    app.dependency_overrides[get_report_service] = lambda: AsyncMock()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_channels_never_show_secrets(client: TestClient, service: AsyncMock) -> None:
    service.channels.return_value = [
        ChannelStatus(Channel.EMAIL, False, ChannelSettings(Channel.EMAIL, True, ("overview",))),
        ChannelStatus(Channel.TELEGRAM, True, ChannelSettings(Channel.TELEGRAM, True, ())),
    ]

    body = client.get("/api/v1/notifications/channels").json()

    assert body["channels"][0] == {
        "channel": "email",
        "configured": False,
        "settings": {
            "channel": "email",
            "enabled": True,
            "sections": ["overview"],
            "alerts_enabled": True,
            "scan_summary_enabled": False,
        },
    }
    assert body["channels"][1]["configured"] is True


def test_send_test(client: TestClient, service: AsyncMock) -> None:
    service.send_test.return_value = SENT

    body = client.post("/api/v1/notifications/test/telegram").json()

    service.send_test.assert_awaited_once_with(Channel.TELEGRAM)
    assert body == {
        "id": 7,
        "channel": "telegram",
        "kind": "test",
        "status": "sent",
        "day": None,
        "error_code": None,
        "created_at": "2026-10-17T12:00:00Z",
    }


@pytest.mark.parametrize(
    ("error", "status_code", "code", "hint"),
    [
        (ChannelNotConfiguredError("x"), 409, "channel_not_configured", ".env"),
        (NotificationError("auth_failed", "x"), 502, "notification_failed", "credentials"),
        (NotificationError("rejected", "x"), 502, "notification_failed", "TELEGRAM_CHAT_ID"),
        (NotificationError("unavailable", "x"), 502, "notification_failed", "try again"),
    ],
)
def test_test_failures(
    client: TestClient,
    service: AsyncMock,
    error: Exception,
    status_code: int,
    code: str,
    hint: str,
) -> None:
    service.send_test.side_effect = error

    response = client.post("/api/v1/notifications/test/email")

    assert response.status_code == status_code
    assert response.json()["error"]["code"] == code
    assert hint in response.json()["error"]["message"]


def test_send_daily_report(client: TestClient, service: AsyncMock) -> None:
    service.send_daily_report.return_value = Delivery(
        Channel.EMAIL, DeliveryKind.DAILY_REPORT, DeliveryStatus.SENT, NOW, day=NOW.date(), id=1
    )

    body = client.post(
        "/api/v1/reports/daily/send", params={"channel": "email", "day": "2026-10-17"}
    ).json()

    assert (body["kind"], body["day"]) == ("daily_report", "2026-10-17")
    service.send_daily_report.assert_awaited_once_with(Channel.EMAIL, NOW.date())


def test_send_daily_report_twice(client: TestClient, service: AsyncMock) -> None:
    service.send_daily_report.side_effect = ReportAlreadySentError("x")

    response = client.post("/api/v1/reports/daily/send", params={"channel": "email"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "report_already_sent"


def test_deliveries(client: TestClient, service: AsyncMock) -> None:
    failed = Delivery(
        Channel.EMAIL,
        DeliveryKind.ALERTS,
        DeliveryStatus.FAILED,
        NOW,
        error_code=NotificationFailure.AUTH_FAILED,
        id=2,
    )
    service.list_deliveries.return_value = [failed, SENT]

    body = client.get("/api/v1/notifications/deliveries", params={"limit": 2}).json()

    service.list_deliveries.assert_awaited_once_with(2)
    assert [d["error_code"] for d in body["deliveries"]] == ["auth_failed", None]
    assert client.get("/api/v1/notifications/deliveries", params={"limit": 0}).status_code == 422


def test_telegram_chats(client: TestClient, service: AsyncMock) -> None:
    service.telegram_chats.return_value = [TelegramChat("42", "Miguel (@miguel)", "private")]

    body = client.get("/api/v1/notifications/telegram/chats").json()

    assert body == {"chats": [{"id": "42", "name": "Miguel (@miguel)", "kind": "private"}]}


def test_openapi_documents_notification_errors(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    assert {"409", "502"} <= set(paths["/api/v1/notifications/test/{channel}"]["post"]["responses"])
    assert {"409", "502"} <= set(paths["/api/v1/reports/daily/send"]["post"]["responses"])
