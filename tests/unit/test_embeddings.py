"""Tests for embedding providers: OpenAI provider and LRU cache."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from mycelium.embeddings.cache import CachedEmbeddingProvider
from mycelium.embeddings.mock import MockEmbeddingProvider
from mycelium.embeddings.openai import EmbeddingAPIError, OpenAIEmbeddingProvider

# --- OpenAIEmbeddingProvider ---


class TestOpenAIEmbeddingProvider:
    @pytest.mark.asyncio
    async def test_uses_token_provider_per_request(self) -> None:
        class _TokenProvider:
            def __init__(self) -> None:
                self.calls = 0

            async def get_token(self) -> str:
                self.calls += 1
                return "dynamic-token"

        provider = OpenAIEmbeddingProvider(token_provider=_TokenProvider())
        mock_response = httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [0.1] * 1536}]},
        )
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        provider.http_client = mock_client

        await provider.embed("hello")
        _, kwargs = mock_client.post.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer dynamic-token"

    def test_requires_api_key(self) -> None:
        with (
            patch.dict("os.environ", {}, clear=True),
            pytest.raises(ValueError, match="API key required"),
        ):
            OpenAIEmbeddingProvider(api_key=None)

    def test_accepts_explicit_api_key(self) -> None:
        provider = OpenAIEmbeddingProvider(api_key="sk-test-key")
        assert provider.dimension == 1536

    def test_accepts_env_api_key(self) -> None:
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-env-key"}):
            provider = OpenAIEmbeddingProvider()
            assert provider.dimension == 1536

    def test_custom_dimension(self) -> None:
        provider = OpenAIEmbeddingProvider(api_key="sk-test", dimension=768)
        assert provider.dimension == 768

    @pytest.mark.asyncio
    async def test_embed_single(self) -> None:
        provider = OpenAIEmbeddingProvider(api_key="sk-test")

        mock_response = httpx.Response(
            200,
            json={
                "data": [{"index": 0, "embedding": [0.1] * 1536}],
                "usage": {"prompt_tokens": 5, "total_tokens": 5},
            },
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        provider.http_client = mock_client

        result = await provider.embed("hello world")
        assert len(result) == 1536
        assert abs(result[0] - 0.1) < 1e-9

    @pytest.mark.asyncio
    async def test_embed_batch(self) -> None:
        provider = OpenAIEmbeddingProvider(api_key="sk-test")

        mock_response = httpx.Response(
            200,
            json={
                "data": [
                    {"index": 0, "embedding": [0.1] * 1536},
                    {"index": 1, "embedding": [0.2] * 1536},
                ],
                "usage": {"prompt_tokens": 10, "total_tokens": 10},
            },
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        provider.http_client = mock_client

        results = await provider.embed_batch(["hello", "world"])
        assert len(results) == 2
        assert abs(results[0][0] - 0.1) < 1e-9
        assert abs(results[1][0] - 0.2) < 1e-9

    @pytest.mark.asyncio
    async def test_embed_batch_respects_index_order(self) -> None:
        provider = OpenAIEmbeddingProvider(api_key="sk-test")

        # API returns out of order
        mock_response = httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.2] * 1536},
                    {"index": 0, "embedding": [0.1] * 1536},
                ],
                "usage": {"prompt_tokens": 10, "total_tokens": 10},
            },
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        provider.http_client = mock_client

        results = await provider.embed_batch(["first", "second"])
        assert abs(results[0][0] - 0.1) < 1e-9  # index 0 first
        assert abs(results[1][0] - 0.2) < 1e-9  # index 1 second

    @pytest.mark.asyncio
    async def test_embed_empty_batch(self) -> None:
        provider = OpenAIEmbeddingProvider(api_key="sk-test")
        results = await provider.embed_batch([])
        assert results == []

    @pytest.mark.asyncio
    async def test_api_error_raises(self) -> None:
        provider = OpenAIEmbeddingProvider(api_key="sk-test")

        mock_response = httpx.Response(
            429,
            json={"error": {"message": "Rate limit exceeded"}},
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        provider.http_client = mock_client

        with pytest.raises(EmbeddingAPIError, match="429"):
            await provider.embed("hello")

    @pytest.mark.asyncio
    async def test_close(self) -> None:
        provider = OpenAIEmbeddingProvider(api_key="sk-test")
        mock_client = AsyncMock()
        provider.http_client = mock_client
        await provider.close()
        mock_client.aclose.assert_called_once()


# --- CachedEmbeddingProvider ---


class TestCachedEmbeddingProvider:
    @pytest.fixture
    def mock_provider(self) -> MockEmbeddingProvider:
        return MockEmbeddingProvider(dimension=64)

    @pytest.fixture
    def cached(self, mock_provider: MockEmbeddingProvider) -> CachedEmbeddingProvider:
        return CachedEmbeddingProvider(mock_provider, max_size=10)

    @pytest.mark.asyncio
    async def test_dimension_delegates(self, cached: CachedEmbeddingProvider) -> None:
        assert cached.dimension == 64

    @pytest.mark.asyncio
    async def test_cache_hit(self, cached: CachedEmbeddingProvider) -> None:
        result1 = await cached.embed("hello")
        result2 = await cached.embed("hello")

        assert result1 == result2
        assert cached.hits == 1
        assert cached.misses == 1
        assert cached.size == 1

    @pytest.mark.asyncio
    async def test_cache_miss(self, cached: CachedEmbeddingProvider) -> None:
        await cached.embed("hello")
        await cached.embed("world")

        assert cached.misses == 2
        assert cached.size == 2

    @pytest.mark.asyncio
    async def test_lru_eviction(
        self, mock_provider: MockEmbeddingProvider
    ) -> None:
        cached = CachedEmbeddingProvider(mock_provider, max_size=3)

        await cached.embed("a")
        await cached.embed("b")
        await cached.embed("c")
        assert cached.size == 3

        # This should evict "a"
        await cached.embed("d")
        assert cached.size == 3

        # "a" should be a miss now
        await cached.embed("a")
        assert cached.misses == 5  # a, b, c, d, a again

    @pytest.mark.asyncio
    async def test_lru_access_refreshes(
        self, mock_provider: MockEmbeddingProvider
    ) -> None:
        cached = CachedEmbeddingProvider(mock_provider, max_size=3)

        await cached.embed("a")
        await cached.embed("b")
        await cached.embed("c")

        # Access "a" to refresh it
        await cached.embed("a")

        # "b" should now be the LRU entry
        await cached.embed("d")

        # "b" should be evicted, "a" should still be cached
        await cached.embed("a")
        assert cached.hits == 2  # a refresh + a after d

        await cached.embed("b")
        # Misses: a(1), b(2), c(3), d(4), b-again(5). Hits: a-refresh(1), a-after-d(2)
        assert cached.misses == 5

    @pytest.mark.asyncio
    async def test_batch_with_partial_cache(
        self, cached: CachedEmbeddingProvider
    ) -> None:
        # Pre-cache "hello"
        await cached.embed("hello")
        assert cached.misses == 1

        # Batch with one cached and one uncached
        results = await cached.embed_batch(["hello", "world"])
        assert len(results) == 2
        assert cached.hits == 1  # "hello" from cache
        assert cached.misses == 2  # "world" is new

    @pytest.mark.asyncio
    async def test_batch_all_cached(
        self, cached: CachedEmbeddingProvider
    ) -> None:
        await cached.embed("a")
        await cached.embed("b")

        results = await cached.embed_batch(["a", "b"])
        assert len(results) == 2
        assert cached.hits == 2
        assert cached.misses == 2  # only the initial embeds

    @pytest.mark.asyncio
    async def test_clear(self, cached: CachedEmbeddingProvider) -> None:
        await cached.embed("hello")
        assert cached.size == 1

        cached.clear()
        assert cached.size == 0
        assert cached.hits == 0
        assert cached.misses == 0
