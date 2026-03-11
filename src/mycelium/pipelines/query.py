"""Query engine — ARCHITECTURE.md Section 4.2.

Flow: embed query → candidate retrieval → filter → rank → return
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from mycelium.domain.types import RankingProfile
from mycelium.ops.logger import NullOpsLogger, OpsLogger, ms_since

if TYPE_CHECKING:
    from mycelium.config import MyceliumConfig
    from mycelium.domain.types import ActiveContext, Fact, SourceType
    from mycelium.embeddings.protocols import EmbeddingProvider
    from mycelium.storage.protocols import FactRepository


@dataclass(frozen=True)
class QueryFilters:
    """Filters applied to query results."""

    min_confidence: float = 0.0
    source_types: list[SourceType] | None = None
    tags: list[str] | None = None
    subject: str | None = None
    active_only: bool = True
    include_conflicted: bool = True
    consolidate_by_subject: bool = True


@dataclass(frozen=True)
class QueryResult:
    """A ranked query result — fact + relevance score."""

    fact: Fact
    score: float
    similarity: float


@dataclass
class RankingWeights:
    """Weights for the ranking function."""

    similarity: float = 0.5
    trust: float = 0.25
    recency: float = 0.25
    verified_boost: float = 0.15
    stale_penalty: float = 0.10
    failed_penalty: float = 0.25
    unresolved_conflict_penalty: float = 0.10


_ROLE_RANKING_PROFILES: dict[str, RankingProfile] = {
    "trader": RankingProfile(
        similarity_weight=0.35,
        trust_weight=0.30,
        recency_weight=0.35,
        recency_half_life_hours=24,
        verification_boost=0.15,
        stale_penalty=0.10,
        failed_penalty=0.25,
        unresolved_conflict_penalty=0.30,
        no_access_stale_penalty=0.05,
        no_access_grace_hours=336,
    ),
    "code": RankingProfile(
        similarity_weight=0.55,
        trust_weight=0.30,
        recency_weight=0.15,
        recency_half_life_hours=720,
        verification_boost=0.15,
        stale_penalty=0.10,
        failed_penalty=0.25,
        unresolved_conflict_penalty=0.10,
        no_access_stale_penalty=0.05,
        no_access_grace_hours=336,
    ),
    "research": RankingProfile(
        similarity_weight=0.45,
        trust_weight=0.25,
        recency_weight=0.30,
        recency_half_life_hours=168,
        verification_boost=0.20,
        stale_penalty=0.10,
        failed_penalty=0.25,
        unresolved_conflict_penalty=0.12,
        no_access_stale_penalty=0.05,
        no_access_grace_hours=336,
    ),
    "coordinator": RankingProfile(
        similarity_weight=0.45,
        trust_weight=0.30,
        recency_weight=0.25,
        recency_half_life_hours=168,
        verification_boost=0.15,
        stale_penalty=0.10,
        failed_penalty=0.25,
        unresolved_conflict_penalty=0.10,
        no_access_stale_penalty=0.05,
        no_access_grace_hours=336,
    ),
}


class QueryEngine:
    """Orchestrates query: embed → retrieve → filter → rank.

    Dependencies injected via constructor.
    """

    def __init__(
        self,
        fact_repo: FactRepository,
        embedding_provider: EmbeddingProvider,
        config: MyceliumConfig,
        ranking_weights: RankingWeights | None = None,
        ops_logger: OpsLogger | None = None,
    ) -> None:
        self._fact_repo = fact_repo
        self._embedding = embedding_provider
        self._config = config
        self._weights = ranking_weights or RankingWeights()
        self._ops = ops_logger or NullOpsLogger()

    async def query(
        self,
        question: str,
        filters: QueryFilters | None = None,
        limit: int | None = None,
        active_context: ActiveContext | None = None,
        agent_role: str | None = None,
        ranking_profile: RankingProfile | None = None,
        ranking_adjustment: dict[str, object] | None = None,
        requester_agent_id: str | None = None,
    ) -> list[QueryResult]:
        """Run the full query pipeline.

        Args:
            question: Natural language query or structured query text.
            filters: Optional filters to apply.
            limit: Max results to return (defaults to config.query_result_limit).

        Returns:
            Ranked list of QueryResult (fact + relevance score).
        """
        t0 = time.monotonic()
        filters = filters or QueryFilters()
        limit = limit or self._config.query_result_limit

        # 1. Embed query
        query_embedding = await self._embedding.embed(question)

        # 2. Candidate retrieval — hybrid approach
        candidates: dict[str, tuple[Fact, float]] = {}  # fact_id_str -> (fact, similarity)

        # 2a. Semantic retrieval via embedding
        semantic_results = await self._fact_repo.find_by_embedding(
            query_embedding,
            limit=limit * 3,  # over-fetch for filtering
            min_similarity=0.0,
            active_only=filters.active_only,
        )
        for fact, similarity in semantic_results:
            candidates[str(fact.id)] = (fact, similarity)

        # 2b. Structural retrieval — exact subject match
        if filters.subject:
            subject_results = await self._fact_repo.find_by_subject(
                filters.subject, active_only=filters.active_only
            )
            for fact in subject_results:
                if str(fact.id) not in candidates:
                    # Compute similarity for ranking
                    if fact.embedding is not None:
                        from mycelium.domain.conflict import cosine_similarity

                        sim = cosine_similarity(query_embedding, fact.embedding)
                    else:
                        sim = 0.0
                    candidates[str(fact.id)] = (fact, sim)

        # 2c. Tag retrieval
        if filters.tags:
            tag_results = await self._fact_repo.find_by_tags(
                filters.tags, active_only=filters.active_only
            )
            for fact in tag_results:
                if str(fact.id) not in candidates:
                    if fact.embedding is not None:
                        from mycelium.domain.conflict import cosine_similarity

                        sim = cosine_similarity(query_embedding, fact.embedding)
                    else:
                        sim = 0.0
                    candidates[str(fact.id)] = (fact, sim)

        # 3. Filter
        filtered: list[tuple[Fact, float]] = []
        for fact, similarity in candidates.values():
            if not self._passes_filters(fact, filters):
                continue
            filtered.append((fact, similarity))

        # 4. Rank
        now = datetime.now(UTC)
        ranked: list[QueryResult] = []
        profile = self._resolve_profile(agent_role, ranking_profile)
        profile = self._apply_profile_adjustment(profile, ranking_adjustment)
        for fact, similarity in filtered:
            score = self._compute_score(
                fact,
                similarity,
                now,
                profile,
                active_context=active_context,
            )
            ranked.append(QueryResult(fact=fact, score=score, similarity=similarity))

        ranked.sort(key=lambda r: r.score, reverse=True)

        if filters.consolidate_by_subject:
            ranked = self._consolidate_by_subject(ranked)

        # 5. Record access for returned facts
        final = ranked[:limit]
        for result in final:
            await self._fact_repo.record_access(result.fact.id)

        await self._ops.log(
            "query", "success",
            agent_id=requester_agent_id,
            latency_ms=ms_since(t0),
            detail={
                "question": question,
                "candidates": len(candidates),
                "filtered": len(filtered),
                "ranked": len(ranked),
                "returned": len(final),
            },
        )

        return final

    def _apply_profile_adjustment(
        self,
        profile: RankingProfile,
        adjustment: dict[str, object] | None,
    ) -> RankingProfile:
        if not adjustment:
            return profile

        similarity_delta = _as_float(adjustment.get("similarity_weight_delta"))
        trust_delta = _as_float(adjustment.get("trust_weight_delta"))
        recency_delta = _as_float(adjustment.get("recency_weight_delta"))
        if similarity_delta is None and trust_delta is None and recency_delta is None:
            return profile

        sim = max(0.05, profile.similarity_weight + (similarity_delta or 0.0))
        trust = max(0.05, profile.trust_weight + (trust_delta or 0.0))
        recency = max(0.05, profile.recency_weight + (recency_delta or 0.0))
        total = sim + trust + recency

        return RankingProfile(
            similarity_weight=round(sim / total, 4),
            trust_weight=round(trust / total, 4),
            recency_weight=round(recency / total, 4),
            recency_half_life_hours=profile.recency_half_life_hours,
            verification_boost=profile.verification_boost,
            stale_penalty=profile.stale_penalty,
            failed_penalty=profile.failed_penalty,
            unresolved_conflict_penalty=profile.unresolved_conflict_penalty,
            no_access_stale_penalty=profile.no_access_stale_penalty,
            no_access_grace_hours=profile.no_access_grace_hours,
        )

    def _passes_filters(self, fact: Fact, filters: QueryFilters) -> bool:
        """Check if a fact passes the query filters."""
        if fact.confidence < filters.min_confidence:
            return False
        if filters.source_types and fact.source_type not in filters.source_types:
            return False
        return filters.include_conflicted or not fact.is_conflicted

    def _compute_score(
        self,
        fact: Fact,
        similarity: float,
        now: datetime,
        profile: RankingProfile,
        *,
        active_context: ActiveContext | None = None,
    ) -> float:
        """Compute weighted relevance score.

        Score = w_sim * similarity + w_trust * trust_score + w_recency * recency_factor
        """
        # Recency factor: exponential decay from valid_from
        # Ensure both datetimes have matching awareness to avoid TypeError
        valid_from = fact.valid_from
        if valid_from.tzinfo is None and now.tzinfo is not None:
            valid_from = valid_from.replace(tzinfo=now.tzinfo)
        elif valid_from.tzinfo is not None and now.tzinfo is None:
            now = now.replace(tzinfo=valid_from.tzinfo)
        age_hours = max(0, (now - valid_from).total_seconds() / 3600)
        recency = 0.5 ** (age_hours / max(1, profile.recency_half_life_hours))
        score = (
            profile.similarity_weight * similarity
            + profile.trust_weight * fact.trust_score
            + profile.recency_weight * recency
        )

        if fact.verification_status.value == "verified":
            score += profile.verification_boost
        elif fact.verification_status.value == "stale":
            score -= profile.stale_penalty
        elif fact.verification_status.value == "failed":
            score -= profile.failed_penalty

        if fact.is_conflicted:
            score -= profile.unresolved_conflict_penalty

        if fact.access_count == 0 and age_hours >= profile.no_access_grace_hours:
            score -= profile.no_access_stale_penalty

        score += self._compute_context_boost(fact, active_context)

        return score

    def _resolve_profile(
        self,
        role: str | None,
        override: RankingProfile | None,
    ) -> RankingProfile:
        if override is not None:
            return override
        if role is None:
            return self._default_profile()
        normalized = role.strip().lower()
        return _ROLE_RANKING_PROFILES.get(normalized, self._default_profile())

    def _default_profile(self) -> RankingProfile:
        w = self._weights
        return RankingProfile(
            similarity_weight=w.similarity,
            trust_weight=w.trust,
            recency_weight=w.recency,
            recency_half_life_hours=168,
            verification_boost=w.verified_boost,
            stale_penalty=w.stale_penalty,
            failed_penalty=w.failed_penalty,
            unresolved_conflict_penalty=w.unresolved_conflict_penalty,
            no_access_stale_penalty=0.05,
            no_access_grace_hours=336,
        )

    def _consolidate_by_subject(
        self,
        ranked: list[QueryResult],
    ) -> list[QueryResult]:
        seen: set[str] = set()
        consolidated: list[QueryResult] = []
        for item in ranked:
            key = item.fact.content.subject.strip().lower()
            if key in seen:
                continue
            seen.add(key)
            consolidated.append(item)
        return consolidated

    def _compute_context_boost(
        self,
        fact: Fact,
        active_context: ActiveContext | None,
    ) -> float:
        if active_context is None or not active_context.entities:
            return 0.0

        entity_tokens = [item.strip().lower() for item in active_context.entities if item.strip()]
        if not entity_tokens:
            return 0.0

        haystacks = [
            fact.content.subject.lower(),
            fact.content.object.lower(),
            (fact.content.context or "").lower(),
        ]
        matched = any(
            token in hay
            for token in entity_tokens
            for hay in haystacks
        )
        if not matched:
            return 0.0

        urgency = active_context.urgency.value
        if urgency == "critical":
            return 0.30
        if urgency == "elevated":
            return 0.20
        return 0.10


def _as_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None
