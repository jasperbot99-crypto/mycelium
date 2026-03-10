"""Unit tests for InProcessTransport."""

import asyncio
from collections.abc import Callable
from typing import Any
from uuid import uuid4

import pytest

from mycelium.domain.types import Priority, PropagationEvent
from mycelium.transport.in_process import InProcessTransport
from mycelium.transport.supabase_rt import SupabaseRealtimeTransport


def _make_event() -> PropagationEvent:
    return PropagationEvent(
        id=uuid4(),
        fact_id=uuid4(),
        target_agent_id="test-agent",
        reason="test",
        priority=Priority.NORMAL,
    )


class TestInProcessTransport:
    @pytest.mark.asyncio
    async def test_publish_to_subscriber(self) -> None:
        transport = InProcessTransport()
        received: list[PropagationEvent] = []

        async def callback(event: PropagationEvent) -> None:
            received.append(event)

        await transport.subscribe("agent-a", callback)
        event = _make_event()
        await transport.publish("agent-a", event)

        assert len(received) == 1
        assert received[0].id == event.id

    @pytest.mark.asyncio
    async def test_publish_to_nonexistent_agent(self) -> None:
        transport = InProcessTransport()
        event = _make_event()
        # Should not raise
        await transport.publish("nonexistent", event)

    @pytest.mark.asyncio
    async def test_unsubscribe(self) -> None:
        transport = InProcessTransport()
        received: list[PropagationEvent] = []

        async def callback(event: PropagationEvent) -> None:
            received.append(event)

        await transport.subscribe("agent-a", callback)
        await transport.unsubscribe("agent-a")
        await transport.publish("agent-a", _make_event())

        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_callback_error_does_not_propagate(self) -> None:
        transport = InProcessTransport()

        async def bad_callback(event: PropagationEvent) -> None:
            raise RuntimeError("callback failed")

        await transport.subscribe("agent-a", bad_callback)
        # Should not raise — error is caught and logged
        await transport.publish("agent-a", _make_event())

    @pytest.mark.asyncio
    async def test_callback_timeout(self) -> None:
        transport = InProcessTransport(callback_timeout=0.1)

        async def slow_callback(event: PropagationEvent) -> None:
            await asyncio.sleep(1.0)

        await transport.subscribe("agent-a", slow_callback)
        # Should not hang — timeout kicks in
        await transport.publish("agent-a", _make_event())

    @pytest.mark.asyncio
    async def test_multiple_agents(self) -> None:
        transport = InProcessTransport()
        received_a: list[PropagationEvent] = []
        received_b: list[PropagationEvent] = []

        async def callback_a(event: PropagationEvent) -> None:
            received_a.append(event)

        async def callback_b(event: PropagationEvent) -> None:
            received_b.append(event)

        await transport.subscribe("agent-a", callback_a)
        await transport.subscribe("agent-b", callback_b)

        event = _make_event()
        await transport.publish("agent-a", event)

        assert len(received_a) == 1
        assert len(received_b) == 0  # Only agent-a received it


class _DummyPublishConn:
    def __init__(self) -> None:
        self.executed: list[tuple[str, str, str]] = []

    async def execute(self, query: str, channel: str, payload: str) -> None:
        self.executed.append((query, channel, payload))


class _DummyAcquire:
    def __init__(self, conn: _DummyPublishConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _DummyPublishConn:
        return self._conn

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class _DummyPool:
    def __init__(self) -> None:
        self.conn = _DummyPublishConn()
        self.closed = False

    def acquire(self) -> _DummyAcquire:
        return _DummyAcquire(self.conn)

    async def close(self) -> None:
        self.closed = True


class _DummyListenConn:
    def __init__(self) -> None:
        self.listeners: dict[str, Callable[..., None]] = {}
        self.closed = False

    async def add_listener(self, channel: str, callback: Callable[..., None]) -> None:
        self.listeners[channel] = callback

    async def remove_listener(self, channel: str, callback: Callable[..., None]) -> None:
        del callback
        if channel in self.listeners:
            del self.listeners[channel]
        else:
            raise RuntimeError("listener not found")

    async def close(self) -> None:
        self.closed = True

    def emit(self, channel: str, payload: str) -> None:
        callback = self.listeners[channel]
        callback(self, 12345, channel, payload)


class TestSupabaseRealtimeTransport:
    @pytest.mark.asyncio
    async def test_publish_notifies_agent_channel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pool = _DummyPool()
        listen_conn = _DummyListenConn()

        async def fake_create_pool(*args: Any, **kwargs: Any) -> _DummyPool:
            del args, kwargs
            return pool

        async def fake_connect(*args: Any, **kwargs: Any) -> _DummyListenConn:
            del args, kwargs
            return listen_conn

        monkeypatch.setattr("mycelium.transport.supabase_rt.asyncpg.create_pool", fake_create_pool)
        monkeypatch.setattr("mycelium.transport.supabase_rt.asyncpg.connect", fake_connect)

        transport = SupabaseRealtimeTransport("postgresql://example/db")
        event = _make_event()
        event.target_agent_id = "agent-b"
        await transport.publish("agent-b", event)

        assert len(pool.conn.executed) == 1
        _, channel, payload = pool.conn.executed[0]
        assert channel == "mycelium_agent_agent_b"
        assert str(event.id) in payload

        await transport.close()
        assert pool.closed

    @pytest.mark.asyncio
    async def test_subscribe_receives_notified_event(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        pool = _DummyPool()
        listen_conn = _DummyListenConn()

        async def fake_create_pool(*args: Any, **kwargs: Any) -> _DummyPool:
            del args, kwargs
            return pool

        async def fake_connect(*args: Any, **kwargs: Any) -> _DummyListenConn:
            del args, kwargs
            return listen_conn

        monkeypatch.setattr("mycelium.transport.supabase_rt.asyncpg.create_pool", fake_create_pool)
        monkeypatch.setattr("mycelium.transport.supabase_rt.asyncpg.connect", fake_connect)

        transport = SupabaseRealtimeTransport("postgresql://example/db")
        received: list[PropagationEvent] = []

        async def callback(event: PropagationEvent) -> None:
            received.append(event)

        await transport.subscribe("agent-b", callback)
        event = _make_event()
        event.target_agent_id = "agent-b"
        await transport.publish("agent-b", event)

        _, channel, payload = pool.conn.executed[0]
        listen_conn.emit(channel, payload)
        await asyncio.sleep(0)

        assert len(received) == 1
        assert received[0].id == event.id

        await transport.close()
        assert listen_conn.closed

    @pytest.mark.asyncio
    async def test_unsubscribe_stops_delivery(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        pool = _DummyPool()
        listen_conn = _DummyListenConn()

        async def fake_create_pool(*args: Any, **kwargs: Any) -> _DummyPool:
            del args, kwargs
            return pool

        async def fake_connect(*args: Any, **kwargs: Any) -> _DummyListenConn:
            del args, kwargs
            return listen_conn

        monkeypatch.setattr("mycelium.transport.supabase_rt.asyncpg.create_pool", fake_create_pool)
        monkeypatch.setattr("mycelium.transport.supabase_rt.asyncpg.connect", fake_connect)

        transport = SupabaseRealtimeTransport("postgresql://example/db")
        received: list[PropagationEvent] = []

        async def callback(event: PropagationEvent) -> None:
            received.append(event)

        await transport.subscribe("agent-b", callback)
        await transport.unsubscribe("agent-b")

        event = _make_event()
        event.target_agent_id = "agent-b"
        await transport.publish("agent-b", event)
        _, channel, payload = pool.conn.executed[0]

        # No listener registered on channel after unsubscribe.
        assert channel not in listen_conn.listeners
        # Emitting would raise KeyError; no callback should run.
        with pytest.raises(KeyError):
            listen_conn.emit(channel, payload)
        assert received == []

        await transport.close()

    @pytest.mark.asyncio
    async def test_callback_failure_is_swallowed(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        pool = _DummyPool()
        listen_conn = _DummyListenConn()

        async def fake_create_pool(*args: Any, **kwargs: Any) -> _DummyPool:
            del args, kwargs
            return pool

        async def fake_connect(*args: Any, **kwargs: Any) -> _DummyListenConn:
            del args, kwargs
            return listen_conn

        monkeypatch.setattr("mycelium.transport.supabase_rt.asyncpg.create_pool", fake_create_pool)
        monkeypatch.setattr("mycelium.transport.supabase_rt.asyncpg.connect", fake_connect)

        transport = SupabaseRealtimeTransport("postgresql://example/db")

        async def bad_callback(event: PropagationEvent) -> None:
            del event
            raise RuntimeError("boom")

        await transport.subscribe("agent-b", bad_callback)
        event = _make_event()
        event.target_agent_id = "agent-b"
        await transport.publish("agent-b", event)
        _, channel, payload = pool.conn.executed[0]
        listen_conn.emit(channel, payload)
        await asyncio.sleep(0)

        # No exception should propagate from callback failure.
        await transport.close()
