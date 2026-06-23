# M10 Self-Healing Reconnect Supervisor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wrap `OlympTradeBroker` with a self-healing reconnect supervisor that survives transient WS drops without process restart, terminates cleanly after 5 failed reconnect attempts, and notifies the user at each lifecycle transition.

**Architecture:** New wrapper class `ReconnectingOlympTradeBroker` (`src/signal_copier/broker/reconnect.py`) that holds an inner `OlympTradeBroker`, runs a 1s `is_connected` polling task, and triggers an exponential-backoff reconnect loop on disconnect detection. Both the polling watcher AND `place/wait_result`'s `ConnectionError` path funnel into the same `_trigger_reconnect()` coroutine, guarded by an `asyncio.Lock`. In-flight cascades already map `ConnectionError` to `error` at `scheduler/trigger.py:662` — no M2/M6 changes. The vendored `olymptrade_ws` package is untouched per PRD R-15.

**Tech Stack:** Python 3.13, asyncio stdlib, vendored `olymptrade_ws`, `pytest` + `pytest-asyncio` (asyncio_mode="auto").

**Spec:** `docs/superpowers/specs/2026-06-23-m10-olymptrade-reconnect-supervisor-design.md`

**Test environment:** All tests use the existing `FakeOlympTradeClient` from `tests/_broker_fixtures.py:74`, extended with a new `FakeClientFactory` that returns fakes one-at-a-time. To keep tests fast, `watcher_poll_seconds=0.05` and `reconnect_max_attempts=3` are passed in constructor where useful.

---

## File Structure

```
src/signal_copier/broker/
├── reconnect.py             # NEW — ReconnectingOlympTradeBroker (~250 LoC)
└── (others unchanged)

src/signal_copier/notify/
├── protocol.py              # MODIFIED — +3 Protocol methods, +3 NoOpNotifier methods
└── telegram_dm.py           # MODIFIED — implement 3 new methods; soften on_olymp_disconnect copy

src/signal_copier/
└── __main__.py              # MODIFIED — wrap OlympTradeBroker in ReconnectingOlympTradeBroker

tests/
├── _broker_fixtures.py      # MODIFIED — +FakeClientFactory
├── _scheduler_fixtures.py   # MODIFIED — +3 RecordingNotifier no-op methods
├── test_notifier.py         # MODIFIED — +3 NoOpNotifier log tests
├── test_recording_notifier_protocol.py  # MODIFIED — extend method list to 6 names
├── test_telegram_dm.py      # MODIFIED — +3 telegram_dm tests
└── test_reconnect_supervisor.py  # NEW — 9 supervisor tests
```

---

## Task 1: Extend `Notifier` Protocol + `NoOpNotifier` with 3 new methods

**Files:**
- Modify: `src/signal_copier/notify/protocol.py:121-125` (add 3 Protocol methods after `on_olymp_disconnect`)
- Modify: `src/signal_copier/notify/protocol.py:269-270` (add 3 NoOpNotifier implementations)
- Modify: `tests/_scheduler_fixtures.py:227-228` (add 3 RecordingNotifier no-op methods)
- Modify: `tests/test_recording_notifier_protocol.py:24-32` (extend expected tuple from 3 names to 6)
- Modify: `tests/test_notifier.py:154-194` (add 3 NoOpNotifier WARNING-log tests)

- [ ] **Step 1.1: Update RecordingNotifier protocol-satisfaction test**

In `tests/test_recording_notifier_protocol.py:27`, change:
```python
expected = ("on_parse_failure", "on_telegram_disconnect", "on_olymp_disconnect")
```
to:
```python
expected = (
    "on_parse_failure",
    "on_telegram_disconnect",
    "on_olymp_disconnect",
    "on_olymp_reconnecting",
    "on_olymp_reconnected",
    "on_olymp_reconnect_failed",
)
```

Run: `pytest tests/test_recording_notifier_protocol.py -v`
Expected: FAIL — `test_recording_notifier_satisfies_protocol_after_m7` will fail with `missing methods: ['on_olymp_reconnecting', 'on_olymp_reconnected', 'on_olymp_reconnect_failed']`.

- [ ] **Step 1.2: Add 3 new Protocol methods to `Notifier`**

In `src/signal_copier/notify/protocol.py` immediately after line 124 (end of `on_olymp_disconnect` docstring), add:

```python
    async def on_olymp_reconnecting(
        self,
        *,
        attempt: int,
        max_attempts: int,
        downtime_seconds: float,
        next_delay_seconds: float,
    ) -> None:
        """M10 reconnect lifecycle. Fires from `ReconnectingOlympTradeBroker`
        before each backoff sleep. `downtime_seconds` is total elapsed since
        disconnect was detected; `next_delay_seconds` is the backoff before
        the next connect attempt."""

    async def on_olymp_reconnected(
        self,
        *,
        attempts_used: int,
        total_downtime_seconds: float,
    ) -> None:
        """M10 reconnect lifecycle. Fires after a successful reconnect.
        `attempts_used` is 1-based (1 = succeeded on first try)."""

    async def on_olymp_reconnect_failed(
        self,
        *,
        attempts: int,
        total_downtime_seconds: float,
    ) -> None:
        """M10 reconnect lifecycle. Fires after `reconnect_max_attempts`
        consecutive failures. The supervisor then raises `BrokerAuthError`
        so `__main__` exits non-zero (Railway restart as backstop)."""
```

- [ ] **Step 1.3: Add 3 new NoOpNotifier implementations**

In `src/signal_copier/notify/protocol.py` immediately after line 270 (`on_olymp_disconnect` implementation), add:

```python
    async def on_olymp_reconnecting(
        self,
        *,
        attempt: int,
        max_attempts: int,
        downtime_seconds: float,
        next_delay_seconds: float,
    ) -> None:
        _log.warning(
            "notify: event=olymp_reconnecting attempt=%d/%d downtime=%.1fs next_delay=%.1fs",
            attempt, max_attempts, downtime_seconds, next_delay_seconds,
        )

    async def on_olymp_reconnected(
        self,
        *,
        attempts_used: int,
        total_downtime_seconds: float,
    ) -> None:
        _log.warning(
            "notify: event=olymp_reconnected attempts_used=%d total_downtime=%.1fs",
            attempts_used, total_downtime_seconds,
        )

    async def on_olymp_reconnect_failed(
        self,
        *,
        attempts: int,
        total_downtime_seconds: float,
    ) -> None:
        _log.error(
            "notify: event=olymp_reconnect_failed attempts=%d total_downtime=%.1fs",
            attempts, total_downtime_seconds,
        )
```

(Note `error` level for `reconnect_failed` — it's the terminal halt condition, distinct from the WARNING-level lifecycle events.)

- [ ] **Step 1.4: Add 3 no-op methods to `RecordingNotifier` test fixture**

In `tests/_scheduler_fixtures.py` immediately after line 228 (`on_olymp_disconnect` method), add:

```python
    async def on_olymp_reconnecting(
        self,
        *,
        attempt: int,
        max_attempts: int,
        downtime_seconds: float,
        next_delay_seconds: float,
    ) -> None:
        await self._record(
            "on_olymp_reconnecting",
            attempt=attempt,
            max_attempts=max_attempts,
            downtime_seconds=downtime_seconds,
            next_delay_seconds=next_delay_seconds,
        )

    async def on_olymp_reconnected(
        self,
        *,
        attempts_used: int,
        total_downtime_seconds: float,
    ) -> None:
        await self._record(
            "on_olymp_reconnected",
            attempts_used=attempts_used,
            total_downtime_seconds=total_downtime_seconds,
        )

    async def on_olymp_reconnect_failed(
        self,
        *,
        attempts: int,
        total_downtime_seconds: float,
    ) -> None:
        await self._record(
            "on_olymp_reconnect_failed",
            attempts=attempts,
            total_downtime_seconds=total_downtime_seconds,
        )
```

- [ ] **Step 1.5: Add 3 NoOpNotifier log tests**

In `tests/test_notifier.py` immediately after line 194 (end of `test_noop_notifier_logs_olymp_disconnect_at_warning`), add:

```python
@pytest.mark.asyncio
async def test_noop_notifier_logs_olymp_reconnecting_at_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="signal_copier.notify.protocol"):
        await NoOpNotifier().on_olymp_reconnecting(
            attempt=1, max_attempts=5, downtime_seconds=3.0, next_delay_seconds=1.0,
        )
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.WARNING
    msg = caplog.records[0].getMessage()
    assert "event=olymp_reconnecting" in msg
    assert "attempt=1/5" in msg
    assert "downtime=3.0s" in msg


@pytest.mark.asyncio
async def test_noop_notifier_logs_olymp_reconnected_at_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="signal_copier.notify.protocol"):
        await NoOpNotifier().on_olymp_reconnected(
            attempts_used=1, total_downtime_seconds=12.3,
        )
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.WARNING
    msg = caplog.records[0].getMessage()
    assert "event=olymp_reconnected" in msg
    assert "attempts_used=1" in msg


@pytest.mark.asyncio
async def test_noop_notifier_logs_olymp_reconnect_failed_at_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """reconnect_failed is the terminal halt — log at ERROR (vs WARNING)."""
    with caplog.at_level(logging.ERROR, logger="signal_copier.notify.protocol"):
        await NoOpNotifier().on_olymp_reconnect_failed(
            attempts=5, total_downtime_seconds=67.8,
        )
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.ERROR
    msg = caplog.records[0].getMessage()
    assert "event=olymp_reconnect_failed" in msg
```

- [ ] **Step 1.6: Run the test suite and verify it passes**

Run: `pytest tests/test_notifier.py tests/test_recording_notifier_protocol.py -v`
Expected: all tests pass (the 3 new NoOp tests + the updated Protocol-satisfaction test).

- [ ] **Step 1.7: Commit**

```bash
git add src/signal_copier/notify/protocol.py tests/_scheduler_fixtures.py tests/test_notifier.py tests/test_recording_notifier_protocol.py
git commit -m "feat(notify): add 3 reconnect-lifecycle methods to Notifier Protocol + NoOpNotifier"
```

---

## Task 2: Update `TelegramDMNotifier` with 3 new methods + soften `on_olymp_disconnect`

**Files:**
- Modify: `src/signal_copier/notify/telegram_dm.py:306-307` (soften disconnect copy)
- Modify: `src/signal_copier/notify/telegram_dm.py:307` (add 3 new methods after disconnect)
- Modify: `tests/test_telegram_dm.py` (add 3 tests for new methods + 1 test for softened disconnect copy)

- [ ] **Step 2.1: Inspect existing telegram_dm test file to understand pattern**

Read `tests/test_telegram_dm.py` to find the existing test for `on_olymp_disconnect` (line range varies). Look for the pattern used: `RecordingTgClient` or similar fake, and how `await notifier.on_olymp_disconnect()` is invoked.

- [ ] **Step 2.2: Soften `on_olymp_disconnect` copy**

In `src/signal_copier/notify/telegram_dm.py:307`, change:
```python
        await self._send("🔌 OlympTrade disconnected. Process will exit; supervisor will restart.")
```
to:
```python
        await self._send("🔌 OlympTrade disconnected. Reconnecting…")
```

- [ ] **Step 2.3: Update the existing telegram_dm test for `on_olymp_disconnect`**

In `tests/test_telegram_dm.py`, find the test asserting the old "Process will exit" copy and update it to assert "Reconnecting…".

- [ ] **Step 2.4: Add 3 new method tests (write failing tests first)**

In `tests/test_telegram_dm.py` after the existing `on_olymp_disconnect` test, add:

```python
@pytest.mark.asyncio
async def test_telegram_dm_on_olymp_reconnecting() -> None:
    notifier, client = _make_notifier()
    await notifier.on_olymp_reconnecting(
        attempt=2, max_attempts=5, downtime_seconds=3.0, next_delay_seconds=2.0,
    )
    assert client.sent_messages == [
        "🔁 OlympTrade reconnecting (attempt 2/5)\nDowntime: 3.0s\nNext retry in 2.0s",
    ]


@pytest.mark.asyncio
async def test_telegram_dm_on_olymp_reconnected() -> None:
    notifier, client = _make_notifier()
    await notifier.on_olymp_reconnected(
        attempts_used=1, total_downtime_seconds=12.3,
    )
    assert client.sent_messages == [
        "✅ OlympTrade reconnected\n"
        "Attempts: 1\n"
        "Total downtime: 12.3s\n"
        "Action: resumed normal operation. In-flight cascades (if any) were ended with broker_unavailable."
    ]


@pytest.mark.asyncio
async def test_telegram_dm_on_olymp_reconnect_failed() -> None:
    notifier, client = _make_notifier()
    await notifier.on_olymp_reconnect_failed(
        attempts=5, total_downtime_seconds=67.8,
    )
    assert client.sent_messages == [
        "❌ OlympTrade reconnect failed after 5 attempts\n"
        "Total downtime: 67.8s\n"
        "Action: process will exit; Railway supervisor will restart."
    ]
```

(Use whatever helper `_make_notifier()` and the `client.sent_messages` pattern is in the existing tests.)

Run: `pytest tests/test_telegram_dm.py -v`
Expected: FAIL — the 3 new methods don't exist on `TelegramDMNotifier` yet.

- [ ] **Step 2.5: Implement the 3 new methods**

In `src/signal_copier/notify/telegram_dm.py` immediately after line 307 (the softened `on_olymp_disconnect`), add:

```python
    async def on_olymp_reconnecting(
        self,
        *,
        attempt: int,
        max_attempts: int,
        downtime_seconds: float,
        next_delay_seconds: float,
    ) -> None:
        text = (
            f"🔁 OlympTrade reconnecting (attempt {attempt}/{max_attempts})\n"
            f"Downtime: {downtime_seconds:.1f}s\n"
            f"Next retry in {next_delay_seconds:.1f}s"
        )
        await self._send(text)

    async def on_olymp_reconnected(
        self,
        *,
        attempts_used: int,
        total_downtime_seconds: float,
    ) -> None:
        text = (
            f"✅ OlympTrade reconnected\n"
            f"Attempts: {attempts_used}\n"
            f"Total downtime: {total_downtime_seconds:.1f}s\n"
            f"Action: resumed normal operation. "
            f"In-flight cascades (if any) were ended with broker_unavailable."
        )
        await self._send(text)

    async def on_olymp_reconnect_failed(
        self,
        *,
        attempts: int,
        total_downtime_seconds: float,
    ) -> None:
        text = (
            f"❌ OlympTrade reconnect failed after {attempts} attempts\n"
            f"Total downtime: {total_downtime_seconds:.1f}s\n"
            f"Action: process will exit; Railway supervisor will restart."
        )
        await self._send(text)
```

- [ ] **Step 2.6: Run the test suite and verify it passes**

Run: `pytest tests/test_telegram_dm.py -v`
Expected: all 4 tests pass (the softened disconnect test + 3 new method tests).

- [ ] **Step 2.7: Commit**

```bash
git add src/signal_copier/notify/telegram_dm.py tests/test_telegram_dm.py
git commit -m "feat(notify): implement 3 M10 reconnect-lifecycle Telegram DM methods"
```

---

## Task 3: Add `FakeClientFactory` to `tests/_broker_fixtures.py`

**Files:**
- Modify: `tests/_broker_fixtures.py` (append `FakeClientFactory` class at end)

- [ ] **Step 3.1: Add `FakeClientFactory` class**

In `tests/_broker_fixtures.py` at the end of the file (after `make_balance_message`), add:

```python
class FakeClientFactory:
    """Returns FakeOlympTradeClient instances one-at-a-time from a list.

    Each call to `factory()` pops the next fake from the list. After the list
    is exhausted, returns the LAST fake (so a test can declare "first reconnect
    uses fake[1]; subsequent reuses the recovered fake[1]"). Tests that want
    every reconnect to use a fresh fake pass a list long enough to cover the
    attempt count.

    Matches the contract of OlympTradeBroker._client_factory
    (Callable[[], OlympTradeClient], see broker/olymp.py:106).
    """

    def __init__(self, fakes: list[FakeOlympTradeClient]) -> None:
        if not fakes:
            raise ValueError("FakeClientFactory requires at least one fake")
        self._fakes = list(fakes)
        self._index = 0
        self.call_count = 0

    def __call__(self) -> FakeOlympTradeClient:
        self.call_count += 1
        if self._index >= len(self._fakes):
            return self._fakes[-1]
        fake = self._fakes[self._index]
        self._index += 1
        return fake
```

- [ ] **Step 3.2: Quick sanity check (no separate test file)**

There is no separate test for this fixture — it's tested via `tests/test_reconnect_supervisor.py` in Task 4 onward. Import the new class to confirm it loads:

Run: `python -c "from tests._broker_fixtures import FakeClientFactory; f = FakeClientFactory([])" 2>&1 | tail -1`
Expected: `ValueError: FakeClientFactory requires at least one fake` (confirms the validation works).

- [ ] **Step 3.3: Commit**

```bash
git add tests/_broker_fixtures.py
git commit -m "test(fixtures): add FakeClientFactory for reconnect-supervisor tests"
```

---

## Task 4: Implement `ReconnectingOlympTradeBroker` — initial `connect()` only (TDD)

**Files:**
- Create: `src/signal_copier/broker/reconnect.py`
- Create: `tests/test_reconnect_supervisor.py`

- [ ] **Step 4.1: Write the failing test for initial connect**

Create `tests/test_reconnect_supervisor.py` with:

```python
"""Tests for M10's ReconnectingOlympTradeBroker wrapper.

Wraps OlympTradeBroker (broker/olymp.py). Adds a 1s polling watcher that
detects WS disconnects and a reconnect loop with exponential backoff.
"""
from __future__ import annotations

import asyncio
from collections.abc import Iterator
from decimal import Decimal

import pytest

from signal_copier.broker.base import Broker
from signal_copier.broker.reconnect import ReconnectingOlympTradeBroker
from tests._broker_fixtures import FakeClientFactory, FakeOlympTradeClient
from tests._scheduler_fixtures import RecordingNotifier


@pytest.fixture
def notifier() -> RecordingNotifier:
    return RecordingNotifier()


@pytest.fixture
def fake_client() -> FakeOlympTradeClient:
    return FakeOlympTradeClient()


@pytest.fixture
def factory(fake_client: FakeOlympTradeClient) -> Iterator[FakeClientFactory]:
    yield FakeClientFactory([fake_client])


def _make_wrapper(
    notifier: RecordingNotifier,
    factory: FakeClientFactory,
    *,
    reconnect_max_attempts: int = 3,
    watcher_poll_seconds: float = 0.05,
) -> ReconnectingOlympTradeBroker:
    return ReconnectingOlympTradeBroker(
        access_token="fake",
        account_id="12345",
        account_group="demo",
        notifier=notifier,
        _client_factory=factory,
        reconnect_max_attempts=reconnect_max_attempts,
        watcher_poll_seconds=watcher_poll_seconds,
    )


async def test_satisfies_broker_protocol(notifier: RecordingNotifier, factory: FakeClientFactory) -> None:
    """ReconnectingOlympTradeBroker satisfies the Broker Protocol."""
    wrapper = _make_wrapper(notifier, factory)
    assert isinstance(wrapper, Broker)


async def test_initial_connect_succeeds(
    notifier: RecordingNotifier, factory: FakeClientFactory, fake_client: FakeOlympTradeClient
) -> None:
    """connect() calls inner.connect() once; wrapper._inner is set."""
    wrapper = _make_wrapper(notifier, factory)
    assert wrapper._inner is None
    assert factory.call_count == 0

    await wrapper.connect()

    assert wrapper._inner is not None
    assert factory.call_count == 1
    assert fake_client.start_called is True
    assert fake_client.initialize_session_called is True


async def test_close_is_idempotent_and_cancels_watcher(
    notifier: RecordingNotifier, factory: FakeClientFactory, fake_client: FakeOlympTradeClient
) -> None:
    """close() cancels the watcher task; second close() is a no-op."""
    wrapper = _make_wrapper(notifier, factory)
    await wrapper.connect()
    assert wrapper._watcher is not None
    assert not wrapper._watcher.done()

    await wrapper.close()
    assert wrapper._watcher is None or wrapper._watcher.done()
    assert fake_client.stop_called is True

    # Second close must not raise
    await wrapper.close()
```

Run: `pytest tests/test_reconnect_supervisor.py -v`
Expected: FAIL — `ImportError: cannot import name 'ReconnectingOlympTradeBroker' from 'signal_copier.broker.reconnect'`.

- [ ] **Step 4.2: Implement the minimal `ReconnectingOlympTradeBroker`**

Create `src/signal_copier/broker/reconnect.py`:

```python
"""M10 — ReconnectingOlympTradeBroker.

Wraps OlympTradeBroker (broker/olymp.py) with a self-healing reconnect
supervisor. Detects WS drops via (a) a 1s polling watcher reading
inner._client.connection.is_connected, and (b) ConnectionError raised by
inner.place() / inner.wait_result(). On detection, runs an exponential-
backoff reconnect loop (1s → 2s → 4s → 8s → 16s → 30s cap, max
reconnect_max_attempts). After exhaustion, raises BrokerAuthError so
__main__ exits non-zero (Railway restart as backstop).

Spec: docs/superpowers/specs/2026-06-23-m10-olymptrade-reconnect-supervisor-design.md
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from enum import Enum
from typing import TYPE_CHECKING

from olymptrade_ws import OlympTradeClient

from signal_copier.broker.base import BrokerAuthError
from signal_copier.broker.olymp import OlympTradeBroker
from signal_copier.domain.gale import Stage
from signal_copier.domain.signal import Signal
from signal_copier.infra.clock import monotonic
from signal_copier.notify.protocol import Notifier

if TYPE_CHECKING:
    from signal_copier.domain.state import StageResult

_log = logging.getLogger(__name__)


_BACKOFF_BASE_SECONDS: float = 1.0
_BACKOFF_CAP_SECONDS: float = 30.0


class _ConnectionState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"


def compute_backoff_seconds(attempt: int) -> float:
    """Exponential backoff with a 30s cap. `attempt` is 0-indexed.

    attempt=0 -> 1.0, attempt=1 -> 2.0, attempt=4 -> 16.0, attempt>=5 -> 30.0.
    """
    return min(_BACKOFF_BASE_SECONDS * (2.0**attempt), _BACKOFF_CAP_SECONDS)


class ReconnectingOlympTradeBroker:
    """Wrapper around OlympTradeBroker with a self-healing reconnect loop.

    Satisfies the Broker Protocol (broker/base.py:40). Constructed with the
    same args as OlympTradeBroker plus two knobs: reconnect_max_attempts
    (default 5) and watcher_poll_seconds (default 1.0).

    The `connect()` method starts a background watcher task and calls
    `inner.connect()`. The watcher polls `inner._client.connection.is_connected`
    every `watcher_poll_seconds`. On a False reading (or on a ConnectionError
    from `place()`/`wait_result()`), it triggers `_trigger_reconnect()`,
    which tears down the dead inner broker, builds a fresh one via
    `_client_factory`, calls `connect()` on it, and atomically swaps the
    reference. After `reconnect_max_attempts` consecutive failures, raises
    BrokerAuthError so __main__ exits non-zero.

    In-flight cascades that hit a disconnect end with
    `error_reason='broker_unavailable'` via M6's existing ConnectionError→
    'error' mapping at scheduler/trigger.py:662 — the wrapper does not
    attempt to preserve or resume cascades across reconnect (M10 spec §2.2).
    """

    def __init__(
        self,
        *,
        access_token: str,
        account_id: str,
        account_group: str = "demo",
        notifier: Notifier,
        _client_factory: Callable[[], OlympTradeClient] | None = None,
        reconnect_max_attempts: int = 5,
        watcher_poll_seconds: float = 1.0,
    ) -> None:
        self._access_token = access_token
        self._account_id = account_id
        self._account_group = account_group
        self._notifier = notifier
        self._client_factory = _client_factory or self._default_client_factory
        self._reconnect_max_attempts = reconnect_max_attempts
        self._watcher_poll_seconds = watcher_poll_seconds

        self._inner: OlympTradeBroker | None = None
        self._watcher: asyncio.Task[None] | None = None
        self._reconnect_lock = asyncio.Lock()
        self._consecutive_failures: int = 0
        self._state: _ConnectionState = _ConnectionState.DISCONNECTED

    def _default_client_factory(self) -> OlympTradeClient:
        return OlympTradeClient(
            access_token=self._access_token,
            account_id=int(self._account_id) if self._account_id else None,
            account_group=self._account_group,
            log_raw_messages=False,
        )

    def _build_inner(self) -> OlympTradeBroker:
        return OlympTradeBroker(
            access_token=self._access_token,
            account_id=self._account_id,
            account_group=self._account_group,
            notifier=self._notifier,
            _client_factory=self._client_factory,
        )

    async def connect(self) -> None:
        """Build inner broker, connect it, start watcher task."""
        self._inner = self._build_inner()
        await self._inner.connect()
        self._state = _ConnectionState.CONNECTED
        self._consecutive_failures = 0
        self._watcher = asyncio.create_task(self._watcher_loop(), name="olymp-watcher")

    async def place(self, signal: Signal, *, stage: Stage, amount: "Decimal") -> str:
        """Delegate to inner; on ConnectionError trigger reconnect and re-raise."""
        assert self._inner is not None
        try:
            return await self._inner.place(signal, stage=stage, amount=amount)
        except ConnectionError:
            await self._trigger_reconnect()
            raise

    async def wait_result(self, trade_id: str, *, timeout: float) -> "StageResult":
        """Delegate to inner; on ConnectionError trigger reconnect and re-raise."""
        assert self._inner is not None
        try:
            return await self._inner.wait_result(trade_id, timeout=timeout)
        except ConnectionError:
            await self._trigger_reconnect()
            raise

    async def close(self) -> None:
        """Cancel watcher task, close inner broker. Idempotent."""
        if self._watcher is not None and not self._watcher.done():
            self._watcher.cancel()
            try:
                await self._watcher
            except asyncio.CancelledError:
                pass
        self._watcher = None
        if self._inner is not None:
            await self._inner.close()
        self._state = _ConnectionState.DISCONNECTED

    async def _watcher_loop(self) -> None:
        """Poll is_connected every watcher_poll_seconds; trigger reconnect on False."""
        try:
            while True:
                await asyncio.sleep(self._watcher_poll_seconds)
                if self._state != _ConnectionState.CONNECTED:
                    continue
                if self._inner is None or self._inner._client is None:
                    continue
                if not self._inner._client.connection.is_connected:
                    await self._trigger_reconnect()
        except asyncio.CancelledError:
            return

    async def _trigger_reconnect(self) -> None:
        """Acquire reconnect lock; run reconnect loop or no-op if already running.

        If a reconnect is already in progress, the caller blocks until that
        reconnect finishes (success or exhaustion). Otherwise, the caller
        runs the reconnect loop itself. After this coroutine returns,
        `self._inner` is either the new live broker (success) or the state
        is DISCONNECTED (exhaustion).
        """
        if self._state == _ConnectionState.RECONNECTING:
            # Already running; wait for it to finish.
            # We need to acquire-and-release the lock so we serialize on the
            # running loop's completion. But the lock is HELD by that loop.
            # Wait for the lock to be released (which happens when the loop
            # finishes).
            async with self._reconnect_lock:
                pass
            return
        async with self._reconnect_lock:
            await self._reconnect_loop()

    async def _reconnect_loop(self) -> None:
        """Tear down dead inner; up to reconnect_max_attempts fresh connects."""
        # ... placeholder; implemented in Task 5
        raise NotImplementedError
```

Run: `pytest tests/test_reconnect_supervisor.py -v`
Expected: all 3 tests PASS. The `_reconnect_loop` raises `NotImplementedError` only if a reconnect is actually triggered — these tests don't trigger one, so the placeholder doesn't fire.

- [ ] **Step 4.3: Commit**

```bash
git add src/signal_copier/broker/reconnect.py tests/test_reconnect_supervisor.py
git commit -m "feat(broker): add ReconnectingOlympTradeBroker skeleton with initial connect"
```

---

## Task 5: Implement watcher-driven disconnect detection + reconnect loop (TDD)

**Files:**
- Modify: `src/signal_copier/broker/reconnect.py` (replace `_reconnect_loop` placeholder with real impl; add `_safe_notify` helper)
- Modify: `tests/test_reconnect_supervisor.py` (add 4 tests)

- [ ] **Step 5.1: Write 3 failing tests**

Append to `tests/test_reconnect_supervisor.py`:

```python
from signal_copier.broker.reconnect import compute_backoff_seconds


async def test_watcher_detects_disconnect_and_reconnects(
    notifier: RecordingNotifier,
) -> None:
    """Flipping inner.connection._connected=False triggers a watcher-driven reconnect."""
    fake0 = FakeOlympTradeClient()
    fake1 = FakeOlympTradeClient()
    factory = FakeClientFactory([fake0, fake1])
    wrapper = _make_wrapper(
        notifier, factory,
        reconnect_max_attempts=3, watcher_poll_seconds=0.02,
    )

    await wrapper.connect()
    inner0_id = id(wrapper._inner)

    # Simulate disconnect: flip fake0's underlying connection state.
    fake0.connection._connected = False

    # Wait up to ~1s for the watcher to detect and the reconnect loop to swap.
    for _ in range(50):
        await asyncio.sleep(0.02)
        if id(wrapper._inner) != inner0_id:
            break

    assert id(wrapper._inner) != inner0_id, "watcher did not trigger reconnect"
    assert factory.call_count == 2
    assert fake1.start_called is True

    # Notifier saw the reconnect lifecycle.
    methods = [m for m, _ in notifier.calls]
    assert "on_olymp_disconnect" in methods
    assert "on_olymp_reconnecting" in methods
    assert "on_olymp_reconnected" in methods


@pytest.mark.parametrize("trigger_method", ["place", "wait_result"])
async def test_event_driven_reconnect(
    notifier: RecordingNotifier,
    trigger_method: str,
) -> None:
    """ConnectionError from inner.place() or inner.wait_result() triggers a reconnect,
    and is re-raised to the caller so M6's existing handler can map it to 'error'."""
    fake0 = FakeOlympTradeClient()
    fake1 = FakeOlympTradeClient()
    factory = FakeClientFactory([fake0, fake1])
    wrapper = _make_wrapper(
        notifier, factory,
        reconnect_max_attempts=3, watcher_poll_seconds=10.0,  # watcher disabled
    )

    await wrapper.connect()
    inner0_id = id(wrapper._inner)
    assert wrapper._inner is not None

    if trigger_method == "place":
        # Force inner.place_order() to raise ConnectionError on next call.
        wrapper._inner._client.trade.raise_on_call = ConnectionError("WS down on place")
        sig = make_signal()
        with pytest.raises(ConnectionError, match="WS down on place"):
            await wrapper.place(sig, stage="initial", amount=Decimal("2.00"))
    else:
        # Force inner.wait_result() to raise ConnectionError on next call.
        async def raise_conn_err(*args: object, **kwargs: object) -> StageResult:
            raise ConnectionError("WS down on wait_result")

        wrapper._inner.wait_result = raise_conn_err  # type: ignore[method-assign]
        with pytest.raises(ConnectionError, match="WS down on wait_result"):
            await wrapper.wait_result("fake-trade-id", timeout=1.0)

    # Reconnect should have swapped the inner broker.
    assert id(wrapper._inner) != inner0_id, "event-driven path did not trigger reconnect"
    assert factory.call_count == 2

    methods = [m for m, _ in notifier.calls]
    assert "on_olymp_disconnect" in methods
    assert "on_olymp_reconnected" in methods


def test_compute_backoff_seconds_caps_at_30() -> None:
    """Backoff doubles: 1, 2, 4, 8, 16, then 30-cap."""
    assert compute_backoff_seconds(0) == 1.0
    assert compute_backoff_seconds(1) == 2.0
    assert compute_backoff_seconds(2) == 4.0
    assert compute_backoff_seconds(3) == 8.0
    assert compute_backoff_seconds(4) == 16.0
    assert compute_backoff_seconds(5) == 30.0
    assert compute_backoff_seconds(10) == 30.0
```

Run: `pytest tests/test_reconnect_supervisor.py -v`
Expected: 3 of 3 new tests FAIL — `compute_backoff_seconds` doesn't exist; `_reconnect_loop` raises NotImplementedError.

- [ ] **Step 5.2: Implement the real `_reconnect_loop` and `_safe_notify`**

In `src/signal_copier/broker/reconnect.py`, replace the `NotImplementedError` placeholder in `_reconnect_loop` and add `_safe_notify`. The new `_reconnect_loop`:

```python
    async def _safe_notify(self, coro: object) -> None:
        """Await a notifier call; absorb exceptions so they don't break the loop."""
        try:
            await coro  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001 — defensive isolation
            _log.warning("notifier raised, continuing: exc=%s", exc)

    async def _reconnect_loop(self) -> None:
        """Tear down dead inner; up to reconnect_max_attempts fresh connects.

        Sets self._state = RECONNECTING at entry; back to CONNECTED on success
        or DISCONNECTED on exhaustion. Notifies user at each lifecycle event.
        """
        self._state = _ConnectionState.RECONNECTING
        disconnect_detected_at = monotonic()
        await self._safe_notify(self._notifier.on_olymp_disconnect())

        if self._inner is not None:
            try:
                await self._inner.close()
            except Exception as exc:  # noqa: BLE001 — close is best-effort
                _log.warning("inner.close raised during reconnect: exc=%s", exc)

        last_exc: Exception | None = None
        for attempt in range(1, self._reconnect_max_attempts + 1):
            delay = compute_backoff_seconds(attempt - 1)
            downtime = monotonic() - disconnect_detected_at
            await self._safe_notify(
                self._notifier.on_olymp_reconnecting(
                    attempt=attempt,
                    max_attempts=self._reconnect_max_attempts,
                    downtime_seconds=downtime,
                    next_delay_seconds=delay,
                )
            )
            await asyncio.sleep(delay)
            try:
                new_inner = self._build_inner()
                await new_inner.connect()
            except Exception as exc:  # noqa: BLE001 — connection failure
                self._consecutive_failures += 1
                last_exc = exc
                _log.warning(
                    "reconnect attempt %d/%d failed: exc=%s",
                    attempt, self._reconnect_max_attempts, exc,
                )
                continue

            # Success: swap, notify, return.
            self._inner = new_inner
            self._consecutive_failures = 0
            self._state = _ConnectionState.CONNECTED
            total_downtime = monotonic() - disconnect_detected_at
            await self._safe_notify(
                self._notifier.on_olymp_reconnected(
                    attempts_used=attempt,
                    total_downtime_seconds=total_downtime,
                )
            )
            _log.info(
                "OlympTrade reconnected on attempt %d/%d after %.1fs",
                attempt, self._reconnect_max_attempts, total_downtime,
            )
            return

        # Exhausted.
        self._state = _ConnectionState.DISCONNECTED
        total_downtime = monotonic() - disconnect_detected_at
        await self._safe_notify(
            self._notifier.on_olymp_reconnect_failed(
                attempts=self._reconnect_max_attempts,
                total_downtime_seconds=total_downtime,
            )
        )
        raise BrokerAuthError(
            f"OlympTrade reconnect exhausted after {self._reconnect_max_attempts} attempts"
        ) from last_exc
```

(Add `from signal_copier.infra.clock import monotonic` to imports.)

Run: `pytest tests/test_reconnect_supervisor.py -v`
Expected: all tests in the file PASS (6 total: 3 from Task 4 + 3 new from Task 5).

- [ ] **Step 5.3: Commit**

```bash
git add src/signal_copier/broker/reconnect.py tests/test_reconnect_supervisor.py
git commit -m "feat(broker): implement reconnect loop with exponential backoff + watcher detection"
```

---

## Task 6: Implement circuit breaker (5 failures → halt) + concurrent-detection lock (TDD)

**Files:**
- Modify: `tests/test_reconnect_supervisor.py` (add 3 tests)

- [ ] **Step 6.1: Write 3 failing tests**

Append to `tests/test_reconnect_supervisor.py`:

```python
async def test_reconnect_exhausts_after_max_attempts(notifier: RecordingNotifier) -> None:
    """5 consecutive failures → BrokerAuthError + on_olymp_reconnect_failed fired."""
    bad_fakes = [FakeOlympTradeClient() for _ in range(5)]

    async def bad_start() -> None:
        raise BrokerAuthError("token rejected")

    for f in bad_fakes:
        f.start = bad_start  # type: ignore[method-assign]

    factory = FakeClientFactory(bad_fakes)
    wrapper = _make_wrapper(
        notifier, factory,
        reconnect_max_attempts=5, watcher_poll_seconds=10.0,
    )

    # Pre-populate _inner to a real connected fake so close() doesn't fail.
    good_inner_factory = FakeClientFactory([FakeOlympTradeClient()])
    wrapper._client_factory = good_inner_factory
    wrapper._inner = wrapper._build_inner()
    await wrapper._inner.connect()

    # Now swap to the bad factory and trigger reconnect.
    wrapper._client_factory = factory

    with pytest.raises(BrokerAuthError, match="reconnect exhausted"):
        await wrapper._trigger_reconnect()

    methods = [m for m, _ in notifier.calls]
    assert "on_olymp_disconnect" in methods
    assert methods.count("on_olymp_reconnecting") == 5
    assert "on_olymp_reconnect_failed" in methods


async def test_reconnect_resets_failure_counter_on_success(
    notifier: RecordingNotifier,
) -> None:
    """After a successful reconnect, a NEW disconnect can run the full N attempts again."""
    # First disconnect: attempt 1 fails, attempt 2 succeeds.
    bad_fake = FakeOlympTradeClient()

    async def bad_start() -> None:
        raise BrokerAuthError("transient")

    bad_fake.start = bad_start  # type: ignore[method-assign]

    good_fake = FakeOlympTradeClient()
    factory = FakeClientFactory([bad_fake, good_fake])
    wrapper = _make_wrapper(
        notifier, factory,
        reconnect_max_attempts=3, watcher_poll_seconds=10.0,
    )

    wrapper._inner = wrapper._build_inner()
    await wrapper._inner.connect()

    # First reconnect cycle: should succeed on attempt 2.
    await wrapper._trigger_reconnect()
    assert wrapper._consecutive_failures == 0

    # Second disconnect: simulate all 3 attempts failing.
    bad_fake2 = FakeOlympTradeClient()

    async def bad_start2() -> None:
        raise BrokerAuthError("permanent")

    bad_fake2.start = bad_start2  # type: ignore[method-assign]
    wrapper._client_factory = FakeClientFactory([bad_fake2, bad_fake2, bad_fake2])

    with pytest.raises(BrokerAuthError, match="reconnect exhausted"):
        await wrapper._trigger_reconnect()


async def test_concurrent_detection_only_one_reconnect_loop(
    notifier: RecordingNotifier,
) -> None:
    """If both the watcher and an in-flight place() detect the disconnect
    simultaneously, only ONE reconnect loop runs (asyncio.Lock + state-check
    guard in `_trigger_reconnect`)."""
    fake0 = FakeOlympTradeClient()
    fake1 = FakeOlympTradeClient()
    factory = FakeClientFactory([fake0, fake1])
    wrapper = _make_wrapper(
        notifier, factory,
        reconnect_max_attempts=3, watcher_poll_seconds=0.02,
    )

    await wrapper.connect()

    # Trigger reconnect from two coroutines "simultaneously".
    fake0.connection._connected = False
    # Let the watcher start its reconnect.
    await asyncio.sleep(0.05)

    # Now also trigger via place() while the watcher is still in flight.
    sig = make_signal()
    wrapper._inner._client.trade.raise_on_call = ConnectionError("WS down")
    with pytest.raises(ConnectionError):
        await wrapper.place(sig, stage="initial", amount=Decimal("2.00"))

    # Count on_olymp_disconnect calls — must be exactly 1.
    methods = [m for m, _ in notifier.calls]
    disconnect_count = methods.count("on_olymp_disconnect")
    assert disconnect_count == 1, (
        f"expected exactly 1 disconnect notification, got {disconnect_count}: "
        f"{methods}"
    )
```

Run: `pytest tests/test_reconnect_supervisor.py -v`
Expected: all 9 tests PASS.

- [ ] **Step 6.2: Run the tests**

Run: `pytest tests/test_reconnect_supervisor.py -v`
Expected: all 9 tests PASS. (The circuit breaker counter logic was added in Task 5's `_reconnect_loop` — `self._consecutive_failures = 0` on successful reconnect, incremented on each failure, raised as `BrokerAuthError` after exhaustion.)

- [ ] **Step 6.3: Commit**

```bash
git add tests/test_reconnect_supervisor.py
git commit -m "test(broker): cover reconnect exhaustion, counter reset, and concurrent detection"
```

---

## Task 7: Wire `ReconnectingOlympTradeBroker` into `__main__.py`

**Files:**
- Modify: `src/signal_copier/__main__.py:77-88` (wrap `OlympTradeBroker(...)` in `ReconnectingOlympTradeBroker(...)`)

- [ ] **Step 7.1: Verify `__main__.py` test passes with current construction (baseline)**

Run: `pytest tests/test_main.py -v`
Expected: existing main tests pass with `OlympTradeBroker` (this is the baseline before our change).

- [ ] **Step 7.2: Update `__main__.py`**

a) Remove line 15:
```python
from signal_copier.broker.olymp import OlympTradeBroker
```
(`OlympTradeBroker` is no longer constructed in `__main__.py` — the wrapper is used instead. The class remains importable from `signal_copier.broker.olymp` for any code that needs the raw M8 broker.)

b) Add immediately after line 13 (`from signal_copier.broker.base import Broker, BrokerAuthError`):
```python
from signal_copier.broker.reconnect import ReconnectingOlympTradeBroker
```

c) Replace the broker construction block at lines 77-88 with:
```python
            broker = ReconnectingOlympTradeBroker(
                access_token=config.olymp_access_token,
                account_id=config.olymp_account_id,
                account_group=config.olymp_account_group,
                notifier=notifier,
            )
            _log.info(
                "Broker: ReconnectingOlympTradeBroker (live %s, account_id=%s)",
                config.olymp_account_group,
                config.olymp_account_id,
            )
            await broker.connect()
```

- [ ] **Step 7.3: Run the test suite**

Run: `pytest tests/ -v --tb=short`
Expected: ALL existing tests pass (108+) plus the 9 new supervisor tests (117+ total). No regressions.

- [ ] **Step 7.4: Run linting + type-checking**

Run: `ruff check src/signal_copier/broker/reconnect.py src/signal_copier/__main__.py src/signal_copier/notify/protocol.py src/signal_copier/notify/telegram_dm.py tests/test_reconnect_supervisor.py tests/test_notifier.py tests/test_telegram_dm.py tests/test_recording_notifier_protocol.py tests/_scheduler_fixtures.py tests/_broker_fixtures.py`
Expected: PASS.

Run: `ruff format --check src/signal_copier/broker/reconnect.py src/signal_copier/__main__.py src/signal_copier/notify/protocol.py src/signal_copier/notify/telegram_dm.py tests/test_reconnect_supervisor.py tests/test_notifier.py tests/test_telegram_dm.py tests/test_recording_notifier_protocol.py tests/_scheduler_fixtures.py tests/_broker_fixtures.py`
Expected: PASS. If format issues, run `ruff format <files>` to fix.

Run: `mypy --strict src/signal_copier/broker/reconnect.py`
Expected: PASS (or fix any type errors).

- [ ] **Step 7.5: Check coverage on new file**

Run: `pytest tests/test_reconnect_supervisor.py --cov=signal_copier.broker.reconnect --cov-report=term-missing`
Expected: line coverage ≥ 90% on `signal_copier/broker/reconnect.py`.

If under 90%, add tests for uncovered lines.

- [ ] **Step 7.6: Commit**

```bash
git add src/signal_copier/__main__.py
git commit -m "feat(main): wire ReconnectingOlympTradeBroker for live demo mode"
```

---

## Task 8: Full verification — all tests + lint + coverage

**Files:**
- (none; verification only)

- [ ] **Step 8.1: Run the entire test suite**

Run: `pytest tests/ -v --tb=short`
Expected: ALL tests pass (117+ total: 108 existing + 9 new supervisor tests + 3 new NoOpNotifier tests + 3 new TelegramDM tests + any updates).

- [ ] **Step 8.2: Run project-wide lint + type-check**

Run: `ruff check src/ tests/`
Expected: PASS (no errors).

Run: `mypy --strict src/signal_copier/`
Expected: PASS.

- [ ] **Step 8.3: Verify coverage**

Run: `pytest tests/ --cov=signal_copier.broker.reconnect --cov-report=term-missing`
Expected: ≥90% on `signal_copier/broker/reconnect.py`.

- [ ] **Step 8.4: Update CHANGELOG in PRD**

In `docs/PRD.md` §18 (Changelog), add an entry:

```markdown
### v0.8 — M10 self-healing OlympTrade reconnect supervisor

- **M10 complete.** New `ReconnectingOlympTradeBroker` wrapper at `src/signal_copier/broker/reconnect.py` detects WS drops via 1s polling watcher + event-driven `place/wait_result` ConnectionError path. On disconnect, runs exponential-backoff reconnect loop (1s → 30s cap, max 5 attempts). On exhaustion: `BrokerAuthError` → `__main__` exit-2 → Railway restart. In-flight cascades end with `error_reason='broker_unavailable'` (existing M6 mapping).
- **Notifier Protocol +3 methods**: `on_olymp_reconnecting`, `on_olymp_reconnected`, `on_olymp_reconnect_failed`. `TelegramDMNotifier` implements with FR-7.1-aligned DM copy. `on_olymp_disconnect` copy softened from "Process will exit; supervisor will restart" to "Reconnecting…".
- **Test surface**: 9 new tests in `tests/test_reconnect_supervisor.py`; 3 new NoOpNotifier tests; 3 new TelegramDM tests; extended `RecordingNotifier` + protocol-satisfaction test.
- **Spec**: `docs/superpowers/specs/2026-06-23-m10-olymptrade-reconnect-supervisor-design.md`. Plan: `docs/superpowers/plans/2026-06-23-m10-olymptrade-reconnect-supervisor.md`. No edits to vendored `olymptrade_ws` (R-15).
```

- [ ] **Step 8.5: Commit**

```bash
git add docs/PRD.md
git commit -m "docs(prd): add v0.8 changelog entry for M10"
```

---

## Acceptance Criteria

M10 is done when:

- [ ] `src/signal_copier/broker/reconnect.py` exists with `ReconnectingOlympTradeBroker` class
- [ ] `ReconnectingOlympTradeBroker` satisfies `isinstance(_, Broker)` Protocol check
- [ ] `Notifier` Protocol has 3 new methods; `NoOpNotifier`, `TelegramDMNotifier`, `RecordingNotifier` all implement them
- [ ] `__main__.py` constructs `ReconnectingOlympTradeBroker` when `DRY_RUN=false`
- [ ] All 9 tests in `tests/test_reconnect_supervisor.py` pass
- [ ] All existing 108+ tests still pass (zero regressions)
- [ ] Line coverage on `signal_copier/broker/reconnect.py` ≥ 90%
- [ ] `mypy --strict` passes on `signal_copier/`
- [ ] `ruff check` passes on `src/` and `tests/`
- [ ] `ruff format --check` passes on all modified files
- [ ] PRD v0.7 §15 M10's verifiable outcome met: "Kill network mid-trade; tool reconnects within 30s" (validated by `test_watcher_detects_disconnect_and_reconnects`)
- [ ] PRD §18 changelog updated to v0.8
