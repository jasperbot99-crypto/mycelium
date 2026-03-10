# Decision: Phase 3 Verification and Corroboration Workflows

_Date: 2026-03-10_  
_Status: Accepted_

## Context

Phase 3 required three missing capabilities:

1. Verification workflow for existing facts.
2. Trust evolution over time based on verification outcomes.
3. Explicit cross-agent corroboration flow.

The codebase already had verification fields on `Fact` (`verification_status`, `last_verified_at`) and trust utilities, but no end-to-end workflow that used them.

## Decision

Implement verification as a dedicated Layer 3 pipeline and expose both verification and corroboration in `MyceliumClient`.

## What Was Added

1. `VerificationPipeline` (`pipelines/verification.py`)
   - `verify(fact_id, method, status, reason?) -> VerificationResult`
   - Applies deterministic confidence/trust deltas per verification status.
   - Updates source-agent trust score via repository-level trust delta.
2. Client API
   - `MyceliumClient.verify(...)`
   - `MyceliumClient.corroborate(fact_id, corroborating_fact_id, reason?)`
3. Storage protocol extensions
   - `FactRepository.update_verification(...)`
   - `AgentRepository.update_trust_stats(..., trust_score_delta=...)`
   - Implemented for in-memory and Postgres repositories.
4. Domain types
   - `VerificationMethod`
   - `VerificationResult`
   - `CorroborationResult`

## Rationale

- Keeps verification logic in pipelines (layered architecture compliance).
- Uses deterministic, testable rules without introducing LLM dependencies.
- Preserves fact immutability: only operational fields are updated in place.
- Makes corroboration explicit and auditable via `CORROBORATES` relations.

## Follow-up

- Add pluggable verification providers (system probe, source re-check adapters).
- Add scenario tests for multi-agent corroboration and failed verification cascades.
