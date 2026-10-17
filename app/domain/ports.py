from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol

from app.domain.models import DiscoveredGroup, Profile, ProfileRule, SyncRun, Trace, TraceKey


class TraceSource(Protocol):
    """Any provider of traces (Langfuse, LangSmith, ...)."""

    async def list_traces(self, start: datetime, end: datetime) -> Sequence[Trace]:
        """Return every trace whose timestamp falls within [start, end].

        Implementations handle their own pagination and raise
        `TraceSourceAuthenticationError` when the credentials are rejected or
        `TraceSourceError` for any other failure.
        """
        ...


class TraceRepository(Protocol):
    """Persistent storage of trace metrics (never their content)."""

    async def save_traces(self, traces: Sequence[Trace]) -> None:
        """Insert or update the given traces, idempotently.

        A trace that already exists is overwritten and its observations are replaced
        by the ones given, so saving the same trace twice never duplicates anything.
        """
        ...

    async def list_traces(self, start: datetime, end: datetime) -> Sequence[Trace]:
        """Return every stored trace whose timestamp falls within [start, end].

        Traces include their observations. Raises `RepositoryError` if the storage
        is unavailable (as does `save_traces`).
        """
        ...


class ProfileRepository(Protocol):
    """Profiles and the assignment of stored traces to them.

    Every method raises `RepositoryError` if the storage is unavailable.
    """

    async def add(self, name: str, rule: ProfileRule, created_at: datetime) -> Profile:
        """Create an active profile; raises `ProfileNameTakenError` on a duplicate name."""
        ...

    async def get(self, profile_id: int) -> Profile | None:
        """The profile with that id, or None."""
        ...

    async def list_profiles(self) -> Sequence[Profile]:
        """Every profile, oldest first."""
        ...

    async def update(self, profile: Profile) -> None:
        """Overwrite name, rule and status of an existing profile.

        Raises `ProfileNotFoundError` if it no longer exists and
        `ProfileNameTakenError` if another profile has the new name.
        """
        ...

    async def delete(self, profile_id: int) -> bool:
        """Delete the profile and, in cascade, its traces; False if it did not exist."""
        ...

    async def list_trace_keys(self) -> Sequence[TraceKey]:
        """Every stored trace with the attributes profiles classify on."""
        ...

    async def assign_profiles(self, assignments: Mapping[str, int | None]) -> None:
        """Set the profile (None = unclassified) of each trace id given."""
        ...

    async def count_traces_by_profile(self) -> Mapping[int | None, int]:
        """Number of stored traces per profile id; the None key counts unclassified ones."""
        ...

    async def discover_groups(self) -> Sequence[DiscoveredGroup]:
        """Unclassified traces grouped by environment, name and tags, largest first."""
        ...


class SyncRunRepository(Protocol):
    """History of synchronisations; the last successful one is the sync marker."""

    async def save(self, run: SyncRun) -> None:
        """Record a finished run."""
        ...

    async def latest(self, source: str) -> SyncRun | None:
        """The most recent run of `source`, whatever its outcome; None if never run."""
        ...

    async def latest_successful(self, source: str) -> SyncRun | None:
        """The successful run of `source` that reached furthest; None if none succeeded."""
        ...
