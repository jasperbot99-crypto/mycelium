"""OpenAI embedding provider — text-embedding-3-small.

Uses httpx directly (no OpenAI SDK dependency). Implements EmbeddingProvider protocol.
"""

from __future__ import annotations

import os
from typing import Protocol

import httpx


_OPENAI_EMBEDDINGS_URL = "https://api.openai.com/v1/embeddings"
_DEFAULT_MODEL = "text-embedding-3-small"
_DEFAULT_DIMENSION = 1536
_MAX_BATCH_SIZE = 2048  # OpenAI limit


class _HttpClient(Protocol):
    async def post(self, url: str, *, json: dict[str, object]) -> httpx.Response: ...
    async def aclose(self) -> None: ...


class OpenAIEmbeddingProvider:
    """Production embedding provider using OpenAI's text-embedding-3-small.

    Args:
        api_key: OpenAI API key. Falls back to OPENAI_API_KEY env var.
        model: Model name. Default: text-embedding-3-small.
        dimension: Output vector dimension. Default: 1536.
        timeout: HTTP request timeout in seconds. Default: 30.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = _DEFAULT_MODEL,
        dimension: int = _DEFAULT_DIMENSION,
        timeout: float = 30.0,
    ) -> None:
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self._api_key:
            raise ValueError(
                "OpenAI API key required. Pass api_key= or set OPENAI_API_KEY env var."
            )
        self._model = model
        self._dimension = dimension
        self._timeout = timeout
        self._client: _HttpClient = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def http_client(self) -> _HttpClient:
        return self._client

    @http_client.setter
    def http_client(self, client: _HttpClient) -> None:
        self._client = client

    async def embed(self, text: str) -> list[float]:
        """Embed a single text string."""
        results = await self.embed_batch([text])
        return results[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts. Automatically chunks into batches if needed."""
        if not texts:
            return []

        all_embeddings: list[list[float]] = []

        for i in range(0, len(texts), _MAX_BATCH_SIZE):
            batch = texts[i : i + _MAX_BATCH_SIZE]
            embeddings = await self._request_embeddings(batch)
            all_embeddings.extend(embeddings)

        return all_embeddings

    async def _request_embeddings(self, texts: list[str]) -> list[list[float]]:
        """Make a single API request for a batch of texts."""
        payload: dict[str, object] = {
            "input": texts,
            "model": self._model,
            "dimensions": self._dimension,
        }

        response = await self._client.post(_OPENAI_EMBEDDINGS_URL, json=payload)

        if response.status_code != 200:
            raise EmbeddingAPIError(
                f"OpenAI API error {response.status_code}: {response.text}"
            )

        data = response.json()
        # API returns embeddings sorted by index, but we sort explicitly to be safe
        items = sorted(data["data"], key=lambda x: x["index"])
        return [item["embedding"] for item in items]

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()


class EmbeddingAPIError(Exception):
    """Raised when the OpenAI embedding API returns an error."""
