"""`/reports` endpoints: sections, per-channel settings and a preview of the daily report."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.v1.dependencies import get_notification_service, get_report_service
from app.api.v1.errors import (
    DATABASE_ERROR_RESPONSES,
    NOTIFICATION_RESPONSES,
    UNKNOWN_SECTION_RESPONSES,
)
from app.api.v1.schemas import (
    ChannelSettingsRequest,
    ChannelSettingsResponse,
    DeliveryResponse,
    RenderedReportResponse,
    ReportSectionResponse,
    ReportSectionsResponse,
)
from app.application.notification_service import NotificationService
from app.application.reports.service import ReportService
from app.domain.reporting import Channel

router = APIRouter(prefix="/reports", tags=["reports"], responses=DATABASE_ERROR_RESPONSES)

ReportServiceDep = Annotated[ReportService, Depends(get_report_service)]
NotificationServiceDep = Annotated[NotificationService, Depends(get_notification_service)]


@router.get("/sections", summary="List report sections")
async def list_sections(service: ReportServiceDep) -> ReportSectionsResponse:
    """Every section a report can include, in default order."""
    return ReportSectionsResponse(
        sections=[ReportSectionResponse(key=s.key, title=s.title) for s in service.sections()]
    )


@router.get("/settings/{channel}", summary="Channel report settings")
async def get_settings(channel: Channel, service: ReportServiceDep) -> ChannelSettingsResponse:
    """Whether the channel gets the daily report and with which sections."""
    return ChannelSettingsResponse.from_domain(await service.settings(channel))


@router.put(
    "/settings/{channel}",
    summary="Change channel report settings",
    responses=UNKNOWN_SECTION_RESPONSES,
)
async def put_settings(
    channel: Channel, body: ChannelSettingsRequest, service: ReportServiceDep
) -> ChannelSettingsResponse:
    """Replace the channel's settings (alerts and scan notes keep their value if omitted)."""
    settings = await service.update_settings(
        channel,
        enabled=body.enabled,
        sections=body.sections,
        alerts_enabled=body.alerts_enabled,
        scan_summary_enabled=body.scan_summary_enabled,
    )
    return ChannelSettingsResponse.from_domain(settings)


@router.get("/daily", summary="Preview the daily report")
async def preview_daily(
    service: ReportServiceDep,
    channel: Annotated[Channel, Query(description="Layout to preview.")] = Channel.EMAIL,
    day: Annotated[
        date | None, Query(description="Local day the report is about; default yesterday.")
    ] = None,
) -> RenderedReportResponse:
    """The daily report exactly as it would be sent (nothing is sent)."""
    report_day = day or service.yesterday()
    rendered = await service.daily(channel, report_day)
    return RenderedReportResponse(
        channel=channel,
        day=report_day,
        subject=rendered.subject,
        content_type=rendered.content_type,
        body=rendered.body,
    )


@router.post("/daily/send", summary="Send the daily report now", responses=NOTIFICATION_RESPONSES)
async def send_daily(
    notifications: NotificationServiceDep,
    channel: Annotated[Channel, Query(description="Channel to send it through.")],
    day: Annotated[
        date | None, Query(description="Local day the report is about; default yesterday.")
    ] = None,
) -> DeliveryResponse:
    """Send the daily report through the channel now (once per channel and day)."""
    return DeliveryResponse.from_domain(await notifications.send_daily_report(channel, day))
