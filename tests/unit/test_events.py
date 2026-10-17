"""Business events from step counts and the per-profile mapping (pure domain)."""

import pytest

from app.domain.events import (
    TECHNICAL_ERRORS_LABEL,
    TECHNICAL_WARNINGS_LABEL,
    EventCount,
    EventMapping,
    StepCount,
    summarize_events,
)
from app.domain.models import ObservationLevel

DEFAULT = ObservationLevel.DEFAULT
ERROR = ObservationLevel.ERROR
WARNING = ObservationLevel.WARNING

MAPPINGS = [
    EventMapping("manipulation-attempt", "Intentos de manipulación"),
    EventMapping("in-domain-response-fallback", "Respuestas de reserva"),
    EventMapping("inDomain-response-fallback", "Respuestas de reserva"),
]


def step(
    name: str, level: ObservationLevel = DEFAULT, occurrences: int = 1, *traces: str
) -> StepCount:
    """A step count; traces default to one per occurrence named after the step."""
    trace_ids = frozenset(traces or (f"{name}-{i}" for i in range(occurrences)))
    return StepCount(name=name, level=level, occurrences=occurrences, trace_ids=trace_ids)


def test_nothing_happened() -> None:
    summary = summarize_events([], MAPPINGS)

    assert summary.empty
    assert summary.events == ()
    assert summary.technical_errors == EventCount(TECHNICAL_ERRORS_LABEL, 0, 0)


def test_mapped_steps_become_business_events() -> None:
    summary = summarize_events([step("manipulation-attempt", DEFAULT, 3)], MAPPINGS)

    assert summary.events == (EventCount("Intentos de manipulación", 3, 3),)
    assert not summary.empty


def test_two_spellings_share_a_label_and_queries_count_once() -> None:
    steps = [
        step("in-domain-response-fallback", DEFAULT, 2, "q1", "q2"),
        step("inDomain-response-fallback", DEFAULT, 2, "q2", "q3"),
    ]

    (event,) = summarize_events(steps, MAPPINGS).events

    assert event == EventCount("Respuestas de reserva", 4, 3)


def test_same_name_at_two_levels_counts_queries_once() -> None:
    steps = [
        step("manipulation-attempt", DEFAULT, 1, "q1"),
        step("manipulation-attempt", ERROR, 1, "q1"),
    ]

    (event,) = summarize_events(steps, MAPPINGS).events

    assert event == EventCount("Intentos de manipulación", 2, 1)


def test_mapped_errors_are_events_not_technical_errors() -> None:
    summary = summarize_events([step("manipulation-attempt", ERROR, 2)], MAPPINGS)

    assert summary.events == (EventCount("Intentos de manipulación", 2, 2),)
    assert summary.technical_errors.occurrences == 0


def test_unmapped_errors_and_warnings_are_technical() -> None:
    steps = [
        step("ChatOpenAI", ERROR, 8, *(f"q{i}" for i in range(8))),
        step("router", ERROR, 6, "q1", "q2", "q9"),
        step("retry", WARNING, 2, "q1", "q2"),
    ]

    summary = summarize_events(steps, MAPPINGS)

    assert summary.technical_errors == EventCount(TECHNICAL_ERRORS_LABEL, 14, 9)
    assert summary.technical_warnings == EventCount(TECHNICAL_WARNINGS_LABEL, 2, 2)
    assert summary.events == ()


def test_unmapped_normal_steps_are_plumbing() -> None:
    steps = [step("RunnableLambda", DEFAULT, 791), step("length-rejection", DEFAULT, 63)]

    assert summarize_events(steps, MAPPINGS).empty


def test_without_mappings_only_technical_problems_show() -> None:
    steps = [step("manipulation-attempt", DEFAULT, 3), step("router", ERROR, 1)]

    summary = summarize_events(steps, [])

    assert summary.events == ()
    assert summary.technical_errors.occurrences == 1


def test_events_are_ordered_by_frequency_then_label() -> None:
    mappings = [EventMapping("a", "Beta"), EventMapping("b", "Alfa"), EventMapping("c", "Gamma")]
    steps = [step("a", DEFAULT, 2), step("b", DEFAULT, 2), step("c", DEFAULT, 5)]

    labels = [event.label for event in summarize_events(steps, mappings).events]

    assert labels == ["Gamma", "Alfa", "Beta"]


@pytest.mark.parametrize(("name", "label"), [("", "x"), ("x", " "), ("  ", "  ")])
def test_mapping_rejects_blanks(name: str, label: str) -> None:
    with pytest.raises(ValueError, match="needs a step name and a label"):
        EventMapping(name, label)
