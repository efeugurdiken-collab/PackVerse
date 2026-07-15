"""Selects and caches the configured storage backend.

app/services/asset_service.py calls get_storage_backend() and depends
only on the returned StorageBackend interface - it never imports
LocalStorageBackend or S3StorageBackend directly, so swapping the
configured provider never touches business logic.
"""
from functools import lru_cache

from app.core.config import get_settings
from app.storage.base import StorageBackend
from app.storage.local import LocalStorageBackend
from app.storage.s3 import S3StorageBackend


@lru_cache
def get_storage_backend() -> StorageBackend:
    """Cached for the lifetime of the process, same rationale as
    app.core.config.get_settings: constructing a backend (e.g. the S3
    client, or creating the local root directory) is meant to happen
    once, not per-request."""
    settings = get_settings()

    if settings.storage_backend == "s3":
        # validate_s3_settings_when_selected already guarantees these
        # are non-empty by the time Settings finishes constructing; the
        # asserts are a static-typing bridge (str | None -> str), not a
        # runtime safety net - see app.core.config.Settings.jwt_secret
        # for the same pattern applied to the JWT secret.
        assert settings.s3_endpoint_url is not None
        assert settings.s3_bucket is not None
        assert settings.s3_region is not None
        assert settings.s3_access_key_id is not None
        assert settings.s3_secret_access_key is not None
        return S3StorageBackend(
            endpoint_url=settings.s3_endpoint_url,
            bucket=settings.s3_bucket,
            region=settings.s3_region,
            access_key_id=settings.s3_access_key_id,
            secret_access_key=settings.s3_secret_access_key,
            use_ssl=settings.s3_use_ssl,
            force_path_style=settings.s3_force_path_style,
        )

    return LocalStorageBackend(settings.storage_local_root)
