"""Domain-level exceptions.

Services raise these; the API layer (app/api/) is the only place that
translates them into HTTP status codes, so business logic stays
transport-agnostic.
"""


class DomainError(Exception):
    """Base class for all service-layer errors."""


class ProductNotFoundError(DomainError):
    def __init__(self, product_id: object) -> None:
        super().__init__(f"Product {product_id} not found")
        self.product_id = product_id


class DuplicateSlugError(DomainError):
    def __init__(self, slug: str) -> None:
        super().__init__(f"Product with slug {slug!r} already exists")
        self.slug = slug


class DuplicateEmailError(DomainError):
    def __init__(self, email: str) -> None:
        super().__init__(f"User with email {email!r} already exists")
        self.email = email


class UserNotFoundError(DomainError):
    def __init__(self, identifier: object) -> None:
        super().__init__(f"User {identifier} not found")
        self.identifier = identifier


class InvalidCredentialsError(DomainError):
    """Deliberately message-free of *why* - see app/api/v1/auth.py's login
    endpoint, which maps this to one generic 401 regardless of whether the
    email didn't exist, the password was wrong, or the account is
    disabled, so failed attempts can't be used to enumerate accounts."""

    def __init__(self) -> None:
        super().__init__("invalid credentials")


# --- Asset / upload domain errors (Sprint P4) ---
# Deliberately generic messages: none of these include the storage key,
# filesystem path, bucket name, or any storage credential - see the
# Security rule against leaking internal storage details through error
# responses. app/api/v1/assets.py maps each to a specific HTTP status.


class AssetNotFoundError(DomainError):
    def __init__(self, asset_id: object) -> None:
        super().__init__(f"Asset {asset_id} not found")
        self.asset_id = asset_id


class AssetDeletedError(DomainError):
    def __init__(self, asset_id: object) -> None:
        super().__init__(f"Asset {asset_id} has been deleted")
        self.asset_id = asset_id


class UnsupportedFileTypeError(DomainError):
    def __init__(self, content_type: str) -> None:
        super().__init__(f"Unsupported file type: {content_type!r}")
        self.content_type = content_type


class FileTooLargeError(DomainError):
    def __init__(self, size_bytes: int, max_bytes: int) -> None:
        super().__init__(f"File too large: {size_bytes} bytes (maximum {max_bytes})")
        self.size_bytes = size_bytes
        self.max_bytes = max_bytes


class EmptyFileError(DomainError):
    def __init__(self) -> None:
        super().__init__("Uploaded file is empty")


class InvalidFilenameError(DomainError):
    def __init__(self, filename: str) -> None:
        super().__init__("Uploaded filename is invalid")
        self.filename = filename


class AssetStorageOperationFailedError(DomainError):
    """Wraps a lower-level app.storage.exceptions.StorageError so the API
    layer only ever needs to catch domain errors, never reach into the
    storage layer's own exception types directly."""

    def __init__(self, operation: str) -> None:
        super().__init__(f"Storage {operation} failed")
        self.operation = operation


# --- LLM Gateway domain errors (Sprint P5) ---
# app.llm.exceptions.LLMError and its subclasses are the LLM Gateway's
# own transport-agnostic error hierarchy (raised by app/llm/gateway.py
# and the provider adapters); app/api/v1/llm.py maps those directly.
# LLMRequestNotFoundError below is an ordinary service-layer domain
# error for a request-history row - kept here for consistency with
# every other *NotFoundError in this file, rather than added to
# app/llm/exceptions.py.


class LLMRequestNotFoundError(DomainError):
    """Raised both for a genuinely-missing id and for an id that exists
    but isn't visible to the caller (a non-admin requesting someone
    else's request) - both map to the same 404, so the endpoint can't be
    used to enumerate other users' request ids. See
    app/services/llm_service.py's get_request."""

    def __init__(self, request_id: object) -> None:
        super().__init__(f"LLM request {request_id} not found")
        self.request_id = request_id
