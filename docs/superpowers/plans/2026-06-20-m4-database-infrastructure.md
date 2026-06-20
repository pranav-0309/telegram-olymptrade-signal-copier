# M4 — Database Infrastructure & StateStore Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the persistence layer for the Telegram → OlympTrade Signal Copier — an asyncpg connection pool, an idempotent migration runner, and a `StateStore` with 8 lifecycle-oriented methods, all backed by real PostgreSQL and tested via testcontainers.

**Architecture:** One `Database` class owns the asyncpg pool and runs the initial migration in `connect()`. A stateless `StateStore` (constructed in `Database.connect()`) exposes 8 methods. Row types are frozen dataclasses in `infra/db_rows.py`. asyncpg exceptions bubble up untouched, with two domain exceptions (`DatabaseConnectionError`, `StageAlreadyExistsError`) at well-defined boundaries.

**Tech Stack:** Python 3.13, asyncpg 0.30+, testcontainers[postgresql] 4.8+, pytest + pytest-asyncio (asyncio_mode="auto", already configured in M0). Real PostgreSQL 16 (testcontainer) — no mocks.

**Reference spec:** `docs/superpowers/specs/2026-06-20-m4-database-infrastructure-design.md` — refer to it for design rationale, alternatives considered, and PRD cross-references.

---

## File Structure

Files created and modified by this plan:

| # | Path | Status | Responsibility |
|---|---|---|---|
| 1 | `pyproject.toml` | MODIFY | Add `asyncpg` runtime dep + `testcontainers[postgresql]` dev dep + `force-include` for migrations |
| 2 | `migrations/001_initial.sql` | NEW | DDL for `signals`, `stages`, `daily_summary` tables + 5 indexes (verbatim from PRD §9.0) |
| 3 | `tests/conftest.py` | NEW | `pg_dsn` (session-scoped testcontainer) + `db` (function-scoped, TRUNCATE) fixtures |
| 4 | `src/signal_copier/infra/db_rows.py` | NEW | `SignalRow`, `StageRow`, `DailySummaryRow` frozen dataclasses + `row_to_*` mappers |
| 5 | `src/signal_copier/infra/db.py` | NEW | `Database` class + `DatabaseConnectionError` + `_redact_dsn` + `_load_migration_sql` |
| 6 | `src/signal_copier/infra/state_store.py` | NEW | `StateStore` class with 8 methods + `StageAlreadyExistsError` |
| 7 | `tests/test_db.py` | NEW | ~26 tests across 7 groups (unit + integration + resilience) |
| 8 | `src/signal_copier/infra/__init__.py` | UNCHANGED | Stays empty (per spec §4.5) |

The M2 `Config` model and M2 `Signal` / `SignalState` types are imported (read-only) by the new code; no changes to domain layer.

---

## Task Ordering Rationale

**Phase 1 (Tasks 1–4):** Foundation — file changes with no logic. Must exist before any code can run. No tests yet because there's no test fixture yet.

**Phase 2 (Tasks 5–6):** Pure data + pure functions. TDD with no DB needed. Sets up the types the DB layer consumes.

**Phase 3 (Task 7):** The `Database` class — uses the `pg_dsn` fixture from Task 4. TDD with real PG. This is the prerequisite for all StateStore work.

**Phase 4 (Tasks 8–13):** StateStore methods. TDD with the `db` fixture from Task 4. One method per task (or a small group, for tightly-coupled read/write pairs). Each task is a real unit of behavior with its own commit.

**Phase 5 (Tasks 14–15):** Resilience tests (command_timeout, connection-loss) + final verification.

---

## Task 1: Add `asyncpg` runtime dependency

**Files:**
- Modify: `pyproject.toml:9-12`

- [ ] **Step 1: Edit `pyproject.toml` to add `asyncpg`**

Open `pyproject.toml`. Find the `dependencies` block (lines 9–12):

```toml
dependencies = [
    "pydantic-settings>=2.6",  # M2: config layer (D-3)
    "tzdata>=2024.1",          # IANA tz database on Windows; no-op on Linux/macOS
]
```

Replace it with:

```toml
dependencies = [
    "pydantic-settings>=2.6",  # M2: config layer (D-3)
    "tzdata>=2024.1",          # IANA tz database on Windows; no-op on Linux/macOS
    "asyncpg>=0.30",           # M4: async-native PostgreSQL driver (PRD §6, R-13)
]
```

- [ ] **Step 2: Install the new dep**

Run: `uv sync`
Expected: `asyncpg` added to `.venv`; lockfile updated. No errors.

- [ ] **Step 3: Verify import works**

Run: `python -c "import asyncpg; print(asyncpg.__version__)"`
Expected: prints a version like `0.30.0` (or higher 0.30.x).

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "M4: add asyncpg runtime dependency"
```

---

## Task 2: Add `testcontainers[postgresql]` dev dependency

**Files:**
- Modify: `pyproject.toml:25-32`

- [ ] **Step 1: Edit `pyproject.toml` to add `testcontainers[postgresql]` to dev-dependencies**

Open `pyproject.toml`. Find the `[tool.uv] dev-dependencies` block (lines 25–32):

```toml
[tool.uv]
dev-dependencies = [
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
    "pytest-cov>=5.0",
    "ruff>=0.7",
    "mypy>=1.13",
    "pre-commit>=4.0",
]
```

Replace it with:

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

- [ ] **Step 2: Install the new dev dep**

Run: `uv sync`
Expected: `testcontainers` + `testcontainers[postgresql]` deps added. No errors.

- [ ] **Step 3: Verify import works**

Run: `python -c "from testcontainers.postgres import PostgresContainer; print('OK')"`
Expected: prints `OK`.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "M4: add testcontainers[postgresql] dev dependency"
```

---

## Task 3: Add `force-include` for migrations in `pyproject.toml`

**Files:**
- Modify: `pyproject.toml:21-22`

This is required so the migration SQL file ships inside the built wheel and `importlib.resources` can find it at runtime.

- [ ] **Step 1: Edit `pyproject.toml` to add `force-include`**

Open `pyproject.toml`. Find the `[tool.hatch.build.targets.wheel]` block (lines 21–22):

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/signal_copier"]
```

Replace it with:

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/signal_copier"]

[tool.hatch.build.targets.wheel.force-include]
"migrations" = "signal_copier/migrations"
```

- [ ] **Step 2: Verify the section parses**

Run: `python -c "import tomllib; print(tomllib.loads(open('pyproject.toml').read())['tool']['hatch']['build']['targets']['wheel']['force-include'])"`
Expected: prints `{'migrations': 'signal_copier/migrations'}`.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "M4: force-include migrations directory in wheel"
```

---

## Task 4: Create `migrations/001_initial.sql`

**Files:**
- Create: `migrations/001_initial.sql`

The DDL is verbatim from PRD §9.0 (lines 421–476). All 3 tables and 5 indexes are idempotent (`CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`), so re-running on every boot is safe.

- [ ] **Step 1: Create the file**

Create `migrations/001_initial.sql` with this exact content:

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

- [ ] **Step 2: Verify the file was created**

Run: `Get-Content migrations/001_initial.sql | Measure-Object -Line` (PowerShell)
Or: `wc -l migrations/001_initial.sql` (bash)
Expected: 60 lines.

- [ ] **Step 3: Remove `.gitkeep` from migrations (no longer needed)**

The `migrations/` directory is no longer empty, so the `.gitkeep` file is no longer needed.

Run: `Remove-Item migrations/.gitkeep`
Verify: `Get-ChildItem migrations` should show only `001_initial.sql`.

- [ ] **Step 4: Commit**

```bash
git add migrations/001_initial.sql migrations/.gitkeep
git commit -m "M4: add 001_initial.sql migration (signals, stages, daily_summary)"
```

---

## Task 5: Create `tests/conftest.py` with testcontainers `pg_dsn` fixture

**Files:**
- Create: `tests/conftest.py`

This task sets up the test infrastructure that all subsequent integration tests depend on. It needs Docker running locally.

- [ ] **Step 1: Verify Docker is running**

Run: `docker version --format '{{.Server.Version}}'`
Expected: prints a Docker server version (e.g., `24.0.7`). If this fails, start Docker Desktop or your Docker daemon and try again.

- [ ] **Step 2: Create `tests/conftest.py`**

Create `tests/conftest.py` with this content:

```python
from __future__ import annotations

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

- [ ] **Step 3: Verify pytest collects the fixtures (no errors yet)**

Run: `pytest tests/conftest.py --collect-only 2>&1 | Select-Object -First 20`
Expected: pytest reports "no tests ran" or similar, but does NOT error out. The `Database` import will fail because `db.py` doesn't exist yet — that's expected; the import is inside the fixture body so it only fails when a test actually requests `db`.

(You can confirm collection works by running: `pytest --collect-only tests/test_main.py` which should still pass since test_main.py doesn't use the new fixtures.)

- [ ] **Step 4: Commit**

```bash
git add tests/conftest.py
git commit -m "M4: add testcontainers pg_dsn + db fixtures to conftest.py"
```

---

## Task 6: Create `src/signal_copier/infra/db_rows.py` with row dataclasses + mappers (TDD)

**Files:**
- Create: `src/signal_copier/infra/db_rows.py`
- Create: `tests/test_db.py` (stub; full file in later tasks)

These are pure-data types with no DB connection. TDD-friendly. The mappers take an `asyncpg.Record` (dict-like) and return a frozen dataclass. Money fields are stored as `DOUBLE PRECISION` in the DB; the mapper casts `float → Decimal(str(value))` to avoid float-precision drift (per spec D-7).

- [ ] **Step 1: Write the failing test**

Create `tests/test_db.py` with this initial content (we'll add more tests in later tasks):

```python
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from signal_copier.infra.db_rows import (
    DailySummaryRow,
    SignalRow,
    StageRow,
    row_to_daily_summary_row,
    row_to_signal_row,
    row_to_stage_row,
)


def _record(**fields: Any) -> Any:
    """Build a minimal asyncpg.Record-like object for mapper tests.

    asyncpg.Record supports both dict-style and attribute-style access. We
    only need dict-style for the mappers, so a plain object with __getitem__
    is sufficient.
    """

    class _R:
        def __getitem__(self, key: str) -> Any:
            return fields[key]

    return _R()


def test_row_to_signal_row_maps_all_fields() -> None:
    record = _record(
        signal_id="abc123def456",
        pair="EUR/JPY",
        broker_pair="EURJPY",
        broker_category="digital",
        direction="down",
        trigger_hhmm="10:20",
        trigger_ts_unix=1_700_000_000.0,
        expiration_seconds=300,
        received_at_unix=1_699_999_900.0,
        source_message_id=42,
        source_chat_id=-1001234567890,
        raw_text="💰5-minute expiration\nEUR/JPY;10:20;PUT🟥",
        status="pending",
        error_reason=None,
        created_at_unix=1_699_999_900.0,
        updated_at_unix=1_699_999_900.0,
    )
    row = row_to_signal_row(record)
    assert row == SignalRow(
        signal_id="abc123def456",
        pair="EUR/JPY",
        broker_pair="EURJPY",
        broker_category="digital",
        direction="down",
        trigger_hhmm="10:20",
        trigger_ts_unix=1_700_000_000.0,
        expiration_seconds=300,
        received_at_unix=1_699_999_900.0,
        source_message_id=42,
        source_chat_id=-1001234567890,
        raw_text="💰5-minute expiration\nEUR/JPY;10:20;PUT🟥",
        status="pending",
        error_reason=None,
        created_at_unix=1_699_999_900.0,
        updated_at_unix=1_699_999_900.0,
    )


def test_row_to_stage_row_converts_amount_float_to_decimal_via_str() -> None:
    record = _record(
        trade_id="abc1234567890123",
        signal_id="sig1",
        stage="initial",
        pair="EUR/JPY",
        direction="down",
        amount=2.0,  # float in, Decimal("2.0") out
        placed_at_unix=1_700_000_000.0,
        expires_at_unix=1_700_000_300.0,
        closed_at_unix=None,
        pnl=None,
        result="open",
        broker_trade_id="broker-123",
    )
    row = row_to_stage_row(record)
    assert row.amount == Decimal("2.0")
    assert row.pnl is None
    assert row.closed_at_unix is None


def test_row_to_stage_row_converts_pnl_float_to_decimal() -> None:
    record = _record(
        trade_id="t1", signal_id="s1", stage="initial", pair="EUR/JPY",
        direction="down", amount=2.0, placed_at_unix=1.0, expires_at_unix=2.0,
        closed_at_unix=300.0, pnl=1.84, result="win", broker_trade_id="b1",
    )
    row = row_to_stage_row(record)
    assert row.pnl == Decimal("1.84")
    assert row.result == "win"


def test_row_to_daily_summary_row_handles_null_limit_hit() -> None:
    record = _record(
        date=date(2026, 6, 20),
        signals_count=5, trades_count=10, wins=7, losses=3,
        realized_pnl=12.84, limit_hit=None,
    )
    row = row_to_daily_summary_row(record)
    assert row == DailySummaryRow(
        date=date(2026, 6, 20),
        signals_count=5, trades_count=10, wins=7, losses=3,
        realized_pnl=Decimal("12.84"),
        limit_hit=None,
    )
```

- [ ] **Step 2: Run the tests to verify they fail (module not found)**

Run: `pytest tests/test_db.py -v`
Expected: `ModuleNotFoundError: No module named 'signal_copier.infra.db_rows'`. The 4 tests fail at import time.

- [ ] **Step 3: Implement `db_rows.py`**

Create `src/signal_copier/infra/db_rows.py` with this content:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from signal_copier.domain.gale import Stage
from signal_copier.domain.state import AllStates, ErrorReason, StageResult

Direction = Literal["up", "down"]


@dataclass(frozen=True, slots=True)
class SignalRow:
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
    date: date
    signals_count: int
    trades_count: int
    wins: int
    losses: int
    realized_pnl: Decimal
    limit_hit: str | None  # NULL | 'loss' | 'count' | 'drawdown'


def row_to_signal_row(record: Any) -> SignalRow:
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


def row_to_stage_row(record: Any) -> StageRow:
    return StageRow(
        trade_id=record["trade_id"],
        signal_id=record["signal_id"],
        stage=record["stage"],
        pair=record["pair"],
        direction=record["direction"],
        amount=Decimal(str(record["amount"])),
        placed_at_unix=record["placed_at_unix"],
        expires_at_unix=record["expires_at_unix"],
        closed_at_unix=record["closed_at_unix"],
        pnl=Decimal(str(record["pnl"])) if record["pnl"] is not None else None,
        result=record["result"],
        broker_trade_id=record["broker_trade_id"],
    )


def row_to_daily_summary_row(record: Any) -> DailySummaryRow:
    return DailySummaryRow(
        date=record["date"],
        signals_count=record["signals_count"],
        trades_count=record["trades_count"],
        wins=record["wins"],
        losses=record["losses"],
        realized_pnl=Decimal(str(record["realized_pnl"])),
        limit_hit=record["limit_hit"],
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_db.py -v`
Expected: 4 tests pass.

- [ ] **Step 5: Run mypy on the new file**

Run: `mypy --strict src/signal_copier/infra/db_rows.py`
Expected: `Success: no issues found in 1 source file`.

- [ ] **Step 6: Commit**

```bash
git add src/signal_copier/infra/db_rows.py tests/test_db.py
git commit -m "M4: add row dataclasses and mappers in infra/db_rows.py"
```

---

## Task 7: Add `_redact_dsn` to `src/signal_copier/infra/db.py` (TDD, no DB)

**Files:**
- Create: `src/signal_copier/infra/db.py` (initial version with only `_redact_dsn`)
- Modify: `tests/test_db.py` (add redaction tests)

Pure function, no DB connection needed. TDD-friendly.

- [ ] **Step 1: Add the failing tests for `_redact_dsn`**

Append these tests to `tests/test_db.py` (don't remove the existing 4 tests):

```python
from signal_copier.infra.db import _redact_dsn  # noqa: E402


def test_redact_dsn_strips_password_url_form() -> None:
    assert _redact_dsn("postgresql://user:secret@host:5432/db") == (
        "postgresql://user:***@host:5432/db"
    )


def test_redact_dsn_preserves_query_params() -> None:
    redacted = _redact_dsn(
        "postgresql://user:secret@host:5432/db?sslmode=require&application_name=copier"
    )
    assert redacted == (
        "postgresql://user:***@host:5432/db?sslmode=require&application_name=copier"
    )


def test_redact_dsn_handles_keyword_form() -> None:
    redacted = _redact_dsn("host=h user=u password=secret dbname=d")
    assert redacted == "host=h user=u password=*** dbname=d"


def test_redact_dsn_no_password_unchanged() -> None:
    assert _redact_dsn("postgresql://host:5432/db") == "postgresql://host:5432/db"
```

- [ ] **Step 2: Run the tests to verify they fail (import error)**

Run: `pytest tests/test_db.py -v -k "redact_dsn"`
Expected: `ModuleNotFoundError: No module named 'signal_copier.infra.db'`. The 4 new redaction tests fail at import time.

- [ ] **Step 3: Create `db.py` with `_redact_dsn` only**

Create `src/signal_copier/infra/db.py` with this initial content (we'll add the `Database` class in Task 8):

```python
from __future__ import annotations

import re


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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_db.py -v -k "redact_dsn"`
Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/signal_copier/infra/db.py tests/test_db.py
git commit -m "M4: add _redact_dsn helper to infra/db.py"
```

---

## Task 8: Create `Database` class with `connect()` / `close()` (TDD with real PG)

**Files:**
- Modify: `src/signal_copier/infra/db.py`
- Modify: `tests/test_db.py` (add migration + connection tests)

This is the first task that requires a real PG. The `pg_dsn` fixture from Task 5 spins up a testcontainer; tests use that DSN. Migrations run in `connect()`.

- [ ] **Step 1: Add the failing tests for `Database.connect()` and migration behavior**

Append these tests to `tests/test_db.py`:

```python
import pytest  # noqa: E402

from signal_copier.infra.db import Database, DatabaseConnectionError  # noqa: E402


async def test_migrations_create_expected_tables(db) -> None:
    async with db.pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' ORDER BY table_name"
        )
    table_names = {r["table_name"] for r in rows}
    assert table_names == {"signals", "stages", "daily_summary"}


async def test_migrations_create_expected_indexes(db) -> None:
    async with db.pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname = 'public' ORDER BY indexname"
        )
    index_names = {r["indexname"] for r in rows}
    assert index_names >= {
        "idx_signals_status",
        "idx_signals_trigger_ts",
        "idx_stages_signal_id",
        "idx_stages_placed_at",
        "idx_stages_result",
    }


async def test_migrations_are_idempotent(pg_dsn) -> None:
    # Second connect on the same DSN must succeed (CREATE TABLE IF NOT EXISTS).
    db1 = await Database.connect(pg_dsn)
    try:
        await db1.close()
    finally:
        pass
    db2 = await Database.connect(pg_dsn)
    try:
        # If we get here without error, idempotency is verified.
        assert db2.pool is not None
    finally:
        await db2.close()


async def test_database_connection_error_on_unreachable_host() -> None:
    bad_dsn = "postgresql://nobody:nopass@127.0.0.1:1/nodb"
    with pytest.raises(DatabaseConnectionError) as excinfo:
        await Database.connect(bad_dsn)
    assert "Cannot reach PostgreSQL" in str(excinfo.value)


async def test_database_connection_error_redacts_password() -> None:
    bad_dsn = "postgresql://user:supersecret@127.0.0.1:1/nodb"
    with pytest.raises(DatabaseConnectionError) as excinfo:
        await Database.connect(bad_dsn)
    assert "supersecret" not in str(excinfo.value)
    assert "***" in str(excinfo.value)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_db.py -v -k "migrations or database_connection_error"`
Expected: All 5 tests fail — `Database` class doesn't exist yet, and even if it did, the import would fail.

- [ ] **Step 3: Implement the `Database` class and `DatabaseConnectionError`**

Replace `src/signal_copier/infra/db.py` with this full content:

```python
from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from importlib.resources import files
from typing import ClassVar

import asyncpg

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


_MIGRATION_PACKAGE: ClassVar[str] = "signal_copier"
_MIGRATION_RESOURCE: ClassVar[str] = "migrations/001_initial.sql"


def _redact_dsn(dsn: str) -> str:
    """Replace the password component of a PostgreSQL DSN with `***`.

    Accepts both URL form (postgresql://user:pass@host:port/db) and
    keyword form (host=... user=... password=...). Query string and
    keyword-form parameters are preserved.
    """
    url_match = re.match(
        r"^([\w+.-]+://[^:]+:)([^@]+)(@.*)$", dsn,
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
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(migration_sql)

        _log.info(
            "Database connected (pool min=2 max=10, command_timeout=30s, "
            "migration=001_initial applied)",
        )
        return cls(pool, state_store=None)  # type: ignore[arg-type]

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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_db.py -v -k "migrations or database_connection_error"`
Expected: All 5 tests pass. (Note: `db` fixture also runs these tests because it calls `Database.connect()` internally; that's intentional — both code paths exercise migrations.)

- [ ] **Step 5: Run mypy on the new file**

Run: `mypy --strict src/signal_copier/infra/db.py`
Expected: `Success: no issues found in 1 source file`. (The `state_store: object` placeholder will pass because `object` accepts anything. We'll fix the type in Task 9 when we have the real `StateStore`.)

- [ ] **Step 6: Commit**

```bash
git add src/signal_copier/infra/db.py tests/test_db.py
git commit -m "M4: add Database class with connect/close and migration runner"
```

---

## Task 9: Create `StateStore` skeleton + `upsert_signal` + `get_signal` (TDD)

**Files:**
- Create: `src/signal_copier/infra/state_store.py`
- Modify: `src/signal_copier/infra/db.py` (replace `object` placeholder with real `StateStore` type)
- Modify: `tests/test_db.py`

The `StateStore.__init__` takes a pool. `Database.connect()` constructs a `StateStore` and assigns it to `db.state_store`. The first two methods we ship are `upsert_signal` and `get_signal` — a tight write/read pair that shares the `SignalRow` shape.

- [ ] **Step 1: Add the failing tests for `upsert_signal` and `get_signal`**

Append these tests to `tests/test_db.py`:

```python
from datetime import date as _date  # noqa: E402
from signal_copier.domain.signal import Signal, derive_signal_id, parse_signal  # noqa: E402


def _make_signal(
    pair: str = "EUR/JPY",
    direction: str = "down",
    trigger_hhmm: str = "10:20",
    signal_date: _date | None = None,
) -> Signal:
    """Build a Signal dataclass for tests. trigger_unix_X computed from now."""
    import time
    now = time.time()
    # Trigger at "now + 60s" so it's in the future.
    trigger_initial = now + 60.0
    parsed = type("P", (), {
        "pair": pair, "direction": direction, "trigger_hhmm": trigger_hhmm,
    })()  # minimal stand-in for ParsedSignal
    sid = derive_signal_id(parsed, signal_date=signal_date or _date.today())
    return Signal(
        signal_id=sid,
        pair=pair,
        direction=direction,  # type: ignore[arg-type]
        trigger_hhmm=trigger_hhmm,
        expiration_seconds=300,
        received_at_unix=now,
        source_message_id=42,
        source_chat_id=-1001234567890,
        raw_text="💰5-minute expiration\nEUR/JPY;10:20;PUT🟥",
        trigger_unix_initial=trigger_initial,
        trigger_unix_gale1=trigger_initial + 300.0,
        trigger_unix_gale2=trigger_initial + 600.0,
    )


async def test_upsert_signal_inserts_new_returns_true(db) -> None:
    signal = _make_signal()
    inserted = await db.state_store.upsert_signal(signal)
    assert inserted is True


async def test_upsert_signal_duplicate_returns_false(db) -> None:
    signal = _make_signal()
    first = await db.state_store.upsert_signal(signal)
    second = await db.state_store.upsert_signal(signal)
    assert first is True
    assert second is False


async def test_get_signal_returns_none_for_missing(db) -> None:
    assert await db.state_store.get_signal("nonexistent") is None


async def test_get_signal_round_trip(db) -> None:
    signal = _make_signal()
    await db.state_store.upsert_signal(signal)
    row = await db.state_store.get_signal(signal.signal_id)
    assert row is not None
    assert row.signal_id == signal.signal_id
    assert row.pair == "EUR/JPY"
    assert row.direction == "down"
    assert row.trigger_hhmm == "10:20"
    assert row.status == "pending"
    assert row.error_reason is None
    assert row.expiration_seconds == 300
    assert row.source_message_id == 42
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_db.py -v -k "upsert_signal or get_signal"`
Expected: All 4 tests fail (likely `ImportError` for `state_store`, then `AttributeError` for `db.state_store`).

- [ ] **Step 3: Create `state_store.py` with `upsert_signal` and `get_signal` only**

Create `src/signal_copier/infra/state_store.py` with this initial content (more methods added in later tasks):

```python
from __future__ import annotations

import logging
from typing import Any

import asyncpg

from signal_copier.domain.signal import Signal
from signal_copier.infra.db_rows import SignalRow, row_to_signal_row

_log = logging.getLogger(__name__)


class StateStore:
    """CRUD API over the signal_copier PostgreSQL schema.

    All write methods acquire a connection from the pool, run inside a
    transaction, and return. All read methods acquire a connection and
    return. asyncpg exceptions bubble up untouched.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def upsert_signal(self, signal: Signal) -> bool:
        """INSERT the signal; on signal_id conflict, do nothing.

        Returns True if the row was newly inserted, False if it already
        existed.
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

    async def get_signal(self, signal_id: str) -> SignalRow | None:
        """Fetch one signal by id. None if not found."""
        sql = "SELECT * FROM signals WHERE signal_id = $1"
        async with self._pool.acquire() as conn:
            record = await conn.fetchrow(sql, signal_id)
        if record is None:
            return None
        return row_to_signal_row(record)
```

- [ ] **Step 4: Wire `StateStore` into `Database`**

Open `src/signal_copier/infra/db.py`. Replace the `state_store: object` placeholder with the real import and constructor.

Change the import block at the top from:

```python
import asyncpg

_log = logging.getLogger(__name__)
```

to:

```python
import asyncpg

from signal_copier.infra.state_store import StateStore

_log = logging.getLogger(__name__)
```

Change the `Database` class:

```python
class Database:
    pool: asyncpg.Pool
    state_store: object  # StateStore; filled in during M4's later task

    def __init__(self, pool: asyncpg.Pool, state_store: object) -> None:
        self.pool = pool
        self.state_store = state_store
```

to:

```python
class Database:
    pool: asyncpg.Pool
    state_store: StateStore

    def __init__(self, pool: asyncpg.Pool, state_store: StateStore) -> None:
        self.pool = pool
        self.state_store = state_store
```

Change the `connect()` classmethod's return statement:

```python
        return cls(pool, state_store=None)  # type: ignore[arg-type]
```

to:

```python
        state_store = StateStore(pool)
        return cls(pool, state_store)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_db.py -v -k "upsert_signal or get_signal"`
Expected: All 4 new tests pass. (The earlier migration/connection tests should still pass too.)

- [ ] **Step 6: Run mypy on all the new files**

Run: `mypy --strict src/signal_copier/infra/`
Expected: `Success: no issues found in 3 source files`.

- [ ] **Step 7: Commit**

```bash
git add src/signal_copier/infra/state_store.py src/signal_copier/infra/db.py tests/test_db.py
git commit -m "M4: add StateStore with upsert_signal and get_signal"
```

---

## Task 10: Add `update_signal_state` (TDD)

**Files:**
- Modify: `src/signal_copier/infra/state_store.py`
- Modify: `tests/test_db.py`

State-machine transitions. The caller (M6) calls this after computing a new state via `transition()`.

- [ ] **Step 1: Add the failing tests for `update_signal_state`**

Append these tests to `tests/test_db.py`:

```python
async def test_update_signal_state_round_trip(db) -> None:
    signal = _make_signal()
    await db.state_store.upsert_signal(signal)
    # pending → placed_initial
    await db.state_store.update_signal_state(
        signal.signal_id, "placed_initial", updated_at_unix=1.0,
    )
    row = await db.state_store.get_signal(signal.signal_id)
    assert row is not None
    assert row.status == "placed_initial"
    assert row.error_reason is None
    # placed_initial → done_win
    await db.state_store.update_signal_state(
        signal.signal_id, "done_win", updated_at_unix=2.0,
    )
    row = await db.state_store.get_signal(signal.signal_id)
    assert row is not None
    assert row.status == "done_win"


async def test_update_signal_state_with_error_reason(db) -> None:
    signal = _make_signal()
    await db.state_store.upsert_signal(signal)
    await db.state_store.update_signal_state(
        signal.signal_id, "error",
        error_reason="signal_expired", updated_at_unix=1.0,
    )
    row = await db.state_store.get_signal(signal.signal_id)
    assert row is not None
    assert row.status == "error"
    assert row.error_reason == "signal_expired"


async def test_update_signal_state_warns_on_missing_signal_id(db, caplog) -> None:
    import logging
    with caplog.at_level(logging.WARNING):
        await db.state_store.update_signal_state(
            "nonexistent-id", "done_win", updated_at_unix=1.0,
        )
    assert any("no row" in r.message for r in caplog.records)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_db.py -v -k "update_signal_state"`
Expected: All 3 tests fail with `AttributeError: 'StateStore' object has no attribute 'update_signal_state'`.

- [ ] **Step 3: Add `update_signal_state` to `StateStore`**

Open `src/signal_copier/infra/state_store.py`. Add the import at the top:

```python
from signal_copier.domain.state import AllStates, ErrorReason
```

(Add to the existing import line; the final import block will be:)

```python
from signal_copier.domain.signal import Signal
from signal_copier.domain.state import AllStates, ErrorReason
from signal_copier.infra.db_rows import SignalRow, row_to_signal_row
```

Add the method after `get_signal` (and before any future methods):

```python
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
        signal_id doesn't exist, logs a warning and returns.
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_db.py -v -k "update_signal_state"`
Expected: All 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/signal_copier/infra/state_store.py tests/test_db.py
git commit -m "M4: add StateStore.update_signal_state"
```

---

## Task 11: Add `record_stage_placed` with deterministic `trade_id` (TDD)

**Files:**
- Modify: `src/signal_copier/infra/state_store.py`
- Modify: `tests/test_db.py`

The `trade_id` is derived deterministically as `sha1(signal_id|stage|placed_at_unix)[:16]`. Re-running with the same args produces the same ID; this enables restart recovery (M10) and idempotent retries.

- [ ] **Step 1: Add the failing tests for `record_stage_placed`**

Append these tests to `tests/test_db.py`:

```python
from signal_copier.infra.state_store import StateStore, StageAlreadyExistsError  # noqa: E402


async def test_record_stage_placed_returns_deterministic_trade_id(db) -> None:
    signal = _make_signal()
    await db.state_store.upsert_signal(signal)
    tid1 = await db.state_store.record_stage_placed(
        signal.signal_id, "initial",
        pair=signal.pair, direction=signal.direction, amount=_D("2.00"),
        placed_at_unix=1.0, expires_at_unix=301.0,
    )
    # Same args → same ID (computed independently, not from DB)
    expected = StateStore._derive_trade_id(signal.signal_id, "initial", 1.0)
    assert tid1 == expected
    assert len(tid1) == 16


async def test_record_stage_placed_inserts_row_with_all_fields(db) -> None:
    from signal_copier.infra.db_rows import StageRow
    signal = _make_signal()
    await db.state_store.upsert_signal(signal)
    tid = await db.state_store.record_stage_placed(
        signal.signal_id, "initial",
        pair=signal.pair, direction=signal.direction, amount=_D("2.00"),
        placed_at_unix=1_700_000_000.0, expires_at_unix=1_700_000_300.0,
        broker_trade_id="broker-abc",
    )
    # Read it back via raw SQL to confirm the row exists with the right fields.
    async with db.pool.acquire() as conn:
        record = await conn.fetchrow(
            "SELECT * FROM stages WHERE trade_id = $1", tid,
        )
    assert record is not None
    row = row_to_stage_row(record)  # type: ignore[arg-type]
    assert row == StageRow(
        trade_id=tid, signal_id=signal.signal_id, stage="initial",
        pair=signal.pair, direction=signal.direction, amount=_D("2.00"),
        placed_at_unix=1_700_000_000.0, expires_at_unix=1_700_000_300.0,
        closed_at_unix=None, pnl=None, result="open", broker_trade_id="broker-abc",
    )


async def test_record_stage_placed_raises_on_duplicate(db) -> None:
    signal = _make_signal()
    await db.state_store.upsert_signal(signal)
    kwargs = dict(
        pair=signal.pair, direction=signal.direction, amount=_D("2.00"),
        placed_at_unix=1.0, expires_at_unix=301.0,
    )
    await db.state_store.record_stage_placed(
        signal.signal_id, "initial", **kwargs,
    )
    with pytest.raises(StageAlreadyExistsError):
        await db.state_store.record_stage_placed(
            signal.signal_id, "initial", **kwargs,
        )
```

Also add this helper at the top of the test file (near `_make_signal`):

```python
from decimal import Decimal as _D  # noqa: E402
```

(The `_D` is a one-letter alias to keep test code compact.)

And add this import alongside the other `from signal_copier.infra.db_rows import ...`:

```python
from signal_copier.infra.db_rows import row_to_stage_row  # noqa: E402
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_db.py -v -k "record_stage_placed"`
Expected: All 3 tests fail with `AttributeError`.

- [ ] **Step 3: Add `record_stage_placed` and `StageAlreadyExistsError` to `StateStore`**

Open `src/signal_copier/infra/state_store.py`. Replace the import block:

```python
import logging
from typing import Any

import asyncpg

from signal_copier.domain.signal import Signal
from signal_copier.domain.state import AllStates, ErrorReason
from signal_copier.infra.db_rows import SignalRow, row_to_signal_row
```

with:

```python
import hashlib
import logging
from decimal import Decimal
from typing import Any, Literal

import asyncpg

from signal_copier.domain.gale import Stage
from signal_copier.domain.signal import Signal
from signal_copier.domain.state import AllStates, ErrorReason
from signal_copier.infra.db_rows import SignalRow, row_to_signal_row

_log = logging.getLogger(__name__)


class StageAlreadyExistsError(Exception):
    """Raised by StateStore.record_stage_placed() on a trade_id collision.

    This is a programming bug, not a normal runtime event: the deterministic
    trade_id derivation means a duplicate call with the same
    (signal_id, stage, placed_at_unix) is either a caller bug or a
    misbehaving restart-recovery path.
    """
```

Add the method after `update_signal_state`:

```python
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

        Returns the deterministic trade_id:
            sha1(f"{signal_id}|{stage}|{placed_at_unix:.6f}").hexdigest()[:16]
        Raises StageAlreadyExistsError if the same (signal_id, stage, placed_at_unix)
        is recorded twice.
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
                f"(signal_id={signal_id} stage={stage} "
                f"placed_at_unix={placed_at_unix:.6f})"
            )
        return trade_id

    @staticmethod
    def _derive_trade_id(
        signal_id: str, stage: Stage, placed_at_unix: float,
    ) -> str:
        """Deterministic 16-char trade_id."""
        payload = f"{signal_id}|{stage}|{placed_at_unix:.6f}"
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_db.py -v -k "record_stage_placed"`
Expected: All 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/signal_copier/infra/state_store.py tests/test_db.py
git commit -m "M4: add StateStore.record_stage_placed with deterministic trade_id"
```

---

## Task 12: Add `record_stage_result` (TDD)

**Files:**
- Modify: `src/signal_copier/infra/state_store.py`
- Modify: `tests/test_db.py`

- [ ] **Step 1: Add the failing tests for `record_stage_result`**

Append these tests to `tests/test_db.py`:

```python
async def test_record_stage_result_updates_row(db) -> None:
    from signal_copier.infra.db_rows import row_to_stage_row
    signal = _make_signal()
    await db.state_store.upsert_signal(signal)
    tid = await db.state_store.record_stage_placed(
        signal.signal_id, "initial",
        pair=signal.pair, direction=signal.direction, amount=_D("2.00"),
        placed_at_unix=1.0, expires_at_unix=301.0,
    )
    await db.state_store.record_stage_result(
        tid, "win", pnl=_D("1.84"), closed_at_unix=400.0,
    )
    async with db.pool.acquire() as conn:
        record = await conn.fetchrow("SELECT * FROM stages WHERE trade_id = $1", tid)
    assert record is not None
    row = row_to_stage_row(record)  # type: ignore[arg-type]
    assert row.result == "win"
    assert row.pnl == _D("1.84")
    assert row.closed_at_unix == 400.0


async def test_record_stage_result_warns_on_missing_trade_id(db, caplog) -> None:
    import logging
    with caplog.at_level(logging.WARNING):
        await db.state_store.record_stage_result(
            "nonexistent-trade-id", "win",
            pnl=_D("1.84"), closed_at_unix=400.0,
        )
    assert any("no row" in r.message for r in caplog.records)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_db.py -v -k "record_stage_result"`
Expected: Both tests fail with `AttributeError`.

- [ ] **Step 3: Add `record_stage_result` to `StateStore`**

Open `src/signal_copier/infra/state_store.py`. Add the import at the top alongside the other `domain.state` imports:

```python
from signal_copier.domain.state import AllStates, ErrorReason, StageResult
```

Add the method after `record_stage_placed`:

```python
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
        doesn't exist, logs a warning.
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_db.py -v -k "record_stage_result"`
Expected: Both tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/signal_copier/infra/state_store.py tests/test_db.py
git commit -m "M4: add StateStore.record_stage_result"
```

---

## Task 13: Add `get_active_signals`, `update_daily_summary`, `get_daily_summary` (TDD)

**Files:**
- Modify: `src/signal_copier/infra/state_store.py`
- Modify: `tests/test_db.py`

Three methods in one task because they're tightly coupled (the daily-summary UPSERT is one logical operation; the two reads are simple selects).

- [ ] **Step 1: Add the failing tests for all three methods**

Append these tests to `tests/test_db.py`:

```python
async def test_get_active_signals_excludes_terminal_states(db) -> None:
    # 3 signals: one in each kind of state.
    for status in ("placed_initial", "done_win", "error"):
        sig = _make_signal(trigger_hhmm=status)  # vary signal_id
        await db.state_store.upsert_signal(sig)
        await db.state_store.update_signal_state(
            sig.signal_id, status,  # type: ignore[arg-type]
            error_reason="signal_expired" if status == "error" else None,
            updated_at_unix=1.0,
        )
    active = await db.state_store.get_active_signals()
    assert len(active) == 1
    assert active[0].status == "placed_initial"


async def test_update_daily_summary_inserts_new_row(db) -> None:
    today = _date.today()
    await db.state_store.update_daily_summary(
        today, signals_count_delta=1, trades_count_delta=2,
        wins_delta=1, losses_delta=1, realized_pnl_delta=_D("1.84"),
    )
    row = await db.state_store.get_daily_summary(today)
    assert row is not None
    assert row.signals_count == 1
    assert row.trades_count == 2
    assert row.wins == 1
    assert row.losses == 1
    assert row.realized_pnl == _D("1.84")
    assert row.limit_hit is None


async def test_update_daily_summary_adds_deltas(db) -> None:
    today = _date.today()
    await db.state_store.update_daily_summary(today, wins_delta=1)
    await db.state_store.update_daily_summary(today, wins_delta=1)
    row = await db.state_store.get_daily_summary(today)
    assert row is not None
    assert row.wins == 2


async def test_update_daily_summary_preserves_limit_hit(db) -> None:
    today = _date.today()
    await db.state_store.update_daily_summary(today, limit_hit="loss")
    # Second call without limit_hit must NOT clear it.
    await db.state_store.update_daily_summary(today, wins_delta=1)
    row = await db.state_store.get_daily_summary(today)
    assert row is not None
    assert row.limit_hit == "loss"
    assert row.wins == 1


async def test_update_daily_summary_concurrent(db) -> None:
    import asyncio
    today = _date.today()
    await asyncio.gather(*[
        db.state_store.update_daily_summary(today, signals_count_delta=1)
        for _ in range(10)
    ])
    row = await db.state_store.get_daily_summary(today)
    assert row is not None
    assert row.signals_count == 10


async def test_get_daily_summary_returns_none_for_missing(db) -> None:
    assert await db.state_store.get_daily_summary(_date(2020, 1, 1)) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_db.py -v -k "active_signals or daily_summary"`
Expected: All 6 tests fail with `AttributeError`.

- [ ] **Step 3: Add the three methods to `StateStore`**

Open `src/signal_copier/infra/state_store.py`. Update the import block to add the daily-summary row type:

```python
from signal_copier.infra.db_rows import (
    DailySummaryRow,
    SignalRow,
    StageRow,
    row_to_daily_summary_row,
    row_to_signal_row,
    row_to_stage_row,
)
```

Add the methods after `record_stage_result`:

```python
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
        """UPSERT a row in `daily_summary` with additive deltas.

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

    async def get_daily_summary(self, on_date: date) -> DailySummaryRow | None:
        """Fetch one daily summary by date. None if no row yet (clean day)."""
        sql = "SELECT * FROM daily_summary WHERE date = $1"
        async with self._pool.acquire() as conn:
            record = await conn.fetchrow(sql, on_date)
        if record is None:
            return None
        return row_to_daily_summary_row(record)
```

Add the `date` import at the top of the file:

```python
from datetime import date
```

(Add to the import block; the final import block will be:)

```python
import hashlib
import logging
from datetime import date
from decimal import Decimal
from typing import Any, Literal
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_db.py -v -k "active_signals or daily_summary"`
Expected: All 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/signal_copier/infra/state_store.py tests/test_db.py
git commit -m "M4: add get_active_signals, update_daily_summary, get_daily_summary"
```

---

## Task 14: Add resilience tests (command_timeout, connection-loss recovery)

**Files:**
- Modify: `tests/test_db.py`

These tests verify the resilience properties called out in PRD §15's M4 verification criteria.

- [ ] **Step 1: Add the failing tests**

Append these tests to `tests/test_db.py`:

```python
async def test_command_timeout_aborts_long_query(db) -> None:
    # Use a fresh connection with a tight statement_timeout.
    async with db.pool.acquire() as conn:
        await conn.execute("SET LOCAL statement_timeout = 100")
        with pytest.raises((asyncio.TimeoutError, asyncpg.exceptions.QueryCanceledError)):
            await conn.fetch("SELECT pg_sleep(2)")


async def test_pool_reconnect_after_backend_terminated(db) -> None:
    # Acquire a connection, ask PG to kill it, release, then verify the
    # next acquire returns a working connection within 5 seconds.
    async with db.pool.acquire() as conn:
        pid = await conn.fetchval("SELECT pg_backend_pid()")
        # Kill our own backend from a separate connection.
        async with db.pool.acquire() as killer:
            await killer.execute(
                "SELECT pg_terminate_backend($1)", pid,
            )
    # The released connection is now dead. Next acquire must give us a
    # fresh, working connection.
    import asyncio
    async with asyncio.timeout(5.0):
        async with db.pool.acquire() as fresh_conn:
            result = await fresh_conn.fetchval("SELECT 1")
            assert result == 1
```

Add the missing imports at the top of `tests/test_db.py`:

```python
import asyncio
import asyncpg
import asyncpg.exceptions
```

- [ ] **Step 2: Run the tests to verify they pass**

Run: `pytest tests/test_db.py -v -k "command_timeout or reconnect"`
Expected: Both tests pass. (These tests exercise the pool's built-in resilience; they're regression-locks, not new code. They should pass without changes to `state_store.py` or `db.py`.)

If a test fails, the failure is diagnostic: the pool config (`min_size`, `max_size`, `command_timeout`) in `Database.connect()` may need adjustment. The most likely cause of `test_command_timeout_aborts_long_query` flakiness is the `SET LOCAL` not being applied to the same transaction as the `pg_sleep` — re-run the test to check.

- [ ] **Step 3: Commit**

```bash
git add tests/test_db.py
git commit -m "M4: add command_timeout and pool-reconnect resilience tests"
```

---

## Task 15: Final verification

**Files:** (no new files; just verification)

This task runs the full verification suite per the spec's §10 "Definition of Done for M4".

- [ ] **Step 1: Run the full test suite**

Run: `pytest tests/test_db.py -v`
Expected: All tests pass. The output should show 4 mapper tests, 4 redaction tests, 3 migration tests, 2 connection-error tests, 4 upsert/get tests, 3 update-state tests, 3 record-stage-placed tests, 2 record-stage-result tests, 1 get-active-signals test, 5 daily-summary tests, 2 resilience tests = **33 tests in total**.

- [ ] **Step 2: Run the full project test suite (regression check)**

Run: `pytest -v`
Expected: All tests across all milestones pass (parser, state machine, gale math, dry-run broker, broker protocol, config, main, db). No regressions in M0–M3.

- [ ] **Step 3: Run mypy strict on the new code**

Run: `mypy --strict src/signal_copier/infra/`
Expected: `Success: no issues found in 3 source files`.

- [ ] **Step 4: Run ruff on the new code**

Run: `ruff check src/signal_copier/infra/ tests/test_db.py tests/conftest.py`
Expected: `All checks passed!`

Run: `ruff format --check src/signal_copier/infra/ tests/test_db.py tests/conftest.py`
Expected: `N files already formatted` (where N is the number of files; no diff to print).

If formatting diff exists, run `ruff format src/signal_copier/infra/ tests/test_db.py tests/conftest.py` to fix and re-verify.

- [ ] **Step 5: Manual end-to-end smoke test (production-like PG)**

This step requires Docker and exercises the full `Database.connect()` → migration → `StateStore.upsert_signal` → `close()` flow against a real PG, using a DSN that looks like what Railway will inject.

Start a local PG:
```bash
docker run -d --name m4-smoke-pg -p 5432:5432 \
  -e POSTGRES_USER=copier -e POSTGRES_PASSWORD=copier -e POSTGRES_DB=copier \
  postgres:16-alpine
```

Set the DSN:
```bash
$env:DATABASE_URL = "postgresql://copier:copier@localhost:5432/copier"
```

Run the smoke script (PowerShell, from the repo root):
```powershell
$env:DATABASE_URL = "postgresql://copier:copier@localhost:5432/copier"
python -c @'
import asyncio
from signal_copier.infra.db import Database

async def main():
    db = await Database.connect("postgresql://copier:copier@localhost:5432/copier")
    print("connect OK; pool:", db.pool)
    await db.close()
    print("close OK")

asyncio.run(main())
'@
```

Expected output (two lines):
```
connect OK; pool: <asyncpg.pool.Pool ...>
close OK
```

If `connect OK` does NOT print, the most common cause is that the `migrations/` directory is not being included by Hatchling. Re-check Task 3's `force-include` directive and re-run `uv sync` (Hatchling reads `pyproject.toml` at build time; in editable mode it picks up source changes automatically).

Clean up:
```bash
docker stop m4-smoke-pg && docker rm m4-smoke-pg
```

- [ ] **Step 6: Verify the wheel includes the migration file**

This step validates that the production build will ship the migration SQL.

```bash
uv build
```

Expected: builds `dist/signal_copier-0.1.0-py3-none-any.whl` and a tar.gz. No errors.

```bash
python -c "import zipfile; z = zipfile.ZipFile('dist/signal_copier-0.1.0-py3-none-any.whl'); names = [n for n in z.namelist() if 'migration' in n.lower() or '001' in n]; print('\\n'.join(names))"
```

Expected: prints at least one line containing `signal_copier/migrations/001_initial.sql`.

```bash
rm -rf dist build  # PowerShell: Remove-Item -Recurse -Force dist, build
```

- [ ] **Step 7: Commit (no code changes; just a checkpoint tag if desired)**

If all 6 previous steps passed, the milestone is complete. Optionally tag the release:

```bash
git tag -a m4-complete -m "M4 milestone: database infrastructure & StateStore"
```

(No file changes, so no `git add` needed. The tag is informational only.)

- [ ] **Step 8: Update todos**

Update the in-session todo list to mark the M4 plan as complete. The next step in the build plan is M5 (Telegram listener), which is a separate brainstorming + planning cycle.

---

## Self-Review (run after writing, before execution)

This section documents the checks the planner ran against the spec. Listed for traceability; no action required from the executing engineer.

**1. Spec coverage:** Every section in the spec maps to at least one task:

| Spec section | Covered by |
|---|---|
| §4.1 `Database` class + `DatabaseConnectionError` + `_redact_dsn` + `_load_migration_sql` | Tasks 7, 8 |
| §4.2 `SignalRow`, `StageRow`, `DailySummaryRow` + `row_to_*` mappers | Task 6 |
| §4.3 `StateStore` 8 methods + `StageAlreadyExistsError` | Tasks 9, 10, 11, 12, 13 |
| §4.4 `migrations/001_initial.sql` | Task 4 |
| §4.5 `__init__.py` unchanged | (no task — explicitly unchanged) |
| §5 dependency changes (asyncpg, testcontainers, force-include) | Tasks 1, 2, 3 |
| §6.2 `DatabaseConnectionError` semantics | Task 8 |
| §6.7 `StageAlreadyExistsError` semantics | Task 11 |
| §6.8 connection-loss recovery | Task 14 |
| §6.9 concurrency model | Task 14 (concurrent daily-summary test) |
| §6.10 logging | Tasks 8, 10, 12 (warnings on 0-row updates) |
| §7.1 testcontainers fixtures | Task 5 |
| §7.2 26 tests across 7 groups | Tasks 6, 7, 8, 9, 10, 11, 12, 13, 14 |
| §7.4 CI / local-dev runtime | Task 15 (smoke test) |

All 16 M4 verification criteria from spec §10 are exercised.

**2. Placeholder scan:** No TBD / TODO / FIXME / "see spec" / "implement later" in any task step. Every code block is complete; every command has expected output.

**3. Type consistency:**

- `Database.state_store` type: starts as `object` placeholder in Task 8, replaced with `StateStore` import + annotation in Task 9 (Step 4). Tasks 8's mypy passes because `object` is bivariant.
- `StateStore._derive_trade_id` is a `@staticmethod` (per Task 11); called both as `self._derive_trade_id(...)` from `record_stage_placed` and as `StateStore._derive_trade_id(...)` from the test in Task 11. Both work; the staticmethod decorator is consistent.
- `_make_signal` helper uses `direction: str` then casts via `# type: ignore[arg-type]` (Task 9) because the `Signal` dataclass uses `Literal["up", "down"]`. Mypy is not run on `tests/test_db.py` (per `pyproject.toml` test override in M0). The cast is local and the test runs green.
- `Decimal` import in `state_store.py` is added in Task 11 (for `record_stage_placed`); `date` import is added in Task 13 (for daily-summary). No forward references; each task compiles independently after the import is added in that task.
- `row_to_stage_row` import is added in Task 11 (for the test that uses it). Task 12's test reuses the same import. Task 13's tests don't need it.
- `_redact_dsn` and `_load_migration_sql` are module-level functions in `db.py`, not class methods. Imported as `from signal_copier.infra.db import _redact_dsn` in tests. The leading underscore signals "package-private" but Python allows the import.

No type-name or signature drift detected.
