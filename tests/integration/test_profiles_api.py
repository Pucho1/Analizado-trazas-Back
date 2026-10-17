from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.dependencies import get_profile_service
from app.application.profile_service import ProfileService
from app.core.constants import UNNAMED_TRACE_NAME
from app.core.exceptions import RepositoryError
from app.domain.models import Profile
from app.main import create_app
from tests.unit.fakes import InMemoryProfileRepository, make_trace

NOW = datetime(2026, 10, 17, 14, 0, tzinfo=UTC)
PATH = "/api/v1/profiles"
PORTFOLIO_RULE = {
    "environment": "sdk-experiment",
    "trace_name": "rag-portafolio-eval",
    "tags": ["app:rag-portafolio"],
}


@pytest.fixture
def repository() -> InMemoryProfileRepository:
    """Traces shaped like the user's real data, nothing classified."""
    return InMemoryProfileRepository(
        [
            make_trace(
                id="p1",
                timestamp=NOW,
                environment="sdk-experiment",
                name="rag-portafolio-eval",
                tags=("app:rag-portafolio",),
                cost=Decimal("0.0005"),
            ),
            make_trace(id="e1", environment="sdk-experiment", name="experiment-item-run"),
            make_trace(id="e2", environment="sdk-experiment", name="experiment-item-run"),
            make_trace(id="x1", environment="production", name="checkout-agent"),
            make_trace(id="u1", environment="default", name=UNNAMED_TRACE_NAME),
        ]
    )


@pytest.fixture
def client(repository: InMemoryProfileRepository) -> Iterator[TestClient]:
    """Client whose profile service runs on the in-memory repository."""
    app: FastAPI = create_app()
    service = ProfileService(repository, clock=lambda: NOW)
    app.dependency_overrides[get_profile_service] = lambda: service
    yield TestClient(app)
    app.dependency_overrides.clear()


def create(client: TestClient, name: str = "Asistente del portafolio") -> dict[str, object]:
    """Create the portfolio profile and return its body."""
    response = client.post(PATH, json={"name": name, "rule": PORTFOLIO_RULE})
    assert response.status_code == 201
    body: dict[str, object] = response.json()
    return body


def test_discover_proposes_groups_and_flags_the_monitored_environment(
    client: TestClient,
) -> None:
    response = client.get(f"{PATH}/discover")

    assert response.status_code == 200
    groups = response.json()["groups"]
    assert [(g["suggested_name"], g["trace_count"]) for g in groups] == [
        ("experiment-item-run", 2),
        ("checkout-agent", 1),
        ("rag-portafolio-eval", 1),
    ]
    assert groups[1]["monitored_environment"] is True
    assert groups[0]["monitored_environment"] is False
    assert groups[2]["rule"] == PORTFOLIO_RULE
    assert groups[2]["total_cost"] == 0.0005


def test_create_classifies_and_returns_the_profile(client: TestClient) -> None:
    body = create(client)

    assert body == {
        "id": 1,
        "name": "Asistente del portafolio",
        "rule": PORTFOLIO_RULE,
        "status": "active",
        "created_at": "2026-10-17T14:00:00Z",
        "archived_at": None,
        "trace_count": 1,
    }


def test_environment_defaults_to_production(client: TestClient) -> None:
    response = client.post(
        PATH, json={"name": "Checkout", "rule": {"trace_name": "checkout-agent"}}
    )

    assert response.json()["rule"]["environment"] == "production"
    assert response.json()["trace_count"] == 1


def test_list_includes_unclassified(client: TestClient) -> None:
    create(client)

    body = client.get(PATH).json()

    assert [p["name"] for p in body["profiles"]] == ["Asistente del portafolio"]
    assert body["unclassified"] == {"trace_count": 4}


def test_duplicate_name_is_a_conflict(client: TestClient) -> None:
    create(client)

    response = client.post(PATH, json={"name": "asistente DEL portafolio", "rule": PORTFOLIO_RULE})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "profile_name_taken"


@pytest.mark.parametrize(
    "body",
    [
        {"name": "  ", "rule": PORTFOLIO_RULE},
        {"name": "x" * 101, "rule": PORTFOLIO_RULE},
        {"name": "ok", "rule": {"environment": ""}},
        {"name": "ok", "rule": {"tags": [" "]}},
        {"name": "ok"},
    ],
)
def test_invalid_bodies_are_rejected(client: TestClient, body: dict[str, object]) -> None:
    assert client.post(PATH, json=body).status_code == 422


def test_patch_renames_and_changes_the_rule(client: TestClient) -> None:
    profile_id = create(client)["id"]

    renamed = client.patch(f"{PATH}/{profile_id}", json={"name": "Asistente de la web"})
    moved = client.patch(
        f"{PATH}/{profile_id}",
        json={"rule": {"environment": "sdk-experiment", "trace_name": "experiment-item-run"}},
    )

    assert renamed.json()["name"] == "Asistente de la web"
    assert renamed.json()["trace_count"] == 1
    assert moved.json()["trace_count"] == 2
    assert client.patch(f"{PATH}/{profile_id}", json={}).status_code == 422


def test_archive_and_unarchive(client: TestClient) -> None:
    profile_id = create(client)["id"]

    archived = client.post(f"{PATH}/{profile_id}/archive").json()
    active = client.post(f"{PATH}/{profile_id}/unarchive").json()

    assert (archived["status"], archived["archived_at"]) == ("archived", "2026-10-17T14:00:00Z")
    assert archived["trace_count"] == 1
    assert (active["status"], active["archived_at"]) == ("active", None)


def test_delete_removes_the_profile_and_its_traces(
    client: TestClient, repository: InMemoryProfileRepository
) -> None:
    profile_id = create(client)["id"]

    response = client.delete(f"{PATH}/{profile_id}")

    assert response.status_code == 204
    assert response.content == b""
    assert "p1" not in repository.traces
    assert client.get(PATH).json()["profiles"] == []


@pytest.mark.parametrize(
    ("method", "suffix", "body"),
    [
        ("PATCH", "", {"name": "x"}),
        ("POST", "/archive", None),
        ("POST", "/unarchive", None),
        ("DELETE", "", None),
    ],
)
def test_unknown_profile_is_not_found(
    client: TestClient, method: str, suffix: str, body: dict[str, str] | None
) -> None:
    response = client.request(method, f"{PATH}/42{suffix}", json=body)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "profile_not_found"


def test_database_failures_are_service_unavailable() -> None:
    class DownRepository(InMemoryProfileRepository):
        async def list_profiles(self) -> Sequence[Profile]:
            """The database is down."""
            raise RepositoryError("refused")

    app = create_app()
    service = ProfileService(DownRepository())
    app.dependency_overrides[get_profile_service] = lambda: service

    response = TestClient(app).get(PATH)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_unavailable"


def test_openapi_documents_profile_errors(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    assert set(paths[PATH]["post"]["responses"]) == {"201", "409", "422", "503"}
    assert set(paths[f"{PATH}/{{profile_id}}"]["patch"]["responses"]) == {
        "200",
        "404",
        "409",
        "422",
        "503",
    }
    assert set(paths[f"{PATH}/{{profile_id}}"]["delete"]["responses"]) == {
        "204",
        "404",
        "422",
        "503",
    }
