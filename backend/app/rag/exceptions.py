"""Text-extraction exception hierarchy (Sprint P10B2).

Deliberately separate from app/services/exceptions.py's ingestion-level
domain errors, same rationale as app/storage/exceptions.py's StorageError
hierarchy: these are lower-level extraction failures app/rag/extraction.py
can raise independent of any asset/ingestion business rule.
app/services/ingestion_service.py catches these and decides what they
mean for a given ingest_asset() call; they never leak past it as-is.

No FastAPI or SQLAlchemy imports here, by design - app/rag/ must stay
usable from a plain script or a unit test, same convention as
app/rag/chunking.py.
"""
from __future__ import annotations


class ExtractionError(Exception):
    """Base class for all text-extraction errors."""


class UnsupportedContentTypeError(ExtractionError):
    def __init__(self, content_type: str) -> None:
        super().__init__(f"no text extractor for content type {content_type!r}")
        self.content_type = content_type


class TextDecodingError(ExtractionError):
    """Raised when plain-text bytes aren't valid UTF-8."""

    def __init__(self, reason: str = "") -> None:
        message = "content is not valid UTF-8 text"
        if reason:
            message = f"{message} ({reason})"
        super().__init__(message)


class PdfExtractionError(ExtractionError):
    """Raised for a corrupt, unreadable, or password-encrypted PDF -
    pypdf's own exception types are never allowed to leak past
    app/rag/extraction.py."""

    def __init__(self, reason: str = "") -> None:
        message = "failed to extract text from PDF"
        if reason:
            message = f"{message} ({reason})"
        super().__init__(message)
