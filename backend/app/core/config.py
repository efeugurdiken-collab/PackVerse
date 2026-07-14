"""Application configuration loaded from environment variables.

Uses Pydantic Settings so every config value is typed and validated at
startup, rather than read ad hoc via os.environ throughout the codebase.
"""
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- App ---
    app_name: str = "PackVerse Platform"
    environment: str = "development"  # development | staging | production | test
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    # --- Database ---
    postgres_user: str = "packverse"
    postgres_password: str = "packverse"
    postgres_host: str = "db"
    postgres_port: int = 5432
    postgres_db: str = "packverse"

    # --- Test database (Sprint P2: isolated DB for the test suite) ---
    # Defaults to postgres_db + "_test" if not explicitly set, so tests
    # never run against the same database as local development by accident.
    test_postgres_db: str | None = None

    # --- Logging ---
    log_level: str = "INFO"

    @property
    def database_url(self) -> str:
        """Async SQLAlchemy connection string (asyncpg driver)."""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def sync_database_url(self) -> str:
        """Sync connection string, used by Alembic migrations."""
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def test_database_url(self) -> str:
        """Async connection string for the isolated test database."""
        db_name = self.test_postgres_db or f"{self.postgres_db}_test"
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{db_name}"
        )

    @property
    def test_sync_database_url(self) -> str:
        """Sync connection string for the isolated test database, used by
        Alembic when the migration test suite drives upgrade/downgrade."""
        db_name = self.test_postgres_db or f"{self.postgres_db}_test"
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{db_name}"
        )

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, v: str) -> str:
        allowed = {"development", "staging", "production", "test"}
        if v not in allowed:
            raise ValueError(f"environment must be one of {allowed}, got {v!r}")
        return v


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance - environment is read once per process."""
    return Settings()
