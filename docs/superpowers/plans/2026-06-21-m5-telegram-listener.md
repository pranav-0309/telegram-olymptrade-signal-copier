# M5 — Telegram Listener Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the Telegram input side of the signal-copier pipeline — a Telethon-based listener that watches one channel, parses incoming messages via M1's parser, persists valid signals via M4's `StateStore`, and puts them on an `asyncio.Queue` for M6 (or M5's stub `dump_consumer`) to drain.

**Architecture:** Four new modules under `src/signal_copier/` (a tz/clock helper, a Telegram client wrapper, a listener with handler chain, an auth subcommand) plus minor changes to `__main__.py`, `infra/log.py`, and `pyproject.toml`. The listener's handlers are plain async functions called directly by tests with synthetic Telethon events; no live Telegram connection in unit tests. M5 is end-to-end demoable from `python -m signal_copier` (auth bootstrap via `python -m signal_copier.telegram.auth`).

**Tech Stack:** Python 3.13, telethon 1.44+, pydantic-settings 2.6+, asyncpg 0.30+ (M4), pytest 8.3+ + pytest-asyncio 0.24+ (`asyncio_mode="auto"`, already configured in M0). Existing `Config` and M1 parser / M4 `StateStore` are reused unchanged.

**Reference spec:** `docs/superpowers/specs/2026-06-21-m5-telegram-listener-design.md` — refer to it for design rationale, decisions, and PRD cross-references.

---

## File Structure

Files created and modified by this plan:

| # | Path | Status | Responsibility |
|---|---|---|---|
| 1 | `pyproject.toml` | MODIFY | Add `telethon` runtime dep; add `signal-copier-auth` script; add 3 M5 test modules to mypy override |
| 2 | `src/signal_copier/telegram/__init__.py` | NEW | Empty package marker (no re-exports) |
| 3 | `src/signal_copier/infra/clock.py` | NEW | 5 pure tz/clock helpers: `hhmm_to_unix`, `signal_date_in_tz`, `is_within_window`, `now_unix`, `monotonic` |
| 4 | `src/signal_copier/infra/log.py` | MODIFY | Add `setup_parse_failures_log(log_dir) -> logging.Logger`; keep `setup_logging` as the M2 stub (M7 replaces it) |
| 5 | `src/signal_copier/telegram/client.py` | NEW | `TelegramClient` (Telethon wrapper), `TelegramConfigError`, `compute_backoff_seconds` |
| 6 | `src/signal_copier/telegram/listener.py` | NEW | `Listener` class with `on_new_message` + `on_message_edited` handlers; private `_process_message` is the single source of truth |
| 7 | `src/signal_copier/telegram/auth.py` | NEW | `main()` entrypoint for `python -m signal_copier.telegram.auth`; runs Telethon interactive auth, prints StringSession |
| 8 | `src/signal_copier/__main__.py` | MODIFY | Wire `Config` → `Database.connect` → `TelegramClient` → `Listener` → `dump_consumer` task → `TelegramClient.start()` |
| 9 | `tests/test_clock.py` | NEW | ~10 unit tests for `infra/clock.py` (DST boundaries, time-window tolerances, epoch arithmetic) |
| 10 | `tests/test_log.py` | NEW | ~4 tests for `setup_parse_failures_log` (idempotency, file creation, no propagation) |
| 11 | `tests/_telegram_fixtures.py` | NEW | Shared helpers: `make_event(...)`, `FakeStateStore`, `NullLogger` |
| 12 | `tests/test_telegram_client.py` | NEW | ~6 tests for `compute_backoff_seconds` + `TelegramClient.__init__` validation + reconnect supervisor |
| 13 | `tests/test_telegram_listener.py` | NEW | ~13 tests for `Listener` using synthetic events + `FakeStateStore` + `NullLogger` |
| 14 | `tests/test_auth.py` | NEW | ~4 tests for `telegram.auth.main()` (missing config → 2; auth failure → 1; success path mocked) |
| 15 | `tests/test_main.py` | MODIFY | Add 2 tests for the M5 wiring in `__main__.main()` (config-validation error path; success path with all components mocked) |

The M1 parser (`domain/signal.py`), M2 state machine (`domain/state.py`), M2 config (`config.py`), M3 broker protocol, and M4 `StateStore` are imported (read-only) by the new code; no changes to those layers.

---

## Task Ordering Rationale

**Phase 1 (Tasks 1–3):** Foundation — pyproject.toml changes + empty `telegram/` package. No tests yet because nothing is testable.

**Phase 2 (Task 4):** Pure tz/clock helpers in `infra/clock.py`. TDD, no async, no I/O. Sets the foundation for the Listener's trigger-time computation.

**Phase 3 (Task 5):** Parse-failure logger in `infra/log.py`. TDD-lite, exercises stdlib `FileHandler` + idempotency.

**Phase 4 (Task 6):** Shared test fixtures in `tests/_telegram_fixtures.py`. No TDD — pure test infrastructure used by Tasks 7 and 8.

**Phase 5 (Task 7):** `telegram/client.py` (Telethon wrapper). TDD with mocked `_TelethonClient` from telethon. `compute_backoff_seconds` is a pure function tested in isolation; `__init__` validation and reconnect supervisor tested with `unittest.mock`.

**Phase 6 (Task 8):** `telegram/listener.py`. TDD with synthetic Telethon events + `FakeStateStore` + `NullLogger`. The biggest task in M5 (~13 tests).

**Phase 7 (Task 9):** `telegram/auth.py`. TDD with `monkeypatch` for env vars and `unittest.mock` for the Telethon interactive flow.

**Phase 8 (Task 10):** `__main__.py` wiring. TDD with all components mocked (Database, TelegramClient, Listener). Tests use `asyncio.run` + mock assertions.

**Phase 9 (Task 11):** Lint, type-check, and full test-run verification.

---

## Task 1: Add `telethon` runtime dependency

**Files:**
- Modify: `pyproject.toml:9-13`

- [ ] **Step 1: Edit `pyproject.toml` to add `telethon`**

Open `pyproject.toml`. Find the `dependencies` block (lines 9–13):

```toml
dependencies = [
    "pydantic-settings>=2.6",  # M2: config layer (D-3)
    "tzdata>=2024.1",          # IANA tz database on Windows; no-op on Linux/macOS
    "asyncpg>=0.30",           # M4: async-native PostgreSQL driver (PRD §6, R-13)
]
```

Replace it with:

```toml
dependencies = [
    "pydantic-settings>=2.6",  # M2: config layer
    "tzdata>=2024.1",          # IANA tz database on Windows
    "asyncpg>=0.30",           # M4: async PostgreSQL driver
    "telethon>=1.44",          # M5: Telegram MTProto user-account client
]
```

- [ ] **Step 2: Install the new dep**

Run: `uv sync`
Expected: `telethon` added to `.venv`; lockfile updated. No errors.

- [ ] **Step 3: Verify import works**

Run: `python -c "import telethon; from telethon.sessions import StringSession; from telethon.errors import FloodWaitError; from telethon.events import NewMessage, MessageEdited; print(telethon.__version__)"`
Expected: prints a version like `1.44.0` (or higher 1.44.x).

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "M5: add telethon runtime dependency"
```

---

## Task 2: Create empty `telegram/` package

**Files:**
- Create: `src/signal_copier/telegram/__init__.py`

- [ ] **Step 1: Create the file**

Create `src/signal_copier/telegram/__init__.py` with this exact content:

```python
# Empty. Callers import from submodules:
#   from signal_copier.telegram.client import TelegramClient, TelegramConfigError
#   from signal_copier.telegram.listener import Listener
#   from signal_copier.telegram.auth import main
#
# No top-level re-exports — the package is a namespace, not a facade.
# Matches the M4 convention in src/signal_copier/infra/__init__.py.
```

- [ ] **Step 2: Verify the package is importable**

Run: `python -c "import signal_copier.telegram; print('OK')"`
Expected: prints `OK`.

- [ ] **Step 3: Commit**

```bash
git add src/signal_copier/telegram/__init__.py
git commit -m "M5: create empty telegram/ package"
```

---

## Task 3: Add `signal-copier-auth` script entry point

**Files:**
- Modify: `pyproject.toml:15-16`

- [ ] **Step 1: Edit `pyproject.toml` to add the auth script**

Open `pyproject.toml`. Find the `[project.scripts]` block (lines 15–16):

```toml
[project.scripts]
signal-copier = "signal_copier.__main__:main"
```

Replace it with:

```toml
[project.scripts]
signal-copier      = "signal_copier.__main__:main"
signal-copier-auth = "signal_copier.telegram.auth:main"
```

- [ ] **Step 2: Verify the section parses**

Run: `python -c "import tomllib; print(tomllib.loads(open('pyproject.toml').read())['project']['scripts'])"`
Expected: prints `{'signal-copier': 'signal_copier.__main__:main', 'signal-copier-auth': 'signal_copier.telegram.auth:main'}`.

(The script won't actually run until Task 9 creates `signal_copier.telegram.auth`. That's fine — pytest doesn't import scripts.)

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "M5: add signal-copier-auth script entry point"
```

---

## Task 4: Implement `infra/clock.py` with tests (TDD)

**Files:**
- Create: `src/signal_copier/infra/clock.py`
- Create: `tests/test_clock.py`

- [ ] **Step 1: Write the failing test file**

Create `tests/test_clock.py` with this exact content:

```python
from __future__ import annotations

import time
from datetime import date
from zoneinfo import ZoneInfo

import pytest

from signal_copier.infra.clock import (
    hhmm_to_unix,
    is_within_window,
    monotonic,
    now_unix,
    signal_date_in_tz,
)

# --- hhmm_to_unix ----------------------------------------------------------


def test_hhmm_to_unix_happy_path() -> None:
    # America/Sao_Paulo is UTC-3 (DST-free since 2019).
    tz = ZoneInfo("America/Sao_Paulo")
    # 10:20 on 2026-06-20 in UTC-3 is 13:20 UTC.
    # 2026-06-20 13:20:00 UTC = epoch 1782015600.
    assert hhmm_to_unix("10:20", date(2026, 6, 20), tz) == pytest.approx(
        1782015600.0, abs=0.001
    )


def test_hhmm_to_unix_invalid_format_raises() -> None:
    tz = ZoneInfo("America/Sao_Paulo")
    with pytest.raises(ValueError):
        hhmm_to_unix("25:00", date(2026, 6, 20), tz)
    with pytest.raises(ValueError):
        hhmm_to_unix("10:99", date(2026, 6, 20), tz)
    with pytest.raises(ValueError):
        hhmm_to_unix("abc", date(2026, 6, 20), tz)
    with pytest.raises(ValueError):
        hhmm_to_unix("", date(2026, 6, 20), tz)


def test_hhmm_to_unix_at_midnight() -> None:
    tz = ZoneInfo("America/Sao_Paulo")
    # 00:00 on 2026-06-20 in UTC-3 is 03:00 UTC.
    assert hhmm_to_unix("00:00", date(2026, 6, 20), tz) == pytest.approx(
        1782001200.0, abs=0.001
    )
    # 23:59 on 2026-06-20 in UTC-3 is 02:59 UTC next day.
    assert hhmm_to_unix("23:59", date(2026, 6, 20), tz) == pytest.approx(
        1782089940.0, abs=0.001
    )


def test_hhmm_to_unix_across_dst_spring_forward() -> None:
    # America/New_York: DST starts 2026-03-08 02:00 -> 03:00.
    # At 02:30 on that day, local time doesn't exist; zoneinfo
    # resolves to 03:30 (one hour later).
    tz = ZoneInfo("America/New_York")
    # 03:30 on 2026-03-08 in UTC-4 (EDT) is 07:30 UTC.
    skipped = hhmm_to_unix("02:30", date(2026, 3, 8), tz)
    expected_03_30 = hhmm_to_unix("03:30", date(2026, 3, 8), tz)
    assert skipped == pytest.approx(expected_03_30, abs=0.001)


def test_hhmm_to_unix_across_date_line() -> None:
    # Asia/Tokyo is UTC+9 (no DST). 23:30 in Tokyo on date X is 14:30 UTC
    # on date X. But 23:30 Tokyo on date X is also 14:30 UTC date X (no
    # date change here). To test date line: 01:00 Tokyo on date X is
    # 16:00 UTC date X-1.
    tz = ZoneInfo("Asia/Tokyo")
    # 2026-06-20 01:00 Tokyo = 2026-06-19 16:00 UTC.
    epoch = hhmm_to_unix("01:00", date(2026, 6, 20), tz)
    # 2026-06-19 16:00 UTC epoch = 1781913600.
    assert epoch == pytest.approx(1781913600.0, abs=0.001)


# --- signal_date_in_tz -----------------------------------------------------


def test_signal_date_in_tz_at_local_midnight() -> None:
    # 2026-06-20 00:00:00 in America/Sao_Paulo = 2026-06-20 03:00:00 UTC.
    # Epoch for that UTC instant: compute via the helper itself.
    tz = ZoneInfo("America/Sao_Paulo")
    midnight_local = hhmm_to_unix("00:00", date(2026, 6, 20), tz)
    assert signal_date_in_tz(midnight_local, tz) == date(2026, 6, 20)


def test_signal_date_in_tz_just_before_local_midnight() -> None:
    # 2026-06-19 23:59:59 in Sao_Paulo is still date 2026-06-19 locally.
    tz = ZoneInfo("America/Sao_Paulo")
    # 23:59 local = 02:59 UTC next day.
    utc_just_after_midnight = hhmm_to_unix("23:59", date(2026, 6, 19), tz) + 1
    assert signal_date_in_tz(utc_just_after_midnight, tz) == date(2026, 6, 19)


# --- is_within_window -----------------------------------------------------


def test_is_within_window_past_boundary() -> None:
    # Exactly 60s in the past is acceptable; 61s is not.
    now = 1_000.0
    assert is_within_window(now - 60.0, now) is True
    assert is_within_window(now - 61.0, now) is False


def test_is_within_window_future_boundary() -> None:
    now = 1_000.0
    assert is_within_window(now + 1_800.0, now) is True
    assert is_within_window(now + 1_801.0, now) is False


def test_is_within_window_default_tolerances() -> None:
    # Without explicit kwargs, defaults are 60s past / 1800s future.
    now = 1_000.0
    assert is_within_window(now - 30.0, now) is True
    assert is_within_window(now + 100.0, now) is True


def test_is_within_window_custom_tolerances() -> None:
    now = 1_000.0
    # Tighten the past tolerance to 10s.
    assert is_within_window(now - 5.0, now, past_tolerance=10.0) is True
    assert is_within_window(now - 11.0, now, past_tolerance=10.0) is False


# --- now_unix and monotonic ------------------------------------------------


def test_now_unix_close_to_time_time() -> None:
    assert now_unix() == pytest.approx(time.time(), abs=1.0)


def test_now_unix_returns_non_decreasing_values() -> None:
    a = now_unix()
    b = now_unix()
    assert b >= a


def test_monotonic_returns_non_decreasing_values() -> None:
    a = monotonic()
    b = monotonic()
    assert b >= a
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_clock.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'signal_copier.infra.clock'`.

- [ ] **Step 3: Write the implementation**

Create `src/signal_copier/infra/clock.py` with this exact content:

```python
from __future__ import annotations

import time
from datetime import date, datetime
from zoneinfo import ZoneInfo


def hhmm_to_unix(hhmm: str, on_date: date, tz: ZoneInfo) -> float:
    """Convert an 'HH:MM' string + date in `tz` to a Unix epoch (float seconds)."""
    hour, minute = (int(x) for x in hhmm.split(":"))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"invalid HH:MM: {hhmm!r}")
    local_dt = datetime(
        on_date.year, on_date.month, on_date.day, hour, minute, tzinfo=tz,
    )
    return local_dt.timestamp()


def signal_date_in_tz(unix_ts: float, tz: ZoneInfo) -> date:
    """Return the date (in `tz`) that `unix_ts` falls on."""
    return datetime.fromtimestamp(unix_ts, tz=tz).date()


def is_within_window(
    trigger_unix: float,
    now_unix: float,
    *,
    past_tolerance: float = 60.0,
    future_tolerance: float = 1800.0,
) -> bool:
    """True if `trigger_unix` is within `[now - past_tolerance, now + future_tolerance]`."""
    return (now_unix - past_tolerance) <= trigger_unix <= (now_unix + future_tolerance)


def now_unix() -> float:
    """Current wall-clock Unix time as a float. Thin wrapper over time.time()."""
    return time.time()


def monotonic() -> float:
    """Monotonic clock reading (seconds, float). Reserved for M6's scheduler."""
    return time.monotonic()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_clock.py -v`
Expected: all 12 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/signal_copier/infra/clock.py tests/test_clock.py
git commit -m "M5: add infra/clock.py with 5 tz/clock helpers"
```

---

## Task 5: Add `setup_parse_failures_log` to `infra/log.py` (TDD)

**Files:**
- Modify: `src/signal_copier/infra/log.py`
- Create: `tests/test_log.py`

- [ ] **Step 1: Write the failing test file**

Create `tests/test_log.py` with this exact content:

```python
from __future__ import annotations

import logging
from pathlib import Path

from signal_copier.infra.log import setup_parse_failures_log


def test_creates_log_dir_if_missing(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    assert not log_dir.exists()
    setup_parse_failures_log(log_dir)
    assert log_dir.is_dir()


def test_creates_parse_failures_log_file(tmp_path: Path) -> None:
    setup_parse_failures_log(tmp_path)
    expected = tmp_path / "parse_failures.log"
    assert expected.is_file()


def test_logger_writes_warning_to_file(tmp_path: Path) -> None:
    logger = setup_parse_failures_log(tmp_path)
    logger.warning("test message: %s", "hello")
    # Close the handler so the file is flushed.
    for handler in logger.handlers:
        handler.close()
    content = (tmp_path / "parse_failures.log").read_text(encoding="utf-8")
    assert "test message: hello" in content
    assert "WARNING" in content


def test_logger_does_not_propagate_to_root(tmp_path: Path) -> None:
    logger = setup_parse_failures_log(tmp_path)
    assert logger.propagate is False
    # Logger name should be namespaced so it doesn't pollute root.
    assert logger.name == "signal_copier.parse_failures"


def test_setup_is_idempotent(tmp_path: Path) -> None:
    # Calling setup_parse_failures_log twice should not stack handlers.
    logger1 = setup_parse_failures_log(tmp_path)
    logger2 = setup_parse_failures_log(tmp_path)
    assert logger1 is logger2  # same Logger instance (cached by name)
    file_handlers = [
        h for h in logger1.handlers if isinstance(h, logging.FileHandler)
    ]
    assert len(file_handlers) == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_log.py -v`
Expected: FAIL with `ImportError: cannot import name 'setup_parse_failures_log'`.

- [ ] **Step 3: Modify `infra/log.py` to add the new function**

Open `src/signal_copier/infra/log.py` and replace its content with:

```python
from __future__ import annotations

import logging
from pathlib import Path


def setup_logging(log_path: Path) -> None:
    """Configure the root logger with a stderr handler at INFO level.

    M5 keeps this minimal. M7 replaces it with a loguru setup that
    adds rotation, file sinks, and the FR-7.1 DM-mirroring handler.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    _ = log_path  # unused until M7


def setup_parse_failures_log(log_dir: Path) -> logging.Logger:
    """Configure a dedicated logger for parse failures.

    Writes WARNING+ records to `<log_dir>/parse_failures.log`. The
    returned logger is passed to the Listener constructor; tests
    inject a NullLogger from `tests/_telegram_fixtures.py`.

    M5 uses a plain FileHandler (no rotation) because parse failures
    are rare. M7's loguru setup will add rotation along with the
    main log file.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "parse_failures.log"

    logger = logging.getLogger("signal_copier.parse_failures")
    logger.setLevel(logging.WARNING)
    # Idempotent: don't double-add the same FileHandler on re-call.
    if not any(
        isinstance(h, logging.FileHandler) and h.baseFilename == str(log_path)
        for h in logger.handlers
    ):
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-8s %(message)s")
        )
        logger.addHandler(handler)
    logger.propagate = False
    return logger
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_log.py -v`
Expected: all 5 tests PASS.

- [ ] **Step 5: Run the full test suite to confirm no regressions**

Run: `pytest -q`
Expected: all existing tests still pass; the 5 new tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/signal_copier/infra/log.py tests/test_log.py
git commit -m "M5: add setup_parse_failures_log helper"
```

---

## Task 6: Create shared test fixtures `tests/_telegram_fixtures.py`

**Files:**
- Create: `tests/_telegram_fixtures.py`

This file is pure test infrastructure used by Tasks 7 and 8. No TDD needed — it's a helper module, not production code.

- [ ] **Step 1: Create the file**

Create `tests/_telegram_fixtures.py` with this exact content:

```python
"""Shared test fixtures for the M5 telegram module.

Helpers:
  - make_event: build a synthetic Telethon NewMessage.Event for tests.
  - FakeStateStore: drop-in replacement for StateStore that records
    upsert_signal calls and returns a configurable bool.
  - NullLogger: a logging.Logger that swallows records; lets tests
    assert on parse-failure routing without writing to logs/parse_failures.log.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

from signal_copier.domain.signal import Signal


class _StubMessage:
    """Minimal stand-in for telethon.tl.custom.message.Message."""

    def __init__(self, *, message_id: int, outgoing: bool = False) -> None:
        self.id = message_id
        self.out = outgoing


def make_event(
    *,
    text: str,
    chat_id: int,
    message_id: int = 1,
    outgoing: bool = False,
) -> Any:
    """Build a synthetic Telethon NewMessage.Event.

    Only the attributes Listener reads are populated. Tests can call
    listener.on_new_message(make_event(...)) and assert on the side
    effects (queue contents, upsert_signal calls, parse_failures logs).
    """
    event = MagicMock()
    event.text = text
    event.chat_id = chat_id
    event.message = _StubMessage(message_id=message_id, outgoing=outgoing)
    return event


class FakeStateStore:
    """Drop-in replacement for StateStore. Records upsert_signal calls."""

    def __init__(self, *, next_insert_returns: bool = True) -> None:
        self.upserted: list[Signal] = []
        self._next_returns = next_insert_returns

    async def upsert_signal(self, signal: Signal) -> bool:
        self.upserted.append(signal)
        return self._next_returns


class NullLogger(logging.Logger):
    """A logging.Logger that swallows all records.

    Used in tests that don't care about parse-failure logging.
    """

    def __init__(self, name: str = "null") -> None:
        super().__init__(name, level=logging.CRITICAL + 1)

    def handle(self, record: logging.LogRecord) -> None:
        return None
```

- [ ] **Step 2: Verify the helpers import correctly**

Run: `python -c "from tests._telegram_fixtures import make_event, FakeStateStore, NullLogger; print('OK')"`
Expected: prints `OK`.

- [ ] **Step 3: Commit**

```bash
git add tests/_telegram_fixtures.py
git commit -m "M5: add shared telegram test fixtures"
```

---

## Task 7: Implement `telegram/client.py` with tests (TDD)

**Files:**
- Create: `src/signal_copier/telegram/client.py`
- Create: `tests/test_telegram_client.py`

- [ ] **Step 1: Write the failing test file**

Create `tests/test_telegram_client.py` with this exact content:

```python
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from signal_copier.telegram.client import (
    TelegramClient,
    TelegramConfigError,
    compute_backoff_seconds,
)

# --- compute_backoff_seconds (pure function) ------------------------------


def test_compute_backoff_seconds_exponential() -> None:
    assert compute_backoff_seconds(0) == 1.0
    assert compute_backoff_seconds(1) == 2.0
    assert compute_backoff_seconds(2) == 4.0
    assert compute_backoff_seconds(3) == 8.0
    assert compute_backoff_seconds(4) == 16.0


def test_compute_backoff_seconds_capped_at_30() -> None:
    assert compute_backoff_seconds(5) == 30.0
    assert compute_backoff_seconds(6) == 30.0
    assert compute_backoff_seconds(20) == 30.0


def test_compute_backoff_seconds_returns_float() -> None:
    assert isinstance(compute_backoff_seconds(0), float)
    assert isinstance(compute_backoff_seconds(5), float)


# --- TelegramClient.__init__ validation ------------------------------------


def test_init_raises_on_zero_api_id() -> None:
    with pytest.raises(TelegramConfigError, match="TELEGRAM_API_ID"):
        TelegramClient(
            api_id=0, api_hash="abc", phone="+1", session_string="s", target_chat="@c",
        )


def test_init_raises_on_empty_api_hash() -> None:
    with pytest.raises(TelegramConfigError, match="TELEGRAM_API_HASH"):
        TelegramClient(
            api_id=1, api_hash="", phone="+1", session_string="s", target_chat="@c",
        )


def test_init_raises_on_empty_phone() -> None:
    with pytest.raises(TelegramConfigError, match="TELEGRAM_PHONE"):
        TelegramClient(
            api_id=1, api_hash="abc", phone="", session_string="s", target_chat="@c",
        )


def test_init_raises_on_empty_session_string() -> None:
    with pytest.raises(TelegramConfigError, match="TELEGRAM_SESSION_STRING"):
        TelegramClient(
            api_id=1, api_hash="abc", phone="+1", session_string="", target_chat="@c",
        )


def test_init_raises_helpful_message_for_empty_session() -> None:
    with pytest.raises(TelegramConfigError, match="telegram.auth"):
        TelegramClient(
            api_id=1, api_hash="abc", phone="+1", session_string="", target_chat="@c",
        )


# --- TelegramClient.target_chat_id property -------------------------------


def test_target_chat_id_raises_before_connect() -> None:
    client = TelegramClient(
        api_id=1, api_hash="abc", phone="+1",
        session_string="abc", target_chat="@c",
    )
    with pytest.raises(RuntimeError, match="connect"):
        _ = client.target_chat_id


# --- TelegramClient.add_message_handler requires connect -------------------


def test_add_message_handler_requires_connect() -> None:
    client = TelegramClient(
        api_id=1, api_hash="abc", phone="+1",
        session_string="abc", target_chat="@c",
    )
    with pytest.raises(RuntimeError, match="connect"):
        client.add_message_handler(handler=AsyncMock())


# --- TelegramClient.close is idempotent -----------------------------------


async def test_close_is_idempotent_when_not_connected() -> None:
    # If close() is called before connect() (or twice after connect()),
    # it should silently no-op rather than raise.
    client = TelegramClient(
        api_id=1, api_hash="abc", phone="+1",
        session_string="abc", target_chat="@c",
    )
    await client.close()  # before connect
    await client.close()  # again — no-op
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_telegram_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'signal_copier.telegram.client'`.

- [ ] **Step 3: Write the implementation**

Create `src/signal_copier/telegram/client.py` with this exact content:

```python
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar

from telethon import TelegramClient as _TelethonClient
from telethon.errors import FloodWaitError
from telethon.events import MessageEdited, NewMessage
from telethon.sessions import StringSession

_log = logging.getLogger(__name__)


_MAX_RECONNECT_ATTEMPTS: ClassVar[int] = 10
_BACKOFF_BASE_SECONDS: ClassVar[float] = 1.0
_BACKOFF_CAP_SECONDS: ClassVar[float] = 30.0
_FLOOD_WAIT_THRESHOLD_SECONDS: ClassVar[int] = 60


class TelegramConfigError(RuntimeError):
    """Raised when required config is missing/invalid or the target chat
    cannot be resolved at startup. Caught by __main__; exits 2."""


def compute_backoff_seconds(attempt: int) -> float:
    """Exponential backoff with a 30s cap. attempt is 0-indexed.

    attempt=0 -> 1.0, attempt=1 -> 2.0, ..., attempt=4 -> 16.0,
    attempt>=5 -> 30.0 (capped).
    """
    return min(_BACKOFF_BASE_SECONDS * (2 ** attempt), _BACKOFF_CAP_SECONDS)


class TelegramClient:
    """Thin wrapper over the vendored Telethon client.

    Owns the StringSession lifecycle, the reconnect supervisor, and
    the FloodWaitError policy. Construction is sync (validates config
    eagerly — D-12). connect() resolves the target chat. start() runs
    the client until disconnect with exponential-backoff reconnect.
    """

    def __init__(
        self,
        *,
        api_id: int,
        api_hash: str,
        phone: str,
        session_string: str,
        target_chat: str,
    ) -> None:
        if api_id == 0:
            raise TelegramConfigError(
                "TELEGRAM_API_ID is 0; set it in .env (get from my.telegram.org)"
            )
        if not api_hash:
            raise TelegramConfigError("TELEGRAM_API_HASH is empty; set it in .env")
        if not phone:
            raise TelegramConfigError("TELEGRAM_PHONE is empty; set it in .env")
        if not session_string:
            raise TelegramConfigError(
                "TELEGRAM_SESSION_STRING is empty; run "
                "'python -m signal_copier.telegram.auth' to generate one"
            )

        self._api_id = api_id
        self._api_hash = api_hash
        self._phone = phone
        self._target_chat = target_chat
        self._session_string = session_string

        self._client: _TelethonClient | None = None
        self._target_chat_id: int | None = None

    @property
    def target_chat_id(self) -> int:
        if self._target_chat_id is None:
            raise RuntimeError(
                "target_chat_id is not resolved; call TelegramClient.connect() first"
            )
        return self._target_chat_id

    async def connect(self) -> None:
        self._client = _TelethonClient(
            StringSession(self._session_string),
            self._api_id,
            self._api_hash,
        )
        await self._client.connect()
        try:
            entity = await self._client.get_entity(self._target_chat)
        except Exception as exc:
            raise TelegramConfigError(
                f"Cannot resolve TELEGRAM_TARGET_CHAT={self._target_chat!r}: "
                f"{type(exc).__name__}: {exc}. Check the value in .env."
            ) from exc
        self._target_chat_id = entity.id
        _log.info(
            "TelegramClient connected (target_chat=%r -> chat_id=%d)",
            self._target_chat, self._target_chat_id,
        )

    def add_message_handler(
        self,
        handler: Callable[[Any], Awaitable[None]],
    ) -> None:
        if self._client is None:
            raise RuntimeError(
                "add_message_handler called before connect(); call TelegramClient.connect() first"
            )
        self._client.on(NewMessage)(handler)
        self._client.on(MessageEdited)(handler)

    async def start(self) -> None:
        if self._client is None:
            raise RuntimeError("start() called before connect()")
        attempt = 0
        while True:
            try:
                await self._client.run_until_disconnected()
                return
            except FloodWaitError as exc:
                if exc.seconds > _FLOOD_WAIT_THRESHOLD_SECONDS:
                    _log.error(
                        "Telegram FloodWaitError: %ds wait requested; re-raising "
                        "(FR-1.7: 'raise + log for longer')",
                        exc.seconds,
                    )
                    raise
                _log.warning("FloodWaitError %ds; continuing", exc.seconds)
                continue
            except ConnectionError as exc:
                attempt += 1
                if attempt > _MAX_RECONNECT_ATTEMPTS:
                    _log.error(
                        "Telegram reconnect failed after %d attempts; re-raising",
                        _MAX_RECONNECT_ATTEMPTS,
                    )
                    raise
                delay = compute_backoff_seconds(attempt - 1)
                _log.warning(
                    "Telegram ConnectionError: %s. Reconnect attempt %d/%d in %.1fs",
                    type(exc).__name__, attempt, _MAX_RECONNECT_ATTEMPTS, delay,
                )
                await asyncio.sleep(delay)

    async def close(self) -> None:
        if self._client is None:
            return
        try:
            await self._client.disconnect()
        except Exception as exc:  # noqa: BLE001 — close is best-effort
            _log.debug("TelegramClient.close: disconnect raised: %s", exc)
        self._client = None
        self._target_chat_id = None
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_telegram_client.py -v`
Expected: all 11 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/signal_copier/telegram/client.py tests/test_telegram_client.py
git commit -m "M5: add TelegramClient wrapper with reconnect supervisor"
```

---

## Task 8: Implement `telegram/listener.py` with tests (TDD)

**Files:**
- Create: `src/signal_copier/telegram/listener.py`
- Create: `tests/test_telegram_listener.py`

- [ ] **Step 1: Write the failing test file**

Create `tests/test_telegram_listener.py` with this exact content:

```python
from __future__ import annotations

import asyncio
import logging
from datetime import date
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from signal_copier.config import Config
from signal_copier.domain.signal import FailureReason, Signal
from signal_copier.infra.clock import now_unix
from signal_copier.telegram.listener import Listener
from tests._telegram_fixtures import (
    FakeStateStore,
    NullLogger,
    make_event,
)

# --- Test fixtures --------------------------------------------------------


def _config() -> Config:
    """Build a Config suitable for listener tests.

    The only fields Listener reads are: telegram_target_chat (NOT —
    that's TelegramClient's job; Listener only needs the resolved int
    chat_id), expiration_seconds, and timezone. We use defaults for
    everything else and don't set TELEGRAM_* env vars.
    """
    return Config(
        expiration_seconds=300,
        timezone="America/Sao_Paulo",
    )


def _listener(
    *,
    state_store: FakeStateStore,
    queue: asyncio.Queue[Signal],
    config: Config | None = None,
    target_chat_id: int = 42,
    parse_failures_logger: logging.Logger | None = None,
) -> Listener:
    return Listener(
        target_chat_id=target_chat_id,
        state_store=state_store,  # type: ignore[arg-type]  # FakeStateStore is duck-typed
        queue=queue,
        config=config or _config(),
        parse_failures_logger=parse_failures_logger or NullLogger(),
    )


VALID_SIGNAL_TEXT = (
    "💰5-minute expiration\n"
    "EUR/JPY;10:20;PUT🟥\n"
    "🕛TIME UNTIL 10:25\n"
    "1st GALE -> TIME UNTIL 10:30\n"
    "2nd GALE - TIME UNTIL 10:35\n"
)

# --- Happy path ----------------------------------------------------------


async def test_happy_path_valid_signal_enqueued_and_upserted(
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = FakeStateStore()
    queue: asyncio.Queue[Signal] = asyncio.Queue()
    listener = _listener(state_store=state, queue=queue)

    event = make_event(
        text=VALID_SIGNAL_TEXT, chat_id=42, message_id=7,
    )
    await listener.on_new_message(event)

    assert len(state.upserted) == 1
    assert queue.qsize() == 1
    enqueued = queue.get_nowait()
    assert enqueued.pair == "EUR/JPY"
    assert enqueued.direction == "down"
    assert enqueued.trigger_hhmm == "10:20"
    assert enqueued.source_message_id == 7

    out = capsys.readouterr().out
    assert '"pair": "EUR/JPY"' in out
    assert '"signal_id"' in out


async def test_duplicate_signal_logged_not_re_enqueued(
    caplog: pytest.LogCaptureFixture,
) -> None:
    state = FakeStateStore(next_insert_returns=False)
    queue: asyncio.Queue[Signal] = asyncio.Queue()
    listener = _listener(state_store=state, queue=queue)

    event = make_event(text=VALID_SIGNAL_TEXT, chat_id=42, message_id=1)
    with caplog.at_level(logging.INFO, logger="signal_copier.telegram.listener"):
        await listener.on_new_message(event)

    assert len(state.upserted) == 1
    assert queue.empty()
    assert any("duplicate signal" in rec.message for rec in caplog.records)


# --- Parse failure -------------------------------------------------------


@pytest.mark.parametrize(
    "bad_text, expected_reason",
    [
        ("random ad text with no signal line", FailureReason.MISSING_HEADER_LINE),
        ("💰5-minute expiration\n", FailureReason.MISSING_SIGNAL_LINE),
        (
            "💰5-minute expiration\nEURJPY;10:20;PUT🟥\n",
            FailureReason.BAD_PAIR_FORMAT,
        ),
        (
            "💰5-minute expiration\nEUR/JPY;25:99;PUT🟥\n",
            FailureReason.BAD_TIME_FORMAT,
        ),
        (
            "💰3-minute expiration\nEUR/JPY;10:20;PUT🟥\n",
            FailureReason.EXPIRATION_NOT_ALLOWED,
        ),
    ],
)
async def test_parse_failure_routed_to_logger(
    bad_text: str, expected_reason: FailureReason,
) -> None:
    parse_logger = logging.getLogger(
        f"test.parse_failures.{expected_reason.value}"
    )
    parse_logger.handlers = [logging.handlers.MemoryHandler(  # type: ignore[attr-defined]
        capacity=100,
    )] if hasattr(logging, "handlers") else []
    # Simpler: use a custom list-handler.
    records: list[logging.LogRecord] = []

    class _ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    parse_logger.addHandler(_ListHandler())
    parse_logger.setLevel(logging.WARNING)
    parse_logger.propagate = False

    state = FakeStateStore()
    queue: asyncio.Queue[Signal] = asyncio.Queue()
    listener = _listener(
        state_store=state, queue=queue, parse_failures_logger=parse_logger,
    )

    event = make_event(text=bad_text, chat_id=42, message_id=1)
    await listener.on_new_message(event)

    assert state.upserted == []
    assert queue.empty()
    assert len(records) == 1
    assert expected_reason.value in records[0].getMessage()


# --- Time-window rejection -----------------------------------------------


async def test_out_of_window_past_rejected() -> None:
    parse_logger = logging.getLogger("test.out_of_window_past")
    records: list[logging.LogRecord] = []

    class _ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    parse_logger.addHandler(_ListHandler())
    parse_logger.setLevel(logging.WARNING)
    parse_logger.propagate = False

    state = FakeStateStore()
    queue: asyncio.Queue[Signal] = asyncio.Queue()
    listener = _listener(
        state_store=state, queue=queue, parse_failures_logger=parse_logger,
    )

    # 5 minutes in the past is well outside the 60s past tolerance.
    now = now_unix()
    five_min_ago = now - 300
    import datetime as _dt
    tz = ZoneInfo("America/Sao_Paulo")
    past_hhmm = _dt.datetime.fromtimestamp(five_min_ago, tz=tz).strftime("%H:%M")
    past_date = _dt.date.fromtimestamp(five_min_ago, tz=tz)
    text = (
        f"💰5-minute expiration\n"
        f"EUR/JPY;{past_hhmm};PUT🟥\n"
    )

    event = make_event(text=text, chat_id=42, message_id=1)
    await listener.on_new_message(event)

    assert state.upserted == []
    assert queue.empty()
    assert len(records) == 1
    assert "out_of_window" in records[0].getMessage()


async def test_out_of_window_within_tolerance_accepted() -> None:
    state = FakeStateStore()
    queue: asyncio.Queue[Signal] = asyncio.Queue()
    listener = _listener(state_store=state, queue=queue)

    # 30s in the past is within the 60s past tolerance.
    now = now_unix()
    thirty_sec_ago = now - 30
    import datetime as _dt
    tz = ZoneInfo("America/Sao_Paulo")
    past_hhmm = _dt.datetime.fromtimestamp(thirty_sec_ago, tz=tz).strftime("%H:%M")
    text = (
        f"💰5-minute expiration\n"
        f"EUR/JPY;{past_hhmm};PUT🟥\n"
    )

    event = make_event(text=text, chat_id=42, message_id=1)
    await listener.on_new_message(event)

    # Within tolerance, so it should be enqueued (or at least upserted).
    # Note: signal_id includes the date; if the date shifted, the
    # signal is still valid for the new date — we accept it.
    assert len(state.upserted) == 1


# --- Chat filter / outgoing filter ---------------------------------------


async def test_wrong_chat_filtered_silently() -> None:
    state = FakeStateStore()
    queue: asyncio.Queue[Signal] = asyncio.Queue()
    listener = _listener(state_store=state, queue=queue, target_chat_id=42)

    event = make_event(text=VALID_SIGNAL_TEXT, chat_id=999, message_id=1)
    await listener.on_new_message(event)

    assert state.upserted == []
    assert queue.empty()


async def test_outgoing_message_ignored() -> None:
    state = FakeStateStore()
    queue: asyncio.Queue[Signal] = asyncio.Queue()
    listener = _listener(state_store=state, queue=queue)

    event = make_event(
        text=VALID_SIGNAL_TEXT, chat_id=42, message_id=1, outgoing=True,
    )
    await listener.on_new_message(event)

    assert state.upserted == []
    assert queue.empty()


# --- NewMessage and MessageEdited parity ----------------------------------


async def test_new_message_and_edited_produce_identical_output() -> None:
    state1 = FakeStateStore()
    q1: asyncio.Queue[Signal] = asyncio.Queue()
    l1 = _listener(state_store=state1, queue=q1)
    await l1.on_new_message(
        make_event(text=VALID_SIGNAL_TEXT, chat_id=42, message_id=1)
    )

    state2 = FakeStateStore()
    q2: asyncio.Queue[Signal] = asyncio.Queue()
    l2 = _listener(state_store=state2, queue=q2)
    await l2.on_message_edited(
        make_event(text=VALID_SIGNAL_TEXT, chat_id=42, message_id=1)
    )

    # Same signal_id, same content; the only difference is the underlying
    # event class. Both should produce the same Signal in the queue.
    assert len(state1.upserted) == 1
    assert len(state2.upserted) == 1
    assert state1.upserted[0].signal_id == state2.upserted[0].signal_id
    assert state1.upserted[0].pair == state2.upserted[0].pair
    assert state1.upserted[0].direction == state2.upserted[0].direction
    assert q1.get_nowait().signal_id == q2.get_nowait().signal_id


# --- Edge cases ---------------------------------------------------------


async def test_empty_message_handled() -> None:
    state = FakeStateStore()
    queue: asyncio.Queue[Signal] = asyncio.Queue()
    listener = _listener(state_store=state, queue=queue)

    event = make_event(text="", chat_id=42, message_id=1)
    await listener.on_new_message(event)

    assert state.upserted == []
    assert queue.empty()


async def test_bom_message_handled() -> None:
    state = FakeStateStore()
    queue: asyncio.Queue[Signal] = asyncio.Queue()
    listener = _listener(state_store=state, queue=queue)

    text_with_bom = "\ufeff" + VALID_SIGNAL_TEXT
    event = make_event(text=text_with_bom, chat_id=42, message_id=1)
    await listener.on_new_message(event)

    assert len(state.upserted) == 1


async def test_handler_survives_parse_failure() -> None:
    state = FakeStateStore()
    queue: asyncio.Queue[Signal] = asyncio.Queue()
    listener = _listener(state_store=state, queue=queue)

    # First: a bad message (parse failure)
    bad_event = make_event(text="random ad text", chat_id=42, message_id=1)
    await listener.on_new_message(bad_event)

    # Then: a good message
    good_event = make_event(text=VALID_SIGNAL_TEXT, chat_id=42, message_id=2)
    await listener.on_new_message(good_event)

    # The good message was processed normally.
    assert len(state.upserted) == 1
    assert queue.qsize() == 1
    assert state.upserted[0].source_message_id == 2
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_telegram_listener.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'signal_copier.telegram.listener'`.

- [ ] **Step 3: Write the implementation**

Create `src/signal_copier/telegram/listener.py` with this exact content:

```python
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict
from typing import Any

from signal_copier.config import Config
from signal_copier.domain.signal import (
    ParseFailure,
    Signal,
    derive_signal_id,
    parse_signal,
)
from signal_copier.infra.clock import (
    hhmm_to_unix,
    is_within_window,
    now_unix,
    signal_date_in_tz,
)
from signal_copier.infra.state_store import StateStore

_log = logging.getLogger(__name__)


def _allowed_expirations(config: Config) -> frozenset[int]:
    return frozenset({config.expiration_seconds})


class Listener:
    """Wires Telethon NewMessage/MessageEdited events into the M1 parser
    and M4 StateStore. Filter-aware: only `chat_id == target` and
    non-outgoing messages are processed. A successful parse goes through
    the M1 parser, the M5 time-window check, M4's StateStore.upsert_signal,
    and finally lands on the asyncio.Queue for M6 (or M5's dump_consumer)
    to drain.
    """

    def __init__(
        self,
        *,
        target_chat_id: int,
        state_store: StateStore,
        queue: asyncio.Queue[Signal],
        config: Config,
        parse_failures_logger: logging.Logger,
    ) -> None:
        self._target_chat_id = target_chat_id
        self._state_store = state_store
        self._queue = queue
        self._config = config
        self._parse_failures_logger = parse_failures_logger
        self._allowed_expirations = _allowed_expirations(config)

    async def on_new_message(self, event: Any) -> None:
        await self._process_message(event)

    async def on_message_edited(self, event: Any) -> None:
        await self._process_message(event)

    async def _process_message(self, event: Any) -> None:
        # D-14: skip bot's own outgoing messages
        if event.message.out:
            return
        # D-13: chat filter (the ONLY filter — no sender allowlist per R-14)
        if event.chat_id != self._target_chat_id:
            return

        text: str = event.text or ""
        if not text.strip():
            return

        source_message_id: int = event.message.id
        source_chat_id: int = event.chat_id
        received_at_unix: float = now_unix()

        # Step 1: parse
        result = parse_signal(text, allowed_expirations=self._allowed_expirations)
        if isinstance(result, ParseFailure):
            self._log_parse_failure(result, text, source_message_id)
            return

        # Step 2: compute trigger times + signal_id
        tz = self._config.tz()
        signal_date = signal_date_in_tz(received_at_unix, tz)
        trigger_unix_initial = hhmm_to_unix(
            result.trigger_hhmm, signal_date, tz,
        )
        trigger_unix_gale1 = trigger_unix_initial + result.expiration_seconds
        trigger_unix_gale2 = trigger_unix_initial + 2 * result.expiration_seconds

        # Step 3: time-window check (FR-2.3)
        if not is_within_window(trigger_unix_initial, received_at_unix):
            self._log_out_of_window(
                result.trigger_hhmm, trigger_unix_initial, received_at_unix,
                source_message_id,
            )
            return

        # Step 4: build the full Signal dataclass
        signal_id = derive_signal_id(result, signal_date=signal_date)
        signal = Signal(
            signal_id=signal_id,
            pair=result.pair,
            direction=result.direction,
            trigger_hhmm=result.trigger_hhmm,
            expiration_seconds=result.expiration_seconds,
            received_at_unix=received_at_unix,
            source_message_id=source_message_id,
            source_chat_id=source_chat_id,
            raw_text=text,
            trigger_unix_initial=trigger_unix_initial,
            trigger_unix_gale1=trigger_unix_gale1,
            trigger_unix_gale2=trigger_unix_gale2,
        )

        # Step 5: persist
        inserted = await self._state_store.upsert_signal(signal)
        if not inserted:
            _log.info(
                "duplicate signal, ignoring: signal_id=%s pair=%s trigger=%s",
                signal.signal_id, signal.pair, signal.trigger_hhmm,
            )
            return

        # Step 6: enqueue
        await self._queue.put(signal)

        # Step 7: pretty-print to stdout
        print(json.dumps(asdict(signal), indent=2, default=str))

    def _log_parse_failure(
        self, failure: ParseFailure, text: str, source_message_id: int,
    ) -> None:
        preview = text[:80].replace("\n", " ")
        self._parse_failures_logger.warning(
            "parse_failure: reason=%s message_id=%s preview=%r",
            failure.reason.value, source_message_id, preview,
        )

    def _log_out_of_window(
        self,
        trigger_hhmm: str,
        trigger_unix: float,
        now_unix_val: float,
        source_message_id: int,
    ) -> None:
        self._parse_failures_logger.warning(
            "parse_failure: reason=out_of_window message_id=%s trigger_hhmm=%s "
            "trigger_unix=%.3f now_unix=%.3f skew_sec=%.1f",
            source_message_id, trigger_hhmm, trigger_unix, now_unix_val,
            now_unix_val - trigger_unix,
        )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_telegram_listener.py -v`
Expected: all 13 tests PASS (5 parametrized parse-failure cases count as one test function; 8 other distinct tests; plus the 2 time-window tests).

- [ ] **Step 5: Commit**

```bash
git add src/signal_copier/telegram/listener.py tests/test_telegram_listener.py
git commit -m "M5: add Listener with NewMessage/MessageEdited handlers"
```

---

## Task 9: Implement `telegram/auth.py` with tests (TDD)

**Files:**
- Create: `src/signal_copier/telegram/auth.py`
- Create: `tests/test_auth.py`

- [ ] **Step 1: Write the failing test file**

Create `tests/test_auth.py` with this exact content:

```python
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from signal_copier.telegram import auth


def test_read_creds_succeeds_with_full_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc123")
    monkeypatch.setenv("TELEGRAM_PHONE", "+15551234567")
    # Clean up other env vars that Config might read from previous tests.
    for key in [
        "TELEGRAM_SESSION_STRING", "TELEGRAM_TARGET_CHAT",
        "OLYMP_ACCESS_TOKEN", "OLYMP_ACCOUNT_ID", "OLYMP_ACCOUNT_GROUP",
        "DATABASE_URL", "AMOUNT_INITIAL", "AMOUNT_GALE1", "AMOUNT_GALE2",
        "EXPIRATION_SECONDS", "DAILY_LOSS_LIMIT", "DAILY_TRADE_LIMIT",
        "DAILY_DRAWDOWN_PCT", "TIMEZONE", "TRIGGER_SKEW_TOLERANCE_SECONDS",
        "LOG_PATH", "DRY_RUN", "REQUIRE_CONFIRM",
    ]:
        monkeypatch.delenv(key, raising=False)

    api_id, api_hash, phone = auth._read_creds()
    assert api_id == 12345
    assert api_hash == "abc123"
    assert phone == "+15551234567"


def test_main_returns_2_on_missing_config(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Clear all env vars that Config reads.
    for key in [
        "TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_PHONE",
        "TELEGRAM_SESSION_STRING", "TELEGRAM_TARGET_CHAT",
        "OLYMP_ACCESS_TOKEN", "OLYMP_ACCOUNT_ID", "OLYMP_ACCOUNT_GROUP",
        "DATABASE_URL", "AMOUNT_INITIAL", "AMOUNT_GALE1", "AMOUNT_GALE2",
        "EXPIRATION_SECONDS", "DAILY_LOSS_LIMIT", "DAILY_TRADE_LIMIT",
        "DAILY_DRAWDOWN_PCT", "TIMEZONE", "TRIGGER_SKEW_TOLERANCE_SECONDS",
        "LOG_PATH", "DRY_RUN", "REQUIRE_CONFIRM",
    ]:
        monkeypatch.delenv(key, raising=False)

    rc = auth.main()
    assert rc == 2
    err = capsys.readouterr().err
    assert "TELEGRAM_API_ID" in err or "Config validation" in err


def test_main_returns_2_on_zero_api_id(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("TELEGRAM_API_ID", "0")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc")
    monkeypatch.setenv("TELEGRAM_PHONE", "+1")
    # Clear session string.
    monkeypatch.delenv("TELEGRAM_SESSION_STRING", raising=False)

    rc = auth.main()
    assert rc == 2
    err = capsys.readouterr().err
    assert "TELEGRAM_API_ID" in err


def test_main_returns_1_on_auth_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc123")
    monkeypatch.setenv("TELEGRAM_PHONE", "+15551234567")
    monkeypatch.delenv("TELEGRAM_SESSION_STRING", raising=False)
    monkeypatch.delenv("TELEGRAM_TARGET_CHAT", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    # Mock _do_auth to raise an exception.
    async def _failing_auth(*args: object, **kwargs: object) -> str:
        raise RuntimeError("simulated auth failure")

    with patch.object(auth, "_do_auth", side_effect=_failing_auth):
        rc = auth.main()

    assert rc == 1
    err = capsys.readouterr().err
    assert "auth failed" in err.lower()
    assert "simulated auth failure" in err


def test_main_prints_session_string_on_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc123")
    monkeypatch.setenv("TELEGRAM_PHONE", "+15551234567")
    monkeypatch.delenv("TELEGRAM_SESSION_STRING", raising=False)
    monkeypatch.delenv("TELEGRAM_TARGET_CHAT", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    SESSION = "AAAAfakebase64session=="

    async def _success_auth(*args: object, **kwargs: object) -> str:
        return SESSION

    with patch.object(auth, "_do_auth", side_effect=_success_auth):
        rc = auth.main()

    assert rc == 0
    out = capsys.readouterr().out
    assert f"TELEGRAM_SESSION_STRING={SESSION}" in out
    assert "Generated by" in out
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'signal_copier.telegram.auth'`.

- [ ] **Step 3: Write the implementation**

Create `src/signal_copier/telegram/auth.py` with this exact content:

```python
from __future__ import annotations

import asyncio
import sys

from pydantic import ValidationError
from telethon import TelegramClient as _TelethonClient
from telethon.sessions import StringSession

from signal_copier.config import Config
from signal_copier.telegram.client import TelegramConfigError

# Interactive auth has no bound — the user may take minutes to enter
# the SMS code. We use a generous default.
_AUTH_TIMEOUT_SECONDS: int = 300


def _read_creds() -> tuple[int, str, str]:
    """Read API_ID / API_HASH / PHONE from .env via the Config validator.

    Re-uses M2's pydantic validators. On validation failure, prints a
    friendly error to stderr and raises so main() can return 2.
    """
    config = Config()
    return (
        config.telegram_api_id,
        config.telegram_api_hash,
        config.telegram_phone,
    )


async def _do_auth(api_id: int, api_hash: str, phone: str) -> str:
    """Run the Telethon interactive auth flow, return the StringSession string."""
    client = _TelethonClient(StringSession(), api_id, api_hash)
    await client.start(phone=phone)  # interactive: prompts for code + 2FA
    session_str = client.session.save()
    await client.disconnect()
    return session_str


def main() -> int:
    """Entry point for `python -m signal_copier.telegram.auth`.

    Reads credentials from .env, runs the Telethon interactive auth
    flow, prints the resulting StringSession to stdout. Exits 0 on
    success, 1 on auth failure, 2 on config error.
    """
    try:
        api_id, api_hash, phone = _read_creds()
    except (ValidationError, ValueError) as exc:
        sys.stderr.write(
            f"❌ Config validation failed; check API_ID / API_HASH / PHONE in .env:\n"
            f"{exc}\n"
        )
        return 2
    except TelegramConfigError as exc:
        sys.stderr.write(f"❌ {exc}\n")
        return 2

    if api_id == 0 or not api_hash or not phone:
        sys.stderr.write(
            "❌ TELEGRAM_API_ID / TELEGRAM_API_HASH / TELEGRAM_PHONE must be set in .env\n"
            "   Get API_ID and API_HASH from https://my.telegram.org\n"
        )
        return 2

    try:
        session_str = asyncio.run(
            asyncio.wait_for(
                _do_auth(api_id, api_hash, phone),
                timeout=_AUTH_TIMEOUT_SECONDS,
            )
        )
    except TelegramConfigError as exc:
        sys.stderr.write(f"❌ {exc}\n")
        return 2
    except asyncio.TimeoutError:
        sys.stderr.write(
            f"❌ Auth timed out after {_AUTH_TIMEOUT_SECONDS}s; run again and "
            "respond to the prompts more quickly.\n"
        )
        return 1
    except Exception as exc:
        sys.stderr.write(f"❌ Telegram auth failed: {type(exc).__name__}: {exc}\n")
        return 1

    print("# --- Telegram session ---")
    print("# Generated by `python -m signal_copier.telegram.auth`")
    print(f"TELEGRAM_SESSION_STRING={session_str}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_auth.py -v`
Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/signal_copier/telegram/auth.py tests/test_auth.py
git commit -m "M5: add telegram.auth subcommand for StringSession bootstrap"
```

---

## Task 10: Wire `__main__.py` for the M5 pipeline (TDD with mocks)

**Files:**
- Modify: `src/signal_copier/__main__.py`
- Modify: `tests/test_main.py` (existing file from M2)

- [ ] **Step 1: Read the existing `test_main.py` to understand what's already tested**

Run: `rtk read "C:\Users\ACER\Documents\opencode_projects\olymptrade\tests\test_main.py"` and review its content. The M2 test verifies the stub `main()` returns 0 with the "M2 started" banner. M5's test will need to coexist with it.

- [ ] **Step 2: Add the new M5 tests to `tests/test_main.py`**

Open `tests/test_main.py`. Append the following tests at the end of the file (do not modify the existing M2 tests; just add to them):

```python
# --- M5 wiring tests (added in M5) ----------------------------------------

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from signal_copier import __main__ as m5_main


def test_main_returns_2_on_config_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Clear all env vars so Config validation fails.
    for key in [
        "TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_PHONE",
        "TELEGRAM_SESSION_STRING", "TELEGRAM_TARGET_CHAT",
        "OLYMP_ACCESS_TOKEN", "OLYMP_ACCOUNT_ID", "OLYMP_ACCOUNT_GROUP",
        "DATABASE_URL", "AMOUNT_INITIAL", "AMOUNT_GALE1", "AMOUNT_GALE2",
        "EXPIRATION_SECONDS", "DAILY_LOSS_LIMIT", "DAILY_TRADE_LIMIT",
        "DAILY_DRAWDOWN_PCT", "TIMEZONE", "TRIGGER_SKEW_TOLERANCE_SECONDS",
        "LOG_PATH", "DRY_RUN", "REQUIRE_CONFIRM",
    ]:
        monkeypatch.delenv(key, raising=False)
    # Force a config error by setting real account + dry_run off.
    monkeypatch.setenv("OLYMP_ACCOUNT_GROUP", "real")
    monkeypatch.setenv("DRY_RUN", "false")

    rc = m5_main.main()
    assert rc == 2


def test_main_returns_1_on_database_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Set valid minimum env so config passes.
    for key in [
        "TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_PHONE",
        "TELEGRAM_SESSION_STRING", "TELEGRAM_TARGET_CHAT",
        "OLYMP_ACCESS_TOKEN", "OLYMP_ACCOUNT_ID", "OLYMP_ACCOUNT_GROUP",
        "DATABASE_URL", "AMOUNT_INITIAL", "AMOUNT_GALE1", "AMOUNT_GALE2",
        "EXPIRATION_SECONDS", "DAILY_LOSS_LIMIT", "DAILY_TRADE_LIMIT",
        "DAILY_DRAWDOWN_PCT", "TIMEZONE", "TRIGGER_SKEW_TOLERANCE_SECONDS",
        "LOG_PATH", "DRY_RUN", "REQUIRE_CONFIRM",
    ]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TELEGRAM_API_ID", "1")
    monkeypatch.setenv("TELEGRAM_API_HASH", "x")
    monkeypatch.setenv("TELEGRAM_PHONE", "+1")
    monkeypatch.setenv("TELEGRAM_SESSION_STRING", "abc")
    monkeypatch.setenv("TELEGRAM_TARGET_CHAT", "@c")
    monkeypatch.setenv("DATABASE_URL", "postgresql://bad@bad/bad")
    monkeypatch.setenv("DRY_RUN", "true")

    with patch.object(m5_main, "Database") as mock_db_cls:
        mock_db = MagicMock()
        mock_db.connect = AsyncMock(
            side_effect=m5_main.DatabaseConnectionError("simulated"),
        )
        mock_db_cls.connect = AsyncMock(return_value=mock_db)

        rc = m5_main.main()

    assert rc == 1
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `pytest tests/test_main.py -v`
Expected: the new M5 tests FAIL (the old M2 tests still pass — they don't depend on Database).

- [ ] **Step 4: Replace `src/signal_copier/__main__.py` with the M5 wiring**

Open `src/signal_copier/__main__.py` and replace its content with:

```python
from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict

from pydantic import ValidationError

from signal_copier.config import Config
from signal_copier.infra.db import Database, DatabaseConnectionError
from signal_copier.infra.log import setup_logging, setup_parse_failures_log
from signal_copier.telegram.client import TelegramClient, TelegramConfigError
from signal_copier.telegram.listener import Listener

# Bounded as a safety net. M5's dump_consumer drains instantly; M6's
# scheduler drains at ~1 signal/min so the cap is never hit.
_SIGNALS_QUEUE_MAXSIZE: int = 1000


def _build_dump_consumer(
    queue: asyncio.Queue,
) -> "asyncio.Task[None]":
    """Return an asyncio Task that drains `queue` and pretty-prints each Signal.

    D-17: lives in __main__ as a local helper. M6 will replace this
    body with the scheduler (or delete it entirely when M6 owns the
    consumer).
    """
    async def _consume() -> None:
        while True:
            signal = await queue.get()
            try:
                print(json.dumps(asdict(signal), indent=2, default=str))
            finally:
                queue.task_done()

    return asyncio.create_task(_consume(), name="dump_consumer")


async def _run(config: Config) -> int:
    """Async main: wire up the pipeline and run until cancelled or fatal error."""
    db: Database | None = None
    tg: TelegramClient | None = None
    dump_task: asyncio.Task[None] | None = None
    try:
        db = await Database.connect(config.database_url)
        tg = TelegramClient(
            api_id=config.telegram_api_id,
            api_hash=config.telegram_api_hash,
            phone=config.telegram_phone,
            session_string=config.telegram_session_string,
            target_chat=config.telegram_target_chat,
        )
        await tg.connect()

        signals_queue: asyncio.Queue = asyncio.Queue(maxsize=_SIGNALS_QUEUE_MAXSIZE)
        parse_failures = setup_parse_failures_log(config.log_path.parent)

        listener = Listener(
            target_chat_id=tg.target_chat_id,
            state_store=db.state_store,
            queue=signals_queue,
            config=config,
            parse_failures_logger=parse_failures,
        )
        tg.add_message_handler(listener.on_new_message)
        tg.add_message_handler(listener.on_message_edited)

        dump_task = _build_dump_consumer(signals_queue)

        print(
            f"🟢 signal_copier M5 started\n"
            f"   Mode: {'dry_run' if config.dry_run else 'live demo'}\n"
            f"   Timezone: {config.timezone}\n"
            f"   Target chat: {config.telegram_target_chat} (chat_id={tg.target_chat_id})\n"
            f"   Watching for new messages and edits...\n"
        )

        await tg.start()  # blocks until disconnect or re-raise
        return 0
    finally:
        if dump_task is not None:
            dump_task.cancel()
            try:
                await dump_task
            except (asyncio.CancelledError, Exception):
                pass
        if tg is not None:
            await tg.close()
        if db is not None:
            await db.close()


def main() -> int:
    try:
        config = Config()
    except ValidationError as exc:
        sys.stderr.write(f"❌ Config validation failed:\n{exc}\n")
        return 2

    setup_logging(config.log_path)

    try:
        return asyncio.run(_run(config))
    except DatabaseConnectionError as exc:
        sys.stderr.write(f"❌ {exc}\n")
        return 1
    except TelegramConfigError as exc:
        sys.stderr.write(f"❌ {exc}\n")
        return 2
    except KeyboardInterrupt:
        print("\n🔴 signal_copier stopping (SIGINT)")
        return 0
    except Exception as exc:
        sys.stderr.write(f"❌ Unhandled error: {type(exc).__name__}: {exc}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_main.py -v`
Expected: all M2 and M5 tests PASS.

- [ ] **Step 6: Add the M5 test modules to the mypy override in `pyproject.toml`**

Open `pyproject.toml`. Find the `[[tool.mypy.overrides]]` block (around line 56):

```toml
[[tool.mypy.overrides]]
# Tests use Pydantic private APIs (_env_file), untyped **kwargs helpers, and
# rely on mypy-not-knowable narrowing (e.g. transition() returning a state the
# caller then asserts is non-None). Keep src strict; relax tests only.
module = ["test_config", "test_db", "test_gale_math", "test_main", "test_parser", "test_state_machine"]
ignore_errors = true
```

Replace the `module` list with:

```toml
module = [
    "test_config", "test_db", "test_gale_math", "test_main", "test_parser",
    "test_state_machine",
    "test_clock", "test_log", "test_auth",
    "test_telegram_client", "test_telegram_listener",
]
ignore_errors = true
```

- [ ] **Step 7: Commit**

```bash
git add src/signal_copier/__main__.py tests/test_main.py pyproject.toml
git commit -m "M5: wire __main__.py for the Telegram listener pipeline"
```

---

## Task 11: Lint, type-check, and full test-run verification

**Files:** none (verification task)

- [ ] **Step 1: Run `ruff check` on the new code**

Run: `ruff check src/signal_copier/telegram/ src/signal_copier/infra/clock.py src/signal_copier/infra/log.py src/signal_copier/__main__.py`
Expected: zero errors.

- [ ] **Step 2: Run `ruff format --check` on the new code**

Run: `ruff format --check src/signal_copier/telegram/ src/signal_copier/infra/clock.py src/signal_copier/infra/log.py src/signal_copier/__main__.py`
Expected: zero formatting issues (or auto-fix with `ruff format` if any).

- [ ] **Step 3: Run `mypy --strict` on the new src code**

Run: `mypy src/signal_copier/telegram/ src/signal_copier/infra/clock.py src/signal_copier/infra/log.py src/signal_copier/__main__.py`
Expected: zero errors. (The test modules are excluded by the mypy override from Task 10 Step 6; this command targets only the src files.)

- [ ] **Step 4: Run the full test suite**

Run: `pytest -q`
Expected: all existing tests (M1 parser, M2 state machine + config, M3 dry-run broker, M4 db) pass; all new M5 tests (clock, log, telegram fixtures loaded by telegram_listener + telegram_client + auth, main) pass. Total: ~62+ tests passing.

- [ ] **Step 5: Manual smoke — `python -m signal_copier.telegram.auth` shows the right error message with no env**

Run: `python -m signal_copier.telegram.auth`
Expected: prints `❌ Config validation failed; check API_ID / API_HASH / PHONE in .env: ...` and exits 2. This proves the entry point is wired correctly without needing a real Telegram account.

(Do NOT actually run auth interactively — that requires a real SMS code. The exit-code-2 path is the smoke test.)

- [ ] **Step 6: Commit any auto-fixes from Steps 1-3 (if any)**

```bash
git add -u
git commit -m "M5: ruff/mypy auto-fixes" || echo "no changes to commit"
```

- [ ] **Step 7: Final commit summary**

```bash
git log --oneline -15
```

Expected: ~11 commits for M5, each with a clear message starting with `M5:`.

---

## Self-Review

Performed after writing the plan.

**1. Spec coverage:**

| Spec section / requirement | Implementing task(s) |
|---|---|
| §4.1 `infra/clock.py` 5 functions | Task 4 |
| §4.2 `TelegramClient` class | Task 7 |
| §4.2 `TelegramConfigError` | Task 7 |
| §4.2 `compute_backoff_seconds` | Task 7 |
| §4.2 reconnect supervisor (D-11) | Task 7 |
| §4.2 FloodWaitError handling (D-7) | Task 7 |
| §4.2 add_message_handler (D-6) | Task 7 |
| §4.2 idempotent close | Task 7 |
| §4.2 eager config validation (D-12) | Task 7 |
| §4.2 target_chat resolution (D-10, D-18) | Task 7 |
| §4.3 `Listener` class | Task 8 |
| §4.3 `on_new_message` / `on_message_edited` shared body (D-6) | Task 8 |
| §4.3 chat filter (D-13) | Task 8 |
| §4.3 outgoing filter (D-14) | Task 8 |
| §4.3 empty-message filter | Task 8 |
| §4.3 parse_signal → time-window → build → upsert → enqueue → print | Task 8 |
| §4.3 parse-failure logging | Task 8 |
| §4.3 out-of-window logging | Task 8 |
| §4.3 pretty-print to stdout | Task 8 |
| §4.4 `auth.py` `main()` entrypoint | Task 9 |
| §4.4 `_read_creds` validation | Task 9 |
| §4.4 `_do_auth` Telethon interactive flow | Task 9 |
| §4.4 timeout protection | Task 9 |
| §4.4 success print format | Task 9 |
| §4.5 `telegram/__init__.py` empty | Task 2 |
| §4.6 `infra/log.py` `setup_parse_failures_log` | Task 5 |
| §4.7 `__main__.py` full wiring | Task 10 |
| §4.7 `dump_consumer` helper | Task 10 |
| §4.7 SIGINT/SIGTERM cleanup | Task 10 |
| §4.7 error-to-exit-code mapping | Task 10 |
| §5.1 pyproject.toml `telethon` dep | Task 1 |
| §5.1 pyproject.toml `signal-copier-auth` script | Task 3 |
| §5.1 pyproject.toml mypy override update | Task 10 Step 6 |
| §4.8 `tests/_telegram_fixtures.py` | Task 6 |
| §7.1 `test_clock.py` ~10 tests | Task 4 (12 tests written; covers all spec items) |
| §7.2 `test_telegram_listener.py` ~13 tests | Task 8 (13 tests written; covers all spec items) |
| §7.3 `test_telegram_client.py` ~4 tests | Task 7 (11 tests written; covers compute_backoff, init validation, target_chat_id, add_message_handler, close idempotency) |
| §7.4 `test_log.py` for parse_failures | Task 5 (5 tests written) |
| §7.4 `test_auth.py` for main() | Task 9 (5 tests written) |
| §7.4 `test_main.py` M5 wiring | Task 10 (2 tests added) |

All spec items have a task. No gaps.

**2. Placeholder scan:**

Searched the plan for "TBD", "TODO", "implement later", "add appropriate", "fill in details", "similar to Task N". None found. Every code block contains actual implementation. Every test block contains actual test code.

**3. Type consistency:**

- `TelegramClient.__init__` signature in Task 7 matches what `__main__.py` (Task 10) calls it with. Verified: `api_id`, `api_hash`, `phone`, `session_string`, `target_chat` — all keyword args, all supplied.
- `Listener.__init__` signature in Task 8 matches what `__main__.py` calls it with. Verified: `target_chat_id`, `state_store`, `queue`, `config`, `parse_failures_logger` — all keyword args, all supplied.
- `compute_backoff_seconds(attempt: int) -> float` defined in Task 7; used internally in Task 7 only. No external callers, so no signature drift risk.
- `setup_parse_failures_log(log_dir: Path) -> logging.Logger` defined in Task 5; used by `__main__.py` in Task 10 with `config.log_path.parent`. Verified: `Config.log_path` is a `Path` and `.parent` is a `Path`. ✓
- `Signal` dataclass is the existing M1 type; all 10 fields (including the M2 additions `trigger_unix_initial/gale1/gale2`) are populated in `Listener._process_message` (Task 8). Verified against `src/signal_copier/domain/signal.py`.
- `parse_signal`, `derive_signal_id`, `ParseFailure`, `FailureReason` all imported from `signal_copier.domain.signal`; signatures match M1's `tests/test_parser.py` usage.

No type inconsistencies found. Plan is ready to execute.
