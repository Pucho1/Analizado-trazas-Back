"""Public response models of the v1 API.

They are the HTTP contract (exposed through OpenAPI) and are kept separate from the
domain models so the domain can evolve without breaking clients. Monetary amounts are
computed with `Decimal` in the domain and only converted to JSON numbers here.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, Field, StringConstraints, model_validator

from app.application.monitoring_service import BaselineView
from app.application.profile_service import ProfileOverview
from app.application.sync_service import SyncState
from app.core.constants import (
    LEARNING_TRACES,
    MIN_BASELINE_DAYS,
    MONITORED_ENVIRONMENT,
    PROFILE_NAME_MAX_LENGTH,
)
from app.domain.models import (
    DiscoveredGroup,
    Profile,
    ProfileRule,
    ProfileStatus,
    Summary,
    SyncRun,
    SyncStatus,
    SyncTrigger,
    Thresholds,
    TimeRange,
    Trace,
)
from app.domain.monitoring import (
    Alert,
    AlertKind,
    AlertMetric,
    AlertScope,
    DayMetric,
    Severity,
    TraceMetric,
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
    INVALID_THRESHOLDS = "invalid_thresholds"


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


class ThresholdsResponse(BaseModel):
    """When a profile raises alerts. Amounts in USD."""

    warning_factor: float = Field(description="Warning above this many times the p95.")
    critical_factor: float = Field(description="Critical above this many times the p95.")
    daily_budget: float | None = Field(
        description="Alert whenever a day costs more; null = no budget."
    )
    min_trace_cost: float = Field(description="Cheaper traces never raise cost alerts.")
    min_daily_cost: float = Field(description="Cheaper days never raise cost alerts.")
    silence_hours: int = Field(description="The same alert is not repeated within this time.")

    @classmethod
    def from_domain(cls, thresholds: Thresholds) -> ThresholdsResponse:
        """Response body for domain thresholds."""
        return cls(
            warning_factor=float(thresholds.warning_factor),
            critical_factor=float(thresholds.critical_factor),
            daily_budget=_optional_float(thresholds.daily_budget),
            min_trace_cost=float(thresholds.min_trace_cost),
            min_daily_cost=float(thresholds.min_daily_cost),
            silence_hours=thresholds.silence_hours,
        )


class ThresholdsUpdateRequest(BaseModel):
    """Body of `PATCH /profiles/{id}/thresholds`: only the fields sent change.

    Send `"daily_budget": null` to remove the budget.
    """

    warning_factor: Decimal | None = Field(default=None, gt=0)
    critical_factor: Decimal | None = Field(default=None, gt=0)
    daily_budget: Decimal | None = Field(default=None, gt=0)
    min_trace_cost: Decimal | None = Field(default=None, ge=0)
    min_daily_cost: Decimal | None = Field(default=None, ge=0)
    silence_hours: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _requires_a_change(self) -> ThresholdsUpdateRequest:
        """Reject an empty body and nulls other than `daily_budget`."""
        if not self.model_fields_set:
            raise ValueError("Send at least one threshold")
        nulls = {name for name in self.model_fields_set if getattr(self, name) is None}
        if nulls - {"daily_budget"}:
            raise ValueError(f"Only daily_budget can be null, not {sorted(nulls)}")
        return self

    def changes(self) -> dict[str, Any]:
        """The fields sent, ready for `MonitoringService.update_thresholds`."""
        return {name: getattr(self, name) for name in self.model_fields_set}


class ProfileResponse(BaseModel):
    """A profile and how many stored traces belong to it."""

    id: int
    name: str
    rule: ProfileRuleSchema
    status: ProfileStatus = Field(description="`archived` profiles are no longer monitored.")
    created_at: datetime
    archived_at: datetime | None
    baseline_since: datetime | None = Field(
        description="The baseline only uses traces from here on (set by relearning)."
    )
    thresholds: ThresholdsResponse
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
            baseline_since=profile.baseline_since,
            thresholds=ThresholdsResponse.from_domain(profile.thresholds),
            trace_count=trace_count,
        )


class BaselineStatus(StrEnum):
    """Whether a profile can raise anomaly alerts yet."""

    LEARNING = "learning"
    READY = "ready"


class TraceMetricStatsResponse(BaseModel):
    """Median and p95 of one per-trace metric."""

    metric: TraceMetric
    median: float
    p95: float = Field(description="The ceiling of normal.")
    sample_size: int


class DayMetricStatsResponse(BaseModel):
    """Median and p95 of one per-day metric."""

    metric: DayMetric
    median: float
    p95: float = Field(description="The ceiling of normal.")
    sample_size: int


class BaselineResponse(BaseModel):
    """What is normal for a profile right now."""

    profile_id: int
    status: BaselineStatus
    trace_count: int = Field(description="Traces since `baseline_since` (all if null).")
    learning_target: int = Field(description="Traces needed before anomaly alerts start.")
    baseline_since: datetime | None
    daily_monitoring: bool = Field(
        description=f"Whether there are {MIN_BASELINE_DAYS}+ complete days for daily alerts."
    )
    computed_at: datetime
    trace_sample_size: int = Field(description="Most recent traces used per trace.")
    day_sample_size: int = Field(description="Complete days used per day (empty days = 0).")
    trace_metrics: list[TraceMetricStatsResponse]
    day_metrics: list[DayMetricStatsResponse]

    @classmethod
    def from_domain(cls, view: BaselineView) -> BaselineResponse:
        """Response body for the service's baseline view."""
        baseline = view.baseline
        return cls(
            profile_id=view.profile.id,
            status=BaselineStatus.LEARNING if baseline.learning else BaselineStatus.READY,
            trace_count=baseline.profile_trace_count,
            learning_target=LEARNING_TRACES,
            baseline_since=view.profile.baseline_since,
            daily_monitoring=baseline.days_ready,
            computed_at=baseline.computed_at,
            trace_sample_size=baseline.trace_sample_size,
            day_sample_size=baseline.day_sample_size,
            trace_metrics=[
                TraceMetricStatsResponse(
                    metric=metric,
                    median=float(stats.median),
                    p95=float(stats.p95),
                    sample_size=stats.sample_size,
                )
                for metric, stats in baseline.trace_stats.items()
            ],
            day_metrics=[
                DayMetricStatsResponse(
                    metric=metric,
                    median=float(stats.median),
                    p95=float(stats.p95),
                    sample_size=stats.sample_size,
                )
                for metric, stats in baseline.day_stats.items()
            ],
        )


class AlertResponse(BaseModel):
    """A threshold crossed by a profile."""

    id: int
    profile_id: int
    kind: AlertKind = Field(description="`anomaly` (above its normal) or `budget`.")
    scope: AlertScope = Field(description="`trace` (individual traces) or `day`.")
    metric: AlertMetric
    severity: Severity
    observed: float = Field(description="Worst value seen (USD, seconds or queries).")
    threshold: float = Field(description="Limit it crossed.")
    baseline_p95: float | None = Field(description="The profile's p95; null for budgets.")
    day: date | None = Field(description="Local day of a per-day alert.")
    affected_count: int = Field(description="Traces that crossed (trace) or queries (day).")
    worst_trace_id: str | None
    created_at: datetime
    notified_at: datetime | None = Field(description="When it was sent; null if not yet.")

    @classmethod
    def from_domain(cls, alert: Alert) -> AlertResponse:
        """Response body for a stored domain alert."""
        if alert.id is None:
            raise ValueError("Only stored alerts have a response")
        return cls(
            id=alert.id,
            profile_id=alert.profile_id,
            kind=alert.kind,
            scope=alert.scope,
            metric=alert.metric,
            severity=alert.severity,
            observed=float(alert.observed),
            threshold=float(alert.threshold),
            baseline_p95=_optional_float(alert.baseline_p95),
            day=alert.day,
            affected_count=alert.affected_count,
            worst_trace_id=alert.worst_trace_id,
            created_at=alert.created_at,
            notified_at=alert.notified_at,
        )


class AlertListResponse(BaseModel):
    """Alerts, newest first."""

    alerts: list[AlertResponse]


def _optional_float(value: Decimal | None) -> float | None:
    """`value` as a float, or None."""
    return None if value is None else float(value)


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
