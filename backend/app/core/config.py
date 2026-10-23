from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    app_name: str = "RevenueGuard"
    app_env: str = "development"

    postgres_db: str = "revenueguard"
    postgres_user: str = "revenueguard"
    postgres_password: str = ""
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    database_url: str = "postgresql+psycopg://revenueguard:revenueguard_dev@localhost:5432/revenueguard"
    redis_url: str = "redis://localhost:6379/0"
    webhook_secret: str = ""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


settings = Settings()
