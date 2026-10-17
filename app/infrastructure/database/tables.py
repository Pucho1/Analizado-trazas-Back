"""Relational schema (SQLAlchemy 2.0). Alembic migrations are generated from it.

Only metrics and metadata are stored, never the inputs or outputs of a trace.
"""

from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    Numeric,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.domain.models import ObservationLevel, ProfileStatus, SyncStatus, SyncTrigger

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


class Base(DeclarativeBase):
    """Declarative base holding the metadata of every table."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class ProfileRow(Base):
    """A profile: a business name plus the rule that selects its traces."""

    __tablename__ = "profiles"
    __table_args__ = (
        CheckConstraint(f"status IN ({_PROFILE_STATUSES_SQL})", name="status"),
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


class TraceRow(Base):
    """One trace. `cost` is in USD and `latency` in seconds.

    `profile_id` is NULL while no profile captures the trace ("unclassified");
    deleting a profile deletes its traces.
    """

    __tablename__ = "traces"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    profile_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("profiles.id", ondelete="CASCADE"), index=True
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
