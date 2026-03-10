"""LLM conflict resolvers for Phase 4 (OpenAI + Anthropic)."""

from __future__ import annotations

import json
import os
from typing import Any, Protocol, cast
from uuid import UUID

import httpx

from mycelium.domain.types import Conflict, Fact
from mycelium.pipelines.conflict_resolution import (
    ConflictLLMResolver,
    LLMResolutionDecision,
)

_OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"
_ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"


class ConflictLLMProviderError(Exception):
    """Raised when a conflict-resolution provider fails."""


class _HttpClient(Protocol):
    async def post(self, url: str, *, json: dict[str, object]) -> httpx.Response: ...
    async def aclose(self) -> None: ...


class OpenAIConflictResolver(ConflictLLMResolver):
    """OpenAI-backed resolver for ambiguous conflict records."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str = "gpt-4.1-mini",
        timeout: float = 30.0,
    ) -> None:
        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ValueError("OpenAI API key required for conflict resolution")

        self._model = model
        self._client: _HttpClient = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
        )

    @property
    def http_client(self) -> _HttpClient:
        return self._client

    @http_client.setter
    def http_client(self, client: _HttpClient) -> None:
        self._client = client

    async def resolve(
        self,
        conflict: Conflict,
        fact_a: Fact,
        fact_b: Fact,
    ) -> LLMResolutionDecision:
        payload: dict[str, object] = {
            "model": self._model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": _system_prompt()},
                {
                    "role": "user",
                    "content": _user_prompt(
                        conflict=conflict,
                        fact_a=fact_a,
                        fact_b=fact_b,
                    ),
                },
            ],
        }
        response = await self._client.post(_OPENAI_CHAT_COMPLETIONS_URL, json=payload)
        if response.status_code != 200:
            raise ConflictLLMProviderError(
                f"OpenAI resolver error {response.status_code}: {response.text}"
            )

        data = cast(dict[str, Any], response.json())
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ConflictLLMProviderError("OpenAI response missing choices")

        first = cast(dict[str, Any], choices[0])
        message = cast(dict[str, Any], first.get("message", {}))
        content = message.get("content")
        if not isinstance(content, str):
            raise ConflictLLMProviderError("OpenAI response missing content")

        return _parse_decision_json(
            content=content,
            fact_a_id=fact_a.id,
            fact_b_id=fact_b.id,
        )

    async def close(self) -> None:
        await self._client.aclose()


class AnthropicConflictResolver(ConflictLLMResolver):
    """Anthropic-backed resolver for ambiguous conflict records."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str = "claude-sonnet-4-20250514",
        timeout: float = 30.0,
    ) -> None:
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise ValueError("Anthropic API key required for conflict resolution")

        self._model = model
        self._client: _HttpClient = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )

    @property
    def http_client(self) -> _HttpClient:
        return self._client

    @http_client.setter
    def http_client(self, client: _HttpClient) -> None:
        self._client = client

    async def resolve(
        self,
        conflict: Conflict,
        fact_a: Fact,
        fact_b: Fact,
    ) -> LLMResolutionDecision:
        payload: dict[str, object] = {
            "model": self._model,
            "max_tokens": 300,
            "temperature": 0,
            "system": _system_prompt(),
            "messages": [
                {
                    "role": "user",
                    "content": _user_prompt(
                        conflict=conflict,
                        fact_a=fact_a,
                        fact_b=fact_b,
                    ),
                }
            ],
        }
        response = await self._client.post(_ANTHROPIC_MESSAGES_URL, json=payload)
        if response.status_code != 200:
            raise ConflictLLMProviderError(
                f"Anthropic resolver error {response.status_code}: {response.text}"
            )

        data = cast(dict[str, Any], response.json())
        blocks = data.get("content")
        if not isinstance(blocks, list):
            raise ConflictLLMProviderError("Anthropic response missing content blocks")

        text_chunks: list[str] = []
        for block in cast(list[object], blocks):
            if not isinstance(block, dict):
                continue
            typed_block = cast(dict[str, object], block)
            if typed_block.get("type") != "text":
                continue
            text = typed_block.get("text")
            if isinstance(text, str):
                text_chunks.append(text)

        if not text_chunks:
            raise ConflictLLMProviderError("Anthropic response missing text")

        content = "\n".join(text_chunks)
        return _parse_decision_json(
            content=content,
            fact_a_id=fact_a.id,
            fact_b_id=fact_b.id,
        )

    async def close(self) -> None:
        await self._client.aclose()


def build_conflict_resolver(
    provider: str,
    *,
    api_key: str | None,
) -> ConflictLLMResolver:
    """Create concrete conflict resolver from provider name."""
    name = provider.strip().lower()
    if name == "openai":
        return OpenAIConflictResolver(api_key=api_key)
    if name == "anthropic":
        return AnthropicConflictResolver(api_key=api_key)
    raise ValueError(f"Unsupported llm_provider: {provider}")


def _system_prompt() -> str:
    return (
        "You resolve contradictions between two facts in a shared memory graph. "
        "Return ONLY valid JSON with keys: winning_fact_id, confidence, rationale, escalate. "
        "winning_fact_id must be one of the two candidate IDs, or null when escalating."
    )


def _user_prompt(*, conflict: Conflict, fact_a: Fact, fact_b: Fact) -> str:
    payload = {
        "conflict_id": str(conflict.id),
        "fact_a": _fact_payload(fact_a),
        "fact_b": _fact_payload(fact_b),
    }
    return (
        "Choose a winner only if evidence is materially stronger. "
        "If still ambiguous, set escalate=true and winning_fact_id=null.\n"
        f"Conflict input:\n{json.dumps(payload, ensure_ascii=True)}"
    )


def _fact_payload(fact: Fact) -> dict[str, object]:
    return {
        "id": str(fact.id),
        "subject": fact.content.subject,
        "predicate": fact.content.predicate,
        "object": fact.content.object,
        "context": fact.content.context,
        "source_type": fact.source_type.value,
        "confidence": fact.confidence,
        "trust_score": fact.trust_score,
        "valid_from": fact.valid_from.isoformat(),
        "metadata": {
            "version_vector": fact.metadata.get("version_vector"),
            "origin_agent_id": fact.metadata.get("origin_agent_id"),
        },
    }


def _parse_decision_json(
    *,
    content: str,
    fact_a_id: UUID,
    fact_b_id: UUID,
) -> LLMResolutionDecision:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip()

    try:
        parsed = cast(dict[str, object], json.loads(cleaned))
    except json.JSONDecodeError as exc:
        raise ConflictLLMProviderError(f"Invalid LLM decision JSON: {exc}") from exc

    raw_winner = parsed.get("winning_fact_id")
    winner: UUID | None = None
    if isinstance(raw_winner, str) and raw_winner.strip():
        try:
            candidate = UUID(raw_winner)
        except ValueError:
            candidate = None
        if candidate in {fact_a_id, fact_b_id}:
            winner = candidate

    raw_confidence = parsed.get("confidence")
    confidence = 0.0
    if isinstance(raw_confidence, (float, int)) and not isinstance(raw_confidence, bool):
        confidence = float(raw_confidence)
    confidence = max(0.0, min(1.0, confidence))

    raw_rationale = parsed.get("rationale")
    rationale = raw_rationale.strip() if isinstance(raw_rationale, str) else ""
    if not rationale:
        rationale = "No rationale provided"

    raw_escalate = parsed.get("escalate")
    escalate = bool(raw_escalate) if isinstance(raw_escalate, bool) else False

    if winner is None:
        escalate = True

    return LLMResolutionDecision(
        winning_fact_id=winner,
        confidence=confidence,
        rationale=rationale,
        escalate=escalate,
    )
