"""`/alerts` endpoint: alerts raised after each scan."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.v1.dependencies import get_monitoring_service
from app.api.v1.errors import DATABASE_ERROR_RESPONSES
from app.api.v1.schemas import AlertListResponse, AlertResponse
from app.application.monitoring_service import MonitoringService
from app.core.constants import ALERTS_DEFAULT_LIMIT, ALERTS_MAX_LIMIT

router = APIRouter(prefix="/alerts", tags=["alerts"], responses=DATABASE_ERROR_RESPONSES)

MonitoringServiceDep = Annotated[MonitoringService, Depends(get_monitoring_service)]


@router.get("", summary="List alerts")
async def list_alerts(
    monitoring: MonitoringServiceDep,
    profile_id: Annotated[int | None, Query(description="Only this profile's alerts.")] = None,
    since: Annotated[
        datetime | None, Query(description="Only alerts created from this moment on.")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=ALERTS_MAX_LIMIT)] = ALERTS_DEFAULT_LIMIT,
) -> AlertListResponse:
    """Alerts raised by the monitoring, newest first."""
    alerts = await monitoring.list_alerts(profile_id, since, limit)
    return AlertListResponse(alerts=[AlertResponse.from_domain(alert) for alert in alerts])
