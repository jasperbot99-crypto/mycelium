"""FastAPI server mode for Mycelium."""

from __future__ import annotations

from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from uuid import UUID  # noqa: TC003

from fastapi import APIRouter, Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

from mycelium.config import MyceliumConfig, SubscriptionConfig
from mycelium.pipelines.parsing import (
    extract_query_from_context,
    looks_like_garbage,
    parse_memory_store_to_fact,
)
from mycelium.server.auth import auth_dependency_factory
from mycelium.server.dto import (
    AckEventRequest,
    ConflictDTO,
    ConflictResolutionResultDTO,
    ConnectRequest,
    ConnectResponse,
    CorrectRequest,
    CorroborateRequest,
    CorroborationResultDTO,
    ErrorResponse,
    EventDTO,
    ExtractionRunRequest,
    ExtractionRunResponse,
    FeedbackRequest,
    FeedbackResultDTO,
    HealthResponse,
    IngestRequest,
    IngestResponse,
    QueryRequest,
    QueryResultDTO,
    RawIngestRequest,
    RawIngestResponse,
    RejectionDTO,
    ReplayRequest,
    ResolveConflictsRequest,
    UpdateContextRequest,
    UpdateSubscriptionsRequest,
    VerificationResultDTO,
    VerifyRequest,
    VersionResponse,
    WorkspaceExtractionStatsDTO,
    conflict_resolution_to_dto,
    conflict_to_dto,
    corroboration_to_dto,
    event_to_dto,
    fact_to_dto,
    feedback_to_dto,
    ingest_result_to_dto,
    provenance_to_dto,
    query_result_to_dto,
    subscription_to_detail,
    verification_to_dto,
)
from mycelium.server.state import ServerState


def _to_subscription_configs(
    req: ConnectRequest | UpdateSubscriptionsRequest,
) -> list[SubscriptionConfig]:
    return [
        SubscriptionConfig(
            topic=item.topic,
            priority=item.priority.value,
            min_confidence=item.min_confidence,
            source_types=[st.value for st in item.source_types] if item.source_types else None,
        )
        for item in (req.subscriptions or [])
    ]


def create_app(config: MyceliumConfig, state: ServerState | None = None) -> FastAPI:
    """Create configured FastAPI app instance for Mycelium server mode."""

    server_state = state or ServerState(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if state is None:
            await server_state.startup()
        try:
            yield
        finally:
            if state is None:
                await server_state.shutdown()

    app = FastAPI(
        title="Mycelium Server",
        version="0.1.0",
        description="REST API for Mycelium multi-agent memory",
        lifespan=lifespan,
    )
    app.state.mycelium = server_state

    if config.server_cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=config.server_cors_allow_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    auth_dep = auth_dependency_factory(config.server_api_key)
    router = APIRouter(
        prefix="/v1",
        dependencies=[Depends(auth_dep)],
        responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
    )

    @router.post("/agents/connect", response_model=ConnectResponse)
    async def connect_agent(request: ConnectRequest) -> ConnectResponse:
        subs = _to_subscription_configs(request) if request.subscriptions else None
        client = await server_state.connect_agent(
            request.agent_id,
            role=request.role,
            subscriptions=subs,
        )
        get_connected_since = getattr(server_state, "get_connected_since", None)
        connected_since = (
            get_connected_since(client.agent_id) if callable(get_connected_since) else None
        )
        return ConnectResponse(
            agent_id=client.agent_id,
            connected=client.connected,
            connected_since=connected_since,
        )

    @router.post("/agents/{agent_id}/disconnect", response_model=ConnectResponse)
    async def disconnect_agent(agent_id: str) -> ConnectResponse:
        await server_state.disconnect_agent(agent_id)
        return ConnectResponse(agent_id=agent_id, connected=False, connected_since=None)

    @router.post(
        "/agents/{agent_id}/ingest",
        response_model=IngestResponse,
        responses={400: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
    )
    async def ingest(agent_id: str, request: IngestRequest) -> IngestResponse:
        client = server_state.require_client(agent_id)
        result = await client.ingest(
            content=request.content.to_domain(),
            source_type=request.source_type,
            tags=request.tags,
            derived_from=request.derived_from,
            metadata=request.metadata,
            initial_confidence=request.initial_confidence,
        )
        return ingest_result_to_dto(result)

    @router.post(
        "/agents/{agent_id}/ingest/raw",
        response_model=RawIngestResponse,
        responses={400: {"model": ErrorResponse}},
    )
    async def ingest_raw(agent_id: str, request: RawIngestRequest) -> RawIngestResponse:
        client = server_state.require_client(agent_id)
        source = request.source_agent_id or agent_id

        # Reject garbage before parsing
        if looks_like_garbage(request.raw_text):
            return RawIngestResponse(
                accepted=False,
                rejection=RejectionDTO(
                    code="parse_rejected:garbage",
                    message="Input looks like garbage "
                    "(JSON fragment, escaped strings, or credentials)",
                ),
            )

        parsed = parse_memory_store_to_fact(request.raw_text, source)
        if parsed is None:
            return RawIngestResponse(
                accepted=False,
                rejection=RejectionDTO(
                    code="parse_rejected:unparseable",
                    message="Could not parse input into a structured fact",
                ),
            )

        result = await client.ingest(
            content=parsed.content,
            source_type=parsed.source_type,
            tags=parsed.tags,
        )
        dto = ingest_result_to_dto(result)
        return RawIngestResponse(
            accepted=dto.accepted,
            fact=dto.fact,
            rejection=dto.rejection,
            contradiction_fact_ids=dto.contradiction_fact_ids,
            corroboration_fact_ids=dto.corroboration_fact_ids,
            parsed_subject=parsed.content.subject,
            parsed_predicate=parsed.content.predicate,
        )

    @router.post("/agents/{agent_id}/query", response_model=list[QueryResultDTO])
    async def query(agent_id: str, request: QueryRequest) -> list[QueryResultDTO]:
        client = server_state.require_client(agent_id)

        question = request.question
        # When question is empty and raw_context is provided, extract entities
        if not question and request.raw_context:
            question = extract_query_from_context(
                task_name=request.raw_context.task_name,
                prompt=request.raw_context.prompt,
            ) or ""

        if not question:
            return []

        results = await client.query(
            question=question,
            filters=request.filters.to_domain() if request.filters else None,
            limit=request.limit,
        )
        return [query_result_to_dto(item) for item in results]

    @router.get(
        "/agents/{agent_id}/facts",
        response_model=list[dict[str, object]],
    )
    async def list_agent_facts(
        agent_id: str,
        limit: int = 50,
        offset: int = 0,
        active_only: bool = True,
    ) -> list[dict[str, object]]:
        facts = await server_state.list_agent_facts(
            agent_id,
            limit=max(1, min(limit, 500)),
            offset=max(0, offset),
            active_only=active_only,
        )
        return [fact_to_dto(fact).model_dump(mode="json") for fact in facts]

    @router.post(
        "/extraction/run",
        response_model=ExtractionRunResponse,
        responses={400: {"model": ErrorResponse}},
    )
    async def run_extraction(request: ExtractionRunRequest) -> ExtractionRunResponse:
        workspace_configs = (
            [item.to_domain() for item in request.workspaces]
            if request.workspaces is not None
            else None
        )
        result = await server_state.run_daily_notes_extraction(
            workspaces=workspace_configs,
            expire_memory_migration_facts=request.expire_memory_migration_facts,
        )
        return ExtractionRunResponse(
            started_at=result.started_at,
            finished_at=result.finished_at,
            expired_memory_migration_facts=result.expired_memory_migration_facts,
            total_files_seen=result.total_files_seen,
            total_files_processed=result.total_files_processed,
            total_facts_extracted=result.total_facts_extracted,
            total_facts_ingested=result.total_facts_ingested,
            total_facts_skipped=result.total_facts_skipped,
            total_facts_failed=result.total_facts_failed,
            workspaces=[
                WorkspaceExtractionStatsDTO(
                    workspace_key=item.workspace_key,
                    files_seen=item.files_seen,
                    files_processed=item.files_processed,
                    facts_extracted=item.facts_extracted,
                    facts_ingested=item.facts_ingested,
                    facts_skipped=item.facts_skipped,
                    facts_failed=item.facts_failed,
                )
                for item in result.workspaces
            ],
            errors=result.errors,
        )

    @router.post(
        "/agents/{agent_id}/correct",
        response_model=IngestResponse,
        responses={400: {"model": ErrorResponse}},
    )
    async def correct(agent_id: str, request: CorrectRequest) -> IngestResponse:
        client = server_state.require_client(agent_id)
        result = await client.correct(
            fact_id=request.fact_id,
            new_content=request.new_content.to_domain(),
            reason=request.reason,
        )
        return ingest_result_to_dto(result)

    @router.post(
        "/agents/{agent_id}/verify",
        response_model=VerificationResultDTO,
        responses={400: {"model": ErrorResponse}},
    )
    async def verify(agent_id: str, request: VerifyRequest) -> VerificationResultDTO:
        client = server_state.require_client(agent_id)
        result = await client.verify(
            fact_id=request.fact_id,
            method=request.method,
            status=request.status,
            reason=request.reason,
        )
        return verification_to_dto(result)

    @router.post(
        "/agents/{agent_id}/corroborate",
        response_model=CorroborationResultDTO,
        responses={400: {"model": ErrorResponse}},
    )
    async def corroborate(agent_id: str, request: CorroborateRequest) -> CorroborationResultDTO:
        client = server_state.require_client(agent_id)
        result = await client.corroborate(
            fact_id=request.fact_id,
            corroborating_fact_id=request.corroborating_fact_id,
            reason=request.reason,
        )
        return corroboration_to_dto(result)

    @router.post(
        "/agents/{agent_id}/feedback",
        response_model=FeedbackResultDTO,
        responses={400: {"model": ErrorResponse}},
    )
    async def feedback(agent_id: str, request: FeedbackRequest) -> FeedbackResultDTO:
        client = server_state.require_client(agent_id)
        result = await client.feedback(
            fact_id=request.fact_id,
            signal=request.signal,
            reason=request.reason,
        )
        return feedback_to_dto(result)

    @router.post(
        "/agents/{agent_id}/resolve-conflicts",
        response_model=list[ConflictResolutionResultDTO],
    )
    async def resolve_conflicts(
        agent_id: str,
        request: ResolveConflictsRequest,
    ) -> list[ConflictResolutionResultDTO]:
        client = server_state.require_client(agent_id)
        results = await client.resolve_conflicts(limit=request.limit)
        return [conflict_resolution_to_dto(item) for item in results]

    @router.post(
        "/agents/{agent_id}/resolve-conflict/{conflict_id}",
        response_model=ConflictResolutionResultDTO,
        responses={404: {"model": ErrorResponse}},
    )
    async def resolve_conflict(agent_id: str, conflict_id: UUID) -> ConflictResolutionResultDTO:
        client = server_state.require_client(agent_id)
        assert server_state.conflict_repo is not None
        conflict = await server_state.conflict_repo.get_by_id(conflict_id)
        if conflict is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conflict not found")
        result = await client.resolve_conflict(conflict)
        return conflict_resolution_to_dto(result)

    @router.get(
        "/agents/{agent_id}/provenance/{fact_id}",
        response_model=list[dict[str, object]],
    )
    async def trace_provenance(
        agent_id: str,
        fact_id: UUID,
        max_depth: int = 16,
    ) -> list[dict[str, object]]:
        client = server_state.require_client(agent_id)
        chain = await client.trace_provenance(fact_id=fact_id, max_depth=max_depth)
        entries = [provenance_to_dto(item) for item in chain]
        return [entry.model_dump(mode="json") for entry in entries]

    @router.post("/agents/{agent_id}/context", status_code=204)
    async def update_context(agent_id: str, request: UpdateContextRequest) -> None:
        client = server_state.require_client(agent_id)
        await client.update_context(request.context.to_domain())

    @router.put(
        "/agents/{agent_id}/subscriptions",
        response_model=list[dict[str, object]],
    )
    async def update_subscriptions(
        agent_id: str,
        request: UpdateSubscriptionsRequest,
    ) -> list[dict[str, object]]:
        subs = await server_state.sync_subscriptions(agent_id, _to_subscription_configs(request))
        return [subscription_to_detail(item) for item in subs]

    @router.get(
        "/agents/{agent_id}/subscriptions",
        response_model=list[dict[str, object]],
    )
    async def get_subscriptions(agent_id: str) -> list[dict[str, object]]:
        assert server_state.subscription_repo is not None
        subs = await server_state.subscription_repo.get_for_agent(agent_id)
        return [subscription_to_detail(item) for item in subs]

    @router.post("/agents/{agent_id}/replay", response_model=list[EventDTO])
    async def replay(agent_id: str, request: ReplayRequest) -> list[EventDTO]:
        client = server_state.require_client(agent_id)
        events = await client.replay(deliver=request.deliver)
        return [event_to_dto(event) for event in events]

    @router.post("/agents/{agent_id}/ack", status_code=204)
    async def ack_event(agent_id: str, request: AckEventRequest) -> None:
        del agent_id
        assert server_state.event_log is not None
        await server_state.event_log.mark_delivered(request.event_id)

    @router.get(
        "/agents/{agent_id}/conflicts/{fact_id}",
        response_model=list[ConflictDTO],
    )
    async def get_conflicts(agent_id: str, fact_id: UUID) -> list[ConflictDTO]:
        del agent_id
        assert server_state.conflict_repo is not None
        conflicts = await server_state.conflict_repo.find_for_fact(fact_id)
        return [conflict_to_dto(item) for item in conflicts]

    app.include_router(router)

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.get("/ready", response_model=HealthResponse)
    async def ready() -> HealthResponse:
        if server_state.pool is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Pool not ready",
            )
        if server_state.sweeper is None or not server_state.sweeper.running:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Sweeper not running",
            )
        if server_state.decay_runner is None or not server_state.decay_runner.running:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Decay runner not running",
            )
        verification_runner = getattr(server_state, "verification_runner", None)
        if verification_runner is None or not verification_runner.running:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Verification runner not running",
            )
        adaptive_runner = getattr(server_state, "adaptive_learning_runner", None)
        if adaptive_runner is None or not adaptive_runner.running:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Adaptive learning runner not running",
            )
        return HealthResponse(status="ready")

    @app.get("/version", response_model=VersionResponse)
    async def get_version() -> VersionResponse:
        try:
            pkg_version = version("mycelium")
        except PackageNotFoundError:
            pkg_version = "0.1.0"
        return VersionResponse(service="mycelium-server", version=pkg_version)

    @app.get("/metrics", response_class=PlainTextResponse)
    async def metrics() -> PlainTextResponse:
        metrics_snapshot = getattr(server_state, "metrics_snapshot", None)
        if callable(metrics_snapshot):
            values = await metrics_snapshot()
        else:
            values = {
                "mycelium_active_facts": 0,
                "mycelium_agents": 0,
                "mycelium_unresolved_conflicts": 0,
                "mycelium_query_total_1h": 0,
                "mycelium_query_errors_1h": 0,
                "mycelium_query_latency_avg_ms_1h": 0.0,
                "mycelium_query_latency_p95_ms_1h": 0.0,
            }
        lines = [
            "# HELP mycelium_active_facts Active non-expired facts.",
            "# TYPE mycelium_active_facts gauge",
            f"mycelium_active_facts {values['mycelium_active_facts']}",
            "# HELP mycelium_agents Registered agents.",
            "# TYPE mycelium_agents gauge",
            f"mycelium_agents {values['mycelium_agents']}",
            "# HELP mycelium_unresolved_conflicts Unresolved conflicts.",
            "# TYPE mycelium_unresolved_conflicts gauge",
            f"mycelium_unresolved_conflicts {values['mycelium_unresolved_conflicts']}",
            "# HELP mycelium_query_total_1h Queries logged in the last hour.",
            "# TYPE mycelium_query_total_1h gauge",
            f"mycelium_query_total_1h {values['mycelium_query_total_1h']}",
            "# HELP mycelium_query_errors_1h Query errors in the last hour.",
            "# TYPE mycelium_query_errors_1h gauge",
            f"mycelium_query_errors_1h {values['mycelium_query_errors_1h']}",
            "# HELP mycelium_query_latency_avg_ms_1h Average query latency in ms (1h).",
            "# TYPE mycelium_query_latency_avg_ms_1h gauge",
            f"mycelium_query_latency_avg_ms_1h {values['mycelium_query_latency_avg_ms_1h']}",
            "# HELP mycelium_query_latency_p95_ms_1h P95 query latency in ms (1h).",
            "# TYPE mycelium_query_latency_p95_ms_1h gauge",
            f"mycelium_query_latency_p95_ms_1h {values['mycelium_query_latency_p95_ms_1h']}",
        ]
        return PlainTextResponse("\n".join(lines) + "\n")

    @app.exception_handler(ValueError)
    async def value_error_handler(_, exc: ValueError):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc)},
        )

    @app.exception_handler(RuntimeError)
    async def runtime_error_handler(_, exc: RuntimeError):
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": str(exc)},
        )

    return app
