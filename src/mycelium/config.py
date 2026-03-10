"""Mycelium configuration — ARCHITECTURE.md Section 8."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mycelium.embeddings.protocols import EmbeddingProvider
    from mycelium.transport.protocols import Transport


def _default_subscriptions() -> list[SubscriptionConfig]:
    return []


@dataclass
class SubscriptionConfig:
    """Subscription declared at connect time — synced to database."""

    topic: str
    priority: str = "normal"  # low, normal, high, critical
    min_confidence: float = 0.0
    source_types: list[str] | None = None


@dataclass
class MyceliumConfig:
    """Configuration for a Mycelium instance — ARCHITECTURE.md Section 8."""

    # Database
    database_url: str = "postgresql://localhost:5432/mycelium_dev"
    schema: str = "mycelium"

    # Embedding
    embedding_provider: EmbeddingProvider | None = None
    openai_api_key: str | None = None

    # Transport
    transport: Transport | None = None
    supabase_url: str | None = None
    supabase_key: str | None = None

    # LLM (optional — for enrichment operations)
    llm_provider: str | None = None
    llm_api_key: str | None = None

    # Tuning
    contradiction_threshold: float = 0.75
    corroboration_threshold: float = 0.95
    conflict_ambiguity_window_s: int = 120
    llm_resolution_min_confidence: float = 0.7
    contradiction_sweep_interval_s: int = 60
    callback_timeout_s: float = 5.0
    default_confidence: float = 0.5
    query_result_limit: int = 20
    decay_cycle_interval_hours: int = 24

    # Operational logging
    ops_log_enabled: bool = True
    ops_log_schema: str = "ops"

    # Agent (set per client instance)
    subscriptions: list[SubscriptionConfig] = field(default_factory=_default_subscriptions)
