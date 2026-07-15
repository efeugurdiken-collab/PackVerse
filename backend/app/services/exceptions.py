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
