"""Embedding provider interface — ARCHITECTURE.md Section 6.1."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Pluggable embed(text) -> vector interface.

    Spec section D3: designed as a pluggable function from day 1.
    Default implementation: OpenAI text-embedding-3-small.
    """

    async def embed(self, text: str) -> list[float]:
        """Embed a single text string into a vector."""
        ...

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple text strings into vectors."""
        ...

    @property
    def dimension(self) -> int:
        """The dimensionality of the embedding vectors."""
        ...
