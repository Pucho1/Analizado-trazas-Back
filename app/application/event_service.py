"""Use cases for the per-profile mapping of steps to business events."""

from collections.abc import Sequence

from app.core.exceptions import ProfileNotFoundError
from app.domain.events import EventMapping, StepCount
from app.domain.ports import EventMappingRepository, ProfileRepository, SpendRepository


class EventService:
    """Read and replace a profile's event mapping and list its step names to choose from."""

    def __init__(
        self,
        profiles: ProfileRepository,
        mappings: EventMappingRepository,
        spend: SpendRepository,
    ) -> None:
        """Check profiles exist with `profiles`; read steps with `spend`."""
        self._profiles = profiles
        self._mappings = mappings
        self._spend = spend

    async def mappings(self, profile_id: int) -> Sequence[EventMapping]:
        """The profile's mapping (empty until the user defines it)."""
        await self._require(profile_id)
        return await self._mappings.list_mappings(profile_id)

    async def replace_mappings(
        self, profile_id: int, mappings: Sequence[EventMapping]
    ) -> Sequence[EventMapping]:
        """Replace the profile's whole mapping and return it."""
        await self._require(profile_id)
        await self._mappings.replace_mappings(profile_id, mappings)
        return await self._mappings.list_mappings(profile_id)

    async def step_names(self, profile_id: int) -> list[StepCount]:
        """Every named step of the profile's stored traces, most frequent first."""
        await self._require(profile_id)
        steps = await self._spend.step_counts(profile_id, None, None)
        return sorted(steps, key=lambda step: (-step.occurrences, step.name, step.level.value))

    async def _require(self, profile_id: int) -> None:
        """Raise `ProfileNotFoundError` unless the profile exists."""
        if await self._profiles.get(profile_id) is None:
            raise ProfileNotFoundError(f"Profile {profile_id} does not exist")
