"""Translation of Langfuse v2 observations into domain traces.

A Langfuse trace is not returned as a single object: it is the set of observations
(spans, generations, ...) sharing a `traceId`. Mapping rules:

- timestamp: earliest observation start.
- name: `traceName` of the root observation, else its `name`, else `UNNAMED_TRACE_NAME`.
- environment: that of the root observation, else of the earliest one, else
  `DEFAULT_ENVIRONMENT` (what Langfuse assigns when none is sent).
- tags: sorted union of the tags of every observation (they are trace-level in Langfuse).
- latency: latest observation end minus earliest start; None if nothing has ended yet.
- cost: sum of `totalCost` (USD).
- tokens: sums of `usageDetails` `input`, `output` and `total`; `total` falls back to
  input + output for observations that do not report it.
- observations: every observation, ordered by start time, keeping only metadata
  (name, type, level, model). An empty name or model counts as missing.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from decimal import Decimal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.core.constants import DEFAULT_ENVIRONMENT, UNNAMED_TRACE_NAME
from app.domain.models import Observation, ObservationLevel, Trace


class LangfuseObservation(BaseModel):
    """The subset of a v2 observation that Observa needs."""

    model_config = ConfigDict(frozen=True)

    id: str
    trace_id: str | None = Field(default=None, alias="traceId")
    type: str
    start_time: AwareDatetime = Field(alias="startTime")
    end_time: AwareDatetime | None = Field(default=None, alias="endTime")
    name: str | None = None
    level: ObservationLevel = ObservationLevel.DEFAULT
    environment: str | None = None
    tags: list[str] | None = None
    model: str | None = None
    trace_name: str | None = Field(default=None, alias="traceName")
    is_root_observation: bool | None = Field(default=None, alias="isRootObservation")
    total_cost: Decimal | None = Field(default=None, alias="totalCost")
    usage_details: dict[str, int] | None = Field(default=None, alias="usageDetails")


class LangfuseObservationsMeta(BaseModel):
    cursor: str | None = None


class LangfuseObservationsPage(BaseModel):
    """One page of `GET /api/public/v2/observations`."""

    data: list[LangfuseObservation]
    meta: LangfuseObservationsMeta


def map_traces(observations: Iterable[LangfuseObservation]) -> list[Trace]:
    """Group observations by trace and build one `Trace` per group."""
    grouped: dict[str, list[LangfuseObservation]] = defaultdict(list)
    for observation in observations:
        if observation.trace_id is not None:
            grouped[observation.trace_id].append(observation)
    return [_to_trace(trace_id, group) for trace_id, group in grouped.items()]


def _to_trace(trace_id: str, observations: Sequence[LangfuseObservation]) -> Trace:
    """Build one `Trace` from all the observations sharing `trace_id`."""
    ordered = sorted(observations, key=lambda obs: (obs.start_time, obs.id))
    usages = [observation.usage_details or {} for observation in ordered]
    input_tokens = sum(usage.get("input", 0) for usage in usages)
    output_tokens = sum(usage.get("output", 0) for usage in usages)
    return Trace(
        id=trace_id,
        timestamp=ordered[0].start_time,
        name=_trace_name(ordered),
        environment=_environment(ordered),
        latency=_latency(ordered),
        cost=sum(
            (observation.total_cost or Decimal(0) for observation in ordered),
            start=Decimal(0),
        ),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=sum(_total_tokens(usage) for usage in usages),
        tags=tuple(sorted({tag for observation in ordered for tag in observation.tags or ()})),
        observations=tuple(_to_observation(observation) for observation in ordered),
    )


def _to_observation(observation: LangfuseObservation) -> Observation:
    """Keep the metadata of one observation, dropping everything else."""
    return Observation(
        id=observation.id,
        name=observation.name or None,
        type=observation.type,
        level=observation.level,
        model=observation.model or None,
        start_time=observation.start_time,
    )


def _root(observations: Sequence[LangfuseObservation]) -> LangfuseObservation | None:
    """The root observation of the trace, if it has arrived."""
    return next((obs for obs in observations if obs.is_root_observation), None)


def _trace_name(observations: Sequence[LangfuseObservation]) -> str:
    """Name of the trace taken from its root observation, or `UNNAMED_TRACE_NAME`."""
    root = _root(observations)
    if root is None:
        return UNNAMED_TRACE_NAME
    return root.trace_name or root.name or UNNAMED_TRACE_NAME


def _environment(observations: Sequence[LangfuseObservation]) -> str:
    """Environment of the root (else earliest) observation, or `DEFAULT_ENVIRONMENT`.

    `observations` must be ordered by start time.
    """
    source = _root(observations) or observations[0]
    return source.environment or DEFAULT_ENVIRONMENT


def _latency(observations: Sequence[LangfuseObservation]) -> float | None:
    """Seconds from the earliest start to the latest end; None if nothing has ended."""
    ends: list[datetime] = [obs.end_time for obs in observations if obs.end_time is not None]
    if not ends:
        return None
    start = min(observation.start_time for observation in observations)
    return (max(ends) - start).total_seconds()


def _total_tokens(usage: Mapping[str, int]) -> int:
    """Reported `total` of one observation, or input + output when it is missing."""
    if "total" in usage:
        return usage["total"]
    return usage.get("input", 0) + usage.get("output", 0)
