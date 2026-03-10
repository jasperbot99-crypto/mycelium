"""Transport layer interface — ARCHITECTURE.md Section 7.1."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from mycelium.domain.types import PropagationEvent


@runtime_checkable
class Transport(Protocol):
    """Abstraction over event delivery mechanism.

    Implementations must follow the error handling contract (ARCHITECTURE.md Section 7.2):
    - A failing callback is logged and the event is left as undelivered
    - A slow callback is timed out and treated as undelivered
    - One agent's failure never blocks propagation to other agents
    - Undelivered events are picked up on the agent's next replay() call
    """

    async def publish(self, agent_id: str, event: PropagationEvent) -> None:
        """Push an event to a specific agent."""
        ...

    async def subscribe(
        self,
        agent_id: str,
        callback: Callable[[PropagationEvent], Awaitable[None]],
    ) -> None:
        """Register a callback for an agent to receive events."""
        ...

    async def unsubscribe(self, agent_id: str) -> None:
        """Remove an agent's event callback."""
        ...
