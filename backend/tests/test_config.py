"""Tests for the JWT secret resolution policy (Sprint P3 dev-automation
change): auto-generate + persist in development, never overwrite an
existing secret, fail loudly outside development.

Each test isolates itself from the real container environment/.env via
monkeypatch (delenv + chdir into a throwaway tmp_path), since the actual
dev environment this suite runs in will usually already have a real
JWT_SECRET_KEY set - without that isolation these tests would just be
exercising the "already provided" branch instead of the branch they're
meant to cover.
"""
import pytest
from pydantic import ValidationError

from app.core.config import Settings, _persist_dev_secret_to_env_file


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_missing_secret_in_development_is_generated() -> None:
    settings = Settings(environment="development")
    assert settings.jwt_secret_key is not None
    assert len(settings.jwt_secret_key) >= 43  # token_urlsafe(64) is well over this


def test_missing_secret_in_development_persists_to_existing_env_file(_isolated_env) -> None:
    env_file = _isolated_env / ".env"
    env_file.write_text("POSTGRES_USER=packverse\n")

    settings = Settings(environment="development")

    content = env_file.read_text()
    assert f"JWT_SECRET_KEY={settings.jwt_secret_key}" in content


def test_missing_secret_in_development_without_env_file_still_boots(_isolated_env) -> None:
    # No .env file created in this tmp_path - nothing to persist to.
    settings = Settings(environment="development")
    assert settings.jwt_secret_key is not None
    assert not (_isolated_env / ".env").exists()


def test_existing_secret_is_never_overwritten(_isolated_env) -> None:
    env_file = _isolated_env / ".env"
    original = "a" * 40
    env_file.write_text(f"JWT_SECRET_KEY={original}\n")

    settings = Settings(environment="development")

    assert settings.jwt_secret_key == original
    assert env_file.read_text().count("JWT_SECRET_KEY=") == 1


def test_missing_secret_outside_development_raises() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET_KEY is required"):
        Settings(environment="production")


def test_short_explicit_secret_is_rejected() -> None:
    with pytest.raises(ValidationError, match="at least"):
        Settings(environment="development", jwt_secret_key="too-short")


def test_persist_helper_returns_false_when_key_already_present(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("JWT_SECRET_KEY=existing-value\n")

    wrote = _persist_dev_secret_to_env_file("new-generated-value", env_path=env_file)

    assert wrote is False
    assert "new-generated-value" not in env_file.read_text()


def test_persist_helper_returns_false_when_file_missing(tmp_path) -> None:
    missing_path = tmp_path / "does-not-exist" / ".env"
    wrote = _persist_dev_secret_to_env_file("some-secret", env_path=missing_path)
    assert wrote is False


# --- Storage settings (Sprint P4) --------------------------------------


def test_storage_backend_defaults_to_local() -> None:
    settings = Settings(environment="development")
    assert settings.storage_backend == "local"


def test_storage_backend_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError, match="storage_backend must be one of"):
        Settings(environment="development", storage_backend="dropbox")


def test_s3_backend_without_required_settings_raises() -> None:
    with pytest.raises(ValidationError, match="requires"):
        Settings(environment="development", storage_backend="s3")


def test_s3_backend_reports_all_missing_fields() -> None:
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            environment="development",
            storage_backend="s3",
            s3_bucket="my-bucket",
        )
    message = str(exc_info.value)
    assert "S3_ENDPOINT_URL" in message
    assert "S3_REGION" in message
    assert "S3_ACCESS_KEY_ID" in message
    assert "S3_SECRET_ACCESS_KEY" in message
    assert "S3_BUCKET" not in message  # this one was provided


def test_s3_backend_with_all_required_settings_boots() -> None:
    settings = Settings(
        environment="development",
        storage_backend="s3",
        s3_endpoint_url="https://s3.example.com",
        s3_bucket="my-bucket",
        s3_region="us-east-1",
        s3_access_key_id="AKIAEXAMPLE",
        s3_secret_access_key="secret",
    )
    assert settings.storage_backend == "s3"


def test_allowed_mime_types_list_parses_comma_separated_string() -> None:
    settings = Settings(
        environment="development", allowed_mime_types="image/png, image/jpeg,application/pdf"
    )
    assert settings.allowed_mime_types_list == ["image/png", "image/jpeg", "application/pdf"]
