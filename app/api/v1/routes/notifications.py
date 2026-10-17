"""`/notifications` endpoints: channel status, tests, deliveries and Telegram chats."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.v1.dependencies import get_notification_service
from app.api.v1.errors import DATABASE_ERROR_RESPONSES, NOTIFICATION_RESPONSES
from app.api.v1.schemas import (
    ChannelsResponse,
    DeliveriesResponse,
    DeliveryResponse,
    TelegramChatResponse,
    TelegramChatsResponse,
)
from app.application.notification_service import NotificationService
from app.core.constants import DELIVERIES_DEFAULT_LIMIT, DELIVERIES_MAX_LIMIT
from app.domain.reporting import Channel

router = APIRouter(
    prefix="/notifications", tags=["notifications"], responses=DATABASE_ERROR_RESPONSES
)

NotificationServiceDep = Annotated[NotificationService, Depends(get_notification_service)]


@router.get("/channels", summary="Notification channels")
async def list_channels(service: NotificationServiceDep) -> ChannelsResponse:
    """Each channel: configured in `.env` or not (never the secrets) and what it receives."""
    return ChannelsResponse.from_domain(await service.channels())


@router.post("/test/{channel}", summary="Send a test message", responses=NOTIFICATION_RESPONSES)
async def send_test(channel: Channel, service: NotificationServiceDep) -> DeliveryResponse:
    """Send a short message through the channel to check that it works."""
    return DeliveryResponse.from_domain(await service.send_test(channel))


@router.get("/deliveries", summary="Delivery history")
async def list_deliveries(
    service: NotificationServiceDep,
    limit: Annotated[int, Query(ge=1, le=DELIVERIES_MAX_LIMIT)] = DELIVERIES_DEFAULT_LIMIT,
) -> DeliveriesResponse:
    """Every attempt to send something, newest first."""
    deliveries = await service.list_deliveries(limit)
    return DeliveriesResponse(deliveries=[DeliveryResponse.from_domain(d) for d in deliveries])


@router.get(
    "/telegram/chats", summary="Chats that wrote to the bot", responses=NOTIFICATION_RESPONSES
)
async def list_telegram_chats(service: NotificationServiceDep) -> TelegramChatsResponse:
    """Write to your bot first, then copy your chat's `id` to TELEGRAM_CHAT_ID.

    Needs only TELEGRAM_BOT_TOKEN. Telegram keeps these messages for about 24 hours.
    """
    chats = await service.telegram_chats()
    return TelegramChatsResponse(
        chats=[TelegramChatResponse(id=c.id, name=c.name, kind=c.kind) for c in chats]
    )
