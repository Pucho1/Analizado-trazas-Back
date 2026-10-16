import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.v1.dependencies import get_trace_service
from app.application.trace_service import TraceService
from app.core.config import Settings
from app.main import create_app
from tests.unit.fakes import InMemoryTraceSource

ALLOWED_ORIGIN = "http://localhost:5173"
OTHER_ORIGIN = "http://evil.test"
PATH = "/api/v1/traces/summary"


def make_settings(cors_allowed_origins: list[str]) -> Settings:
    """Build test settings with the given CORS origins and dummy Langfuse credentials."""
    return Settings.model_validate(
        {
            "langfuse_public_key": "pk-lf-test",
            "langfuse_secret_key": "sk-lf-test",
            "langfuse_host": "https://langfuse.test",
            "cors_allowed_origins": cors_allowed_origins,
        }
    )


@pytest.fixture
def client() -> TestClient:
    app = create_app(make_settings([ALLOWED_ORIGIN]))
    app.dependency_overrides[get_trace_service] = lambda: TraceService(InMemoryTraceSource())
    return TestClient(app)


def test_allowed_origin_receives_cors_header(client: TestClient) -> None:
    response = client.get(PATH, headers={"Origin": ALLOWED_ORIGIN})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert "access-control-allow-credentials" not in response.headers


def test_preflight_from_allowed_origin_allows_get(client: TestClient) -> None:
    response = client.options(
        PATH, headers={"Origin": ALLOWED_ORIGIN, "Access-Control-Request-Method": "GET"}
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert response.headers["access-control-allow-methods"] == "GET"


def test_other_origin_gets_no_cors_header(client: TestClient) -> None:
    response = client.get(PATH, headers={"Origin": OTHER_ORIGIN})

    assert "access-control-allow-origin" not in response.headers


def test_preflight_from_other_origin_is_rejected(client: TestClient) -> None:
    response = client.options(
        PATH, headers={"Origin": OTHER_ORIGIN, "Access-Control-Request-Method": "GET"}
    )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_preflight_for_write_method_is_rejected(client: TestClient) -> None:
    response = client.options(
        PATH, headers={"Origin": ALLOWED_ORIGIN, "Access-Control-Request-Method": "POST"}
    )

    assert response.status_code == 400


def test_no_origins_are_allowed_by_default() -> None:
    settings = Settings.model_validate(
        {
            "langfuse_public_key": "pk-lf-test",
            "langfuse_secret_key": "sk-lf-test",
            "langfuse_host": "https://langfuse.test",
        },
    )

    assert settings.cors_allowed_origins == []


def test_empty_origins_variable_means_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "")

    assert Settings(_env_file=None).cors_allowed_origins == []


def test_origins_are_read_from_json_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", '["http://localhost:5173"]')

    assert Settings(_env_file=None).cors_allowed_origins == ["http://localhost:5173"]


@pytest.mark.parametrize(
    "origin",
    ["http://localhost:5173", "https://observa.example.com", "http://127.0.0.1:3000"],
)
def test_valid_origins_are_accepted(origin: str) -> None:
    assert make_settings([origin]).cors_allowed_origins == [origin]


@pytest.mark.parametrize(
    "origin",
    ["*", "http://localhost:5173/", "http://localhost:5173/app", "localhost:5173", "ftp://x.test"],
)
def test_invalid_origins_are_rejected(origin: str) -> None:
    with pytest.raises(ValidationError, match="Invalid CORS origins"):
        make_settings([origin])
