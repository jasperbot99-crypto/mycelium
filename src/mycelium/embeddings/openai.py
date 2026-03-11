"""OpenAI embedding provider — text-embedding-3-small.

Uses httpx directly (no OpenAI SDK dependency). Implements EmbeddingProvider protocol.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from mycelium.auth.tokens import TokenProvider, build_token_provider

if TYPE_CHECKING:
    from mycelium.http.protocols import AsyncJsonHttpClient

_OPENAI_EMBEDDINGS_URL = "https://api.openai.com/v1/embeddings"
_DEFAULT_MODEL = "text-embedding-3-small"
_DEFAULT_DIMENSION = 1536
_MAX_BATCH_SIZE = 2048  # OpenAI limit


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
        token_provider: TokenProvider | None = None,
        model: str = _DEFAULT_MODEL,
        dimension: int = _DEFAULT_DIMENSION,
        timeout: float = 30.0,
    ) -> None:
        self._token_provider = build_token_provider(
            api_key,
            env_var="OPENAI_API_KEY",
            token_provider=token_provider,
            require_token=True,
        )
        self._model = model
        self._dimension = dimension
        self._timeout = timeout
        self._client: AsyncJsonHttpClient = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            headers={"Content-Type": "application/json"},
        )

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def http_client(self) -> AsyncJsonHttpClient:
        return self._client

    @http_client.setter
    def http_client(self, client: AsyncJsonHttpClient) -> None:
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

        token = await self._token_provider.get_token()
        response = await self._client.post(
            _OPENAI_EMBEDDINGS_URL,
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )

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
