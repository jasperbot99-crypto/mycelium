"""Mock embedding provider for testing — deterministic, no API calls."""

from __future__ import annotations

import hashlib
import math


class MockEmbeddingProvider:
    """Deterministic embedding provider for testing.

    Generates consistent vectors from text content using a hash-based approach.
    Same text always produces the same vector. Vectors are normalized.
    """

    def __init__(self, dimension: int = 64) -> None:
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed(self, text: str) -> list[float]:
        return self._deterministic_vector(text)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self._deterministic_vector(t) for t in texts]

    def _deterministic_vector(self, text: str) -> list[float]:
        """Generate a normalized vector deterministically from text.

        Uses SHA-256 to generate enough bytes, then normalizes to unit length.
        Similar texts will NOT have similar vectors (this is a mock, not a model).
        """
        # Generate enough hash bytes for our dimension
        raw: list[float] = []
        seed = text.encode("utf-8")
        i = 0
        while len(raw) < self._dimension:
            h = hashlib.sha256(seed + i.to_bytes(4, "big")).digest()
            for byte in h:
                if len(raw) >= self._dimension:
                    break
                # Map byte to [-1, 1]
                raw.append((byte / 127.5) - 1.0)
            i += 1

        # Normalize to unit length
        norm = math.sqrt(sum(x * x for x in raw))
        if norm > 0:
            raw = [x / norm for x in raw]

        return raw
