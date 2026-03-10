"""Supabase-backed realtime transport for cross-process propagation.

Uses PostgreSQL LISTEN/NOTIFY on Supabase/Postgres to deliver events across
processes. Events remain durable via EventLog persistence in the propagation
pipeline; this transport provides best-effort low-latency push.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

import asyncpg

from mycelium.domain.types import Priority, PropagationEvent

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

_MAX_NOTIFY_PAYLOAD_BYTES = 7000


def _sanitize_agent_id(agent_id: str) -> str:
    """Convert agent id to a safe PostgreSQL channel suffix."""
    lowered = agent_id.lower().strip()
    safe_chars: list[str] = []
    for ch in lowered:
        if ch.isalnum():
            safe_chars.append(ch)
        else:
            safe_chars.append("_")
    return "".join(safe_chars)


def _to_payload(event: PropagationEvent) -> str:
    payload = {
        "id": str(event.id),
        "fact_id": str(event.fact_id),
        "target_agent_id": event.target_agent_id,
        "reason": event.reason,
        "priority": event.priority.value,
        "sequence_num": event.sequence_num,
        "created_at": event.created_at.isoformat(),
    }
    rendered = json.dumps(payload, separators=(",", ":"))
    if len(rendered.encode("utf-8")) > _MAX_NOTIFY_PAYLOAD_BYTES:
        payload["reason"] = event.reason[:512]
        rendered = json.dumps(payload, separators=(",", ":"))
    return rendered


def _from_payload(payload: str) -> PropagationEvent:
    data = cast("dict[str, Any]", json.loads(payload))
    created_at_raw = data.get("created_at")
    created_at = datetime.now()
    if isinstance(created_at_raw, str):
        try:
            created_at = datetime.fromisoformat(created_at_raw)
        except ValueError:
            created_at = datetime.now()

    return PropagationEvent(
        id=UUID(str(data["id"])),
        fact_id=UUID(str(data["fact_id"])),
        target_agent_id=str(data["target_agent_id"]),
        reason=str(data["reason"]),
        priority=Priority(str(data["priority"])),
        sequence_num=int(data.get("sequence_num", 0)),
        created_at=created_at,
    )


class SupabaseRealtimeTransport:
    """Transport that pushes events between processes using Postgres notify."""

    def __init__(
        self,
        database_url: str,
        *,
        callback_timeout: float = 5.0,
        channel_prefix: str = "mycelium_agent_",
    ) -> None:
        self._database_url = database_url
        self._callback_timeout = callback_timeout
        self._channel_prefix = channel_prefix
        self._listeners: dict[str, Callable[[PropagationEvent], Awaitable[None]]] = {}
        self._channels: dict[str, str] = {}
        self._pool: asyncpg.Pool | None = None
        self._listen_conn: asyncpg.Connection | None = None
        self._lock = asyncio.Lock()
        self._closed = False
        self._dispatch_tasks: set[asyncio.Task[None]] = set()

    async def publish(self, agent_id: str, event: PropagationEvent) -> None:
        """Publish event to the target agent's channel."""
        if self._closed:
            return
        await self._ensure_publisher()
        assert self._pool is not None
        channel = self._channel_for_agent(agent_id)
        payload = _to_payload(event)
        async with self._pool.acquire() as conn:
            await conn.execute("SELECT pg_notify($1, $2)", channel, payload)

    async def subscribe(
        self,
        agent_id: str,
        callback: Callable[[PropagationEvent], Awaitable[None]],
    ) -> None:
        """Register callback and start listening on the agent's channel."""
        if self._closed:
            raise RuntimeError("Transport is closed")
        async with self._lock:
            self._listeners[agent_id] = callback
            channel = self._channel_for_agent(agent_id)
            self._channels[agent_id] = channel
            await self._ensure_listener()
            assert self._listen_conn is not None
            with suppress(Exception):
                await self._listen_conn.remove_listener(channel, self._on_notify)
            await self._listen_conn.add_listener(channel, self._on_notify)

    async def unsubscribe(self, agent_id: str) -> None:
        """Remove callback and stop listening on the agent's channel."""
        async with self._lock:
            self._listeners.pop(agent_id, None)
            channel = self._channels.pop(agent_id, None)
            if channel is not None and self._listen_conn is not None:
                await self._listen_conn.remove_listener(channel, self._on_notify)

    async def close(self) -> None:
        """Close transport resources."""
        async with self._lock:
            self._closed = True
            self._listeners.clear()
            self._channels.clear()
            if self._listen_conn is not None:
                await self._listen_conn.close()
                self._listen_conn = None
            if self._pool is not None:
                await self._pool.close()
                self._pool = None

        if self._dispatch_tasks:
            await asyncio.gather(*self._dispatch_tasks, return_exceptions=True)
            self._dispatch_tasks.clear()

    def has_listener(self, agent_id: str) -> bool:
        return agent_id in self._listeners

    async def _ensure_publisher(self) -> None:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self._database_url,
                min_size=1,
                max_size=5,
            )

    async def _ensure_listener(self) -> None:
        if self._listen_conn is None:
            self._listen_conn = await asyncpg.connect(self._database_url)

    def _channel_for_agent(self, agent_id: str) -> str:
        safe = _sanitize_agent_id(agent_id)
        return f"{self._channel_prefix}{safe}"

    def _on_notify(
        self,
        connection: asyncpg.Connection,
        pid: int,
        channel: str,
        payload: str,
    ) -> None:
        del connection, pid
        task = asyncio.create_task(self._dispatch_notification(channel, payload))
        self._dispatch_tasks.add(task)
        task.add_done_callback(self._dispatch_tasks.discard)

    async def _dispatch_notification(self, channel: str, payload: str) -> None:
        agent_id = None
        for candidate, mapped_channel in self._channels.items():
            if mapped_channel == channel:
                agent_id = candidate
                break
        if agent_id is None:
            return

        callback = self._listeners.get(agent_id)
        if callback is None:
            return

        try:
            event = _from_payload(payload)
        except Exception:
            logger.exception("supabase_rt_payload_error: channel=%s", channel)
            return

        try:
            await asyncio.wait_for(
                callback(event),
                timeout=self._callback_timeout,
            )
        except TimeoutError:
            logger.warning(
                "supabase_rt_timeout: agent=%s event=%s timeout=%.1fs",
                agent_id,
                event.id,
                self._callback_timeout,
            )
        except Exception:
            logger.exception(
                "supabase_rt_callback_error: agent=%s event=%s",
                agent_id,
                event.id,
            )
