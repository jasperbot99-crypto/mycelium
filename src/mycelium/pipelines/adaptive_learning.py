"""Adaptive learning runner: implicit feedback, meta-learning, gaps, and trend summaries."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from mycelium.domain.types import FactContent, SourceType, VerificationStatus
from mycelium.ops.logger import NullOpsLogger, OpsLogger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import asyncpg

    from mycelium.client.client import MyceliumClient
    from mycelium.storage.protocols import AgentRepository, FactRepository

logger = logging.getLogger(__name__)


_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "into",
    "what",
    "when",
    "where",
    "why",
    "is",
    "today",
    "about",
    "please",
    "agent",
    "query",
    "status",
}


@dataclass
class AdaptiveLearningConfig:
    cycle_interval_hours: int = 6
    metrics_lookback_hours: int = 24
    max_metrics_rows_per_cycle: int = 1000
    min_agents_for_gap: int = 3
    trend_min_versions: int = 3
    max_trend_facts_per_cycle: int = 20


@dataclass
class AdaptiveLearningResult:
    implicit_feedback_updates: int = 0
    reliability_updates: int = 0
    gap_facts_created: int = 0
    stale_topic_facts_created: int = 0
    autotune_updates: int = 0
    trend_summaries_created: int = 0


class AdaptiveLearningRunner:
    """Periodic learning runner driven by operational/behavioral signals."""

    def __init__(
        self,
        *,
        pool: asyncpg.Pool,
        fact_repo: FactRepository,
        agent_repo: AgentRepository,
        system_client_factory: Callable[[], Awaitable[MyceliumClient]],
        config: AdaptiveLearningConfig | None = None,
        ops_logger: OpsLogger | None = None,
    ) -> None:
        self._pool = pool
        self._fact_repo = fact_repo
        self._agent_repo = agent_repo
        self._system_client_factory = system_client_factory
        self._config = config or AdaptiveLearningConfig()
        self._ops = ops_logger or NullOpsLogger()
        self._task: asyncio.Task[None] | None = None
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def run_cycle(self, now: datetime | None = None) -> AdaptiveLearningResult:
        now = now or datetime.now(UTC)
        since = now - timedelta(hours=self._config.metrics_lookback_hours)
        result = AdaptiveLearningResult()

        metrics_rows = await self._fetch_metrics_rows(since)
        result.implicit_feedback_updates = await self._apply_implicit_feedback(metrics_rows)
        result.autotune_updates = await self._autotune_agent_profiles(metrics_rows)
        result.reliability_updates = await self._apply_reliability_loop()

        system_client = await self._system_client_factory()
        gap_count, stale_count = await self._detect_gaps(system_client, since)
        trend_count = await self._extract_trend_summaries(system_client, now)
        result.gap_facts_created = gap_count
        result.stale_topic_facts_created = stale_count
        result.trend_summaries_created = trend_count

        await self._ops.log(
            "adaptive_learning_cycle",
            "complete",
            detail={
                "implicit_feedback_updates": result.implicit_feedback_updates,
                "reliability_updates": result.reliability_updates,
                "gap_facts_created": result.gap_facts_created,
                "stale_topic_facts_created": result.stale_topic_facts_created,
                "autotune_updates": result.autotune_updates,
                "trend_summaries_created": result.trend_summaries_created,
            },
        )
        return result

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self.run_cycle()
            except Exception:
                logger.exception("adaptive learning cycle failed")
            try:
                await asyncio.sleep(self._config.cycle_interval_hours * 3600)
            except asyncio.CancelledError:
                break

    async def _fetch_metrics_rows(self, since: datetime) -> list[dict[str, object]]:
        query = """
            SELECT created_at, event_type, agent_id, session_type,
                   stale_count, conflict_count, query_latency_ms, detail
            FROM mycelium_metrics
            WHERE created_at >= $1
            ORDER BY created_at DESC
            LIMIT $2
        """
        try:
            rows = await self._pool.fetch(query, since, self._config.max_metrics_rows_per_cycle)
        except Exception:
            return []
        result: list[dict[str, object]] = []
        for row in rows:
            result.append(
                {
                    "created_at": row.get("created_at"),
                    "event_type": row.get("event_type"),
                    "agent_id": row.get("agent_id"),
                    "session_type": row.get("session_type"),
                    "stale_count": row.get("stale_count"),
                    "conflict_count": row.get("conflict_count"),
                    "query_latency_ms": row.get("query_latency_ms"),
                    "detail": row.get("detail") or {},
                }
            )
        return result

    async def _apply_implicit_feedback(self, rows: list[dict[str, object]]) -> int:
        updates = 0
        for row in rows:
            if row.get("event_type") != "injection":
                continue
            detail = row.get("detail")
            if not isinstance(detail, dict):
                continue
            fact_ids = detail.get("fact_ids")
            if not isinstance(fact_ids, list) or not fact_ids:
                continue

            stale_count = _to_int(row.get("stale_count"))
            conflict_count = _to_int(row.get("conflict_count"))
            total = max(1, len(fact_ids))
            quality = 1.0 - (stale_count + conflict_count) / total
            delta = max(-0.03, min(0.03, (quality - 0.5) * 0.06))

            for raw in fact_ids:
                try:
                    fact_id = UUID(str(raw))
                except ValueError:
                    continue
                fact = await self._fact_repo.get_by_id(fact_id)
                if fact is None:
                    continue

                new_conf = round(min(1.0, max(0.0, fact.confidence + delta)), 4)
                await self._fact_repo.update_scores(fact.id, confidence=new_conf)
                await self._pool.execute(
                    """
                    UPDATE mycelium.facts
                    SET metadata = jsonb_set(
                        COALESCE(metadata, '{}'::jsonb),
                        '{usefulness_score}',
                        to_jsonb(
                            LEAST(
                                1.0,
                                GREATEST(
                                    -1.0,
                                    COALESCE((metadata->>'usefulness_score')::float, 0.0) + $1
                                )
                            )
                        ),
                        true
                    )
                    WHERE id = $2
                    """,
                    delta,
                    fact.id,
                )
                updates += 1
        return updates

    async def _autotune_agent_profiles(self, rows: list[dict[str, object]]) -> int:
        by_agent: dict[str, list[float]] = {}
        for row in rows:
            if row.get("event_type") != "session_summary":
                continue
            agent_id = str(row.get("agent_id") or "").strip()
            if not agent_id:
                continue
            detail = row.get("detail")
            if not isinstance(detail, dict):
                continue
            hit_rate = _to_float(detail.get("hit_rate"))
            if hit_rate is None:
                continue
            by_agent.setdefault(agent_id, []).append(max(0.0, min(1.0, hit_rate)))

        updates = 0
        for agent_id, hits in by_agent.items():
            if not hits:
                continue
            avg_hit = sum(hits) / len(hits)
            error = avg_hit - 0.60
            adjustment = {
                "similarity_weight_delta": round(max(-0.08, min(0.08, error * 0.10)), 4),
                "trust_weight_delta": round(max(-0.05, min(0.05, error * 0.06)), 4),
                "recency_weight_delta": round(max(-0.08, min(0.08, -error * 0.08)), 4),
                "updated_at": datetime.now(UTC).isoformat(),
                "avg_hit_rate": round(avg_hit, 4),
            }

            agent = await self._agent_repo.get_by_id(agent_id)
            if agent is None:
                continue
            meta = dict(agent.metadata)
            meta["ranking_adjustment"] = adjustment
            agent.metadata = meta
            await self._agent_repo.upsert(agent)
            updates += 1

        return updates

    async def _apply_reliability_loop(self) -> int:
        updates = 0
        for agent in await self._agent_repo.list_all():
            target = round(max(0.0, min(1.0, 1.0 - agent.contradiction_rate)), 4)
            delta = target - agent.trust_score
            if abs(delta) < 0.01:
                continue
            bounded = max(-0.03, min(0.03, delta))
            await self._agent_repo.update_trust_stats(agent.id, trust_score_delta=bounded)
            updates += 1
        return updates

    async def _detect_gaps(
        self,
        client: MyceliumClient,
        since: datetime,
    ) -> tuple[int, int]:
        rows = await self._pool.fetch(
            """
            SELECT agent_id, detail
            FROM ops.operation_log
            WHERE operation = 'query'
              AND status = 'success'
              AND created_at >= $1
              AND agent_id IS NOT NULL
            ORDER BY created_at DESC
            LIMIT 4000
            """,
            since,
        )

        topics_to_agents: dict[str, set[str]] = {}
        for row in rows:
            detail = row["detail"] or {}
            if not isinstance(detail, dict):
                continue
            returned = _to_int(detail.get("returned"))
            if returned > 0:
                continue
            question = str(detail.get("question") or "")
            topic = _topic_key(question)
            if not topic:
                continue
            agent = str(row["agent_id"])
            topics_to_agents.setdefault(topic, set()).add(agent)

        gap_facts = 0
        stale_facts = 0
        for topic, agents in topics_to_agents.items():
            if len(agents) < self._config.min_agents_for_gap:
                continue
            gap_subject = f"gap:{topic}"
            existing_gap = await self._fact_repo.find_by_subject(gap_subject, active_only=True)
            if not existing_gap:
                ingest = await client.ingest(
                    FactContent(
                        subject=gap_subject,
                        predicate="needs_knowledge",
                        object=f"{len(agents)} agents queried this topic with zero results",
                    ),
                    source_type=SourceType.SYSTEM_VERIFICATION,
                    tags=["meta.gap", "gap_detection"],
                    metadata={"agents": sorted(agents)},
                    initial_confidence=0.8,
                )
                if ingest.accepted:
                    gap_facts += 1

            subject_facts = await self._fact_repo.find_by_subject(topic, active_only=False)
            if subject_facts and all(
                (not f.is_active)
                or f.verification_status in {VerificationStatus.STALE, VerificationStatus.FAILED}
                for f in subject_facts
            ):
                stale_subject = f"stale-topic:{topic}"
                existing_stale = await self._fact_repo.find_by_subject(
                    stale_subject,
                    active_only=True,
                )
                if not existing_stale:
                    ingest = await client.ingest(
                        FactContent(
                            subject=stale_subject,
                            predicate="needs_refresh",
                            object="topic keeps getting queried but only stale/expired facts exist",
                        ),
                        source_type=SourceType.SYSTEM_VERIFICATION,
                        tags=["meta.gap", "stale_topic"],
                        metadata={"topic": topic},
                        initial_confidence=0.8,
                    )
                    if ingest.accepted:
                        stale_facts += 1

        return gap_facts, stale_facts

    async def _extract_trend_summaries(
        self,
        client: MyceliumClient,
        now: datetime,
    ) -> int:
        active = await self._fact_repo.find_all_active()
        cutoff = now - timedelta(days=30)

        groups: dict[tuple[str, str], list] = {}
        for fact in active:
            if fact.created_at < cutoff:
                continue
            key = (fact.content.subject.strip().lower(), fact.content.predicate.strip().lower())
            groups.setdefault(key, []).append(fact)

        created = 0
        for (subject_key, predicate_key), facts in sorted(groups.items()):
            if created >= self._config.max_trend_facts_per_cycle:
                break
            facts.sort(key=lambda f: f.created_at)
            unique_objects: list[str] = []
            seen: set[str] = set()
            for fact in facts:
                obj = fact.content.object.strip()
                if obj in seen:
                    continue
                seen.add(obj)
                unique_objects.append(obj)
            if len(unique_objects) < self._config.trend_min_versions:
                continue

            trend_subject = f"trend:{subject_key}:{predicate_key}:{now.date().isoformat()}"
            exists = await self._fact_repo.find_by_subject(trend_subject, active_only=True)
            if exists:
                continue

            tail = unique_objects[-3:]
            trend_text = " -> ".join(tail)
            ingest = await client.ingest(
                FactContent(
                    subject=trend_subject,
                    predicate="summarizes",
                    object=(
                        f"{subject_key} {predicate_key} changed "
                        f"{len(unique_objects)} times in 30d; "
                        f"latest sequence: {trend_text}"
                    ),
                ),
                source_type=SourceType.SYSTEM_VERIFICATION,
                tags=["meta.summary", "meta.trend"],
                metadata={
                    "subject": subject_key,
                    "predicate": predicate_key,
                    "versions": len(unique_objects),
                },
                initial_confidence=0.82,
            )
            if ingest.accepted:
                created += 1

        return created


def _to_int(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _to_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _topic_key(question: str) -> str:
    tokens = re.findall(r"[a-zA-Z0-9_./:-]{3,}", question.lower())
    for token in tokens:
        cleaned = token.strip("._-/:")
        if not cleaned or cleaned in _STOPWORDS:
            continue
        if cleaned.isdigit():
            continue
        return cleaned[:64]
    return ""
