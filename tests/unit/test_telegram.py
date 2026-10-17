"""Telegram Bot API adapter against a mocked transport."""

import json
import traceback
from collections.abc import Callable
from typing import Any

import httpx2
import pytest

from app.core.exceptions import NotificationError
from app.domain.notifications import NotificationFailure, OutgoingMessage, TelegramChat
from app.infrastructure.notifications.telegram import TelegramBotApi, create_telegram_http_client

pytestmark = pytest.mark.anyio

TOKEN = "123456:SECRET-token-value"
HTML = OutgoingMessage("Asunto", "<b>Hola</b> &amp; adiós", "text/html; charset=utf-8")

Handler = Callable[[httpx2.Request], httpx2.Response]


def api(handler: Handler, chat_id: str | None = "42") -> TelegramBotApi:
    """Bot API over a mocked transport."""
    client = create_telegram_http_client(TOKEN, transport=httpx2.MockTransport(handler))
    return TelegramBotApi(client, chat_id)


def ok(result: Any = True) -> httpx2.Response:
    """A successful Bot API answer."""
    return httpx2.Response(200, json={"ok": True, "result": result})


async def test_send_posts_html_to_the_chat() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return ok({"message_id": 1})

    await api(handler).send(HTML)

    (request,) = requests
    assert request.method == "POST"
    assert str(request.url) == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    assert json.loads(request.content) == {
        "chat_id": "42",
        "text": "<b>Hola</b> &amp; adiós",
        "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True},
    }


async def test_plain_text_has_no_parse_mode() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        bodies.append(json.loads(request.content))
        return ok()

    await api(handler).send(OutgoingMessage("s", "1 < 2", "text/plain; charset=utf-8"))

    assert "parse_mode" not in bodies[0]


async def test_without_chat_id_it_is_not_configured() -> None:
    with pytest.raises(NotificationError) as raised:
        await api(lambda request: ok(), chat_id=None).send(HTML)

    assert raised.value.reason == NotificationFailure.NOT_CONFIGURED


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (httpx2.Response(401, json={"ok": False, "description": "Unauthorized"}), "auth_failed"),
        (httpx2.Response(404, json={"ok": False, "description": "Not Found"}), "auth_failed"),
        (httpx2.Response(400, json={"ok": False, "description": "chat not found"}), "rejected"),
        (httpx2.Response(403, json={"ok": False, "description": "bot blocked"}), "rejected"),
        (httpx2.Response(429, json={"ok": False}), "unavailable"),
        (httpx2.Response(500, text="oops"), "unavailable"),
        (httpx2.Response(200, json={"ok": False}), "unavailable"),
    ],
)
async def test_errors_become_reasons(response: httpx2.Response, reason: str) -> None:
    with pytest.raises(NotificationError) as raised:
        await api(lambda request: response).send(HTML)

    assert raised.value.reason == reason
    assert TOKEN not in str(raised.value)


async def test_network_errors_never_leak_the_token() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError(f"cannot reach {request.url}", request=request)

    with pytest.raises(NotificationError) as raised:
        await api(handler).send(HTML)

    assert raised.value.reason == NotificationFailure.UNAVAILABLE
    printed = "".join(traceback.format_exception(raised.value))
    assert TOKEN not in printed
    assert "SECRET" not in printed


async def test_list_chats_deduplicates_and_names_them() -> None:
    updates = [
        {
            "update_id": 1,
            "message": {
                "chat": {
                    "id": 42,
                    "type": "private",
                    "first_name": "Miguel",
                    "last_name": "M",
                    "username": "miguel",
                }
            },
        },
        {
            "update_id": 2,
            "message": {
                "chat": {
                    "id": 42,
                    "type": "private",
                    "first_name": "Miguel",
                    "last_name": "M",
                    "username": "miguel",
                }
            },
        },
        {"update_id": 3, "message": {"chat": {"id": -100, "type": "group", "title": "Equipo"}}},
        {"update_id": 4, "edited_message": {"chat": {"id": 7}}},
    ]
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.url.path)
        return ok(updates)

    chats = await api(handler, chat_id=None).list_chats()

    assert seen == [f"/bot{TOKEN}/getUpdates"]
    assert chats == [
        TelegramChat(id="42", name="Miguel M (@miguel)", kind="private"),
        TelegramChat(id="-100", name="Equipo", kind="group"),
    ]


async def test_list_chats_with_a_wrong_token() -> None:
    response = httpx2.Response(401, json={"ok": False})

    with pytest.raises(NotificationError) as raised:
        await api(lambda request: response).list_chats()

    assert raised.value.reason == NotificationFailure.AUTH_FAILED
