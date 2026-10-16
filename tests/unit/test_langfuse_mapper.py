from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.constants import UNNAMED_TRACE_NAME
from app.infrastructure.langfuse.mapper import LangfuseObservation, map_traces
from tests.unit.langfuse_samples import TRACE_ID, generation_json, observation_json, root_json


def parse(*payloads: dict[str, Any]) -> list[LangfuseObservation]:
    return [LangfuseObservation.model_validate(payload) for payload in payloads]


def test_maps_a_complete_trace() -> None:
    (trace,) = map_traces(parse(root_json(), generation_json(), observation_json()))

    assert trace.id == TRACE_ID
    assert trace.timestamp == datetime(2026, 10, 9, 9, 33, 9, 166000, tzinfo=UTC)
    assert trace.name == "experiment-item-run"
    assert trace.latency == pytest.approx(5.358)
    assert trace.cost == Decimal("0.0001605")
    assert (trace.input_tokens, trace.output_tokens, trace.total_tokens) == (762, 77, 839)


def test_sums_cost_and_tokens_across_generations() -> None:
    second = generation_json(
        id="obs-generation-2",
        totalCost=0.00011595,
        usageDetails={"input": 669, "output": 26, "total": 695},
    )

    (trace,) = map_traces(parse(root_json(), generation_json(), second))

    assert trace.cost == Decimal("0.00027645")
    assert (trace.input_tokens, trace.output_tokens, trace.total_tokens) == (1431, 103, 1534)


def test_cost_keeps_decimal_precision() -> None:
    (trace,) = map_traces(parse(root_json(), generation_json(totalCost=5.5049999e-05)))

    assert trace.cost == Decimal("0.000055049999")


def test_trace_without_llm_calls_has_zero_cost_and_tokens() -> None:
    (trace,) = map_traces(parse(root_json(), observation_json()))

    assert trace.cost == Decimal(0)
    assert (trace.input_tokens, trace.output_tokens, trace.total_tokens) == (0, 0, 0)


def test_null_usage_and_cost_count_as_zero() -> None:
    (trace,) = map_traces(parse(root_json(usageDetails=None, totalCost=None), generation_json()))

    assert trace.cost == Decimal("0.0001605")
    assert trace.total_tokens == 839


def test_total_tokens_falls_back_to_input_plus_output() -> None:
    generation = generation_json(usageDetails={"input": 100, "output": 20})

    (trace,) = map_traces(parse(root_json(), generation))

    assert trace.total_tokens == 120


def test_total_tokens_keeps_reported_total_with_cache_reads() -> None:
    generation = generation_json(
        usageDetails={"input": 100, "input_cache_read": 50, "output": 20, "total": 170}
    )

    (trace,) = map_traces(parse(root_json(), generation))

    assert (trace.input_tokens, trace.output_tokens, trace.total_tokens) == (100, 20, 170)


def test_latency_spans_from_first_start_to_last_end() -> None:
    root = root_json(endTime="2026-10-09T09:33:11.384Z")
    straggler = observation_json(endTime="2026-10-09T09:33:11.386Z")

    (trace,) = map_traces(parse(root, straggler))

    assert trace.latency == pytest.approx(2.22)


def test_latency_is_none_while_nothing_has_ended() -> None:
    (trace,) = map_traces(parse(root_json(endTime=None), observation_json(endTime=None)))

    assert trace.latency is None


def test_latency_ignores_observations_still_running() -> None:
    (trace,) = map_traces(parse(root_json(endTime=None), generation_json()))

    assert trace.latency == pytest.approx(2.918)


def test_trace_without_root_is_unnamed_and_starts_at_earliest_observation() -> None:
    orphan_span = observation_json(startTime="2026-09-30T11:00:12.540Z", endTime=None)
    orphan_generation = generation_json(
        startTime="2026-09-30T11:00:13.000Z", endTime="2026-09-30T11:00:17.110Z"
    )

    (trace,) = map_traces(parse(orphan_span, orphan_generation))

    assert trace.name == UNNAMED_TRACE_NAME
    assert trace.timestamp == datetime(2026, 9, 30, 11, 0, 12, 540000, tzinfo=UTC)
    assert trace.latency == pytest.approx(4.57)


@pytest.mark.parametrize(
    ("trace_name", "name", "expected"),
    [
        ("my-agent", "root-span", "my-agent"),
        (None, "root-span", "root-span"),
        ("", "root-span", "root-span"),
        (None, None, UNNAMED_TRACE_NAME),
        ("", "", UNNAMED_TRACE_NAME),
    ],
)
def test_name_falls_back_from_trace_name_to_root_name(
    trace_name: str | None, name: str | None, expected: str
) -> None:
    (trace,) = map_traces(parse(root_json(traceName=trace_name, name=name)))

    assert trace.name == expected


def test_groups_observations_into_one_trace_each() -> None:
    other = "a796584fdd43d805436d2473020c2138"
    observations = parse(
        root_json(),
        root_json(id="other-root", traceId=other, traceName="RunnableSequence"),
        generation_json(),
        generation_json(id="other-generation", traceId=other, totalCost=0.001),
    )

    traces = {trace.id: trace for trace in map_traces(observations)}

    assert set(traces) == {TRACE_ID, other}
    assert traces[other].name == "RunnableSequence"
    assert traces[other].cost == Decimal("0.001")
    assert traces[TRACE_ID].cost == Decimal("0.0001605")


def test_skips_observations_without_trace() -> None:
    assert map_traces(parse(observation_json(traceId=None))) == []


def test_no_observations_means_no_traces() -> None:
    assert map_traces([]) == []


def test_rejects_timestamps_without_timezone() -> None:
    with pytest.raises(ValidationError):
        parse(root_json(startTime="2026-10-09T09:33:09.166"))
