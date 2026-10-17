"""`MonitoringRepository` backed by `traces`, `baselines` and `alerts`."""

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, Select, delete, func, insert, select, true
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.monitoring import (
    Alert,
    AlertKind,
    AlertMetric,
    AlertScope,
    Baseline,
    DayMetric,
    MetricStats,
    Severity,
    TraceMetric,
    TraceSample,
)
from app.infrastructure.database.errors import database_errors_as_repository_errors
from app.infrastructure.database.tables import AlertRow, BaselineRow, TraceRow

_TRACE_SCOPE = "trace"
_DAY_SCOPE = "day"

# Query returning the columns of a `TraceSample`.
_SampleSelect = Select[str, datetime, Decimal, int, int, float | None]


class SqlAlchemyMonitoringRepository:
    """Reads trace numbers per profile and stores baselines and alerts."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Use `session_factory` for connections."""
        self._session_factory = session_factory

    async def count_traces(self, profile_id: int, since: datetime | None) -> int:
        """Number of the profile's traces at or after `since`."""
        statement = select(func.count()).select_from(TraceRow).where(_of(profile_id, since))
        async with database_errors_as_repository_errors(), self._session_factory() as session:
            return (await session.execute(statement)).scalar_one()

    async def latest_traces(
        self, profile_id: int, since: datetime | None, limit: int
    ) -> Sequence[TraceSample]:
        """The `limit` most recent traces of the profile at or after `since`."""
        statement = (
            _samples()
            .where(_of(profile_id, since))
            .order_by(TraceRow.timestamp.desc(), TraceRow.id.desc())
            .limit(limit)
        )
        return await self._samples(statement)

    async def first_trace_at(self, profile_id: int, since: datetime | None) -> datetime | None:
        """Start of the profile's earliest trace at or after `since`."""
        statement = select(func.min(TraceRow.timestamp)).where(_of(profile_id, since))
        async with database_errors_as_repository_errors(), self._session_factory() as session:
            first: datetime | None = (await session.execute(statement)).scalar_one()
            return first

    async def traces_between(
        self, profile_id: int, start: datetime, end: datetime
    ) -> Sequence[TraceSample]:
        """The profile's traces that started in [start, end), oldest first."""
        statement = (
            _samples()
            .where(_of(profile_id, start), TraceRow.timestamp < end)
            .order_by(TraceRow.timestamp, TraceRow.id)
        )
        return await self._samples(statement)

    async def get_baseline(self, profile_id: int) -> Baseline | None:
        """The profile's stored baseline."""
        async with database_errors_as_repository_errors(), self._session_factory() as session:
            row = await session.get(BaselineRow, profile_id)
            return None if row is None else _baseline_from_row(row)

    async def save_baseline(self, profile_id: int, baseline: Baseline) -> None:
        """Insert or replace the profile's baseline."""
        values = {
            "profile_id": profile_id,
            "computed_at": baseline.computed_at,
            "profile_trace_count": baseline.profile_trace_count,
            "trace_sample_size": baseline.trace_sample_size,
            "day_sample_size": baseline.day_sample_size,
            "stats": _stats_to_json(baseline),
        }
        upsert = pg_insert(BaselineRow).values(values)
        upsert = upsert.on_conflict_do_update(
            index_elements=[BaselineRow.profile_id],
            set_={key: upsert.excluded[key] for key in values if key != "profile_id"},
        )
        async with (
            database_errors_as_repository_errors(),
            self._session_factory() as session,
            session.begin(),
        ):
            await session.execute(upsert)

    async def delete_baseline(self, profile_id: int) -> None:
        """Forget the profile's baseline."""
        statement = delete(BaselineRow).where(BaselineRow.profile_id == profile_id)
        async with (
            database_errors_as_repository_errors(),
            self._session_factory() as session,
            session.begin(),
        ):
            await session.execute(statement)

    async def alerts_since(self, profile_id: int, since: datetime) -> Sequence[Alert]:
        """The profile's alerts created at or after `since`, oldest first."""
        statement = (
            select(AlertRow)
            .where(AlertRow.profile_id == profile_id, AlertRow.created_at >= since)
            .order_by(AlertRow.created_at, AlertRow.id)
        )
        return await self._alerts(statement)

    async def save_alerts(self, alerts: Sequence[Alert]) -> None:
        """Insert the alerts."""
        if not alerts:
            return
        async with (
            database_errors_as_repository_errors(),
            self._session_factory() as session,
            session.begin(),
        ):
            await session.execute(insert(AlertRow), [_alert_values(alert) for alert in alerts])

    async def list_alerts(
        self, profile_id: int | None, since: datetime | None, limit: int
    ) -> Sequence[Alert]:
        """Up to `limit` alerts, newest first."""
        statement = select(AlertRow).order_by(AlertRow.created_at.desc(), AlertRow.id.desc())
        if profile_id is not None:
            statement = statement.where(AlertRow.profile_id == profile_id)
        if since is not None:
            statement = statement.where(AlertRow.created_at >= since)
        return await self._alerts(statement.limit(limit))

    async def _samples(self, statement: _SampleSelect) -> list[TraceSample]:
        """Run a `_samples()` query."""
        async with database_errors_as_repository_errors(), self._session_factory() as session:
            rows = await session.execute(statement)
            return [
                TraceSample(
                    id=row.id,
                    timestamp=row.timestamp,
                    cost=row.cost,
                    input_tokens=row.input_tokens,
                    output_tokens=row.output_tokens,
                    latency=row.latency,
                )
                for row in rows
            ]

    async def _alerts(self, statement: Select[AlertRow]) -> list[Alert]:
        """Run a query over `alerts`."""
        async with database_errors_as_repository_errors(), self._session_factory() as session:
            return [_alert_from_row(row) for row in await session.scalars(statement)]


def _samples() -> _SampleSelect:
    """Select the columns a `TraceSample` needs."""
    return select(
        TraceRow.id,
        TraceRow.timestamp,
        TraceRow.cost,
        TraceRow.input_tokens,
        TraceRow.output_tokens,
        TraceRow.latency,
    )


def _of(profile_id: int, since: datetime | None) -> ColumnElement[bool]:
    """Traces of the profile that started at or after `since` (any time if None)."""
    started = true() if since is None else TraceRow.timestamp >= since
    return (TraceRow.profile_id == profile_id) & started


def _stats_to_json(baseline: Baseline) -> dict[str, Any]:
    """Baseline statistics as JSON, decimals as strings."""

    def encode(stats: MetricStats) -> dict[str, Any]:
        return {
            "median": str(stats.median),
            "p95": str(stats.p95),
            "sample_size": stats.sample_size,
        }

    return {
        _TRACE_SCOPE: {metric.value: encode(s) for metric, s in baseline.trace_stats.items()},
        _DAY_SCOPE: {metric.value: encode(s) for metric, s in baseline.day_stats.items()},
    }


def _decode_stats(raw: dict[str, Any]) -> MetricStats:
    """`MetricStats` from its JSON form."""
    return MetricStats(
        median=Decimal(raw["median"]), p95=Decimal(raw["p95"]), sample_size=raw["sample_size"]
    )


def _baseline_from_row(row: BaselineRow) -> Baseline:
    """Domain baseline for a row."""
    return Baseline(
        computed_at=row.computed_at,
        profile_trace_count=row.profile_trace_count,
        trace_sample_size=row.trace_sample_size,
        day_sample_size=row.day_sample_size,
        trace_stats={
            TraceMetric(name): _decode_stats(raw)
            for name, raw in row.stats.get(_TRACE_SCOPE, {}).items()
        },
        day_stats={
            DayMetric(name): _decode_stats(raw)
            for name, raw in row.stats.get(_DAY_SCOPE, {}).items()
        },
    )


def _alert_values(alert: Alert) -> dict[str, Any]:
    """Column values of the `alerts` row for a new alert."""
    return {
        "profile_id": alert.profile_id,
        "kind": alert.kind.value,
        "scope": alert.scope.value,
        "metric": alert.metric.value,
        "severity": alert.severity.value,
        "observed": alert.observed,
        "threshold": alert.threshold,
        "baseline_p95": alert.baseline_p95,
        "day": alert.day,
        "affected_count": alert.affected_count,
        "worst_trace_id": alert.worst_trace_id,
        "created_at": alert.created_at,
        "notified_at": alert.notified_at,
    }


def _alert_from_row(row: AlertRow) -> Alert:
    """Domain alert for a row."""
    return Alert(
        id=row.id,
        profile_id=row.profile_id,
        kind=AlertKind(row.kind),
        scope=AlertScope(row.scope),
        metric=AlertMetric(row.metric),
        severity=Severity(row.severity),
        observed=row.observed,
        threshold=row.threshold,
        baseline_p95=row.baseline_p95,
        day=row.day,
        affected_count=row.affected_count,
        worst_trace_id=row.worst_trace_id,
        created_at=row.created_at,
        notified_at=row.notified_at,
    )
