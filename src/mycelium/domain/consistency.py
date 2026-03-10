"""Distributed consistency helpers for Phase 4.

Implements a lightweight version-vector model used for causal ordering across
multi-process writers. Vectors are stored in fact metadata.
"""

from __future__ import annotations

from enum import StrEnum
from typing import cast


class CausalOrder(StrEnum):
    """Relative ordering between two version vectors."""

    BEFORE = "before"
    AFTER = "after"
    CONCURRENT = "concurrent"
    EQUAL = "equal"


VersionVector = dict[str, int]


def normalize_vector(raw: object) -> VersionVector:
    """Normalize arbitrary metadata value into a safe version vector."""
    if not isinstance(raw, dict):
        return {}

    raw_dict = cast("dict[object, object]", raw)
    normalized: VersionVector = {}
    for key, value in raw_dict.items():
        if not isinstance(key, str):
            continue
        if not key.strip():
            continue
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            normalized[key] = max(0, value)
            continue
        if isinstance(value, str):
            try:
                parsed = int(value)
            except ValueError:
                continue
            normalized[key] = max(0, parsed)
    return normalized


def merge_vectors(vectors: list[VersionVector]) -> VersionVector:
    """Merge vectors by taking max counter per agent."""
    merged: VersionVector = {}
    for vec in vectors:
        for agent_id, counter in vec.items():
            existing = merged.get(agent_id, 0)
            if counter > existing:
                merged[agent_id] = counter
    return merged


def next_vector(base: VersionVector, agent_id: str) -> VersionVector:
    """Return next logical vector for a write by agent_id."""
    if not agent_id.strip():
        raise ValueError("agent_id must not be empty")

    out = dict(base)
    out[agent_id] = out.get(agent_id, 0) + 1
    return out


def compare_vectors(left: VersionVector, right: VersionVector) -> CausalOrder:
    """Compare two vectors and return their causal relationship."""
    keys = set(left.keys()) | set(right.keys())
    left_lt_right = False
    right_lt_left = False

    for key in keys:
        l_value = left.get(key, 0)
        r_value = right.get(key, 0)
        if l_value < r_value:
            left_lt_right = True
        elif l_value > r_value:
            right_lt_left = True

    if not left_lt_right and not right_lt_left:
        return CausalOrder.EQUAL
    if left_lt_right and not right_lt_left:
        return CausalOrder.BEFORE
    if right_lt_left and not left_lt_right:
        return CausalOrder.AFTER
    return CausalOrder.CONCURRENT
