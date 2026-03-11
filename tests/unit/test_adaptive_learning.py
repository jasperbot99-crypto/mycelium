from __future__ import annotations

from mycelium.pipelines.adaptive_learning import _to_float, _to_int, _topic_key


def test_topic_key_extracts_non_stopword_token() -> None:
    topic = _topic_key("Why is EURUSD broker adapter failing today?")
    assert topic == "eurusd"


def test_topic_key_returns_empty_for_noise() -> None:
    topic = _topic_key("what when where the and this")
    assert topic == ""


def test_numeric_coercions() -> None:
    assert _to_int("7") == 7
    assert _to_int("x") == 0
    assert _to_float("0.75") == 0.75
    assert _to_float("nope") is None
