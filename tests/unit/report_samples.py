"""Hand-made `ReportData` for section and presenter tests."""

from datetime import UTC, date, datetime
from decimal import Decimal

from app.application.reports.data import ProfileDay, ReportData, TypicalDay
from app.domain.events import EventCount, EventSummary
from app.domain.models import ProfileRule
from app.domain.monitoring import Alert, AlertKind, AlertMetric, AlertScope, Severity
from app.domain.spend import DayRange, PeriodComparison, PeriodKind, SpendTotals
from tests.unit.fakes import make_profile

DAY = date(2026, 10, 16)  # a Friday
D = Decimal


def totals(queries: int = 0, cost: str = "0", inp: int = 0, out: int = 0) -> SpendTotals:
    """Totals with defaults."""
    return SpendTotals(queries, D(cost), inp, out, inp + out)


def events(
    *items: tuple[str, int, int],
    errors: tuple[int, int] = (0, 0),
    warnings: tuple[int, int] = (0, 0),
) -> EventSummary:
    """An event summary from (label, occurrences, queries) tuples."""
    return EventSummary(
        events=tuple(EventCount(label, n, q) for label, n, q in items),
        technical_errors=EventCount("Errores técnicos", *errors),
        technical_warnings=EventCount("Avisos técnicos", *warnings),
    )


def alert(
    severity: Severity = Severity.WARNING,
    *,
    kind: AlertKind = AlertKind.ANOMALY,
    scope: AlertScope = AlertScope.TRACE,
    metric: AlertMetric = AlertMetric.LATENCY,
    observed: str = "10",
    threshold: str = "2.4",
    p95: str | None = "1.2",
    affected: int = 3,
) -> Alert:
    """An alert about `DAY`."""
    return Alert(
        profile_id=1,
        kind=kind,
        scope=scope,
        metric=metric,
        severity=severity,
        observed=D(observed),
        threshold=D(threshold),
        baseline_p95=None if p95 is None else D(p95),
        day=DAY if scope is AlertScope.DAY else None,
        affected_count=affected,
        worst_trace_id="t1",
        created_at=datetime(2026, 10, 16, 12, tzinfo=UTC),
        id=1,
    )


def profile_day(
    name: str = "Asistente del portafolio",
    *,
    today: SpendTotals | None = None,
    previous: SpendTotals | None = None,
    typical: TypicalDay | None = None,
    trace_count: int = 500,
    summary: EventSummary | None = None,
    alerts: tuple[Alert, ...] = (),
    profile_id: int = 1,
) -> ProfileDay:
    """One profile's day; by default 230 queries for 1,40 $, past learning, no events."""
    return ProfileDay(
        profile=make_profile(id=profile_id, name=name, rule=ProfileRule(environment="production")),
        totals=today if today is not None else totals(230, "1.40", 150_000, 20_000),
        previous=previous if previous is not None else totals(200, "1.20"),
        typical=typical or TypicalDay(queries=D(170), cost=D("1.0370")),
        trace_count=trace_count,
        events=summary or events(),
        alerts=alerts,
    )


def comparisons(
    current: SpendTotals | None = None, previous: SpendTotals | None = None
) -> tuple[PeriodComparison, ...]:
    """Day, week and month comparisons with the same totals."""
    current = current or totals(230, "1.40")
    previous = previous or totals(170, "1.0370")
    ranges = {
        PeriodKind.DAY: (DayRange(DAY, DAY), DayRange(date(2026, 10, 15), date(2026, 10, 15))),
        PeriodKind.WEEK: (
            DayRange(date(2026, 10, 12), DAY),
            DayRange(date(2026, 10, 5), date(2026, 10, 9)),
        ),
        PeriodKind.MONTH: (
            DayRange(date(2026, 10, 1), DAY),
            DayRange(date(2026, 9, 1), date(2026, 9, 16)),
        ),
    }
    return tuple(
        PeriodComparison(period, now, before, current, previous)
        for period, (now, before) in ranges.items()
    )


def report_data(
    *profiles: ProfileDay,
    unclassified: SpendTotals | None = None,
    unclassified_previous: SpendTotals | None = None,
    periods: tuple[PeriodComparison, ...] | None = None,
) -> ReportData:
    """Report data for `DAY`."""
    return ReportData(
        day=DAY,
        profiles=profiles,
        unclassified=unclassified or totals(),
        unclassified_previous=unclassified_previous or totals(),
        comparisons=periods if periods is not None else comparisons(),
    )
