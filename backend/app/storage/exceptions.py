"""Storage-backend-level exceptions.

Deliberately separate from app/services/exceptions.py's asset-level
domain errors (AssetNotFound, UnsupportedFileType, etc.) - these are
lower-level I/O failures that any backend (local filesystem, S3-
compatible object storage) can raise, independent of any asset/product
business rule. The asset service layer catches these and decides what
they mean for a given operation; it never lets them leak past it as-is.
"""


class StorageError(Exception):
    """Base class for all storage-backend errors."""


class StorageNotFound(StorageError):
    def __init__(self, key: str) -> None:
        super().__init__(f"storage object not found: {key}")
        self.key = key


class StorageUnavailable(StorageError):
    """The backend itself is unreachable or misconfigured - connection
    refused, authentication failure, bucket doesn't exist, local storage
    root can't be created, etc. Distinct from StorageNotFound: this means
    "I couldn't even check", not "I checked and it's not there"."""


class StorageWriteFailed(StorageError):
    def __init__(self, key: str, reason: str = "") -> None:
        message = f"failed to write storage object: {key}"
        if reason:
            message = f"{message} ({reason})"
        super().__init__(message)
        self.key = key


class StorageDeleteFailed(StorageError):
    def __init__(self, key: str, reason: str = "") -> None:
        message = f"failed to delete storage object: {key}"
        if reason:
            message = f"{message} ({reason})"
        super().__init__(message)
        self.key = key
