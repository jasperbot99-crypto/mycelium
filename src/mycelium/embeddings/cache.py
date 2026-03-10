"""LRU cache wrapper for embedding providers.

Wraps any EmbeddingProvider and caches results in an async-safe LRU cache.
Avoids redundant API calls for repeated text.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mycelium.embeddings.protocols import EmbeddingProvider


class CachedEmbeddingProvider:
    """LRU cache wrapper around any EmbeddingProvider.

    Thread-safety note: Python's GIL + single-threaded asyncio event loop
    means OrderedDict operations are safe without locks.

    Args:
        inner: The underlying embedding provider to cache.
        max_size: Maximum number of cached embeddings. Default: 4096.
    """

    def __init__(self, inner: EmbeddingProvider, max_size: int = 4096) -> None:
        self._inner = inner
        self._max_size = max_size
        self._cache: OrderedDict[str, list[float]] = OrderedDict()
        self._hits = 0
        self._misses = 0

    @property
    def dimension(self) -> int:
        return self._inner.dimension

    @property
    def hits(self) -> int:
        return self._hits

    @property
    def misses(self) -> int:
        return self._misses

    @property
    def size(self) -> int:
        return len(self._cache)

    async def embed(self, text: str) -> list[float]:
        """Embed with LRU cache lookup."""
        cached = self._cache.get(text)
        if cached is not None:
            self._hits += 1
            self._cache.move_to_end(text)
            return cached

        self._misses += 1
        result = await self._inner.embed(text)
        self._put(text, result)
        return result

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Batch embed with per-item cache lookup.

        Only sends uncached texts to the inner provider.
        """
        results: list[list[float] | None] = [None] * len(texts)
        uncached_indices: list[int] = []
        uncached_texts: list[str] = []

        for i, text in enumerate(texts):
            cached = self._cache.get(text)
            if cached is not None:
                self._hits += 1
                self._cache.move_to_end(text)
                results[i] = cached
            else:
                self._misses += 1
                uncached_indices.append(i)
                uncached_texts.append(text)

        if uncached_texts:
            new_embeddings = await self._inner.embed_batch(uncached_texts)
            for idx, embedding in zip(uncached_indices, new_embeddings, strict=False):
                results[idx] = embedding
                self._put(texts[idx], embedding)

        return [r for r in results if r is not None]

    def _put(self, key: str, value: list[float]) -> None:
        """Insert into cache, evicting LRU entry if at capacity."""
        if key in self._cache:
            self._cache.move_to_end(key)
        else:
            if len(self._cache) >= self._max_size:
                self._cache.popitem(last=False)
            self._cache[key] = value

    def clear(self) -> None:
        """Clear the cache and reset stats."""
        self._cache.clear()
        self._hits = 0
        self._misses = 0
