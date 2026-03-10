"""Operational logger — ARCHITECTURE.md Section 3.7, SPEC Section 5.5.

Structured logging to ops.operation_log. Separate from memory facts.
Mycelium does NOT use itself for operational observability.

All operations are append-only. The detail field is JSONB for extensibility.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID, uuid4


@dataclass
class OpsLogEntry:
    """A single operation log entry matching ops.operation_log schema."""

    id: UUID
    operation: str
    status: str
    agent_id: str | None = None
    fact_id: UUID | None = None
    latency_ms: int | None = None
    detail: dict[str, Any] | None = None
    created_at: datetime = field(default_factory=datetime.now)


@runtime_checkable
class OpsLogger(Protocol):
    """Protocol for operational logging — append-only."""

    async def log(
        self,
        operation: str,
        status: str,
        *,
        agent_id: str | None = None,
        fact_id: UUID | None = None,
        latency_ms: int | None = None,
        detail: dict[str, Any] | None = None,
    ) -> OpsLogEntry:
        """Append an operation log entry."""
        ...

    async def find_by_operation(
        self,
        operation: str,
        limit: int = 100,
    ) -> list[OpsLogEntry]:
        """Find log entries by operation type."""
        ...

    async def find_by_agent(
        self,
        agent_id: str,
        limit: int = 100,
    ) -> list[OpsLogEntry]:
        """Find log entries for a specific agent."""
        ...


class NullOpsLogger:
    """No-op logger for when logging is not needed (e.g., lightweight tests)."""

    async def log(
        self,
        operation: str,
        status: str,
        *,
        agent_id: str | None = None,
        fact_id: UUID | None = None,
        latency_ms: int | None = None,
        detail: dict[str, Any] | None = None,
    ) -> OpsLogEntry:
        return OpsLogEntry(
            id=uuid4(),
            operation=operation,
            status=status,
            agent_id=agent_id,
            fact_id=fact_id,
            latency_ms=latency_ms,
            detail=detail,
        )

    async def find_by_operation(
        self,
        operation: str,
        limit: int = 100,
    ) -> list[OpsLogEntry]:
        return []

    async def find_by_agent(
        self,
        agent_id: str,
        limit: int = 100,
    ) -> list[OpsLogEntry]:
        return []


class InMemoryOpsLogger:
    """In-memory OpsLogger for testing — stores entries in a list."""

    def __init__(self) -> None:
        self._entries: list[OpsLogEntry] = []

    @property
    def entries(self) -> list[OpsLogEntry]:
        return list(self._entries)

    async def log(
        self,
        operation: str,
        status: str,
        *,
        agent_id: str | None = None,
        fact_id: UUID | None = None,
        latency_ms: int | None = None,
        detail: dict[str, Any] | None = None,
    ) -> OpsLogEntry:
        entry = OpsLogEntry(
            id=uuid4(),
            operation=operation,
            status=status,
            agent_id=agent_id,
            fact_id=fact_id,
            latency_ms=latency_ms,
            detail=detail,
        )
        self._entries.append(entry)
        return entry

    async def find_by_operation(
        self,
        operation: str,
        limit: int = 100,
    ) -> list[OpsLogEntry]:
        results = [e for e in self._entries if e.operation == operation]
        return results[:limit]

    async def find_by_agent(
        self,
        agent_id: str,
        limit: int = 100,
    ) -> list[OpsLogEntry]:
        results = [e for e in self._entries if e.agent_id == agent_id]
        return results[:limit]

    def clear(self) -> None:
        self._entries.clear()


def ms_since(start: float) -> int:
    """Convert time.monotonic() delta to milliseconds."""
    return int((time.monotonic() - start) * 1000)
