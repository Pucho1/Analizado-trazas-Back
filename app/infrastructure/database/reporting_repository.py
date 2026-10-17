"""Repositories for spend aggregates, event mappings and report settings."""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime

from sqlalchemy import delete, func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.constants import DEFAULT_EMAIL_SECTIONS, DEFAULT_TELEGRAM_SECTIONS
from app.domain.events import EventMapping, StepCount
from app.domain.models import ObservationLevel
from app.domain.reporting import Channel, ChannelSettings
from app.domain.spend import SpendTotals
from app.infrastructure.database.errors import database_errors_as_repository_errors
from app.infrastructure.database.tables import (
    EventMappingRow,
    ObservationRow,
    ReportSettingsRow,
    TraceRow,
)

_DEFAULT_SECTIONS = {
    Channel.EMAIL: DEFAULT_EMAIL_SECTIONS,
    Channel.TELEGRAM: DEFAULT_TELEGRAM_SECTIONS,
}


class SqlAlchemySpendRepository:
    """Spend totals and step counts computed by PostgreSQL."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Use `session_factory` for connections."""
        self._session_factory = session_factory

    async def totals_by_profile(
        self, start: datetime, end: datetime
    ) -> Mapping[int | None, SpendTotals]:
        """Totals per profile id of the traces that started in [start, end)."""
        statement = (
            select(
                TraceRow.profile_id,
                func.count(),
                func.sum(TraceRow.cost),
                func.sum(TraceRow.input_tokens),
                func.sum(TraceRow.output_tokens),
                func.sum(TraceRow.total_tokens),
            )
            .where(TraceRow.timestamp >= start, TraceRow.timestamp < end)
            .group_by(TraceRow.profile_id)
        )
        async with database_errors_as_repository_errors(), self._session_factory() as session:
            rows = await session.execute(statement)
            return {
                profile_id: SpendTotals(
                    queries=count,
                    cost=cost,
                    input_tokens=int(input_tokens),
                    output_tokens=int(output_tokens),
                    total_tokens=int(total_tokens),
                )
                for profile_id, count, cost, input_tokens, output_tokens, total_tokens in rows
            }

    async def step_counts(
        self, profile_id: int, start: datetime | None, end: datetime | None
    ) -> Sequence[StepCount]:
        """Named steps of the profile's traces (started in [start, end)) by name and level."""
        statement = (
            select(ObservationRow.name, ObservationRow.level, ObservationRow.trace_id, func.count())
            .join(TraceRow, TraceRow.id == ObservationRow.trace_id)
            .where(TraceRow.profile_id == profile_id, ObservationRow.name.is_not(None))
            .group_by(ObservationRow.name, ObservationRow.level, ObservationRow.trace_id)
        )
        if start is not None:
            statement = statement.where(TraceRow.timestamp >= start)
        if end is not None:
            statement = statement.where(TraceRow.timestamp < end)
        occurrences: dict[tuple[str, str], int] = defaultdict(int)
        traces: dict[tuple[str, str], set[str]] = defaultdict(set)
        async with database_errors_as_repository_errors(), self._session_factory() as session:
            for name, level, trace_id, count in await session.execute(statement):
                if name is None:  # excluded by the query; narrows the type
                    continue
                occurrences[(name, level)] += count
                traces[(name, level)].add(trace_id)
        return [
            StepCount(
                name=name,
                level=ObservationLevel(level),
                occurrences=occurrences[(name, level)],
                trace_ids=frozenset(traces[(name, level)]),
            )
            for name, level in sorted(occurrences)
        ]


class SqlAlchemyEventMappingRepository:
    """Event mappings in the `event_mappings` table."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Use `session_factory` for connections."""
        self._session_factory = session_factory

    async def list_mappings(self, profile_id: int) -> Sequence[EventMapping]:
        """The profile's mappings, by step name."""
        statement = (
            select(EventMappingRow)
            .where(EventMappingRow.profile_id == profile_id)
            .order_by(EventMappingRow.observation_name)
        )
        async with database_errors_as_repository_errors(), self._session_factory() as session:
            return [
                EventMapping(row.observation_name, row.label)
                for row in await session.scalars(statement)
            ]

    async def replace_mappings(self, profile_id: int, mappings: Sequence[EventMapping]) -> None:
        """Delete the profile's mappings and insert `mappings`, in one transaction."""
        async with (
            database_errors_as_repository_errors(),
            self._session_factory() as session,
            session.begin(),
        ):
            await session.execute(
                delete(EventMappingRow).where(EventMappingRow.profile_id == profile_id)
            )
            if mappings:
                await session.execute(
                    insert(EventMappingRow),
                    [
                        {
                            "profile_id": profile_id,
                            "observation_name": mapping.observation_name,
                            "label": mapping.label,
                        }
                        for mapping in mappings
                    ],
                )


class SqlAlchemyReportSettingsRepository:
    """Per-channel report settings in the `report_settings` table."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Use `session_factory` for connections."""
        self._session_factory = session_factory

    async def get(self, channel: Channel) -> ChannelSettings:
        """The channel's stored settings, or the defaults (enabled, default sections)."""
        async with database_errors_as_repository_errors(), self._session_factory() as session:
            row = await session.get(ReportSettingsRow, channel.value)
        if row is None:
            return ChannelSettings(channel, enabled=True, sections=_DEFAULT_SECTIONS[channel])
        return ChannelSettings(
            channel,
            enabled=row.enabled,
            sections=tuple(row.sections),
            alerts_enabled=row.alerts_enabled,
            scan_summary_enabled=row.scan_summary_enabled,
        )

    async def save(self, settings: ChannelSettings) -> None:
        """Insert or replace the channel's settings."""
        values = {
            "channel": settings.channel.value,
            "enabled": settings.enabled,
            "sections": list(settings.sections),
            "alerts_enabled": settings.alerts_enabled,
            "scan_summary_enabled": settings.scan_summary_enabled,
        }
        upsert = pg_insert(ReportSettingsRow).values(values)
        upsert = upsert.on_conflict_do_update(
            index_elements=[ReportSettingsRow.channel],
            set_={key: upsert.excluded[key] for key in values if key != "channel"},
        )
        async with (
            database_errors_as_repository_errors(),
            self._session_factory() as session,
            session.begin(),
        ):
            await session.execute(upsert)
