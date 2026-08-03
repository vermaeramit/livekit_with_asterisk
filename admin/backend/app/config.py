from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str

    # Signing key for access tokens. Rotating it invalidates every access token
    # immediately; refresh tokens survive because they are verified against
    # user_sessions, not against this key.
    jwt_secret: str
    jwt_algorithm: str = "HS256"

    # Short-lived access tokens keep the blast radius of a leaked token small.
    # Revocation lives on the refresh side, where we can actually check the DB.
    access_token_minutes: int = 15
    refresh_token_days: int = 7

    cors_origins: str = "*"

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()  # type: ignore[call-arg]
