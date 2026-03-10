"""Declarative YAML scenario runner — SPEC D6.

Runs scenario files against the real Mycelium stack (in-memory storage).
No LLMs in the test loop. Deterministic, fast, CI-friendly.

Scenario format:
    scenario: "name"
    agents:
      - id: agent-a
        role: code
        subscriptions:
          - topic: "api.*"
    steps:
      - action: ingest
        agent: agent-a
        fact:
          subject: "api-orders"
          predicate: "has_status"
          object: "healthy"
        tags: [api]
      - action: assert_query
        agent: agent-a
        query: "api-orders has_status"
        expect:
          min_results: 1
      - action: assert_conflict
        expect:
          count: 1
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

if TYPE_CHECKING:
    from pathlib import Path

import yaml

from mycelium.client.client import MyceliumClient
from mycelium.config import MyceliumConfig, SubscriptionConfig
from mycelium.domain.types import (
    ConflictStatus,
    FactContent,
    PropagationEvent,
    SourceType,
    VerificationMethod,
    VerificationStatus,
)
from mycelium.embeddings.mock import MockEmbeddingProvider
from mycelium.pipelines.sweeper import ContradictionSweeper
from mycelium.storage.memory import (
    InMemoryAgentRepository,
    InMemoryConflictRepository,
    InMemoryEventLog,
    InMemoryFactRepository,
    InMemoryRelationRepository,
    InMemorySubscriptionRepository,
)
from mycelium.transport.in_process import InProcessTransport


def _step_results() -> list[StepResult]:
    return []


@dataclass
class StepResult:
    """Result of executing one scenario step."""

    step_index: int
    action: str
    passed: bool
    message: str = ""


@dataclass
class ScenarioResult:
    """Result of running a complete scenario."""

    name: str
    passed: bool
    steps: list[StepResult] = field(default_factory=_step_results)
    error: str | None = None


class ScenarioRunner:
    """Runs YAML scenario files against in-memory Mycelium stack."""

    def __init__(self) -> None:
        self._embedding = MockEmbeddingProvider(dimension=64)
        self._fact_repo = InMemoryFactRepository()
        self._agent_repo = InMemoryAgentRepository()
        self._conflict_repo = InMemoryConflictRepository()
        self._relation_repo = InMemoryRelationRepository()
        self._sub_repo = InMemorySubscriptionRepository()
        self._event_log = InMemoryEventLog()
        self._transport = InProcessTransport()

        self._config = MyceliumConfig()
        self._clients: dict[str, MyceliumClient] = {}
        self._received_events: dict[str, list[PropagationEvent]] = {}
        self._fact_refs: dict[str, UUID] = {}

        self._sweeper = ContradictionSweeper(
            fact_repo=self._fact_repo,
            conflict_repo=self._conflict_repo,
            relation_repo=self._relation_repo,
            contradiction_threshold=self._config.contradiction_threshold,
            corroboration_threshold=self._config.corroboration_threshold,
        )

    async def run_file(self, path: Path) -> ScenarioResult:
        """Load and run a scenario from a YAML file."""
        with open(path) as f:
            data = yaml.safe_load(f)
        return await self.run(data)

    async def run(self, scenario: dict[str, Any]) -> ScenarioResult:
        """Run a parsed scenario dict."""
        name = scenario.get("scenario", "unnamed")
        result = ScenarioResult(name=name, passed=True)

        try:
            # Register agents
            raw_agents_obj: object = scenario.get("agents", [])
            if not isinstance(raw_agents_obj, list):
                raise ValueError("scenario.agents must be a list")
            agent_defs: list[dict[str, Any]] = []
            for raw_agent in cast("list[object]", raw_agents_obj):
                if isinstance(raw_agent, dict):
                    agent_defs.append(cast("dict[str, Any]", raw_agent))
                else:
                    raise ValueError("scenario.agents entries must be objects")

            for agent_def in agent_defs:
                await self._register_agent(agent_def)

            # Execute steps
            raw_steps_obj: object = scenario.get("steps", [])
            if not isinstance(raw_steps_obj, list):
                raise ValueError("scenario.steps must be a list")
            steps: list[dict[str, Any]] = []
            for raw_step in cast("list[object]", raw_steps_obj):
                if isinstance(raw_step, dict):
                    steps.append(cast("dict[str, Any]", raw_step))
                else:
                    raise ValueError("scenario.steps entries must be objects")

            for i, step in enumerate(steps):
                step_result = await self._execute_step(i, step)
                result.steps.append(step_result)
                if not step_result.passed:
                    result.passed = False

        except Exception as e:
            result.passed = False
            result.error = f"Scenario failed with exception: {e}"

        finally:
            # Cleanup
            for client in self._clients.values():
                with contextlib.suppress(Exception):
                    await client.disconnect()

        return result

    async def _register_agent(self, agent_def: dict[str, Any]) -> None:
        """Register an agent from a scenario agent definition."""
        agent_id = agent_def["id"]
        role = agent_def.get("role", "test")
        subscriptions = [
            SubscriptionConfig(
                topic=s.get("topic", "*"),
                priority=s.get("priority", "normal"),
            )
            for s in agent_def.get("subscriptions", [])
        ]

        config = MyceliumConfig(subscriptions=subscriptions)

        client = MyceliumClient(
            agent_id=agent_id,
            role=role,
            config=config,
            fact_repo=self._fact_repo,
            agent_repo=self._agent_repo,
            conflict_repo=self._conflict_repo,
            relation_repo=self._relation_repo,
            subscription_repo=self._sub_repo,
            event_log=self._event_log,
            embedding_provider=self._embedding,
            transport=self._transport,
        )

        # Track propagation events received by this agent
        self._received_events[agent_id] = []
        received = self._received_events[agent_id]

        async def on_fact(event: PropagationEvent, _r: list[PropagationEvent] = received) -> None:
            _r.append(event)

        client.on_fact(on_fact)

        await client.connect()
        self._clients[agent_id] = client

    async def _execute_step(self, index: int, step: dict[str, Any]) -> StepResult:
        """Execute a single scenario step."""
        action = step.get("action", "")
        expected_error = step.get("expect_error")

        handlers: dict[str, Any] = {
            "ingest": self._step_ingest,
            "assert_query": self._step_assert_query,
            "assert_conflict": self._step_assert_conflict,
            "assert_propagation": self._step_assert_propagation,
            "correct": self._step_correct,
            "sweep": self._step_sweep,
            "verify": self._step_verify,
            "corroborate": self._step_corroborate,
            "assert_fact": self._step_assert_fact,
            "assert_agent": self._step_assert_agent,
        }

        handler = handlers.get(action)
        if handler is None:
            return StepResult(
                step_index=index, action=action, passed=False,
                message=f"Unknown action: {action}",
            )

        try:
            result = await handler(index, step)
            return self._apply_expected_error(index, action, result, expected_error)
        except Exception as e:
            result = StepResult(
                step_index=index, action=action, passed=False,
                message=f"Step raised exception: {e}",
            )
            return self._apply_expected_error(index, action, result, expected_error)

    def _apply_expected_error(
        self,
        index: int,
        action: str,
        result: StepResult,
        expected_error: object,
    ) -> StepResult:
        """Convert an expected failure into a passing step."""
        if expected_error is None:
            return result

        if result.passed:
            return StepResult(
                step_index=index,
                action=action,
                passed=False,
                message="Step was expected to fail but succeeded",
            )

        if isinstance(expected_error, str) and expected_error not in result.message:
            return StepResult(
                step_index=index,
                action=action,
                passed=False,
                message=(
                    f"Expected error containing '{expected_error}', got: "
                    f"{result.message}"
                ),
            )

        return StepResult(
            step_index=index,
            action=action,
            passed=True,
            message=f"Expected failure observed: {result.message}",
        )

    async def _step_ingest(self, index: int, step: dict[str, Any]) -> StepResult:
        """Execute an ingest step."""
        agent_id = step["agent"]
        client = self._clients.get(agent_id)
        if client is None:
            return StepResult(
                step_index=index, action="ingest", passed=False,
                message=f"Agent '{agent_id}' not registered",
            )

        fact_def = step["fact"]
        source_type_str = step.get("source_type", "agent_extraction")
        source_type = SourceType(source_type_str)
        tags = step.get("tags", [])

        content = FactContent(
            subject=fact_def["subject"],
            predicate=fact_def["predicate"],
            object=fact_def["object"],
            context=fact_def.get("context"),
        )

        result = await client.ingest(
            content=content,
            source_type=source_type,
            tags=tags,
        )

        save_as = step.get("save_as")
        if result.accepted and result.fact is not None and isinstance(save_as, str):
            self._fact_refs[save_as] = result.fact.id

        if result.accepted:
            return StepResult(
                step_index=index, action="ingest", passed=True,
                message=f"Ingested fact {result.fact.id}" if result.fact else "Ingested",
            )
        else:
            return StepResult(
                step_index=index, action="ingest", passed=False,
                message=f"Ingest rejected: {result.rejection}",
            )

    async def _step_assert_query(self, index: int, step: dict[str, Any]) -> StepResult:
        """Execute a query assertion step."""
        agent_id = step["agent"]
        client = self._clients.get(agent_id)
        if client is None:
            return StepResult(
                step_index=index, action="assert_query", passed=False,
                message=f"Agent '{agent_id}' not registered",
            )

        query = step["query"]
        expect = step.get("expect", {})
        min_results = expect.get("min_results", 1)
        max_results = expect.get("max_results")

        results = await client.query(query)

        if len(results) < min_results:
            return StepResult(
                step_index=index, action="assert_query", passed=False,
                message=f"Expected >= {min_results} results, got {len(results)}",
            )

        if max_results is not None and len(results) > max_results:
            return StepResult(
                step_index=index, action="assert_query", passed=False,
                message=f"Expected <= {max_results} results, got {len(results)}",
            )

        # Check for specific subject match if requested
        expect_subject = expect.get("subject")
        if expect_subject:
            subjects = [r.fact.content.subject for r in results]
            if expect_subject not in subjects:
                return StepResult(
                    step_index=index, action="assert_query", passed=False,
                    message=f"Expected subject '{expect_subject}' in results, got {subjects}",
                )

        return StepResult(
            step_index=index, action="assert_query", passed=True,
            message=f"Query returned {len(results)} results",
        )

    async def _step_assert_conflict(self, index: int, step: dict[str, Any]) -> StepResult:
        """Assert conflict state."""
        expect = step.get("expect", {})
        expected_count = expect.get("count")
        expected_status = expect.get("status", "detected")

        status = ConflictStatus(expected_status)
        conflicts = await self._conflict_repo.find_by_status(status)

        if expected_count is not None and len(conflicts) != expected_count:
            return StepResult(
                step_index=index, action="assert_conflict", passed=False,
                message=(
                    f"Expected {expected_count} conflicts with status "
                    f"'{expected_status}', got {len(conflicts)}"
                ),
            )

        return StepResult(
            step_index=index, action="assert_conflict", passed=True,
            message=f"Found {len(conflicts)} conflicts with status '{expected_status}'",
        )

    async def _step_assert_propagation(self, index: int, step: dict[str, Any]) -> StepResult:
        """Assert propagation events received by an agent."""
        agent_id = step["agent"]
        expect = step.get("expect", {})
        min_events = expect.get("min_events", 0)
        max_events = expect.get("max_events")
        exact_events = expect.get("count")

        received = self._received_events.get(agent_id, [])
        count = len(received)

        if exact_events is not None and count != exact_events:
            return StepResult(
                step_index=index, action="assert_propagation", passed=False,
                message=f"Agent '{agent_id}': expected {exact_events} events, got {count}",
            )

        if count < min_events:
            return StepResult(
                step_index=index, action="assert_propagation", passed=False,
                message=f"Agent '{agent_id}': expected >= {min_events} events, got {count}",
            )

        if max_events is not None and count > max_events:
            return StepResult(
                step_index=index, action="assert_propagation", passed=False,
                message=f"Agent '{agent_id}': expected <= {max_events} events, got {count}",
            )

        return StepResult(
            step_index=index, action="assert_propagation", passed=True,
            message=f"Agent '{agent_id}': received {count} propagation events",
        )

    async def _step_correct(self, index: int, step: dict[str, Any]) -> StepResult:
        """Execute a correction step."""
        agent_id = step["agent"]
        client = self._clients.get(agent_id)
        if client is None:
            return StepResult(
                step_index=index, action="correct", passed=False,
                message=f"Agent '{agent_id}' not registered",
            )

        from uuid import UUID

        old_fact_id = step["old_fact_id"]
        new_fact_def = step["new_fact"]
        reason = step.get("reason", "correction")

        new_content = FactContent(
            subject=new_fact_def["subject"],
            predicate=new_fact_def["predicate"],
            object=new_fact_def["object"],
            context=new_fact_def.get("context"),
        )

        result = await client.correct(
            fact_id=UUID(old_fact_id),
            new_content=new_content,
            reason=reason,
        )

        if result.accepted:
            return StepResult(
                step_index=index, action="correct", passed=True,
                message="Correction accepted",
            )
        else:
            return StepResult(
                step_index=index, action="correct", passed=False,
                message=f"Correction rejected: {result.rejection}",
            )

    async def _step_sweep(self, index: int, step: dict[str, Any]) -> StepResult:
        """Run a contradiction sweep."""
        result = await self._sweeper.sweep()
        return StepResult(
            step_index=index, action="sweep", passed=True,
            message=(
                f"Sweep: scanned={result.facts_scanned}, "
                f"contradictions={result.contradictions_found}, "
                f"corroborations={result.corroborations_found}"
            ),
        )

    def _resolve_fact_id(self, step: dict[str, Any]) -> UUID:
        raw_fact_id = step.get("fact_id")
        raw_fact_ref = step.get("fact_ref")

        if isinstance(raw_fact_id, str):
            return UUID(raw_fact_id)
        if isinstance(raw_fact_ref, str):
            resolved = self._fact_refs.get(raw_fact_ref)
            if resolved is None:
                raise ValueError(f"unknown fact_ref: {raw_fact_ref}")
            return resolved

        raise ValueError("step requires either fact_id or fact_ref")

    async def _step_verify(self, index: int, step: dict[str, Any]) -> StepResult:
        """Execute a verification step."""
        agent_id = step["agent"]
        client = self._clients.get(agent_id)
        if client is None:
            return StepResult(
                step_index=index,
                action="verify",
                passed=False,
                message=f"Agent '{agent_id}' not registered",
            )

        fact_id = self._resolve_fact_id(step)
        status = VerificationStatus(step["status"])
        method = VerificationMethod(step.get("method", VerificationMethod.MANUAL_REVIEW.value))
        reason = step.get("reason")
        if reason is not None and not isinstance(reason, str):
            reason = str(reason)

        result = await client.verify(
            fact_id=fact_id,
            method=method,
            status=status,
            reason=reason,
        )
        return StepResult(
            step_index=index,
            action="verify",
            passed=True,
            message=f"Verified {result.fact_id} with status {result.status.value}",
        )

    async def _step_corroborate(self, index: int, step: dict[str, Any]) -> StepResult:
        """Execute a corroboration step."""
        agent_id = step["agent"]
        client = self._clients.get(agent_id)
        if client is None:
            return StepResult(
                step_index=index,
                action="corroborate",
                passed=False,
                message=f"Agent '{agent_id}' not registered",
            )

        fact_id = self._resolve_fact_id(step)
        corroborating_raw = step.get("corroborating_fact_id")
        corroborating_ref = step.get("corroborating_fact_ref")
        corroborating_fact_id: UUID
        if isinstance(corroborating_raw, str):
            corroborating_fact_id = UUID(corroborating_raw)
        elif isinstance(corroborating_ref, str):
            resolved = self._fact_refs.get(corroborating_ref)
            if resolved is None:
                return StepResult(
                    step_index=index,
                    action="corroborate",
                    passed=False,
                    message=f"Unknown corroborating_fact_ref '{corroborating_ref}'",
                )
            corroborating_fact_id = resolved
        else:
            return StepResult(
                step_index=index,
                action="corroborate",
                passed=False,
                message="Missing corroborating_fact_id or corroborating_fact_ref",
            )

        reason = step.get("reason")
        if reason is not None and not isinstance(reason, str):
            reason = str(reason)

        result = await client.corroborate(
            fact_id=fact_id,
            corroborating_fact_id=corroborating_fact_id,
            reason=reason,
        )
        return StepResult(
            step_index=index,
            action="corroborate",
            passed=True,
            message=(
                f"Corroborated {result.fact_id} "
                f"(count={result.corroboration_count}, created={result.relation_created})"
            ),
        )

    async def _step_assert_fact(self, index: int, step: dict[str, Any]) -> StepResult:
        """Assert fact state by id/ref."""
        fact_id = self._resolve_fact_id(step)
        expect = step.get("expect", {})
        if not isinstance(expect, dict):
            return StepResult(
                step_index=index,
                action="assert_fact",
                passed=False,
                message="expect must be an object",
            )
        expect_map = cast("dict[str, object]", expect)

        fact = await self._fact_repo.get_by_id(fact_id)
        if fact is None:
            return StepResult(
                step_index=index,
                action="assert_fact",
                passed=False,
                message=f"Fact '{fact_id}' not found",
            )

        expected_status = expect_map.get("verification_status")
        if isinstance(expected_status, str):
            status = VerificationStatus(expected_status)
            if fact.verification_status != status:
                return StepResult(
                    step_index=index,
                    action="assert_fact",
                    passed=False,
                    message=(
                        f"Expected verification_status={status.value}, "
                        f"got {fact.verification_status.value}"
                    ),
                )

        min_corroboration_count = expect_map.get("min_corroboration_count")
        if (
            isinstance(min_corroboration_count, int)
            and fact.corroboration_count < min_corroboration_count
        ):
            return StepResult(
                step_index=index,
                action="assert_fact",
                passed=False,
                message=(
                    f"Expected corroboration_count >= {min_corroboration_count}, "
                    f"got {fact.corroboration_count}"
                ),
            )

        min_confidence = expect_map.get("min_confidence")
        if isinstance(min_confidence, (int, float)) and fact.confidence < float(min_confidence):
            return StepResult(
                step_index=index,
                action="assert_fact",
                passed=False,
                message=f"Expected confidence >= {min_confidence}, got {fact.confidence}",
            )

        max_confidence = expect_map.get("max_confidence")
        if isinstance(max_confidence, (int, float)) and fact.confidence > float(max_confidence):
            return StepResult(
                step_index=index,
                action="assert_fact",
                passed=False,
                message=f"Expected confidence <= {max_confidence}, got {fact.confidence}",
            )

        return StepResult(
            step_index=index,
            action="assert_fact",
            passed=True,
            message=f"Fact '{fact_id}' matched expectations",
        )

    async def _step_assert_agent(self, index: int, step: dict[str, Any]) -> StepResult:
        """Assert agent state."""
        agent_id = step["agent"]
        expect = step.get("expect", {})
        if not isinstance(expect, dict):
            return StepResult(
                step_index=index,
                action="assert_agent",
                passed=False,
                message="expect must be an object",
            )
        expect_map = cast("dict[str, object]", expect)

        agent = await self._agent_repo.get_by_id(agent_id)
        if agent is None:
            return StepResult(
                step_index=index,
                action="assert_agent",
                passed=False,
                message=f"Agent '{agent_id}' not found",
            )

        min_trust = expect_map.get("min_trust_score")
        if isinstance(min_trust, (int, float)) and agent.trust_score < float(min_trust):
            return StepResult(
                step_index=index,
                action="assert_agent",
                passed=False,
                message=f"Expected trust_score >= {min_trust}, got {agent.trust_score}",
            )

        max_trust = expect_map.get("max_trust_score")
        if isinstance(max_trust, (int, float)) and agent.trust_score > float(max_trust):
            return StepResult(
                step_index=index,
                action="assert_agent",
                passed=False,
                message=f"Expected trust_score <= {max_trust}, got {agent.trust_score}",
            )

        return StepResult(
            step_index=index,
            action="assert_agent",
            passed=True,
            message=f"Agent '{agent_id}' matched expectations",
        )
