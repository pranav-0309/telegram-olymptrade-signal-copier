from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

import pytest_asyncio
from testcontainers.postgres import PostgresContainer

if TYPE_CHECKING:
    from signal_copier.infra.db import Database


@pytest_asyncio.fixture(scope="session")
async def pg_dsn() -> AsyncIterator[str]:
    """Spin up a real PG 16 container, return its DSN, drop at session end."""
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg.get_connection_url(driver=None)


@pytest_asyncio.fixture
async def db(pg_dsn: str) -> AsyncIterator[Database]:
    """Fresh Database per test: connect, TRUNCATE all tables, yield, close."""
    from signal_copier.infra.db import Database

    database = await Database.connect(pg_dsn)
    try:
        async with database.pool.acquire() as conn:
            await conn.execute("TRUNCATE signals, stages, daily_summary RESTART IDENTITY CASCADE")
        yield database
    finally:
        await database.close()
