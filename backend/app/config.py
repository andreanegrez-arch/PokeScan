from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/.env, wherever the command is started from.
ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


class Settings(BaseSettings):
    """App settings, read from environment variables or backend/.env."""

    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    # Connection without the password: postgresql://user@host:port/database
    database_url: str = "postgresql://postgres@localhost:5432/pokescan"
    # Kept out of the URL so that any character works without URL-encoding.
    database_password: str | None = None
    cors_origins: list[str] = ["http://localhost:5173"]


settings = Settings()
