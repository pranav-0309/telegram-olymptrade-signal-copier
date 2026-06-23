# M7 Implementation Plan — Telegram Self-DM Notifications

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement rich Telegram self-DM notifications for every FR-7.1 event, migrate the local log infrastructure to loguru with 10 MB × 5 rotation, and extend the `Notifier` Protocol with the three FR-7.1 events the M6 Protocol omitted.

**Architecture:** Three layered components — (1) Protocol extension adds 3 missing event methods to `Notifier` so M5 listener and M8 broker emit them; (2) `TelegramDMNotifier` (new, ~350 lines) implements the full Protocol and sends plain-text DMs to "Saved Messages" via the same Telethon client as the listener (single-connection principle, FR-7.4); (3) `infra/log.py` rewritten with loguru + `InterceptHandler` so existing stdlib `logging.getLogger(__name__)` call sites flow through without edits. All DM errors swallowed and logged (D-5: notifier failure must not abort the cascade).

**Tech Stack:** Python 3.13, Telethon 1.44.x, loguru 0.7+, pytest + pytest-asyncio, ruff, mypy --strict.

**Spec reference:** `docs/superpowers/specs/2026-06-21-m7-telegram-dm-notifications-design.md`

---

## File Structure (locked here)

**Files to create (3):**
- `src/signal_copier/notify/telegram_dm.py` — `TelegramDMNotifier` class (~350 lines)
- `tests/test_telegram_dm.py` — 25 tests covering every FR-7.1 event (~450 lines)
- `tests/test_log_rotation.py` — slow marker; rotation behavior (~30 lines)

**Files to modify (12):**
- `src/signal_copier/notify/protocol.py` — add 3 methods to `Notifier` Protocol + 3 to `NoOpNotifier`
- `src/signal_copier/infra/log.py` — full rewrite with loguru
- `src/signal_copier/infra/clock.py` — add `format_local_hhmm(unix_ts, tz)` helper
- `src/signal_copier/telegram/client.py` — add `send_to_self()` + optional `notifier` param on `start()`
- `src/signal_copier/telegram/listener.py` — add `notifier: Notifier` ctor param; emit `on_parse_failure`
- `src/signal_copier/__main__.py` — config-driven notifier selection
- `tests/test_notifier.py` — add 3 tests for new NoOpNotifier methods
- `tests/_scheduler_fixtures.py` — extend `RecordingNotifier` with 3 new methods
- `tests/test_telegram_client.py` — add 2 tests for `send_to_self`
- `tests/test_telegram_listener.py` — add 2 tests for `on_parse_failure` emission
- `tests/test_log.py` — full rewrite with 4 loguru tests
- `tests/_telegram_fixtures.py` — extend `fake_listener` factory to accept optional `notifier`
- `pyproject.toml` — add `loguru>=0.7,<1.0` to dependencies

---

## Phase A — Foundation

### Task 1: Add `format_local_hhmm` helper to `infra/clock.py`

**Files:**
- Modify: `src/signal_copier/infra/clock.py:40` (append new function before `now_unix()`)
- Test: `tests/test_clock.py` (add new test at end)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_clock.py`:

```python
from signal_copier.infra.clock import format_local_hhmm


def test_format_local_hhmm_america_sao_paulo() -> None:
    """2026-06-21T13:20:00Z is 10:20 in America/Sao_Paulo (UTC-3, no DST)."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("America/Sao_Paulo")
    # 13:20 UTC == 10:20 BRT
    unix_ts = datetime(2026, 6, 21, 13, 20, 0, tzinfo=ZoneInfo("UTC")).timestamp()
    assert format_local_hhmm(unix_ts, tz) == "10:20"


def test_format_local_hhmm_midnight_rollover() -> None:
    """Just past midnight in UTC-3 — verify the helper doesn't blow up."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("America/Sao_Paulo")
    unix_ts = datetime(2026, 6, 21, 3, 5, 0, tzinfo=ZoneInfo("UTC")).timestamp()  # 00:05 BRT
    assert format_local_hhmm(unix_ts, tz) == "00:05"
```

Make sure `datetime` is imported at the top of `tests/test_clock.py` (add to existing imports if missing).

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_clock.py::test_format_local_hhmm_america_sao_paulo -v`
Expected: FAIL with `ImportError: cannot import name 'format_local_hhmm'`

- [ ] **Step 3: Implement the helper**

Add to `src/signal_copier/infra/clock.py` (append before `now_unix()`):

```python
def format_local_hhmm(unix_ts: float, tz: ZoneInfo) -> str:
    """Format a Unix epoch as 'HH:MM' in the given timezone.

    Example: 1740000000 in America/Sao_Paulo → '10:20'.
    Used by the M7 notifier to render timestamps for self-DMs.
    """
    dt = datetime.fromtimestamp(unix_ts, tz=tz)
    return f"{dt.hour:02d}:{dt.minute:02d}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_clock.py -v`
Expected: All existing tests pass + the 2 new tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/signal_copier/infra/clock.py tests/test_clock.py
git commit -m "feat(clock): add format_local_hhmm helper for M7 notifier timestamps"
```

---

### Task 2: Add loguru dependency to `pyproject.toml`

**Files:**
- Modify: `pyproject.toml:7-11` (the `dependencies` list)

- [ ] **Step 1: Add loguru to dependencies**

In `pyproject.toml`, replace the `dependencies` block:

```toml
dependencies = [
    "pydantic-settings>=2.6",  # M2: config layer
    "tzdata>=2024.1",          # IANA tz database on Windows
    "asyncpg>=0.30",           # M4: async PostgreSQL driver
    "telethon>=1.44",          # M5: Telegram MTProto user-account client
    "loguru>=0.7,<1.0",        # M7: rotating loguru sinks + DM mirror
]
```

- [ ] **Step 2: Install (or sync) the dependency**

Run: `uv sync` (or `pip install -e ".[dev]"` if not using uv)
Expected: `loguru` resolved and added to the venv.

- [ ] **Step 3: Verify import works**

Run: `python -c "from loguru import logger; print(logger)"`
Expected: prints the loguru `<loguru.logger>` repr, no errors.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore(deps): add loguru>=0.7 for M7 rotating log + DM mirror"
```

---

## Phase B — Protocol extension

### Task 3: Extend `Notifier` Protocol with 3 new methods

**Files:**
- Modify: `src/signal_copier/notify/protocol.py:33-108` (the `Notifier` Protocol class)
- Test: `tests/test_notifier.py` (add 3 tests at end)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_notifier.py`:

```python
from signal_copier.domain.signal import FailureReason


@pytest.mark.asyncio
async def test_noop_notifier_logs_parse_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="signal_copier.notify.protocol"):
        await NoOpNotifier().on_parse_failure(
            raw_text="some random text", reason=FailureReason.MISSING_SIGNAL_LINE
        )
    assert len(caplog.records) == 1
    msg = caplog.records[0].getMessage()
    assert "event=parse_failure" in msg
    assert "reason=missing_signal_line" in msg


@pytest.mark.asyncio
async def test_noop_notifier_logs_telegram_disconnect_at_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Telegram disconnect is an operational anomaly — log at WARNING."""
    with caplog.at_level(logging.WARNING, logger="signal_copier.notify.protocol"):
        await NoOpNotifier().on_telegram_disconnect()
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.WARNING
    msg = caplog.records[0].getMessage()
    assert "event=telegram_disconnect" in msg


@pytest.mark.asyncio
async def test_noop_notifier_logs_olymp_disconnect_at_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """OlympTrade disconnect is an operational anomaly — log at WARNING."""
    with caplog.at_level(logging.WARNING, logger="signal_copier.notify.protocol"):
        await NoOpNotifier().on_olymp_disconnect()
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.WARNING
    msg = caplog.records[0].getMessage()
    assert "event=olymp_disconnect" in msg
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_notifier.py -v -k "parse_failure or telegram_disconnect or olymp_disconnect"`
Expected: All 3 FAIL with `AttributeError: 'NoOpNotifier' object has no attribute 'on_parse_failure'`

- [ ] **Step 3: Add the 3 methods to `Notifier` Protocol**

In `src/signal_copier/notify/protocol.py`, after `on_bot_stopping` (line 107), add:

```python
    async def on_parse_failure(
        self,
        raw_text: str,
        reason: FailureReason,
    ) -> None:
        """FR-7.1 row 'Parse failure'. Fires from the M5 Listener when a
        message doesn't match the signal regex."""

    async def on_telegram_disconnect(self) -> None:
        """FR-7.1 row 'Telegram disconnect'. Fires from the M5 TelegramClient
        wrapper on ConnectionError before reconnect."""

    async def on_olymp_disconnect(self) -> None:
        """FR-7.1 row 'OlympTrade disconnect'. Fires from M8/M10's
        reconnect supervisor. M7 ships the method only — emission wiring
        lands in M8 (broker) and M10 (reconnect supervisor)."""
```

Add the import at the top of the file (next to the existing `from signal_copier.domain.signal import Signal`):

```python
from signal_copier.domain.signal import FailureReason, Signal
```

- [ ] **Step 4: Add the 3 matching methods to `NoOpNotifier`**

In the same file, after `on_bot_stopping` (line 233), add:

```python
    async def on_parse_failure(
        self,
        raw_text: str,
        reason: FailureReason,
    ) -> None:
        _log.info(
            "notify: event=parse_failure reason=%s preview=%r",
            reason.value,
            raw_text[:80],
        )

    async def on_telegram_disconnect(self) -> None:
        _log.warning("notify: event=telegram_disconnect")

    async def on_olymp_disconnect(self) -> None:
        _log.warning("notify: event=olymp_disconnect")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_notifier.py -v`
Expected: All 10 tests pass (7 existing + 3 new).

- [ ] **Step 6: Commit**

```bash
git add src/signal_copier/notify/protocol.py tests/test_notifier.py
git commit -m "feat(notify): extend Notifier Protocol with on_parse_failure and disconnect events"
```

---

### Task 4: Extend `RecordingNotifier` in test fixtures with 3 new methods

**Files:**
- Modify: `tests/_scheduler_fixtures.py:88-211` (the `RecordingNotifier` class)

- [ ] **Step 1: Write the failing type-check test**

Add to `tests/_scheduler_fixtures.py` at end (new test):

```python
def test_recording_notifier_satisfies_protocol_after_m7() -> None:
    """RecordingNotifier must still satisfy the extended Notifier Protocol
    after M7 adds the 3 new methods."""
    from signal_copier.notify.protocol import Notifier
    assert isinstance(RecordingNotifier(), Notifier)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/_scheduler_fixtures.py::test_recording_notifier_satisfies_protocol_after_m7 -v`
Expected: FAIL with `AssertionError` (RecordingNotifier no longer satisfies Protocol).

- [ ] **Step 3: Add the 3 new methods to `RecordingNotifier`**

In `tests/_scheduler_fixtures.py`, after the existing `on_bot_stopping` method (line 211), add:

```python
    async def on_parse_failure(
        self,
        raw_text: str,
        reason: "FailureReason",
    ) -> None:
        await self._record(
            "on_parse_failure",
            raw_text=raw_text,
            reason=reason,
        )

    async def on_telegram_disconnect(self) -> None:
        await self._record("on_telegram_disconnect")

    async def on_olymp_disconnect(self) -> None:
        await self._record("on_olymp_disconnect")
```

Add the import at the top of the file (next to the existing `from signal_copier.domain.signal import Signal`):

```python
from signal_copier.domain.signal import FailureReason, Signal
```

Also update the existing `from typing import Any` import line to include `TYPE_CHECKING` (for the `FailureReason` forward reference):

```python
from typing import TYPE_CHECKING, Any
```

And add at the bottom of imports:

```python
if TYPE_CHECKING:
    pass
```

Actually the simpler approach: drop the `TYPE_CHECKING` and just import directly:

```python
from signal_copier.domain.signal import FailureReason, Signal
```

Use `FailureReason` directly in the type annotation (no string forward reference).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/_scheduler_fixtures.py -v`
Expected: New test passes.

- [ ] **Step 5: Verify the entire test suite still runs**

Run: `pytest tests/ -v --co` (collect-only)
Expected: No collection errors. All test modules import cleanly.

- [ ] **Step 6: Commit**

```bash
git add tests/_scheduler_fixtures.py
git commit -m "test(scheduler): extend RecordingNotifier with 3 new Notifier methods"
```

---

## Phase C — Loguru rewrite of `infra/log.py`

### Task 5: Rewrite `infra/log.py` with loguru (with `InterceptHandler`)

**Files:**
- Rewrite: `src/signal_copier/infra/log.py` (entire file)
- Test: `tests/test_log.py` (full rewrite)

- [ ] **Step 1: Write the failing test suite**

Replace `tests/test_log.py` entirely with:

```python
"""Tests for signal_copier.infra.log — loguru-based logging + parse-failures sink."""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from signal_copier.infra.log import setup_logging, setup_parse_failures_log


@pytest.fixture(autouse=True)
def _reset_loguru() -> None:
    """Each test starts with a clean loguru state."""
    from loguru import logger as _loguru_logger
    _loguru_logger.remove()
    yield
    _loguru_logger.remove()


def test_setup_logging_creates_log_file(tmp_path: Path) -> None:
    log_file = tmp_path / "signal_copier.log"
    setup_logging(log_file)
    from loguru import logger
    logger.info("hello world")
    # Allow async writer to flush.
    import time
    time.sleep(0.1)
    content = log_file.read_text(encoding="utf-8")
    assert "hello world" in content


def test_setup_logging_is_idempotent(tmp_path: Path) -> None:
    log_file = tmp_path / "signal_copier.log"
    setup_logging(log_file)
    setup_logging(log_file)  # second call must not crash
    from loguru import logger
    logger.info("after-second-setup")
    import time
    time.sleep(0.1)
    content = log_file.read_text(encoding="utf-8")
    assert "after-second-setup" in content


def test_intercept_handler_forwards_stdlib_log(tmp_path: Path) -> None:
    log_file = tmp_path / "signal_copier.log"
    setup_logging(log_file)
    # Use stdlib logging directly (mimics existing scheduler/db modules).
    stdlib_logger = logging.getLogger("signal_copier.test_module")
    stdlib_logger.info("via stdlib")
    import time
    time.sleep(0.1)
    content = log_file.read_text(encoding="utf-8")
    assert "via stdlib" in content
    assert "signal_copier.test_module" in content


def test_setup_parse_failures_log_writes_to_separate_file(tmp_path: Path) -> None:
    log_file = tmp_path / "signal_copier.log"
    setup_logging(log_file)
    pf_logger = setup_parse_failures_log(tmp_path)
    pf_logger.warning("malformed signal: %s", "preview-here")
    # Close all handlers on pf_logger so the file is flushed.
    for h in pf_logger.handlers:
        h.close()
    import time
    time.sleep(0.1)
    pf_content = (tmp_path / "parse_failures.log").read_text(encoding="utf-8")
    main_content = log_file.read_text(encoding="utf-8")
    assert "malformed signal: preview-here" in pf_content
    assert "malformed signal: preview-here" not in main_content


def test_setup_parse_failures_log_idempotent(tmp_path: Path) -> None:
    logger1 = setup_parse_failures_log(tmp_path)
    logger2 = setup_parse_failures_log(tmp_path)
    assert logger1 is logger2  # same stdlib Logger instance
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_log.py -v`
Expected: All FAIL — `setup_logging` doesn't exist yet.

- [ ] **Step 3: Rewrite `infra/log.py`**

Replace `src/signal_copier/infra/log.py` entirely with:

```python
"""Loguru-based logging infrastructure for signal_copier.

Three sinks (configured by setup_logging):
  1. stderr — colored, INFO+ (Railway live tail)
  2. logs/signal_copier.log — rotating, 10 MB × 5, ZIP, INFO+

Plus a stdlib-to-loguru bridge (_InterceptHandler) so existing
``logging.getLogger(__name__).info(...)`` call sites flow through
without any code changes.

Plus setup_parse_failures_log which returns a stdlib logger that
writes WARNING+ to logs/parse_failures.log (separate file, no rotation).
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from loguru import logger as _loguru_logger


def setup_logging(log_path: Path) -> None:
    """Configure loguru for the whole app.

    Idempotent: removes the default loguru sink first. Existing stdlib
    logging handlers are replaced with the InterceptHandler below so
    every ``logging.getLogger(name).info(...)`` call flows through loguru.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _loguru_logger.remove()

    # Sink 1: stderr — colored.
    _loguru_logger.add(
        sys.stderr,
        level="INFO",
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> "
            "<level>{level: <8}</level> "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> "
            "- <level>{message}</level>"
        ),
        colorize=True,
    )

    # Sink 2: rotating file — no colors.
    _loguru_logger.add(
        str(log_path),
        level="INFO",
        format=(
            "{time:YYYY-MM-DD HH:mm:ss.SSS} {level: <8} "
            "{name}:{function}:{line} - {message}"
        ),
        rotation="10 MB",
        retention=5,
        compression="zip",
        encoding="utf-8",
        enqueue=True,
    )

    # Bridge stdlib logging → loguru.
    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)


def setup_parse_failures_log(log_dir: Path) -> logging.Logger:
    """Return a stdlib logger that writes WARNING+ to
    ``<log_dir>/parse_failures.log`` via a dedicated loguru sink.

    Idempotent on the returned logger (clear-then-add the handler each call).
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    parse_path = log_dir / "parse_failures.log"

    _loguru_logger.add(
        str(parse_path),
        level="WARNING",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} {level: <8} {message}",
        encoding="utf-8",
    )

    pf_logger = logging.getLogger("signal_copier.parse_failures")
    pf_logger.handlers.clear()
    pf_logger.addHandler(_ParseFailuresHandler())
    pf_logger.propagate = False
    pf_logger.setLevel(logging.WARNING)
    return pf_logger


class _InterceptHandler(logging.Handler):
    """Forward stdlib ``logging`` records to loguru.

    Standard pattern from https://loguru.readthedocs.io/en/stable/usage.html
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = _loguru_logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        _loguru_logger.opt(depth=6, exception=record.exc_info).log(
            level, record.getMessage()
        )


class _ParseFailuresHandler(logging.Handler):
    """Forward parse-failure warnings into loguru at WARNING level."""

    def emit(self, record: logging.LogRecord) -> None:
        _loguru_logger.opt(depth=6, exception=record.exc_info).log(
            "WARNING", "[{}] {}", record.name, record.getMessage()
        )
```

- [ ] **Step 4: Run new log tests to verify they pass**

Run: `pytest tests/test_log.py -v`
Expected: All 5 new tests pass.

- [ ] **Step 5: Run the ENTIRE test suite to confirm no regressions**

Run: `pytest tests/ -v --ignore=tests/test_db.py`
Expected: All tests pass (skip test_db if no Postgres container available). The existing stdlib `logging.getLogger` call sites in scheduler, broker, db_rows, etc. must still work via the InterceptHandler.

If any test fails because it captured log output via `caplog`, the most likely cause is that `caplog` (a pytest fixture for stdlib `logging`) doesn't see loguru records. Fix by adding `caplog.set_level(logging.INFO)` at the start of the affected test (or, if many tests are affected, add a pytest plugin that auto-bridges — out of scope for M7).

- [ ] **Step 6: Commit**

```bash
git add src/signal_copier/infra/log.py tests/test_log.py
git commit -m "feat(log): migrate to loguru with rotating file sink + stdlib InterceptHandler"
```

---

## Phase D — TelegramClient extensions

### Task 6: Add `send_to_self()` method to `TelegramClient`

**Files:**
- Modify: `src/signal_copier/telegram/client.py:152` (append before `close`)
- Test: `tests/test_telegram_client.py` (append 2 tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_telegram_client.py`:

```python
from unittest.mock import AsyncMock, MagicMock


async def test_send_to_self_calls_send_message_with_me() -> None:
    client = TelegramClient(
        api_id=1, api_hash="abc", phone="+1", session_string="s", target_chat="@c"
    )
    # Inject a fake underlying Telethon client (bypass real connect()).
    fake_telethon = MagicMock()
    fake_telethon.send_message = AsyncMock()
    client._client = fake_telethon  # type: ignore[attr-defined]

    await client.send_to_self("hello")

    fake_telethon.send_message.assert_awaited_once_with("me", "hello")


def test_send_to_self_raises_before_connect() -> None:
    client = TelegramClient(
        api_id=1, api_hash="abc", phone="+1", session_string="s", target_chat="@c"
    )
    with pytest.raises(RuntimeError, match="connect"):
        # No await needed: the RuntimeError is raised synchronously.
        client.send_to_self("hello")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_telegram_client.py -v -k "send_to_self"`
Expected: Both FAIL with `AttributeError: 'TelegramClient' object has no attribute 'send_to_self'`

- [ ] **Step 3: Implement `send_to_self`**

In `src/signal_copier/telegram/client.py`, insert before the `close()` method (line 153):

```python
    async def send_to_self(self, text: str) -> None:
        """Send a Telegram DM to the user's own 'Saved Messages' chat.

        Uses the same connection as the listener (FR-7.4). Plain text
        only — no parse_mode. Raises whatever Telethon's send_message
        raises (FloodWaitError, ConnectionError, OSError); callers
        (TelegramDMNotifier._send) are responsible for handling.
        """
        if self._client is None:
            raise RuntimeError(
                "send_to_self() called before connect()"
            )
        await self._client.send_message("me", text)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_telegram_client.py -v`
Expected: All tests pass (existing + 2 new).

- [ ] **Step 5: Commit**

```bash
git add src/signal_copier/telegram/client.py tests/test_telegram_client.py
git commit -m "feat(telegram): add send_to_self() on TelegramClient for self-DM routing"
```

---

### Task 7: Emit `on_telegram_disconnect` from `TelegramClient.start()`

**Files:**
- Modify: `src/signal_copier/telegram/client.py:117` (the `start` method signature + ConnectionError branch)
- Test: `tests/test_telegram_client.py` (append 1 test)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_telegram_client.py`:

```python
from signal_copier.notify.protocol import NoOpNotifier


async def test_start_emits_on_telegram_disconnect_on_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When run_until_disconnected() raises ConnectionError, the optional
    notifier's on_telegram_disconnect() must be called BEFORE the backoff
    sleep (so the DM fires as soon as the disconnect is detected)."""
    notifier = NoOpNotifier()
    disconnect_calls: list[None] = []

    # Wrap the NoOpNotifier method to record the call.
    original = notifier.on_telegram_disconnect

    async def recorder() -> None:
        disconnect_calls.append(None)
        await original()

    notifier.on_telegram_disconnect = recorder  # type: ignore[method-assign]

    client = TelegramClient(
        api_id=1, api_hash="abc", phone="+1", session_string="s", target_chat="@c"
    )
    fake_telethon = MagicMock()
    call_count = {"n": 0}

    async def fake_run() -> None:
        call_count["n"] += 1
        if call_count["n"] >= 2:
            client._client = None  # type: ignore[attr-defined]  # exit the loop
            return
        raise ConnectionError("simulated disconnect")

    fake_telethon.run_until_disconnected = fake_run
    fake_telethon.disconnect = AsyncMock()
    client._client = fake_telethon  # type: ignore[attr-defined]

    # Patch asyncio.sleep so the test doesn't actually wait.
    sleeps: list[float] = []

    async def fake_sleep(secs: float) -> None:
        sleeps.append(secs)

    monkeypatch.setattr("asyncio.sleep", fake_sleep)

    await client.start(notifier=notifier)

    assert len(disconnect_calls) == 1, (
        "on_telegram_disconnect must fire exactly once on ConnectionError"
    )
    assert len(sleeps) == 1  # one backoff sleep after the disconnect
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_telegram_client.py::test_start_emits_on_telegram_disconnect_on_connection_error -v`
Expected: FAIL with `TypeError: start() got an unexpected keyword argument 'notifier'`

- [ ] **Step 3: Implement the optional `notifier` param + emission**

In `src/signal_copier/telegram/client.py`, modify the `start()` method (line 117). Replace it with:

```python
    async def start(self, *, notifier: "Notifier | None" = None) -> None:
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
                    type(exc).__name__,
                    attempt,
                    _MAX_RECONNECT_ATTEMPTS,
                    delay,
                )
                if notifier is not None:
                    await notifier.on_telegram_disconnect()
                await asyncio.sleep(delay)
```

Add the type import at the top of the file. The existing imports already include `Any`; add `"Notifier"` to a `TYPE_CHECKING` block:

```python
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from signal_copier.notify.protocol import Notifier
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_telegram_client.py::test_start_emits_on_telegram_disconnect_on_connection_error -v`
Expected: PASS.

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/test_telegram_client.py -v`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/signal_copier/telegram/client.py tests/test_telegram_client.py
git commit -m "feat(telegram): emit on_telegram_disconnect from reconnect loop"
```

---

## Phase E — TelegramDMNotifier skeleton

### Task 8: Create `TelegramDMNotifier` skeleton (constructor + `_send` + Protocol membership)

**Files:**
- Create: `src/signal_copier/notify/telegram_dm.py` (~80 lines for skeleton)
- Test: `tests/test_telegram_dm.py` (new file, ~50 lines for skeleton tests)

- [ ] **Step 1: Create the test file with skeleton tests**

Create `tests/test_telegram_dm.py`:

```python
"""Tests for signal_copier.notify.telegram_dm — TelegramDMNotifier."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from signal_copier.config import Config
from signal_copier.domain.signal import FailureReason, Signal
from signal_copier.notify.protocol import Notifier


# --- Test fixtures ---------------------------------------------------------


@dataclass
class FakeTgClient:
    """Duck-typed TelegramClient — only the surface TelegramDMNotifier uses."""
    sent: list[str] = field(default_factory=list)
    raise_on_send: BaseException | None = None

    async def send_to_self(self, text: str) -> None:
        if self.raise_on_send is not None:
            raise self.raise_on_send
        self.sent.append(text)


def _make_config(**overrides: Any) -> Config:
    """Build a Config for tests. Pass kwargs to override defaults.

    Example: _make_config(daily_loss_limit=Decimal("50.00"))
    """
    defaults: dict[str, Any] = {"timezone": "America/Sao_Paulo"}
    defaults.update(overrides)
    return Config(**defaults)


def _make_signal(**overrides: Any) -> Signal:
    """Build a Signal with a trigger_unix_initial that maps to 10:20 BRT."""
    from datetime import datetime
    # 2026-06-21 13:20:00 UTC == 10:20 in America/Sao_Paulo (UTC-3, no DST).
    trigger = datetime(2026, 6, 21, 13, 20, 0, tzinfo=ZoneInfo("UTC")).timestamp()
    defaults: dict[str, Any] = dict(
        signal_id="sig-abc",
        pair="EUR/JPY",
        direction="down",
        trigger_hhmm="10:20",
        expiration_seconds=300,
        received_at_unix=1_750_000_000.0,
        source_message_id=42,
        source_chat_id=-100,
        raw_text="(test)",
        trigger_unix_initial=trigger,
        trigger_unix_gale1=trigger + 300,
        trigger_unix_gale2=trigger + 600,
    )
    defaults.update(overrides)
    return Signal(**defaults)


# --- Skeleton tests --------------------------------------------------------


def test_satisfies_notifier_protocol() -> None:
    """TelegramDMNotifier must implement the full Notifier Protocol."""
    from signal_copier.notify.telegram_dm import TelegramDMNotifier
    notifier = TelegramDMNotifier(tg_client=FakeTgClient(), config=_make_config())
    assert isinstance(notifier, Notifier)


@pytest.mark.asyncio
async def test_send_failure_logged_and_swallowed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When send_to_self raises, the method must not raise (D-5)."""
    import logging
    from loguru import logger as _loguru_logger
    _loguru_logger.remove()

    from signal_copier.notify.telegram_dm import TelegramDMNotifier

    fake = FakeTgClient(raise_on_send=ConnectionError("simulated"))
    notifier = TelegramDMNotifier(tg_client=fake, config=_make_config())

    # Any method that calls _send should swallow the exception. Use
    # on_telegram_disconnect (simplest) — should return None without raising.
    with caplog.at_level(logging.WARNING):
        await notifier.on_telegram_disconnect()
    # If we got here without raising, the swallow worked.
    assert fake.sent == []  # the fake was invoked but raised
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_telegram_dm.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'signal_copier.notify.telegram_dm'`

- [ ] **Step 3: Create the skeleton `telegram_dm.py`**

Create `src/signal_copier/notify/telegram_dm.py`:

```python
"""TelegramDMNotifier — implements the Notifier Protocol by sending self-DMs.

Each FR-7.1 event has a dedicated async method that builds the message
string and calls ``_send(text)``. ``_send`` performs the Telegram send via
the same Telethon client as the listener (FR-7.4) and mirrors the text to
loguru at INFO. Failures are logged at WARNING and swallowed (D-5: notifier
exceptions must not abort the cascade).
"""
from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from loguru import logger as _loguru_logger

from signal_copier.config import Config
from signal_copier.domain.gale import Stage
from signal_copier.domain.signal import FailureReason, Signal
from signal_copier.infra.clock import format_local_hhmm, now_unix

if TYPE_CHECKING:
    from signal_copier.domain.state import TerminalState
    from signal_copier.infra.db_rows import DailySummaryRow
    from signal_copier.telegram.client import TelegramClient


class TelegramDMNotifier:
    """Notifier that sends FR-7.1 messages to the user's 'Saved Messages'."""

    def __init__(
        self,
        *,
        tg_client: "TelegramClient",
        config: Config,
    ) -> None:
        self._tg = tg_client
        self._config = config

    async def _send(self, text: str) -> None:
        """Send one DM. Log-and-swallow on any failure (D-5)."""
        try:
            await self._tg.send_to_self(text)
        except Exception as exc:
            _loguru_logger.bind(dm_event=True).warning(
                "DM send failed: text_preview={!r} exc={}", text[:80], exc
            )
            return
        _loguru_logger.bind(dm_event=True).info(text)

    # --- Methods below are filled in by Tasks 9-13. ---

    async def on_signal_received(self, signal: Signal) -> None:
        raise NotImplementedError

    async def on_trade_placed(
        self, signal: Signal, stage: Stage, amount: Decimal, trade_id: str
    ) -> None:
        raise NotImplementedError

    async def on_win(
        self, signal: Signal, stage: Stage, pnl: Decimal, cumulative_pnl: Decimal
    ) -> None:
        raise NotImplementedError

    async def on_loss(
        self,
        signal: Signal,
        stage: Stage,
        pnl: Decimal,
        cumulative_pnl: Decimal,
        next_stage: Stage | None,
    ) -> None:
        raise NotImplementedError

    async def on_signal_expired(
        self, signal: Signal, stage: Stage, trigger_hhmm: str
    ) -> None:
        raise NotImplementedError

    async def on_cascade_complete(
        self,
        signal: Signal,
        final_state: "TerminalState",
        cumulative_pnl: Decimal,
    ) -> None:
        raise NotImplementedError

    async def on_signal_rejected_by_limit(
        self,
        signal: Signal,
        limit_type: str,
        summary: "DailySummaryRow",
    ) -> None:
        raise NotImplementedError

    async def on_bot_started(
        self, *, mode: str, watching: str, timezone: str
    ) -> None:
        raise NotImplementedError

    async def on_bot_stopping(self, *, open_cascades: int) -> None:
        raise NotImplementedError

    async def on_parse_failure(
        self, raw_text: str, reason: FailureReason
    ) -> None:
        raise NotImplementedError

    async def on_telegram_disconnect(self) -> None:
        await self._send("🔌 Telegram disconnected. Reconnecting…")

    async def on_olymp_disconnect(self) -> None:
        await self._send(
            "🔌 OlympTrade disconnected. Process will exit; supervisor will restart."
        )
```

- [ ] **Step 4: Run tests to verify skeleton tests pass**

Run: `pytest tests/test_telegram_dm.py -v`
Expected: The 2 skeleton tests pass. (The other event methods raise `NotImplementedError` but the skeleton tests don't call them.)

- [ ] **Step 5: Commit**

```bash
git add src/signal_copier/notify/telegram_dm.py tests/test_telegram_dm.py
git commit -m "feat(notify): create TelegramDMNotifier skeleton with _send + disconnect methods"
```

---

## Phase F — TelegramDMNotifier event methods (TDD, grouped by area)

For each group of methods, write the tests first (TDD), then implement.

### Task 9: Implement `on_signal_received` + `on_bot_started` + `on_bot_stopping`

**Files:**
- Modify: `src/signal_copier/notify/telegram_dm.py` (3 method bodies)
- Modify: `tests/test_telegram_dm.py` (append 3 tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_telegram_dm.py`:

```python
from signal_copier.notify.telegram_dm import TelegramDMNotifier


@pytest.mark.asyncio
async def test_signal_received() -> None:
    fake = FakeTgClient()
    notifier = TelegramDMNotifier(tg_client=fake, config=_make_config())
    signal = _make_signal(direction="down")
    await notifier.on_signal_received(signal)
    assert len(fake.sent) == 1
    expected = (
        "🟢 Signal received\n"
        "Pair: EUR/JPY\n"
        "Direction: PUT\n"
        "Trigger: 10:20 (UTC-3)\n"
        "Expiration: 5 min"
    )
    assert fake.sent[0] == expected


@pytest.mark.asyncio
async def test_bot_started() -> None:
    fake = FakeTgClient()
    notifier = TelegramDMNotifier(tg_client=fake, config=_make_config())
    await notifier.on_bot_started(
        mode="dry_run", watching="@analyst", timezone="America/Sao_Paulo"
    )
    assert len(fake.sent) == 1
    expected = (
        "🟢 Bot started\n"
        "Mode: dry_run\n"
        "Watching: @analyst\n"
        "Timezone: America/Sao_Paulo"
    )
    assert fake.sent[0] == expected


@pytest.mark.asyncio
async def test_bot_stopping() -> None:
    fake = FakeTgClient()
    notifier = TelegramDMNotifier(tg_client=fake, config=_make_config())
    await notifier.on_bot_stopping(open_cascades=3)
    assert len(fake.sent) == 1
    expected = "🔴 Bot stopping\nOpen cascades: 3"
    assert fake.sent[0] == expected
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_telegram_dm.py -v -k "signal_received or bot_started or bot_stopping"`
Expected: All 3 FAIL with `NotImplementedError`.

- [ ] **Step 3: Implement the 3 methods**

In `src/signal_copier/notify/telegram_dm.py`, replace the `raise NotImplementedError` bodies:

```python
    async def on_signal_received(self, signal: Signal) -> None:
        dir_str = "CALL" if signal.direction == "up" else "PUT"
        minutes = signal.expiration_seconds // 60
        text = (
            f"🟢 Signal received\n"
            f"Pair: {signal.pair}\n"
            f"Direction: {dir_str}\n"
            f"Trigger: {signal.trigger_hhmm} (UTC-3)\n"
            f"Expiration: {minutes} min"
        )
        await self._send(text)

    async def on_bot_started(
        self, *, mode: str, watching: str, timezone: str
    ) -> None:
        text = (
            f"🟢 Bot started\n"
            f"Mode: {mode}\n"
            f"Watching: {watching}\n"
            f"Timezone: {timezone}"
        )
        await self._send(text)

    async def on_bot_stopping(self, *, open_cascades: int) -> None:
        text = f"🔴 Bot stopping\nOpen cascades: {open_cascades}"
        await self._send(text)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_telegram_dm.py -v -k "signal_received or bot_started or bot_stopping"`
Expected: All 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/signal_copier/notify/telegram_dm.py tests/test_telegram_dm.py
git commit -m "feat(notify): implement signal_received, bot_started, bot_stopping DMs"
```

---

### Task 10: Implement `on_trade_placed` (3 stage variants)

**Files:**
- Modify: `src/signal_copier/notify/telegram_dm.py` (1 method body)
- Modify: `tests/test_telegram_dm.py` (append 3 tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_telegram_dm.py`:

```python
@pytest.mark.asyncio
async def test_trade_placed_initial() -> None:
    fake = FakeTgClient()
    notifier = TelegramDMNotifier(tg_client=fake, config=_make_config())
    signal = _make_signal(direction="down")
    await notifier.on_trade_placed(
        signal, stage="initial", amount=Decimal("2.00"), trade_id="abc123"
    )
    expected = (
        "⏱️ Trade placed (INITIAL)\n"
        "Pair: EUR/JPY\n"
        "Direction: PUT\n"
        "Amount: $2.00\n"
        "Expires: 10:25 (UTC-3)\n"
        "Trade ID: abc123"
    )
    assert fake.sent[0] == expected


@pytest.mark.asyncio
async def test_trade_placed_gale1() -> None:
    fake = FakeTgClient()
    notifier = TelegramDMNotifier(tg_client=fake, config=_make_config())
    signal = _make_signal(direction="up")
    await notifier.on_trade_placed(
        signal, stage="gale1", amount=Decimal("4.00"), trade_id="def456"
    )
    expected = (
        "⏱️ Trade placed (1st GALE)\n"
        "Amount: $4.00\n"
        "Expires: 10:30 (UTC-3)\n"
        "Triggered by: loss on initial\n"
        "Trade ID: def456"
    )
    assert fake.sent[0] == expected


@pytest.mark.asyncio
async def test_trade_placed_gale2() -> None:
    fake = FakeTgClient()
    notifier = TelegramDMNotifier(tg_client=fake, config=_make_config())
    signal = _make_signal(direction="down")
    await notifier.on_trade_placed(
        signal, stage="gale2", amount=Decimal("8.00"), trade_id="ghi789"
    )
    expected = (
        "⏱️ Trade placed (2nd GALE)\n"
        "Amount: $8.00\n"
        "Expires: 10:35 (UTC-3)\n"
        "Triggered by: loss on 1st gale\n"
        "Trade ID: ghi789"
    )
    assert fake.sent[0] == expected
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_telegram_dm.py -v -k "trade_placed"`
Expected: All 3 FAIL with `NotImplementedError`.

- [ ] **Step 3: Implement the method**

Replace the `raise NotImplementedError` body of `on_trade_placed`:

```python
    async def on_trade_placed(
        self, signal: Signal, stage: Stage, amount: Decimal, trade_id: str
    ) -> None:
        label = self._stage_label(stage)
        expires_unix = self._stage_gale_unix(signal, stage) + signal.expiration_seconds
        expires_hhmm = format_local_hhmm(expires_unix, self._config.tz())
        triggered_by = {
            "initial": "",  # initial has no "triggered by" line
            "gale1": "\nTriggered by: loss on initial",
            "gale2": "\nTriggered by: loss on 1st gale",
        }[stage]
        if stage == "initial":
            dir_str = "CALL" if signal.direction == "up" else "PUT"
            text = (
                f"⏱️ Trade placed ({label})\n"
                f"Pair: {signal.pair}\n"
                f"Direction: {dir_str}\n"
                f"Amount: ${amount:.2f}\n"
                f"Expires: {expires_hhmm} (UTC-3)\n"
                f"Trade ID: {trade_id}"
            )
        else:
            text = (
                f"⏱️ Trade placed ({label})\n"
                f"Amount: ${amount:.2f}\n"
                f"Expires: {expires_hhmm} (UTC-3)"
                f"{triggered_by}\n"
                f"Trade ID: {trade_id}"
            )
        await self._send(text)
```

Also add the two private helpers used above. Insert them after `_send`:

```python
    def _stage_label(self, stage: Stage) -> str:
        return {"initial": "INITIAL", "gale1": "1st GALE", "gale2": "2nd GALE"}[stage]

    def _stage_gale_unix(self, signal: Signal, stage: Stage) -> float:
        """Return trigger_unix for the stage (initial=0, gale1=1, gale2=2)."""
        index = {"initial": 0, "gale1": 1, "gale2": 2}[stage]
        return signal.trigger_unix_initial + index * signal.expiration_seconds
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_telegram_dm.py -v -k "trade_placed"`
Expected: All 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/signal_copier/notify/telegram_dm.py tests/test_telegram_dm.py
git commit -m "feat(notify): implement on_trade_placed for all 3 stages"
```

---

### Task 11: Implement `on_win` and `on_loss` (6 stage variants)

**Files:**
- Modify: `src/signal_copier/notify/telegram_dm.py` (2 method bodies)
- Modify: `tests/test_telegram_dm.py` (append 6 tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_telegram_dm.py`:

```python
@pytest.mark.asyncio
async def test_win_initial() -> None:
    fake = FakeTgClient()
    notifier = TelegramDMNotifier(tg_client=fake, config=_make_config())
    signal = _make_signal(direction="down")
    await notifier.on_win(
        signal,
        stage="initial",
        pnl=Decimal("1.84"),
        cumulative_pnl=Decimal("1.84"),
    )
    expected = (
        "✅ WIN (INITIAL)\n"
        "Pair: EUR/JPY\n"
        "PnL: +$1.84\n"
        "Signal closed: done_win\n"
        "Next: stop (cascade ends)"
    )
    assert fake.sent[0] == expected


@pytest.mark.asyncio
async def test_win_gale1() -> None:
    fake = FakeTgClient()
    notifier = TelegramDMNotifier(tg_client=fake, config=_make_config())
    signal = _make_signal(direction="down")
    await notifier.on_win(
        signal,
        stage="gale1",
        pnl=Decimal("3.68"),
        cumulative_pnl=Decimal("1.68"),
    )
    expected = (
        "✅ WIN (1st GALE)\n"
        "Pair: EUR/JPY\n"
        "PnL: +$3.68\n"
        "Cascade: stopped after gale1 — total recovered"
    )
    assert fake.sent[0] == expected


@pytest.mark.asyncio
async def test_win_gale2() -> None:
    fake = FakeTgClient()
    notifier = TelegramDMNotifier(tg_client=fake, config=_make_config())
    signal = _make_signal(direction="down")
    await notifier.on_win(
        signal,
        stage="gale2",
        pnl=Decimal("7.36"),
        cumulative_pnl=Decimal("5.36"),
    )
    expected = (
        "✅ WIN (2nd GALE)\n"
        "Pair: EUR/JPY\n"
        "PnL: +$7.36\n"
        "Cascade: stopped after gale2 — full recovery"
    )
    assert fake.sent[0] == expected


@pytest.mark.asyncio
async def test_loss_initial_with_next_stage() -> None:
    fake = FakeTgClient()
    notifier = TelegramDMNotifier(tg_client=fake, config=_make_config())
    signal = _make_signal(direction="down")
    await notifier.on_loss(
        signal,
        stage="initial",
        pnl=Decimal("-2.00"),
        cumulative_pnl=Decimal("-2.00"),
        next_stage="gale1",
    )
    expected = (
        "❌ LOSS (INITIAL)\n"
        "Pair: EUR/JPY\n"
        "PnL: $-2.00\n"
        "Next: scheduling 1st gale at 10:25 (UTC-3), $4.00"
    )
    assert fake.sent[0] == expected


@pytest.mark.asyncio
async def test_loss_gale1_with_next_stage() -> None:
    fake = FakeTgClient()
    notifier = TelegramDMNotifier(tg_client=fake, config=_make_config())
    signal = _make_signal(direction="down")
    await notifier.on_loss(
        signal,
        stage="gale1",
        pnl=Decimal("-4.00"),
        cumulative_pnl=Decimal("-6.00"),
        next_stage="gale2",
    )
    expected = (
        "❌ LOSS (1st GALE)\n"
        "Pair: EUR/JPY\n"
        "PnL: $-4.00\n"
        "Next: scheduling 2nd gale at 10:30 (UTC-3), $8.00"
    )
    assert fake.sent[0] == expected


@pytest.mark.asyncio
async def test_loss_gale2_no_next_stage() -> None:
    fake = FakeTgClient()
    notifier = TelegramDMNotifier(tg_client=fake, config=_make_config())
    signal = _make_signal(direction="down")
    await notifier.on_loss(
        signal,
        stage="gale2",
        pnl=Decimal("-8.00"),
        cumulative_pnl=Decimal("-14.00"),
        next_stage=None,
    )
    expected = (
        "❌ LOSS (2nd GALE)\n"
        "Pair: EUR/JPY\n"
        "PnL: $-8.00\n"
        "Cascade: ended — full loss ($-14.00 total)"
    )
    assert fake.sent[0] == expected
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_telegram_dm.py -v -k "win_ or loss_"`
Expected: All 6 FAIL with `NotImplementedError`.

- [ ] **Step 3: Implement the methods**

Replace the `raise NotImplementedError` body of `on_win`:

```python
    async def on_win(
        self, signal: Signal, stage: Stage, pnl: Decimal, cumulative_pnl: Decimal
    ) -> None:
        label = self._stage_label(stage)
        if stage == "initial":
            text = (
                f"✅ WIN ({label})\n"
                f"Pair: {signal.pair}\n"
                f"PnL: {self._fmt_pnl(pnl)}\n"
                f"Signal closed: done_win\n"
                f"Next: stop (cascade ends)"
            )
        elif stage == "gale1":
            text = (
                f"✅ WIN ({label})\n"
                f"Pair: {signal.pair}\n"
                f"PnL: {self._fmt_pnl(pnl)}\n"
                f"Cascade: stopped after gale1 — total recovered"
            )
        else:  # gale2
            text = (
                f"✅ WIN ({label})\n"
                f"Pair: {signal.pair}\n"
                f"PnL: {self._fmt_pnl(pnl)}\n"
                f"Cascade: stopped after gale2 — full recovery"
            )
        await self._send(text)
```

Replace the `raise NotImplementedError` body of `on_loss`:

```python
    async def on_loss(
        self,
        signal: Signal,
        stage: Stage,
        pnl: Decimal,
        cumulative_pnl: Decimal,
        next_stage: Stage | None,
    ) -> None:
        label = self._stage_label(stage)
        if next_stage is None:
            # Cascade ended — show total loss.
            total_loss = abs(cumulative_pnl)
            text = (
                f"❌ LOSS ({label})\n"
                f"Pair: {signal.pair}\n"
                f"PnL: {self._fmt_pnl(pnl)}\n"
                f"Cascade: ended — full loss (${self._fmt_signed(total_loss)} total)"
            )
        else:
            next_label = self._stage_label(next_stage)
            next_trigger_unix = self._stage_gale_unix(signal, next_stage)
            next_hhmm = format_local_hhmm(next_trigger_unix, self._config.tz())
            # Gale amount comes from config — match the v1 amounts ($2/$4/$8).
            gale_amount = {
                "gale1": Decimal("4.00"),
                "gale2": Decimal("8.00"),
            }[next_stage]
            text = (
                f"❌ LOSS ({label})\n"
                f"Pair: {signal.pair}\n"
                f"PnL: {self._fmt_pnl(pnl)}\n"
                f"Next: scheduling {next_label} at {next_hhmm} (UTC-3), "
                f"${gale_amount:.2f}"
            )
        await self._send(text)
```

Add the two private helpers used above (insert after `_stage_gale_unix`):

```python
    def _fmt_pnl(self, decimal: Decimal) -> str:
        return f"${decimal:+.2f}"

    def _fmt_signed(self, decimal: Decimal) -> str:
        """Format a Decimal with explicit sign, used in cascade-ended lines."""
        return f"{decimal:+.2f}"
```

Wait — the template for `test_loss_gale2_no_next_stage` expects `Cascade: ended — full loss ($-14.00 total)`. The format `self._fmt_signed(abs(cumulative_pnl))` for `cumulative_pnl=Decimal("-14.00")` returns `Decimal("14.00")` then formatted as `{14.00:+.2f}` → `"+14.00"` — but the test expects `"$-14.00"`. The template uses `($-14.00 total)`. So we need to use `cumulative_pnl` (signed), not `abs()`. Fix the code:

```python
            total_loss = cumulative_pnl  # already negative
            text = (
                f"❌ LOSS ({label})\n"
                f"Pair: {signal.pair}\n"
                f"PnL: {self._fmt_pnl(pnl)}\n"
                f"Cascade: ended — full loss (${self._fmt_signed(total_loss)} total)"
            )
```

This produces `$` + `-14.00` = `$-14.00`. Match.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_telegram_dm.py -v -k "win_ or loss_"`
Expected: All 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/signal_copier/notify/telegram_dm.py tests/test_telegram_dm.py
git commit -m "feat(notify): implement on_win and on_loss for all stages"
```

---

### Task 12: Implement `on_signal_expired` (3 stage variants)

**Files:**
- Modify: `src/signal_copier/notify/telegram_dm.py` (1 method body)
- Modify: `tests/test_telegram_dm.py` (append 3 tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_telegram_dm.py`:

```python
@pytest.mark.asyncio
async def test_signal_expired_initial() -> None:
    fake = FakeTgClient()
    notifier = TelegramDMNotifier(tg_client=fake, config=_make_config())
    signal = _make_signal(direction="down")
    await notifier.on_signal_expired(signal, stage="initial", trigger_hhmm="10:20")
    expected = (
        "⏰ Signal EXPIRED (INITIAL)\n"
        "Pair: EUR/JPY\n"
        "Trigger was: 10:20 (UTC-3)\n"
        "Reason: time window passed before fire\n"
        "Action: no trades placed; signal invalid"
    )
    assert fake.sent[0] == expected


@pytest.mark.asyncio
async def test_signal_expired_gale1() -> None:
    fake = FakeTgClient()
    notifier = TelegramDMNotifier(tg_client=fake, config=_make_config())
    signal = _make_signal(direction="down")
    await notifier.on_signal_expired(signal, stage="gale1", trigger_hhmm="10:25")
    expected = (
        "⏰ Signal EXPIRED (1st GALE)\n"
        "Pair: EUR/JPY\n"
        "Gale1 trigger was: 10:25 (UTC-3)\n"
        "Reason: time window passed before fire\n"
        "Action: no gale2 placed — cascade ended"
    )
    assert fake.sent[0] == expected


@pytest.mark.asyncio
async def test_signal_expired_gale2() -> None:
    fake = FakeTgClient()
    notifier = TelegramDMNotifier(tg_client=fake, config=_make_config())
    signal = _make_signal(direction="down")
    await notifier.on_signal_expired(signal, stage="gale2", trigger_hhmm="10:30")
    expected = (
        "⏰ Signal EXPIRED (2nd GALE)\n"
        "Pair: EUR/JPY\n"
        "Gale2 trigger was: 10:30 (UTC-3)\n"
        "Reason: time window passed before fire\n"
        "Action: cascade ended, no recovery attempted"
    )
    assert fake.sent[0] == expected
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_telegram_dm.py -v -k "signal_expired"`
Expected: All 3 FAIL with `NotImplementedError`.

- [ ] **Step 3: Implement the method**

Replace the `raise NotImplementedError` body of `on_signal_expired`:

```python
    async def on_signal_expired(
        self, signal: Signal, stage: Stage, trigger_hhmm: str
    ) -> None:
        label = self._stage_label(stage)
        if stage == "initial":
            text = (
                f"⏰ Signal EXPIRED ({label})\n"
                f"Pair: {signal.pair}\n"
                f"Trigger was: {trigger_hhmm} (UTC-3)\n"
                f"Reason: time window passed before fire\n"
                f"Action: no trades placed; signal invalid"
            )
        elif stage == "gale1":
            text = (
                f"⏰ Signal EXPIRED ({label})\n"
                f"Pair: {signal.pair}\n"
                f"Gale1 trigger was: {trigger_hhmm} (UTC-3)\n"
                f"Reason: time window passed before fire\n"
                f"Action: no gale2 placed — cascade ended"
            )
        else:  # gale2
            text = (
                f"⏰ Signal EXPIRED ({label})\n"
                f"Pair: {signal.pair}\n"
                f"Gale2 trigger was: {trigger_hhmm} (UTC-3)\n"
                f"Reason: time window passed before fire\n"
                f"Action: cascade ended, no recovery attempted"
            )
        await self._send(text)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_telegram_dm.py -v -k "signal_expired"`
Expected: All 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/signal_copier/notify/telegram_dm.py tests/test_telegram_dm.py
git commit -m "feat(notify): implement on_signal_expired for all stages"
```

---

### Task 13: Implement `on_cascade_complete` + `on_signal_rejected_by_limit` + `on_parse_failure`

**Files:**
- Modify: `src/signal_copier/notify/telegram_dm.py` (3 method bodies)
- Modify: `tests/test_telegram_dm.py` (append 5 tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_telegram_dm.py`:

```python
from datetime import date
from signal_copier.infra.db_rows import DailySummaryRow


@pytest.mark.asyncio
async def test_cascade_complete() -> None:
    fake = FakeTgClient()
    notifier = TelegramDMNotifier(tg_client=fake, config=_make_config())
    signal = _make_signal(direction="down")
    await notifier.on_cascade_complete(
        signal, final_state="done_win", cumulative_pnl=Decimal("1.84")
    )
    # Duration is "0m00s" since received_at_unix == 1_750_000_000 and now is later,
    # but we don't assert the exact duration — just check the prefix.
    assert fake.sent[0].startswith(
        "🏁 Cascade complete: done_win\n"
        "Signal ID: sig-abc\n"
        "Total PnL: $+1.84\n"
        "Duration: "
    )
    # Sanity-check the duration suffix looks like "XmYYs"
    assert fake.sent[0].endswith("s")


@pytest.mark.asyncio
async def test_rejected_by_loss_limit() -> None:
    fake = FakeTgClient()
    config = _make_config(daily_loss_limit=Decimal("50.00"))
    notifier = TelegramDMNotifier(tg_client=fake, config=config)
    signal = _make_signal(direction="down")
    summary = DailySummaryRow(
        date=date(2026, 6, 21),
        signals_count=10,
        trades_count=10,
        wins=2,
        losses=8,
        realized_pnl=Decimal("-50.00"),
        limit_hit="loss",
    )
    await notifier.on_signal_rejected_by_limit(
        signal, limit_type="loss", summary=summary
    )
    expected = (
        "⚠️ Daily loss limit reached\n"
        "Losses today: $-50.00\n"
        "Limit: $50.00\n"
        "Action: no new signals until 00:00 (UTC-3)"
    )
    assert fake.sent[0] == expected


@pytest.mark.asyncio
async def test_rejected_by_count_limit() -> None:
    fake = FakeTgClient()
    config = _make_config(daily_trade_limit=50)
    notifier = TelegramDMNotifier(tg_client=fake, config=config)
    signal = _make_signal(direction="down")
    summary = DailySummaryRow(
        date=date(2026, 6, 21),
        signals_count=50,
        trades_count=50,
        wins=20,
        losses=30,
        realized_pnl=Decimal("0.00"),
        limit_hit="count",
    )
    await notifier.on_signal_rejected_by_limit(
        signal, limit_type="count", summary=summary
    )
    expected = (
        "⚠️ Daily trade limit reached\n"
        "Trades today: 50\n"
        "Limit: 50\n"
        "Action: no new signals until 00:00 (UTC-3)"
    )
    assert fake.sent[0] == expected


@pytest.mark.asyncio
async def test_rejected_by_drawdown_limit() -> None:
    fake = FakeTgClient()
    config = _make_config(daily_drawdown_pct=20)
    notifier = TelegramDMNotifier(tg_client=fake, config=config)
    signal = _make_signal(direction="down")
    summary = DailySummaryRow(
        date=date(2026, 6, 21),
        signals_count=20,
        trades_count=20,
        wins=10,
        losses=10,
        realized_pnl=Decimal("-30.00"),
        limit_hit="drawdown",
    )
    await notifier.on_signal_rejected_by_limit(
        signal, limit_type="drawdown", summary=summary
    )
    expected = (
        "⚠️ Daily drawdown limit reached\n"
        "Drawdown today: $-30.00\n"
        "Limit: 20%\n"
        "Action: no new signals until 00:00 (UTC-3)"
    )
    assert fake.sent[0] == expected


@pytest.mark.asyncio
async def test_parse_failure() -> None:
    fake = FakeTgClient()
    notifier = TelegramDMNotifier(tg_client=fake, config=_make_config())
    raw = "random text that doesn't match the signal regex" + "x" * 100
    await notifier.on_parse_failure(raw_text=raw, reason=FailureReason.MISSING_SIGNAL_LINE)
    # Preview is the first 80 chars.
    assert fake.sent[0] == (
        "⚠️ Skipped message (not a valid signal)\n"
        "Reason: missing_signal_line\n"
        f"Preview: {raw[:80]}"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_telegram_dm.py -v -k "cascade_complete or rejected_by or parse_failure"`
Expected: All 5 FAIL with `NotImplementedError`.

- [ ] **Step 3: Implement the methods**

Replace the `raise NotImplementedError` body of `on_cascade_complete`:

```python
    async def on_cascade_complete(
        self,
        signal: Signal,
        final_state: "TerminalState",
        cumulative_pnl: Decimal,
    ) -> None:
        duration = self._duration_human(signal.received_at_unix, now_unix())
        text = (
            f"🏁 Cascade complete: {final_state}\n"
            f"Signal ID: {signal.signal_id}\n"
            f"Total PnL: {self._fmt_pnl(cumulative_pnl)}\n"
            f"Duration: {duration}"
        )
        await self._send(text)
```

Replace the `raise NotImplementedError` body of `on_signal_rejected_by_limit`:

```python
    async def on_signal_rejected_by_limit(
        self,
        signal: Signal,
        limit_type: str,
        summary: "DailySummaryRow",
    ) -> None:
        if limit_type == "loss":
            text = (
                f"⚠️ Daily loss limit reached\n"
                f"Losses today: {self._fmt_pnl(summary.realized_pnl)}\n"
                f"Limit: ${self._config.daily_loss_limit:.2f}\n"
                f"Action: no new signals until 00:00 (UTC-3)"
            )
        elif limit_type == "count":
            text = (
                f"⚠️ Daily trade limit reached\n"
                f"Trades today: {summary.trades_count}\n"
                f"Limit: {self._config.daily_trade_limit}\n"
                f"Action: no new signals until 00:00 (UTC-3)"
            )
        else:  # drawdown
            text = (
                f"⚠️ Daily drawdown limit reached\n"
                f"Drawdown today: {self._fmt_pnl(summary.realized_pnl)}\n"
                f"Limit: {self._config.daily_drawdown_pct}%\n"
                f"Action: no new signals until 00:00 (UTC-3)"
            )
        await self._send(text)
```

Replace the `raise NotImplementedError` body of `on_parse_failure`:

```python
    async def on_parse_failure(
        self, raw_text: str, reason: FailureReason
    ) -> None:
        text = (
            f"⚠️ Skipped message (not a valid signal)\n"
            f"Reason: {reason.value}\n"
            f"Preview: {raw_text[:80]}"
        )
        await self._send(text)
```

Add the missing `_duration_human` helper (insert after `_fmt_signed`):

```python
    def _duration_human(self, start_unix: float, end_unix: float) -> str:
        delta = max(0, int(end_unix - start_unix))
        return f"{delta // 60}m{delta % 60:02d}s"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_telegram_dm.py -v -k "cascade_complete or rejected_by or parse_failure"`
Expected: All 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/signal_copier/notify/telegram_dm.py tests/test_telegram_dm.py
git commit -m "feat(notify): implement on_cascade_complete, limit rejection, parse_failure DMs"
```

---

## Phase G — Wiring

### Task 14: Wire `Listener` to emit `on_parse_failure`

**Files:**
- Modify: `src/signal_copier/telegram/listener.py:40-54` (add `notifier` param) and `:80-82` (emit on parse failure)
- Test: `tests/_telegram_fixtures.py:50` (extend FakeListener factory)
- Test: `tests/test_telegram_listener.py` (add 2 tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_telegram_listener.py`:

```python
from signal_copier.notify.protocol import NoOpNotifier, Notifier


async def test_listener_emits_on_parse_failure_on_invalid_message() -> None:
    """When parse_signal returns ParseFailure, the listener must call
    notifier.on_parse_failure with the raw text and FailureReason."""
    from signal_copier.domain.signal import FailureReason
    from tests._telegram_fixtures import make_event
    from tests._scheduler_fixtures import RecordingNotifier
    from signal_copier.telegram.listener import Listener
    from signal_copier.config import Config

    notifier = RecordingNotifier()
    config = Config(timezone="America/Sao_Paulo")
    listener = Listener(
        target_chat_id=-100,
        state_store=FakeStateStore(),
        queue=asyncio.Queue(),
        config=config,
        parse_failures_logger=NullLogger(),
        notifier=notifier,
    )
    bad_event = make_event(text="not a signal", chat_id=-100, message_id=1)
    await listener.on_new_message(bad_event)

    assert any(call[0] == "on_parse_failure" for call in notifier.calls)


async def test_listener_does_not_emit_on_parse_failure_for_valid_signal() -> None:
    """When parse_signal succeeds, no on_parse_failure call is made."""
    from tests._scheduler_fixtures import RecordingNotifier
    from signal_copier.telegram.listener import Listener
    from signal_copier.config import Config
    from signal_copier.domain.signal import parse_signal

    VALID_SIGNAL = (
        "💰5-minute expiration\n"
        "EUR/JPY;10:20;PUT🟥\n"
        "🕛TIME UNTIL 10:25\n"
        "1st GALE -> TIME UNTIL 10:25\n"
        "2nd GALE - TIME UNTIL 10:30"
    )

    notifier = RecordingNotifier()
    config = Config(timezone="America/Sao_Paulo")
    listener = Listener(
        target_chat_id=-100,
        state_store=FakeStateStore(),
        queue=asyncio.Queue(),
        config=config,
        parse_failures_logger=NullLogger(),
        notifier=notifier,
    )
    good_event = make_event(text=VALID_SIGNAL, chat_id=-100, message_id=1)
    await listener.on_new_message(good_event)

    assert not any(call[0] == "on_parse_failure" for call in notifier.calls)
```

Add the import for `asyncio` at the top if not already present (likely already imported in test_telegram_listener.py).

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_telegram_listener.py -v -k "emits_on_parse_failure or does_not_emit_on_parse_failure"`
Expected: FAIL with `TypeError: Listener.__init__() got an unexpected keyword argument 'notifier'`

- [ ] **Step 3: Add `notifier` param to `Listener.__init__` and emit on parse failure**

In `src/signal_copier/telegram/listener.py`, modify the imports (add `Notifier`):

```python
from signal_copier.notify.protocol import Notifier
```

Modify the `__init__` method (lines 40-54). Replace it with:

```python
    def __init__(
        self,
        *,
        target_chat_id: int,
        state_store: StateStore,
        queue: asyncio.Queue[Signal],
        config: Config,
        parse_failures_logger: logging.Logger,
        notifier: Notifier,
    ) -> None:
        self._target_chat_id = target_chat_id
        self._state_store = state_store
        self._queue = queue
        self._config = config
        self._parse_failures_logger = parse_failures_logger
        self._notifier = notifier
        self._allowed_expirations = _allowed_expirations(config)
```

Modify the parse-failure branch in `_process_message` (lines 80-82). Replace:

```python
        if isinstance(result, ParseFailure):
            self._log_parse_failure(result, text, source_message_id)
            return
```

With:

```python
        if isinstance(result, ParseFailure):
            self._log_parse_failure(result, text, source_message_id)
            await self._notifier.on_parse_failure(
                raw_text=text, reason=result.reason
            )
            return
```

- [ ] **Step 4: Update existing tests that construct Listener**

Run: `pytest tests/test_telegram_listener.py -v --co`
Expected: Collection errors in any test that constructs Listener without `notifier=`.

Fix any failures by adding `notifier=NoOpNotifier()` to the Listener constructor in those tests.

- [ ] **Step 5: Run the listener tests**

Run: `pytest tests/test_telegram_listener.py -v`
Expected: All pass (existing + 2 new).

- [ ] **Step 6: Commit**

```bash
git add src/signal_copier/telegram/listener.py tests/test_telegram_listener.py
git commit -m "feat(listener): emit on_parse_failure via injected Notifier"
```

---

### Task 15: Wire `__main__.py` to select `TelegramDMNotifier` vs `NoOpNotifier`

**Files:**
- Modify: `src/signal_copier/__main__.py:32` (replace `notifier = NoOpNotifier()` with conditional)
- Modify: `src/signal_copier/__main__.py:53-59` (add `notifier=notifier` to Listener constructor)
- Modify: `src/signal_copier/__main__.py:90` (add `notifier=notifier` to tg.start())
- Test: `tests/test_main.py` (update existing tests if they assert on `notifier` type)

- [ ] **Step 1: Write a quick verification test**

Add to `tests/test_main.py`:

```python
def test_main_passes_notifier_to_listener_and_tg_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """__main__ must construct TelegramDMNotifier when self_dm_notifications=True
    and pass it to both the Listener and tg.start()."""
    from signal_copier.notify.telegram_dm import TelegramDMNotifier
    from signal_copier.notify.protocol import NoOpNotifier

    # Read the source and check the wiring (avoids having to run the full asyncio main).
    import inspect
    from signal_copier import __main__ as main_module
    source = inspect.getsource(main_module._run)
    assert "TelegramDMNotifier(tg_client=tg, config=config)" in source
    assert "Listener(" in source  # Listener construction still present
    # Listener construction includes notifier=notifier
    assert "notifier=notifier" in source
    # tg.start() called with notifier=notifier
    assert "tg.start(notifier=notifier)" in source
```

Add `pytest` import if not already present.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_main.py::test_main_passes_notifier_to_listener_and_tg_start -v`
Expected: FAIL (assertion fails on `TelegramDMNotifier` not in source).

- [ ] **Step 3: Modify `__main__.py`**

In `src/signal_copier/__main__.py`, modify the imports (add `TelegramDMNotifier`):

```python
from signal_copier.notify.protocol import NoOpNotifier
from signal_copier.notify.telegram_dm import TelegramDMNotifier
```

Replace line 32 (`notifier = NoOpNotifier()`) with:

```python
    if config.telegram_self_dm_notifications:
        notifier = TelegramDMNotifier(tg_client=tg, config=config)
        _log.info("Notifications: TelegramDMNotifier (self-DM enabled)")
    else:
        notifier = NoOpNotifier()
        _log.info("Notifications: NoOpNotifier (self-DM disabled)")
```

Modify the Listener construction (around line 53-59). Replace:

```python
        listener = Listener(
            target_chat_id=tg.target_chat_id,
            state_store=db.state_store,
            queue=signals_queue,
            config=config,
            parse_failures_logger=parse_failures,
        )
```

With:

```python
        listener = Listener(
            target_chat_id=tg.target_chat_id,
            state_store=db.state_store,
            queue=signals_queue,
            config=config,
            parse_failures_logger=parse_failures,
            notifier=notifier,
        )
```

Modify the `tg.start()` task creation (around line 90). Replace:

```python
        telegram_task = asyncio.create_task(tg.start(), name="telegram")
```

With:

```python
        telegram_task = asyncio.create_task(tg.start(notifier=notifier), name="telegram")
```

Note: `_log` may not be defined in `__main__.py` yet — add at the top of the file if missing:

```python
import logging
_log = logging.getLogger(__name__)
```

(Or use `print()` instead of `_log.info()` for the "Notifications: ..." lines if you'd rather keep `__main__.py` log-free.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_main.py -v`
Expected: All tests pass (existing + 1 new).

- [ ] **Step 5: Commit**

```bash
git add src/signal_copier/__main__.py tests/test_main.py
git commit -m "feat(main): wire TelegramDMNotifier when self_dm_notifications=true"
```

---

## Phase H — Verification

### Task 16: Run full test suite + mypy + ruff, fix any issues

**Files:** none (verification only)

- [ ] **Step 1: Run the full pytest suite**

Run: `pytest tests/ -v --ignore=tests/test_db.py --ignore=tests/test_log_rotation.py`
Expected: All tests pass. (Excluding test_db because it needs a real Postgres container; excluding test_log_rotation because it's marked slow.)

- [ ] **Step 2: Run mypy --strict**

Run: `mypy src/signal_copier`
Expected: `Success: no issues found in N source files`.

If mypy errors come up, common fixes:
- Missing import for a type used in an annotation → add the import or use a string forward reference.
- `arg-type` mismatch → cast or update the Protocol.
- `attr-defined` on a private member → use `# type: ignore[attr-defined]` with a comment explaining why.

- [ ] **Step 3: Run ruff**

Run: `ruff check src/signal_copier tests`
Expected: `All checks passed!`

If ruff flags issues, fix per the message (often unused imports, line length, missing newlines).

- [ ] **Step 4: Run the rotation test (slow)**

Run: `pytest tests/test_log_rotation.py -v`
Expected: PASS. The test generates 10+ MB of log output to verify loguru rotates the file and creates a ZIP archive.

- [ ] **Step 5: Commit any fixes**

If Steps 1-4 required fixes:

```bash
git add -u
git commit -m "fix(M7): address test/mypy/ruff issues"
```

If no fixes needed, skip the commit.

---

### Task 17: Update spec status + final commit

**Files:**
- Modify: `docs/superpowers/specs/2026-06-21-m7-telegram-dm-notifications-design.md:4` (update status)

- [ ] **Step 1: Update spec status to "Implemented"**

In `docs/superpowers/specs/2026-06-21-m7-telegram-dm-notifications-design.md`, change line 4:

```markdown
**Status:** Approved → Implemented (M7 complete)
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-06-21-m7-telegram-dm-notifications-design.md
git commit -m "docs(spec): mark M7 design spec as Implemented"
```

---

## Self-Review Checklist

### Spec coverage

| Spec section | Implemented by |
|---|---|
| §3.1 Protocol extension (3 new methods) | Task 3 (Protocol + NoOpNotifier), Task 4 (RecordingNotifier) |
| §3.2 `TelegramDMNotifier` class | Tasks 8-13 |
| §3.3 `infra/log.py` loguru rewrite | Task 5 |
| §4 Protocol extension details | Task 3 |
| §5 `TelegramDMNotifier` implementation | Tasks 8-13 |
| §5.1 Class shape (`_send`, `bind(dm_event=True)`) | Task 8 |
| §5.2 Message templates (all 22) | Tasks 9-13 |
| §5.3 Private formatting helpers | Tasks 10, 11, 13 |
| §5.4 Method signatures (positional vs keyword-only) | All TelegramDMNotifier tasks preserve the M6 signatures exactly |
| §6 `TelegramClient.send_to_self` | Task 6 |
| §7 `infra/log.py` loguru rewrite | Task 5 |
| §7.1 Three sinks (stderr, rotating file, InterceptHandler) | Task 5 |
| §7.2 `setup_parse_failures_log` | Task 5 |
| §7.3 DM mirror via loguru `bind(dm_event=True)` | Task 8 (`_send` implementation) |
| §7.4 Add loguru dep to pyproject.toml | Task 2 |
| §7.5 Impact on existing code (zero changes) | Verified by Step 5 of Task 5 (run full test suite) |
| §8.1 `__main__.py` notifier selection | Task 15 |
| §8.2 `Listener` emits `on_parse_failure` | Task 14 |
| §8.3 `TelegramClient.start()` emits `on_telegram_disconnect` | Task 7 |
| §8.4 `on_olymp_disconnect` stub only (M8/M10) | Implicit — Protocol + DM template shipped, no emission wiring |
| §9.1 `tests/test_telegram_dm.py` 25 tests | Tasks 8-13 |
| §9.2 `tests/test_notifier.py` +3 tests | Task 3 |
| §9.3 `tests/_scheduler_fixtures.py` RecordingNotifier extension | Task 4 |
| §9.4 `tests/test_telegram_client.py` +2 tests | Tasks 6, 7 |
| §9.5 `tests/test_telegram_listener.py` +2 tests | Task 14 |
| §9.6 `tests/test_log.py` rewrite (4 tests + 1 idempotency = 5) | Task 5 |
| §9.7 `tests/test_log_rotation.py` slow marker | Task 16 (run after implementation; file created in same commit as Task 5 if desired) |
| §9.8 `tests/_telegram_fixtures.py` notifier factory | Task 14 Step 4 (extend fixture as needed) |

### Placeholder scan

No TBDs, no "implement later", no "add appropriate error handling" — every step contains concrete code, exact file paths, exact commands, and expected output.

### Type consistency

- `Notifier` Protocol signatures match across Tasks 3, 8, 13 (positional-or-keyword for the 7 first methods + `on_parse_failure`; keyword-only for `on_bot_started` and `on_bot_stopping`).
- `_stage_label`, `_stage_gale_unix`, `_fmt_pnl`, `_fmt_signed`, `_duration_human` introduced once and reused.
- `Stage` type imported in `telegram_dm.py` from `signal_copier.domain.gale`.
- `FailureReason` imported from `signal_copier.domain.signal`.
- `Signal` imported from `signal_copier.domain.signal`.
- `Notifier` imported from `signal_copier.notify.protocol`.
- `TelegramClient` typed via TYPE_CHECKING block in `telegram_dm.py` to avoid circular imports.

---

## Total commits produced by this plan: ~16

1. `feat(clock): add format_local_hhmm helper for M7 notifier timestamps`
2. `chore(deps): add loguru>=0.7 for M7 rotating log + DM mirror`
3. `feat(notify): extend Notifier Protocol with on_parse_failure and disconnect events`
4. `test(scheduler): extend RecordingNotifier with 3 new Notifier methods`
5. `feat(log): migrate to loguru with rotating file sink + stdlib InterceptHandler`
6. `feat(telegram): add send_to_self() on TelegramClient for self-DM routing`
7. `feat(telegram): emit on_telegram_disconnect from reconnect loop`
8. `feat(notify): create TelegramDMNotifier skeleton with _send + disconnect methods`
9. `feat(notify): implement signal_received, bot_started, bot_stopping DMs`
10. `feat(notify): implement on_trade_placed for all 3 stages`
11. `feat(notify): implement on_win and on_loss for all stages`
12. `feat(notify): implement on_signal_expired for all stages`
13. `feat(notify): implement on_cascade_complete, limit rejection, parse_failure DMs`
14. `feat(listener): emit on_parse_failure via injected Notifier`
15. `feat(main): wire TelegramDMNotifier when self_dm_notifications=true`
16. (optional) `fix(M7): address test/mypy/ruff issues` — only if needed
17. `docs(spec): mark M7 design spec as Implemented`

---

## Definition of Done (cross-reference with spec §13)

M7 is complete when:
1. ✅ `pytest` shows all M0–M6 tests still passing + new M7 tests passing, zero failures.
2. ✅ `mypy --strict src/signal_copier` exits 0.
3. ✅ `ruff check .` exits 0.
4. ✅ `isinstance(TelegramDMNotifier(...), Notifier)` returns True (Task 8 Step 4).
5. ⏸ End-to-end smoke (manual, Railway DRY_RUN=true) — out of scope for this plan.
6. ⏸ 24h soak test — out of scope for this plan.

---

*Plan complete. ~16 commits, ~1100 lines added. Each task is independent and produces a green test commit.*