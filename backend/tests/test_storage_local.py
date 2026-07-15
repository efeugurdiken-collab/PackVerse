"""Unit tests for app.storage.local.LocalStorageBackend (Sprint P4).

These tests instantiate LocalStorageBackend directly against pytest's
tmp_path - they never go through the FastAPI app or a database, since the
storage layer is deliberately framework-agnostic (see app/storage/base.py).
"""
import os
from pathlib import Path

import pytest

from app.storage.exceptions import StorageNotFound, StorageWriteFailed
from app.storage.local import LocalStorageBackend


@pytest.fixture
def backend(tmp_path: Path) -> LocalStorageBackend:
    return LocalStorageBackend(str(tmp_path / "storage-root"))


async def test_store_creates_object_and_returns_metadata(backend: LocalStorageBackend) -> None:
    metadata = await backend.store("products/p1/a1/file.txt", b"hello world", content_type="text/plain")
    assert metadata.key == "products/p1/a1/file.txt"
    assert metadata.size_bytes == len(b"hello world")
    assert metadata.content_type == "text/plain"
    assert metadata.etag  # sha256 hex digest, non-empty


async def test_open_reads_back_exact_content(backend: LocalStorageBackend) -> None:
    content = b"\x00\x01binary-ish content\xff"
    await backend.store("products/p1/a1/blob.bin", content)
    assert await backend.open("products/p1/a1/blob.bin") == content


async def test_exists_true_for_stored_object(backend: LocalStorageBackend) -> None:
    await backend.store("k1", b"data")
    assert await backend.exists("k1") is True


async def test_exists_false_for_missing_object(backend: LocalStorageBackend) -> None:
    assert await backend.exists("never-written") is False


async def test_delete_removes_object(backend: LocalStorageBackend) -> None:
    await backend.store("k2", b"data")
    await backend.delete("k2")
    assert await backend.exists("k2") is False


async def test_delete_is_idempotent_for_missing_key(backend: LocalStorageBackend) -> None:
    # Must not raise - deleting an already-missing key succeeds silently.
    await backend.delete("was-never-here")


async def test_open_missing_object_raises_storage_not_found(backend: LocalStorageBackend) -> None:
    with pytest.raises(StorageNotFound):
        await backend.open("does-not-exist")


async def test_get_metadata_missing_object_raises_storage_not_found(
    backend: LocalStorageBackend,
) -> None:
    with pytest.raises(StorageNotFound):
        await backend.get_metadata("does-not-exist")


async def test_get_metadata_returns_size(backend: LocalStorageBackend) -> None:
    await backend.store("k3", b"twelve bytes")
    metadata = await backend.get_metadata("k3")
    assert metadata.size_bytes == len(b"twelve bytes")
    assert metadata.key == "k3"


@pytest.mark.parametrize(
    "malicious_key",
    [
        "../escape.txt",
        "products/../../etc/passwd",
        "/etc/passwd",
        "a/../../b",
    ],
)
async def test_path_traversal_keys_are_rejected(
    backend: LocalStorageBackend, malicious_key: str
) -> None:
    with pytest.raises(StorageWriteFailed):
        await backend.store(malicious_key, b"payload")


async def test_path_traversal_key_never_escapes_storage_root(
    backend: LocalStorageBackend, tmp_path: Path
) -> None:
    # Even if a rejected key somehow reached the filesystem layer, nothing
    # should ever be written outside the configured root.
    with pytest.raises(StorageWriteFailed):
        await backend.store("../outside.txt", b"payload")
    assert not (tmp_path / "outside.txt").exists()


async def test_store_overwrites_existing_object(backend: LocalStorageBackend) -> None:
    await backend.store("k4", b"version one")
    await backend.store("k4", b"version two")
    assert await backend.open("k4") == b"version two"


async def test_store_is_atomic_no_temp_files_left_behind(
    backend: LocalStorageBackend, tmp_path: Path
) -> None:
    await backend.store("products/p1/a1/file.txt", b"content")
    directory = tmp_path / "storage-root" / "products" / "p1" / "a1"
    leftover_temp_files = [f for f in os.listdir(directory) if f.startswith(".tmp-")]
    assert leftover_temp_files == []


async def test_store_creates_nested_directories_automatically(
    backend: LocalStorageBackend, tmp_path: Path
) -> None:
    await backend.store("a/b/c/d/deep.txt", b"nested")
    assert (tmp_path / "storage-root" / "a" / "b" / "c" / "d" / "deep.txt").is_file()


async def test_get_download_url_returns_none_for_existing_local_object(
    backend: LocalStorageBackend,
) -> None:
    await backend.store("k5", b"data")
    assert await backend.get_download_url("k5") is None


async def test_get_download_url_missing_object_raises_storage_not_found(
    backend: LocalStorageBackend,
) -> None:
    with pytest.raises(StorageNotFound):
        await backend.get_download_url("missing")


def test_constructor_creates_storage_root_if_missing(tmp_path: Path) -> None:
    root = tmp_path / "does" / "not" / "exist" / "yet"
    assert not root.exists()
    LocalStorageBackend(str(root))
    assert root.is_dir()
