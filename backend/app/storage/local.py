"""Local filesystem storage backend (Sprint P4).

Used for development and the test suite. Every method wraps blocking
pathlib/file I/O in asyncio.to_thread so the async interface is real
(doesn't block the event loop under a real ASGI server) without adding a
dependency on aiofiles just for this.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
from pathlib import Path, PurePosixPath

from app.storage.base import StorageBackend, StorageMetadata
from app.storage.exceptions import (
    StorageDeleteFailed,
    StorageNotFound,
    StorageUnavailable,
    StorageWriteFailed,
)


class LocalStorageBackend(StorageBackend):
    def __init__(self, root: str) -> None:
        self._root = Path(root).resolve()
        try:
            self._root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageUnavailable(f"cannot create storage root {self._root}: {exc}") from exc

    def _resolve(self, key: str) -> Path:
        """Resolves a storage key to an absolute filesystem path inside
        self._root, rejecting anything that would escape it.

        The storage key rules (server-generated, POSIX separators, no
        "..", no absolute paths) are enforced upstream by
        asset_service's key generation, but this is the backend's own
        last line of defense - it must never trust a key blindly, since
        nothing stops a future caller from passing one through
        unvalidated.
        """
        posix_key = PurePosixPath(key)
        if posix_key.is_absolute() or ".." in posix_key.parts:
            raise StorageWriteFailed(key, "rejected: path traversal or absolute path")

        candidate = (self._root / Path(*posix_key.parts)).resolve()
        if candidate != self._root and self._root not in candidate.parents:
            raise StorageWriteFailed(key, "rejected: resolves outside the storage root")
        return candidate

    async def store(
        self, key: str, content: bytes, *, content_type: str | None = None
    ) -> StorageMetadata:
        return await asyncio.to_thread(self._store_sync, key, content, content_type)

    def _store_sync(self, key: str, content: bytes, content_type: str | None) -> StorageMetadata:
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Atomic write: create a temp file in the same directory (so the
        # final os.replace is same-filesystem, which is what makes it
        # atomic) and rename it over the target only once it's fully
        # written - a reader can never observe a partially-written file.
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(content)
            os.replace(tmp_name, path)
        except OSError as exc:
            Path(tmp_name).unlink(missing_ok=True)
            raise StorageWriteFailed(key, str(exc)) from exc

        checksum = hashlib.sha256(content).hexdigest()
        return StorageMetadata(
            key=key, size_bytes=len(content), content_type=content_type, etag=checksum
        )

    async def open(self, key: str) -> bytes:
        return await asyncio.to_thread(self._open_sync, key)

    def _open_sync(self, key: str) -> bytes:
        path = self._resolve(key)
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise StorageNotFound(key) from exc
        except OSError as exc:
            raise StorageUnavailable(str(exc)) from exc

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._delete_sync, key)

    def _delete_sync(self, key: str) -> None:
        path = self._resolve(key)
        try:
            path.unlink()
        except FileNotFoundError:
            return  # deleting an already-missing key is not an error
        except OSError as exc:
            raise StorageDeleteFailed(key, str(exc)) from exc

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(self._exists_sync, key)

    def _exists_sync(self, key: str) -> bool:
        return self._resolve(key).is_file()

    async def get_metadata(self, key: str) -> StorageMetadata:
        return await asyncio.to_thread(self._get_metadata_sync, key)

    def _get_metadata_sync(self, key: str) -> StorageMetadata:
        path = self._resolve(key)
        try:
            stat = path.stat()
        except FileNotFoundError as exc:
            raise StorageNotFound(key) from exc
        return StorageMetadata(key=key, size_bytes=stat.st_size, content_type=None, etag=None)

    async def get_download_url(self, key: str, *, expires_in: int = 300) -> str | None:
        """The local backend has no direct-fetch URL concept - raises
        StorageNotFound if the key is missing (for consistency with the
        S3 backend, which must do a HEAD/existence check before signing),
        otherwise returns None so callers fall back to streaming via
        open()."""
        if not await self.exists(key):
            raise StorageNotFound(key)
        return None
