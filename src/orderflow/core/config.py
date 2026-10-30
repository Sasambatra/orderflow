from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    database_url: str = Field(repr=False)
    test_database_url: str = Field(default="", repr=False)
    redis_url: str = Field(default="redis://127.0.0.1:6380/0", repr=False)
    jwt_secret: SecretStr
    webhook_secret: SecretStr
    jwt_issuer: str = "orderflow"
    jwt_audience: str = "orderflow-api"
    access_token_ttl_seconds: int = Field(default=900, ge=30, le=86400)
    reservation_ttl_seconds: int = Field(default=900, ge=1, le=86400)
    webhook_tolerance_seconds: int = Field(default=300, ge=1, le=3600)
    rate_limit_enabled: bool = True
    log_level: str = "INFO"

    @model_validator(mode="after")
    def validate_secrets(self) -> "Settings":
        for secret in (self.jwt_secret, self.webhook_secret):
            value = secret.get_secret_value()
            if len(value) < 32 or value.startswith("GENERATE_"):
                raise ValueError(
                    "Configure two independent random secrets of at least 32 characters"
                )
        if self.jwt_secret == self.webhook_secret:
            raise ValueError("JWT and webhook secrets must be different")
        if not self.database_url.startswith("postgresql+psycopg://"):
            raise ValueError("OrderFlow requires PostgreSQL with the psycopg driver")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
