from __future__ import annotations

from collections.abc import Iterator, Mapping
from types import TracebackType
from typing import Any

class Record(Mapping[str, Any]):
    def __getitem__(self, key: str) -> Any: ...
    def __iter__(self) -> Iterator[str]: ...
    def __len__(self) -> int: ...
    def get(self, key: str, default: Any = None) -> Any: ...


class Connection:
    async def execute(self, query: str, *args: object) -> str: ...
    async def fetchrow(self, query: str, *args: object) -> Record | None: ...
    async def fetch(self, query: str, *args: object) -> list[Record]: ...
    def transaction(self) -> Transaction: ...
    async def add_listener(
        self,
        channel: str,
        callback: object,
    ) -> None: ...
    async def remove_listener(
        self,
        channel: str,
        callback: object,
    ) -> None: ...
    async def close(self) -> None: ...


class PoolAcquireContext:
    async def __aenter__(self) -> Connection: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool | None: ...


class Transaction:
    async def __aenter__(self) -> None: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool | None: ...


class Pool:
    def acquire(self, *, timeout: float | None = None) -> PoolAcquireContext: ...
    async def close(self) -> None: ...


async def create_pool(
    dsn: str | None = None,
    *,
    min_size: int = 10,
    max_size: int = 10,
    max_queries: int = 50000,
    max_inactive_connection_lifetime: float = 300,
    **connect_kwargs: object,
) -> Pool: ...


async def connect(
    dsn: str | None = None,
    **connect_kwargs: object,
) -> Connection: ...
