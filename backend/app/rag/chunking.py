"""Deterministic text chunking (Sprint P10B1).

Pure, framework-agnostic utility - no DB, no network, no model imports -
same "framework-agnostic" convention app/llm/models.py's dataclasses
follow. Produces the Chunk rows app/models/document_chunk.py's table is
shaped to store; nothing in this module writes to the database itself
(that's ingestion, out of scope for this sprint).

Character-based, not token-based: this codebase deliberately has no
tokenizer dependency (see app/llm/providers/fake.py's embedding
docstring for the same reasoning), so chunk_size/chunk_overlap are
counts of characters, not tokens.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class Chunk:
    """One chunk of a larger text. char_start/char_end are the
    half-open [start, end) offsets into the original text that produced
    `content` - i.e. content == text[char_start:char_end]."""

    index: int
    content: str
    char_start: int
    char_end: int
    content_hash: str


def chunk_text(text: str, chunk_size: int = 1000, chunk_overlap: int = 200) -> list[Chunk]:
    """Splits `text` into overlapping, deterministic chunks.

    Same (text, chunk_size, chunk_overlap) always produces the same
    output, including content_hash - callers can safely re-chunk the
    same source text and compare hashes rather than re-storing content.

    A sliding character window: each chunk after the first starts
    `chunk_size - chunk_overlap` characters after the previous chunk's
    start, so consecutive chunks share `chunk_overlap` characters of
    context. The final chunk is clipped to len(text) rather than padded,
    so it may be shorter than chunk_size.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if chunk_overlap < 0:
        raise ValueError("chunk_overlap must not be negative")
    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be smaller than chunk_size")

    if not text:
        return []

    step = chunk_size - chunk_overlap
    text_length = len(text)
    chunks: list[Chunk] = []

    start = 0
    index = 0
    while start < text_length:
        end = min(start + chunk_size, text_length)
        content = text[start:end]
        chunks.append(
            Chunk(
                index=index,
                content=content,
                char_start=start,
                char_end=end,
                content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            )
        )
        if end == text_length:
            break
        start += step
        index += 1

    return chunks
