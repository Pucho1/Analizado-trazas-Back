from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from app.domain.models import Observation, ObservationLevel, SyncRun, SyncStatus, Trace

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
