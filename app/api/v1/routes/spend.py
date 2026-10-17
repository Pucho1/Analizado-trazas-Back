"""`/spend` endpoints: spend per profile and comparisons between periods."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.v1.dependencies import get_spend_service
from app.api.v1.errors import DATABASE_ERROR_RESPONSES, PROFILE_NOT_FOUND_RESPONSES
from app.api.v1.schemas import (
    ComparisonsResponse,
    PeriodComparisonResponse,
    SpendSummaryResponse,
)
from app.application.spend_service import SpendService
from app.domain.models import TimeRange

router = APIRouter(prefix="/spend", tags=["spend"], responses=DATABASE_ERROR_RESPONSES)

SpendServiceDep = Annotated[SpendService, Depends(get_spend_service)]


@router.get("", summary="Spend per profile")
async def get_spend(
    service: SpendServiceDep,
    time_range: Annotated[
        TimeRange, Query(alias="range", description="Time window ending now.")
    ] = TimeRange.LAST_24_HOURS,
) -> SpendSummaryResponse:
    """Queries, cost and text volume per profile, unclassified and in total."""
    return SpendSummaryResponse.from_domain(time_range, await service.summary(time_range))


@router.get("/comparisons", summary="Compare periods", responses=PROFILE_NOT_FOUND_RESPONSES)
async def get_comparisons(
    service: SpendServiceDep,
    day: Annotated[
        date | None, Query(description="Last day of the periods (local); default yesterday.")
    ] = None,
    profile_id: Annotated[int | None, Query(description="One profile; default all.")] = None,
) -> ComparisonsResponse:
    """Day, week to date and month to date against the same days of the previous ones."""
    day, comparisons = await service.comparisons(day, profile_id)
    return ComparisonsResponse(
        day=day,
        profile_id=profile_id,
        comparisons=[PeriodComparisonResponse.from_domain(c) for c in comparisons],
    )
