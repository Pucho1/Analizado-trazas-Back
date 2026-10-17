import re
from datetime import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.constants import CORS_ORIGIN_PATTERN


class Settings(BaseSettings):
    """Application configuration, read and validated from the environment and `.env`."""

    # Empty variables (as copied from `.env.example`) count as unset and use the default.
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_ignore_empty=True, extra="ignore"
    )

    langfuse_public_key: str = Field(min_length=1)
    langfuse_secret_key: SecretStr = Field(min_length=1)
    langfuse_host: AnyHttpUrl
    # PostgreSQL. The same variables configure the container in docker-compose.yml.
    postgres_user: str = Field(min_length=1)
    postgres_password: SecretStr = Field(min_length=1)
    postgres_db: str = Field(min_length=1)
    # 127.0.0.1 rather than localhost: on Windows, localhost tries IPv6 first and every
    # connection to the container waits ~2 s for that attempt to fail.
    postgres_host: str = "127.0.0.1"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    # Browser origins allowed to call the API, as a JSON list. Empty means none.
    cors_allowed_origins: list[str] = Field(default_factory=list)
    # Automatic synchronisation: daily scans at these local times (JSON list of "HH:MM")
    # plus a first one at startup if nothing was ever synced. "Sync now" always works.
    sync_enabled: bool = True
    sync_schedule: list[time] = Field(
        default_factory=lambda: [time(8, 0), time(14, 0), time(20, 0)]
    )
    sync_timezone: str = "Europe/Madrid"
    # How far back the very first sync reads.
    sync_initial_lookback_days: int = Field(default=30, ge=1)

    @field_validator("cors_allowed_origins")
    @classmethod
    def _validate_cors_origins(cls, origins: list[str]) -> list[str]:
        """Accept only exact origins (`http(s)://host[:port]`), never `*` or URLs with a path."""
        invalid = [origin for origin in origins if not re.fullmatch(CORS_ORIGIN_PATTERN, origin)]
        if invalid:
            raise ValueError(
                f"Invalid CORS origins {invalid}: use scheme://host[:port] without path or '*'"
            )
        return origins

    @field_validator("sync_schedule")
    @classmethod
    def _validate_sync_schedule(cls, schedule: list[time]) -> list[time]:
        """Reject repeated times; return them sorted."""
        if len(set(schedule)) != len(schedule):
            raise ValueError("SYNC_SCHEDULE must not repeat a time")
        return sorted(schedule)

    @field_validator("sync_timezone")
    @classmethod
    def _validate_sync_timezone(cls, name: str) -> str:
        """Accept only IANA time zone names, such as `Europe/Madrid`."""
        try:
            ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError(f"Unknown time zone {name!r}") from error
        return name
