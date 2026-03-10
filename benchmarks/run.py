"""Benchmark suite for Mycelium Phase 5.

Produces:
- benchmarks/results/latest.json
- benchmarks/results/latest.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from mycelium.client.client import MyceliumClient
from mycelium.config import MyceliumConfig
from mycelium.domain.types import Conflict, ConflictStatus, FactContent, SourceType
from mycelium.embeddings.mock import MockEmbeddingProvider
from mycelium.storage.memory import (
    InMemoryAgentRepository,
    InMemoryConflictRepository,
    InMemoryEventLog,
    InMemoryFactRepository,
    InMemoryRelationRepository,
    InMemorySubscriptionRepository,
)
from mycelium.transport.in_process import InProcessTransport


@dataclass
class Metric:
    p50_ms: float
    p95_ms: float
    avg_ms: float


@dataclass
class BenchmarkResult:
    ingest: Metric
    query: Metric
    propagation_p95_ms: float
    conflict_resolution_per_s: float


def _percentile(samples: list[float], pct: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    idx = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * pct))))
    return ordered[idx]


def _metric(samples: list[float]) -> Metric:
    return Metric(
        p50_ms=round(_percentile(samples, 0.50), 3),
        p95_ms=round(_percentile(samples, 0.95), 3),
        avg_ms=round(statistics.mean(samples), 3),
    )


async def _build_client(
    agent_id: str,
    transport: InProcessTransport,
    fact_repo: InMemoryFactRepository,
    agent_repo: InMemoryAgentRepository,
    conflict_repo: InMemoryConflictRepository,
    relation_repo: InMemoryRelationRepository,
    sub_repo: InMemorySubscriptionRepository,
    event_log: InMemoryEventLog,
) -> MyceliumClient:
    client = MyceliumClient(
        agent_id=agent_id,
        role="benchmark",
        config=MyceliumConfig(),
        fact_repo=fact_repo,
        agent_repo=agent_repo,
        conflict_repo=conflict_repo,
        relation_repo=relation_repo,
        subscription_repo=sub_repo,
        event_log=event_log,
        embedding_provider=MockEmbeddingProvider(dimension=64),
        transport=transport,
    )
    await client.connect()
    return client


async def run_benchmarks(iterations: int) -> BenchmarkResult:
    fact_repo = InMemoryFactRepository()
    agent_repo = InMemoryAgentRepository()
    conflict_repo = InMemoryConflictRepository()
    relation_repo = InMemoryRelationRepository()
    sub_repo = InMemorySubscriptionRepository()
    event_log = InMemoryEventLog()
    transport = InProcessTransport()

    producer = await _build_client(
        "bench-producer",
        transport,
        fact_repo,
        agent_repo,
        conflict_repo,
        relation_repo,
        sub_repo,
        event_log,
    )
    consumer = await _build_client(
        "bench-consumer",
        transport,
        fact_repo,
        agent_repo,
        conflict_repo,
        relation_repo,
        sub_repo,
        event_log,
    )

    # Simple listener to exercise propagation path.
    async def _sink(_):
        return None

    consumer.on_fact(_sink)

    ingest_samples: list[float] = []
    for i in range(iterations):
        t0 = time.perf_counter()
        await producer.ingest(
            FactContent(subject=f"svc-{i}", predicate="has_status", object="ok"),
            source_type=SourceType.AGENT_EXTRACTION,
            tags=["bench.api"],
        )
        ingest_samples.append((time.perf_counter() - t0) * 1000)

    query_samples: list[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        await producer.query("svc has_status ok")
        query_samples.append((time.perf_counter() - t0) * 1000)

    propagation_samples: list[float] = []
    for i in range(max(10, iterations // 5)):
        t0 = time.perf_counter()
        await producer.ingest(
            FactContent(subject=f"prop-{i}", predicate="version_is", object="v2"),
            source_type=SourceType.AGENT_EXTRACTION,
            tags=["bench.propagation"],
        )
        propagation_samples.append((time.perf_counter() - t0) * 1000)

    # Create synthetic conflicts and measure resolver throughput.
    for i in range(max(20, iterations)):
        a = await producer.ingest(
            FactContent(subject=f"conflict-{i}", predicate="has_status", object="down"),
            source_type=SourceType.AGENT_EXTRACTION,
        )
        b = await producer.ingest(
            FactContent(subject=f"conflict-{i}", predicate="has_status", object="up"),
            source_type=SourceType.AGENT_INFERENCE,
        )
        if a.fact is not None and b.fact is not None:
            await conflict_repo.insert(
                Conflict(
                    id=uuid4(),
                    fact_a_id=a.fact.id,
                    fact_b_id=b.fact.id,
                    status=ConflictStatus.DETECTED,
                )
            )

    start = time.perf_counter()
    resolved = await producer.resolve_conflicts(limit=10_000)
    elapsed = max(0.001, time.perf_counter() - start)
    throughput = round(len(resolved) / elapsed, 3)

    await producer.disconnect()
    await consumer.disconnect()

    return BenchmarkResult(
        ingest=_metric(ingest_samples),
        query=_metric(query_samples),
        propagation_p95_ms=round(_percentile(propagation_samples, 0.95), 3),
        conflict_resolution_per_s=throughput,
    )


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _render_markdown(
    result: BenchmarkResult,
    baseline: dict[str, Any] | None,
) -> str:
    lines = [
        "# Mycelium Benchmark Report",
        "",
        "| Metric | Current | Baseline |",
        "|---|---:|---:|",
    ]

    current = asdict(result)
    flat = {
        "ingest_p95_ms": current["ingest"]["p95_ms"],
        "query_p95_ms": current["query"]["p95_ms"],
        "propagation_p95_ms": current["propagation_p95_ms"],
        "conflict_resolution_per_s": current["conflict_resolution_per_s"],
    }

    baseline_flat = baseline or {}
    for key, value in flat.items():
        base = baseline_flat.get(key, "-")
        lines.append(f"| {key} | {value} | {base} |")

    lines.extend(
        [
            "",
            "## Spec Targets",
            "",
            f"- ingest <100ms p95: {'PASS' if flat['ingest_p95_ms'] < 100 else 'FAIL'}",
            f"- query <50ms p95: {'PASS' if flat['query_p95_ms'] < 50 else 'FAIL'}",
            f"- propagation <1000ms p95: {'PASS' if flat['propagation_p95_ms'] < 1000 else 'FAIL'}",
        ]
    )

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--baseline", type=str, default="benchmarks/baseline.json")
    args = parser.parse_args()

    result = asyncio.run(run_benchmarks(args.iterations))

    result_dir = Path("benchmarks/results")
    result_dir.mkdir(parents=True, exist_ok=True)

    data = asdict(result)
    flat = {
        "ingest_p95_ms": data["ingest"]["p95_ms"],
        "query_p95_ms": data["query"]["p95_ms"],
        "propagation_p95_ms": data["propagation_p95_ms"],
        "conflict_resolution_per_s": data["conflict_resolution_per_s"],
    }

    json_path = result_dir / "latest.json"
    json_path.write_text(json.dumps(data, indent=2))

    baseline = _load_json(Path(args.baseline))
    report = _render_markdown(result, baseline)
    md_path = result_dir / "latest.md"
    md_path.write_text(report)

    # Print machine-readable summary for CI logs.
    print(json.dumps(flat, indent=2))


if __name__ == "__main__":
    main()
