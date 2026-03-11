"""Transport DTOs for server mode.

These models are intentionally separate from domain dataclasses.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any
from uuid import UUID  # noqa: TC003

from pydantic import BaseModel, ConfigDict, Field

from mycelium.domain.types import (
    ActiveContext,
    Conflict,
    ConflictResolutionResult,
    ConflictStatus,
    CorroborationResult,
    Fact,
    FactContent,
    FeedbackResult,
    FeedbackSignal,
    Priority,
    PropagationEvent,
    SourceType,
    Subscription,
    Urgency,
    VerificationMethod,
    VerificationResult,
    VerificationStatus,
)
from mycelium.extraction.daily_notes import DailyNotesWorkspaceConfig
from mycelium.pipelines.ingest import IngestResult  # noqa: TC001
from mycelium.pipelines.provenance import ProvenanceEntry  # noqa: TC001
from mycelium.pipelines.query import QueryFilters, QueryResult


class ErrorResponse(BaseModel):
    error: str
    detail: str


class FactContentDTO(BaseModel):
    subject: str
    predicate: str
    object: str
    context: str | None = None

    def to_domain(self) -> FactContent:
        return FactContent(
            subject=self.subject,
            predicate=self.predicate,
            object=self.object,
            context=self.context,
        )


class ActiveContextDTO(BaseModel):
    task: str | None = None
    entities: list[str] = Field(default_factory=list)
    urgency: Urgency = Urgency.NORMAL

    def to_domain(self) -> ActiveContext:
        return ActiveContext(
            task=self.task,
            entities=tuple(self.entities),
            urgency=self.urgency,
        )


class SubscriptionDTO(BaseModel):
    topic: str
    priority: Priority = Priority.NORMAL
    min_confidence: float = 0.0
    source_types: list[SourceType] | None = None


class FactDTO(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    id: UUID
    content: FactContentDTO
    source_agent_id: str
    source_type: SourceType
    confidence: float
    trust_score: float
    valid_from: datetime
    valid_until: datetime | None
    created_at: datetime
    expired_at: datetime | None
    derived_from: list[UUID]
    supersedes: UUID | None
    predicate_canonical: str | None
    corroboration_count: int
    last_accessed_at: datetime | None
    access_count: int
    last_verified_at: datetime | None
    verification_status: VerificationStatus
    tags: list[str]
    conflict_status: str
    metadata: dict[str, object]


class ConflictDTO(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    id: UUID
    fact_a_id: UUID
    fact_b_id: UUID
    status: ConflictStatus
    resolution: dict[str, object] | None
    winning_fact_id: UUID | None
    detected_at: datetime
    resolved_at: datetime | None
    resolved_by: str | None
    metadata: dict[str, object]


class QueryFiltersDTO(BaseModel):
    min_confidence: float = 0.0
    source_types: list[SourceType] | None = None
    tags: list[str] | None = None
    subject: str | None = None
    active_only: bool = True
    include_conflicted: bool = True
    consolidate_by_subject: bool = True

    def to_domain(self) -> QueryFilters:
        return QueryFilters(
            min_confidence=self.min_confidence,
            source_types=self.source_types,
            tags=self.tags,
            subject=self.subject,
            active_only=self.active_only,
            include_conflicted=self.include_conflicted,
            consolidate_by_subject=self.consolidate_by_subject,
        )


class QueryResultDTO(BaseModel):
    fact: FactDTO
    score: float
    similarity: float


class IngestRequest(BaseModel):
    content: FactContentDTO
    source_type: SourceType
    tags: list[str] | None = None
    derived_from: list[UUID] | None = None
    metadata: dict[str, object] | None = None
    initial_confidence: float | None = None


class RejectionDTO(BaseModel):
    code: str
    message: str
    existing_fact_id: UUID | None = None


class IngestResponse(BaseModel):
    accepted: bool
    fact: FactDTO | None = None
    rejection: RejectionDTO | None = None
    contradiction_fact_ids: list[UUID] = Field(default_factory=lambda: list[UUID]())
    corroboration_fact_ids: list[UUID] = Field(default_factory=lambda: list[UUID]())


class RawIngestRequest(BaseModel):
    """Request for /ingest/raw — raw text that the server parses into a fact."""

    raw_text: str
    source_agent_id: str | None = None


class RawIngestResponse(BaseModel):
    """Response for /ingest/raw — extends IngestResponse with parsed fields."""

    accepted: bool
    fact: FactDTO | None = None
    rejection: RejectionDTO | None = None
    contradiction_fact_ids: list[UUID] = Field(default_factory=lambda: list[UUID]())
    corroboration_fact_ids: list[UUID] = Field(default_factory=lambda: list[UUID]())
    parsed_subject: str | None = None
    parsed_predicate: str | None = None


class RawQueryContext(BaseModel):
    """Optional raw context for query — server extracts entities to build question."""

    task_name: str | None = None
    prompt: str | None = None


class QueryRequest(BaseModel):
    question: str = ""
    filters: QueryFiltersDTO | None = None
    limit: int | None = None
    raw_context: RawQueryContext | None = None


class ExtractionWorkspaceDTO(BaseModel):
    workspace_key: str
    glob_pattern: str
    source_agent_id: str
    correction_authority: bool = False

    def to_domain(self) -> DailyNotesWorkspaceConfig:
        return DailyNotesWorkspaceConfig(
            workspace_key=self.workspace_key,
            glob_pattern=self.glob_pattern,
            source_agent_id=self.source_agent_id,
            correction_authority=self.correction_authority,
        )


class ExtractionRunRequest(BaseModel):
    workspaces: list[ExtractionWorkspaceDTO] | None = None
    expire_memory_migration_facts: bool = True


class WorkspaceExtractionStatsDTO(BaseModel):
    workspace_key: str
    files_seen: int
    files_processed: int
    facts_extracted: int
    facts_ingested: int
    facts_skipped: int
    facts_failed: int


class ExtractionRunResponse(BaseModel):
    started_at: datetime
    finished_at: datetime | None
    expired_memory_migration_facts: int
    total_files_seen: int
    total_files_processed: int
    total_facts_extracted: int
    total_facts_ingested: int
    total_facts_skipped: int
    total_facts_failed: int
    workspaces: list[WorkspaceExtractionStatsDTO] = Field(
        default_factory=lambda: list[WorkspaceExtractionStatsDTO]()
    )
    errors: list[str] = Field(default_factory=lambda: list[str]())


class CorrectRequest(BaseModel):
    fact_id: UUID
    new_content: FactContentDTO
    reason: str


class VerifyRequest(BaseModel):
    fact_id: UUID
    method: VerificationMethod
    status: VerificationStatus
    reason: str | None = None


class CorroborateRequest(BaseModel):
    fact_id: UUID
    corroborating_fact_id: UUID
    reason: str | None = None


class FeedbackRequest(BaseModel):
    fact_id: UUID
    signal: FeedbackSignal
    reason: str | None = None


class ResolveConflictsRequest(BaseModel):
    limit: int = 100


class ResolveConflictByIdRequest(BaseModel):
    conflict_id: UUID


class ReplayRequest(BaseModel):
    deliver: bool = False


class AckEventRequest(BaseModel):
    event_id: UUID


class ConnectRequest(BaseModel):
    agent_id: str
    role: str = "generic"
    subscriptions: list[SubscriptionDTO] | None = None


class ConnectResponse(BaseModel):
    agent_id: str
    connected: bool


class UpdateContextRequest(BaseModel):
    context: ActiveContextDTO


class UpdateSubscriptionsRequest(BaseModel):
    subscriptions: list[SubscriptionDTO]


class HealthResponse(BaseModel):
    status: str


class VersionResponse(BaseModel):
    service: str
    version: str


class VerificationResultDTO(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    fact_id: UUID
    status: VerificationStatus
    method: VerificationMethod
    previous_status: VerificationStatus
    verified_at: datetime
    confidence_delta: float
    trust_delta: float
    reason: str | None


class CorroborationResultDTO(BaseModel):
    fact_id: UUID
    corroborating_fact_id: UUID
    corroboration_count: int
    confidence_delta: float
    trust_delta: float
    relation_created: bool
    verified_at: datetime


class FeedbackResultDTO(BaseModel):
    fact_id: UUID
    signal: FeedbackSignal
    confidence_delta: float
    trust_delta: float
    verification_status: VerificationStatus
    reason: str | None


class ConflictResolutionResultDTO(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    conflict_id: UUID
    status: ConflictStatus
    winning_fact_id: UUID | None
    resolved_by: str
    resolution: dict[str, object]


class ProvenanceEntryDTO(BaseModel):
    depth: int
    fact: FactDTO


class EventDTO(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    id: UUID
    fact_id: UUID
    target_agent_id: str
    reason: str
    priority: Priority
    delivered: bool
    delivered_at: datetime | None
    created_at: datetime
    sequence_num: int


def fact_to_dto(fact: Fact) -> FactDTO:
    return FactDTO(
        id=fact.id,
        content=FactContentDTO(
            subject=fact.content.subject,
            predicate=fact.content.predicate,
            object=fact.content.object,
            context=fact.content.context,
        ),
        source_agent_id=fact.source_agent_id,
        source_type=fact.source_type,
        confidence=fact.confidence,
        trust_score=fact.trust_score,
        valid_from=fact.valid_from,
        valid_until=fact.valid_until,
        created_at=fact.created_at,
        expired_at=fact.expired_at,
        derived_from=fact.derived_from,
        supersedes=fact.supersedes,
        predicate_canonical=fact.predicate_canonical,
        corroboration_count=fact.corroboration_count,
        last_accessed_at=fact.last_accessed_at,
        access_count=fact.access_count,
        last_verified_at=fact.last_verified_at,
        verification_status=fact.verification_status,
        tags=fact.tags,
        conflict_status=fact.conflict_status,
        metadata=fact.metadata,
    )


def conflict_to_dto(conflict: Conflict) -> ConflictDTO:
    return ConflictDTO(
        id=conflict.id,
        fact_a_id=conflict.fact_a_id,
        fact_b_id=conflict.fact_b_id,
        status=conflict.status,
        resolution=conflict.resolution,
        winning_fact_id=conflict.winning_fact_id,
        detected_at=conflict.detected_at,
        resolved_at=conflict.resolved_at,
        resolved_by=conflict.resolved_by,
        metadata=conflict.metadata,
    )


def ingest_result_to_dto(result: IngestResult) -> IngestResponse:
    rejection = None
    if result.rejection is not None:
        rejection = RejectionDTO(
            code=result.rejection.code,
            message=result.rejection.message,
            existing_fact_id=result.rejection.existing_fact_id,
        )
    return IngestResponse(
        accepted=result.accepted,
        fact=fact_to_dto(result.fact) if result.fact is not None else None,
        rejection=rejection,
        contradiction_fact_ids=[c.existing_fact.id for c in result.conflicts],
        corroboration_fact_ids=[c.existing_fact.id for c in result.corroborations],
    )


def query_result_to_dto(result: QueryResult) -> QueryResultDTO:
    return QueryResultDTO(
        fact=fact_to_dto(result.fact),
        score=result.score,
        similarity=result.similarity,
    )


def verification_to_dto(result: VerificationResult) -> VerificationResultDTO:
    return VerificationResultDTO(
        fact_id=result.fact_id,
        status=result.status,
        method=result.method,
        previous_status=result.previous_status,
        verified_at=result.verified_at,
        confidence_delta=result.confidence_delta,
        trust_delta=result.trust_delta,
        reason=result.reason,
    )


def corroboration_to_dto(result: CorroborationResult) -> CorroborationResultDTO:
    return CorroborationResultDTO(
        fact_id=result.fact_id,
        corroborating_fact_id=result.corroborating_fact_id,
        corroboration_count=result.corroboration_count,
        confidence_delta=result.confidence_delta,
        trust_delta=result.trust_delta,
        relation_created=result.relation_created,
        verified_at=result.verified_at,
    )


def feedback_to_dto(result: FeedbackResult) -> FeedbackResultDTO:
    return FeedbackResultDTO(
        fact_id=result.fact_id,
        signal=result.signal,
        confidence_delta=result.confidence_delta,
        trust_delta=result.trust_delta,
        verification_status=result.verification_status,
        reason=result.reason,
    )


def conflict_resolution_to_dto(
    result: ConflictResolutionResult,
) -> ConflictResolutionResultDTO:
    return ConflictResolutionResultDTO(
        conflict_id=result.conflict_id,
        status=result.status,
        winning_fact_id=result.winning_fact_id,
        resolved_by=result.resolved_by,
        resolution=result.resolution,
    )


def provenance_to_dto(entry: ProvenanceEntry) -> ProvenanceEntryDTO:
    return ProvenanceEntryDTO(depth=entry.depth, fact=fact_to_dto(entry.fact))


def event_to_dto(event: PropagationEvent) -> EventDTO:
    return EventDTO(
        id=event.id,
        fact_id=event.fact_id,
        target_agent_id=event.target_agent_id,
        reason=event.reason,
        priority=event.priority,
        delivered=event.delivered,
        delivered_at=event.delivered_at,
        created_at=event.created_at,
        sequence_num=event.sequence_num,
    )


def subscription_to_detail(sub: Subscription) -> dict[str, Any]:
    return {
        "topic": sub.topic,
        "priority": sub.priority.value,
        "min_confidence": sub.min_confidence,
        "source_types": [st.value for st in sub.source_types] if sub.source_types else None,
        "created_at": sub.created_at.isoformat(),
    }
