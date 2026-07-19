"""Application configuration loaded from environment variables.

Uses Pydantic Settings so every config value is typed and validated at
startup, rather than read ad hoc via os.environ throughout the codebase.
"""
import json
import logging
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

_ENV_FILE = Path(".env")
_MIN_JWT_SECRET_LENGTH = 32  # enforced on any explicitly-provided secret
_GENERATED_JWT_SECRET_BYTES = 64  # entropy of an auto-generated dev secret

# --- LLM Gateway (Sprint P5) ---
# The only providers app/llm/providers/ actually implements. Deliberately
# closed (not "any string") so a typo in LLM_DEFAULT_PROVIDER or
# LLM_ALLOWED_PROVIDERS fails at startup, not on the first request.
KNOWN_LLM_PROVIDERS = frozenset({"anthropic", "openai", "fake"})


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

    # --- Storage (Sprint P4) ---
    storage_backend: str = "local"  # local | s3
    storage_local_root: str = "./data/storage"

    s3_endpoint_url: str | None = None
    s3_bucket: str | None = None
    s3_region: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    s3_use_ssl: bool = True
    s3_force_path_style: bool = False

    max_upload_size_bytes: int = 26_214_400  # 25 MiB
    # Comma-separated; see allowed_mime_types_list for the parsed form.
    # Deliberately narrow default allowlist - product asset types actually
    # produced by the vault's 07 Products/ lines (images, SVGs, PDFs,
    # zipped packs, fonts, STL binaries) plus common web-safe formats.
    # Do not add application/x-msdownload, application/x-executable, or
    # similar - see the File Validation security rule against silently
    # accepting arbitrary executables.
    # text/plain and text/markdown were added in Sprint P10B2 so a source
    # document can be uploaded as an Asset and then ingested (see
    # app/services/ingestion_service.py) - not part of the original P4
    # product-asset allowlist above.
    allowed_mime_types: str = (
        "image/png,image/jpeg,image/webp,image/svg+xml,"
        "application/pdf,application/zip,application/json,"
        "font/ttf,font/otf,"
        "model/stl,application/sla,application/octet-stream,"
        "text/plain,text/markdown"
    )

    # --- LLM Gateway (Sprint P5) ---
    # Unset by default on purpose: routing.py's rule #3 ("fail clearly if
    # no valid provider exists") only bites when neither an explicit
    # request-level provider nor this default is usable, so leaving this
    # unset is a legitimate, safe starting state - not a config error.
    llm_default_provider: str | None = None
    llm_default_model: str | None = None
    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 2
    llm_max_output_tokens: int = 4096
    # Comma-separated subset of KNOWN_LLM_PROVIDERS. "fake" ships enabled
    # by default so the generate endpoint is smoke-testable
    # (`{"provider": "fake", ...}`) with zero external credentials - see
    # the Quality Gates section of Sprint P5's spec.
    llm_allowed_providers: str = "anthropic,openai,fake"
    # JSON: {"<provider>": {"<alias>": "<model-name>", ...}, ...}. Aliases
    # (fast/balanced/quality) resolve through this configuration, never
    # through hardcoded model names in routing.py.
    llm_model_aliases: str = "{}"
    # JSON: {"<provider>:<model>": {"input_per_1k": "<decimal-string>",
    # "output_per_1k": "<decimal-string>"}, ...}. A provider/model pair
    # with no entry here prices as null, never a fabricated guess - see
    # app/llm/pricing.py.
    llm_pricing_json: str = "{}"

    # Provider credentials are intentionally NOT validated for presence
    # here (unlike Storage's validate_s3_settings_when_selected in P4) -
    # the sprint spec is explicit: "missing credentials must fail only
    # when that provider is selected". app/llm/factory.py raises
    # LLMProviderNotConfigured lazily, the first time a request actually
    # asks for a provider whose credentials are missing.
    anthropic_api_key: str | None = None
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_default_model: str | None = None

    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_default_model: str | None = None
    openai_organization: str | None = None
    openai_project: str | None = None

    # --- Job Queue / Worker (Sprint P8) ---
    # Default bounded-retry ceiling for a newly-enqueued job - a per-job
    # override is possible (Job.max_attempts) but nothing in this sprint
    # sets one, so every job currently gets this value. Only worker/infra
    # -level failures are ever retried - see app/worker/dispatch.py's
    # module docstring for the full retry-policy rationale.
    job_max_attempts: int = 3
    # How long a worker's claim on a job is valid before it's considered
    # abandoned and eligible for stale-lease recovery. Must comfortably
    # exceed job_heartbeat_interval_seconds - a live worker renews the
    # lease well before it can expire; only a genuinely stuck/crashed
    # worker ever lets it lapse.
    job_lease_seconds: float = 120.0
    # How often a worker renews its lease on the job it's currently
    # executing (a heartbeat) - independent of job_worker_poll_interval_
    # seconds, which only governs how often an IDLE worker checks for new
    # work.
    job_heartbeat_interval_seconds: float = 15.0
    job_worker_poll_interval_seconds: float = 1.0
    # Exponential backoff base for job-level retries: attempt N waits
    # job_retry_backoff_base_seconds * 2^(N-1) before becoming eligible
    # again - mirrors app/llm/gateway.py's own retry backoff shape
    # (Sprint P5), applied one layer up (whole-job re-attempts, not
    # individual provider calls, which the LLM Gateway already retries on
    # its own).
    job_retry_backoff_base_seconds: float = 5.0
    # A worker_heartbeats row older than this is considered dead for
    # /api/v1/health's "worker available" reporting - should comfortably
    # exceed job_heartbeat_interval_seconds for the same reason as
    # job_lease_seconds above.
    worker_heartbeat_stale_after_seconds: float = 60.0

    # --- MCP client (Sprint P9B) ---
    # JSON array: [{"name": "...", "base_url": "...", "auth_token": "..."}, ...]
    # ("auth_token" optional). Empty by default - the app boots fine with
    # zero MCP servers configured, the same "no config needed to boot"
    # posture as the LLM Gateway. Read-only server/tool discovery only
    # this sprint - see app/api/v1/mcp.py; there is no server-
    # registration API, servers are configured here directly.
    mcp_servers_json: str = "[]"
    mcp_timeout_seconds: float = 10.0

    # --- Runtime tool loop (Sprint P9C1) ---
    # Maximum number of LLM Gateway calls a single agent run's tool-use
    # loop may make (app/runtime/executor.py's _run_tool_loop) before
    # failing with ToolLoopLimitExceededError instead of continuing
    # indefinitely. An agent with no mcp_server configured always makes
    # exactly one call, regardless of this setting.
    runtime_max_tool_iterations: int = 5

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, v: str) -> str:
        allowed = {"development", "staging", "production", "test"}
        if v not in allowed:
            raise ValueError(f"environment must be one of {allowed}, got {v!r}")
        return v

    @field_validator("storage_backend")
    @classmethod
    def validate_storage_backend(cls, v: str) -> str:
        allowed = {"local", "s3"}
        if v not in allowed:
            raise ValueError(f"storage_backend must be one of {allowed}, got {v!r}")
        return v

    @model_validator(mode="after")
    def validate_s3_settings_when_selected(self) -> "Settings":
        """Fails fast (at startup, in every environment - not just
        production) if STORAGE_BACKEND=s3 but any mandatory S3 setting is
        missing, rather than deferring the failure to the first upload."""
        if self.storage_backend != "s3":
            return self

        required = {
            "S3_ENDPOINT_URL": self.s3_endpoint_url,
            "S3_BUCKET": self.s3_bucket,
            "S3_REGION": self.s3_region,
            "S3_ACCESS_KEY_ID": self.s3_access_key_id,
            "S3_SECRET_ACCESS_KEY": self.s3_secret_access_key,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(
                f"STORAGE_BACKEND=s3 requires {', '.join(missing)} to be set - "
                "none of these are auto-generated or defaulted."
            )
        return self

    @property
    def allowed_mime_types_list(self) -> list[str]:
        return [t.strip() for t in self.allowed_mime_types.split(",") if t.strip()]

    @field_validator("llm_default_provider")
    @classmethod
    def validate_llm_default_provider(cls, v: str | None) -> str | None:
        if v is not None and v not in KNOWN_LLM_PROVIDERS:
            raise ValueError(
                f"llm_default_provider must be one of {sorted(KNOWN_LLM_PROVIDERS)} or unset, "
                f"got {v!r}"
            )
        return v

    @field_validator("llm_allowed_providers")
    @classmethod
    def validate_llm_allowed_providers(cls, v: str) -> str:
        names = [p.strip() for p in v.split(",") if p.strip()]
        if not names:
            raise ValueError("llm_allowed_providers must list at least one provider")
        unknown = set(names) - KNOWN_LLM_PROVIDERS
        if unknown:
            raise ValueError(
                f"llm_allowed_providers contains unknown provider(s) {sorted(unknown)} - "
                f"must be a subset of {sorted(KNOWN_LLM_PROVIDERS)}"
            )
        return v

    @field_validator("llm_model_aliases", "llm_pricing_json")
    @classmethod
    def validate_llm_json_config(cls, v: str, info: ValidationInfo) -> str:
        try:
            parsed = json.loads(v)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{info.field_name} must be valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError(f"{info.field_name} must be a JSON object")
        return v

    @model_validator(mode="after")
    def validate_llm_default_provider_is_allowed(self) -> "Settings":
        """A default provider that isn't in the allowed list would fail
        confusingly on the first real request instead of at startup -
        this is exactly the "invalid provider configuration" the sprint
        spec says production must reject up front."""
        if self.llm_default_provider is not None:
            allowed = {p.strip() for p in self.llm_allowed_providers.split(",") if p.strip()}
            if self.llm_default_provider not in allowed:
                raise ValueError(
                    f"llm_default_provider {self.llm_default_provider!r} is not in "
                    f"llm_allowed_providers ({self.llm_allowed_providers!r})"
                )
        return self

    @property
    def llm_allowed_providers_list(self) -> list[str]:
        return [p.strip() for p in self.llm_allowed_providers.split(",") if p.strip()]

    @property
    def llm_model_aliases_map(self) -> dict[str, dict[str, str]]:
        """{"<provider>": {"<alias>": "<model-name>"}}. Validated as
        well-formed JSON at startup (validate_llm_json_config); reparsed
        here rather than cached, since these blobs are tiny and this
        keeps Settings a plain, picklable/comparable model."""
        parsed = json.loads(self.llm_model_aliases)
        return {
            str(provider): {str(alias): str(model) for alias, model in aliases.items()}
            for provider, aliases in parsed.items()
        }

    @property
    def llm_pricing_map(self) -> dict[str, dict[str, str]]:
        """{"<provider>:<model>": {"input_per_1k": "...", "output_per_1k": "..."}}
        - kept as strings here (Decimal isn't JSON-serializable/hashable
        in a way pydantic-settings needs); app/llm/pricing.py converts to
        Decimal at lookup time."""
        parsed = json.loads(self.llm_pricing_json)
        return {
            str(key): {str(k): str(v) for k, v in entry.items()}
            for key, entry in parsed.items()
        }

    @field_validator("mcp_servers_json")
    @classmethod
    def validate_mcp_servers_json(cls, v: str) -> str:
        try:
            parsed = json.loads(v)
        except json.JSONDecodeError as exc:
            raise ValueError(f"mcp_servers_json must be valid JSON: {exc}") from exc
        if not isinstance(parsed, list):
            raise ValueError("mcp_servers_json must be a JSON array")
        seen_names: set[object] = set()
        for entry in parsed:
            if not isinstance(entry, dict) or "name" not in entry or "base_url" not in entry:
                raise ValueError(
                    "mcp_servers_json entries must be objects with at least "
                    "'name' and 'base_url'"
                )
            if entry["name"] in seen_names:
                raise ValueError(
                    f"mcp_servers_json has a duplicate server name: {entry['name']!r}"
                )
            seen_names.add(entry["name"])
        return v

    @property
    def mcp_servers_list(self) -> list[dict[str, str | None]]:
        """[{"name": ..., "base_url": ..., "auth_token": ... | None}, ...] -
        validated as well-formed JSON at startup (validate_mcp_servers_json).
        Reparsed here rather than cached, the same rationale as
        llm_model_aliases_map/llm_pricing_map: this blob is tiny.
        app/mcp/factory.py turns each entry into an MCPServerConfig."""
        parsed = json.loads(self.mcp_servers_json)
        return [
            {
                "name": str(entry["name"]),
                "base_url": str(entry["base_url"]),
                "auth_token": str(entry["auth_token"]) if entry.get("auth_token") else None,
            }
            for entry in parsed
        ]

    @field_validator("runtime_max_tool_iterations")
    @classmethod
    def validate_runtime_max_tool_iterations(cls, v: int) -> int:
        if v < 1:
            raise ValueError("runtime_max_tool_iterations must be at least 1")
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
