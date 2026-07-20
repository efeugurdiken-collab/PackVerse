"""Unit tests for app.rag.retrieval (Sprint P10B4).

Pure functions/dataclass, no fixtures/DB needed - these run as plain
sync tests, same as tests/test_chunking.py.
"""
import uuid

from app.rag.retrieval import ScoredChunk, cosine_distance_to_score


def test_cosine_distance_to_score_identical_direction_is_one() -> None:
    assert cosine_distance_to_score(0.0) == 1.0


def test_cosine_distance_to_score_orthogonal_is_zero() -> None:
    assert cosine_distance_to_score(1.0) == 0.0


def test_cosine_distance_to_score_opposite_direction_is_negative_one() -> None:
    assert cosine_distance_to_score(2.0) == -1.0


def test_cosine_distance_to_score_is_the_exact_complement() -> None:
    for distance in (0.1, 0.25, 0.5, 0.75, 1.5):
        assert cosine_distance_to_score(distance) == 1.0 - distance


def test_scored_chunk_carries_distance_and_score_together() -> None:
    chunk = ScoredChunk(
        chunk_id=uuid.uuid4(),
        asset_id=uuid.uuid4(),
        chunk_index=0,
        content="hello world",
        char_start=0,
        char_end=11,
        distance=0.3,
        score=cosine_distance_to_score(0.3),
    )
    assert chunk.distance == 0.3
    assert chunk.score == 0.7
