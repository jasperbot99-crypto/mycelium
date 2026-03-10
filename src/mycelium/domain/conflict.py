"""Conflict detection — ARCHITECTURE.md Section 4.4.

Pure domain logic. Given a new fact and existing candidates, determines
contradictions and corroborations. No I/O — candidates are passed in.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mycelium.domain.types import Fact


class DetectionResult(StrEnum):
    """Outcome of comparing two facts."""

    CONTRADICTION = "contradiction"
    CORROBORATION = "corroboration"
    UNRELATED = "unrelated"


@dataclass(frozen=True)
class ConflictCandidate:
    """Result of comparing a new fact against an existing fact."""

    existing_fact: Fact
    result: DetectionResult
    similarity: float
    reason: str


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors.

    Returns value in [-1.0, 1.0]. For normalized embeddings, this is the dot product.
    """
    if len(a) != len(b):
        raise ValueError(f"Vector dimension mismatch: {len(a)} vs {len(b)}")

    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))

    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0

    return dot / (norm_a * norm_b)


def detect_conflicts(
    new_fact: Fact,
    candidates: list[Fact],
    contradiction_threshold: float = 0.75,
    corroboration_threshold: float = 0.95,
) -> list[ConflictCandidate]:
    """Compare a new fact against candidate facts for contradictions and corroborations.

    ARCHITECTURE.md Section 4.4 detection strategy:
    1. Candidates are pre-filtered by subject (same subject, active, not expired)
    2. Semantic similarity determines if facts are related
    3. Predicate analysis determines contradiction vs. corroboration

    Args:
        new_fact: The newly ingested fact (must have embedding).
        candidates: Existing active facts with same subject (must have embeddings).
        contradiction_threshold: Minimum cosine similarity to consider facts related.
        corroboration_threshold: Minimum cosine similarity for near-identical claims.

    Returns:
        List of ConflictCandidate results (contradictions and corroborations only,
        unrelated facts are filtered out).
    """
    if new_fact.embedding is None:
        raise ValueError("new_fact must have an embedding for conflict detection")

    results: list[ConflictCandidate] = []

    for candidate in candidates:
        if candidate.embedding is None:
            continue

        if candidate.id == new_fact.id:
            continue

        similarity = cosine_similarity(new_fact.embedding, candidate.embedding)

        # Below threshold = unrelated, skip
        if similarity < contradiction_threshold:
            continue

        # Near-identical = potential corroboration
        if similarity >= corroboration_threshold:
            result = _analyze_corroboration_or_duplicate(new_fact, candidate, similarity)
            results.append(result)
            continue

        # Middle zone: high similarity but not identical — potential contradiction
        result = _analyze_contradiction(new_fact, candidate, similarity)
        results.append(result)

    return results


def _analyze_corroboration_or_duplicate(
    new_fact: Fact,
    candidate: Fact,
    similarity: float,
) -> ConflictCandidate:
    """Analyze two very similar facts — corroboration or exact duplicate."""
    # Same canonical predicate + same object = corroboration
    if (
        new_fact.predicate_canonical is not None
        and candidate.predicate_canonical is not None
        and new_fact.predicate_canonical == candidate.predicate_canonical
        and new_fact.content.object.strip().lower() == candidate.content.object.strip().lower()
    ):
        return ConflictCandidate(
            existing_fact=candidate,
            result=DetectionResult.CORROBORATION,
            similarity=similarity,
            reason=(
                f"Same canonical predicate '{new_fact.predicate_canonical}' "
                f"with same object value"
            ),
        )

    # High similarity + same object text = corroboration (even without canonical match)
    if new_fact.content.object.strip().lower() == candidate.content.object.strip().lower():
        return ConflictCandidate(
            existing_fact=candidate,
            result=DetectionResult.CORROBORATION,
            similarity=similarity,
            reason="Near-identical embedding with same object value",
        )

    # High similarity + different object = contradiction (same claim, different value)
    return ConflictCandidate(
        existing_fact=candidate,
        result=DetectionResult.CONTRADICTION,
        similarity=similarity,
        reason=(
            f"Near-identical embedding (similarity={similarity:.3f}) "
            f"but different object values: "
            f"'{new_fact.content.object}' vs '{candidate.content.object}'"
        ),
    )


def _analyze_contradiction(
    new_fact: Fact,
    candidate: Fact,
    similarity: float,
) -> ConflictCandidate:
    """Analyze two related-but-not-identical facts — contradiction or unrelated."""
    # Both have canonical predicates
    if (
        new_fact.predicate_canonical is not None
        and candidate.predicate_canonical is not None
        and new_fact.predicate_canonical == candidate.predicate_canonical
    ):
        # Same canonical predicate + different object = contradiction
        if (
            new_fact.content.object.strip().lower()
            != candidate.content.object.strip().lower()
        ):
            return ConflictCandidate(
                existing_fact=candidate,
                result=DetectionResult.CONTRADICTION,
                similarity=similarity,
                reason=(
                    f"Same canonical predicate '{new_fact.predicate_canonical}' "
                    f"with different object: "
                    f"'{new_fact.content.object}' vs '{candidate.content.object}'"
                ),
            )
        else:
            return ConflictCandidate(
                existing_fact=candidate,
                result=DetectionResult.CORROBORATION,
                similarity=similarity,
                reason=(
                    f"Same canonical predicate '{new_fact.predicate_canonical}' "
                    f"with same object value"
                ),
            )

    # Without canonical predicates: rely on embedding similarity + object comparison
    # Different object values in the middle zone = potential contradiction
    if new_fact.content.object.strip().lower() != candidate.content.object.strip().lower():
        return ConflictCandidate(
            existing_fact=candidate,
            result=DetectionResult.CONTRADICTION,
            similarity=similarity,
            reason=(
                f"Similar facts (similarity={similarity:.3f}) with different "
                f"object values: '{new_fact.content.object}' vs '{candidate.content.object}'"
            ),
        )

    # Same object = corroboration
    return ConflictCandidate(
        existing_fact=candidate,
        result=DetectionResult.CORROBORATION,
        similarity=similarity,
        reason=f"Similar facts (similarity={similarity:.3f}) with same object value",
    )
