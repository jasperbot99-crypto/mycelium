"""Unit tests for Phase 4 LLM conflict resolvers."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from mycelium.domain.types import Conflict, ConflictStatus, Fact, FactContent, SourceType
from mycelium.llm.conflict import (
    AnthropicConflictResolver,
    ConflictLLMProviderError,
    OpenAIConflictResolver,
    build_conflict_resolver,
)


class _DummyHttpClient:
    def __init__(self, response: httpx.Response) -> None:
        self._response = response
        self.calls: list[tuple[str, dict[str, object], dict[str, str] | None]] = []

    async def post(
        self,
        url: str,
        *,
        json: dict[str, object],
        headers: dict[str, str] | None = None,
        **kwargs: object,
    ) -> httpx.Response:
        del kwargs
        self.calls.append((url, json, headers))
        return self._response

    async def aclose(self) -> None:
        return None


def _json_response(status_code: int, body: dict[str, object]) -> httpx.Response:
    request = httpx.Request("POST", "https://example.test")
    return httpx.Response(
        status_code=status_code,
        content=json.dumps(body).encode("utf-8"),
        request=request,
    )


def _text_response(status_code: int, body: str) -> httpx.Response:
    request = httpx.Request("POST", "https://example.test")
    return httpx.Response(
        status_code=status_code,
        content=body.encode("utf-8"),
        request=request,
    )


def _make_fact(*, source: SourceType, obj: str) -> Fact:
    return Fact(
        id=uuid4(),
        content=FactContent(subject="api-orders", predicate="has_status", object=obj),
        source_agent_id="agent-a",
        source_type=source,
        confidence=0.6,
        trust_score=0.5,
        valid_from=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_openai_resolver_parses_winner() -> None:
    fact_a = _make_fact(source=SourceType.AGENT_EXTRACTION, obj="healthy")
    fact_b = _make_fact(source=SourceType.HUMAN_CORRECTION, obj="degraded")
    conflict = Conflict(
        id=uuid4(),
        fact_a_id=fact_a.id,
        fact_b_id=fact_b.id,
        status=ConflictStatus.DETECTED,
    )

    resolver = OpenAIConflictResolver(api_key="test-key")
    resolver.http_client = _DummyHttpClient(
        _json_response(
            200,
            {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"winning_fact_id":"'
                                f"{fact_b.id}"
                                '","confidence":0.91,"rationale":"newer and stronger",'
                                '"escalate":false}'
                            )
                        }
                    }
                ]
            },
        )
    )

    decision = await resolver.resolve(conflict, fact_a, fact_b)
    assert decision.winning_fact_id == fact_b.id
    assert decision.confidence == 0.91
    assert decision.escalate is False


@pytest.mark.asyncio
async def test_anthropic_resolver_escalates_on_null_winner() -> None:
    fact_a = _make_fact(source=SourceType.AGENT_EXTRACTION, obj="healthy")
    fact_b = _make_fact(source=SourceType.AGENT_EXTRACTION, obj="degraded")
    conflict = Conflict(
        id=uuid4(),
        fact_a_id=fact_a.id,
        fact_b_id=fact_b.id,
        status=ConflictStatus.DETECTED,
    )

    resolver = AnthropicConflictResolver(api_key="test-key")
    resolver.http_client = _DummyHttpClient(
        _json_response(
            200,
            {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            '{"winning_fact_id":null,"confidence":0.4,'
                            '"rationale":"uncertain","escalate":true}'
                        ),
                    }
                ]
            },
        )
    )

    decision = await resolver.resolve(conflict, fact_a, fact_b)
    assert decision.winning_fact_id is None
    assert decision.escalate is True


@pytest.mark.asyncio
async def test_provider_errors_raise_conflict_provider_error() -> None:
    fact_a = _make_fact(source=SourceType.AGENT_EXTRACTION, obj="healthy")
    fact_b = _make_fact(source=SourceType.AGENT_EXTRACTION, obj="degraded")
    conflict = Conflict(
        id=uuid4(),
        fact_a_id=fact_a.id,
        fact_b_id=fact_b.id,
        status=ConflictStatus.DETECTED,
    )

    resolver = OpenAIConflictResolver(api_key="test-key")
    resolver.http_client = _DummyHttpClient(_text_response(500, "boom"))

    with pytest.raises(ConflictLLMProviderError):
        await resolver.resolve(conflict, fact_a, fact_b)


@pytest.mark.asyncio
async def test_openai_resolver_uses_token_provider_header() -> None:
    class _TokenProvider:
        async def get_token(self) -> str:
            return "oauth-token"

    fact_a = _make_fact(source=SourceType.AGENT_EXTRACTION, obj="healthy")
    fact_b = _make_fact(source=SourceType.HUMAN_CORRECTION, obj="degraded")
    conflict = Conflict(
        id=uuid4(),
        fact_a_id=fact_a.id,
        fact_b_id=fact_b.id,
        status=ConflictStatus.DETECTED,
    )

    resolver = OpenAIConflictResolver(token_provider=_TokenProvider())
    dummy = _DummyHttpClient(
        _json_response(
            200,
            {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"winning_fact_id":"'
                                f"{fact_b.id}"
                                '","confidence":0.9,"rationale":"ok","escalate":false}'
                            )
                        }
                    }
                ]
            },
        )
    )
    resolver.http_client = dummy

    await resolver.resolve(conflict, fact_a, fact_b)
    assert dummy.calls[0][2] == {"Authorization": "Bearer oauth-token"}


def test_build_conflict_resolver_supports_openai_and_anthropic() -> None:
    openai = build_conflict_resolver("openai", api_key="x", token_provider=None)
    anthropic = build_conflict_resolver("anthropic", api_key="x", token_provider=None)

    assert isinstance(openai, OpenAIConflictResolver)
    assert isinstance(anthropic, AnthropicConflictResolver)


def test_build_conflict_resolver_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError, match="Unsupported llm_provider"):
        _ = build_conflict_resolver("unknown", api_key="x", token_provider=None)
