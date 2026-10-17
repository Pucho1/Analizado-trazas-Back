"""Profile rules, profiles and the classifier (pure domain)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.models import (
    DiscoveredGroup,
    Profile,
    ProfileRule,
    ProfileStatus,
    TraceKey,
    classify,
)
from tests.unit.fakes import make_profile

T0 = datetime(2026, 10, 1, tzinfo=UTC)


def key(
    id: str = "t1",
    environment: str = "production",
    name: str = "rag-portafolio",
    tags: tuple[str, ...] = (),
) -> TraceKey:
    """A trace key with defaults."""
    return TraceKey(id=id, environment=environment, name=name, tags=tags)


def test_rule_normalises_tags() -> None:
    rule = ProfileRule(environment="production", tags=("web", "app:rag", "web"))

    assert rule.tags == ("app:rag", "web")


@pytest.mark.parametrize(
    ("environment", "trace_name", "tags"),
    [
        (" ", None, ()),
        ("production", "", ()),
        ("production", None, ("ok", " ")),
    ],
)
def test_rule_rejects_blank_values(
    environment: str, trace_name: str | None, tags: tuple[str, ...]
) -> None:
    with pytest.raises(ValueError, match=r"must not be blank|needs an environment"):
        ProfileRule(environment=environment, trace_name=trace_name, tags=tags)


@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        (ProfileRule(environment="production"), 0),
        (ProfileRule(environment="production", trace_name="x"), 1),
        (ProfileRule(environment="production", tags=("a", "b")), 2),
        (ProfileRule(environment="production", trace_name="x", tags=("a",)), 2),
    ],
)
def test_rule_specificity(rule: ProfileRule, expected: int) -> None:
    assert rule.specificity == expected


@pytest.mark.parametrize(
    ("rule", "trace", "matches"),
    [
        (ProfileRule(environment="production"), key(), True),
        (ProfileRule(environment="production"), key(environment="staging"), False),
        (ProfileRule(environment="production", trace_name="rag-portafolio"), key(), True),
        (ProfileRule(environment="production", trace_name="other"), key(), False),
        (ProfileRule(environment="production", tags=("web",)), key(tags=("app", "web")), True),
        (ProfileRule(environment="production", tags=("web", "beta")), key(tags=("web",)), False),
        (ProfileRule(environment="production", tags=("web",)), key(tags=()), False),
    ],
)
def test_rule_matching(rule: ProfileRule, trace: TraceKey, matches: bool) -> None:
    assert rule.matches(trace) is matches


@pytest.mark.parametrize(
    ("status", "archived_at"),
    [(ProfileStatus.ACTIVE, T0), (ProfileStatus.ARCHIVED, None)],
)
def test_profile_requires_archive_date_exactly_when_archived(
    status: ProfileStatus, archived_at: datetime | None
) -> None:
    rule = ProfileRule(environment="production")

    with pytest.raises(ValueError, match="archive date"):
        Profile(id=1, name="x", rule=rule, status=status, created_at=T0, archived_at=archived_at)


def test_profile_requires_a_name() -> None:
    with pytest.raises(ValueError, match="needs a name"):
        make_profile(name="  ")


def test_unmatched_traces_are_unclassified() -> None:
    profile = make_profile(rule=ProfileRule(environment="production", trace_name="a"))

    assert classify([key(name="b")], [profile]) == {"t1": None}


def test_no_profiles_means_everything_unclassified() -> None:
    assert classify([key("a"), key("b")], []) == {"a": None, "b": None}


def test_most_specific_rule_wins() -> None:
    broad = make_profile(id=1, name="Todo producción", created_at=T0)
    by_name = make_profile(
        id=2,
        name="Asistente",
        rule=ProfileRule(environment="production", trace_name="rag-portafolio"),
        created_at=T0 + timedelta(days=1),
    )
    by_name_and_tag = make_profile(
        id=3,
        name="Asistente web",
        rule=ProfileRule(environment="production", trace_name="rag-portafolio", tags=("web",)),
        created_at=T0 + timedelta(days=2),
    )
    traces = [key("plain"), key("tagged", tags=("web",)), key("other", name="other")]

    assignments = classify(traces, [broad, by_name, by_name_and_tag])

    assert assignments == {"plain": 2, "tagged": 3, "other": 1}


def test_ties_go_to_the_oldest_profile() -> None:
    newer = make_profile(
        id=1,
        name="Etiqueta web",
        rule=ProfileRule(environment="production", tags=("web",)),
        created_at=T0 + timedelta(days=1),
    )
    older = make_profile(
        id=2,
        name="Por nombre",
        rule=ProfileRule(environment="production", trace_name="rag-portafolio"),
        created_at=T0,
    )

    assert classify([key(tags=("web",))], [newer, older]) == {"t1": 2}


def test_archived_profiles_still_classify() -> None:
    archived = make_profile(status=ProfileStatus.ARCHIVED)

    assert classify([key()], [archived]) == {"t1": archived.id}


def test_discovered_group_rule_captures_exactly_the_group() -> None:
    group = DiscoveredGroup(
        environment="sdk-experiment",
        trace_name="rag-portafolio-eval",
        tags=("app:rag-portafolio",),
        trace_count=6,
        first_seen=T0,
        last_seen=T0,
        total_cost=Decimal("0.000522"),
    )

    assert group.rule == ProfileRule(
        environment="sdk-experiment",
        trace_name="rag-portafolio-eval",
        tags=("app:rag-portafolio",),
    )
