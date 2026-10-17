from datetime import time
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from tests.unit.fakes import REQUIRED_SETTINGS


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every setting from the environment so only the explicit values count."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def make_settings(**values: Any) -> Settings:
    """Settings from `values` alone: no environment variables, no `.env` file."""
    return Settings(_env_file=None, **values)


def required_plus(**overrides: Any) -> Settings:
    """Settings from dummy required values plus `overrides`."""
    return make_settings(**{**REQUIRED_SETTINGS, **overrides})


def test_sync_defaults() -> None:
    settings = required_plus()

    assert settings.sync_enabled is True
    assert settings.sync_schedule == [time(8, 0), time(14, 0), time(20, 0)]
    assert settings.sync_timezone == "Europe/Madrid"
    assert settings.sync_initial_lookback_days == 30
    assert settings.postgres_host == "127.0.0.1"
    assert settings.postgres_port == 5432


def test_sync_schedule_is_read_from_a_json_list_and_sorted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SYNC_SCHEDULE", '["21:30", "07:15"]')

    assert required_plus().sync_schedule == [time(7, 15), time(21, 30)]


def test_sync_can_be_disabled_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYNC_ENABLED", "false")

    assert required_plus().sync_enabled is False


def test_sync_schedule_rejects_repeated_times() -> None:
    with pytest.raises(ValidationError, match="must not repeat"):
        required_plus(sync_schedule=["08:00", "08:00"])


def test_sync_schedule_rejects_invalid_times() -> None:
    with pytest.raises(ValidationError):
        required_plus(sync_schedule=["25:00"])


def test_sync_schedule_may_be_empty() -> None:
    assert required_plus(sync_schedule=[]).sync_schedule == []


@pytest.mark.parametrize("field", ["sync_timezone", "monitoring_timezone"])
def test_timezones_reject_unknown_zones(field: str) -> None:
    with pytest.raises(ValidationError, match="Unknown time zone"):
        required_plus(**{field: "Mars/Olympus"})


def test_monitoring_timezone_defaults_to_madrid() -> None:
    assert required_plus().monitoring_timezone == "Europe/Madrid"


def test_sync_initial_lookback_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        required_plus(sync_initial_lookback_days=0)


@pytest.mark.parametrize("missing", ["postgres_user", "postgres_password", "postgres_db"])
def test_database_credentials_are_required(missing: str) -> None:
    values = {key: value for key, value in REQUIRED_SETTINGS.items() if key != missing}

    with pytest.raises(ValidationError, match=missing):
        make_settings(**values)
