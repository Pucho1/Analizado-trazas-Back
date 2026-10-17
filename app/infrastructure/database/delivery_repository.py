"""`DeliveryRepository` backed by the `deliveries` table."""

from collections.abc import Sequence
from datetime import date

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.notifications import Delivery, DeliveryKind, DeliveryStatus, NotificationFailure
from app.domain.reporting import Channel
from app.infrastructure.database.errors import database_errors_as_repository_errors
from app.infrastructure.database.tables import DeliveryRow


class SqlAlchemyDeliveryRepository:
    """Stores every delivery attempt."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Use `session_factory` for connections."""
        self._session_factory = session_factory

    async def record(self, delivery: Delivery) -> None:
        """Insert the delivery.

        A second successful daily report for the same channel and day violates a
        unique index and surfaces as `RepositoryError`.
        """
        row = DeliveryRow(
            channel=delivery.channel.value,
            kind=delivery.kind.value,
            day=delivery.day,
            status=delivery.status.value,
            error_code=None if delivery.error_code is None else delivery.error_code.value,
            created_at=delivery.created_at,
        )
        async with (
            database_errors_as_repository_errors(),
            self._session_factory() as session,
            session.begin(),
        ):
            session.add(row)

    async def daily_report_sent(self, channel: Channel, day: date) -> bool:
        """Whether a successful daily report about `day` went through `channel`."""
        statement = select(
            exists().where(
                DeliveryRow.channel == channel.value,
                DeliveryRow.kind == DeliveryKind.DAILY_REPORT.value,
                DeliveryRow.day == day,
                DeliveryRow.status == DeliveryStatus.SENT.value,
            )
        )
        async with database_errors_as_repository_errors(), self._session_factory() as session:
            return bool((await session.execute(statement)).scalar_one())

    async def list_deliveries(self, limit: int) -> Sequence[Delivery]:
        """The `limit` most recent deliveries, newest first."""
        statement = (
            select(DeliveryRow)
            .order_by(DeliveryRow.created_at.desc(), DeliveryRow.id.desc())
            .limit(limit)
        )
        async with database_errors_as_repository_errors(), self._session_factory() as session:
            return [_to_domain(row) for row in await session.scalars(statement)]


def _to_domain(row: DeliveryRow) -> Delivery:
    """Domain delivery for a row."""
    return Delivery(
        id=row.id,
        channel=Channel(row.channel),
        kind=DeliveryKind(row.kind),
        status=DeliveryStatus(row.status),
        created_at=row.created_at,
        day=row.day,
        error_code=None if row.error_code is None else NotificationFailure(row.error_code),
    )
