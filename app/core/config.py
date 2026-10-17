import re

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
    # Browser origins allowed to call the API, as a JSON list. Empty means none.
    cors_allowed_origins: list[str] = Field(default_factory=list)

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
