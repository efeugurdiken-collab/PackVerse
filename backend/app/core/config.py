"""Application configuration loaded from environment variables.

Uses Pydantic Settings so every config value is typed and validated at
startup, rather than read ad hoc via os.environ throughout the codebase.
"""
import logging
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

_ENV_FILE = Path(".env")
_MIN_JWT_SECRET_LENGTH = 32  # enforced on any explicitly-provided secret
_GENERATED_JWT_SECRET_BYTES = 64  # entropy of an auto-generated dev secret


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

    # --- Auth (Sprint P3) ---
    # No default value here on purpose - "missing" is meaningfully
    # different from "empty string" for the environment-dependent
    # handling in _resolve_jwt_secret_key below. Production must fail
    # loudly if this is absent; development auto-generates one instead.
    jwt_secret_key: str | None = None
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30
    min_password_length: int = 12

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, v: str) -> str:
        allowed = {"development", "staging", "production", "test"}
        if v not in allowed:
            raise ValueError(f"environment must be one of {allowed}, got {v!r}")
        return v

    @model_validator(mode="after")
    def resolve_jwt_secret_key(self) -> "Settings":
        """Enforces the JWT secret policy (Sprint P3 dev-automation change):

        - Explicitly provided (any environment): must be at least
          _MIN_JWT_SECRET_LENGTH characters, used as-is.
        - Missing, environment == "development": generate a fresh
          high-entropy secret, persist it to .env if a real .env file is
          present (never overwritten if one already exists - we only
          reach the generation branch when it was absent to begin with),
          log that this happened (never the secret value itself), and
          keep booting.
        - Missing, any other environment: fail startup with a clear
          error. Auto-generation is a development convenience only -
          staging/production secrets must be provisioned explicitly, and
          a silently-generated production secret would invalidate every
          previously issued token on each restart.
        """
        if self.jwt_secret_key:
            if len(self.jwt_secret_key) < _MIN_JWT_SECRET_LENGTH:
                raise ValueError(
                    f"jwt_secret_key must be at least {_MIN_JWT_SECRET_LENGTH} characters - "
                    'generate one with: python -c "import secrets; '
                    'print(secrets.token_urlsafe(64))"'
                )
            return self

        if self.environment != "development":
            raise ValueError(
                "JWT_SECRET_KEY is required when ENVIRONMENT is not 'development' "
                f"(got {self.environment!r}). Generate one with: "
                'python -c "import secrets; print(secrets.token_urlsafe(64))" '
                "and set it via an environment variable or .env file. It is never "
                "auto-generated outside development."
            )

        generated = secrets.token_urlsafe(_GENERATED_JWT_SECRET_BYTES)
        self.jwt_secret_key = generated
        persisted = _persist_dev_secret_to_env_file(generated)
        if persisted:
            logger.info(
                "No JWT_SECRET_KEY set - generated a development-only secret and saved it "
                "to .env. Restart-safe from now on; do not use this convenience outside "
                "ENVIRONMENT=development."
            )
        else:
            logger.info(
                "No JWT_SECRET_KEY set - generated a development-only secret for this "
                "process only (no .env file found to persist it to, e.g. inside a "
                "container without it mounted). It will be regenerated - invalidating "
                "existing tokens - on every restart until JWT_SECRET_KEY is set explicitly."
            )
        return self

    @property
    def jwt_secret(self) -> str:
        """Non-optional accessor for jwt_secret_key.

        The field itself is typed `str | None` because that's genuinely
        true before resolve_jwt_secret_key runs; every consumer outside
        this module should use this property instead, since by the time
        Settings finishes constructing (this is a model_validator, it has
        already run), jwt_secret_key is guaranteed populated - either
        explicitly provided, generated for development, or startup
        already failed. The assert is a static-typing bridge, not a
        runtime safety net.
        """
        assert self.jwt_secret_key is not None
        return self.jwt_secret_key

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


def _persist_dev_secret_to_env_file(secret: str, env_path: Path = _ENV_FILE) -> bool:
    """Appends JWT_SECRET_KEY=<secret> to env_path if that file exists and
    doesn't already define the key. Returns whether it wrote anything.

    Never overwrites: if a JWT_SECRET_KEY= line is already present (even
    empty), this is a no-op - the caller only invokes this when Settings
    resolved jwt_secret_key as falsy, but a stale blank line in the file
    itself is left untouched rather than duplicated.

    No-ops (returns False) when env_path doesn't exist - e.g. a Docker
    container that received config only via docker-compose's `env_file:`
    variable injection, with no .env actually mounted into the container
    filesystem. The generated secret still works for the current process;
    it just isn't persisted anywhere.

    Not safe against concurrent writers (two processes generating a
    secret at the same instant could both append a line); acceptable for
    a single-developer local convenience feature, not a concern in
    production where this path is never taken.
    """
    if not env_path.exists():
        return False

    content = env_path.read_text()
    if any(line.strip().startswith("JWT_SECRET_KEY=") for line in content.splitlines()):
        return False

    with env_path.open("a") as f:
        if content and not content.endswith("\n"):
            f.write("\n")
        f.write(f"JWT_SECRET_KEY={secret}\n")
    return True


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance - environment is read once per process."""
    return Settings()
