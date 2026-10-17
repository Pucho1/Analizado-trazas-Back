"""`/sync` endpoints: synchronise now and see the state of the synchronisation."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.v1.dependencies import get_sync_service
from app.api.v1.errors import DATABASE_ERROR_RESPONSES, SYNC_ERROR_RESPONSES
from app.api.v1.schemas import SyncRunResponse, SyncStateResponse
from app.application.sync_service import SyncService
from app.domain.models import SyncTrigger

router = APIRouter(prefix="/sync", tags=["sync"])

SyncServiceDep = Annotated[SyncService, Depends(get_sync_service)]


@router.post("", summary="Sync now", responses=SYNC_ERROR_RESPONSES)
async def sync_now(service: SyncServiceDep) -> SyncRunResponse:
    """Read the new traces from Langfuse, store them and return the finished run.

    Waits until the synchronisation ends (usually a few seconds).
    """
    run = await service.sync(SyncTrigger.MANUAL)
    return SyncRunResponse.from_domain(run)


@router.get("", summary="Synchronisation state", responses=DATABASE_ERROR_RESPONSES)
async def get_sync_state(service: SyncServiceDep) -> SyncStateResponse:
    """Return whether a sync is running, the last run and the last successful one."""
    return SyncStateResponse.from_domain(await service.state())
