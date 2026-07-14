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
