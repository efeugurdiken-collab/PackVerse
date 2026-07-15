"""S3-compatible object storage backend (Sprint P4).

Works against AWS S3 and any S3-compatible provider (MinIO, Cloudflare
R2, etc.) via a configurable endpoint URL. Uses the synchronous boto3
client wrapped in asyncio.to_thread - the same pattern as
app/storage/local.py - rather than adding a second async AWS SDK
(aioboto3) just for this: boto3 is the reference implementation, and the
one every S3-compatible provider's own documentation assumes.

Real cloud credentials are not required to import or unit test this
module - see tests/test_storage_s3.py, which mocks the boto3 client
directly rather than hitting a real endpoint.
"""
from __future__ import annotations

import asyncio
from typing import Any

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from app.storage.base import StorageBackend, StorageMetadata
from app.storage.exceptions import (
    StorageDeleteFailed,
    StorageNotFound,
    StorageUnavailable,
    StorageWriteFailed,
)


def _is_not_found(exc: ClientError) -> bool:
    error_code = exc.response.get("Error", {}).get("Code", "")
    return error_code in {"404", "NoSuchKey", "NotFound"}


class S3StorageBackend(StorageBackend):
    def __init__(
        self,
        *,
        endpoint_url: str,
        bucket: str,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        use_ssl: bool = True,
        force_path_style: bool = False,
    ) -> None:
        self._bucket = bucket
        try:
            self._client = boto3.client(
                "s3",
                endpoint_url=endpoint_url,
                region_name=region,
                aws_access_key_id=access_key_id,
                aws_secret_access_key=secret_access_key,
                use_ssl=use_ssl,
                config=BotoConfig(
                    s3={"addressing_style": "path" if force_path_style else "auto"}
                ),
            )
        except (BotoCoreError, ValueError) as exc:
            raise StorageUnavailable(f"could not construct S3 client: {exc}") from exc

    async def store(
        self, key: str, content: bytes, *, content_type: str | None = None
    ) -> StorageMetadata:
        return await asyncio.to_thread(self._store_sync, key, content, content_type)

    def _store_sync(self, key: str, content: bytes, content_type: str | None) -> StorageMetadata:
        kwargs: dict[str, Any] = {"Bucket": self._bucket, "Key": key, "Body": content}
        if content_type:
            kwargs["ContentType"] = content_type
        try:
            response = self._client.put_object(**kwargs)
        except (ClientError, BotoCoreError) as exc:
            raise StorageWriteFailed(key, str(exc)) from exc

        etag: str | None = str(response.get("ETag", "")).strip('"') or None
        return StorageMetadata(
            key=key, size_bytes=len(content), content_type=content_type, etag=etag
        )

    async def open(self, key: str) -> bytes:
        return await asyncio.to_thread(self._open_sync, key)

    def _open_sync(self, key: str) -> bytes:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            body: bytes = response["Body"].read()
            return body
        except ClientError as exc:
            if _is_not_found(exc):
                raise StorageNotFound(key) from exc
            raise StorageUnavailable(str(exc)) from exc
        except BotoCoreError as exc:
            raise StorageUnavailable(str(exc)) from exc

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._delete_sync, key)

    def _delete_sync(self, key: str) -> None:
        try:
            # delete_object is idempotent by design in the S3 API itself
            # - deleting an already-missing key returns 204, not an
            # error - so no special-casing is needed here, unlike the
            # local backend's explicit FileNotFoundError handling.
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except (ClientError, BotoCoreError) as exc:
            raise StorageDeleteFailed(key, str(exc)) from exc

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(self._exists_sync, key)

    def _exists_sync(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except ClientError as exc:
            if _is_not_found(exc):
                return False
            raise StorageUnavailable(str(exc)) from exc
        except BotoCoreError as exc:
            raise StorageUnavailable(str(exc)) from exc

    async def get_metadata(self, key: str) -> StorageMetadata:
        return await asyncio.to_thread(self._get_metadata_sync, key)

    def _get_metadata_sync(self, key: str) -> StorageMetadata:
        try:
            response = self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if _is_not_found(exc):
                raise StorageNotFound(key) from exc
            raise StorageUnavailable(str(exc)) from exc
        except BotoCoreError as exc:
            raise StorageUnavailable(str(exc)) from exc

        etag: str | None = str(response.get("ETag", "")).strip('"') or None
        return StorageMetadata(
            key=key,
            size_bytes=int(response.get("ContentLength", 0)),
            content_type=response.get("ContentType"),
            etag=etag,
        )

    async def get_download_url(self, key: str, *, expires_in: int = 300) -> str | None:
        return await asyncio.to_thread(self._get_download_url_sync, key, expires_in)

    def _get_download_url_sync(self, key: str, expires_in: int) -> str:
        # generate_presigned_url doesn't itself check existence, so do an
        # explicit head first - this keeps behavior consistent with the
        # local backend (StorageNotFound raised before returning
        # anything, rather than handing back a URL for nothing).
        if not self._exists_sync(key):
            raise StorageNotFound(key)
        try:
            url: str = self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=expires_in,
            )
            return url
        except (ClientError, BotoCoreError) as exc:
            raise StorageUnavailable(str(exc)) from exc
