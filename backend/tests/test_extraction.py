"""Tests for app.rag.extraction: plain text/markdown decoding, PDF text
extraction, and the extract_text() content-type dispatcher (Sprint
P10B2).

No fixture files and no extra test dependency: _build_minimal_pdf below
hand-builds a real, byte-correct PDF (with an actual text content
stream per page, not a blank page) by tracking each object's offset as
it's written and using those recorded offsets in the xref table - so it
stays correct regardless of how the per-page text changes, rather than
relying on hardcoded byte offsets. The encrypted-PDF test uses pypdf's
own PdfWriter.encrypt() (pypdf is already a runtime dependency) instead
of a second hand-built PDF variant.
"""
from __future__ import annotations

import io

import pytest
from pypdf import PdfWriter

from app.rag.exceptions import PdfExtractionError, TextDecodingError, UnsupportedContentTypeError
from app.rag.extraction import (
    SUPPORTED_CONTENT_TYPES,
    extract_text,
    extract_text_from_pdf,
    extract_text_from_plain,
)


def _build_minimal_pdf(*, pages: list[str]) -> bytes:
    def content_stream(text: str) -> bytes:
        stream = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
        return b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"

    num_pages = len(pages)
    font_obj_num = 3 + 2 * num_pages

    objects: list[bytes] = []
    kids = " ".join(f"{3 + i} 0 R" for i in range(num_pages))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")  # obj 1
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {num_pages} >>".encode())  # obj 2
    for i in range(num_pages):
        content_num = 3 + num_pages + i
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R "
                f"/Resources << /Font << /F1 {font_obj_num} 0 R >> >> "
                f"/MediaBox [0 0 612 792] /Contents {content_num} 0 R >>"
            ).encode()
        )
    for text in pages:
        objects.append(content_stream(text))
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")  # font obj

    buf = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for idx, obj in enumerate(objects, start=1):
        offsets.append(len(buf))
        buf += f"{idx} 0 obj\n".encode() + obj + b"\nendobj\n"

    xref_offset = len(buf)
    total_objs = len(objects) + 1
    buf += f"xref\n0 {total_objs}\n".encode()
    buf += b"0000000000 65535 f \n"
    for off in offsets:
        buf += f"{off:010d} 00000 n \n".encode()
    buf += f"trailer\n<< /Size {total_objs} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF".encode()
    return bytes(buf)


def _build_encrypted_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt(user_password="secret", owner_password="owner-secret")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


# --- extract_text_from_plain ---


def test_extract_text_from_plain_round_trips_utf8() -> None:
    assert extract_text_from_plain("Hello, world!".encode("utf-8")) == "Hello, world!"


def test_extract_text_from_plain_handles_unicode() -> None:
    text = "café ☃ 日本語"
    assert extract_text_from_plain(text.encode("utf-8")) == text


def test_extract_text_from_plain_empty_bytes_returns_empty_string() -> None:
    assert extract_text_from_plain(b"") == ""


def test_extract_text_from_plain_rejects_invalid_utf8() -> None:
    with pytest.raises(TextDecodingError):
        extract_text_from_plain(b"\xff\xfe not valid utf-8")


# --- extract_text_from_pdf ---


def test_extract_text_from_pdf_single_page() -> None:
    pdf_bytes = _build_minimal_pdf(pages=["Hello chunking world"])
    text = extract_text_from_pdf(pdf_bytes)
    assert "Hello chunking world" in text


def test_extract_text_from_pdf_multi_page_joins_in_order() -> None:
    pdf_bytes = _build_minimal_pdf(pages=["Page one content", "Page two content"])
    text = extract_text_from_pdf(pdf_bytes)
    assert "Page one content" in text
    assert "Page two content" in text
    assert text.index("Page one content") < text.index("Page two content")


def test_extract_text_from_pdf_rejects_corrupt_bytes() -> None:
    with pytest.raises(PdfExtractionError):
        extract_text_from_pdf(b"%PDF-1.4\nthis is not a well-formed PDF body at all")


def test_extract_text_from_pdf_rejects_password_protected() -> None:
    pdf_bytes = _build_encrypted_pdf()
    with pytest.raises(PdfExtractionError):
        extract_text_from_pdf(pdf_bytes)


# --- extract_text dispatcher ---


def test_extract_text_dispatches_pdf() -> None:
    pdf_bytes = _build_minimal_pdf(pages=["Dispatcher test content"])
    assert "Dispatcher test content" in extract_text(pdf_bytes, "application/pdf")


@pytest.mark.parametrize("content_type", ["text/plain", "text/markdown"])
def test_extract_text_dispatches_plain(content_type: str) -> None:
    assert extract_text(b"plain body", content_type) == "plain body"


def test_extract_text_rejects_unsupported_content_type() -> None:
    with pytest.raises(UnsupportedContentTypeError):
        extract_text(b"...", "image/png")


def test_supported_content_types_matches_dispatcher() -> None:
    assert SUPPORTED_CONTENT_TYPES == {"application/pdf", "text/plain", "text/markdown"}
