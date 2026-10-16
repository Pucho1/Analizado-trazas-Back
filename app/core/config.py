from pydantic import AnyHttpUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration, read and validated from the environment and `.env`."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    langfuse_public_key: str = Field(min_length=1)
    langfuse_secret_key: SecretStr = Field(min_length=1)
    langfuse_host: AnyHttpUrl
