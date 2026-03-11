# Decision: Explicit Feedback API for Ranking Learning Loop

Date: 2026-03-11

## Context
The improvement strategy required an explicit feedback path so downstream usefulness signals can influence fact quality and trust without waiting for offline jobs.

## Decision
Add explicit feedback handling in client + server:
- New endpoint: `POST /v1/agents/{agent_id}/feedback`
- Signals: `helpful`, `irrelevant`, `outdated`, `wrong`
- Feedback updates fact confidence/trust and, when relevant, verification status.
- `wrong` feedback additionally increments source agent contradiction stats and applies trust penalty.

## Rationale
- Keeps feedback in the same API boundary as all other graph interactions.
- Provides deterministic, auditable updates in the critical path.
- Creates a clean foundation for later implicit-feedback ingestion and feedback-adjusted ranking.

## Consequences
- New domain feedback types and result DTOs.
- MyceliumClient now exposes `feedback(...)`.
- Server API surface expands with backward-compatible additive route.
