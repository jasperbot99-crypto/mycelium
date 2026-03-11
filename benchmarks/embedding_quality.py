"""One-time embedding quality benchmark for retrieval precision/recall.

Default provider: text-embedding-3-small (OpenAI).
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from mycelium.domain.conflict import cosine_similarity
from mycelium.embeddings.mock import MockEmbeddingProvider
from mycelium.embeddings.openai import OpenAIEmbeddingProvider

if TYPE_CHECKING:
    from mycelium.embeddings.protocols import EmbeddingProvider


@dataclass(frozen=True)
class BenchmarkItem:
    subject: str
    text: str
    query: str


DATASET = [
    BenchmarkItem(
        "broker-alpaca",
        "alpaca broker adapter returns 429 after 15:00 utc",
        "alpaca 429 issue",
    ),
    BenchmarkItem(
        "risk-budget",
        "trading risk budget exhausted no new positions",
        "risk budget exhausted",
    ),
    BenchmarkItem(
        "api-orders",
        "orders api is healthy and processing normally",
        "order api health",
    ),
    BenchmarkItem(
        "research-eurusd",
        "research projects eurusd downside in next sessions",
        "eurusd market outlook",
    ),
    BenchmarkItem(
        "planner-priority",
        "planner set broker reliability as top priority",
        "current planner priorities",
    ),
    BenchmarkItem(
        "deploy-status",
        "deployment completed and service status is green",
        "deploy status",
    ),
]


def _build_provider(model: str, api_key: str | None) -> EmbeddingProvider:
    if model == "mock":
        return MockEmbeddingProvider(dimension=64)
    if not api_key:
        raise ValueError("OPENAI_API_KEY (or --api-key) is required unless --model mock")
    return OpenAIEmbeddingProvider(api_key=api_key, model=model)


async def run_benchmark(provider: EmbeddingProvider, k: int) -> dict[str, float | int]:
    corpus_embeddings: dict[str, list[float]] = {}
    for item in DATASET:
        corpus_embeddings[item.subject] = await provider.embed(item.text)

    hits_at_k = 0
    reciprocal_rank_sum = 0.0

    for item in DATASET:
        query_emb = await provider.embed(item.query)
        ranked = sorted(
            (
                (subject, cosine_similarity(query_emb, emb))
                for subject, emb in corpus_embeddings.items()
            ),
            key=lambda row: row[1],
            reverse=True,
        )
        subjects = [subject for subject, _ in ranked]
        rank = subjects.index(item.subject) + 1
        if rank <= k:
            hits_at_k += 1
        reciprocal_rank_sum += 1.0 / rank

    total = len(DATASET)
    precision_at_k = hits_at_k / total
    recall_at_k = hits_at_k / total
    mrr = reciprocal_rank_sum / total

    return {
        "dataset_size": total,
        "k": k,
        "precision_at_k": round(precision_at_k, 4),
        "recall_at_k": round(recall_at_k, 4),
        "mrr": round(mrr, 4),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="text-embedding-3-small")
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--out", default="benchmarks/results/embedding_quality.json")
    args = parser.parse_args()

    provider = _build_provider(args.model, args.api_key)
    result = asyncio.run(run_benchmark(provider, max(1, args.k)))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
