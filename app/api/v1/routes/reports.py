"""`/reports` endpoints: sections, per-channel settings and a preview of the daily report."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.v1.dependencies import get_report_service
from app.api.v1.errors import DATABASE_ERROR_RESPONSES, UNKNOWN_SECTION_RESPONSES
from app.api.v1.schemas import (
    ChannelSettingsRequest,
    ChannelSettingsResponse,
    RenderedReportResponse,
    ReportSectionResponse,
    ReportSectionsResponse,
)
from app.application.reports.service import ReportService
from app.domain.reporting import Channel

router = APIRouter(prefix="/reports", tags=["reports"], responses=DATABASE_ERROR_RESPONSES)

ReportServiceDep = Annotated[ReportService, Depends(get_report_service)]


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
    """Replace the channel's settings."""
    settings = await service.update_settings(channel, enabled=body.enabled, sections=body.sections)
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
