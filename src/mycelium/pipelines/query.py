"""Query engine — ARCHITECTURE.md Section 4.2.

Flow: embed query → candidate retrieval → filter → rank → return
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime

from mycelium.config import MyceliumConfig
from mycelium.domain.types import Fact, SourceType
from mycelium.embeddings.protocols import EmbeddingProvider
from mycelium.ops.logger import NullOpsLogger, OpsLogger, ms_since
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
        now = datetime.now()
        ranked: list[QueryResult] = []
        for fact, similarity in filtered:
            score = self._compute_score(fact, similarity, now)
            ranked.append(QueryResult(fact=fact, score=score, similarity=similarity))

        ranked.sort(key=lambda r: r.score, reverse=True)

        # 5. Record access for returned facts
        final = ranked[:limit]
        for result in final:
            await self._fact_repo.record_access(result.fact.id)

        await self._ops.log(
            "query", "success",
            latency_ms=ms_since(t0),
            detail={
                "question": question,
                "candidates": len(candidates),
                "filtered": len(filtered),
                "returned": len(final),
            },
        )

        return final

    def _passes_filters(self, fact: Fact, filters: QueryFilters) -> bool:
        """Check if a fact passes the query filters."""
        if fact.confidence < filters.min_confidence:
            return False
        if filters.source_types and fact.source_type not in filters.source_types:
            return False
        if not filters.include_conflicted and fact.is_conflicted:
            return False
        return True

    def _compute_score(
        self, fact: Fact, similarity: float, now: datetime
    ) -> float:
        """Compute weighted relevance score.

        Score = w_sim * similarity + w_trust * trust_score + w_recency * recency_factor
        """
        w = self._weights

        # Recency factor: exponential decay from valid_from
        age_hours = max(0, (now - fact.valid_from).total_seconds() / 3600)
        # Half-life of 168 hours (1 week) — facts lose half their recency value per week
        recency = 0.5 ** (age_hours / 168)

        return (
            w.similarity * similarity
            + w.trust * fact.trust_score
            + w.recency * recency
        )
