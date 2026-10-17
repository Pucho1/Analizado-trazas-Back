"""Telegram channel through the Bot API (https://core.telegram.org/bots/api).

The bot token is part of every Bot API URL, so errors never carry the URL or the
provider's text: only a `NotificationFailure` reason.
"""

from collections.abc import Sequence
from typing import Any

import httpx2

from app.core.constants import NOTIFICATION_TIMEOUT_SECONDS, TELEGRAM_API_URL
from app.core.exceptions import NotificationError
from app.domain.notifications import NotificationFailure, OutgoingMessage, TelegramChat

_PLAIN = "text/plain"


def create_telegram_http_client(
    token: str, transport: httpx2.AsyncBaseTransport | None = None
) -> httpx2.AsyncClient:
    """HTTP client whose relative URLs are Bot API methods of the bot with `token`."""
    return httpx2.AsyncClient(
        base_url=f"{TELEGRAM_API_URL}/bot{token}/",
        timeout=NOTIFICATION_TIMEOUT_SECONDS,
        transport=transport,
    )


class TelegramBotApi:
    """Sends messages to one chat and lists the chats that wrote to the bot."""

    def __init__(self, http_client: httpx2.AsyncClient, chat_id: str | None) -> None:
        """Use a client from `create_telegram_http_client`; send to `chat_id`."""
        self._http_client = http_client
        self._chat_id = chat_id

    async def send(self, message: OutgoingMessage) -> None:
        """Send `message` (Telegram HTML parse mode unless it is plain text)."""
        if not self._chat_id:
            raise NotificationError(
                NotificationFailure.NOT_CONFIGURED, "TELEGRAM_CHAT_ID is not set"
            )
        payload: dict[str, Any] = {
            "chat_id": self._chat_id,
            "text": message.body,
            "link_preview_options": {"is_disabled": True},
        }
        if not message.content_type.startswith(_PLAIN):
            payload["parse_mode"] = "HTML"
        await self._call("sendMessage", payload)

    async def list_chats(self) -> Sequence[TelegramChat]:
        """Chats with recent messages to the bot (Telegram keeps them about 24 h)."""
        result = await self._call("getUpdates", {"allowed_updates": ["message"]})
        chats: dict[str, TelegramChat] = {}
        for update in result if isinstance(result, list) else []:
            chat = (update.get("message") or {}).get("chat") or {}
            if "id" not in chat:
                continue
            name = chat.get("title") or " ".join(
                part for part in (chat.get("first_name"), chat.get("last_name")) if part
            )
            if chat.get("username"):
                name = f"{name} (@{chat['username']})".strip()
            chats[str(chat["id"])] = TelegramChat(
                id=str(chat["id"]), name=name, kind=str(chat.get("type", ""))
            )
        return list(chats.values())

    async def _call(self, method: str, payload: dict[str, Any]) -> Any:
        """Call a Bot API method and return its `result`, or raise a sanitized error."""
        try:
            response = await self._http_client.post(method, json=payload)
        except httpx2.HTTPError as error:
            raise NotificationError(
                NotificationFailure.UNAVAILABLE, "Telegram could not be reached"
            ) from _without_url(error)
        try:
            body = response.json()
        except ValueError:
            body = {}
        # 401: unknown token; 404: malformed token (the method URL does not exist).
        if response.status_code in (401, 404):
            raise NotificationError(NotificationFailure.AUTH_FAILED, "Telegram rejected the token")
        if response.status_code in (400, 403):
            raise NotificationError(
                NotificationFailure.REJECTED, "Telegram refused the message (chat or format)"
            )
        if response.is_error or not body.get("ok"):
            raise NotificationError(
                NotificationFailure.UNAVAILABLE, f"Telegram answered HTTP {response.status_code}"
            )
        return body.get("result")


def _without_url(error: httpx2.HTTPError) -> Exception:
    """A stand-in for `error` that keeps its type name but not its URL (it holds the token)."""
    return RuntimeError(type(error).__name__)
