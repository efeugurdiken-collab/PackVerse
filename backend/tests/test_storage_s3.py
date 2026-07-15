"""Unit tests for app.storage.s3.S3StorageBackend (Sprint P4).

No real AWS/S3-compatible endpoint is contacted - app.storage.s3.boto3.client
is patched with a MagicMock standing in for boto3's S3 client, so these
tests verify our own wrapping/error-mapping logic, not boto3 or AWS itself.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from app.storage.exceptions import (
    StorageDeleteFailed,
    StorageNotFound,
    StorageUnavailable,
    StorageWriteFailed,
)
from app.storage.s3 import S3StorageBackend

_CTOR_KWARGS = dict(
    endpoint_url="https://s3.example.com",
    bucket="packverse-assets",
    region="us-east-1",
    access_key_id="AKIAEXAMPLE",
    secret_access_key="secret",
)


def _client_error(code: str, operation: str = "SomeOperation") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "boom"}}, operation)


@pytest.fixture
def mock_boto_client() -> Any:
    with patch("app.storage.s3.boto3.client") as ctor:
        client = MagicMock()
        ctor.return_value = client
        yield client


@pytest.fixture
def backend(mock_boto_client: Any) -> S3StorageBackend:
    return S3StorageBackend(**_CTOR_KWARGS)


async def test_store_calls_put_object_and_returns_metadata(
    backend: S3StorageBackend, mock_boto_client: Any
) -> None:
    mock_boto_client.put_object.return_value = {"ETag": '"abc123"'}

    metadata = await backend.store("products/p1/a1/file.txt", b"hello", content_type="text/plain")

    mock_boto_client.put_object.assert_called_once_with(
        Bucket="packverse-assets",
        Key="products/p1/a1/file.txt",
        Body=b"hello",
        ContentType="text/plain",
    )
    assert metadata.etag == "abc123"
    assert metadata.size_bytes == 5


async def test_store_maps_client_error_to_storage_write_failed(
    backend: S3StorageBackend, mock_boto_client: Any
) -> None:
    mock_boto_client.put_object.side_effect = _client_error("500")
    with pytest.raises(StorageWriteFailed):
        await backend.store("k", b"data")


async def test_open_returns_object_body(backend: S3StorageBackend, mock_boto_client: Any) -> None:
    body = MagicMock()
    body.read.return_value = b"the content"
    mock_boto_client.get_object.return_value = {"Body": body}

    result = await backend.open("k")

    assert result == b"the content"
    mock_boto_client.get_object.assert_called_once_with(Bucket="packverse-assets", Key="k")


async def test_open_missing_key_raises_storage_not_found(
    backend: S3StorageBackend, mock_boto_client: Any
) -> None:
    mock_boto_client.get_object.side_effect = _client_error("NoSuchKey")
    with pytest.raises(StorageNotFound):
        await backend.open("missing")


async def test_open_other_client_error_raises_storage_unavailable(
    backend: S3StorageBackend, mock_boto_client: Any
) -> None:
    mock_boto_client.get_object.side_effect = _client_error("AccessDenied")
    with pytest.raises(StorageUnavailable):
        await backend.open("k")


async def test_delete_calls_delete_object(
    backend: S3StorageBackend, mock_boto_client: Any
) -> None:
    await backend.delete("k")
    mock_boto_client.delete_object.assert_called_once_with(Bucket="packverse-assets", Key="k")


async def test_delete_maps_client_error_to_storage_delete_failed(
    backend: S3StorageBackend, mock_boto_client: Any
) -> None:
    mock_boto_client.delete_object.side_effect = _client_error("500")
    with pytest.raises(StorageDeleteFailed):
        await backend.delete("k")


async def test_exists_true_when_head_object_succeeds(
    backend: S3StorageBackend, mock_boto_client: Any
) -> None:
    mock_boto_client.head_object.return_value = {}
    assert await backend.exists("k") is True


async def test_exists_false_when_head_object_reports_404(
    backend: S3StorageBackend, mock_boto_client: Any
) -> None:
    mock_boto_client.head_object.side_effect = _client_error("404")
    assert await backend.exists("k") is False


async def test_exists_raises_storage_unavailable_for_non_404_error(
    backend: S3StorageBackend, mock_boto_client: Any
) -> None:
    mock_boto_client.head_object.side_effect = _client_error("AccessDenied")
    with pytest.raises(StorageUnavailable):
        await backend.exists("k")


async def test_get_metadata_returns_parsed_fields(
    backend: S3StorageBackend, mock_boto_client: Any
) -> None:
    mock_boto_client.head_object.return_value = {
        "ETag": '"xyz"',
        "ContentLength": 42,
        "ContentType": "image/png",
    }
    metadata = await backend.get_metadata("k")
    assert metadata.etag == "xyz"
    assert metadata.size_bytes == 42
    assert metadata.content_type == "image/png"


async def test_get_metadata_missing_key_raises_storage_not_found(
    backend: S3StorageBackend, mock_boto_client: Any
) -> None:
    mock_boto_client.head_object.side_effect = _client_error("NotFound")
    with pytest.raises(StorageNotFound):
        await backend.get_metadata("k")


async def test_get_download_url_generates_signed_url_when_object_exists(
    backend: S3StorageBackend, mock_boto_client: Any
) -> None:
    mock_boto_client.head_object.return_value = {}
    mock_boto_client.generate_presigned_url.return_value = "https://signed.example.com/k?sig=abc"

    url = await backend.get_download_url("k", expires_in=120)

    assert url == "https://signed.example.com/k?sig=abc"
    mock_boto_client.generate_presigned_url.assert_called_once_with(
        "get_object",
        Params={"Bucket": "packverse-assets", "Key": "k"},
        ExpiresIn=120,
    )


async def test_get_download_url_missing_key_raises_storage_not_found_without_signing(
    backend: S3StorageBackend, mock_boto_client: Any
) -> None:
    mock_boto_client.head_object.side_effect = _client_error("404")
    with pytest.raises(StorageNotFound):
        await backend.get_download_url("missing")
    mock_boto_client.generate_presigned_url.assert_not_called()


def test_constructor_passes_configured_credentials_and_endpoint(mock_boto_client: Any) -> None:
    with patch("app.storage.s3.boto3.client") as ctor:
        ctor.return_value = MagicMock()
        S3StorageBackend(**_CTOR_KWARGS, use_ssl=False, force_path_style=True)
        _, kwargs = ctor.call_args
        assert kwargs["endpoint_url"] == "https://s3.example.com"
        assert kwargs["region_name"] == "us-east-1"
        assert kwargs["aws_access_key_id"] == "AKIAEXAMPLE"
        assert kwargs["use_ssl"] is False
