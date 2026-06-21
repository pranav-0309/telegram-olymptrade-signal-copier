from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Generator
from typing import TYPE_CHECKING

import pytest
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


@pytest.fixture(autouse=True)
def _reset_parse_failures_logger() -> Generator[None]:
    """Clear the parse-failures logger's handlers before each test.

    `logging.getLogger()` returns a process-wide singleton, so without
    this fixture, each test that calls `setup_parse_failures_log` would
    leave its FileHandler attached to the same logger, and the
    idempotency test in `tests/test_log.py` would fail on the second
    call (or, more insidiously, only fail when the test suite is run
    in a non-isolation order).
    """
    logging.getLogger("signal_copier.parse_failures").handlers.clear()
    yield
