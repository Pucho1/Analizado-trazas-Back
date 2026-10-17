"""`ProfileRepository` backed by the `profiles` table and `traces.profile_id`."""

from collections.abc import Mapping, Sequence
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import ProfileNameTakenError, ProfileNotFoundError
from app.domain.models import DiscoveredGroup, Profile, ProfileRule, ProfileStatus, TraceKey
from app.infrastructure.database.errors import database_errors_as_repository_errors
from app.infrastructure.database.tables import ProfileRow, TraceRow

_NAME_INDEX = "uq_profiles_name_lower"


class SqlAlchemyProfileRepository:
    """Stores profiles and which profile each stored trace belongs to."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Use `session_factory` for connections."""
        self._session_factory = session_factory

    async def add(self, name: str, rule: ProfileRule, created_at: datetime) -> Profile:
        """Insert an active profile and return it with its new id."""
        row = ProfileRow(
            name=name,
            environment=rule.environment,
            trace_name=rule.trace_name,
            tags=list(rule.tags),
            status=ProfileStatus.ACTIVE.value,
            created_at=created_at,
            archived_at=None,
        )
        async with database_errors_as_repository_errors(), self._session_factory() as session:
            session.add(row)
            await _commit_unless_name_taken(session, name)
            return _to_domain(row)

    async def get(self, profile_id: int) -> Profile | None:
        """The profile with that id, or None."""
        async with database_errors_as_repository_errors(), self._session_factory() as session:
            row = await session.get(ProfileRow, profile_id)
            return None if row is None else _to_domain(row)

    async def list_profiles(self) -> Sequence[Profile]:
        """Every profile, oldest first."""
        statement = select(ProfileRow).order_by(ProfileRow.created_at, ProfileRow.id)
        async with database_errors_as_repository_errors(), self._session_factory() as session:
            return [_to_domain(row) for row in await session.scalars(statement)]

    async def update(self, profile: Profile) -> None:
        """Overwrite name, rule, status and archive date of an existing profile."""
        async with database_errors_as_repository_errors(), self._session_factory() as session:
            row = await session.get(ProfileRow, profile.id)
            if row is None:
                raise ProfileNotFoundError(f"Profile {profile.id} does not exist")
            row.name = profile.name
            row.environment = profile.rule.environment
            row.trace_name = profile.rule.trace_name
            row.tags = list(profile.rule.tags)
            row.status = profile.status.value
            row.archived_at = profile.archived_at
            await _commit_unless_name_taken(session, profile.name)

    async def delete(self, profile_id: int) -> bool:
        """Delete the profile; the database deletes its traces and observations."""
        statement = delete(ProfileRow).where(ProfileRow.id == profile_id)
        async with (
            database_errors_as_repository_errors(),
            self._session_factory() as session,
            session.begin(),
        ):
            result = await session.execute(statement)
            return bool(result.rowcount)  # type: ignore[attr-defined]

    async def list_trace_keys(self) -> Sequence[TraceKey]:
        """Every stored trace with environment, name, tags and current profile."""
        statement = select(
            TraceRow.id, TraceRow.environment, TraceRow.name, TraceRow.tags, TraceRow.profile_id
        )
        async with database_errors_as_repository_errors(), self._session_factory() as session:
            rows = await session.execute(statement)
            return [
                TraceKey(
                    id=row.id,
                    environment=row.environment,
                    name=row.name,
                    tags=tuple(row.tags),
                    profile_id=row.profile_id,
                )
                for row in rows
            ]

    async def assign_profiles(self, assignments: Mapping[str, int | None]) -> None:
        """Set `profile_id` of each trace id given, in one transaction."""
        if not assignments:
            return
        values = [
            {"id": trace_id, "profile_id": profile_id}
            for trace_id, profile_id in assignments.items()
        ]
        async with (
            database_errors_as_repository_errors(),
            self._session_factory() as session,
            session.begin(),
        ):
            await session.execute(update(TraceRow), values)

    async def count_traces_by_profile(self) -> Mapping[int | None, int]:
        """Number of traces per profile id (None = unclassified)."""
        statement = select(TraceRow.profile_id, func.count()).group_by(TraceRow.profile_id)
        async with database_errors_as_repository_errors(), self._session_factory() as session:
            rows = await session.execute(statement)
            return dict(rows.all())

    async def discover_groups(self) -> Sequence[DiscoveredGroup]:
        """Unclassified traces grouped by environment, name and tags, largest first."""
        trace_count = func.count().label("trace_count")
        statement = (
            select(
                TraceRow.environment,
                TraceRow.name,
                TraceRow.tags,
                trace_count,
                func.min(TraceRow.timestamp),
                func.max(TraceRow.timestamp),
                func.sum(TraceRow.cost),
            )
            .where(TraceRow.profile_id.is_(None))
            .group_by(TraceRow.environment, TraceRow.name, TraceRow.tags)
            .order_by(trace_count.desc(), TraceRow.environment, TraceRow.name, TraceRow.tags)
        )
        async with database_errors_as_repository_errors(), self._session_factory() as session:
            rows = await session.execute(statement)
            return [
                DiscoveredGroup(
                    environment=environment,
                    trace_name=name,
                    tags=tuple(tags),
                    trace_count=count,
                    first_seen=first_seen,
                    last_seen=last_seen,
                    total_cost=total_cost,
                )
                for environment, name, tags, count, first_seen, last_seen, total_cost in rows
            ]


async def _commit_unless_name_taken(session: AsyncSession, name: str) -> None:
    """Commit, turning a violation of the unique name index into `ProfileNameTakenError`."""
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        if _NAME_INDEX in str(error.orig):
            raise ProfileNameTakenError(f"A profile named {name!r} already exists") from error
        raise


def _to_domain(row: ProfileRow) -> Profile:
    """Domain profile for a row."""
    return Profile(
        id=row.id,
        name=row.name,
        rule=ProfileRule(
            environment=row.environment, trace_name=row.trace_name, tags=tuple(row.tags)
        ),
        status=ProfileStatus(row.status),
        created_at=row.created_at,
        archived_at=row.archived_at,
    )
