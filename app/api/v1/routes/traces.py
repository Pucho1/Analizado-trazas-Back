"""`/traces` endpoints: list of traces and their summary over a time range."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.v1.dependencies import get_trace_service
from app.api.v1.errors import TRACE_SOURCE_ERROR_RESPONSES
from app.api.v1.schemas import SummaryResponse, TraceListResponse
from app.application.trace_service import TraceService
from app.domain.models import TimeRange

router = APIRouter(prefix="/traces", tags=["traces"], responses=TRACE_SOURCE_ERROR_RESPONSES)

TimeRangeQuery = Annotated[
    TimeRange,
    Query(alias="range", description="Time window ending now over which traces are read."),
]
TraceServiceDep = Annotated[TraceService, Depends(get_trace_service)]


@router.get("", summary="List traces")
async def list_traces(
    service: TraceServiceDep,
    time_range: TimeRangeQuery = TimeRange.LAST_24_HOURS,
) -> TraceListResponse:
    """Return every trace in the range, most recent first."""
    traces = await service.list_traces(time_range)
    return TraceListResponse.from_domain(time_range, traces)


@router.get("/summary", summary="Summarise traces")
async def get_summary(
    service: TraceServiceDep,
    time_range: TimeRangeQuery = TimeRange.LAST_24_HOURS,
) -> SummaryResponse:
    """Return trace count, total cost, total tokens and average latency over the range."""
    summary = await service.get_summary(time_range)
    return SummaryResponse.from_domain(time_range, summary)
