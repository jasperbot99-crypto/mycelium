"""FastAPI server mode for Mycelium."""

from __future__ import annotations

from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from uuid import UUID  # noqa: TC003

from fastapi import APIRouter, Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from mycelium.config import MyceliumConfig, SubscriptionConfig
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
    HealthResponse,
    IngestRequest,
    IngestResponse,
    QueryRequest,
    QueryResultDTO,
    ReplayRequest,
    ResolveConflictsRequest,
    UpdateContextRequest,
    UpdateSubscriptionsRequest,
    VerificationResultDTO,
    VerifyRequest,
    VersionResponse,
    conflict_resolution_to_dto,
    conflict_to_dto,
    corroboration_to_dto,
    event_to_dto,
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
        return ConnectResponse(agent_id=client.agent_id, connected=client.connected)

    @router.post("/agents/{agent_id}/disconnect", response_model=ConnectResponse)
    async def disconnect_agent(agent_id: str) -> ConnectResponse:
        await server_state.disconnect_agent(agent_id)
        return ConnectResponse(agent_id=agent_id, connected=False)

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

    @router.post("/agents/{agent_id}/query", response_model=list[QueryResultDTO])
    async def query(agent_id: str, request: QueryRequest) -> list[QueryResultDTO]:
        client = server_state.require_client(agent_id)
        results = await client.query(
            question=request.question,
            filters=request.filters.to_domain() if request.filters else None,
            limit=request.limit,
        )
        return [query_result_to_dto(item) for item in results]

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
        return HealthResponse(status="ready")

    @app.get("/version", response_model=VersionResponse)
    async def get_version() -> VersionResponse:
        try:
            pkg_version = version("mycelium")
        except PackageNotFoundError:
            pkg_version = "0.1.0"
        return VersionResponse(service="mycelium-server", version=pkg_version)

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
