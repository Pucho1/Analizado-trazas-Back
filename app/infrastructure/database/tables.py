"""Relational schema (SQLAlchemy 2.0). Alembic migrations are generated from it.

Only metrics and metadata are stored, never the inputs or outputs of a trace.
"""

from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Double,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.core.constants import (
    DEFAULT_CRITICAL_FACTOR,
    DEFAULT_MIN_DAILY_COST,
    DEFAULT_MIN_TRACE_COST,
    DEFAULT_SILENCE_HOURS,
    DEFAULT_WARNING_FACTOR,
)
from app.domain.models import ObservationLevel, ProfileStatus, SyncStatus, SyncTrigger
from app.domain.monitoring import AlertKind, AlertMetric, AlertScope, Severity
from app.domain.notifications import DeliveryKind, DeliveryStatus
from app.domain.reporting import Channel

# Stable constraint and index names, so that migrations are reproducible.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def _sql_values(values: Iterable[StrEnum]) -> str:
    """Enum values as a SQL list for a CHECK constraint, e.g. `'a', 'b'`."""
    return ", ".join(f"'{value.value}'" for value in values)


_LEVELS_SQL = _sql_values(ObservationLevel)
_TRIGGERS_SQL = _sql_values(SyncTrigger)
_STATUSES_SQL = _sql_values(SyncStatus)
_PROFILE_STATUSES_SQL = _sql_values(ProfileStatus)
# Every child of a profile is deleted with it.
_PROFILE_ID = "profiles.id"


class Base(DeclarativeBase):
    """Declarative base holding the metadata of every table."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class ProfileRow(Base):
    """A profile: a business name plus the rule that selects its traces."""

    __tablename__ = "profiles"
    __table_args__ = (
        CheckConstraint(f"status IN ({_PROFILE_STATUSES_SQL})", name="status"),
        CheckConstraint("0 < warning_factor AND warning_factor < critical_factor", name="factors"),
        CheckConstraint("daily_budget IS NULL OR daily_budget > 0", name="daily_budget"),
        CheckConstraint("min_trace_cost >= 0 AND min_daily_cost >= 0", name="min_costs"),
        CheckConstraint("silence_hours > 0", name="silence_hours"),
        # Names are unique ignoring case ("Web" and "web" are the same profile name).
        Index("uq_profiles_name_lower", func.lower(text("name")), unique=True),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    environment: Mapped[str] = mapped_column(Text)
    trace_name: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    status: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Relearning moves this date: the baseline only uses later traces.
    baseline_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Alert thresholds (see `Thresholds`); amounts in USD.
    warning_factor: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), server_default=text(str(DEFAULT_WARNING_FACTOR))
    )
    critical_factor: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), server_default=text(str(DEFAULT_CRITICAL_FACTOR))
    )
    daily_budget: Mapped[Decimal | None] = mapped_column(Numeric(20, 12))
    min_trace_cost: Mapped[Decimal] = mapped_column(
        Numeric(20, 12), server_default=text(str(DEFAULT_MIN_TRACE_COST))
    )
    min_daily_cost: Mapped[Decimal] = mapped_column(
        Numeric(20, 12), server_default=text(str(DEFAULT_MIN_DAILY_COST))
    )
    silence_hours: Mapped[int] = mapped_column(
        Integer, server_default=text(str(DEFAULT_SILENCE_HOURS))
    )


class BaselineRow(Base):
    """The latest baseline of a profile (replaced on every recalculation).

    `stats` holds `{"trace"|"day": {metric: {"median", "p95", "sample_size"}}}` with
    the decimals as strings, so they stay exact.
    """

    __tablename__ = "baselines"

    profile_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(_PROFILE_ID, ondelete="CASCADE"), primary_key=True
    )
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    profile_trace_count: Mapped[int] = mapped_column(Integer)
    trace_sample_size: Mapped[int] = mapped_column(Integer)
    day_sample_size: Mapped[int] = mapped_column(Integer)
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB)


class AlertRow(Base):
    """A threshold crossed by a profile; `notified_at` is set once it has been sent."""

    __tablename__ = "alerts"
    __table_args__ = (
        CheckConstraint(f"kind IN ({_sql_values(AlertKind)})", name="kind"),
        CheckConstraint(f"scope IN ({_sql_values(AlertScope)})", name="scope"),
        CheckConstraint(f"metric IN ({_sql_values(AlertMetric)})", name="metric"),
        CheckConstraint(f"severity IN ({_sql_values(Severity)})", name="severity"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(_PROFILE_ID, ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(Text)
    metric: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text)
    observed: Mapped[Decimal] = mapped_column(Numeric(30, 12))
    threshold: Mapped[Decimal] = mapped_column(Numeric(30, 12))
    baseline_p95: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    day: Mapped[date | None] = mapped_column(Date)
    affected_count: Mapped[int] = mapped_column(Integer)
    worst_trace_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EventMappingRow(Base):
    """A profile's business label for steps with a given name."""

    __tablename__ = "event_mappings"
    __table_args__ = (UniqueConstraint("profile_id", "observation_name"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(_PROFILE_ID, ondelete="CASCADE"), index=True
    )
    observation_name: Mapped[str] = mapped_column(Text)
    label: Mapped[str] = mapped_column(Text)


class ReportSettingsRow(Base):
    """Sections (ordered keys) of one channel's daily report."""

    __tablename__ = "report_settings"
    __table_args__ = (CheckConstraint(f"channel IN ({_sql_values(Channel)})", name="channel"),)

    channel: Mapped[str] = mapped_column(Text, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean)
    sections: Mapped[list[str]] = mapped_column(ARRAY(Text))
    alerts_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    scan_summary_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))


class DeliveryRow(Base):
    """One attempt to send something through a channel.

    The partial unique index makes a second successful daily report for the same
    channel and day impossible.
    """

    __tablename__ = "deliveries"
    __table_args__ = (
        CheckConstraint(f"channel IN ({_sql_values(Channel)})", name="channel"),
        CheckConstraint(f"kind IN ({_sql_values(DeliveryKind)})", name="kind"),
        CheckConstraint(f"status IN ({_sql_values(DeliveryStatus)})", name="status"),
        Index(
            "uq_deliveries_daily_report",
            "channel",
            "day",
            unique=True,
            postgresql_where=text(
                f"kind = '{DeliveryKind.DAILY_REPORT.value}' "
                f"AND status = '{DeliveryStatus.SENT.value}'"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    channel: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    day: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class TraceRow(Base):
    """One trace. `cost` is in USD and `latency` in seconds.

    `profile_id` is NULL while no profile captures the trace ("unclassified");
    deleting a profile deletes its traces.
    """

    __tablename__ = "traces"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    profile_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey(_PROFILE_ID, ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    environment: Mapped[str] = mapped_column(Text, index=True)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    latency: Mapped[float | None] = mapped_column(Double)
    cost: Mapped[Decimal] = mapped_column(Numeric(20, 12))
    input_tokens: Mapped[int] = mapped_column(BigInteger)
    output_tokens: Mapped[int] = mapped_column(BigInteger)
    total_tokens: Mapped[int] = mapped_column(BigInteger)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    observations: Mapped[list[ObservationRow]] = relationship(
        back_populates="trace",
        order_by="(ObservationRow.start_time, ObservationRow.id)",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class ObservationRow(Base):
    """One step of a trace: metadata only."""

    __tablename__ = "observations"
    __table_args__ = (CheckConstraint(f"level IN ({_LEVELS_SQL})", name="level"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    trace_id: Mapped[str] = mapped_column(
        Text, ForeignKey("traces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str | None] = mapped_column(Text)
    type: Mapped[str] = mapped_column(Text)
    level: Mapped[str] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    trace: Mapped[TraceRow] = relationship(back_populates="observations")


class SyncRunRow(Base):
    """One synchronisation run; the last successful one per source is the sync marker."""

    __tablename__ = "sync_runs"
    __table_args__ = (
        CheckConstraint(f"trigger IN ({_TRIGGERS_SQL})", name="trigger"),
        CheckConstraint(f"status IN ({_STATUSES_SQL})", name="status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    source: Mapped[str] = mapped_column(Text)
    trigger: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    trace_count: Mapped[int] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(Text)
