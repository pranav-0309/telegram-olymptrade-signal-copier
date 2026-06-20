# M4 — Database Infrastructure & StateStore Design

**Date:** 2026-06-20
**Status:** Draft (pending user review)
**PRD reference:** `docs/PRD.md` v0.7 (§4.5 Result Monitor, §6 Tech Stack, §7 Architecture, §9 Data Model, §10 Error Handling, §15 Build Plan M4 row, §17 Hosting)
**Build plan reference:** PRD §15, M4 row
**M3 spec reference:** `docs/superpowers/specs/2026-06-20-m3-broker-protocol-design.md` (D-1 `StageResult`, D-2 `Decimal` money type — both reused here)

---

## 1. Purpose & Scope

M4 is the fifth milestone of the Telegram → OlympTrade Signal Copier (PRD v0.7). It ships the **persistence layer** — a connection-pooled PostgreSQL backend with three tables (`signals`, `stages`, `daily_summary`), a migration runner, a `StateStore` exposing 8 lifecycle-oriented methods, and a row-mapper layer that returns frozen dataclasses.

**M4 ships I/O** (the first persistence in the project) but **does not yet wire it into the app**. No scheduler, no Telegram listener, no broker writes through `StateStore`. M4 is the database-side shape that M5 (Telegram listener), M6 (scheduler), M8 (broker push events), and M10 (restart recovery) plug into.

**In scope for M4 (8 new/modified files):**

| # | File | Type | Purpose |
|---|---|---|---|
| 1 | `src/signal_copier/infra/db.py` | NEW | `Database` class (asyncpg pool + migration runner + `state_store` attr) + `DatabaseConnectionError` + `_redact_dsn` helper |
| 2 | `src/signal_copier/infra/db_rows.py` | NEW | `SignalRow`, `StageRow`, `DailySummaryRow` frozen dataclasses + `row_to_*` mappers |
| 3 | `src/signal_copier/infra/state_store.py` | NEW | `StateStore` class with 8 methods + `StageAlreadyExistsError` |
| 4 | `migrations/001_initial.sql` | NEW | DDL verbatim from PRD §9.0 — 3 tables, 5 indexes |
| 5 | `tests/conftest.py` | NEW | `pg_dsn` session fixture (testcontainers) + `db` function fixture (per-test `Database` with TRUNCATE) |
| 6 | `tests/test_db.py` | NEW | ~26 integration tests + 4 unit tests covering migrations, round-trip CRUD, daily-summary UPSERT, command timeout, connection-loss recovery |
| 7 | `pyproject.toml` | MODIFY | add `asyncpg>=0.30` to `dependencies`; add `testcontainers[postgresql]>=4.8` to `[tool.uv].dev-dependencies`; add `migrations/*.sql` to `[tool.hatch.build.targets.wheel] package-data` |
| 8 | `src/signal_copier/infra/__init__.py` | unchanged | (no re-exports — callers import from submodules) |

**Out of scope (deferred to later milestones):**

| Concern | Lands in |
|---|---|
| Calling `state_store.upsert_signal(signal)` from M5's Telegram listener | M5 |
| Calling `state_store.update_signal_state(...)` and `record_stage_placed(...)` from M6's scheduler | M6 |
| Calling `state_store.record_stage_result(trade_id, pnl, ...)` from M8's push-event handler | M8 |
| Restart-recovery logic via `state_store.get_active_signals()` | M10 |
| Daily-limit enforcement via `state_store.get_daily_summary(date)` and `update_daily_summary(date, ...)` | M6 |
| `signal_row_to_state(row, config) -> SignalState` helper that reconstructs M2's `SignalState` from M4's `SignalRow` | M6 (small follow-on; documented in §6.5) |
| Real broker WebSocket connection | M8 |
| Logging config (M4 uses stdlib `logging` like M3; loguru arrives in M7) | M7 |
| Schema migrations beyond `001_initial.sql` (real migration tool: v2 concern per PRD §9.2) | v2 |

---

## 2. Resolved Decisions (M4-specific)

The PRD resolves all architectural questions (R-1 through R-15). The following are M4-specific scoping calls, confirmed during brainstorming on 2026-06-20.

| # | Decision | Rationale |
|---|---|---|
| D-1 | **One `Database` class owns the asyncpg pool; `StateStore` is a stateless attribute constructed in `Database.connect()`** | Single entry point means migrations are guaranteed to run before any `StateStore` call. The async context manager protocol (`__aenter__`/`__aexit__`) makes `Database` ergonomic to use in both production (`__main__`) and tests (`db` fixture). Avoids the caller-construction trap that two-class designs (Database + StateStore, separately wired) suffer from. |
| D-2 | **Migrations are loaded via `importlib.resources` from package data, not via `Path(__file__)` traversal** | Migrations shipped in the wheel need to be discoverable at runtime in installed environments, not just source checkouts. `importlib.resources.files("signal_copier")` walks the package; the migration lives at a known path within the package's installed data. Requires `migrations/*.sql` to be declared as `package-data` in `pyproject.toml` (Hatchling target). Alternative: a `MIGRATIONS_DIR` config var. **Picking: `importlib.resources` + `package-data`** because the migration path is fixed and small. |
| D-3 | **`StateStore` exposes 8 lifecycle-oriented methods** (3 writes, 2 transition writes, 1 transition read, 1 summary write, 1 summary read — see §4.3) | One method per logical operation matches FR-5.8 ("persist on every transition") and avoids forcing M6's scheduler to do fetch → modify → save round-trips for every state change. A `record_stage` method that handles both "place" and "result" semantics would conflate two distinct operations; splitting them keeps each method testable in isolation. |
| D-4 | **StateStore methods return frozen dataclasses (`SignalRow`, `StageRow`, `DailySummaryRow`)** defined in `infra/db_rows.py` | Consistent with M1/M2 style (`@dataclass(frozen=True, slots=True)`). Keeps asyncpg types out of domain code (M6/M8 can take a `FakeStateStore` in unit tests that returns dataclasses, no asyncpg dependency). mypy strict-friendly. The float-to-Decimal conversion at the mapper layer is the one place where money-precision is handled; the rest of the app stays in `Decimal` like M2. |
| D-5 | **`asyncpg` exceptions bubble up untouched from `StateStore` methods** | Pythonic; no information loss; no premature abstraction. asyncpg is locked via R-13 (no plans to swap drivers). The only place we add a domain exception is `Database.connect()` (`DatabaseConnectionError`) and `StateStore.record_stage_placed` (`StageAlreadyExistsError`) — both for the reasons in §6.2. |
| D-6 | **Test strategy: `testcontainers[postgresql]` with a session-scoped container and a function-scoped `TRUNCATE` fixture** | Real PG (matches PRD §9.4 spirit) without per-test container overhead. The `testcontainers` Python lib handles Docker socket discovery across macOS/Linux/Windows. Function-scoped TRUNCATE gives clean isolation without per-test transaction-savepoint complexity. A developer with Docker running gets green tests in ~10 seconds. |
| D-7 | **Money round-trips through `DOUBLE PRECISION` in the DB but `Decimal` in Python** (PRD §9.0's call) | The PRD chose `DOUBLE PRECISION` for `amount`, `pnl`, `realized_pnl`. asyncpg maps this to Python `float`. Mappers cast `float → Decimal(str(value))` on read to avoid precision drift (`Decimal(0.92)` ≠ `Decimal("0.92")`). Writes accept `Decimal`; asyncpg converts to `float` for the wire. The `Decimal(str(value))` cast is the only place this wart is visible in code. Documented as a v1 wart; the proper fix is `NUMERIC(12,4)` in v2 if/when financial-grade precision is needed. |
| D-8 | **`upsert_signal` returns `bool`** (True = newly inserted, False = duplicate) | Lets the M5 listener distinguish "this is a new signal" from "this is a duplicate" without a separate `SELECT`. The `INSERT ... ON CONFLICT (signal_id) DO NOTHING RETURNING signal_id` returns a row on insert, no row on conflict. Simple and lossless. |
| D-9 | **`trade_id` is generated deterministically by `StateStore.record_stage_placed` as `sha1(f"{signal_id}\|{stage}\|{placed_at_unix:.6f}".encode()).hexdigest()[:16]`** | Determinism lets M10's restart-recovery logic look up the stage without persisting the ID separately, and `record_stage_result` is safely retriable. The `.6f` format prevents float-precision differences from causing ID drift. 16 hex chars = 64 bits = collision probability ~0 for in-flight trades (single-channel tool). |
| D-10 | **`StageAlreadyExistsError` is the only domain exception `StateStore` raises** (raised by `record_stage_placed` when the generated `trade_id` collides — which is a programming bug, not a normal event) | The deterministic `trade_id` derivation (D-9) means a legitimate second call with the same `(signal_id, stage, placed_at_unix)` is a bug — either the broker reported the same trade twice (caller bug) or M10's restart-recovery re-ran the placement (caller bug). Raising forces the bug to surface; not silently ignoring. |
| D-11 | **Daily-summary UPSERT uses additive deltas, not absolute values** | `update_daily_summary(date, *, signals_count_delta, trades_count_delta, wins_delta, losses_delta, realized_pnl_delta, limit_hit)` lets callers say "another WIN just happened" without read-modify-write inside a transaction. `limit_hit` is special: passed as `None` to leave it unchanged (`COALESCE(EXCLUDED.limit_hit, daily_summary.limit_hit)`), passed as a string to set/replace. |
| D-12 | **`Database.close()` is idempotent; safe to call twice** (matches M3's `Broker.close()` D-X from M3 spec) | A misbehaving shutdown handler may call `close()` twice. asyncpg's `pool.close()` raises on a closed pool; we catch and log. |
| D-13 | **DSN redaction in `DatabaseConnectionError` strips the password** | The error message must not leak credentials into logs. `_redact_dsn(dsn)` converts `postgresql://user:secret@host:5432/db?sslmode=require` → `postgresql://user:***@host:5432/db?sslmode=require`. Covered by a unit test (`test_redact_dsn_strips_password`) so the redaction is regression-protected. |
| D-14 | **Pool config: `min_size=2, max_size=10, command_timeout=30`** (verbatim from PRD §9.2) | No deviation from PRD. `min_size=2` keeps a warm connection ready for the next StateStore call; `max_size=10` is far above our peak concurrency (M6 will serialize per-signal transitions; M5/M8 are also single-threaded async). `command_timeout=30` aborts hung queries. |
| D-15 | **Logging uses stdlib `logging` for M4** (same as M3 D-6); loguru arrives in M7 | Zero-dep; M7's loguru setup will route stdlib `logging` through loguru's sinks. M4's error and warning paths emit structured log lines with the `signal_id` correlation. |
| D-16 | **M4 uses `package_data` for migrations** (Hatchling-specific `[tool.hatch.build.targets.wheel.force-include]`) | Without this, `migrations/001_initial.sql` won't be in the built wheel and `importlib.resources` will fail at runtime. Documented in §5. |

---

## 3. Repository Layout (post-M4)

```
olymptrade/
├── pyproject.toml                          # MODIFY: +asyncpg, +testcontainers[postgresql], package-data
├── migrations/
│   └── 001_initial.sql                     # NEW: DDL verbatim from PRD §9.0
├── src/
│   ├── olymptrade_ws/                      # (unchanged, vendored)
│   └── signal_copier/
│       ├── __init__.py                     # (unchanged from M3)
│       ├── __main__.py                     # (unchanged from M2)
│       ├── config.py                       # (unchanged from M2)
│       ├── broker/                         # (unchanged from M3)
│       │   ├── __init__.py
│       │   ├── base.py
│       │   └── dry_run.py
│       ├── domain/                         # (unchanged from M2)
│       │   ├── __init__.py
│       │   ├── signal.py
│       │   ├── gale.py
│       │   └── state.py
│       └── infra/                          # MODIFY (additions only)
│           ├── __init__.py                 # (unchanged, no re-exports)
│           ├── log.py                      # (unchanged from M2 stub)
│           ├── db.py                       # NEW: Database, DatabaseConnectionError, _redact_dsn
│           ├── db_rows.py                  # NEW: SignalRow, StageRow, DailySummaryRow + mappers
│           └── state_store.py              # NEW: StateStore + StageAlreadyExistsError
└── tests/
    ├── conftest.py                         # NEW: pg_dsn, db fixtures
    ├── test_db.py                          # NEW: ~30 tests
    ├── test_broker_protocol.py             # (unchanged from M3)
    ├── test_dry_run_broker.py              # (unchanged from M3)
    ├── test_main.py                        # (unchanged from M2)
    ├── test_parser.py                      # (unchanged from M1)
    ├── test_gale_math.py                   # (unchanged from M2)
    ├── test_state_machine.py               # (unchanged from M2)
    └── test_config.py                      # (unchanged from M2)
```

**Notable choices:**

- `src/signal_copier/infra/db.py`, `db_rows.py`, `state_store.py` are three new files under the existing `infra/` package. `db.py` owns the pool; `db_rows.py` is the row-type module (importable without asyncpg); `state_store.py` is the query API. Splitting them keeps each focused and unit-testable (mappers don't import asyncpg; `Database` doesn't import domain types).
- `migrations/` already exists from M0 (empty, with `.gitkeep`). M4 adds `001_initial.sql` and removes the `.gitkeep` (the directory is no longer empty).
- `tests/conftest.py` is a new top-level test-config file. M0–M3 had no shared fixtures (each test file was self-contained). M4 introduces the first cross-file fixture (`db`).
- `pyproject.toml` gets the two new deps and the `package-data` directive (D-2, D-16). No other config changes.

---

## 4. Key File Contents

### 4.1 `src/signal_copier/infra/db.py` (NEW)

```python
from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.resources import files
from typing import ClassVar

import asyncpg

from signal_copier.infra.state_store import StateStore

_log = logging.getLogger(__name__)


# Exceptions that indicate "cannot reach the DB at all" — i.e., the DSN is
# wrong, the host is down, the credentials are invalid, or the database
# doesn't exist. We wrap these in DatabaseConnectionError so __main__ can
# print a single actionable message and exit. Other asyncpg errors
# (e.g., SQL syntax error in a migration) bubble up untouched so the
# developer sees the original asyncpg diagnostic.
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


_MIGRATION_PACKAGE: ClassVar[str] = "signal_copier"
_MIGRATION_RESOURCE: ClassVar[str] = "migrations/001_initial.sql"


def _redact_dsn(dsn: str) -> str:
    """Replace the password component of a PostgreSQL DSN with `***`.

    Accepts both URL form (postgresql://user:pass@host:port/db) and
    keyword form (host=... user=... password=...). Query string and
    keyword-form parameters are preserved.
    """
    # URL form: postgresql://user:pass@host:port/db?sslmode=require
    url_match = re.match(
        r"^([\w+.-]+://[^:]+:)([^@]+)(@.*)$", dsn,
    )
    if url_match is not None:
        return f"{url_match.group(1)}***{url_match.group(3)}"
    # Keyword form: password=secret ...
    return re.sub(r"(password\s*=\s*)([^\s]+)", r"\1***", dsn, flags=re.IGNORECASE)


def _load_migration_sql() -> str:
    """Read migrations/001_initial.sql from the installed package data.

    Requires the migrations directory to be declared as package-data in
    pyproject.toml (D-2, D-16). Raises FileNotFoundError if the wheel was
    built without the migration file — a packaging bug, not a runtime bug.
    """
    return files(_MIGRATION_PACKAGE).joinpath(  # type: ignore[no-any-return]
        _MIGRATION_RESOURCE,
    ).read_text(encoding="utf-8")


class Database:
    """Owns the asyncpg connection pool and runs the initial migration.

    Construction is via the async classmethod connect(); __aenter__/__aexit__
    make it usable as an async context manager for tests and __main__.
    The state_store attribute is constructed during connect() and is the
    public query API for callers.
    """

    pool: asyncpg.Pool
    state_store: StateStore

    def __init__(self, pool: asyncpg.Pool, state_store: StateStore) -> None:
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

        # Run the initial migration (idempotent CREATE TABLE IF NOT EXISTS).
        migration_sql = _load_migration_sql()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(migration_sql)

        state_store = StateStore(pool)
        _log.info(
            "Database connected (pool min=2 max=10, command_timeout=30s, "
            "migration=001_initial applied)",
        )
        return cls(pool, state_store)

    async def close(self) -> None:
        """Close the pool. Idempotent (D-12)."""
        try:
            await self.pool.close()
        except Exception as exc:  # noqa: BLE001 — D-12: idempotent close
            _log.debug("Database.close: pool already closed or error: %s", exc)

    async def __aenter__(self) -> Database:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()
```

**Notes:**

- `_REACHABILITY_ERRORS` lists the asyncpg / stdlib exceptions that mean "the DB is unreachable" — these get wrapped. Other asyncpg errors (e.g., `CheckViolationError`, `UndefinedTableError`, syntax errors) bubble up untouched so the developer sees the original asyncpg message.
- `_redact_dsn` handles both URL form and keyword form (asyncpg accepts both). DSN form is the one used in production (Railway env var); keyword form is a defensive convenience.
- `_load_migration_sql` uses `importlib.resources.files()` (Python 3.12+ stdlib) to read the SQL from package data. Requires `pyproject.toml` to declare the migrations directory as `package-data` (D-16).
- `Database.__init__` is public (not private) so tests can construct a `Database` from a pre-existing pool if needed (e.g., for testing the `state_store` attribute in isolation).
- `Database.close()` swallows any exception from `pool.close()` on the second call — D-12.

### 4.2 `src/signal_copier/infra/db_rows.py` (NEW)

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from asyncpg import Record

from signal_copier.domain.gale import Stage
from signal_copier.domain.signal import Signal
from signal_copier.domain.state import AllStates, ErrorReason, StageResult

# Local type aliases keep the row types self-contained and easy to scan.
Direction = Literal["up", "down"]


@dataclass(frozen=True, slots=True)
class SignalRow:
    """One row from the `signals` table (PRD §9.0).

    Returned by StateStore.get_signal() and StateStore.get_active_signals().
    Money fields are Decimal in Python (per D-7); mappers cast
    float → Decimal(str(value)) on read.
    """

    signal_id: str
    pair: str
    broker_pair: str | None
    broker_category: str | None
    direction: Direction
    trigger_hhmm: str
    trigger_ts_unix: float
    expiration_seconds: int
    received_at_unix: float
    source_message_id: int
    source_chat_id: int
    raw_text: str
    status: AllStates
    error_reason: ErrorReason | None
    created_at_unix: float
    updated_at_unix: float


@dataclass(frozen=True, slots=True)
class StageRow:
    """One row from the `stages` table (PRD §9.0)."""

    trade_id: str
    signal_id: str
    stage: Stage
    pair: str
    direction: Direction
    amount: Decimal
    placed_at_unix: float
    expires_at_unix: float
    closed_at_unix: float | None
    pnl: Decimal | None
    result: StageResult
    broker_trade_id: str | None


@dataclass(frozen=True, slots=True)
class DailySummaryRow:
    """One row from the `daily_summary` table (PRD §9.0)."""

    date: date
    signals_count: int
    trades_count: int
    wins: int
    losses: int
    realized_pnl: Decimal
    limit_hit: str | None  # NULL | 'loss' | 'count' | 'drawdown'


def row_to_signal_row(record: Record) -> SignalRow:
    """Map an asyncpg.Record from a `signals` SELECT to a SignalRow.

    Money fields are stored as DOUBLE PRECISION in the DB; the mapper
    converts via Decimal(str(value)) to avoid float-precision drift (D-7).
    """
    return SignalRow(
        signal_id=record["signal_id"],
        pair=record["pair"],
        broker_pair=record["broker_pair"],
        broker_category=record["broker_category"],
        direction=record["direction"],
        trigger_hhmm=record["trigger_hhmm"],
        trigger_ts_unix=record["trigger_ts_unix"],
        expiration_seconds=record["expiration_seconds"],
        received_at_unix=record["received_at_unix"],
        source_message_id=record["source_message_id"],
        source_chat_id=record["source_chat_id"],
        raw_text=record["raw_text"],
        status=record["status"],
        error_reason=record["error_reason"],
        created_at_unix=record["created_at_unix"],
        updated_at_unix=record["updated_at_unix"],
    )


def row_to_stage_row(record: Record) -> StageRow:
    return StageRow(
        trade_id=record["trade_id"],
        signal_id=record["signal_id"],
        stage=record["stage"],
        pair=record["pair"],
        direction=record["direction"],
        amount=Decimal(str(record["amount"])),  # D-7: float → Decimal via str
        placed_at_unix=record["placed_at_unix"],
        expires_at_unix=record["expires_at_unix"],
        closed_at_unix=record["closed_at_unix"],
        pnl=Decimal(str(record["pnl"])) if record["pnl"] is not None else None,
        result=record["result"],
        broker_trade_id=record["broker_trade_id"],
    )


def row_to_daily_summary_row(record: Record) -> DailySummaryRow:
    return DailySummaryRow(
        date=record["date"],
        signals_count=record["signals_count"],
        trades_count=record["trades_count"],
        wins=record["wins"],
        losses=record["losses"],
        realized_pnl=Decimal(str(record["realized_pnl"])),  # D-7
        limit_hit=record["limit_hit"],
    )
```

**Notes:**

- All three row types are frozen dataclasses with `slots=True` — consistent with M1's `Signal` (which has the same shape pattern) and M2's `SignalState`.
- The `StageResult` value in `StageRow` may be `"open"` (the row was just inserted; no result yet) or one of `"win" | "loss" | "tie" | "timeout" | "error"`. M2's existing `StageResult` literal already includes `"open"` via PRD §9.0's CHECK constraint.
- `row_to_*` mappers are pure functions — easily unit-testable with hand-built `asyncpg.Record` instances.
- The `Decimal(str(value))` cast is the single point of float-precision handling in the codebase (D-7). It's named explicitly so a future v2 migration to `NUMERIC(12,4)` has one place to change.

### 4.3 `src/signal_copier/infra/state_store.py` (NEW)

```python
from __future__ import annotations

import hashlib
import logging
from datetime import date
from decimal import Decimal
from typing import Any, Literal

import asyncpg

from signal_copier.domain.gale import Stage
from signal_copier.domain.signal import Signal
from signal_copier.domain.state import AllStates, ErrorReason, StageResult
from signal_copier.infra.db_rows import (
    DailySummaryRow,
    SignalRow,
    StageRow,
    row_to_daily_summary_row,
    row_to_signal_row,
    row_to_stage_row,
)

_log = logging.getLogger(__name__)


class StageAlreadyExistsError(Exception):
    """Raised by StateStore.record_stage_placed() on a trade_id collision.

    This is a programming bug, not a normal runtime event (D-9, D-10):
    the deterministic trade_id derivation means a duplicate call with
    the same (signal_id, stage, placed_at_unix) is either a caller bug
    or a misbehaving restart-recovery path.
    """


class StateStore:
    """CRUD API over the signal_copier PostgreSQL schema.

    All write methods acquire a connection from the pool, run inside a
    transaction, and return. All read methods acquire a connection and
    return. asyncpg exceptions bubble up untouched (D-5); the only domain
    exception raised is StageAlreadyExistsError (D-10).
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # --- Writes ----------------------------------------------------------

    async def upsert_signal(self, signal: Signal) -> bool:
        """INSERT the signal; on signal_id conflict, do nothing.

        Returns True if the row was newly inserted, False if it already
        existed (D-8). The caller (M5 listener) uses the bool to decide
        whether to log "new signal" or "duplicate signal, ignored".
        """
        sql = """
            INSERT INTO signals (
                signal_id, pair, broker_pair, broker_category, direction,
                trigger_hhmm, trigger_ts_unix, expiration_seconds,
                received_at_unix, source_message_id, source_chat_id, raw_text,
                status, error_reason, created_at_unix, updated_at_unix
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                'pending', NULL, $9, $9
            )
            ON CONFLICT (signal_id) DO NOTHING
            RETURNING signal_id
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchval(
                    sql,
                    signal.signal_id, signal.pair, None, None, signal.direction,
                    signal.trigger_hhmm, signal.trigger_unix_initial,
                    signal.expiration_seconds, signal.received_at_unix,
                    signal.source_message_id, signal.source_chat_id,
                    signal.raw_text,
                )
        return row is not None

    async def update_signal_state(
        self,
        signal_id: str,
        new_state: AllStates,
        *,
        error_reason: ErrorReason | None = None,
        updated_at_unix: float,
    ) -> None:
        """Persist a state-machine transition for an existing signal.

        Idempotent: a re-write with the same new_state is a no-op. If
        signal_id doesn't exist, logs a warning and returns (the state
        machine's invariant is that you can't transition a non-existent
        signal; M6's call site is responsible for that, not M4).
        """
        sql = """
            UPDATE signals
            SET status = $1, error_reason = $2, updated_at_unix = $3
            WHERE signal_id = $4
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                result = await conn.execute(
                    sql, new_state, error_reason, updated_at_unix, signal_id,
                )
        if result.endswith(" 0"):
            _log.warning(
                "update_signal_state: no row for signal_id=%s (idempotent no-op)",
                signal_id,
            )

    async def record_stage_placed(
        self,
        signal_id: str,
        stage: Stage,
        *,
        pair: str,
        direction: Literal["up", "down"],
        amount: Decimal,
        placed_at_unix: float,
        expires_at_unix: float,
        broker_trade_id: str | None = None,
    ) -> str:
        """INSERT a row in `stages` marking a trade as placed.

        Returns the deterministic trade_id (D-9):
            sha1(f"{signal_id}|{stage}|{placed_at_unix:.6f}".encode()).hexdigest()[:16]
        Raises StageAlreadyExistsError if the same (signal_id, stage, placed_at_unix)
        is recorded twice — a programming bug (D-10).
        """
        trade_id = self._derive_trade_id(signal_id, stage, placed_at_unix)
        sql = """
            INSERT INTO stages (
                trade_id, signal_id, stage, pair, direction, amount,
                placed_at_unix, expires_at_unix, result, broker_trade_id
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'open', $9)
            ON CONFLICT (trade_id) DO NOTHING
            RETURNING trade_id
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchval(
                    sql,
                    trade_id, signal_id, stage, pair, direction, amount,
                    placed_at_unix, expires_at_unix, broker_trade_id,
                )
        if row is None:
            raise StageAlreadyExistsError(
                f"stages.trade_id={trade_id} already exists "
                f"(signal_id={signal_id} stage={stage} placed_at_unix={placed_at_unix:.6f})"
            )
        return trade_id

    async def record_stage_result(
        self,
        trade_id: str,
        result: StageResult,
        *,
        pnl: Decimal,
        closed_at_unix: float,
    ) -> None:
        """Update an existing `stages` row with the broker-reported result.

        Idempotent: re-writing the same result is a no-op. If trade_id
        doesn't exist, logs a warning (this is expected during restart
        recovery if a push event arrives after M10's recovery sweep
        missed a stage row).
        """
        sql = """
            UPDATE stages
            SET result = $1, pnl = $2, closed_at_unix = $3
            WHERE trade_id = $4
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                tag = await conn.execute(sql, result, pnl, closed_at_unix, trade_id)
        if tag.endswith(" 0"):
            _log.warning(
                "record_stage_result: no row for trade_id=%s (late push event?)",
                trade_id,
            )

    async def update_daily_summary(
        self,
        on_date: date,
        *,
        signals_count_delta: int = 0,
        trades_count_delta: int = 0,
        wins_delta: int = 0,
        losses_delta: int = 0,
        realized_pnl_delta: Decimal = Decimal("0"),
        limit_hit: str | None = None,
    ) -> None:
        """UPSERT a row in `daily_summary` with additive deltas (D-11).

        Pass deltas (not absolutes). Passing limit_hit=None leaves the
        existing value unchanged; passing a string sets/replaces it.
        """
        sql = """
            INSERT INTO daily_summary (
                date, signals_count, trades_count, wins, losses,
                realized_pnl, limit_hit
            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (date) DO UPDATE SET
                signals_count  = daily_summary.signals_count  + EXCLUDED.signals_count,
                trades_count   = daily_summary.trades_count   + EXCLUDED.trades_count,
                wins           = daily_summary.wins           + EXCLUDED.wins,
                losses         = daily_summary.losses         + EXCLUDED.losses,
                realized_pnl   = daily_summary.realized_pnl   + EXCLUDED.realized_pnl,
                limit_hit      = COALESCE(EXCLUDED.limit_hit, daily_summary.limit_hit)
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    sql,
                    on_date, signals_count_delta, trades_count_delta,
                    wins_delta, losses_delta, realized_pnl_delta, limit_hit,
                )

    # --- Reads -----------------------------------------------------------

    async def get_signal(self, signal_id: str) -> SignalRow | None:
        """Fetch one signal by id. None if not found."""
        sql = "SELECT * FROM signals WHERE signal_id = $1"
        async with self._pool.acquire() as conn:
            record = await conn.fetchrow(sql, signal_id)
        if record is None:
            return None
        return row_to_signal_row(record)

    async def get_active_signals(self) -> list[SignalRow]:
        """Fetch all signals currently in placed_* states (restart recovery input)."""
        sql = """
            SELECT * FROM signals
            WHERE status IN ('placed_initial', 'placed_gale1', 'placed_gale2')
            ORDER BY trigger_ts_unix
        """
        async with self._pool.acquire() as conn:
            records = await conn.fetch(sql)
        return [row_to_signal_row(r) for r in records]

    async def get_daily_summary(self, on_date: date) -> DailySummaryRow | None:
        """Fetch one daily summary by date. None if no row yet (clean day)."""
        sql = "SELECT * FROM daily_summary WHERE date = $1"
        async with self._pool.acquire() as conn:
            record = await conn.fetchrow(sql, on_date)
        if record is None:
            return None
        return row_to_daily_summary_row(record)

    # --- Internal helpers ------------------------------------------------

    @staticmethod
    def _derive_trade_id(
        signal_id: str, stage: Stage, placed_at_unix: float,
    ) -> str:
        """Deterministic 16-char trade_id (D-9)."""
        payload = f"{signal_id}|{stage}|{placed_at_unix:.6f}"
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
```

**Notes:**

- The `*_count_delta=0` defaults on `update_daily_summary` let callers pass only the fields they want to change. All deltas default to 0 (a no-op for that column). `limit_hit` is the exception: `None` means "leave unchanged", a string means "set/replace" — handled by the SQL `COALESCE` clause.
- `record_stage_placed` returns the generated `trade_id` so M6 can pass it to `broker.wait_result(trade_id, ...)` and to `record_stage_result(trade_id, ...)` later. The caller never constructs the `trade_id` — M4 owns that.
- `update_signal_state` requires `updated_at_unix` as a required parameter (not a default) so callers can't forget to pass the timestamp. The state's transition time is meaningful for the state machine (e.g., for "signal expired" recovery logic in M6).
- `record_stage_result` accepts `result: StageResult` directly (matching M2's `StageResult` literal) — no separate `TradeResult` type. M3 D-1.
- The "0 rows updated" warnings are at WARNING level so they show up in production logs but don't fail the call. M6's call site is the right place to decide whether to retry.

### 4.4 `migrations/001_initial.sql` (NEW)

Verbatim from PRD §9.0:

```sql
-- migrations/001_initial.sql
-- Signal Copier v1 schema. Idempotent: safe to run on every boot.
-- See docs/PRD.md §9 for the full design rationale.

CREATE TABLE IF NOT EXISTS signals (
    signal_id          TEXT PRIMARY KEY,
    pair               TEXT NOT NULL,
    broker_pair        TEXT,
    broker_category    TEXT,
    direction          TEXT NOT NULL CHECK (direction IN ('up', 'down')),
    trigger_hhmm       TEXT NOT NULL,
    trigger_ts_unix    DOUBLE PRECISION NOT NULL,
    expiration_seconds INTEGER NOT NULL,
    received_at_unix   DOUBLE PRECISION NOT NULL,
    source_message_id  BIGINT,
    source_chat_id     BIGINT,
    raw_text           TEXT,
    status             TEXT NOT NULL
        CHECK (status IN (
            'pending', 'placed_initial', 'placed_gale1', 'placed_gale2',
            'done_win', 'done_loss', 'done_tie', 'done_timeout', 'error'
        )),
    error_reason       TEXT,
    created_at_unix    DOUBLE PRECISION NOT NULL,
    updated_at_unix    DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS stages (
    trade_id           TEXT PRIMARY KEY,
    signal_id          TEXT NOT NULL REFERENCES signals(signal_id) ON DELETE CASCADE,
    stage              TEXT NOT NULL CHECK (stage IN ('initial', 'gale1', 'gale2')),
    pair               TEXT NOT NULL,
    direction          TEXT NOT NULL,
    amount             DOUBLE PRECISION NOT NULL,
    placed_at_unix     DOUBLE PRECISION NOT NULL,
    expires_at_unix    DOUBLE PRECISION NOT NULL,
    closed_at_unix     DOUBLE PRECISION,
    pnl                DOUBLE PRECISION,
    result             TEXT CHECK (result IN ('open', 'win', 'loss', 'tie', 'timeout', 'error')),
    broker_trade_id    TEXT
);

CREATE TABLE IF NOT EXISTS daily_summary (
    date              DATE PRIMARY KEY,
    signals_count     INTEGER NOT NULL DEFAULT 0,
    trades_count      INTEGER NOT NULL DEFAULT 0,
    wins              INTEGER NOT NULL DEFAULT 0,
    losses            INTEGER NOT NULL DEFAULT 0,
    realized_pnl      DOUBLE PRECISION NOT NULL DEFAULT 0,
    limit_hit         TEXT
);

CREATE INDEX IF NOT EXISTS idx_signals_status      ON signals(status);
CREATE INDEX IF NOT EXISTS idx_signals_trigger_ts  ON signals(trigger_ts_unix);
CREATE INDEX IF NOT EXISTS idx_stages_signal_id    ON stages(signal_id);
CREATE INDEX IF NOT EXISTS idx_stages_placed_at    ON stages(placed_at_unix);
CREATE INDEX IF NOT EXISTS idx_stages_result       ON stages(result);
```

**Notes:**

- This is the **exact** DDL from PRD §9.0, lines 421–476, with only the comment header added.
- All 3 tables use `CREATE TABLE IF NOT EXISTS`; all 5 indexes use `CREATE INDEX IF NOT EXISTS` — the entire file is idempotent. `Database.connect()` runs it on every boot; subsequent boots are no-ops.

### 4.5 `src/signal_copier/infra/__init__.py` (UNCHANGED)

Empty. Callers import from submodules: `from signal_copier.infra.db import Database`, `from signal_copier.infra.state_store import StateStore`, `from signal_copier.infra.db_rows import SignalRow`. No top-level re-exports — the `infra` package is a namespace, not a facade.

---

## 5. Dependency Changes

### 5.1 `pyproject.toml` modifications

Three changes, all additive:

**a. `dependencies` (runtime):**

```toml
dependencies = [
    "pydantic-settings>=2.6",  # M2: config layer (D-3)
    "tzdata>=2024.1",          # IANA tz database on Windows; no-op on Linux/macOS
    "asyncpg>=0.30",           # M4: async-native PostgreSQL driver (PRD §6, R-13)
]
```

**b. `dev-dependencies` (test-time only):**

```toml
[tool.uv]
dev-dependencies = [
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
    "pytest-cov>=5.0",
    "ruff>=0.7",
    "mypy>=1.13",
    "pre-commit>=4.0",
    "testcontainers[postgresql]>=4.8",  # M4: spin up real PG per test session
]
```

**c. `package-data` (Hatchling wheel build):**

```toml
[tool.hatch.build.targets.wheel.force-include]
"migrations" = "signal_copier/migrations"
```

This is the mechanism that ships `migrations/001_initial.sql` inside the built wheel so `importlib.resources` can find it (D-2, D-16). Without this, the wheel would not contain the SQL and `Database.connect()` would raise `FileNotFoundError` at runtime in production.

### 5.2 New symbols

| Symbol | Source | Purpose |
|---|---|---|
| `asyncpg.Pool`, `asyncpg.create_pool` | asyncpg | Connection pool (D-14) |
| `asyncpg.Record`, `asyncpg.fetch` / `fetchrow` / `fetchval` / `execute` | asyncpg | DB queries |
| `asyncpg.PostgresError` hierarchy | asyncpg | Bubbles up to callers (D-5) |
| `testcontainers.postgres.PostgresContainer` | testcontainers[postgresql] | Test-only PG instance (D-6) |
| `importlib.resources.files` | stdlib | Migration file lookup (D-2) |
| `hashlib.sha1` | stdlib | Deterministic trade_id (D-9) |
| `re` | stdlib | DSN redaction |

### 5.3 Docker image impact

`asyncpg` is a small C extension. Adding it to the Railway image increases image size by ~5–10 MB. The Dockerfile (defined in M0) needs no changes — `pip install` picks up the new dep automatically.

`testcontainers[postgresql]` is dev-only; not in the image.

---

## 6. Architecture

### 6.1 Module relationships

```
                    ┌────────────────────┐
                    │  signal_copier/    │
                    │      __main__.py   │  (M6 will call Database.connect)
                    └─────────┬──────────┘
                              │
                              ▼
                    ┌────────────────────┐
                    │  infra/db.py       │
                    │  Database          │  owns the pool, runs migration
                    │  - pool            │  in connect()
                    │  - state_store ────┼──┐
                    └────────────────────┘  │
                                             ▼
                    ┌────────────────────┐  ┌────────────────────┐
                    │  infra/db_rows.py  │  │ infra/state_store  │
                    │  SignalRow         │◀─│ StateStore         │
                    │  StageRow          │  │ 8 methods          │
                    │  DailySummaryRow   │  │ (writes+reads)     │
                    │  row_to_* mappers  │  └─────────┬──────────┘
                    └────────────────────┘            │
                                                      ▼
                                            ┌──────────────────┐
                                            │   asyncpg.Pool   │
                                            │   (min=2, max=10)│
                                            └────────┬─────────┘
                                                     │
                                                     ▼
                                            ┌──────────────────┐
                                            │   PostgreSQL 16  │
                                            │   (Railway)      │
                                            └──────────────────┘
```

### 6.2 Sequence diagram — boot

```
__main__.py startup:
  config = Config()                            # M2
  db = await Database.connect(config.database_url)
       │
       │  1. asyncpg.create_pool(dsn, min=2, max=10, command_timeout=30)
       │     └── may raise DatabaseConnectionError (DSN bad, host down, etc.)
       │
       │  2. pool.acquire() → conn.transaction() → conn.execute(migration_sql)
       │     └── bubbles up any asyncpg error if migration fails
       │
       │  3. state_store = StateStore(pool)
       │
       └── return Database(pool, state_store)

  ... app uses db.state_store for all reads/writes ...

  on shutdown (SIGINT/SIGTERM):
  await db.close()    # pool.close(); idempotent
```

### 6.3 Sequence diagram — signal insert (M5 will do this)

```
M5 Telegram listener:
  parsed = parse_signal(text, ...)
  signal = build_signal(parsed, source_msg_id, source_chat_id, tz, ...)

  inserted = await db.state_store.upsert_signal(signal)
  if not inserted:
      log.info("duplicate signal, ignoring: %s", signal.signal_id)
      return
  log.info("new signal: %s", signal.signal_id)
```

### 6.4 Sequence diagram — state transition (M6 will do this)

```
M6 scheduler (call_at fires):
  row = await db.state_store.get_signal(signal_id)         # SignalRow
  current = SignalState.from_signal(...)                   # M2 in-memory
  result = transition(current, FireEvent(now), config)    # M2 pure fn

  if result.success:
      await db.state_store.update_signal_state(
          signal_id,
          result.new_state.state,
          error_reason=result.new_state.error_reason,
          updated_at_unix=event.now_unix,
      )
      if result.new_state.state.startswith("placed_"):
          trade_id = await db.state_store.record_stage_placed(
              signal_id, result.new_state.stage,
              pair=signal.pair, direction=signal.direction,
              amount=result.new_state.amount,
              placed_at_unix=result.new_state.trigger_unix,
              expires_at_unix=result.new_state.expires_at_unix,
          )
          # hand trade_id to broker.wait_result
```

### 6.5 Follow-on for M6 (out of M4 scope)

M6 will need a small helper to reconstruct a `SignalState` from a `SignalRow` loaded by `get_signal()` or `get_active_signals()`. The conversion is straightforward but lives in M6's domain code, not M4's `db_rows.py` (which has no `Config` dependency). Sketch:

```python
# In signal_copier/domain/state.py (added by M6):
@classmethod
def from_signal_row(cls, row: SignalRow, config: Config) -> SignalState:
    """Reconstruct SignalState from a SignalRow + persisted stage rows.

    The "current stage" and "cumulative PnL" are derived from the latest
    `stages` row(s) for this signal_id. M6 owns this logic because M2's
    state machine is unaware of M4's persistence.
    """
    ...
```

This is documented here so M6's plan knows it needs to ship the helper; M4 does not implement it.

### 6.6 Error handling — `DatabaseConnectionError`

The only domain exception `Database.connect()` raises. Caught at the `__main__.py` top level:

```python
try:
    db = await Database.connect(config.database_url)
except DatabaseConnectionError as exc:
    logger.critical(str(exc))    # redacted DSN, actionable hint
    sys.exit(2)                  # distinct from signal-processing exit codes
```

The error message:
- Names the redacted DSN (no password leak)
- Points to `docs/PRD.md §17.5` (Railway provisioning)
- Includes the underlying asyncpg exception type and message (truncated to one line for log readability)

Other asyncpg errors during `connect()` (e.g., migration SQL syntax error) **bubble up untouched** — the developer needs the original asyncpg message + the failing SQL to fix the bug. Wrapping would hide the SQL.

### 6.7 Error handling — `StageAlreadyExistsError`

Raised only by `record_stage_placed()` when the deterministic `trade_id` collides (D-10). This is a programming bug. M6 is the expected caller; M6's contract is that it never calls `record_stage_placed` twice for the same `(signal_id, stage, placed_at_unix)`. M10's restart-recovery logic must check `record_stage_placed` for this exception and treat it as "stage already recorded" rather than a crash:

```python
# M10 sketch (NOT M4):
try:
    trade_id = await store.record_stage_placed(...)
except StageAlreadyExistsError:
    log.info("stage already recorded after restart, skipping")
```

This is documented in M6/M10's spec when those get written. M4's role is just to define and raise the exception cleanly.

### 6.8 Connection-loss recovery

asyncpg's pool auto-reconnects on `pool.acquire()` after a backend-disconnect (the pool detects the dead connection, removes it, opens a new one). PRD §10 confirms: *"Postgres connection lost | asyncpg pool auto-reconnects on next acquire."*

M4's `test_pool_reconnect_after_backend_terminated` test verifies this behavior. The M6 scheduler's call site does **not** need to wrap StateStore calls in retry logic — the pool handles transient errors transparently. Only persistent failures (e.g., the DB is down for >30s) bubble up as `asyncpg.PostgresError`, at which point M6's behavior is to crash and let Railway's restart policy (PRD §10, §17.3) restart the container.

### 6.9 Concurrency

- The asyncpg pool handles connection concurrency (PRD §9.2: "no `asyncio.Lock` needed at the app layer").
- M4's methods acquire one connection at a time. Concurrent `state_store.upsert_signal(...)` calls run on separate connections from the pool.
- `update_daily_summary` is the one place where two concurrent calls could theoretically race; the row-level lock in `INSERT ... ON CONFLICT DO UPDATE` serializes them atomically (verified by `test_update_daily_summary_concurrent`).
- Per-signal serialization (e.g., two `update_signal_state` calls for the same `signal_id` arriving simultaneously) is **M6's responsibility** — the state machine is the orchestrator. M4 only ensures individual SQL operations are atomic; M6 ensures they're ordered.

### 6.10 Logging

M4 uses stdlib `logging` (D-15). M7's loguru setup will route it through loguru sinks.

| Event | Log format | Level |
|---|---|---|
| `Database.connect()` success | `"Database connected (pool min=2 max=10, command_timeout=30s, migration=001_initial applied)"` | INFO |
| `Database.close()` (pool already closed) | `"Database.close: pool already closed or error: %s"` | DEBUG |
| `update_signal_state` 0 rows | `"update_signal_state: no row for signal_id=%s (idempotent no-op)"` | WARNING |
| `record_stage_result` 0 rows | `"record_stage_result: no row for trade_id=%s (late push event?)"` | WARNING |

No INFO log per `upsert_signal` / `update_signal_state` / `record_stage_*` — those happen at high frequency and would flood logs. Callers (M5, M6) add their own INFO logs with `signal_id` correlation.

---

## 7. Test Plan

M4 uses **real PostgreSQL** via testcontainers (D-6). No mocks, no in-memory fakes. The unit tests in `test_db.py` (4 tests) cover pure functions (`_redact_dsn`, `row_to_*` mappers) without needing a DB.

### 7.1 `tests/conftest.py` (NEW)

```python
from collections.abc import AsyncIterator
import pytest_asyncio
from testcontainers.postgres import PostgresContainer

from signal_copier.infra.db import Database


@pytest_asyncio.fixture(scope="session")
async def pg_dsn() -> AsyncIterator[str]:
    """Spin up a real PG 16 container, return its DSN, drop at session end."""
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg.get_connection_url(driver="asyncpg")


@pytest_asyncio.fixture
async def db(pg_dsn: str) -> AsyncIterator[Database]:
    """Fresh Database per test: connect, TRUNCATE all tables, yield, close."""
    database = await Database.connect(pg_dsn)
    try:
        async with database.pool.acquire() as conn:
            await conn.execute(
                "TRUNCATE signals, stages, daily_summary RESTART IDENTITY CASCADE"
            )
        yield database
    finally:
        await database.close()
```

**Notes:**
- Session-scoped `pg_dsn`: container startup is ~1–2s; sharing across tests is the right tradeoff.
- Function-scoped `db`: per-test TRUNCATE is ~1ms; gives clean isolation without per-test transaction complexity.
- `pg.get_connection_url(driver="asyncpg")` returns a `postgresql+asyncpg://...` URL by default; the `driver=` argument strips the `+asyncpg` part so asyncpg can connect directly.

### 7.2 `tests/test_db.py` (NEW)

**Group A — unit tests (no DB, 4 tests):**

| # | Test | Verifies |
|---|---|---|
| 1 | `test_redact_dsn_strips_password_url_form` | `postgresql://u:secret@h:5432/d` → `postgresql://u:***@h:5432/d` |
| 2 | `test_redact_dsn_preserves_query_params` | `?sslmode=require` survives redaction |
| 3 | `test_redact_dsn_handles_keyword_form` | `password=secret host=h` → `password=*** host=h` |
| 4 | `test_row_to_daily_summary_row_handles_null_limit_hit` | `None` in column → `None` in dataclass |

**Group B — migration tests (real PG, 3 tests):**

| # | Test | Verifies |
|---|---|---|
| 5 | `test_migrations_create_expected_tables` | after `connect()`, `information_schema.tables` contains `signals`, `stages`, `daily_summary` |
| 6 | `test_migrations_create_expected_indexes` | `pg_indexes` lists the 5 indexes from PRD §9.0 |
| 7 | `test_migrations_are_idempotent` | second `Database.connect()` on same DSN succeeds (no error) |

**Group C — connection-error tests (real PG, expects failure, 2 tests):**

| # | Test | Verifies |
|---|---|---|
| 8 | `test_database_connection_error_on_unreachable_host` | DSN with closed port → `DatabaseConnectionError` |
| 9 | `test_database_connection_error_redacts_password` | error message does NOT contain the password string |

**Group D — StateStore round-trip (real PG, 10 tests):**

| # | Test | Verifies |
|---|---|---|
| 10 | `test_upsert_signal_inserts_new_returns_true` | first call: `True`, row in DB |
| 11 | `test_upsert_signal_duplicate_returns_false` | second call with same `signal_id`: `False`, single row |
| 12 | `test_get_signal_returns_none_for_missing` | unknown `signal_id` → `None` |
| 13 | `test_get_signal_round_trip` | insert, then read back → all fields match |
| 14 | `test_update_signal_state_round_trip` | pending → placed_initial → done_win, status field reflects each step |
| 15 | `test_update_signal_state_warns_on_missing_signal_id` | unknown `signal_id` → log warning, no exception |
| 16 | `test_record_stage_placed_returns_deterministic_trade_id` | same `(signal_id, stage, placed_at_unix)` twice → same `trade_id` |
| 17 | `test_record_stage_placed_inserts_row_with_all_fields` | DB row matches the dataclass fields |
| 18 | `test_record_stage_placed_raises_on_duplicate` | second call with same `trade_id` → `StageAlreadyExistsError` |
| 19 | `test_record_stage_result_updates_row` | place, then result, then read back → `result`, `pnl`, `closed_at_unix` updated |

**Group E — get_active_signals (real PG, 1 test):**

| # | Test | Verifies |
|---|---|---|
| 20 | `test_get_active_signals_excludes_terminal_states` | mix of `placed_initial` + `done_win` + `error` → only `placed_initial` returned |

**Group F — daily-summary (real PG, 4 tests):**

| # | Test | Verifies |
|---|---|---|
| 21 | `test_update_daily_summary_inserts_new_row` | first call inserts |
| 22 | `test_update_daily_summary_adds_deltas` | two calls with `wins_delta=1` each → row has `wins=2` |
| 23 | `test_update_daily_summary_preserves_limit_hit` | call A sets `limit_hit="loss"`, call B passes `None` → final row still has `limit_hit="loss"` |
| 24 | `test_update_daily_summary_concurrent` | 10 `asyncio.gather` calls with `signals_count_delta=1` each → final row has `signals_count=10` |

**Group G — resilience (real PG, 2 tests):**

| # | Test | Verifies |
|---|---|---|
| 25 | `test_command_timeout_aborts_long_query` | `SET LOCAL statement_timeout=100; SELECT pg_sleep(2)` raises `asyncio.TimeoutError` |
| 26 | `test_pool_reconnect_after_backend_terminated` | acquire conn, `pg_terminate_backend(pid)`, release, next acquire succeeds within 5s |

**Total: 26 tests.**

### 7.3 Mapping tests → M4 verification criteria (PRD §15)

| PRD §15 criterion | Covered by tests |
|---|---|
| "Migrations run idempotently against a test PG (Docker)" | #6, #7 |
| "Round-trip CRUD tested" | #10–#19, #21–#23 |
| "`command_timeout` and connection-loss recovery tested" | #25, #26 |

All three PRD criteria are covered. Tests #1–#4 (unit), #5/#8/#9 (infra), #20 (filter), and #24 (concurrent daily-summary) are quality bars I'm adding because they're cheap and catch the kind of bugs that bite in production.

### 7.4 CI / local-dev runtime

- **Local dev:** developer runs `pytest tests/test_db.py` from the repo root with Docker running. The session-scoped container prints its DSN at startup if `pytest -s` is passed.
- **CI:** requires a Docker daemon. GitHub Actions runners, Railway build env, and most CI providers have one. Documented in the README's "Running tests" section (M11 deliverable).
- **Test runtime budget:** container start + migrations + 26 tests ≈ 8–12 seconds. Well under any reasonable CI timeout.
- **Failure mode if Docker is missing:** `testcontainers` raises `DockerNotFoundError` on first fixture instantiation. The test session fails immediately with a clear message.

---

## 8. Risks & Non-Goals

| # | Risk | Mitigation |
|---|---|---|
| 1 | **`Decimal(str(value))` cast loses precision for very large doubles** (e.g., `1e20`) | M4's money values are bounded by `AMOUNT_INITIAL=2.00` to `AMOUNT_GALE2=8.00`, plus daily PnL bounded by typical account sizes. No value exceeds `1e9`. `Decimal(str(1e9))` is exact. The cast is safe for our domain. |
| 2 | **asyncpg `command_timeout=30` aborts a normal long query** (e.g., a future "daily report" query taking >30s) | M4 has no long-running queries. If a future feature adds one, the timeout is per-method, not global — they can use `conn.execute(sql, timeout=120)` for that one call. |
| 3 | **testcontainers requires Docker daemon running** | Same prerequisite as PRD §9.4's local-dev `docker run`. No new burden. Documented in the test plan. |
| 4 | **`importlib.resources` API is Python 3.9+; we require 3.13** | No version risk — `pyproject.toml` already pins `requires-python = ">=3.13"`. |
| 5 | **Migration file path is hardcoded in `db.py`** (`"migrations/001_initial.sql"`) | A future `migrations/002_*.sql` will need a migration-discovery mechanism. Out of scope for M4; v2's "real migration tool" is the right place. |
| 6 | **`DatabaseConnectionError` only wraps 5 asyncpg/stdlib exception types** | Other asyncpg errors (e.g., `InsufficientPrivilegeError`, `ReadOnlySqlTransactionError`) bubble up untouched. The 5 wrapped types cover the "cannot reach DB at all" cases per PRD §10; the unwrapped ones are "DB is reachable but query failed" which the developer needs to see raw. |
| 7 | **`StageAlreadyExistsError` is a domain exception, not asyncpg** | This is the only place we deviate from "bubble up asyncpg untouched" (D-5, D-10). Justified because the deterministic trade_id derivation makes a duplicate a true programming bug, not a DB constraint violation. The exception is specific to the StateStore contract. |
| 8 | **`update_daily_summary` is the only UPSERT** | The `ON CONFLICT (date) DO UPDATE` with additive deltas is the simplest correct way to handle concurrent updates. The alternative (read-modify-write inside a `SERIALIZABLE` transaction) is more code and slower. The chosen approach is verified by `test_update_daily_summary_concurrent` (24-way contention). |
| 9 | **M4 doesn't validate that `state` value is in M2's `AllStates` enum before `UPDATE`** | The DB CHECK constraint catches invalid states (PRD §9.0). If an M6 caller passes an unknown string, asyncpg raises `CheckViolationError` — bubbles up, M6 handles. M4's `update_signal_state` signature uses `AllStates` for mypy enforcement, but the DB is the authoritative enforcer. |
| 10 | **Connection-loss recovery test (#26) is timing-sensitive** | The test polls for 5s; asyncpg's pool typically reconnects in <100ms. If the test flakes, the threshold can be raised. Documented in the test docstring. |

---

## 9. Out of Scope (explicit non-goals for M4)

- **Wiring `state_store` into M5/M6/M8/M10** — those milestones' concern.
- **Daily-limit enforcement logic** — M6 reads `get_daily_summary()` and decides whether to halt; M4 just persists.
- **Schema migrations beyond `001_initial.sql`** — v2's "real migration tool" (e.g., `yoyo-migrations` per PRD §9.2).
- **`NUMERIC(12,4)` migration for money fields** — would replace `DOUBLE PRECISION` and the `Decimal(str(value))` cast; v2 if/when financial-grade precision is needed.
- **Read replicas, connection-level sharding, multi-tenant** — single-channel personal tool, no scale concerns.
- **Soft-delete or audit-log table** — PRD §9.0's `ON DELETE CASCADE` is the only delete behavior; v1 production code never deletes.
- **Index tuning beyond PRD §9.0's 5 indexes** — premature; the schema is small and the query patterns are simple.
- **Connection pool metrics (Prometheus exporter etc.)** — Railway's dashboard gives basic container metrics; v2 if needed.
- **Migration rollback** — v1 has no down-migration path. Idempotent `CREATE TABLE IF NOT EXISTS` only.

---

## 10. Definition of Done for M4

- [ ] All 8 files created/modified per §3
- [ ] `migrations/001_initial.sql` is byte-identical to PRD §9.0 lines 421–476
- [ ] `pytest tests/test_db.py` passes all 26 tests
- [ ] Docker daemon is running; testcontainers spins up `postgres:16-alpine` cleanly
- [ ] `mypy --strict src/signal_copier/infra/` passes (D-4, D-15 enforced)
- [ ] `ruff check src/signal_copier/infra/ tests/test_db.py tests/conftest.py` passes
- [ ] `ruff format --check` on the same files passes
- [ ] `python -c "from signal_copier.infra.db import Database, DatabaseConnectionError; from signal_copier.infra.state_store import StateStore, StageAlreadyExistsError; from signal_copier.infra.db_rows import SignalRow, StageRow, DailySummaryRow; print('imports OK')"` succeeds
- [ ] Manual verification: `docker run -d --name m4-pg -p 5432:5432 -e POSTGRES_USER=copier -e POSTGRES_PASSWORD=copier -e POSTGRES_DB=copier postgres:16-alpine && export DATABASE_URL=postgresql://copier:copier@localhost:5432/copier && python -c "import asyncio; from signal_copier.infra.db import Database; db = asyncio.run(Database.connect('postgresql://copier:copier@localhost:5432/copier')); print('connect OK'); asyncio.run(db.close())"` succeeds end-to-end
- [ ] `pyproject.toml` declares `asyncpg` as a runtime dep and `testcontainers[postgresql]` as a dev dep
- [ ] `migrations/001_initial.sql` ships in the wheel (verified by `python -c "from importlib.resources import files; print(files('signal_copier').joinpath('migrations/001_initial.sql').read_text()[:100])"` succeeding after `pip install -e .`)

---

## 11. References

- PRD §4.5 — Result Monitor & Gale State Machine (where `StageResult`, `AllStates` originate)
- PRD §6 — Tech Stack (`asyncpg`, loguru deferred to M7, `pytest`+`pytest-asyncio`)
- PRD §7 — Architecture (`infra/db.py` package layout, vendored `olymptrade_ws` exclusion)
- PRD §9.0 — Schema DDL (3 tables, 5 indexes — verbatim source for `migrations/001_initial.sql`)
- PRD §9.1 — Field semantics (CHECK constraints, error_reason enum, signal_id derivation)
- PRD §9.2 — Connection & transaction model (pool config, transaction wrapping, idempotent `ON CONFLICT DO NOTHING`)
- PRD §9.4 — Local development without Railway (Docker runbook for PG)
- PRD §10 — Error handling (`DatabaseConnectionError` semantics, asyncpg pool auto-reconnect, idempotent migration, halt-and-DM on persistent failure)
- PRD §15 — Build plan, M4 row (verification criteria: idempotent migrations, round-trip CRUD, command_timeout, connection-loss recovery)
- PRD §17.5 — Railway Postgres provisioning (referenced in `DatabaseConnectionError` message)
- M2 spec `docs/superpowers/specs/2026-06-19-m2-state-machine-design.md` — `AllStates`, `StageResult`, `ErrorReason` type definitions
- M3 spec `docs/superpowers/specs/2026-06-20-m3-broker-protocol-design.md` — D-1 `StageResult` reuse, D-2 `Decimal` money type alignment
- asyncpg docs: [connection pool](https://magicstack.github.io/asyncpg/current/api/index.html#connection-pools), [exceptions](https://magicstack.github.io/asyncpg/current/api/index.html#exceptions)
- testcontainers-python docs: [PostgresContainer](https://github.com/testcontainers/testcontainers-python)
- Python docs: [`importlib.resources`](https://docs.python.org/3/library/importlib.resources.html)
