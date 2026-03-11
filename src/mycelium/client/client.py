"""MyceliumClient — the public API for agents. ARCHITECTURE.md Section 2, Layer 4.

Thin facade: validates input, delegates to pipelines, returns results.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from mycelium.domain.trust import compute_trust_score
from mycelium.domain.types import (
    ActiveContext,
    AgentRecord,
    Conflict,
    ConflictResolutionResult,
    CorroborationResult,
    FactContent,
    FactRelation,
    Priority,
    PropagationEvent,
    RelationType,
    SourceType,
    Subscription,
    VerificationMethod,
    VerificationResult,
    VerificationStatus,
)
from mycelium.llm import build_conflict_resolver
from mycelium.ops.logger import NullOpsLogger, OpsLogger
from mycelium.pipelines.conflict_resolution import (
    ConflictLLMResolver,
    ConflictResolutionPipeline,
)
from mycelium.pipelines.ingest import IngestPipeline, IngestResult
from mycelium.pipelines.propagation import PropagationEngine
from mycelium.pipelines.provenance import ProvenanceEntry, ProvenancePipeline
from mycelium.pipelines.query import QueryEngine, QueryFilters, QueryResult
from mycelium.pipelines.verification import VerificationPipeline

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from mycelium.config import MyceliumConfig
    from mycelium.embeddings.protocols import EmbeddingProvider
    from mycelium.storage.protocols import (
        AgentRepository,
        ConflictRepository,
        EventLog,
        FactRepository,
        RelationRepository,
        SubscriptionRepository,
    )
    from mycelium.transport.protocols import Transport


class MyceliumClient:
    """The single entry point for agents.

    Lifecycle: construct → connect() → use → disconnect()
    """

    def __init__(
        self,
        agent_id: str,
        config: MyceliumConfig,
        *,
        role: str = "generic",
        # Dependency injection — if None, will be created from config
        fact_repo: FactRepository | None = None,
        agent_repo: AgentRepository | None = None,
        conflict_repo: ConflictRepository | None = None,
        relation_repo: RelationRepository | None = None,
        subscription_repo: SubscriptionRepository | None = None,
        event_log: EventLog | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        transport: Transport | None = None,
        ops_logger: OpsLogger | None = None,
        conflict_llm_resolver: ConflictLLMResolver | None = None,
    ) -> None:
        self._agent_id = agent_id
        self._role = role
        self._config = config
        self._connected = False
        self._on_fact_callback: Callable[[PropagationEvent], Awaitable[None]] | None = (
            None
        )

        # Store injected dependencies
        self._fact_repo = fact_repo
        self._agent_repo = agent_repo
        self._conflict_repo = conflict_repo
        self._relation_repo = relation_repo
        self._subscription_repo = subscription_repo
        self._event_log = event_log
        self._embedding = embedding_provider or config.embedding_provider
        self._transport = transport or config.transport
        self._ops = ops_logger or NullOpsLogger()
        self._conflict_llm_resolver = conflict_llm_resolver

        # Pipelines — created on connect()
        self._ingest_pipeline: IngestPipeline | None = None
        self._query_engine: QueryEngine | None = None
        self._verification_pipeline: VerificationPipeline | None = None
        self._conflict_resolution_pipeline: ConflictResolutionPipeline | None = None
        self._provenance_pipeline: ProvenancePipeline | None = None

    @property
    def agent_id(self) -> str:
        return self._agent_id

    @property
    def connected(self) -> bool:
        return self._connected

    def _require_connected(self) -> None:
        if not self._connected:
            raise RuntimeError(
                f"Client '{self._agent_id}' is not connected. Call connect() first."
            )

    def _require_repos(self) -> None:
        """Ensure all required repos are available."""
        if self._fact_repo is None:
            raise RuntimeError("fact_repo not provided")
        if self._agent_repo is None:
            raise RuntimeError("agent_repo not provided")
        if self._conflict_repo is None:
            raise RuntimeError("conflict_repo not provided")
        if self._relation_repo is None:
            raise RuntimeError("relation_repo not provided")
        if self._embedding is None:
            raise RuntimeError("embedding_provider not provided")

    async def connect(self) -> None:
        """Connect to Mycelium: upsert agent, sync subscriptions, replay missed events.

        ARCHITECTURE.md Section 4.5 — Agent Registration Lifecycle.
        """
        self._require_repos()
        assert self._agent_repo is not None
        assert self._fact_repo is not None
        assert self._conflict_repo is not None
        assert self._relation_repo is not None
        assert self._embedding is not None

        if self._conflict_llm_resolver is None and self._config.llm_provider is not None:
            self._conflict_llm_resolver = build_conflict_resolver(
                self._config.llm_provider,
                api_key=self._config.llm_api_key,
                token_provider=self._config.llm_token_provider,
            )

        # 1. Upsert agent record
        agent = AgentRecord(
            id=self._agent_id,
            role=self._role,
            last_seen_at=datetime.now(),
        )
        await self._agent_repo.upsert(agent)

        # 2. Sync subscriptions from config
        if self._subscription_repo is not None and self._config.subscriptions:
            subs = [
                Subscription(
                    id=uuid4(),
                    agent_id=self._agent_id,
                    topic=sc.topic,
                    priority=Priority(sc.priority),
                    min_confidence=sc.min_confidence,
                    source_types=(
                        [SourceType(st) for st in sc.source_types]
                        if sc.source_types
                        else None
                    ),
                )
                for sc in self._config.subscriptions
            ]
            await self._subscription_repo.sync_subscriptions(self._agent_id, subs)

        # 3. Create propagation engine (if dependencies available)
        propagation_engine: PropagationEngine | None = None
        if (
            self._subscription_repo is not None
            and self._event_log is not None
            and self._transport is not None
        ):
            propagation_engine = PropagationEngine(
                subscription_repo=self._subscription_repo,
                event_log=self._event_log,
                transport=self._transport,
                agent_repo=self._agent_repo,
                ops_logger=self._ops,
            )

        # 4. Create pipelines
        self._ingest_pipeline = IngestPipeline(
            fact_repo=self._fact_repo,
            agent_repo=self._agent_repo,
            conflict_repo=self._conflict_repo,
            relation_repo=self._relation_repo,
            embedding_provider=self._embedding,
            config=self._config,
            ops_logger=self._ops,
            propagation_engine=propagation_engine,
        )

        self._query_engine = QueryEngine(
            fact_repo=self._fact_repo,
            embedding_provider=self._embedding,
            config=self._config,
            ops_logger=self._ops,
        )
        self._verification_pipeline = VerificationPipeline(
            fact_repo=self._fact_repo,
            agent_repo=self._agent_repo,
            ops_logger=self._ops,
        )
        self._conflict_resolution_pipeline = ConflictResolutionPipeline(
            fact_repo=self._fact_repo,
            conflict_repo=self._conflict_repo,
            relation_repo=self._relation_repo,
            config=self._config,
            llm_resolver=self._conflict_llm_resolver,
            ops_logger=self._ops,
        )
        self._provenance_pipeline = ProvenancePipeline(
            fact_repo=self._fact_repo,
            relation_repo=self._relation_repo,
        )

        self._connected = True

        # 5. Subscribe to transport for incoming events
        if self._transport is not None:
            await self._transport.subscribe(self._agent_id, self._handle_event)

        # 6. Replay undelivered events
        if self._on_fact_callback is not None:
            undelivered = await self.replay()
            for event in undelivered:
                await self._handle_event(event)

        await self._ops.log("connect", "success", agent_id=self._agent_id)

    async def _handle_event(self, event: PropagationEvent) -> None:
        """Internal handler for incoming propagation events."""
        if self._on_fact_callback is not None:
            await self._on_fact_callback(event)
            if self._event_log is not None and not event.delivered:
                await self._event_log.mark_delivered(event.id)

    async def disconnect(self) -> None:
        """Disconnect from Mycelium."""
        if self._transport is not None:
            await self._transport.unsubscribe(self._agent_id)
        if self._agent_repo is not None:
            await self._agent_repo.update_last_seen(self._agent_id)
        await self._ops.log("disconnect", "success", agent_id=self._agent_id)
        self._connected = False
        self._ingest_pipeline = None
        self._query_engine = None
        self._verification_pipeline = None
        self._conflict_resolution_pipeline = None
        self._provenance_pipeline = None

    # --- Core operations ---

    async def ingest(
        self,
        content: FactContent,
        source_type: SourceType,
        tags: list[str] | None = None,
        derived_from: list[UUID] | None = None,
        metadata: dict[str, object] | None = None,
        initial_confidence: float | None = None,
    ) -> IngestResult:
        """Ingest a new fact into the knowledge graph.

        Returns IngestResult with the stored fact or a rejection reason.
        """
        self._require_connected()
        assert self._ingest_pipeline is not None

        return await self._ingest_pipeline.ingest(
            content=content,
            source_agent_id=self._agent_id,
            source_type=source_type,
            tags=tags,
            derived_from=derived_from,
            metadata=metadata,
            initial_confidence=initial_confidence,
        )

    async def query(
        self,
        question: str,
        filters: QueryFilters | None = None,
        limit: int | None = None,
    ) -> list[QueryResult]:
        """Query the knowledge graph.

        Returns ranked list of matching facts with relevance scores.
        """
        self._require_connected()
        assert self._query_engine is not None

        return await self._query_engine.query(
            question=question,
            filters=filters,
            limit=limit,
        )

    async def correct(
        self,
        fact_id: UUID,
        new_content: FactContent,
        reason: str,
    ) -> IngestResult:
        """Correct an existing fact by creating a superseding fact.

        D-ARCH-6: Facts are immutable. Corrections create new facts.
        """
        self._require_connected()
        assert self._fact_repo is not None
        assert self._ingest_pipeline is not None

        # Log and expire the old fact
        await self._ops.log(
            "correct", "started",
            agent_id=self._agent_id,
            fact_id=fact_id,
            detail={"reason": reason},
        )
        await self._fact_repo.expire(fact_id)

        # Ingest the new fact as a human correction, linking to the old one
        result = await self._ingest_pipeline.ingest(
            content=new_content,
            source_agent_id=self._agent_id,
            source_type=SourceType.HUMAN_CORRECTION,
            derived_from=[fact_id],
            metadata={"correction_reason": reason},
        )

        # If accepted, create supersedes relation
        if result.accepted and result.fact is not None:
            result.fact.supersedes = fact_id
            relation = FactRelation(
                id=uuid4(),
                source_fact_id=result.fact.id,
                target_fact_id=fact_id,
                relation_type=RelationType.SUPERSEDES,
                created_by=self._agent_id,
            )
            assert self._relation_repo is not None
            await self._relation_repo.insert(relation)

        return result

    async def verify(
        self,
        fact_id: UUID,
        method: VerificationMethod,
        status: VerificationStatus,
        reason: str | None = None,
    ) -> VerificationResult:
        """Verify an existing fact and evolve trust based on the outcome."""
        self._require_connected()
        assert self._verification_pipeline is not None
        return await self._verification_pipeline.verify(
            fact_id=fact_id,
            method=method,
            status=status,
            reason=reason,
        )

    async def corroborate(
        self,
        fact_id: UUID,
        corroborating_fact_id: UUID,
        reason: str | None = None,
    ) -> CorroborationResult:
        """Record explicit cross-agent corroboration and update scores."""
        self._require_connected()
        assert self._fact_repo is not None
        assert self._relation_repo is not None
        assert self._agent_repo is not None

        if fact_id == corroborating_fact_id:
            raise ValueError("corroborating_fact_id must differ from fact_id")

        target = await self._fact_repo.get_by_id(fact_id)
        corroborating = await self._fact_repo.get_by_id(corroborating_fact_id)
        if target is None:
            raise ValueError(f"fact not found: {fact_id}")
        if corroborating is None:
            raise ValueError(f"corroborating fact not found: {corroborating_fact_id}")

        existing_relations = await self._relation_repo.find_for_fact(fact_id)
        relation_exists = any(
            rel.relation_type == RelationType.CORROBORATES
            and (
                (
                    rel.source_fact_id == corroborating_fact_id
                    and rel.target_fact_id == fact_id
                )
                or (
                    rel.source_fact_id == fact_id
                    and rel.target_fact_id == corroborating_fact_id
                )
            )
            for rel in existing_relations
        )

        relation_created = False
        if not relation_exists:
            relation = FactRelation(
                id=uuid4(),
                source_fact_id=corroborating_fact_id,
                target_fact_id=fact_id,
                relation_type=RelationType.CORROBORATES,
                created_by=self._agent_id,
                metadata={"reason": reason} if reason else {},
            )
            await self._relation_repo.insert(relation)
            relation_created = True

        new_corroboration_count = target.corroboration_count + (1 if relation_created else 0)
        source_agent = await self._agent_repo.get_by_id(target.source_agent_id)
        new_trust = compute_trust_score(
            target.source_type,
            agent=source_agent,
            corroboration_count=new_corroboration_count,
        )
        confidence_delta = 0.05 if relation_created else 0.0
        new_confidence = round(
            min(1.0, max(0.0, target.confidence + confidence_delta)),
            4,
        )

        await self._fact_repo.update_scores(
            target.id,
            confidence=new_confidence,
            trust_score=new_trust,
            corroboration_count=new_corroboration_count,
        )
        verified_at = datetime.now()
        await self._fact_repo.update_verification(
            target.id,
            status=VerificationStatus.VERIFIED,
            verified_at=verified_at,
        )
        await self._agent_repo.update_trust_stats(
            target.source_agent_id,
            trust_score_delta=(0.01 if relation_created else 0.0),
        )

        await self._ops.log(
            "verify",
            "corroborated",
            fact_id=target.id,
            agent_id=self._agent_id,
            detail={
                "method": VerificationMethod.CROSS_AGENT_CORROBORATION.value,
                "corroborating_fact_id": corroborating.id,
                "relation_created": relation_created,
                "reason": reason,
            },
        )

        return CorroborationResult(
            fact_id=target.id,
            corroborating_fact_id=corroborating.id,
            corroboration_count=new_corroboration_count,
            confidence_delta=confidence_delta,
            trust_delta=round(new_trust - target.trust_score, 4),
            relation_created=relation_created,
            verified_at=verified_at,
        )

    async def resolve_conflicts(
        self,
        limit: int = 100,
    ) -> list[ConflictResolutionResult]:
        """Resolve detected conflicts using Phase 4 conflict resolution workflow."""
        self._require_connected()
        assert self._conflict_resolution_pipeline is not None
        return await self._conflict_resolution_pipeline.resolve_pending(limit=limit)

    async def resolve_conflict(
        self,
        conflict: Conflict,
    ) -> ConflictResolutionResult:
        """Resolve a single conflict record."""
        self._require_connected()
        assert self._conflict_resolution_pipeline is not None
        return await self._conflict_resolution_pipeline.resolve_one(conflict)

    async def trace_provenance(
        self,
        fact_id: UUID,
        max_depth: int = 16,
    ) -> list[ProvenanceEntry]:
        """Trace causal provenance chain for a fact."""
        self._require_connected()
        assert self._provenance_pipeline is not None
        return await self._provenance_pipeline.trace_chain(
            fact_id=fact_id,
            max_depth=max_depth,
        )

    # --- Context management ---

    async def update_context(self, context: ActiveContext) -> None:
        """Update this agent's active context for relevance matching."""
        self._require_connected()
        assert self._agent_repo is not None

        agent = await self._agent_repo.get_by_id(self._agent_id)
        if agent is not None:
            agent.active_context = context
            agent.context_updated_at = datetime.now()
            await self._agent_repo.upsert(agent)

    # --- Event listener ---

    def on_fact(
        self, callback: Callable[[PropagationEvent], Awaitable[None]]
    ) -> None:
        """Register a callback for incoming propagation events."""
        self._on_fact_callback = callback

    # --- Replay ---

    async def replay(
        self, since: datetime | None = None, *, deliver: bool = False
    ) -> list[PropagationEvent]:
        """Replay missed propagation events since last disconnect.

        If deliver=True and on_fact callback is registered, delivers each event
        to the callback and marks it as delivered.

        Returns the list of undelivered events.
        """
        self._require_connected()
        if self._event_log is None:
            return []

        events = await self._event_log.get_undelivered(self._agent_id)

        if deliver and self._on_fact_callback is not None:
            for event in events:
                await self._handle_event(event)

        return events
