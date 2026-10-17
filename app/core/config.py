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
    # Time zone of the calendar days used by daily baselines, budgets and alerts.
    monitoring_timezone: str = "Europe/Madrid"
    # Daily report: sent at this local time (after the morning scan) about the day before.
    daily_report_time: time = time(8, 30)

    # Email channel (SMTP). Unset = channel not configured. Port 465 uses implicit TLS;
    # any other port upgrades with STARTTLS.
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    email_from: str | None = None
    email_to: str | None = None

    # Telegram channel. The token comes from @BotFather; the chat id from
    # GET /api/v1/notifications/telegram/chats after writing to the bot once.
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None

    @property
    def email_configured(self) -> bool:
        """Whether every variable the email channel needs is set."""
        return all(
            (self.smtp_host, self.smtp_username, self.smtp_password, self.email_from, self.email_to)
        )

    @property
    def telegram_configured(self) -> bool:
        """Whether the Telegram channel can send (token and chat id)."""
        return self.telegram_bot_token is not None and bool(self.telegram_chat_id)

    @field_validator("email_from", "email_to")
    @classmethod
    def _validate_address(cls, address: str | None) -> str | None:
        """Require something that looks like an email address."""
        if address is not None and "@" not in address:
            raise ValueError(f"{address!r} is not an email address")
        return address

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

    @field_validator("sync_timezone", "monitoring_timezone")
    @classmethod
    def _validate_timezone(cls, name: str) -> str:
        """Accept only IANA time zone names, such as `Europe/Madrid`."""
        try:
            ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError(f"Unknown time zone {name!r}") from error
        return name
