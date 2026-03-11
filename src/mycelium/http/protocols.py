"""Shared HTTP protocols used across providers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import httpx


class AsyncJsonHttpClient(Protocol):
    """Minimal async HTTP interface for JSON POST workflows."""

    async def post(
        self,
        url: str,
        *,
        json: dict[str, object],
        headers: dict[str, str] | None = None,
    ) -> httpx.Response: ...

    async def aclose(self) -> None: ...
