"""In-memory storage implementations — for testing and pipeline development.

Dict-based implementations of all storage protocols. No persistence,
no SQL. Used for unit tests and as a stepping stone before Postgres.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
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


class InMemoryFactRepository:
    """In-memory FactRepository implementation."""

    def __init__(self) -> None:
        self._facts: dict[UUID, Fact] = {}

    async def insert(self, fact: Fact) -> Fact:
        self._facts[fact.id] = fact
        return fact

    async def get_by_id(self, fact_id: UUID) -> Fact | None:
        return self._facts.get(fact_id)

    async def find_by_subject(
        self,
        subject: str,
        active_only: bool = True,
    ) -> list[Fact]:
        results: list[Fact] = []
        for fact in self._facts.values():
            if fact.content.subject.lower() == subject.lower():
                if active_only and not fact.is_active:
                    continue
                results.append(fact)
        return results

    async def find_by_embedding(
        self,
        embedding: list[float],
        limit: int = 20,
        min_similarity: float = 0.0,
        active_only: bool = True,
    ) -> list[tuple[Fact, float]]:
        from mycelium.domain.conflict import cosine_similarity

        results: list[tuple[Fact, float]] = []
        for fact in self._facts.values():
            if active_only and not fact.is_active:
                continue
            if fact.embedding is None:
                continue
            sim = cosine_similarity(embedding, fact.embedding)
            if sim >= min_similarity:
                results.append((fact, sim))
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:limit]

    async def find_by_tags(
        self,
        tags: list[str],
        active_only: bool = True,
        limit: int = 20,
    ) -> list[Fact]:
        tag_set = set(tags)
        results: list[Fact] = []
        for fact in self._facts.values():
            if active_only and not fact.is_active:
                continue
            if tag_set & set(fact.tags):
                results.append(fact)
        return results[:limit]

    async def expire(self, fact_id: UUID, expired_at: datetime | None = None) -> None:
        fact = self._facts.get(fact_id)
        if fact is not None:
            fact.expired_at = expired_at or datetime.now()

    async def set_valid_until(self, fact_id: UUID, valid_until: datetime) -> None:
        fact = self._facts.get(fact_id)
        if fact is not None:
            fact.valid_until = valid_until

    async def update_conflict_status(self, fact_id: UUID, status: str) -> None:
        fact = self._facts.get(fact_id)
        if fact is not None:
            fact.conflict_status = status

    async def update_scores(
        self,
        fact_id: UUID,
        confidence: float | None = None,
        trust_score: float | None = None,
        corroboration_count: int | None = None,
    ) -> None:
        fact = self._facts.get(fact_id)
        if fact is not None:
            if confidence is not None:
                fact.confidence = confidence
            if trust_score is not None:
                fact.trust_score = trust_score
            if corroboration_count is not None:
                fact.corroboration_count = corroboration_count

    async def record_access(self, fact_id: UUID) -> None:
        fact = self._facts.get(fact_id)
        if fact is not None:
            fact.access_count += 1
            fact.last_accessed_at = datetime.now()

    async def update_verification(
        self,
        fact_id: UUID,
        status: VerificationStatus,
        verified_at: datetime | None = None,
    ) -> None:
        fact = self._facts.get(fact_id)
        if fact is not None:
            fact.verification_status = status
            fact.last_verified_at = verified_at or datetime.now()

    async def find_created_since(
        self,
        since: datetime,
        active_only: bool = True,
    ) -> list[Fact]:
        results: list[Fact] = []
        for fact in self._facts.values():
            if fact.created_at >= since:
                if active_only and not fact.is_active:
                    continue
                results.append(fact)
        return results

    async def find_all_active(self) -> list[Fact]:
        return [f for f in self._facts.values() if f.is_active]


class InMemoryAgentRepository:
    """In-memory AgentRepository implementation."""

    def __init__(self) -> None:
        self._agents: dict[str, AgentRecord] = {}

    async def upsert(self, agent: AgentRecord) -> AgentRecord:
        self._agents[agent.id] = agent
        return agent

    async def get_by_id(self, agent_id: str) -> AgentRecord | None:
        return self._agents.get(agent_id)

    async def update_last_seen(self, agent_id: str) -> None:
        agent = self._agents.get(agent_id)
        if agent is not None:
            agent.last_seen_at = datetime.now()

    async def update_trust_stats(
        self,
        agent_id: str,
        facts_contributed_delta: int = 0,
        facts_contradicted_delta: int = 0,
        trust_score_delta: float = 0.0,
    ) -> None:
        agent = self._agents.get(agent_id)
        if agent is not None:
            agent.facts_contributed += facts_contributed_delta
            agent.facts_contradicted += facts_contradicted_delta
            if trust_score_delta != 0.0:
                agent.trust_score = round(
                    min(1.0, max(0.0, agent.trust_score + trust_score_delta)),
                    4,
                )
            if agent.facts_contributed > 0:
                agent.contradiction_rate = round(
                    agent.facts_contradicted / agent.facts_contributed, 4
                )

    async def list_all(self) -> list[AgentRecord]:
        return list(self._agents.values())


class InMemorySubscriptionRepository:
    """In-memory SubscriptionRepository implementation."""

    def __init__(self) -> None:
        self._subs: dict[str, list[Subscription]] = {}

    async def sync_subscriptions(
        self,
        agent_id: str,
        subscriptions: list[Subscription],
    ) -> list[Subscription]:
        self._subs[agent_id] = subscriptions
        return subscriptions

    async def get_for_agent(self, agent_id: str) -> list[Subscription]:
        return self._subs.get(agent_id, [])

    async def get_all(self) -> list[Subscription]:
        result: list[Subscription] = []
        for subs in self._subs.values():
            result.extend(subs)
        return result


class InMemoryConflictRepository:
    """In-memory ConflictRepository implementation."""

    def __init__(self) -> None:
        self._conflicts: dict[UUID, Conflict] = {}

    async def insert(self, conflict: Conflict) -> Conflict:
        self._conflicts[conflict.id] = conflict
        return conflict

    async def get_by_id(self, conflict_id: UUID) -> Conflict | None:
        return self._conflicts.get(conflict_id)

    async def find_for_fact(self, fact_id: UUID) -> list[Conflict]:
        return [
            c
            for c in self._conflicts.values()
            if c.fact_a_id == fact_id or c.fact_b_id == fact_id
        ]

    async def find_by_status(self, status: ConflictStatus) -> list[Conflict]:
        return [c for c in self._conflicts.values() if c.status == status]

    async def update_resolution(
        self,
        conflict_id: UUID,
        status: ConflictStatus,
        winning_fact_id: UUID | None,
        resolved_by: str,
        resolution: dict[str, object] | None = None,
    ) -> None:
        conflict = self._conflicts.get(conflict_id)
        if conflict is not None:
            conflict.status = status
            conflict.winning_fact_id = winning_fact_id
            conflict.resolved_by = resolved_by
            conflict.resolved_at = datetime.now()
            if resolution is not None:
                conflict.resolution = resolution


class InMemoryRelationRepository:
    """In-memory RelationRepository implementation."""

    def __init__(self) -> None:
        self._relations: dict[UUID, FactRelation] = {}

    async def insert(self, relation: FactRelation) -> FactRelation:
        self._relations[relation.id] = relation
        return relation

    async def find_for_fact(
        self,
        fact_id: UUID,
        relation_type: RelationType | None = None,
    ) -> list[FactRelation]:
        results: list[FactRelation] = []
        for rel in self._relations.values():
            if rel.source_fact_id == fact_id or rel.target_fact_id == fact_id:
                if relation_type is not None and rel.relation_type != relation_type:
                    continue
                results.append(rel)
        return results


class InMemoryEventLog:
    """In-memory EventLog implementation."""

    def __init__(self) -> None:
        self._events: list[PropagationEvent] = []
        self._sequence: int = 0

    async def append(self, event: PropagationEvent) -> PropagationEvent:
        self._sequence += 1
        event.sequence_num = self._sequence
        self._events.append(event)
        return event

    async def get_since(
        self,
        agent_id: str,
        since_sequence: int = 0,
        limit: int = 1000,
    ) -> list[PropagationEvent]:
        results = [
            e
            for e in self._events
            if e.target_agent_id == agent_id and e.sequence_num > since_sequence
        ]
        return results[:limit]

    async def mark_delivered(self, event_id: UUID) -> None:
        for event in self._events:
            if event.id == event_id:
                event.delivered = True
                event.delivered_at = datetime.now()
                break

    async def get_undelivered(self, agent_id: str) -> list[PropagationEvent]:
        return [
            e
            for e in self._events
            if e.target_agent_id == agent_id and not e.delivered
        ]
