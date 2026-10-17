"""Deterministic baselines and alert thresholds (Phase 2).

Pure functions and value objects: no database, no network, no clock. Every number
is computed with `Decimal` so results are exact and reproducible.

- Percentiles use linear interpolation between closest ranks (the "type 7" method
  used by numpy and spreadsheets); the median is the 50th percentile.
- A baseline has two levels: per trace (cost, input tokens, output tokens, latency)
  and per local calendar day (number of queries and cost; days without traces count
  as zero).
- Thresholds compare with a strict "greater than": a value equal to the limit is
  normal.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from zoneinfo import ZoneInfo

from app.core.constants import BASELINE_DAYS, LEARNING_TRACES, MIN_BASELINE_DAYS
from app.domain.models import Thresholds

_HUNDRED = Decimal(100)
_MEDIAN = Decimal(50)
_P95 = Decimal(95)


class TraceMetric(StrEnum):
    """What the per-trace baseline measures."""

    COST = "cost"
    INPUT_TOKENS = "input_tokens"
    OUTPUT_TOKENS = "output_tokens"
    LATENCY = "latency"


class DayMetric(StrEnum):
    """What the per-day baseline measures."""

    QUERY_COUNT = "query_count"
    COST = "cost"


class AlertMetric(StrEnum):
    """What an alert is about (thresholds apply to cost, number of queries and latency)."""

    COST = "cost"
    LATENCY = "latency"
    QUERY_COUNT = "query_count"


class AlertScope(StrEnum):
    """Whether an alert judges individual traces or a whole day."""

    TRACE = "trace"
    DAY = "day"


class AlertKind(StrEnum):
    """Why an alert fired."""

    # Above the profile's normal (its p95).
    ANOMALY = "anomaly"
    # Above the fixed daily budget set by the user.
    BUDGET = "budget"


class Severity(StrEnum):
    """How serious an alert is."""

    WARNING = "warning"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        """Order of seriousness: a higher rank is more serious."""
        return 2 if self is Severity.CRITICAL else 1


def percentile(values: Iterable[Decimal], p: Decimal | int) -> Decimal:
    """The `p`-th percentile (0-100) of `values`, by linear interpolation.

    With the values sorted as x[0..n-1], the position is (n - 1) * p / 100 and the
    result interpolates between the two values around it. Raises `ValueError` for
    no values or `p` outside [0, 100].
    """
    ordered = sorted(values)
    if not ordered:
        raise ValueError("Cannot compute a percentile of no values")
    rank = Decimal(p)
    if not Decimal(0) <= rank <= _HUNDRED:
        raise ValueError("The percentile must be between 0 and 100")
    position = (len(ordered) - 1) * rank / _HUNDRED
    lower = int(position)
    fraction = position - lower
    if fraction == 0:
        return ordered[lower]
    return ordered[lower] + (ordered[lower + 1] - ordered[lower]) * fraction


@dataclass(frozen=True, slots=True)
class MetricStats:
    """Median and 95th percentile ("the ceiling of normal") of one metric."""

    median: Decimal
    p95: Decimal
    sample_size: int

    @classmethod
    def of(cls, values: Sequence[Decimal]) -> MetricStats | None:
        """Statistics of `values`; None if there are none."""
        if not values:
            return None
        return cls(
            median=percentile(values, _MEDIAN),
            p95=percentile(values, _P95),
            sample_size=len(values),
        )


@dataclass(frozen=True, slots=True)
class TraceSample:
    """The numbers of one stored trace that baselines and alerts look at."""

    id: str
    timestamp: datetime
    cost: Decimal
    input_tokens: int
    output_tokens: int
    latency: float | None

    def value(self, metric: TraceMetric) -> Decimal | None:
        """The trace's value for `metric`; None if it has no latency."""
        match metric:
            case TraceMetric.COST:
                return self.cost
            case TraceMetric.INPUT_TOKENS:
                return Decimal(self.input_tokens)
            case TraceMetric.OUTPUT_TOKENS:
                return Decimal(self.output_tokens)
            case TraceMetric.LATENCY:
                return None if self.latency is None else Decimal(str(self.latency))


@dataclass(frozen=True, slots=True)
class DayTotal:
    """Number of queries and cost of one local calendar day."""

    day: date
    query_count: int
    cost: Decimal

    def value(self, metric: DayMetric) -> Decimal:
        """The day's value for `metric`."""
        if metric is DayMetric.QUERY_COUNT:
            return Decimal(self.query_count)
        return self.cost


def local_day(moment: datetime, timezone: ZoneInfo) -> date:
    """Calendar day of `moment` in `timezone`."""
    return moment.astimezone(timezone).date()


def day_bounds(day: date, timezone: ZoneInfo) -> tuple[datetime, datetime]:
    """Start (inclusive) and end (exclusive) of `day` in `timezone`.

    Days are 23 or 25 hours long when the clocks change. The bounds are returned in
    UTC: Python subtracts datetimes that share a time zone by wall clock, which would
    hide that difference.
    """
    start = datetime.combine(day, time(), tzinfo=timezone)
    end = datetime.combine(day + timedelta(days=1), time(), tzinfo=timezone)
    return start.astimezone(UTC), end.astimezone(UTC)


def days_between(start: datetime, end: datetime, timezone: ZoneInfo) -> list[date]:
    """Every local day touched by [start, end], in order."""
    first, last = local_day(start, timezone), local_day(end, timezone)
    return [first + timedelta(days=offset) for offset in range((last - first).days + 1)]


def baseline_days(first_trace_at: datetime | None, now: datetime, timezone: ZoneInfo) -> list[date]:
    """Complete days the per-day baseline uses, oldest first.

    From the day of the first trace (or `BASELINE_DAYS` days ago, whichever is later)
    to yesterday; today is still in progress and never counts.
    """
    if first_trace_at is None:
        return []
    today = local_day(now, timezone)
    first = max(local_day(first_trace_at, timezone), today - timedelta(days=BASELINE_DAYS))
    return [first + timedelta(days=offset) for offset in range((today - first).days)]


def daily_totals(
    samples: Iterable[TraceSample], days: Sequence[date], timezone: ZoneInfo
) -> list[DayTotal]:
    """One total per day in `days` (zero if it has no traces); other samples are ignored."""
    counts = dict.fromkeys(days, 0)
    costs = dict.fromkeys(days, Decimal(0))
    for sample in samples:
        day = local_day(sample.timestamp, timezone)
        if day in counts:
            counts[day] += 1
            costs[day] += sample.cost
    return [DayTotal(day=day, query_count=counts[day], cost=costs[day]) for day in days]


@dataclass(frozen=True, slots=True)
class Baseline:
    """What is normal for a profile, per trace and per day.

    `profile_trace_count` is how many traces the profile has since its baseline
    started; below `LEARNING_TRACES` the profile is still learning.
    """

    computed_at: datetime
    profile_trace_count: int
    trace_sample_size: int
    day_sample_size: int
    trace_stats: Mapping[TraceMetric, MetricStats]
    day_stats: Mapping[DayMetric, MetricStats]

    @property
    def learning(self) -> bool:
        """Whether the profile has too few traces for anomaly alerts."""
        return self.profile_trace_count < LEARNING_TRACES

    @property
    def days_ready(self) -> bool:
        """Whether there are enough complete days for per-day anomaly alerts."""
        return self.day_sample_size >= MIN_BASELINE_DAYS


def compute_baseline(
    *,
    samples: Sequence[TraceSample],
    days: Sequence[DayTotal],
    profile_trace_count: int,
    previous: Baseline | None,
    critical_factor: Decimal,
    now: datetime,
) -> Baseline:
    """Baseline from recent traces and complete days.

    Values the previous baseline would call critical (above `critical_factor` x its
    p95) are left out, per metric, so anomalies do not inflate the new normal. If
    that would leave no values (the normal itself moved), all are kept.
    """
    trace_stats: dict[TraceMetric, MetricStats] = {}
    for trace_metric in TraceMetric:
        values = [v for sample in samples if (v := sample.value(trace_metric)) is not None]
        previous_stats = None if previous is None else previous.trace_stats.get(trace_metric)
        stats = MetricStats.of(_without_critical(values, previous_stats, critical_factor))
        if stats is not None:
            trace_stats[trace_metric] = stats

    day_stats: dict[DayMetric, MetricStats] = {}
    for day_metric in DayMetric:
        values = [total.value(day_metric) for total in days]
        previous_stats = None if previous is None else previous.day_stats.get(day_metric)
        stats = MetricStats.of(_without_critical(values, previous_stats, critical_factor))
        if stats is not None:
            day_stats[day_metric] = stats

    return Baseline(
        computed_at=now,
        profile_trace_count=profile_trace_count,
        trace_sample_size=len(samples),
        day_sample_size=len(days),
        trace_stats=trace_stats,
        day_stats=day_stats,
    )


def _without_critical(
    values: list[Decimal], previous: MetricStats | None, critical_factor: Decimal
) -> list[Decimal]:
    """`values` minus those above `critical_factor` x the previous p95 (if any remain)."""
    if previous is None or previous.p95 <= 0:
        return values
    limit = previous.p95 * critical_factor
    kept = [value for value in values if value <= limit]
    return kept or values


def anomaly_severity(value: Decimal, p95: Decimal, thresholds: Thresholds) -> Severity | None:
    """Severity of `value` against a p95; None if normal or if p95 is zero."""
    if p95 <= 0:
        return None
    if value > p95 * thresholds.critical_factor:
        return Severity.CRITICAL
    if value > p95 * thresholds.warning_factor:
        return Severity.WARNING
    return None


def _factor(severity: Severity, thresholds: Thresholds) -> Decimal:
    """The multiplier of p95 that a severity starts above."""
    if severity is Severity.CRITICAL:
        return thresholds.critical_factor
    return thresholds.warning_factor


AlertKey = tuple[int, AlertKind, AlertScope, AlertMetric, date | None]


@dataclass(frozen=True, slots=True)
class Alert:
    """A threshold crossed by a profile.

    Per-trace alerts group every offending trace of one evaluation: `observed` is the
    worst value, `worst_trace_id` its trace and `affected_count` how many traces
    crossed. Per-day alerts carry the `day` and its number of queries.
    """

    profile_id: int
    kind: AlertKind
    scope: AlertScope
    metric: AlertMetric
    severity: Severity
    observed: Decimal
    threshold: Decimal
    baseline_p95: Decimal | None
    day: date | None
    affected_count: int
    worst_trace_id: str | None
    created_at: datetime
    id: int | None = None
    notified_at: datetime | None = None

    @property
    def key(self) -> AlertKey:
        """What makes two alerts "the same" for the silence period."""
        return (self.profile_id, self.kind, self.scope, self.metric, self.day)


_TRACE_ALERT_METRICS = (
    (AlertMetric.COST, TraceMetric.COST),
    (AlertMetric.LATENCY, TraceMetric.LATENCY),
)
_DAY_ALERT_METRICS = (
    (AlertMetric.QUERY_COUNT, DayMetric.QUERY_COUNT),
    (AlertMetric.COST, DayMetric.COST),
)


def evaluate_traces(
    profile_id: int,
    samples: Sequence[TraceSample],
    baseline: Baseline | None,
    thresholds: Thresholds,
    now: datetime,
) -> list[Alert]:
    """Anomaly alerts for individual traces: at most one per metric (cost, latency).

    Nothing while learning. Cost alerts skip traces cheaper than `min_trace_cost`.
    """
    if baseline is None or baseline.learning:
        return []
    ordered = sorted(samples, key=lambda sample: (sample.timestamp, sample.id))
    alerts: list[Alert] = []
    for alert_metric, trace_metric in _TRACE_ALERT_METRICS:
        stats = baseline.trace_stats.get(trace_metric)
        if stats is None:
            continue
        minimum = thresholds.min_trace_cost if alert_metric is AlertMetric.COST else None
        hits = _trace_hits(ordered, trace_metric, stats.p95, thresholds, minimum)
        if hits:
            alerts.append(
                _grouped_trace_alert(profile_id, alert_metric, hits, stats, thresholds, now)
            )
    return alerts


_TraceHit = tuple[TraceSample, Decimal, Severity]


def _trace_hits(
    samples: Sequence[TraceSample],
    metric: TraceMetric,
    p95: Decimal,
    thresholds: Thresholds,
    minimum: Decimal | None,
) -> list[_TraceHit]:
    """Samples whose `metric` is anomalous, skipping missing values and those below `minimum`."""
    hits: list[_TraceHit] = []
    for sample in samples:
        value = sample.value(metric)
        if value is None or (minimum is not None and value < minimum):
            continue
        severity = anomaly_severity(value, p95, thresholds)
        if severity is not None:
            hits.append((sample, value, severity))
    return hits


def _grouped_trace_alert(
    profile_id: int,
    metric: AlertMetric,
    hits: Sequence[_TraceHit],
    stats: MetricStats,
    thresholds: Thresholds,
    now: datetime,
) -> Alert:
    """One alert for all `hits`: the most serious severity and the worst (earliest) trace."""
    worst_sample, worst_value, _ = max(hits, key=lambda hit: hit[1])
    severity = max((hit[2] for hit in hits), key=lambda s: s.rank)
    return Alert(
        profile_id=profile_id,
        kind=AlertKind.ANOMALY,
        scope=AlertScope.TRACE,
        metric=metric,
        severity=severity,
        observed=worst_value,
        threshold=stats.p95 * _factor(severity, thresholds),
        baseline_p95=stats.p95,
        day=None,
        affected_count=len(hits),
        worst_trace_id=worst_sample.id,
        created_at=now,
    )


def evaluate_days(
    profile_id: int,
    totals: Sequence[DayTotal],
    baseline: Baseline | None,
    thresholds: Thresholds,
    now: datetime,
) -> list[Alert]:
    """Budget and anomaly alerts for whole days (a day in progress counts so far).

    The budget always applies, also while learning and whatever its amount. Anomaly
    alerts need a baseline that is past learning and has enough complete days; cost
    anomalies skip days cheaper than `min_daily_cost`.
    """
    anomalies_on = baseline is not None and not baseline.learning and baseline.days_ready
    alerts: list[Alert] = []
    for total in totals:
        budget_alert = _budget_alert(profile_id, total, thresholds, now)
        if budget_alert is not None:
            alerts.append(budget_alert)
        if anomalies_on and baseline is not None:
            alerts.extend(_day_anomalies(profile_id, total, baseline, thresholds, now))
    return alerts


def _budget_alert(
    profile_id: int, total: DayTotal, thresholds: Thresholds, now: datetime
) -> Alert | None:
    """A critical alert if the day cost more than the daily budget; None otherwise."""
    budget = thresholds.daily_budget
    if budget is None or total.cost <= budget:
        return None
    return Alert(
        profile_id=profile_id,
        kind=AlertKind.BUDGET,
        scope=AlertScope.DAY,
        metric=AlertMetric.COST,
        severity=Severity.CRITICAL,
        observed=total.cost,
        threshold=budget,
        baseline_p95=None,
        day=total.day,
        affected_count=total.query_count,
        worst_trace_id=None,
        created_at=now,
    )


def _day_anomalies(
    profile_id: int, total: DayTotal, baseline: Baseline, thresholds: Thresholds, now: datetime
) -> list[Alert]:
    """Anomaly alerts for one day: number of queries and cost (above the minimum)."""
    alerts: list[Alert] = []
    for alert_metric, day_metric in _DAY_ALERT_METRICS:
        stats = baseline.day_stats.get(day_metric)
        value = total.value(day_metric)
        if stats is None or (
            alert_metric is AlertMetric.COST and value < thresholds.min_daily_cost
        ):
            continue
        severity = anomaly_severity(value, stats.p95, thresholds)
        if severity is None:
            continue
        alerts.append(
            Alert(
                profile_id=profile_id,
                kind=AlertKind.ANOMALY,
                scope=AlertScope.DAY,
                metric=alert_metric,
                severity=severity,
                observed=value,
                threshold=stats.p95 * _factor(severity, thresholds),
                baseline_p95=stats.p95,
                day=total.day,
                affected_count=total.query_count,
                worst_trace_id=None,
                created_at=now,
            )
        )
    return alerts


def apply_silence(
    candidates: Sequence[Alert], recent: Sequence[Alert], silence: timedelta, now: datetime
) -> list[Alert]:
    """Drop candidates already raised (same key) within `silence` before `now`.

    An earlier alert stops being "recent" exactly `silence` after it was created.
    A more serious candidate (warning -> critical) is kept even inside the period;
    an equal or less serious one is dropped.
    """
    kept: list[Alert] = []
    for candidate in candidates:
        silenced_by = [
            alert
            for alert in recent
            if alert.key == candidate.key and now - alert.created_at < silence
        ]
        if any(alert.severity.rank >= candidate.severity.rank for alert in silenced_by):
            continue
        kept.append(candidate)
    return kept
