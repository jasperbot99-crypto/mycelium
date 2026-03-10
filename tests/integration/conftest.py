"""Integration test fixtures — requires a running PostgreSQL with pgvector."""

from __future__ import annotations

import os

import asyncpg
import pytest
import pytest_asyncio

from mycelium.storage.postgres.repositories import (
    PostgresAgentRepository,
    PostgresConflictRepository,
    PostgresEventLog,
    PostgresFactRepository,
    PostgresRelationRepository,
    PostgresSubscriptionRepository,
)

TEST_DATABASE_URL = os.environ.get(
    "MYCELIUM_TEST_DATABASE_URL",
    "postgresql://localhost:5432/mycelium_test",
)

CLEAN_TABLES = [
    "mycelium.propagation_events",
    "mycelium.fact_relations",
    "mycelium.conflicts",
    "mycelium.subscriptions",
    "mycelium.facts",
    "mycelium.agents",
]


@pytest_asyncio.fixture
async def pool():
    """Create a connection pool for the test database."""
    pool = await asyncpg.create_pool(TEST_DATABASE_URL, min_size=1, max_size=5)
    yield pool
    await pool.close()


@pytest_asyncio.fixture
async def clean_db(pool: asyncpg.Pool):
    """Clean all mycelium tables before and after each test."""
    async with pool.acquire() as conn:
        for table in CLEAN_TABLES:
            await conn.execute(f"DELETE FROM {table}")
    yield
    async with pool.acquire() as conn:
        for table in CLEAN_TABLES:
            await conn.execute(f"DELETE FROM {table}")


@pytest.fixture
def fact_repo(pool: asyncpg.Pool) -> PostgresFactRepository:
    return PostgresFactRepository(pool)


@pytest.fixture
def agent_repo(pool: asyncpg.Pool) -> PostgresAgentRepository:
    return PostgresAgentRepository(pool)


@pytest.fixture
def conflict_repo(pool: asyncpg.Pool) -> PostgresConflictRepository:
    return PostgresConflictRepository(pool)


@pytest.fixture
def relation_repo(pool: asyncpg.Pool) -> PostgresRelationRepository:
    return PostgresRelationRepository(pool)


@pytest.fixture
def subscription_repo(pool: asyncpg.Pool) -> PostgresSubscriptionRepository:
    return PostgresSubscriptionRepository(pool)


@pytest.fixture
def event_log(pool: asyncpg.Pool) -> PostgresEventLog:
    return PostgresEventLog(pool)
