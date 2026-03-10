"""Storage layer abstract interfaces — ARCHITECTURE.md Section 2, Layer 1.

All database interaction is contained behind these protocols.
No SQL leaks above this boundary. Repository methods return domain objects.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from mycelium.domain.types import (
    AgentRecord,
    Conflict,
    ConflictStatus,
    Fact,
    FactRelation,
    PropagationEvent,
    RelationType,
    Subscription,
    VerificationStatus,
)


@runtime_checkable
class FactRepository(Protocol):
    """CRUD for facts, temporal queries, semantic search."""

    async def insert(self, fact: Fact) -> Fact:
        """Store a new fact. Returns the stored fact with any server-generated fields."""
        ...

    async def get_by_id(self, fact_id: UUID) -> Fact | None:
        """Retrieve a single fact by ID."""
        ...

    async def find_by_subject(
        self,
        subject: str,
        active_only: bool = True,
    ) -> list[Fact]:
        """Find all facts about a specific subject."""
        ...

    async def find_by_embedding(
        self,
        embedding: list[float],
        limit: int = 20,
        min_similarity: float = 0.0,
        active_only: bool = True,
    ) -> list[tuple[Fact, float]]:
        """Semantic search via pgvector. Returns (fact, similarity) pairs."""
        ...

    async def find_by_tags(
        self,
        tags: list[str],
        active_only: bool = True,
        limit: int = 20,
    ) -> list[Fact]:
        """Find facts matching any of the given tags."""
        ...

    async def expire(self, fact_id: UUID, expired_at: datetime | None = None) -> None:
        """Mark a fact as expired (set expired_at). Does not delete."""
        ...

    async def set_valid_until(self, fact_id: UUID, valid_until: datetime) -> None:
        """Set the temporal end of a fact's validity."""
        ...

    async def update_conflict_status(self, fact_id: UUID, status: str) -> None:
        """Update a fact's conflict status."""
        ...

    async def update_scores(
        self,
        fact_id: UUID,
        confidence: float | None = None,
        trust_score: float | None = None,
        corroboration_count: int | None = None,
    ) -> None:
        """Update mutable scoring fields (D-ARCH-6 exception)."""
        ...

    async def record_access(self, fact_id: UUID) -> None:
        """Increment access_count and set last_accessed_at."""
        ...

    async def update_verification(
        self,
        fact_id: UUID,
        status: VerificationStatus,
        verified_at: datetime | None = None,
    ) -> None:
        """Update verification metadata for a fact."""
        ...

    async def find_created_since(
        self,
        since: datetime,
        active_only: bool = True,
    ) -> list[Fact]:
        """Find facts created since a given timestamp. Used by ContradictionSweeper."""
        ...

    async def find_all_active(self) -> list[Fact]:
        """Return all active (non-expired, non-invalidated) facts. Used by DecayCycleRunner."""
        ...


@runtime_checkable
class AgentRepository(Protocol):
    """Agent registration and management."""

    async def upsert(self, agent: AgentRecord) -> AgentRecord:
        """Create or update an agent record. Upsert on connect()."""
        ...

    async def get_by_id(self, agent_id: str) -> AgentRecord | None:
        """Retrieve an agent by ID."""
        ...

    async def update_last_seen(self, agent_id: str) -> None:
        """Touch last_seen_at timestamp."""
        ...

    async def update_trust_stats(
        self,
        agent_id: str,
        facts_contributed_delta: int = 0,
        facts_contradicted_delta: int = 0,
        trust_score_delta: float = 0.0,
    ) -> None:
        """Increment agent trust statistics."""
        ...

    async def list_all(self) -> list[AgentRecord]:
        """List all registered agents."""
        ...


@runtime_checkable
class SubscriptionRepository(Protocol):
    """Subscription management."""

    async def sync_subscriptions(
        self,
        agent_id: str,
        subscriptions: list[Subscription],
    ) -> list[Subscription]:
        """Replace all subscriptions for an agent. Source of truth is config."""
        ...

    async def get_for_agent(self, agent_id: str) -> list[Subscription]:
        """Get all subscriptions for a specific agent."""
        ...

    async def get_all(self) -> list[Subscription]:
        """Get all subscriptions across all agents."""
        ...


@runtime_checkable
class ConflictRepository(Protocol):
    """Conflict record management."""

    async def insert(self, conflict: Conflict) -> Conflict:
        """Store a new conflict record."""
        ...

    async def get_by_id(self, conflict_id: UUID) -> Conflict | None:
        """Retrieve a conflict by ID."""
        ...

    async def find_for_fact(self, fact_id: UUID) -> list[Conflict]:
        """Find all conflicts involving a specific fact."""
        ...

    async def find_by_status(self, status: ConflictStatus) -> list[Conflict]:
        """Find all conflicts with a given status."""
        ...

    async def update_resolution(
        self,
        conflict_id: UUID,
        status: ConflictStatus,
        winning_fact_id: UUID | None,
        resolved_by: str,
        resolution: dict[str, object] | None = None,
    ) -> None:
        """Record the resolution of a conflict."""
        ...


@runtime_checkable
class RelationRepository(Protocol):
    """Fact relation management."""

    async def insert(self, relation: FactRelation) -> FactRelation:
        """Store a new relation between facts."""
        ...

    async def find_for_fact(
        self,
        fact_id: UUID,
        relation_type: RelationType | None = None,
    ) -> list[FactRelation]:
        """Find all relations involving a specific fact (as source or target)."""
        ...


@runtime_checkable
class EventLog(Protocol):
    """Append-only propagation event log."""

    async def append(self, event: PropagationEvent) -> PropagationEvent:
        """Append a new event. Returns event with server-generated sequence_num."""
        ...

    async def get_since(
        self,
        agent_id: str,
        since_sequence: int = 0,
        limit: int = 1000,
    ) -> list[PropagationEvent]:
        """Get events for an agent since a given sequence number."""
        ...

    async def mark_delivered(self, event_id: UUID) -> None:
        """Mark an event as delivered."""
        ...

    async def get_undelivered(self, agent_id: str) -> list[PropagationEvent]:
        """Get all undelivered events for an agent."""
        ...
