from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.application.profile_service import ProfileService
from app.core.constants import UNNAMED_TRACE_NAME
from app.core.exceptions import ProfileNameTakenError, ProfileNotFoundError
from app.domain.models import ProfileRule, ProfileStatus, SyncRun, SyncStatus, SyncTrigger
from tests.unit.fakes import InMemoryProfileRepository, make_trace

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 17, 14, 0, tzinfo=UTC)
PORTFOLIO = ProfileRule(environment="sdk-experiment", trace_name="rag-portafolio-eval")
EXPERIMENTS = ProfileRule(environment="sdk-experiment", trace_name="experiment-item-run")


class Clock:
    """Clock that advances one minute on every call."""

    def __init__(self) -> None:
        """Start at `NOW`."""
        self.now = NOW

    def __call__(self) -> datetime:
        """Return the current time, then advance."""
        current = self.now
        self.now += timedelta(minutes=1)
        return current


def sample_traces() -> InMemoryProfileRepository:
    """Repository with traces shaped like the user's real data."""
    return InMemoryProfileRepository(
        [
            make_trace(id="e1", environment="sdk-experiment", name="experiment-item-run"),
            make_trace(id="e2", environment="sdk-experiment", name="experiment-item-run"),
            make_trace(
                id="p1",
                environment="sdk-experiment",
                name="rag-portafolio-eval",
                tags=("app:rag-portafolio",),
                cost=Decimal("0.0003"),
            ),
            make_trace(id="r1", environment="default", name="RunnableSequence"),
            make_trace(id="u1", environment="default", name=UNNAMED_TRACE_NAME),
        ]
    )


def make_service(repository: InMemoryProfileRepository) -> ProfileService:
    """Service over `repository` with a deterministic clock."""
    return ProfileService(repository, clock=Clock())


async def test_create_classifies_matching_traces() -> None:
    repository = sample_traces()

    profile = await make_service(repository).create("  Asistente del portafolio ", PORTFOLIO)

    assert profile.name == "Asistente del portafolio"
    assert profile.status is ProfileStatus.ACTIVE
    assert profile.created_at == NOW
    assert repository.profile_of["p1"] == profile.id
    assert repository.profile_of["e1"] is None


async def test_create_rejects_a_taken_name_ignoring_case() -> None:
    service = make_service(sample_traces())
    await service.create("Asistente", PORTFOLIO)

    with pytest.raises(ProfileNameTakenError):
        await service.create("ASISTENTE", EXPERIMENTS)


async def test_overview_counts_traces_per_profile_and_unclassified() -> None:
    service = make_service(sample_traces())
    portfolio = await service.create("Portafolio", PORTFOLIO)
    experiments = await service.create("Experimentos", EXPERIMENTS)

    overview = await service.overview()

    assert [(item.profile.id, item.trace_count) for item in overview.profiles] == [
        (portfolio.id, 1),
        (experiments.id, 2),
    ]
    assert overview.unclassified_count == 2


async def test_discover_proposes_unclassified_groups_without_unnamed() -> None:
    service = make_service(sample_traces())
    await service.create("Portafolio", PORTFOLIO)

    groups = await service.discover()

    assert [(g.environment, g.trace_name, g.trace_count) for g in groups] == [
        ("sdk-experiment", "experiment-item-run", 2),
        ("default", "RunnableSequence", 1),
    ]


async def test_discover_on_untouched_data() -> None:
    groups = await make_service(sample_traces()).discover()

    portfolio = next(g for g in groups if g.trace_name == "rag-portafolio-eval")
    assert portfolio.tags == ("app:rag-portafolio",)
    assert portfolio.total_cost == Decimal("0.0003")
    assert UNNAMED_TRACE_NAME not in {g.trace_name for g in groups}


async def test_changing_the_rule_reclassifies() -> None:
    repository = sample_traces()
    service = make_service(repository)
    profile = await service.create("Agente", PORTFOLIO)

    updated = await service.update(profile.id, rule=EXPERIMENTS)

    assert updated.rule == EXPERIMENTS
    assert repository.profile_of["p1"] is None
    assert repository.profile_of["e1"] == profile.id


async def test_renaming_does_not_reclassify() -> None:
    repository = sample_traces()
    service = make_service(repository)
    profile = await service.create("Agente", PORTFOLIO)
    calls_before = len(repository.assign_calls)

    renamed = await service.update(profile.id, name=" Asistente de la web ")

    assert renamed.name == "Asistente de la web"
    assert renamed.rule == PORTFOLIO
    assert len(repository.assign_calls) == calls_before


async def test_archive_and_unarchive() -> None:
    repository = sample_traces()
    service = make_service(repository)
    profile = await service.create("Portafolio", PORTFOLIO)

    archived = await service.archive(profile.id)
    archived_again = await service.archive(profile.id)
    active = await service.unarchive(profile.id)

    assert archived.status is ProfileStatus.ARCHIVED
    assert archived.archived_at is not None
    assert archived_again == archived
    assert (active.status, active.archived_at) == (ProfileStatus.ACTIVE, None)
    assert repository.profile_of["p1"] == profile.id


async def test_archived_profile_keeps_receiving_its_traces() -> None:
    repository = sample_traces()
    service = make_service(repository)
    profile = await service.create("Portafolio", PORTFOLIO)
    await service.archive(profile.id)
    new_trace = make_trace(
        id="p2", environment="sdk-experiment", name="rag-portafolio-eval", timestamp=NOW
    )
    repository.traces[new_trace.id] = new_trace
    repository.profile_of[new_trace.id] = None

    await service.reclassify()

    assert repository.profile_of["p2"] == profile.id


async def test_delete_removes_the_profile_and_its_traces() -> None:
    repository = sample_traces()
    service = make_service(repository)
    profile = await service.create("Portafolio", PORTFOLIO)

    await service.delete(profile.id)

    assert profile.id not in repository.profiles
    assert "p1" not in repository.traces
    assert "e1" in repository.traces


async def _update(service: ProfileService) -> object:
    """Rename profile 99."""
    return await service.update(99, name="x")


async def _archive(service: ProfileService) -> object:
    """Archive profile 99."""
    return await service.archive(99)


async def _unarchive(service: ProfileService) -> object:
    """Unarchive profile 99."""
    return await service.unarchive(99)


async def _delete(service: ProfileService) -> object:
    """Delete profile 99."""
    await service.delete(99)
    return None


@pytest.mark.parametrize("operation", [_update, _archive, _unarchive, _delete])
async def test_unknown_profile(
    operation: Callable[[ProfileService], Awaitable[object]],
) -> None:
    service = make_service(sample_traces())

    with pytest.raises(ProfileNotFoundError):
        await operation(service)


async def test_reclassify_only_writes_changes() -> None:
    repository = sample_traces()
    service = make_service(repository)
    await service.create("Portafolio", PORTFOLIO)

    assert await service.reclassify() == 0
    assert repository.assign_calls == [{"p1": 1}]


async def test_sync_hook_classifies_newly_synced_traces() -> None:
    repository = sample_traces()
    service = make_service(repository)
    profile = await service.create("Experimentos", EXPERIMENTS)
    new_trace = make_trace(id="e3", environment="sdk-experiment", name="experiment-item-run")
    repository.traces[new_trace.id] = new_trace
    repository.profile_of[new_trace.id] = None
    run = SyncRun(
        source="langfuse",
        trigger=SyncTrigger.SCHEDULED,
        status=SyncStatus.SUCCEEDED,
        started_at=NOW,
        finished_at=NOW,
        window_start=NOW - timedelta(hours=1),
        window_end=NOW,
        trace_count=1,
    )

    await service.reclassify_after_sync(run)

    assert repository.profile_of["e3"] == profile.id
