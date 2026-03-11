"""PostgreSQL storage implementations — ARCHITECTURE.md Section 2, Layer 1.

All SQL is contained here. Repository methods return domain objects.
Uses asyncpg for async database access.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from mycelium.domain.types import (
    ActiveContext,
    AgentRecord,
    Conflict,
    ConflictStatus,
    Fact,
    FactContent,
    FactRelation,
    Priority,
    PropagationEvent,
    RelationType,
    SourceType,
    Subscription,
    Urgency,
    VerificationStatus,
)

if TYPE_CHECKING:
    from uuid import UUID

    import asyncpg


def _row_to_fact(row: asyncpg.Record) -> Fact:
    """Convert a database row to a Fact domain object."""
    return Fact(
        id=row["id"],
        content=FactContent(
            subject=row["subject"],
            predicate=row["predicate"],
            object=row["object"],
            context=row["context"],
        ),
        source_agent_id=row["source_agent_id"],
        source_type=SourceType(row["source_type"]),
        confidence=row["confidence"],
        trust_score=row["trust_score"],
        valid_from=row["valid_from"],
        valid_until=row["valid_until"],
        created_at=row["created_at"],
        expired_at=row["expired_at"],
        derived_from=row["derived_from"] or [],
        supersedes=row["supersedes"],
        predicate_canonical=row["predicate_canonical"],
        corroboration_count=row["corroboration_count"],
        last_accessed_at=row["last_accessed_at"],
        access_count=row["access_count"],
        last_verified_at=row["last_verified_at"],
        verification_status=VerificationStatus(row["verification_status"])
        if row["verification_status"]
        else VerificationStatus.UNVERIFIED,
        tags=row["tags"] or [],
        conflict_status=row["conflict_status"] or "none",
        embedding=_pgvector_to_list(row["embedding"]) if row["embedding"] else None,
        metadata=json.loads(row["metadata"]) if row["metadata"] else {},
        migrated_from=row["migrated_from"],
        migration_date=row["migration_date"],
    )


def _pgvector_to_list(vec: Any) -> list[float]:
    """Convert pgvector string representation to list of floats."""
    if isinstance(vec, list):
        values: list[float] = []
        for raw in cast("list[float | int | str]", vec):
            values.append(float(raw))
        return values
    if isinstance(vec, tuple):
        values: list[float] = []
        for raw in cast("tuple[float | int | str, ...]", vec):
            values.append(float(raw))
        return values
    if isinstance(vec, str):
        return [float(x) for x in vec.strip("[]").split(",")]
    return [float(x) for x in vec]


def _list_to_pgvector(vec: list[float]) -> str:
    """Convert list of floats to pgvector string representation."""
    return "[" + ",".join(str(v) for v in vec) + "]"


def _row_to_agent(row: asyncpg.Record) -> AgentRecord:
    """Convert a database row to an AgentRecord domain object."""
    return AgentRecord(
        id=row["id"],
        role=row["role"],
        trust_score=row["trust_score"],
        facts_contributed=row["facts_contributed"],
        facts_contradicted=row["facts_contradicted"],
        contradiction_rate=row["contradiction_rate"],
        active_context=ActiveContext(
            task=row["active_task"],
            entities=tuple(row["active_entities"] or []),
            urgency=Urgency(row["active_urgency"]) if row["active_urgency"] else Urgency.NORMAL,
        ),
        context_updated_at=row["context_updated_at"],
        last_seen_at=row["last_seen_at"],
        last_ack_event_id=row["last_ack_event_id"],
        created_at=row["created_at"],
        metadata=json.loads(row["metadata"]) if row["metadata"] else {},
    )


class PostgresFactRepository:
    """PostgreSQL FactRepository implementation."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def insert(self, fact: Fact) -> Fact:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO mycelium.facts (
                    id, subject, predicate, predicate_canonical, object, context,
                    source_agent_id, source_type, confidence, trust_score,
                    valid_from, valid_until, created_at, expired_at,
                    derived_from, supersedes,
                    corroboration_count, last_accessed_at, access_count,
                    last_verified_at, verification_status,
                    tags, conflict_status, embedding, metadata,
                    migrated_from, migration_date
                ) VALUES (
                    $1, $2, $3, $4, $5, $6,
                    $7, $8, $9, $10,
                    $11, $12, $13, $14,
                    $15, $16,
                    $17, $18, $19,
                    $20, $21,
                    $22, $23, $24, $25,
                    $26, $27
                )
                """,
                fact.id,
                fact.content.subject,
                fact.content.predicate,
                fact.predicate_canonical,
                fact.content.object,
                fact.content.context,
                fact.source_agent_id,
                fact.source_type.value,
                fact.confidence,
                fact.trust_score,
                fact.valid_from,
                fact.valid_until,
                fact.created_at,
                fact.expired_at,
                fact.derived_from or None,
                fact.supersedes,
                fact.corroboration_count,
                fact.last_accessed_at,
                fact.access_count,
                fact.last_verified_at,
                fact.verification_status.value,
                fact.tags or [],
                fact.conflict_status,
                _list_to_pgvector(fact.embedding) if fact.embedding else None,
                json.dumps(fact.metadata) if fact.metadata else "{}",
                fact.migrated_from,
                fact.migration_date,
            )
        return fact

    async def get_by_id(self, fact_id: UUID) -> Fact | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM mycelium.facts WHERE id = $1", fact_id
            )
        if row is None:
            return None
        return _row_to_fact(row)

    async def find_by_subject(
        self,
        subject: str,
        active_only: bool = True,
    ) -> list[Fact]:
        query = "SELECT * FROM mycelium.facts WHERE LOWER(subject) = LOWER($1)"
        if active_only:
            query += " AND expired_at IS NULL AND valid_until IS NULL"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, subject)
        return [_row_to_fact(row) for row in rows]

    async def find_by_embedding(
        self,
        embedding: list[float],
        limit: int = 20,
        min_similarity: float = 0.0,
        active_only: bool = True,
    ) -> list[tuple[Fact, float]]:
        vec_str = _list_to_pgvector(embedding)
        query = """
            SELECT *, 1 - (embedding <=> $1::vector) AS similarity
            FROM mycelium.facts
            WHERE embedding IS NOT NULL
        """
        if active_only:
            query += " AND expired_at IS NULL AND valid_until IS NULL"
        if min_similarity > 0:
            query += f" AND 1 - (embedding <=> $1::vector) >= {min_similarity}"
        query += " ORDER BY embedding <=> $1::vector LIMIT $2"

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, vec_str, limit)
        return [(_row_to_fact(row), row["similarity"]) for row in rows]

    async def find_by_tags(
        self,
        tags: list[str],
        active_only: bool = True,
        limit: int = 20,
    ) -> list[Fact]:
        query = "SELECT * FROM mycelium.facts WHERE tags && $1"
        if active_only:
            query += " AND expired_at IS NULL AND valid_until IS NULL"
        query += " LIMIT $2"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, tags, limit)
        return [_row_to_fact(row) for row in rows]

    async def expire(self, fact_id: UUID, expired_at: datetime | None = None) -> None:
        ts = expired_at or datetime.now(UTC)
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE mycelium.facts SET expired_at = $2 WHERE id = $1",
                fact_id, ts,
            )

    async def set_valid_until(self, fact_id: UUID, valid_until: datetime) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE mycelium.facts SET valid_until = $2 WHERE id = $1",
                fact_id, valid_until,
            )

    async def update_conflict_status(self, fact_id: UUID, status: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE mycelium.facts SET conflict_status = $2 WHERE id = $1",
                fact_id, status,
            )

    async def update_scores(
        self,
        fact_id: UUID,
        confidence: float | None = None,
        trust_score: float | None = None,
        corroboration_count: int | None = None,
    ) -> None:
        sets: list[str] = []
        args: list[Any] = [fact_id]
        idx = 2

        if confidence is not None:
            sets.append(f"confidence = ${idx}")
            args.append(confidence)
            idx += 1
        if trust_score is not None:
            sets.append(f"trust_score = ${idx}")
            args.append(trust_score)
            idx += 1
        if corroboration_count is not None:
            sets.append(f"corroboration_count = ${idx}")
            args.append(corroboration_count)
            idx += 1

        if not sets:
            return

        query = f"UPDATE mycelium.facts SET {', '.join(sets)} WHERE id = $1"
        async with self._pool.acquire() as conn:
            await conn.execute(query, *args)

    async def record_access(self, fact_id: UUID) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE mycelium.facts
                SET access_count = access_count + 1, last_accessed_at = now()
                WHERE id = $1
                """,
                fact_id,
            )

    async def update_verification(
        self,
        fact_id: UUID,
        status: VerificationStatus,
        verified_at: datetime | None = None,
    ) -> None:
        ts = verified_at or datetime.now(UTC)
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE mycelium.facts
                SET verification_status = $2, last_verified_at = $3
                WHERE id = $1
                """,
                fact_id,
                status.value,
                ts,
            )

    async def find_created_since(
        self,
        since: datetime,
        active_only: bool = True,
    ) -> list[Fact]:
        query = "SELECT * FROM mycelium.facts WHERE created_at >= $1"
        if active_only:
            query += " AND expired_at IS NULL AND valid_until IS NULL"
        query += " ORDER BY created_at"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, since)
        return [_row_to_fact(row) for row in rows]

    async def find_all_active(self) -> list[Fact]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM mycelium.facts WHERE expired_at IS NULL AND valid_until IS NULL"
            )
        return [_row_to_fact(row) for row in rows]

    async def list_for_agent(
        self,
        agent_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        active_only: bool = True,
    ) -> list[Fact]:
        query = """
            SELECT *
            FROM mycelium.facts
            WHERE source_agent_id = $1
        """
        if active_only:
            query += " AND expired_at IS NULL AND valid_until IS NULL"
        query += " ORDER BY created_at DESC LIMIT $2 OFFSET $3"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, agent_id, limit, offset)
        return [_row_to_fact(row) for row in rows]


class PostgresAgentRepository:
    """PostgreSQL AgentRepository implementation."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def upsert(self, agent: AgentRecord) -> AgentRecord:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO mycelium.agents (
                    id, role, trust_score, facts_contributed, facts_contradicted,
                    contradiction_rate, active_task, active_entities, active_urgency,
                    context_updated_at, last_seen_at, last_ack_event_id, metadata
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                ON CONFLICT (id) DO UPDATE SET
                    role = EXCLUDED.role,
                    trust_score = EXCLUDED.trust_score,
                    facts_contributed = EXCLUDED.facts_contributed,
                    facts_contradicted = EXCLUDED.facts_contradicted,
                    contradiction_rate = EXCLUDED.contradiction_rate,
                    active_task = EXCLUDED.active_task,
                    active_entities = EXCLUDED.active_entities,
                    active_urgency = EXCLUDED.active_urgency,
                    context_updated_at = EXCLUDED.context_updated_at,
                    last_seen_at = COALESCE(EXCLUDED.last_seen_at, now()),
                    last_ack_event_id = EXCLUDED.last_ack_event_id,
                    metadata = EXCLUDED.metadata
                """,
                agent.id,
                agent.role,
                agent.trust_score,
                agent.facts_contributed,
                agent.facts_contradicted,
                agent.contradiction_rate,
                agent.active_context.task,
                list(agent.active_context.entities) or None,
                agent.active_context.urgency.value,
                agent.context_updated_at,
                agent.last_seen_at,
                agent.last_ack_event_id,
                json.dumps(agent.metadata) if agent.metadata else "{}",
            )
        return agent

    async def get_by_id(self, agent_id: str) -> AgentRecord | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM mycelium.agents WHERE id = $1", agent_id
            )
        if row is None:
            return None
        return _row_to_agent(row)

    async def update_last_seen(self, agent_id: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE mycelium.agents SET last_seen_at = now() WHERE id = $1",
                agent_id,
            )

    async def update_trust_stats(
        self,
        agent_id: str,
        facts_contributed_delta: int = 0,
        facts_contradicted_delta: int = 0,
        trust_score_delta: float = 0.0,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE mycelium.agents SET
                    facts_contributed = facts_contributed + $2,
                    facts_contradicted = facts_contradicted + $3,
                    trust_score = LEAST(1.0, GREATEST(0.0, trust_score + $4)),
                    contradiction_rate = CASE
                        WHEN (facts_contributed + $2) > 0
                        THEN ROUND(CAST((facts_contradicted + $3) AS NUMERIC)
                             / (facts_contributed + $2), 4)
                        ELSE 0.0
                    END
                WHERE id = $1
                """,
                agent_id,
                facts_contributed_delta,
                facts_contradicted_delta,
                trust_score_delta,
            )

    async def list_all(self) -> list[AgentRecord]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM mycelium.agents ORDER BY id")
        return [_row_to_agent(row) for row in rows]


class PostgresSubscriptionRepository:
    """PostgreSQL SubscriptionRepository implementation."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def sync_subscriptions(
        self,
        agent_id: str,
        subscriptions: list[Subscription],
    ) -> list[Subscription]:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "DELETE FROM mycelium.subscriptions WHERE agent_id = $1",
                agent_id,
            )
            for sub in subscriptions:
                await conn.execute(
                    """
                        INSERT INTO mycelium.subscriptions
                            (id, agent_id, topic, priority, min_confidence, source_types)
                        VALUES ($1, $2, $3, $4, $5, $6)
                        """,
                    sub.id,
                    sub.agent_id,
                    sub.topic,
                    sub.priority.value,
                    sub.min_confidence,
                    [st.value for st in sub.source_types] if sub.source_types else None,
                )
        return subscriptions

    async def get_for_agent(self, agent_id: str) -> list[Subscription]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM mycelium.subscriptions WHERE agent_id = $1",
                agent_id,
            )
        return [_row_to_subscription(row) for row in rows]

    async def get_all(self) -> list[Subscription]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM mycelium.subscriptions")
        return [_row_to_subscription(row) for row in rows]


class PostgresConflictRepository:
    """PostgreSQL ConflictRepository implementation."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def insert(self, conflict: Conflict) -> Conflict:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO mycelium.conflicts
                    (id, fact_a_id, fact_b_id, status, resolution,
                     winning_fact_id, detected_at, resolved_at, resolved_by, metadata)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                conflict.id,
                conflict.fact_a_id,
                conflict.fact_b_id,
                conflict.status.value,
                json.dumps(conflict.resolution) if conflict.resolution else None,
                conflict.winning_fact_id,
                conflict.detected_at,
                conflict.resolved_at,
                conflict.resolved_by,
                json.dumps(conflict.metadata) if conflict.metadata else "{}",
            )
        return conflict

    async def get_by_id(self, conflict_id: UUID) -> Conflict | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM mycelium.conflicts WHERE id = $1", conflict_id
            )
        if row is None:
            return None
        return _row_to_conflict(row)

    async def find_for_fact(self, fact_id: UUID) -> list[Conflict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM mycelium.conflicts WHERE fact_a_id = $1 OR fact_b_id = $1",
                fact_id,
            )
        return [_row_to_conflict(row) for row in rows]

    async def find_by_status(self, status: ConflictStatus) -> list[Conflict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM mycelium.conflicts WHERE status = $1",
                status.value,
            )
        return [_row_to_conflict(row) for row in rows]

    async def update_resolution(
        self,
        conflict_id: UUID,
        status: ConflictStatus,
        winning_fact_id: UUID | None,
        resolved_by: str,
        resolution: dict[str, object] | None = None,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE mycelium.conflicts SET
                    status = $2, winning_fact_id = $3, resolved_by = $4,
                    resolved_at = now(), resolution = $5
                WHERE id = $1
                """,
                conflict_id,
                status.value,
                winning_fact_id,
                resolved_by,
                json.dumps(resolution) if resolution else None,
            )


class PostgresRelationRepository:
    """PostgreSQL RelationRepository implementation."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def insert(self, relation: FactRelation) -> FactRelation:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO mycelium.fact_relations
                    (id, source_fact_id, target_fact_id, relation_type, created_by, metadata)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                relation.id,
                relation.source_fact_id,
                relation.target_fact_id,
                relation.relation_type.value,
                relation.created_by,
                json.dumps(relation.metadata) if relation.metadata else "{}",
            )
        return relation

    async def find_for_fact(
        self,
        fact_id: UUID,
        relation_type: RelationType | None = None,
    ) -> list[FactRelation]:
        query = """
            SELECT * FROM mycelium.fact_relations
            WHERE source_fact_id = $1 OR target_fact_id = $1
        """
        args: list[Any] = [fact_id]
        if relation_type is not None:
            query += " AND relation_type = $2"
            args.append(relation_type.value)

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *args)
        return [_row_to_relation(row) for row in rows]


class PostgresEventLog:
    """PostgreSQL EventLog implementation."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def append(self, event: PropagationEvent) -> PropagationEvent:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO mycelium.propagation_events
                    (id, fact_id, target_agent_id, reason, priority, delivered, delivered_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                RETURNING sequence_num
                """,
                event.id,
                event.fact_id,
                event.target_agent_id,
                event.reason,
                event.priority.value,
                event.delivered,
                event.delivered_at,
            )
            if row is None:
                raise RuntimeError("failed to append propagation event")
            event.sequence_num = row["sequence_num"]
        return event

    async def get_since(
        self,
        agent_id: str,
        since_sequence: int = 0,
        limit: int = 1000,
    ) -> list[PropagationEvent]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM mycelium.propagation_events
                WHERE target_agent_id = $1 AND sequence_num > $2
                ORDER BY sequence_num LIMIT $3
                """,
                agent_id, since_sequence, limit,
            )
        return [_row_to_event(row) for row in rows]

    async def mark_delivered(self, event_id: UUID) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE mycelium.propagation_events
                SET delivered = TRUE, delivered_at = now()
                WHERE id = $1
                """,
                event_id,
            )

    async def get_undelivered(self, agent_id: str) -> list[PropagationEvent]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM mycelium.propagation_events
                WHERE target_agent_id = $1 AND delivered = FALSE
                ORDER BY sequence_num
                """,
                agent_id,
            )
        return [_row_to_event(row) for row in rows]


# --- Row converters ---


def _row_to_subscription(row: asyncpg.Record) -> Subscription:
    return Subscription(
        id=row["id"],
        agent_id=row["agent_id"],
        topic=row["topic"],
        priority=Priority(row["priority"]),
        min_confidence=row["min_confidence"] or 0.0,
        source_types=(
            [SourceType(st) for st in row["source_types"]]
            if row["source_types"]
            else None
        ),
        created_at=row["created_at"],
    )


def _row_to_conflict(row: asyncpg.Record) -> Conflict:
    return Conflict(
        id=row["id"],
        fact_a_id=row["fact_a_id"],
        fact_b_id=row["fact_b_id"],
        status=ConflictStatus(row["status"]),
        resolution=json.loads(row["resolution"]) if row["resolution"] else None,
        winning_fact_id=row["winning_fact_id"],
        detected_at=row["detected_at"],
        resolved_at=row["resolved_at"],
        resolved_by=row["resolved_by"],
        metadata=json.loads(row["metadata"]) if row["metadata"] else {},
    )


def _row_to_relation(row: asyncpg.Record) -> FactRelation:
    return FactRelation(
        id=row["id"],
        source_fact_id=row["source_fact_id"],
        target_fact_id=row["target_fact_id"],
        relation_type=RelationType(row["relation_type"]),
        created_by=row["created_by"],
        metadata=json.loads(row["metadata"]) if row["metadata"] else {},
    )


def _row_to_event(row: asyncpg.Record) -> PropagationEvent:
    return PropagationEvent(
        id=row["id"],
        fact_id=row["fact_id"],
        target_agent_id=row["target_agent_id"],
        reason=row["reason"],
        priority=Priority(row["priority"]),
        delivered=row["delivered"],
        delivered_at=row["delivered_at"],
        created_at=row["created_at"],
        sequence_num=row["sequence_num"],
    )
