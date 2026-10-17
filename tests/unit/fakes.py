from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal

from app.core.exceptions import ProfileNameTakenError, ProfileNotFoundError
from app.domain.models import (
    DiscoveredGroup,
    Observation,
    ObservationLevel,
    Profile,
    ProfileRule,
    ProfileStatus,
    SyncRun,
    SyncStatus,
    Trace,
    TraceKey,
)

DEFAULT_TIMESTAMP = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)

# Every required setting with a dummy value, for tests that build `Settings` themselves.
REQUIRED_SETTINGS = {
    "langfuse_public_key": "pk-lf-test",
    "langfuse_secret_key": "sk-lf-test",
    "langfuse_host": "https://langfuse.test",
    "postgres_user": "observa-test",
    "postgres_password": "observa-test",
    "postgres_db": "observa-test",
}


def make_observation(
    *,
    id: str = "obs-1",
    name: str | None = "router",
    type: str = "SPAN",
    level: ObservationLevel = ObservationLevel.DEFAULT,
    model: str | None = None,
    start_time: datetime = DEFAULT_TIMESTAMP,
) -> Observation:
    """Build a valid `Observation` with sensible defaults."""
    return Observation(
        id=id,
        name=name,
        type=type,
        level=level,
        model=model,
        start_time=start_time,
    )


def make_trace(
    *,
    id: str = "trace-1",
    timestamp: datetime = DEFAULT_TIMESTAMP,
    name: str = "agent",
    environment: str = "production",
    latency: float | None = 1.0,
    cost: Decimal = Decimal("0.01"),
    input_tokens: int = 10,
    output_tokens: int = 5,
    total_tokens: int | None = None,
    tags: tuple[str, ...] = (),
    observations: tuple[Observation, ...] = (),
) -> Trace:
    """Build a valid `Trace` with sensible defaults; `total_tokens` defaults to in + out."""
    return Trace(
        id=id,
        timestamp=timestamp,
        name=name,
        environment=environment,
        latency=latency,
        cost=cost,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens if total_tokens is None else total_tokens,
        tags=tags,
        observations=observations,
    )


class InMemoryTraceSource:
    """TraceSource backed by a fixed list; records the windows it was asked for."""

    def __init__(self, traces: Sequence[Trace] = ()) -> None:
        """Serve `traces`; `calls` starts empty."""
        self._traces = {trace.id: trace for trace in traces}
        self.calls: list[tuple[datetime, datetime]] = []

    async def list_traces(self, start: datetime, end: datetime) -> Sequence[Trace]:
        """Record the window and return the stored traces that fall within it."""
        self.calls.append((start, end))
        return [trace for trace in self._traces.values() if start <= trace.timestamp <= end]


class InMemoryTraceRepository(InMemoryTraceSource):
    """TraceRepository in memory: saving upserts by id; records every save."""

    def __init__(self, traces: Sequence[Trace] = ()) -> None:
        """Start with `traces` stored; `saves` starts empty."""
        super().__init__(traces)
        self.saves: list[list[Trace]] = []

    @property
    def traces(self) -> list[Trace]:
        """Every stored trace."""
        return list(self._traces.values())

    async def save_traces(self, traces: Sequence[Trace]) -> None:
        """Insert or replace each trace by id."""
        self.saves.append(list(traces))
        self._traces.update((trace.id, trace) for trace in traces)


class FailingTraces:
    """TraceSource and TraceRepository whose every operation raises the given error."""

    def __init__(self, error: Exception) -> None:
        """Raise `error` on every call."""
        self._error = error

    async def list_traces(self, start: datetime, end: datetime) -> Sequence[Trace]:
        """Raise the configured error."""
        raise self._error

    async def save_traces(self, traces: Sequence[Trace]) -> None:
        """Raise the configured error."""
        raise self._error


class InMemorySyncRunRepository:
    """SyncRunRepository backed by a list, in insertion order."""

    def __init__(self, runs: Sequence[SyncRun] = ()) -> None:
        """Start with `runs` already recorded."""
        self.runs = list(runs)

    async def save(self, run: SyncRun) -> None:
        """Append the run."""
        self.runs.append(run)

    async def latest(self, source: str) -> SyncRun | None:
        """Most recently started run of `source`."""
        runs = [run for run in self.runs if run.source == source]
        return max(runs, key=lambda run: run.started_at, default=None)

    async def latest_successful(self, source: str) -> SyncRun | None:
        """Successful run of `source` with the latest window end."""
        runs = [
            run for run in self.runs if run.source == source and run.status is SyncStatus.SUCCEEDED
        ]
        return max(runs, key=lambda run: run.window_end, default=None)


class InMemoryProfileRepository:
    """ProfileRepository in memory, over a fixed set of stored traces."""

    def __init__(self, traces: Sequence[Trace] = ()) -> None:
        """Store `traces`, all unclassified, and no profiles."""
        self.traces = {trace.id: trace for trace in traces}
        self.profile_of: dict[str, int | None] = {trace.id: None for trace in traces}
        self.profiles: dict[int, Profile] = {}
        self.assign_calls: list[dict[str, int | None]] = []

    async def add(self, name: str, rule: ProfileRule, created_at: datetime) -> Profile:
        """Create an active profile with the next id."""
        self._check_name_free(name, exclude_id=None)
        profile = Profile(
            id=max(self.profiles, default=0) + 1,
            name=name,
            rule=rule,
            status=ProfileStatus.ACTIVE,
            created_at=created_at,
        )
        self.profiles[profile.id] = profile
        return profile

    async def get(self, profile_id: int) -> Profile | None:
        """The profile with that id, or None."""
        return self.profiles.get(profile_id)

    async def list_profiles(self) -> Sequence[Profile]:
        """Every profile, oldest first."""
        return sorted(self.profiles.values(), key=lambda profile: (profile.created_at, profile.id))

    async def update(self, profile: Profile) -> None:
        """Replace the stored profile."""
        if profile.id not in self.profiles:
            raise ProfileNotFoundError(str(profile.id))
        self._check_name_free(profile.name, exclude_id=profile.id)
        self.profiles[profile.id] = profile

    async def delete(self, profile_id: int) -> bool:
        """Delete the profile and its traces."""
        if self.profiles.pop(profile_id, None) is None:
            return False
        for trace_id in [t for t, p in self.profile_of.items() if p == profile_id]:
            del self.traces[trace_id]
            del self.profile_of[trace_id]
        return True

    async def list_trace_keys(self) -> Sequence[TraceKey]:
        """Every stored trace with its current profile."""
        return [
            TraceKey(
                id=trace.id,
                environment=trace.environment,
                name=trace.name,
                tags=trace.tags,
                profile_id=self.profile_of[trace.id],
            )
            for trace in self.traces.values()
        ]

    async def assign_profiles(self, assignments: Mapping[str, int | None]) -> None:
        """Record and apply the assignments."""
        self.assign_calls.append(dict(assignments))
        self.profile_of.update(assignments)

    async def count_traces_by_profile(self) -> Mapping[int | None, int]:
        """Traces per profile id; None counts unclassified ones."""
        counts: dict[int | None, int] = defaultdict(int)
        for profile_id in self.profile_of.values():
            counts[profile_id] += 1
        return counts

    async def discover_groups(self) -> Sequence[DiscoveredGroup]:
        """Unclassified traces grouped by environment, name and tags, largest first."""
        groups: dict[tuple[str, str, tuple[str, ...]], list[Trace]] = defaultdict(list)
        for trace in self.traces.values():
            if self.profile_of[trace.id] is None:
                groups[(trace.environment, trace.name, trace.tags)].append(trace)
        discovered = [
            DiscoveredGroup(
                environment=environment,
                trace_name=name,
                tags=tags,
                trace_count=len(members),
                first_seen=min(trace.timestamp for trace in members),
                last_seen=max(trace.timestamp for trace in members),
                total_cost=sum((trace.cost for trace in members), start=Decimal(0)),
            )
            for (environment, name, tags), members in groups.items()
        ]
        return sorted(
            discovered,
            key=lambda group: (-group.trace_count, group.environment, group.trace_name),
        )

    def _check_name_free(self, name: str, exclude_id: int | None) -> None:
        """Raise `ProfileNameTakenError` if another profile has `name` (ignoring case)."""
        for profile in self.profiles.values():
            if profile.id != exclude_id and profile.name.lower() == name.lower():
                raise ProfileNameTakenError(name)


def make_profile(
    *,
    id: int = 1,
    name: str = "Asistente de la web",
    rule: ProfileRule | None = None,
    status: ProfileStatus = ProfileStatus.ACTIVE,
    created_at: datetime = DEFAULT_TIMESTAMP,
) -> Profile:
    """Build a valid `Profile`; archived ones get `created_at` as archive date."""
    return Profile(
        id=id,
        name=name,
        rule=rule or ProfileRule(environment="production"),
        status=status,
        created_at=created_at,
        archived_at=created_at if status is ProfileStatus.ARCHIVED else None,
    )
