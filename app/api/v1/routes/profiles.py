"""`/profiles` endpoints: manage profiles and discover candidates."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.api.v1.dependencies import get_profile_service
from app.api.v1.errors import (
    DATABASE_ERROR_RESPONSES,
    PROFILE_NAME_TAKEN_RESPONSES,
    PROFILE_NOT_FOUND_RESPONSES,
)
from app.api.v1.schemas import (
    DiscoveredProfileResponse,
    DiscoveryResponse,
    ProfileCreateRequest,
    ProfileListResponse,
    ProfileResponse,
    ProfileUpdateRequest,
)
from app.application.profile_service import ProfileService
from app.domain.models import Profile

router = APIRouter(prefix="/profiles", tags=["profiles"], responses=DATABASE_ERROR_RESPONSES)

ProfileServiceDep = Annotated[ProfileService, Depends(get_profile_service)]


@router.get("", summary="List profiles")
async def list_profiles(service: ProfileServiceDep) -> ProfileListResponse:
    """Every profile with its trace count, plus the unclassified traces («Sin clasificar»)."""
    return ProfileListResponse.from_domain(await service.overview())


@router.get("/discover", summary="Discover profiles")
async def discover_profiles(service: ProfileServiceDep) -> DiscoveryResponse:
    """Groups of unclassified traces (same environment, name and tags), largest first.

    Nothing is created: name a group and send its `rule` to `POST /profiles`.
    """
    groups = await service.discover()
    return DiscoveryResponse(
        groups=[DiscoveredProfileResponse.from_domain(group) for group in groups]
    )


@router.post(
    "",
    summary="Create a profile",
    status_code=status.HTTP_201_CREATED,
    responses=PROFILE_NAME_TAKEN_RESPONSES,
)
async def create_profile(body: ProfileCreateRequest, service: ProfileServiceDep) -> ProfileResponse:
    """Create an active profile and classify the stored traces with it."""
    profile = await service.create(body.name, body.rule.to_domain())
    return await _with_count(service, profile)


@router.patch(
    "/{profile_id}",
    summary="Rename a profile or change its rule",
    responses={**PROFILE_NOT_FOUND_RESPONSES, **PROFILE_NAME_TAKEN_RESPONSES},
)
async def update_profile(
    profile_id: int, body: ProfileUpdateRequest, service: ProfileServiceDep
) -> ProfileResponse:
    """Change the fields sent; traces are reclassified if the rule changes."""
    profile = await service.update(
        profile_id,
        name=body.name,
        rule=None if body.rule is None else body.rule.to_domain(),
    )
    return await _with_count(service, profile)


@router.post(
    "/{profile_id}/archive", summary="Archive a profile", responses=PROFILE_NOT_FOUND_RESPONSES
)
async def archive_profile(profile_id: int, service: ProfileServiceDep) -> ProfileResponse:
    """Stop monitoring the profile; it keeps its history and its traces."""
    return await _with_count(service, await service.archive(profile_id))


@router.post(
    "/{profile_id}/unarchive",
    summary="Unarchive a profile",
    responses=PROFILE_NOT_FOUND_RESPONSES,
)
async def unarchive_profile(profile_id: int, service: ProfileServiceDep) -> ProfileResponse:
    """Monitor the profile again."""
    return await _with_count(service, await service.unarchive(profile_id))


@router.delete(
    "/{profile_id}",
    summary="Delete a profile and its data",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=PROFILE_NOT_FOUND_RESPONSES,
)
async def delete_profile(profile_id: int, service: ProfileServiceDep) -> Response:
    """Delete the profile and, in cascade, all its traces. Cannot be undone."""
    await service.delete(profile_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _with_count(service: ProfileService, profile: Profile) -> ProfileResponse:
    """Response for `profile` with its current trace count."""
    overview = await service.overview()
    count = next(
        (item.trace_count for item in overview.profiles if item.profile.id == profile.id), 0
    )
    return ProfileResponse.from_domain(profile, count)
