from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from app.domain.models import Summary, TimeRange
from tests.unit.fakes import make_trace


@pytest.mark.parametrize(
    ("time_range", "expected"),
    [
        (TimeRange.LAST_24_HOURS, timedelta(hours=24)),
        (TimeRange.LAST_7_DAYS, timedelta(days=7)),
        (TimeRange.LAST_30_DAYS, timedelta(days=30)),
    ],
)
def test_time_range_duration(time_range: TimeRange, expected: timedelta) -> None:
    assert time_range.duration == expected


def test_time_range_values() -> None:
    assert [time_range.value for time_range in TimeRange] == ["24h", "7d", "30d"]


def test_trace_rejects_naive_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        make_trace(timestamp=datetime(2026, 1, 15, 12, 0))


@pytest.mark.parametrize(
    "overrides",
    [
        {"latency": -0.1},
        {"cost": Decimal("-0.01")},
        {"input_tokens": -1},
        {"output_tokens": -1},
        {"total_tokens": -1},
    ],
)
def test_trace_rejects_negative_values(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="negative"):
        make_trace(**overrides)


def test_trace_accepts_missing_latency() -> None:
    assert make_trace(latency=None).latency is None


def test_summary_of_no_traces() -> None:
    assert Summary.from_traces([]) == Summary(
        trace_count=0,
        total_cost=Decimal(0),
        total_tokens=0,
        average_latency=None,
    )


def test_summary_totals_and_average() -> None:
    traces = [
        make_trace(id="a", cost=Decimal("0.1"), input_tokens=10, output_tokens=5, latency=1.0),
        make_trace(id="b", cost=Decimal("0.2"), input_tokens=20, output_tokens=10, latency=2.0),
        make_trace(id="c", cost=Decimal("0.3"), input_tokens=30, output_tokens=15, latency=4.5),
    ]

    summary = Summary.from_traces(traces)

    assert summary.trace_count == 3
    assert summary.total_cost == Decimal("0.6")
    assert summary.total_tokens == 90
    assert summary.average_latency == pytest.approx(2.5)


def test_summary_uses_total_tokens_as_reported() -> None:
    traces = [make_trace(input_tokens=10, output_tokens=5, total_tokens=20)]

    assert Summary.from_traces(traces).total_tokens == 20


def test_summary_average_latency_ignores_missing_latencies() -> None:
    traces = [
        make_trace(id="a", latency=1.0),
        make_trace(id="b", latency=None),
        make_trace(id="c", latency=3.0),
    ]

    summary = Summary.from_traces(traces)

    assert summary.trace_count == 3
    assert summary.average_latency == pytest.approx(2.0)


def test_summary_average_latency_is_none_when_no_trace_reports_it() -> None:
    traces = [make_trace(id="a", latency=None), make_trace(id="b", latency=None)]

    assert Summary.from_traces(traces).average_latency is None
