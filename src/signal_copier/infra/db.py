from __future__ import annotations

import logging
import re
from importlib.resources import files
from typing import Final

import asyncpg  # type: ignore[import-untyped]  # asyncpg ships no type stubs

_log = logging.getLogger(__name__)


_REACHABILITY_ERRORS: tuple[type[BaseException], ...] = (
    asyncpg.InvalidPasswordError,
    asyncpg.InvalidCatalogNameError,
    asyncpg.CannotConnectNowError,
    ConnectionRefusedError,
    OSError,
)


class DatabaseConnectionError(RuntimeError):
    """Raised when Database.connect() cannot reach PostgreSQL.

    The message is safe to log: passwords are redacted from the embedded DSN.
    """


_MIGRATION_PACKAGE: Final[str] = "signal_copier"
_MIGRATION_RESOURCE: Final[str] = "migrations/001_initial.sql"


def _redact_dsn(dsn: str) -> str:
    """Replace the password component of a PostgreSQL DSN with `***`.

    Accepts both URL form (postgresql://user:pass@host:port/db) and
    keyword form (host=... user=... password=...). Query string and
    keyword-form parameters are preserved.
    """
    url_match = re.match(
        r"^([\w+.-]+://[^:]+:)([^@]+)(@.*)$",
        dsn,
    )
    if url_match is not None:
        return f"{url_match.group(1)}***{url_match.group(3)}"
    return re.sub(r"(password\s*=\s*)([^\s]+)", r"\1***", dsn, flags=re.IGNORECASE)


def _load_migration_sql() -> str:
    """Read migrations/001_initial.sql from the installed package data.

    Requires the migrations directory to be declared as package-data in
    pyproject.toml. Raises FileNotFoundError if the wheel was built without
    the migration file — a packaging bug, not a runtime bug.
    """
    return (
        files(_MIGRATION_PACKAGE)
        .joinpath(
            _MIGRATION_RESOURCE,
        )
        .read_text(encoding="utf-8")
    )


class Database:
    """Owns the asyncpg connection pool and runs the initial migration.

    Construction is via the async classmethod connect(); __aenter__/__aexit__
    make it usable as an async context manager for tests and __main__.
    The state_store attribute is constructed during connect() and is the
    public query API for callers.
    """

    pool: asyncpg.Pool
    state_store: object  # StateStore; filled in during M4's later task

    def __init__(self, pool: asyncpg.Pool, state_store: object) -> None:
        self.pool = pool
        self.state_store = state_store

    @classmethod
    async def connect(cls, dsn: str) -> Database:
        try:
            pool = await asyncpg.create_pool(
                dsn,
                min_size=2,
                max_size=10,
                command_timeout=30,
            )
        except _REACHABILITY_ERRORS as exc:
            raise DatabaseConnectionError(
                f"Cannot reach PostgreSQL at {_redact_dsn(dsn)}. "
                f"Check DATABASE_URL and that the Postgres service is "
                f"provisioned (see docs/PRD.md §17.5). "
                f"Underlying error: {type(exc).__name__}: {exc}"
            ) from exc

        migration_sql = _load_migration_sql()
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(migration_sql)

        _log.info(
            "Database connected (pool min=2 max=10, command_timeout=30s, "
            "migration=001_initial applied)",
        )
        return cls(pool, state_store=None)

    async def close(self) -> None:
        """Close the pool. Idempotent."""
        try:
            await self.pool.close()
        except Exception as exc:  # noqa: BLE001
            _log.debug("Database.close: pool already closed or error: %s", exc)

    async def __aenter__(self) -> Database:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()
