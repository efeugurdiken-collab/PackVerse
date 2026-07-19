"""Text extraction from raw asset bytes (Sprint P10B2).

Pure, framework-agnostic utility - no DB, no network, no model imports,
same convention as app/rag/chunking.py. Turns the bytes
app/services/ingestion_service.py reads via app.storage.base.StorageBackend
into the plain string app/rag/chunking.py's chunk_text() then splits.
Deliberately narrow: only the two content types Sprint P10B2 scopes in
(plain text/markdown, PDF) are supported - no DOCX/HTML, and no OCR for
scanned/image-only PDFs (extract_text_from_pdf returns whatever text
layer the PDF already has, which may be empty; that empty-text case is
an ingestion_service concern, not this module's).
"""
from __future__ import annotations

import io

from pypdf import PdfReader

from app.rag.exceptions import PdfExtractionError, TextDecodingError, UnsupportedContentTypeError

_PLAIN_TEXT_CONTENT_TYPES = frozenset({"text/plain", "text/markdown"})

# Public: app/services/ingestion_service.py checks an asset's content
# type against this before touching storage, so an unsupported asset
# fails without a wasted read - extract_text() below enforces the same
# set independently, so this module is still safe to call directly.
SUPPORTED_CONTENT_TYPES = _PLAIN_TEXT_CONTENT_TYPES | {"application/pdf"}


def extract_text_from_plain(content: bytes) -> str:
    """Decodes plain text/markdown bytes as UTF-8.

    Raises TextDecodingError if content isn't valid UTF-8 - never lets a
    raw UnicodeDecodeError escape this module.
    """
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TextDecodingError(str(exc)) from exc


def extract_text_from_pdf(content: bytes) -> str:
    """Extracts each page's text layer and joins them with a blank line
    between pages, in page order.

    Raises PdfExtractionError for a corrupt/unreadable PDF, or one that's
    password-encrypted (an empty-password decrypt attempt is made, since
    some PDFs are "encrypted" only to restrict permissions and have no
    real owner/user password - but a PDF that genuinely needs one still
    fails clearly here rather than raising deep inside pypdf). Returns
    whatever text pypdf finds, including "" for a scanned/image-only PDF
    with no extractable text layer - that's not an error at this layer,
    see the module docstring.
    """
    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            if reader.decrypt("") == 0:
                raise PdfExtractionError("password-protected")
        pages_text = [page.extract_text() or "" for page in reader.pages]
    except PdfExtractionError:
        raise
    except Exception as exc:  # broad on purpose - see docstring above
        raise PdfExtractionError(str(exc)) from exc

    return "\n\n".join(pages_text)


def extract_text(content: bytes, content_type: str) -> str:
    """Dispatches to the right extractor based on content_type.

    Raises UnsupportedContentTypeError for anything other than the
    content types Sprint P10B2 scopes in (see module docstring).
    """
    if content_type == "application/pdf":
        return extract_text_from_pdf(content)
    if content_type in _PLAIN_TEXT_CONTENT_TYPES:
        return extract_text_from_plain(content)
    raise UnsupportedContentTypeError(content_type)
