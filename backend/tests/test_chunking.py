"""Unit tests for app.rag.chunking.chunk_text (Sprint P10B1).

Pure function, no fixtures/DB needed - these run as plain sync tests
(pytest-asyncio's "auto" mode leaves non-coroutine functions alone, same
as tests/test_migrations.py).
"""
import hashlib

import pytest

from app.rag.chunking import Chunk, chunk_text


def test_chunk_text_empty_string_returns_no_chunks() -> None:
    assert chunk_text("") == []


def test_chunk_text_shorter_than_chunk_size_returns_single_chunk() -> None:
    chunks = chunk_text("hello world", chunk_size=1000, chunk_overlap=200)
    assert len(chunks) == 1
    assert chunks[0] == Chunk(
        index=0,
        content="hello world",
        char_start=0,
        char_end=11,
        content_hash=hashlib.sha256(b"hello world").hexdigest(),
    )


def test_chunk_text_is_deterministic() -> None:
    text = "The quick brown fox jumps over the lazy dog. " * 20
    first = chunk_text(text, chunk_size=100, chunk_overlap=20)
    second = chunk_text(text, chunk_size=100, chunk_overlap=20)
    assert first == second


def test_chunk_text_covers_full_text_with_overlap() -> None:
    text = "x" * 1000
    chunks = chunk_text(text, chunk_size=400, chunk_overlap=100)

    assert [c.index for c in chunks] == list(range(len(chunks)))
    assert chunks[0].char_start == 0
    assert chunks[-1].char_end == len(text)

    # Consecutive chunks overlap by exactly chunk_overlap characters.
    for prev, nxt in zip(chunks, chunks[1:]):
        assert nxt.char_start == prev.char_start + (400 - 100)
        assert prev.char_end - nxt.char_start == 100

    # Reassembling via char_start/char_end reproduces the exact source text.
    for chunk in chunks:
        assert text[chunk.char_start : chunk.char_end] == chunk.content


def test_chunk_text_exact_multiple_of_chunk_size() -> None:
    text = "a" * 1000
    chunks = chunk_text(text, chunk_size=500, chunk_overlap=0)
    assert len(chunks) == 2
    assert chunks[0].char_start == 0 and chunks[0].char_end == 500
    assert chunks[1].char_start == 500 and chunks[1].char_end == 1000


def test_chunk_text_content_hash_matches_sha256_of_content() -> None:
    chunks = chunk_text("some deterministic text", chunk_size=10, chunk_overlap=2)
    for chunk in chunks:
        assert chunk.content_hash == hashlib.sha256(chunk.content.encode("utf-8")).hexdigest()


def test_chunk_text_handles_unicode() -> None:
    text = "café éè \U0001F600 " * 50
    chunks = chunk_text(text, chunk_size=37, chunk_overlap=7)
    assert chunks
    for chunk in chunks:
        assert text[chunk.char_start : chunk.char_end] == chunk.content
        assert chunk.content_hash == hashlib.sha256(chunk.content.encode("utf-8")).hexdigest()


@pytest.mark.parametrize(
    "chunk_size,chunk_overlap",
    [(0, 0), (-10, 0), (100, -1), (100, 100), (100, 150)],
)
def test_chunk_text_rejects_invalid_size_or_overlap(chunk_size: int, chunk_overlap: int) -> None:
    with pytest.raises(ValueError):
        chunk_text("some text", chunk_size=chunk_size, chunk_overlap=chunk_overlap)
