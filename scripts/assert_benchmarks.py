"""Fail CI if benchmark outputs exceed spec thresholds."""

from __future__ import annotations

import json
from pathlib import Path

THRESHOLDS = {
    "ingest": 100.0,
    "query": 50.0,
    "propagation": 1000.0,
}


def main() -> None:
    result_path = Path("benchmarks/results/latest.json")
    data = json.loads(result_path.read_text())

    ingest_p95 = float(data["ingest"]["p95_ms"])
    query_p95 = float(data["query"]["p95_ms"])
    propagation_p95 = float(data["propagation_p95_ms"])

    failures: list[str] = []
    if ingest_p95 >= THRESHOLDS["ingest"]:
        failures.append(f"ingest p95 {ingest_p95} >= {THRESHOLDS['ingest']}")
    if query_p95 >= THRESHOLDS["query"]:
        failures.append(f"query p95 {query_p95} >= {THRESHOLDS['query']}")
    if propagation_p95 >= THRESHOLDS["propagation"]:
        failures.append(f"propagation p95 {propagation_p95} >= {THRESHOLDS['propagation']}")

    if failures:
        raise SystemExit("; ".join(failures))


if __name__ == "__main__":
    main()
