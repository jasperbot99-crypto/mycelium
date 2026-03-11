"""Unit tests for domain types."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from mycelium.domain.types import (
    ActiveContext,
    Fact,
    FactContent,
    Priority,
    RejectionReason,
    SourceType,
    Subscription,
    Urgency,
    VerificationStatus,
)


class TestFactContent:
    def test_creation(self) -> None:
        fc = FactContent(subject="API /v2/orders", predicate="deprecated", object="true")
        assert fc.subject == "API /v2/orders"
        assert fc.predicate == "deprecated"
        assert fc.object == "true"
        assert fc.context is None

    def test_with_context(self) -> None:
        fc = FactContent(
            subject="API /v2/orders",
            predicate="deprecated",
            object="true",
            context="production",
        )
        assert fc.context == "production"

    def test_immutable(self) -> None:
        fc = FactContent(subject="X", predicate="Y", object="Z")
        with pytest.raises(AttributeError):
            fc.subject = "changed"  # type: ignore[misc]

    def test_equality(self) -> None:
        a = FactContent(subject="X", predicate="Y", object="Z")
        b = FactContent(subject="X", predicate="Y", object="Z")
        assert a == b

    def test_empty_subject_rejected(self) -> None:
        with pytest.raises(ValueError, match="subject must not be empty"):
            FactContent(subject="", predicate="Y", object="Z")

    def test_empty_predicate_rejected(self) -> None:
        with pytest.raises(ValueError, match="predicate must not be empty"):
            FactContent(subject="X", predicate="  ", object="Z")

    def test_empty_object_rejected(self) -> None:
        with pytest.raises(ValueError, match="object must not be empty"):
            FactContent(subject="X", predicate="Y", object="")

    def test_to_embedding_text(self) -> None:
        fc = FactContent(subject="API /v2/orders", predicate="deprecated", object="true")
        assert fc.to_embedding_text() == "API /v2/orders deprecated true"

    def test_to_embedding_text_with_context(self) -> None:
        fc = FactContent(
            subject="API /v2/orders",
            predicate="deprecated",
            object="true",
            context="production",
        )
        assert fc.to_embedding_text() == "API /v2/orders deprecated true production"


class TestSourceType:
    def test_trust_weights_ordered(self) -> None:
        assert (
            SourceType.HUMAN_CORRECTION.trust_weight
            > SourceType.SYSTEM_VERIFICATION.trust_weight
        )
        assert (
            SourceType.SYSTEM_VERIFICATION.trust_weight
            > SourceType.AGENT_EXTRACTION.trust_weight
        )
        assert SourceType.AGENT_EXTRACTION.trust_weight > SourceType.AGENT_INFERENCE.trust_weight

    def test_human_correction_is_max(self) -> None:
        assert SourceType.HUMAN_CORRECTION.trust_weight == 1.0

    def test_all_positive(self) -> None:
        for st in SourceType:
            assert st.trust_weight > 0.0


class TestFact:
    def _make_fact(self, **kwargs: object) -> Fact:
        defaults: dict[str, object] = {
            "id": uuid4(),
            "content": FactContent(subject="X", predicate="Y", object="Z"),
            "source_agent_id": "test-agent",
            "source_type": SourceType.AGENT_EXTRACTION,
            "confidence": 0.6,
            "trust_score": 0.5,
            "valid_from": datetime.now(UTC),
        }
        defaults.update(kwargs)
        return Fact(**defaults)  # type: ignore[arg-type]

    def test_is_active_default(self) -> None:
        fact = self._make_fact()
        assert fact.is_active is True

    def test_is_active_expired(self) -> None:
        fact = self._make_fact(expired_at=datetime.now(UTC))
        assert fact.is_active is False

    def test_is_active_valid_until_set(self) -> None:
        fact = self._make_fact(valid_until=datetime.now(UTC))
        assert fact.is_active is False

    def test_is_conflicted(self) -> None:
        fact = self._make_fact(conflict_status="unresolved")
        assert fact.is_conflicted is True

    def test_is_not_conflicted(self) -> None:
        fact = self._make_fact(conflict_status="none")
        assert fact.is_conflicted is False

    def test_default_values(self) -> None:
        fact = self._make_fact()
        assert fact.corroboration_count == 0
        assert fact.access_count == 0
        assert fact.tags == []
        assert fact.metadata == {}
        assert fact.verification_status == VerificationStatus.UNVERIFIED
        assert fact.conflict_status == "none"


class TestActiveContext:
    def test_defaults(self) -> None:
        ctx = ActiveContext()
        assert ctx.task is None
        assert ctx.entities == ()
        assert ctx.urgency == Urgency.NORMAL

    def test_immutable(self) -> None:
        ctx = ActiveContext(task="trading")
        with pytest.raises(AttributeError):
            ctx.task = "changed"  # type: ignore[misc]


class TestSubscription:
    def _make_sub(self, topic: str = "api", **kwargs: object) -> Subscription:
        defaults: dict[str, object] = {
            "id": uuid4(),
            "agent_id": "test-agent",
            "topic": topic,
            "priority": Priority.NORMAL,
        }
        defaults.update(kwargs)
        return Subscription(**defaults)  # type: ignore[arg-type]

    def test_matches_exact_tag(self) -> None:
        sub = self._make_sub(topic="infrastructure")
        assert sub.matches_tags(["infrastructure"]) is True
        assert sub.matches_tags(["api"]) is False

    def test_matches_wildcard(self) -> None:
        sub = self._make_sub(topic="api.*")
        assert sub.matches_tags(["api.orders"]) is True
        assert sub.matches_tags(["api.auth"]) is True
        assert sub.matches_tags(["api"]) is True
        assert sub.matches_tags(["infrastructure"]) is False

    def test_matches_star(self) -> None:
        sub = self._make_sub(topic="*")
        assert sub.matches_tags(["anything"]) is True
        assert sub.matches_tags(["api.orders"]) is True
        # '*' matches everything — including facts with no tags (wildcard = subscribe to all)
        assert sub.matches_tags([]) is True

    def test_passes_filters_confidence(self) -> None:
        sub = self._make_sub(min_confidence=0.8)
        fact_high = Fact(
            id=uuid4(),
            content=FactContent(subject="X", predicate="Y", object="Z"),
            source_agent_id="a",
            source_type=SourceType.AGENT_EXTRACTION,
            confidence=0.9,
            trust_score=0.5,
            valid_from=datetime.now(UTC),
        )
        fact_low = Fact(
            id=uuid4(),
            content=FactContent(subject="X", predicate="Y", object="Z"),
            source_agent_id="a",
            source_type=SourceType.AGENT_EXTRACTION,
            confidence=0.5,
            trust_score=0.5,
            valid_from=datetime.now(UTC),
        )
        assert sub.passes_filters(fact_high) is True
        assert sub.passes_filters(fact_low) is False

    def test_passes_filters_source_type(self) -> None:
        sub = self._make_sub(source_types=[SourceType.HUMAN_CORRECTION])
        fact_human = Fact(
            id=uuid4(),
            content=FactContent(subject="X", predicate="Y", object="Z"),
            source_agent_id="a",
            source_type=SourceType.HUMAN_CORRECTION,
            confidence=0.5,
            trust_score=0.5,
            valid_from=datetime.now(UTC),
        )
        fact_agent = Fact(
            id=uuid4(),
            content=FactContent(subject="X", predicate="Y", object="Z"),
            source_agent_id="a",
            source_type=SourceType.AGENT_INFERENCE,
            confidence=0.5,
            trust_score=0.5,
            valid_from=datetime.now(UTC),
        )
        assert sub.passes_filters(fact_human) is True
        assert sub.passes_filters(fact_agent) is False


class TestRejectionReason:
    def test_creation(self) -> None:
        r = RejectionReason(
            code="duplicate", message="Fact already exists",
            existing_fact_id=uuid4(),
        )
        assert r.code == "duplicate"
        assert r.existing_fact_id is not None
