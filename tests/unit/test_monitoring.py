"""Baselines and thresholds (pure domain): edge cases."""

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.core.constants import BASELINE_DAYS, LEARNING_TRACES, MIN_BASELINE_DAYS
from app.domain.models import Thresholds
from app.domain.monitoring import (
    Alert,
    AlertKind,
    AlertMetric,
    AlertScope,
    Baseline,
    DayMetric,
    DayTotal,
    MetricStats,
    Severity,
    TraceMetric,
    TraceSample,
    anomaly_severity,
    apply_silence,
    baseline_days,
    compute_baseline,
    daily_totals,
    day_bounds,
    days_between,
    evaluate_days,
    evaluate_traces,
    local_day,
    percentile,
)

MADRID = ZoneInfo("Europe/Madrid")
NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)  # 14:00 in Madrid
D = Decimal


def sample(
    id: str = "t",
    *,
    cost: str = "0.001",
    latency: float | None = 1.0,
    input_tokens: int = 100,
    output_tokens: int = 10,
    timestamp: datetime = NOW,
) -> TraceSample:
    """A trace sample with defaults."""
    return TraceSample(
        id=id,
        timestamp=timestamp,
        cost=D(cost),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency=latency,
    )


def stats(p95: str, median: str = "0") -> MetricStats:
    """Stats with the given p95."""
    return MetricStats(median=D(median), p95=D(p95), sample_size=100)


def make_baseline(
    *,
    trace: dict[TraceMetric, MetricStats] | None = None,
    day: dict[DayMetric, MetricStats] | None = None,
    profile_trace_count: int = LEARNING_TRACES,
    day_sample_size: int = MIN_BASELINE_DAYS,
) -> Baseline:
    """A baseline past learning, with enough days, unless told otherwise."""
    return Baseline(
        computed_at=NOW,
        profile_trace_count=profile_trace_count,
        trace_sample_size=profile_trace_count,
        day_sample_size=day_sample_size,
        trace_stats=trace or {},
        day_stats=day or {},
    )


# --- percentile -------------------------------------------------------------------


@pytest.mark.parametrize("p", [0, 50, 95, 100])
def test_percentile_of_one_value_is_that_value(p: int) -> None:
    assert percentile([D(5)], p) == D(5)


@pytest.mark.parametrize(
    ("values", "p", "expected"),
    [
        ([1, 2], 50, "1.5"),
        ([1, 2], 95, "1.95"),
        ([3, 1, 5, 2, 4], 50, "3"),  # unsorted input
        ([3, 1, 5, 2, 4], 95, "4.8"),
        ([1, 1, 1, 10], 95, "8.65"),  # repeated values
        ([7, 7, 7], 95, "7"),  # all equal
        (list(range(1, 21)), 95, "19.05"),
        ([1, 2, 3, 4], 0, "1"),  # minimum
        ([1, 2, 3, 4], 100, "4"),  # maximum
    ],
)
def test_percentile_interpolates_linearly(values: list[int], p: int, expected: str) -> None:
    assert percentile([D(v) for v in values], p) == D(expected)


def test_percentile_is_exact_with_decimals() -> None:
    assert percentile([D("0.0001"), D("0.0003")], 50) == D("0.0002")


def test_percentile_of_nothing_fails() -> None:
    with pytest.raises(ValueError, match="no values"):
        percentile([], 50)


@pytest.mark.parametrize("p", [-1, 101])
def test_percentile_outside_range_fails(p: int) -> None:
    values = [D(1)]

    with pytest.raises(ValueError, match="between 0 and 100"):
        percentile(values, p)


def test_metric_stats() -> None:
    assert MetricStats.of([]) is None
    assert MetricStats.of([D(1), D(2), D(3)]) == MetricStats(
        median=D(2), p95=D("2.9"), sample_size=3
    )


# --- samples, days and time zones ---------------------------------------------------


def test_sample_values() -> None:
    trace = sample(cost="0.0002", latency=2.22, input_tokens=762, output_tokens=77)

    assert trace.value(TraceMetric.COST) == D("0.0002")
    assert trace.value(TraceMetric.LATENCY) == D("2.22")
    assert trace.value(TraceMetric.INPUT_TOKENS) == D(762)
    assert trace.value(TraceMetric.OUTPUT_TOKENS) == D(77)
    assert sample(latency=None).value(TraceMetric.LATENCY) is None


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (datetime(2026, 10, 17, 21, 59, 59, tzinfo=UTC), date(2026, 10, 17)),  # 23:59:59
        (datetime(2026, 10, 17, 22, 0, tzinfo=UTC), date(2026, 10, 18)),  # midnight
        (datetime(2026, 12, 31, 22, 59, tzinfo=UTC), date(2026, 12, 31)),  # winter: UTC+1
        (datetime(2026, 12, 31, 23, 0, tzinfo=UTC), date(2027, 1, 1)),
    ],
)
def test_local_day_cuts_at_madrid_midnight(moment: datetime, expected: date) -> None:
    assert local_day(moment, MADRID) == expected


@pytest.mark.parametrize(
    ("day", "hours"),
    [
        (date(2026, 10, 17), 24),
        (date(2026, 10, 25), 25),  # clocks go back
        (date(2026, 3, 29), 23),  # clocks go forward
    ],
)
def test_day_bounds_follow_clock_changes(day: date, hours: int) -> None:
    start, end = day_bounds(day, MADRID)

    assert end - start == timedelta(hours=hours)
    assert local_day(start, MADRID) == day
    assert local_day(end - timedelta(microseconds=1), MADRID) == day
    assert local_day(end, MADRID) == day + timedelta(days=1)


def test_days_between_includes_both_ends() -> None:
    start = datetime(2026, 10, 16, 21, 0, tzinfo=UTC)  # 23:00 on the 16th
    end = datetime(2026, 10, 17, 6, 0, tzinfo=UTC)  # 08:00 on the 17th

    assert days_between(start, end, MADRID) == [date(2026, 10, 16), date(2026, 10, 17)]
    assert days_between(end, end, MADRID) == [date(2026, 10, 17)]


def test_baseline_days_are_complete_days_up_to_yesterday() -> None:
    three_days_ago = NOW - timedelta(days=3)

    assert baseline_days(three_days_ago, NOW, MADRID) == [
        date(2026, 10, 14),
        date(2026, 10, 15),
        date(2026, 10, 16),
    ]


def test_baseline_days_edge_cases() -> None:
    assert baseline_days(None, NOW, MADRID) == []
    assert baseline_days(NOW - timedelta(hours=1), NOW, MADRID) == []  # only today
    old = baseline_days(NOW - timedelta(days=90), NOW, MADRID)
    assert len(old) == BASELINE_DAYS
    assert old[-1] == date(2026, 10, 16)


def test_daily_totals_count_empty_days_as_zero_and_ignore_other_days() -> None:
    days = [date(2026, 10, 15), date(2026, 10, 16)]
    samples = [
        sample("a", cost="0.5", timestamp=datetime(2026, 10, 16, 21, 59, tzinfo=UTC)),
        sample("b", cost="0.25", timestamp=datetime(2026, 10, 16, 8, 0, tzinfo=UTC)),
        sample("today", cost="9", timestamp=datetime(2026, 10, 16, 22, 0, tzinfo=UTC)),
    ]

    assert daily_totals(samples, days, MADRID) == [
        DayTotal(day=date(2026, 10, 15), query_count=0, cost=D(0)),
        DayTotal(day=date(2026, 10, 16), query_count=2, cost=D("0.75")),
    ]


# --- baseline -----------------------------------------------------------------------


def test_baseline_per_trace_and_per_day() -> None:
    samples = [sample(str(i), cost=str(i), latency=float(i)) for i in range(1, 6)]
    days = [DayTotal(date(2026, 10, d), query_count=d, cost=D(d)) for d in range(1, 8)]

    baseline = compute_baseline(
        samples=samples,
        days=days,
        profile_trace_count=5,
        previous=None,
        critical_factor=D(2),
        now=NOW,
    )

    assert baseline.trace_stats[TraceMetric.COST] == MetricStats(D(3), D("4.8"), 5)
    assert baseline.trace_stats[TraceMetric.LATENCY] == MetricStats(D(3), D("4.8"), 5)
    assert baseline.trace_stats[TraceMetric.INPUT_TOKENS] == MetricStats(D(100), D(100), 5)
    assert baseline.day_stats[DayMetric.QUERY_COUNT] == MetricStats(D(4), D("6.7"), 7)
    assert (baseline.trace_sample_size, baseline.day_sample_size) == (5, 7)


def test_baseline_without_latencies_has_no_latency_stats() -> None:
    baseline = compute_baseline(
        samples=[sample(latency=None)],
        days=[],
        profile_trace_count=1,
        previous=None,
        critical_factor=D(2),
        now=NOW,
    )

    assert TraceMetric.LATENCY not in baseline.trace_stats
    assert baseline.day_stats == {}


def test_baseline_of_nothing() -> None:
    baseline = compute_baseline(
        samples=[], days=[], profile_trace_count=0, previous=None, critical_factor=D(2), now=NOW
    )

    assert (baseline.trace_stats, baseline.day_stats, baseline.learning) == ({}, {}, True)


@pytest.mark.parametrize(("count", "learning"), [(0, True), (LEARNING_TRACES - 1, True)])
def test_learning_until_enough_traces(count: int, learning: bool) -> None:
    assert make_baseline(profile_trace_count=count).learning is learning


def test_learning_ends_at_exactly_the_target() -> None:
    assert make_baseline(profile_trace_count=LEARNING_TRACES).learning is False


@pytest.mark.parametrize(
    ("days", "ready"), [(0, False), (MIN_BASELINE_DAYS - 1, False), (MIN_BASELINE_DAYS, True)]
)
def test_days_ready(days: int, ready: bool) -> None:
    assert make_baseline(day_sample_size=days).days_ready is ready


def test_recalculation_leaves_out_previous_critical_values() -> None:
    previous = make_baseline(trace={TraceMetric.COST: stats("1")})
    samples = [sample("ok", cost="1"), sample("limit", cost="2"), sample("spike", cost="2.01")]

    baseline = compute_baseline(
        samples=samples,
        days=[],
        profile_trace_count=3,
        previous=previous,
        critical_factor=D(2),
        now=NOW,
    )

    assert baseline.trace_stats[TraceMetric.COST].sample_size == 2  # 2 x p95 itself stays
    assert baseline.trace_stats[TraceMetric.LATENCY].sample_size == 3  # other metrics intact


def test_recalculation_keeps_everything_if_the_normal_moved() -> None:
    previous = make_baseline(trace={TraceMetric.COST: stats("1")})
    samples = [sample(str(i), cost="5") for i in range(3)]

    baseline = compute_baseline(
        samples=samples,
        days=[],
        profile_trace_count=3,
        previous=previous,
        critical_factor=D(2),
        now=NOW,
    )

    assert baseline.trace_stats[TraceMetric.COST] == MetricStats(D(5), D(5), 3)


def test_recalculation_ignores_a_previous_zero_p95() -> None:
    previous = make_baseline(day={DayMetric.QUERY_COUNT: stats("0")})
    days = [DayTotal(date(2026, 10, d), query_count=d * 100, cost=D(0)) for d in range(1, 4)]

    baseline = compute_baseline(
        samples=[],
        days=days,
        profile_trace_count=0,
        previous=previous,
        critical_factor=D(2),
        now=NOW,
    )

    assert baseline.day_stats[DayMetric.QUERY_COUNT].sample_size == 3


def test_daily_exclusion_uses_the_previous_daily_p95() -> None:
    previous = make_baseline(day={DayMetric.QUERY_COUNT: stats("10")})
    days = [
        DayTotal(date(2026, 10, 1), query_count=5, cost=D(0)),
        DayTotal(date(2026, 10, 2), query_count=21, cost=D(0)),
    ]

    baseline = compute_baseline(
        samples=[],
        days=days,
        profile_trace_count=0,
        previous=previous,
        critical_factor=D(2),
        now=NOW,
    )

    assert baseline.day_stats[DayMetric.QUERY_COUNT].sample_size == 1
    assert baseline.day_stats[DayMetric.COST].sample_size == 2


# --- thresholds ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("10", None),  # equal to p95: normal
        ("10.01", Severity.WARNING),
        ("20", Severity.WARNING),  # exactly 2 x p95: still a warning
        ("20.0001", Severity.CRITICAL),
        ("0", None),
    ],
)
def test_anomaly_severity(value: str, expected: Severity | None) -> None:
    assert anomaly_severity(D(value), D(10), Thresholds()) is expected


def test_anomaly_severity_with_zero_p95_never_fires() -> None:
    assert anomaly_severity(D(1000), D(0), Thresholds()) is None


def test_anomaly_severity_with_custom_factors() -> None:
    thresholds = Thresholds(warning_factor=D("1.5"), critical_factor=D(3))

    assert anomaly_severity(D(15), D(10), thresholds) is None
    assert anomaly_severity(D("15.1"), D(10), thresholds) is Severity.WARNING
    assert anomaly_severity(D("30.1"), D(10), thresholds) is Severity.CRITICAL


@pytest.mark.parametrize(
    "build",
    [
        lambda: Thresholds(warning_factor=D(2), critical_factor=D(2)),
        lambda: Thresholds(warning_factor=D(0), critical_factor=D(2)),
        lambda: Thresholds(warning_factor=D(3), critical_factor=D(2)),
        lambda: Thresholds(daily_budget=D(0)),
        lambda: Thresholds(min_trace_cost=D("-0.01")),
        lambda: Thresholds(min_daily_cost=D("-1")),
        lambda: Thresholds(silence_hours=0),
    ],
    ids=[
        "equal-factors",
        "zero-warning",
        "inverted",
        "zero-budget",
        "neg-trace",
        "neg-day",
        "no-silence",
    ],
)
def test_invalid_thresholds(build: Callable[[], Thresholds]) -> None:
    with pytest.raises(ValueError):
        build()


def test_default_thresholds() -> None:
    thresholds = Thresholds()

    assert (thresholds.warning_factor, thresholds.critical_factor) == (D(1), D(2))
    assert thresholds.daily_budget is None
    assert (thresholds.min_trace_cost, thresholds.min_daily_cost) == (D("0.01"), D("0.10"))
    assert thresholds.silence == timedelta(hours=24)


# --- per-trace alerts ---------------------------------------------------------------

LATENCY_BASELINE = make_baseline(trace={TraceMetric.LATENCY: stats("2")})
COST_BASELINE = make_baseline(trace={TraceMetric.COST: stats("0.004")})


def test_no_trace_alerts_while_learning_or_without_baseline() -> None:
    spike = [sample(latency=100.0)]
    learning = make_baseline(
        trace={TraceMetric.LATENCY: stats("2")}, profile_trace_count=LEARNING_TRACES - 1
    )

    assert evaluate_traces(1, spike, learning, Thresholds(), NOW) == []
    assert evaluate_traces(1, spike, None, Thresholds(), NOW) == []


def test_offending_traces_are_grouped_into_one_alert_per_metric() -> None:
    samples = [
        sample("normal", latency=2.0),
        sample("warn", latency=3.0, timestamp=NOW - timedelta(minutes=2)),
        sample("crit", latency=5.0, timestamp=NOW - timedelta(minutes=1)),
        sample("warn2", latency=4.0),
    ]

    (alert,) = evaluate_traces(7, samples, LATENCY_BASELINE, Thresholds(), NOW)

    assert alert == Alert(
        profile_id=7,
        kind=AlertKind.ANOMALY,
        scope=AlertScope.TRACE,
        metric=AlertMetric.LATENCY,
        severity=Severity.CRITICAL,
        observed=D(5),
        threshold=D(4),
        baseline_p95=D(2),
        day=None,
        affected_count=3,
        worst_trace_id="crit",
        created_at=NOW,
    )


def test_warning_only_alert_reports_the_warning_threshold() -> None:
    (alert,) = evaluate_traces(1, [sample(latency=3.0)], LATENCY_BASELINE, Thresholds(), NOW)

    assert (alert.severity, alert.threshold) == (Severity.WARNING, D(2))


def test_worst_trace_tie_goes_to_the_earliest() -> None:
    samples = [
        sample("late", latency=9.0, timestamp=NOW),
        sample("early", latency=9.0, timestamp=NOW - timedelta(hours=1)),
    ]

    (alert,) = evaluate_traces(1, samples, LATENCY_BASELINE, Thresholds(), NOW)

    assert alert.worst_trace_id == "early"


def test_traces_without_latency_are_skipped() -> None:
    assert evaluate_traces(1, [sample(latency=None)], LATENCY_BASELINE, Thresholds(), NOW) == []


@pytest.mark.parametrize(
    ("cost", "alerts"),
    [
        ("0.0099", 0),  # above 2 x p95 but below the minimum
        ("0.01", 1),  # exactly the minimum: alerts
        ("0.02", 1),
    ],
)
def test_minimum_trace_cost(cost: str, alerts: int) -> None:
    found = evaluate_traces(1, [sample(cost=cost)], COST_BASELINE, Thresholds(), NOW)

    assert len(found) == alerts


def test_minimum_cost_does_not_apply_to_latency() -> None:
    baseline = make_baseline(
        trace={TraceMetric.COST: stats("0.00001"), TraceMetric.LATENCY: stats("1")}
    )

    (alert,) = evaluate_traces(1, [sample(cost="0.0001", latency=9.0)], baseline, Thresholds(), NOW)

    assert alert.metric is AlertMetric.LATENCY


def test_zero_minimum_lets_tiny_costs_alert() -> None:
    thresholds = Thresholds(min_trace_cost=D(0))
    baseline = make_baseline(trace={TraceMetric.COST: stats("0.0001")})

    (alert,) = evaluate_traces(1, [sample(cost="0.0003")], baseline, thresholds, NOW)

    assert (alert.metric, alert.severity) == (AlertMetric.COST, Severity.CRITICAL)


def test_tokens_never_raise_alerts() -> None:
    baseline = make_baseline(
        trace={TraceMetric.INPUT_TOKENS: stats("10"), TraceMetric.OUTPUT_TOKENS: stats("10")}
    )

    assert evaluate_traces(1, [sample(input_tokens=10_000)], baseline, Thresholds(), NOW) == []


# --- per-day alerts -----------------------------------------------------------------

TODAY = date(2026, 10, 17)
DAY_BASELINE = make_baseline(
    day={DayMetric.QUERY_COUNT: stats("10"), DayMetric.COST: stats("0.20")}
)


def day(queries: int = 5, cost: str = "0.05", on: date = TODAY) -> DayTotal:
    """A day's totals."""
    return DayTotal(day=on, query_count=queries, cost=D(cost))


@pytest.mark.parametrize(("cost", "fires"), [("1.00", False), ("1.0001", True)])
def test_budget_fires_strictly_above_the_amount(cost: str, fires: bool) -> None:
    thresholds = Thresholds(daily_budget=D(1))

    alerts = evaluate_days(1, [day(cost=cost)], None, thresholds, NOW)

    assert len(alerts) == int(fires)


def test_budget_fires_while_learning_and_ignores_the_minimum() -> None:
    thresholds = Thresholds(daily_budget=D("0.01"), min_daily_cost=D(5))
    learning = make_baseline(profile_trace_count=3)

    (alert,) = evaluate_days(1, [day(queries=4, cost="0.02")], learning, thresholds, NOW)

    assert alert == Alert(
        profile_id=1,
        kind=AlertKind.BUDGET,
        scope=AlertScope.DAY,
        metric=AlertMetric.COST,
        severity=Severity.CRITICAL,
        observed=D("0.02"),
        threshold=D("0.01"),
        baseline_p95=None,
        day=TODAY,
        affected_count=4,
        worst_trace_id=None,
        created_at=NOW,
    )


def test_no_budget_no_budget_alert() -> None:
    assert evaluate_days(1, [day(cost="1000")], None, Thresholds(), NOW) == []


def test_query_count_anomaly() -> None:
    (alert,) = evaluate_days(1, [day(queries=21)], DAY_BASELINE, Thresholds(), NOW)

    assert (alert.kind, alert.metric, alert.severity) == (
        AlertKind.ANOMALY,
        AlertMetric.QUERY_COUNT,
        Severity.CRITICAL,
    )
    assert (alert.observed, alert.threshold, alert.day) == (D(21), D(20), TODAY)


@pytest.mark.parametrize(
    "baseline",
    [
        make_baseline(day={DayMetric.QUERY_COUNT: stats("10")}, profile_trace_count=99),
        make_baseline(day={DayMetric.QUERY_COUNT: stats("10")}, day_sample_size=6),
    ],
    ids=["learning", "too-few-days"],
)
def test_no_daily_anomalies_until_learned_and_enough_days(baseline: Baseline) -> None:
    assert evaluate_days(1, [day(queries=500)], baseline, Thresholds(), NOW) == []


@pytest.mark.parametrize(
    ("cost", "alerts"),
    [
        ("0.0999", 0),  # above 2 x p95 (0.08) but below the minimum (0.10)
        ("0.10", 1),  # exactly the minimum: alerts
        ("0.41", 1),
    ],
)
def test_minimum_daily_cost(cost: str, alerts: int) -> None:
    baseline = make_baseline(day={DayMetric.COST: stats("0.04")})

    found = evaluate_days(1, [day(cost=cost)], baseline, Thresholds(), NOW)

    assert len(found) == alerts


def test_each_day_gets_its_own_alert() -> None:
    days = [day(queries=15, on=TODAY - timedelta(days=1)), day(queries=15)]

    alerts = evaluate_days(1, days, DAY_BASELINE, Thresholds(), NOW)

    assert [alert.day for alert in alerts] == [TODAY - timedelta(days=1), TODAY]


def test_zero_daily_p95_never_fires() -> None:
    baseline = make_baseline(day={DayMetric.QUERY_COUNT: stats("0")})

    assert evaluate_days(1, [day(queries=50)], baseline, Thresholds(), NOW) == []


# --- silence ------------------------------------------------------------------------


def alert(
    severity: Severity = Severity.WARNING,
    *,
    created_at: datetime = NOW,
    metric: AlertMetric = AlertMetric.LATENCY,
    scope: AlertScope = AlertScope.TRACE,
    on: date | None = None,
    profile_id: int = 1,
    kind: AlertKind = AlertKind.ANOMALY,
) -> Alert:
    """An alert with defaults."""
    return Alert(
        profile_id=profile_id,
        kind=kind,
        scope=scope,
        metric=metric,
        severity=severity,
        observed=D(5),
        threshold=D(2),
        baseline_p95=D(2),
        day=on,
        affected_count=1,
        worst_trace_id=None,
        created_at=created_at,
    )


SILENCE = timedelta(hours=24)


def test_first_alert_is_kept() -> None:
    assert apply_silence([alert()], [], SILENCE, NOW) == [alert()]


@pytest.mark.parametrize(
    ("age", "kept"),
    [
        (timedelta(hours=1), False),
        (SILENCE - timedelta(seconds=1), False),
        (SILENCE, True),  # exactly at the end of the period
        (SILENCE + timedelta(hours=1), True),
    ],
)
def test_same_alert_is_silenced_within_the_period(age: timedelta, kept: bool) -> None:
    previous = alert(created_at=NOW - age)

    assert apply_silence([alert()], [previous], SILENCE, NOW) == ([alert()] if kept else [])


def test_escalation_breaks_the_silence() -> None:
    previous = alert(Severity.WARNING, created_at=NOW - timedelta(hours=1))
    critical = alert(Severity.CRITICAL)

    assert apply_silence([critical], [previous], SILENCE, NOW) == [critical]


def test_a_lower_severity_stays_silenced() -> None:
    previous = alert(Severity.CRITICAL, created_at=NOW - timedelta(hours=1))

    assert apply_silence([alert(Severity.WARNING)], [previous], SILENCE, NOW) == []


@pytest.mark.parametrize(
    "different",
    [
        alert(metric=AlertMetric.COST),
        alert(profile_id=2),
        alert(kind=AlertKind.BUDGET),
        alert(scope=AlertScope.DAY, on=TODAY),
    ],
    ids=["metric", "profile", "kind", "scope"],
)
def test_other_alerts_do_not_silence(different: Alert) -> None:
    candidate = alert()

    recent = replace(different, created_at=NOW - timedelta(hours=1))

    assert apply_silence([candidate], [recent], SILENCE, NOW) == [candidate]


def test_a_new_day_is_a_new_alert() -> None:
    yesterday = alert(
        scope=AlertScope.DAY, on=TODAY - timedelta(days=1), created_at=NOW - timedelta(hours=2)
    )
    today = alert(scope=AlertScope.DAY, on=TODAY)

    assert apply_silence([today], [yesterday], SILENCE, NOW) == [today]


def test_rescanning_the_same_trace_does_not_duplicate() -> None:
    samples = [sample("spike", latency=9.0)]
    first = evaluate_traces(1, samples, LATENCY_BASELINE, Thresholds(), NOW - timedelta(hours=6))

    again = evaluate_traces(1, samples, LATENCY_BASELINE, Thresholds(), NOW)

    assert apply_silence(again, first, SILENCE, NOW) == []
