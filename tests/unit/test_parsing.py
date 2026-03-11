"""Tests for content parsing — server-side extraction logic."""

from __future__ import annotations

from mycelium.domain.types import SourceType
from mycelium.pipelines.parsing import (
    extract_entities,
    extract_query_from_context,
    extract_subject,
    looks_like_garbage,
    parse_memory_store_to_fact,
)


class TestLooksLikeGarbage:
    def test_normal_text_not_garbage(self) -> None:
        assert not looks_like_garbage("EUR/USD EMA200 unreliable when spread > 5 pips")

    def test_json_object_is_garbage(self) -> None:
        assert looks_like_garbage('{"key": "value", "nested": {"a": 1}}')

    def test_json_array_is_garbage(self) -> None:
        assert looks_like_garbage('[{"id": 1}, {"id": 2}]')

    def test_escaped_quotes_is_garbage(self) -> None:
        assert looks_like_garbage('some text with \\"escaped\\" \\"quotes\\" everywhere')

    def test_high_json_density_is_garbage(self) -> None:
        assert looks_like_garbage("{a:1,b:2,c:3,d:4,e:5}")

    def test_password_pattern_is_garbage(self) -> None:
        assert looks_like_garbage("database password=SuperSecretPassword123!")

    def test_api_key_pattern_is_garbage(self) -> None:
        assert looks_like_garbage("api_key: sk_live_1234567890abcdef")

    def test_short_json_chars_not_garbage(self) -> None:
        # A sentence mentioning JSON with few actual JSON chars
        assert not looks_like_garbage("The API returns JSON with a status field")

    def test_secret_word_without_value_not_garbage(self) -> None:
        assert not looks_like_garbage("The token refresh logic needs fixing")


class TestExtractSubject:
    def test_ticker_pattern(self) -> None:
        assert extract_subject("EUR/USD EMA200 unreliable when spread > 5") == "EUR/USD EMA200"

    def test_ticker_alone(self) -> None:
        assert extract_subject("BTCUSDT is trading at all-time high") == "BTCUSDT"

    def test_backtick_code(self) -> None:
        assert extract_subject("Switched from `registerHook` to api.on()") == "registerHook"

    def test_quoted_string(self) -> None:
        assert extract_subject('The "shared-cognition" module handles memory') == "shared-cognition"

    def test_leading_proper_noun(self) -> None:
        result = extract_subject("Next.js standalone requires full rebuild")
        assert result.startswith("Next.js")

    def test_first_noun_before_verb(self) -> None:
        result = extract_subject("dispatch priority was sorting incorrectly")
        assert "dispatch priority" in result.lower()

    def test_fallback_word_boundary(self) -> None:
        text = "this is a longer piece of text without any special patterns to match against"
        result = extract_subject(text)
        assert len(result) <= 50
        # Should trim at word boundary
        assert not result.endswith(" ")

    def test_btc_ticker(self) -> None:
        assert extract_subject("BTC dropped 5% after the Fed announcement") == "BTC"

    def test_eth_ticker(self) -> None:
        assert extract_subject("ETH gas fees are too high") == "ETH"


class TestParseMemoryStoreToFact:
    def test_canonical_format(self) -> None:
        text = (
            "[jasper-code] [decision] Switched from registerHook"
            " to api.on() for typed plugin hooks"
        )
        result = parse_memory_store_to_fact(text, "jasper-code")
        assert result is not None
        assert result.content.predicate == "decision"
        assert result.source_type == SourceType.AGENT_EXTRACTION
        assert "jasper-code" in result.tags
        assert result.content.context is None  # same agent, no "via"

    def test_different_source_agent_adds_context(self) -> None:
        text = "[jasper-trader] [observation] EUR/USD spread widened"
        result = parse_memory_store_to_fact(text, "jasper-code")
        assert result is not None
        assert result.content.context == "via jasper-trader"

    def test_rejects_too_short(self) -> None:
        assert parse_memory_store_to_fact("short", "agent") is None

    def test_rejects_garbage_json(self) -> None:
        assert parse_memory_store_to_fact('{"key": "value", "nested": {"a": 1}}', "agent") is None

    def test_rejects_short_content_after_brackets(self) -> None:
        assert parse_memory_store_to_fact("[agent] [type] hi", "agent") is None

    def test_plain_sentence_as_inference(self) -> None:
        text = "The API endpoint was returning 500 errors intermittently"
        result = parse_memory_store_to_fact(text, "jasper-code")
        assert result is not None
        assert result.content.predicate == "has_memory"
        assert result.source_type == SourceType.AGENT_INFERENCE

    def test_rejects_text_with_json_brackets(self) -> None:
        assert parse_memory_store_to_fact("Config is {debug: true}", "agent") is None

    def test_rejects_text_with_array_brackets(self) -> None:
        assert parse_memory_store_to_fact("Items are [1, 2, 3]", "agent") is None

    def test_rejects_two_word_text(self) -> None:
        assert parse_memory_store_to_fact("hello world", "agent") is None

    def test_subject_extraction_in_canonical(self) -> None:
        text = "[jasper-trader] [observation] EUR/USD EMA200 unreliable when spread > 5 pips"
        result = parse_memory_store_to_fact(text, "jasper-trader")
        assert result is not None
        assert result.content.subject == "EUR/USD EMA200"

    def test_object_truncated_at_500(self) -> None:
        long_content = "x " * 300
        text = f"[agent] [note] {long_content}"
        result = parse_memory_store_to_fact(text, "agent")
        assert result is not None
        assert len(result.content.object) <= 500

    def test_predicate_normalized(self) -> None:
        text = "[agent] [Important Decision] We chose async over sync"
        result = parse_memory_store_to_fact(text, "agent")
        assert result is not None
        assert result.content.predicate == "important_decision"


class TestExtractEntities:
    def test_known_entity_detection(self) -> None:
        entities = extract_entities("We deployed the Mycelium server to Supabase")
        assert "Mycelium" in entities
        assert "Supabase" in entities

    def test_capitalized_phrases(self) -> None:
        entities = extract_entities("The Strategy-3 FX model outperforms")
        lower_entities = [e.lower() for e in entities]
        assert any("strategy" in e for e in lower_entities)

    def test_quoted_strings(self) -> None:
        entities = extract_entities('Check the "preconscious-buffer" module')
        assert "preconscious-buffer" in entities

    def test_identifiers(self) -> None:
        entities = extract_entities("Updated the memory_store and preconscious-buffer")
        assert any("memory_store" in e for e in entities)
        assert any("preconscious-buffer" in e for e in entities)

    def test_max_8_entities(self) -> None:
        text = "Mycelium OpenClaw LanceDB Supabase Alpaca Telegram GitHub launchd BTCUSDT pgvector"
        entities = extract_entities(text)
        assert len(entities) <= 8

    def test_deduplication(self) -> None:
        entities = extract_entities("Mycelium uses Mycelium patterns for Mycelium things")
        mycelium_count = sum(1 for e in entities if e.lower() == "mycelium")
        assert mycelium_count == 1

    def test_skips_common_words(self) -> None:
        entities = extract_entities("The What Where When How Why")
        # None of these common words should appear
        for entity in entities:
            assert entity not in {"The", "What", "Where", "When", "How", "Why"}

    def test_custom_known_entities(self) -> None:
        entities = extract_entities("FooBar is great", known_entities=["FooBar"])
        assert "FooBar" in entities

    def test_empty_text(self) -> None:
        assert extract_entities("") == []


class TestExtractQueryFromContext:
    def test_task_name_only(self) -> None:
        result = extract_query_from_context(task_name="deploy-api")
        assert result == "deploy-api"

    def test_prompt_entities(self) -> None:
        result = extract_query_from_context(prompt="Check the Mycelium server status")
        assert result is not None
        assert "Mycelium" in result

    def test_task_and_prompt_combined(self) -> None:
        result = extract_query_from_context(
            task_name="fix-trading",
            prompt="The BTCUSDT endpoint is failing",
        )
        assert result is not None
        assert "fix-trading" in result
        assert "BTCUSDT" in result

    def test_no_context_returns_none(self) -> None:
        assert extract_query_from_context() is None

    def test_empty_strings_return_none(self) -> None:
        assert extract_query_from_context(task_name="", prompt="") is None

    def test_prompt_with_no_entities(self) -> None:
        # Only common words, no entities
        result = extract_query_from_context(prompt="the and but for")
        assert result is None
