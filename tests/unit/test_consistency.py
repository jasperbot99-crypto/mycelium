"""Unit tests for distributed consistency helpers."""

from mycelium.domain.consistency import (
    CausalOrder,
    compare_vectors,
    merge_vectors,
    next_vector,
    normalize_vector,
)


def test_normalize_vector_filters_invalid_values() -> None:
    raw = {
        "agent-a": 2,
        "agent-b": "3",
        "agent-c": -1,
        "agent-d": True,
        "": 5,
        42: 9,
    }

    normalized = normalize_vector(raw)
    assert normalized == {
        "agent-a": 2,
        "agent-b": 3,
        "agent-c": 0,
    }


def test_merge_vectors_uses_max_per_agent() -> None:
    merged = merge_vectors([
        {"agent-a": 1, "agent-b": 3},
        {"agent-a": 4, "agent-c": 2},
        {"agent-b": 1},
    ])

    assert merged == {"agent-a": 4, "agent-b": 3, "agent-c": 2}


def test_next_vector_increments_writer_counter() -> None:
    vector = next_vector({"agent-a": 2, "agent-b": 1}, "agent-a")
    assert vector == {"agent-a": 3, "agent-b": 1}


def test_compare_vectors_orders_before_after_equal_and_concurrent() -> None:
    assert compare_vectors({"a": 1}, {"a": 1}) == CausalOrder.EQUAL
    assert compare_vectors({"a": 1}, {"a": 2}) == CausalOrder.BEFORE
    assert compare_vectors({"a": 2}, {"a": 1}) == CausalOrder.AFTER
    assert compare_vectors({"a": 2, "b": 1}, {"a": 1, "b": 2}) == CausalOrder.CONCURRENT
