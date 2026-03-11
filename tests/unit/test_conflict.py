"""Unit tests for conflict detection."""

import math
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from mycelium.domain.conflict import (
    DetectionResult,
    cosine_similarity,
    detect_conflicts,
)
from mycelium.domain.types import Fact, FactContent, SourceType


def _make_fact(
    subject: str = "API /v2/orders",
    predicate: str = "has_status",
    obj: str = "down",
    predicate_canonical: str | None = "has_status",
    embedding: list[float] | None = None,
    **kwargs: object,
) -> Fact:
    defaults: dict[str, object] = {
        "id": uuid4(),
        "content": FactContent(subject=subject, predicate=predicate, object=obj),
        "source_agent_id": "test-agent",
        "source_type": SourceType.AGENT_EXTRACTION,
        "confidence": 0.6,
        "trust_score": 0.5,
        "valid_from": datetime.now(UTC),
        "predicate_canonical": predicate_canonical,
        "embedding": embedding,
    }
    defaults.update(kwargs)
    return Fact(**defaults)  # type: ignore[arg-type]


def _normalized(v: list[float]) -> list[float]:
    """Normalize a vector to unit length."""
    norm = math.sqrt(sum(x * x for x in v))
    if norm == 0:
        return v
    return [x / norm for x in v]


class TestCosineSimilarity:
    def test_identical_vectors(self) -> None:
        v = [1.0, 2.0, 3.0]
        assert abs(cosine_similarity(v, v) - 1.0) < 1e-9

    def test_orthogonal_vectors(self) -> None:
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert abs(cosine_similarity(a, b)) < 1e-9

    def test_opposite_vectors(self) -> None:
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert abs(cosine_similarity(a, b) + 1.0) < 1e-9

    def test_dimension_mismatch(self) -> None:
        with pytest.raises(ValueError, match="dimension mismatch"):
            cosine_similarity([1.0, 2.0], [1.0])

    def test_zero_vector(self) -> None:
        assert cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0


class TestDetectConflicts:
    def test_no_candidates(self) -> None:
        new_fact = _make_fact(embedding=[1.0, 0.0, 0.0])
        results = detect_conflicts(new_fact, [])
        assert results == []

    def test_requires_embedding(self) -> None:
        new_fact = _make_fact(embedding=None)
        with pytest.raises(ValueError, match="must have an embedding"):
            detect_conflicts(new_fact, [])

    def test_skips_candidates_without_embedding(self) -> None:
        new_fact = _make_fact(embedding=[1.0, 0.0, 0.0])
        candidate = _make_fact(obj="up", embedding=None)
        results = detect_conflicts(new_fact, [candidate])
        assert results == []

    def test_contradiction_same_canonical_different_object(self) -> None:
        # Two facts about same subject, same canonical predicate, different values
        emb = _normalized([1.0, 0.1, 0.0])
        emb_similar = _normalized([1.0, 0.15, 0.0])  # Very similar but not identical

        new_fact = _make_fact(obj="down", embedding=emb)
        candidate = _make_fact(obj="up", embedding=emb_similar)

        results = detect_conflicts(new_fact, [candidate], contradiction_threshold=0.5)
        assert len(results) == 1
        assert results[0].result == DetectionResult.CONTRADICTION

    def test_corroboration_same_canonical_same_object(self) -> None:
        emb = _normalized([1.0, 0.0, 0.0])

        new_fact = _make_fact(obj="down", embedding=emb)
        candidate = _make_fact(obj="down", embedding=emb)

        results = detect_conflicts(new_fact, [candidate], contradiction_threshold=0.5)
        assert len(results) == 1
        assert results[0].result == DetectionResult.CORROBORATION

    def test_unrelated_low_similarity(self) -> None:
        # Orthogonal embeddings = unrelated
        new_fact = _make_fact(embedding=[1.0, 0.0, 0.0])
        candidate = _make_fact(obj="up", embedding=[0.0, 1.0, 0.0])

        results = detect_conflicts(new_fact, [candidate], contradiction_threshold=0.75)
        assert results == []

    def test_skips_self(self) -> None:
        fact_id = uuid4()
        emb = [1.0, 0.0, 0.0]
        fact = _make_fact(embedding=emb, id=fact_id)

        # Same fact as candidate should be skipped
        results = detect_conflicts(fact, [fact])
        assert results == []

    def test_free_text_predicate_contradiction(self) -> None:
        # No canonical predicate — relies on embedding similarity + object comparison
        emb = _normalized([1.0, 0.1, 0.0])
        emb_similar = _normalized([1.0, 0.15, 0.0])

        new_fact = _make_fact(
            predicate="custom_status",
            obj="offline",
            predicate_canonical=None,
            embedding=emb,
        )
        candidate = _make_fact(
            predicate="service_status",
            obj="online",
            predicate_canonical=None,
            embedding=emb_similar,
        )

        results = detect_conflicts(new_fact, [candidate], contradiction_threshold=0.5)
        assert len(results) == 1
        assert results[0].result == DetectionResult.CONTRADICTION

    def test_near_identical_different_object_is_contradiction(self) -> None:
        # Very high similarity but different object values
        emb = _normalized([1.0, 0.0, 0.0])
        emb_near = _normalized([1.0, 0.01, 0.0])  # cosine > 0.999

        new_fact = _make_fact(obj="v3", embedding=emb)
        candidate = _make_fact(obj="v4", embedding=emb_near)

        results = detect_conflicts(
            new_fact, [candidate],
            contradiction_threshold=0.5,
            corroboration_threshold=0.95,
        )
        assert len(results) == 1
        assert results[0].result == DetectionResult.CONTRADICTION

    def test_multiple_candidates(self) -> None:
        emb = _normalized([1.0, 0.0, 0.0])
        emb_similar = _normalized([1.0, 0.1, 0.0])
        emb_orthogonal = _normalized([0.0, 1.0, 0.0])

        new_fact = _make_fact(obj="down", embedding=emb)
        candidates = [
            _make_fact(obj="up", embedding=emb_similar),       # contradiction
            _make_fact(obj="down", embedding=emb_similar),     # corroboration
            _make_fact(obj="other", embedding=emb_orthogonal), # unrelated
        ]

        results = detect_conflicts(new_fact, candidates, contradiction_threshold=0.5)
        assert len(results) == 2  # unrelated filtered out

        result_types = {r.result for r in results}
        assert DetectionResult.CONTRADICTION in result_types
        assert DetectionResult.CORROBORATION in result_types
