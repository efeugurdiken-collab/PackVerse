"""Storage backend interface (Sprint P4).

Business logic (app/services/asset_service.py) depends on this abstract
interface, not on a concrete provider - app/storage/factory.py decides
which concrete backend (local.py or s3.py) to hand out at runtime, based
on settings.storage_backend.

No FastAPI or SQLAlchemy imports here, by design: this module must stay
usable from a plain script, a worker process, or a unit test without
dragging in the web framework or the ORM.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class StorageMetadata:
    """What a backend can tell you about a stored object without the
    caller having to read its full content."""

    key: str
    size_bytes: int
    content_type: str | None
    etag: str | None


class StorageBackend(ABC):
    """Async-safe storage interface.

    Every method is a coroutine, even for the local filesystem backend
    (where the underlying I/O is synchronous under the hood, wrapped via
    asyncio.to_thread) - this keeps the interface uniform so business
    logic never needs to know which concrete backend it's talking to.
    """

    @abstractmethod
    async def store(
        self, key: str, content: bytes, *, content_type: str | None = None
    ) -> StorageMetadata:
        """Writes content at key, overwriting any existing object there.

        Raises StorageWriteFailed on failure (including a rejected key -
        path traversal, absolute path, or anything that would resolve
        outside the backend's configured root/bucket namespace).
        """
        raise NotImplementedError

    @abstractmethod
    async def open(self, key: str) -> bytes:
        """Reads and returns the full content stored at key.

        Raises StorageNotFound if no object exists at key.
        """
        raise NotImplementedError

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Deletes the object at key.

        Idempotent: deleting an already-missing key is not an error and
        returns normally. Raises StorageDeleteFailed only for a genuine
        backend failure (e.g. permission denied, provider unreachable).
        """
        raise NotImplementedError

    @abstractmethod
    async def exists(self, key: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def get_metadata(self, key: str) -> StorageMetadata:
        """Raises StorageNotFound if no object exists at key."""
        raise NotImplementedError

    @abstractmethod
    async def get_download_url(self, key: str, *, expires_in: int = 300) -> str | None:
        """Returns a short-lived, directly-fetchable URL for key, or
        None if this backend has no such concept (the local backend
        always returns None; callers should fall back to streaming the
        content themselves via open() in that case).

        Raises StorageNotFound if no object exists at key.
        """
        raise NotImplementedError
