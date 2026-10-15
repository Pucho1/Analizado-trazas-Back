from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.application.trace_service import TraceService
from app.core.exceptions import TraceSourceAuthenticationError, TraceSourceError
from app.domain.models import Summary, TimeRange
from tests.unit.fakes import FailingTraceSource, InMemoryTraceSource, make_trace

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


def fixed_clock() -> datetime:
    return NOW


@pytest.mark.parametrize("time_range", list(TimeRange))
async def test_requests_window_ending_now(time_range: TimeRange) -> None:
    source = InMemoryTraceSource()
    service = TraceService(source, clock=fixed_clock)

    await service.list_traces(time_range)

    assert source.calls == [(NOW - time_range.duration, NOW)]


async def test_default_clock_is_utc() -> None:
    source = InMemoryTraceSource()

    await TraceService(source).list_traces(TimeRange.LAST_24_HOURS)

    start, end = source.calls[0]
    assert start.tzinfo is UTC
    assert end.tzinfo is UTC


async def test_list_traces_returns_most_recent_first() -> None:
    oldest = make_trace(id="oldest", timestamp=NOW - timedelta(hours=3))
    newest = make_trace(id="newest", timestamp=NOW - timedelta(minutes=5))
    middle = make_trace(id="middle", timestamp=NOW - timedelta(hours=1))
    service = TraceService(InMemoryTraceSource([oldest, newest, middle]), clock=fixed_clock)

    traces = await service.list_traces(TimeRange.LAST_24_HOURS)

    assert [trace.id for trace in traces] == ["newest", "middle", "oldest"]


async def test_list_traces_excludes_traces_outside_range() -> None:
    inside = make_trace(id="inside", timestamp=NOW - timedelta(hours=23))
    outside = make_trace(id="outside", timestamp=NOW - timedelta(hours=25))
    service = TraceService(InMemoryTraceSource([inside, outside]), clock=fixed_clock)

    traces = await service.list_traces(TimeRange.LAST_24_HOURS)

    assert [trace.id for trace in traces] == ["inside"]


async def test_list_traces_with_no_traces() -> None:
    service = TraceService(InMemoryTraceSource(), clock=fixed_clock)

    assert await service.list_traces(TimeRange.LAST_7_DAYS) == []


async def test_get_summary_only_counts_traces_in_range() -> None:
    traces = [
        make_trace(id="a", timestamp=NOW - timedelta(days=1), cost=Decimal("0.25"), latency=2.0),
        make_trace(id="b", timestamp=NOW - timedelta(days=6), cost=Decimal("0.75"), latency=4.0),
        make_trace(id="old", timestamp=NOW - timedelta(days=8), cost=Decimal("10"), latency=99.0),
    ]
    service = TraceService(InMemoryTraceSource(traces), clock=fixed_clock)

    summary = await service.get_summary(TimeRange.LAST_7_DAYS)

    assert summary == Summary(
        trace_count=2,
        total_cost=Decimal("1.00"),
        total_tokens=30,
        average_latency=3.0,
    )


async def test_get_summary_with_no_traces() -> None:
    service = TraceService(InMemoryTraceSource(), clock=fixed_clock)

    summary = await service.get_summary(TimeRange.LAST_30_DAYS)

    assert summary == Summary.from_traces([])


@pytest.mark.parametrize(
    "error",
    [TraceSourceAuthenticationError("bad keys"), TraceSourceError("unavailable")],
)
async def test_source_errors_propagate(error: TraceSourceError) -> None:
    service = TraceService(FailingTraceSource(error), clock=fixed_clock)

    with pytest.raises(type(error)):
        await service.list_traces(TimeRange.LAST_24_HOURS)
    with pytest.raises(type(error)):
        await service.get_summary(TimeRange.LAST_24_HOURS)
