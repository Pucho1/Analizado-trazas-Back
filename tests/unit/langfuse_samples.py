"""Langfuse v2 observation payloads shaped like real API responses."""

from typing import Any

TRACE_ID = "b7dbfa7160c276c1e1a7d86d45103788"


def observation_json(**overrides: Any) -> dict[str, Any]:
    """A plain span with no usage or cost; override any field by its API name."""
    payload: dict[str, Any] = {
        "id": "obs-span",
        "traceId": TRACE_ID,
        "startTime": "2026-10-09T09:33:09.300Z",
        "endTime": "2026-10-09T09:33:09.400Z",
        "projectId": "project-1",
        "parentObservationId": "obs-root",
        "type": "SPAN",
        "name": "route-query",
        "level": "DEFAULT",
        "isRootObservation": False,
        "traceName": None,
        "totalCost": None,
        "costDetails": {},
        "usageDetails": {},
    }
    payload.update(overrides)
    return payload


def root_json(**overrides: Any) -> dict[str, Any]:
    return observation_json(
        **{
            "id": "obs-root",
            "parentObservationId": None,
            "name": "experiment-item-run",
            "traceName": "experiment-item-run",
            "isRootObservation": True,
            "startTime": "2026-10-09T09:33:09.166Z",
            "endTime": "2026-10-09T09:33:14.524Z",
            **overrides,
        }
    )


def generation_json(**overrides: Any) -> dict[str, Any]:
    return observation_json(
        **{
            "id": "obs-generation",
            "type": "GENERATION",
            "name": "ChatOpenAI",
            "startTime": "2026-10-09T09:33:09.265Z",
            "endTime": "2026-10-09T09:33:12.084Z",
            "totalCost": 0.0001605,
            "costDetails": {"input": 0.0001143, "output": 4.62e-05, "total": 0.0001605},
            "usageDetails": {
                "input": 762,
                "output": 77,
                "total": 839,
                "input_cache_read": 0,
                "output_reasoning": 0,
            },
            **overrides,
        }
    )
