"""Core domain types for Mycelium — ARCHITECTURE.md Section 5."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID


def _uuid_list() -> list[UUID]:
    return []


def _string_list() -> list[str]:
    return []


def _object_dict() -> dict[str, object]:
    return {}


# --- Enums ---


class SourceType(StrEnum):
    """How a fact was produced — determines base trust weight.

    Hierarchy (spec section 3.4):
        human_correction > system_verification > agent_extraction > agent_inference
    """

    HUMAN_CORRECTION = "human_correction"
    SYSTEM_VERIFICATION = "system_verification"
    AGENT_EXTRACTION = "agent_extraction"
    AGENT_INFERENCE = "agent_inference"

    @property
    def trust_weight(self) -> float:
        """Fixed hierarchy weight. Higher = more trusted."""
        weights: dict[SourceType, float] = {
            SourceType.HUMAN_CORRECTION: 1.0,
            SourceType.SYSTEM_VERIFICATION: 0.85,
            SourceType.AGENT_EXTRACTION: 0.6,
            SourceType.AGENT_INFERENCE: 0.4,
        }
        return weights[self]


class Priority(StrEnum):
    """Subscription/propagation priority levels."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class Urgency(StrEnum):
    """Agent active context urgency levels."""

    NORMAL = "normal"
    ELEVATED = "elevated"
    CRITICAL = "critical"


class ConflictStatus(StrEnum):
    """Lifecycle states for a conflict between two facts."""

    DETECTED = "detected"
    AUTO_RESOLVED = "auto_resolved"
    LLM_RESOLVED = "llm_resolved"
    HUMAN_RESOLVED = "human_resolved"
    ESCALATED = "escalated"


class RelationType(StrEnum):
    """Fixed relation types between facts — spec section 6.1.1a."""

    CONTRADICTS = "contradicts"
    SUPERSEDES = "supersedes"
    DERIVED_FROM = "derived_from"
    CORROBORATES = "corroborates"
    DEPENDS_ON = "depends_on"


class VerificationStatus(StrEnum):
    """Result of fact verification against ground truth."""

    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    FAILED = "failed"
    STALE = "stale"


class VerificationMethod(StrEnum):
    """Verification strategy used for a fact check."""

    SYSTEM_PROBE = "system_probe"
    CROSS_AGENT_CORROBORATION = "cross_agent_corroboration"
    TEMPORAL_CHECK = "temporal_check"
    SOURCE_RECHECK = "source_recheck"
    MANUAL_REVIEW = "manual_review"


# --- Value Objects ---


@dataclass(frozen=True)
class FactContent:
    """Structured content of a fact — spec section 6.1.1.

    Immutable value object. Two FactContents with the same fields are equal.
    """

    subject: str
    predicate: str
    object: str
    context: str | None = None

    def __post_init__(self) -> None:
        if not self.subject.strip():
            raise ValueError("subject must not be empty")
        if not self.predicate.strip():
            raise ValueError("predicate must not be empty")
        if not self.object.strip():
            raise ValueError("object must not be empty")

    def to_embedding_text(self) -> str:
        """Concatenation used for embedding generation — ARCHITECTURE.md Section 6.3."""
        parts = [self.subject, self.predicate, self.object]
        if self.context:
            parts.append(self.context)
        return " ".join(parts)


@dataclass(frozen=True)
class ActiveContext:
    """Dynamic context for relevance matching — spec section 6.2.1.

    Owned by the agent. Updated via update_context(). If never set,
    propagation falls back to static subscriptions only.
    """

    task: str | None = None
    entities: tuple[str, ...] = ()
    urgency: Urgency = Urgency.NORMAL


@dataclass(frozen=True)
class RejectionReason:
    """Returned when ingest rejects a fact."""

    code: str  # 'invalid_content', 'duplicate', 'verification_failed'
    message: str
    existing_fact_id: UUID | None = None


@dataclass(frozen=True)
class VerificationResult:
    """Result of a verification run."""

    fact_id: UUID
    status: VerificationStatus
    method: VerificationMethod
    previous_status: VerificationStatus
    verified_at: datetime
    confidence_delta: float
    trust_delta: float
    reason: str | None = None


@dataclass(frozen=True)
class CorroborationResult:
    """Result of explicit cross-agent corroboration."""

    fact_id: UUID
    corroborating_fact_id: UUID
    corroboration_count: int
    confidence_delta: float
    trust_delta: float
    relation_created: bool
    verified_at: datetime


@dataclass(frozen=True)
class ConflictResolutionResult:
    """Result of resolving a conflict in Phase 4."""

    conflict_id: UUID
    status: ConflictStatus
    winning_fact_id: UUID | None
    resolved_by: str
    resolution: dict[str, object]


# --- Entities ---


@dataclass
class Fact:
    """The atomic unit of knowledge — spec section 6.1.

    Content is immutable (corrections create new facts via supersedes).
    Scoring fields are mutable operational metadata (D-ARCH-6).
    """

    id: UUID
    content: FactContent
    source_agent_id: str
    source_type: SourceType

    # Scoring (mutable — operational metadata)
    confidence: float
    trust_score: float

    # Bi-temporal model
    valid_from: datetime
    valid_until: datetime | None = None
    created_at: datetime = field(default_factory=datetime.now)
    expired_at: datetime | None = None

    # Provenance
    derived_from: list[UUID] = field(default_factory=_uuid_list)
    supersedes: UUID | None = None
    predicate_canonical: str | None = None

    # Quality signals (mutable)
    corroboration_count: int = 0
    last_accessed_at: datetime | None = None
    access_count: int = 0
    last_verified_at: datetime | None = None
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED

    # Classification
    tags: list[str] = field(default_factory=_string_list)

    # Conflict
    conflict_status: str = "none"

    # Embedding
    embedding: list[float] | None = None

    # Extensible
    metadata: dict[str, object] = field(default_factory=_object_dict)

    # Migration tracking
    migrated_from: str | None = None
    migration_date: datetime | None = None

    @property
    def is_active(self) -> bool:
        """A fact is active if it hasn't been expired or temporally invalidated."""
        return self.expired_at is None and self.valid_until is None

    @property
    def is_conflicted(self) -> bool:
        return self.conflict_status == "unresolved"


@dataclass
class AgentRecord:
    """Persistent agent identity — spec section 6.2.

    Stored in mycelium.agents. Created/updated on connect().
    """

    id: str
    role: str

    # Trust history
    trust_score: float = 0.5
    facts_contributed: int = 0
    facts_contradicted: int = 0
    contradiction_rate: float = 0.0

    # Active context (6.2.1)
    active_context: ActiveContext = field(default_factory=ActiveContext)
    context_updated_at: datetime | None = None

    # Lifecycle
    last_seen_at: datetime | None = None
    last_ack_event_id: UUID | None = None
    created_at: datetime = field(default_factory=datetime.now)

    metadata: dict[str, object] = field(default_factory=_object_dict)


@dataclass
class Subscription:
    """Agent topic subscription — spec section 6.3."""

    id: UUID
    agent_id: str
    topic: str
    priority: Priority
    min_confidence: float = 0.0
    source_types: list[SourceType] | None = None
    created_at: datetime = field(default_factory=datetime.now)

    def matches_tags(self, tags: list[str]) -> bool:
        """Check if this subscription's topic matches any of the given tags.

        Supports wildcard patterns:
        - 'api.*' matches 'api.orders', 'api.auth'
        - 'infrastructure' matches only 'infrastructure'
        - '*' matches everything
        """
        if self.topic == "*":
            return True

        if self.topic.endswith(".*"):
            prefix = self.topic[:-2]
            return any(tag == prefix or tag.startswith(prefix + ".") for tag in tags)

        return self.topic in tags

    def passes_filters(self, fact: Fact) -> bool:
        """Check if a fact passes this subscription's filters."""
        if fact.confidence < self.min_confidence:
            return False
        return not (self.source_types is not None and fact.source_type not in self.source_types)


@dataclass
class PropagationEvent:
    """A fact delivery to a specific agent — spec section 6.4."""

    id: UUID
    fact_id: UUID
    target_agent_id: str
    reason: str
    priority: Priority
    delivered: bool = False
    delivered_at: datetime | None = None
    created_at: datetime = field(default_factory=datetime.now)
    sequence_num: int = 0

    # Populated when loading full event (not always present)
    fact: Fact | None = None


@dataclass
class Conflict:
    """A detected contradiction between two facts — spec section 7.7."""

    id: UUID
    fact_a_id: UUID
    fact_b_id: UUID
    status: ConflictStatus
    resolution: dict[str, object] | None = None
    winning_fact_id: UUID | None = None
    detected_at: datetime = field(default_factory=datetime.now)
    resolved_at: datetime | None = None
    resolved_by: str | None = None
    metadata: dict[str, object] = field(default_factory=_object_dict)

    # Populated when loading full conflict (not always present)
    fact_a: Fact | None = None
    fact_b: Fact | None = None


@dataclass(frozen=True)
class FactRelation:
    """A typed relation between two facts — spec section 6.1.1a."""

    id: UUID
    source_fact_id: UUID
    target_fact_id: UUID
    relation_type: RelationType
    created_by: str | None = None
    metadata: dict[str, object] = field(default_factory=_object_dict)
