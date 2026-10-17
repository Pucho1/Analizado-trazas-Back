from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol

from app.domain.events import EventMapping, StepCount
from app.domain.models import DiscoveredGroup, Profile, ProfileRule, SyncRun, Trace, TraceKey
from app.domain.monitoring import Alert, Baseline, TraceSample
from app.domain.reporting import Channel, ChannelSettings
from app.domain.spend import SpendTotals


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


class MonitoringRepository(Protocol):
    """What baselines and alerts read and write. `since` = None means "all traces".

    Every method raises `RepositoryError` if the storage is unavailable.
    """

    async def count_traces(self, profile_id: int, since: datetime | None) -> int:
        """Number of traces of the profile that started at or after `since`."""
        ...

    async def latest_traces(
        self, profile_id: int, since: datetime | None, limit: int
    ) -> Sequence[TraceSample]:
        """The `limit` most recent traces of the profile at or after `since`."""
        ...

    async def first_trace_at(self, profile_id: int, since: datetime | None) -> datetime | None:
        """Start of the profile's earliest trace at or after `since`; None if none."""
        ...

    async def traces_between(
        self, profile_id: int, start: datetime, end: datetime
    ) -> Sequence[TraceSample]:
        """The profile's traces that started in [start, end)."""
        ...

    async def get_baseline(self, profile_id: int) -> Baseline | None:
        """The profile's stored baseline, or None."""
        ...

    async def save_baseline(self, profile_id: int, baseline: Baseline) -> None:
        """Store `baseline` as the profile's baseline, replacing the previous one."""
        ...

    async def delete_baseline(self, profile_id: int) -> None:
        """Forget the profile's baseline (no-op if there is none)."""
        ...

    async def alerts_since(self, profile_id: int, since: datetime) -> Sequence[Alert]:
        """The profile's alerts created at or after `since`."""
        ...

    async def save_alerts(self, alerts: Sequence[Alert]) -> None:
        """Store new alerts."""
        ...

    async def list_alerts(
        self, profile_id: int | None, since: datetime | None, limit: int
    ) -> Sequence[Alert]:
        """Up to `limit` alerts, newest first, optionally of one profile and from `since`."""
        ...


class SpendRepository(Protocol):
    """Aggregates of stored traces for spend summaries and reports.

    Ranges are [start, end) on the trace start time. Raises `RepositoryError` if the
    storage is unavailable.
    """

    async def totals_by_profile(
        self, start: datetime, end: datetime
    ) -> Mapping[int | None, SpendTotals]:
        """Totals per profile id (None = unclassified); profiles without traces are absent."""
        ...

    async def step_counts(
        self, profile_id: int, start: datetime | None, end: datetime | None
    ) -> Sequence[StepCount]:
        """Named steps of the profile's traces by name and level (None bounds = all time)."""
        ...


class EventMappingRepository(Protocol):
    """Per-profile mapping of step names to business event labels."""

    async def list_mappings(self, profile_id: int) -> Sequence[EventMapping]:
        """The profile's mappings, by step name."""
        ...

    async def replace_mappings(self, profile_id: int, mappings: Sequence[EventMapping]) -> None:
        """Replace all the profile's mappings with `mappings`."""
        ...


class ReportSettingsRepository(Protocol):
    """Which sections each channel's report has."""

    async def get(self, channel: Channel) -> ChannelSettings:
        """The channel's settings (the defaults if never changed)."""
        ...

    async def save(self, settings: ChannelSettings) -> None:
        """Store the channel's settings."""
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
