from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App settings, read from environment variables or a local .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Connection without the password: postgresql://user@host:port/database
    database_url: str = "postgresql://postgres@localhost:5432/pokescan"
    # Kept out of the URL so that any character works without URL-encoding.
    database_password: str | None = None
    cors_origins: list[str] = ["http://localhost:5173"]


settings = Settings()
