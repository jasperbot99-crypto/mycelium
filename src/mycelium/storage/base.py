"""Base memory store abstraction (Phase 1)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mycelium.storage.protocols import (
        AgentRepository,
        ConflictRepository,
        EventLog,
        FactRepository,
        RelationRepository,
        SubscriptionRepository,
    )


class BaseMemoryStore(ABC):
    """Common store contract for concrete backends."""

    fact_repo: FactRepository
    agent_repo: AgentRepository
    conflict_repo: ConflictRepository
    relation_repo: RelationRepository
    subscription_repo: SubscriptionRepository
    event_log: EventLog

    @abstractmethod
    async def connect(self) -> None:
        """Initialize backend resources."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Close backend resources."""
