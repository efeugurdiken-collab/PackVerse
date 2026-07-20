"""Pure retrieval scoring logic (Sprint P10B4).

Pure, framework-agnostic utility - no DB, no network, no model imports -
same "framework-agnostic" convention app/rag/chunking.py follows. The
actual similarity search (a pgvector query against document_chunks) is
not pure - it needs an AsyncSession and an LLMGateway - so it lives in
app/services/retrieval_service.py, not here; this module holds only the
part of ranking that's plain arithmetic and worth unit-testing in
isolation without a database.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass


def cosine_distance_to_score(distance: float) -> float:
    """Converts a pgvector cosine distance (`<=>`, range [0, 2], 0 =
    identical direction) into a similarity score (1 = identical
    direction, 0 = orthogonal, negative = opposite direction) - the
    standard `score = 1 - cosine_distance` relationship. Kept as its own
    function (not inlined at the query call site) so
    app/services/retrieval_service.py's query-construction code and this
    conversion can be tested independently - a wrong ORDER BY direction
    and a wrong score formula are different bug classes, and this keeps
    a test for the second from requiring a database."""
    return 1.0 - distance


@dataclass(frozen=True)
class ScoredChunk:
    """One document_chunks row plus its distance/score against a single
    query embedding (app/services/retrieval_service.py's search()
    result shape). Both distance and score are always carried together,
    never just one - see cosine_distance_to_score's docstring for why a
    caller might reasonably want either."""

    chunk_id: uuid.UUID
    asset_id: uuid.UUID
    chunk_index: int
    content: str
    char_start: int
    char_end: int
    distance: float
    score: float
