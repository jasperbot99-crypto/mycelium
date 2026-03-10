"""In-process transport — ARCHITECTURE.md Section 7.2.

Simple async callback registry for single-process library mode.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from mycelium.domain.types import PropagationEvent

logger = logging.getLogger(__name__)


class InProcessTransport:
    """Direct callback invocation within the same process.

    Error handling contract (ARCHITECTURE.md Section 7.2):
    - Failing callbacks are logged, event stays undelivered
    - Slow callbacks are timed out (default: 5s)
    - One agent's failure never blocks propagation to others
    """

    def __init__(self, callback_timeout: float = 5.0) -> None:
        self._listeners: dict[str, Callable[[PropagationEvent], Awaitable[None]]] = {}
        self._timeout = callback_timeout

    async def publish(self, agent_id: str, event: PropagationEvent) -> None:
        """Push an event to a specific agent's callback."""
        if agent_id not in self._listeners:
            return

        try:
            await asyncio.wait_for(
                self._listeners[agent_id](event),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "propagation_timeout: agent=%s event=%s timeout=%.1fs",
                agent_id,
                event.id,
                self._timeout,
            )
        except Exception:
            logger.exception(
                "propagation_error: agent=%s event=%s",
                agent_id,
                event.id,
            )

    async def subscribe(
        self,
        agent_id: str,
        callback: Callable[[PropagationEvent], Awaitable[None]],
    ) -> None:
        """Register a callback for an agent."""
        self._listeners[agent_id] = callback

    async def unsubscribe(self, agent_id: str) -> None:
        """Remove an agent's callback."""
        self._listeners.pop(agent_id, None)

    def has_listener(self, agent_id: str) -> bool:
        """Return whether an agent currently has a subscribed callback."""
        return agent_id in self._listeners
