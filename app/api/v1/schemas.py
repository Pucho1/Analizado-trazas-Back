"""Public response models of the v1 API.

They are the HTTP contract (exposed through OpenAPI) and are kept separate from the
domain models so the domain can evolve without breaking clients. Monetary amounts are
computed with `Decimal` in the domain and only converted to JSON numbers here.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, model_validator

from app.application.profile_service import ProfileOverview
from app.application.sync_service import SyncState
from app.core.constants import MONITORED_ENVIRONMENT, PROFILE_NAME_MAX_LENGTH
from app.domain.models import (
    DiscoveredGroup,
    Profile,
    ProfileRule,
    ProfileStatus,
    Summary,
    SyncRun,
    SyncStatus,
    SyncTrigger,
    TimeRange,
    Trace,
)


class TraceResponse(BaseModel):
    """A single trace as returned by `GET /api/v1/traces`."""

    id: str
    timestamp: datetime = Field(description="Start of the trace (ISO 8601, UTC offset included).")
    name: str = Field(description="Agent or tool that produced the trace.")
    latency: float | None = Field(description="Duration in seconds; null if still in progress.")
    cost: float = Field(description="Cost in USD.")
    input_tokens: int
    output_tokens: int
    total_tokens: int

    @classmethod
    def from_domain(cls, trace: Trace) -> TraceResponse:
        """Build the response model from a domain `Trace`."""
        return cls(
            id=trace.id,
            timestamp=trace.timestamp,
            name=trace.name,
            latency=trace.latency,
            cost=float(trace.cost),
            input_tokens=trace.input_tokens,
            output_tokens=trace.output_tokens,
            total_tokens=trace.total_tokens,
        )


class TraceListResponse(BaseModel):
    """Traces in the requested range, most recent first."""

    time_range: TimeRange
    traces: list[TraceResponse]

    @classmethod
    def from_domain(cls, time_range: TimeRange, traces: list[Trace]) -> TraceListResponse:
        """Build the response model from the requested range and its domain traces."""
        return cls(
            time_range=time_range,
            traces=[TraceResponse.from_domain(trace) for trace in traces],
        )


class SummaryResponse(BaseModel):
    """Aggregated metrics over the traces in the requested range."""

    time_range: TimeRange
    trace_count: int
    total_cost: float = Field(description="Total cost in USD.")
    total_tokens: int
    average_latency: float | None = Field(
        description="Mean latency in seconds over traces that report one; null if none do."
    )

    @classmethod
    def from_domain(cls, time_range: TimeRange, summary: Summary) -> SummaryResponse:
        """Build the response model from the requested range and its domain summary."""
        return cls(
            time_range=time_range,
            trace_count=summary.trace_count,
            total_cost=float(summary.total_cost),
            total_tokens=summary.total_tokens,
            average_latency=summary.average_latency,
        )


class ErrorCode(StrEnum):
    """Machine-readable error identifiers that clients can branch on."""

    TRACE_SOURCE_AUTH_FAILED = "trace_source_auth_failed"
    TRACE_SOURCE_UNAVAILABLE = "trace_source_unavailable"
    DATABASE_UNAVAILABLE = "database_unavailable"
    SYNC_ALREADY_RUNNING = "sync_already_running"
    PROFILE_NOT_FOUND = "profile_not_found"
    PROFILE_NAME_TAKEN = "profile_name_taken"


ProfileName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=PROFILE_NAME_MAX_LENGTH),
    Field(description="Business name, e.g. «Asistente de la web». Unique, ignoring case."),
]
NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ProfileRuleSchema(BaseModel):
    """Which traces belong to a profile: all criteria must hold."""

    environment: NonBlank = Field(
        default=MONITORED_ENVIRONMENT, description="Langfuse environment of the traces."
    )
    trace_name: NonBlank | None = Field(
        default=None, description="Trace name; null matches any name."
    )
    tags: list[NonBlank] = Field(
        default_factory=list,
        description="Tags the trace must carry (it may carry more). Returned sorted.",
    )

    def to_domain(self) -> ProfileRule:
        """Domain rule for this request body."""
        return ProfileRule(
            environment=self.environment, trace_name=self.trace_name, tags=tuple(self.tags)
        )

    @classmethod
    def from_domain(cls, rule: ProfileRule) -> ProfileRuleSchema:
        """Response body for a domain rule."""
        return cls(environment=rule.environment, trace_name=rule.trace_name, tags=list(rule.tags))


class ProfileCreateRequest(BaseModel):
    """Body of `POST /profiles`."""

    name: ProfileName
    rule: ProfileRuleSchema


class ProfileUpdateRequest(BaseModel):
    """Body of `PATCH /profiles/{id}`: only the fields sent change."""

    name: ProfileName | None = None
    rule: ProfileRuleSchema | None = None

    @model_validator(mode="after")
    def _requires_a_change(self) -> ProfileUpdateRequest:
        """Reject an empty body."""
        if self.name is None and self.rule is None:
            raise ValueError("Send `name`, `rule` or both")
        return self


class ProfileResponse(BaseModel):
    """A profile and how many stored traces belong to it."""

    id: int
    name: str
    rule: ProfileRuleSchema
    status: ProfileStatus = Field(description="`archived` profiles are no longer monitored.")
    created_at: datetime
    archived_at: datetime | None
    trace_count: int

    @classmethod
    def from_domain(cls, profile: Profile, trace_count: int) -> ProfileResponse:
        """Response body for a domain profile and its trace count."""
        return cls(
            id=profile.id,
            name=profile.name,
            rule=ProfileRuleSchema.from_domain(profile.rule),
            status=profile.status,
            created_at=profile.created_at,
            archived_at=profile.archived_at,
            trace_count=trace_count,
        )


class UnclassifiedResponse(BaseModel):
    """Traces that no profile captures ("Sin clasificar")."""

    trace_count: int


class ProfileListResponse(BaseModel):
    """Every profile (oldest first) plus the unclassified group."""

    profiles: list[ProfileResponse]
    unclassified: UnclassifiedResponse

    @classmethod
    def from_domain(cls, overview: ProfileOverview) -> ProfileListResponse:
        """Response body for the service's overview."""
        return cls(
            profiles=[
                ProfileResponse.from_domain(item.profile, item.trace_count)
                for item in overview.profiles
            ],
            unclassified=UnclassifiedResponse(trace_count=overview.unclassified_count),
        )


class DiscoveredProfileResponse(BaseModel):
    """A group of unclassified traces that could become a profile."""

    rule: ProfileRuleSchema = Field(description="Rule that captures exactly this group.")
    suggested_name: str = Field(description="The trace name; replace it with a business name.")
    monitored_environment: bool = Field(
        description=f"Whether the group is in `{MONITORED_ENVIRONMENT}`, the environment "
        "watched by default."
    )
    trace_count: int
    first_seen: datetime
    last_seen: datetime
    total_cost: float = Field(description="Cost of the group's traces in USD.")

    @classmethod
    def from_domain(cls, group: DiscoveredGroup) -> DiscoveredProfileResponse:
        """Response body for a discovered group."""
        return cls(
            rule=ProfileRuleSchema.from_domain(group.rule),
            suggested_name=group.trace_name,
            monitored_environment=group.environment == MONITORED_ENVIRONMENT,
            trace_count=group.trace_count,
            first_seen=group.first_seen,
            last_seen=group.last_seen,
            total_cost=float(group.total_cost),
        )


class DiscoveryResponse(BaseModel):
    """Candidate profiles, largest group first."""

    groups: list[DiscoveredProfileResponse]


class SyncRunResponse(BaseModel):
    """One synchronisation from Langfuse into Observa's database."""

    trigger: SyncTrigger = Field(description="`scheduled` (automatic) or `manual` (sync now).")
    status: SyncStatus
    started_at: datetime
    finished_at: datetime
    window_start: datetime = Field(description="Traces that started from this moment...")
    window_end: datetime = Field(description="...up to this one were read.")
    trace_count: int = Field(description="Traces read and stored (0 if the run failed).")
    error_code: ErrorCode | None = Field(
        description="Why the run failed; null if it succeeded. `trace_source_auth_failed` "
        "means the Langfuse keys are wrong."
    )

    @classmethod
    def from_domain(cls, run: SyncRun) -> SyncRunResponse:
        """Build the response model from a domain `SyncRun`."""
        return cls(
            trigger=run.trigger,
            status=run.status,
            started_at=run.started_at,
            finished_at=run.finished_at,
            window_start=run.window_start,
            window_end=run.window_end,
            trace_count=run.trace_count,
            error_code=None if run.failure_reason is None else ErrorCode(run.failure_reason),
        )


class SyncStateResponse(BaseModel):
    """Whether a sync is running, the last run and the last successful one."""

    running: bool
    last_run: SyncRunResponse | None = Field(
        description="Most recent run, successful or not; null if never synced."
    )
    last_successful_run: SyncRunResponse | None = Field(
        description="Run up to which data is complete; null if no sync ever succeeded."
    )

    @classmethod
    def from_domain(cls, state: SyncState) -> SyncStateResponse:
        """Build the response model from the service's `SyncState`."""
        return cls(
            running=state.running,
            last_run=_optional_run(state.last_run),
            last_successful_run=_optional_run(state.last_successful_run),
        )


def _optional_run(run: SyncRun | None) -> SyncRunResponse | None:
    """Response for `run`, or None."""
    return None if run is None else SyncRunResponse.from_domain(run)


class ErrorDetail(BaseModel):
    """Description of an error: a stable `code` plus a human-readable `message`."""

    code: ErrorCode
    message: str


class ErrorResponse(BaseModel):
    """Body of every error response produced by Observa's own exception handlers."""

    error: ErrorDetail
