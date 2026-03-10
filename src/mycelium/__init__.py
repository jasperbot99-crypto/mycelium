"""Mycelium — Multi-agent memory and coordination system."""

from typing import Any

from mycelium.client.client import MyceliumClient
from mycelium.domain.types import (
    ActiveContext,
    Conflict,
    ConflictResolutionResult,
    ConflictStatus,
    CorroborationResult,
    Fact,
    FactContent,
    Priority,
    PropagationEvent,
    RejectionReason,
    RelationType,
    SourceType,
    Subscription,
    Urgency,
    VerificationMethod,
    VerificationResult,
    VerificationStatus,
)


def create_app(*args: Any, **kwargs: Any):
    """Lazy server import to avoid hard dependency in library-only usage."""
    from mycelium.server.app import create_app as _create_app

    return _create_app(*args, **kwargs)

__all__ = [
    "MyceliumClient",
    "create_app",
    "ActiveContext",
    "Conflict",
    "ConflictStatus",
    "Fact",
    "FactContent",
    "Priority",
    "PropagationEvent",
    "RejectionReason",
    "RelationType",
    "CorroborationResult",
    "ConflictResolutionResult",
    "SourceType",
    "Subscription",
    "Urgency",
    "VerificationMethod",
    "VerificationResult",
    "VerificationStatus",
]
