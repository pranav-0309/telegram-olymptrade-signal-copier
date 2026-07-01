# M13.2 MT5 Broker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the M13.1 `Mt5Broker` stub with a real implementation that talks to MetaTrader 5 via `mt5linux`, add a reconnect helper, and ship a preflight sanity-check tool. End state: a `DRY_RUN=false` boot opens market orders, polls for position status, and closes positions reporting broker-side PnL as `Decimal`.

**Architecture:** One broker class (`Mt5Broker`) with internal retry via a function-call helper (`with_retry`) — no wrapper class. `mt5linux` is imported at module scope so tests can mock it cleanly. Preflight is a standalone synchronous script.

**Tech Stack:** Python 3.13, asyncio, mt5linux (drop-in for `MetaTrader5`), pytest + `monkeypatch.setattr` for mockable MT5 calls.

---

## File Structure

### Created (Commit 1)
- `src/signal_copier/broker/reconnect.py` — `compute_backoff_seconds()` + `async with_retry()` helpers
- `tests/test_mt5_broker.py` — replaces `tests/test_mt5_broker_stub.py`; 10 mocked tests covering all 5 Protocol methods

### Created (Commit 2)
- `tools/mt5_preflight.py` — `run_preflight()` function + `__main__` block
- `tests/test_mt5_preflight.py` — 4 mocked tests (happy path + 3 failure paths)

### Modified — Commit 1
- `src/signal_copier/broker/mt5.py` — REPLACE the M13.1 stub with the real impl
- `pyproject.toml` — add `mt5linux>=1.0.0` dep
- `uv.lock` — auto-updated by `uv sync`

### Deleted — Commit 1
- `tests/test_mt5_broker_stub.py` — replaced by `tests/test_mt5_broker.py`

---

## Tasks

### Task 1: Add `mt5linux` dependency

**Files:**
- Modify: `pyproject.toml` (add 1 line to `dependencies`)

- [ ] **Step 1: Open `pyproject.toml` and locate the `dependencies` block**

The block is around lines 9-15. It currently contains:
```toml
dependencies = [
    "pydantic-settings>=2.6",  # M2: config layer
    "tzdata>=2024.1",          # IANA tz database on Windows
    "asyncpg>=0.30",           # M4: async PostgreSQL driver
    "telethon>=1.44",          # M5: Telegram MTProto user-account client
    "loguru>=0.7,<1.0",        # M7: rotating loguru sinks + DM mirror
]
```

- [ ] **Step 2: Add the `mt5linux` dep line at the end of the block**

Append:
```toml
    "mt5linux>=1.0.0",         # M13.2: MT5 client (drop-in for MetaTrader5)
```

After Step 2 the dependencies block reads:
```toml
dependencies = [
    "pydantic-settings>=2.6",  # M2: config layer
    "tzdata>=2024.1",          # IANA tz database on Windows
    "asyncpg>=0.30",           # M4: async PostgreSQL driver
    "telethon>=1.44",          # M5: Telegram MTProto user-account client
    "loguru>=0.7,<1.0",        # M7: rotating loguru sinks + DM mirror
    "mt5linux>=1.0.0",         # M13.2: MT5 client (drop-in for MetaTrader5)
]
```

- [ ] **Step 3: Sync the lockfile**

Run:
```bash
uv sync
```

Expected: `Resolved N packages, installed M packages`. The `mt5linux` package is now installed in `.venv`.

- [ ] **Step 4: Verify the import works**

Run:
```bash
uv run python -c "import mt5linux as mt5; print(mt5.__name__)"
```

Expected output: `mt5linux` (no error — confirms import succeeds).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build(deps): add mt5linux>=1.0.0 for M13.2 MT5 broker impl"
```

---

### Task 2: TDD `compute_backoff_seconds` (pure function)

**Files:**
- Create: `src/signal_copier/broker/reconnect.py`
- Modify: `tests/test_reconnect.py` (NEW test file)

- [ ] **Step 1: Write the failing test for `compute_backoff_seconds`**

Create `tests/test_reconnect.py`:

```python
"""Tests for broker/reconnect.py helpers."""
from __future__ import annotations

import pytest

from signal_copier.broker.reconnect import compute_backoff_seconds


def test_compute_backoff_seconds_exponential_growth() -> None:
    """Attempt 0,1,2,3,4 → ~1,2,4,8,16 with jitter ±10%."""
    for attempt, expected in enumerate((1.0, 2.0, 4.0, 8.0, 16.0)):
        result = compute_backoff_seconds(attempt, base=1.0, cap=30.0, jitter=0.0)
        assert result == pytest.approx(expected, abs=0.01), (
            f"attempt={attempt} got {result}"
        )


def test_compute_backoff_seconds_caps_at_cap_arg() -> None:
    """Large attempts must not exceed the cap."""
    for attempt in (5, 10, 20, 100):
        assert compute_backoff_seconds(attempt, base=1.0, cap=30.0, jitter=0.0) == 30.0


def test_compute_backoff_seconds_within_jitter_range() -> None:
    """With jitter=0.1 the result must be within ±10% of the base value (before cap)."""
    for attempt, base_value in enumerate((1.0, 2.0, 4.0, 8.0)):
        for _ in range(20):  # sample to catch jitter randomness
            result = compute_backoff_seconds(attempt, base=1.0, cap=30.0, jitter=0.1)
            assert abs(result - base_value) <= base_value * 0.1 + 0.001, (
                f"attempt={attempt} result={result} base={base_value}"
            )
```

- [ ] **Step 2: Run the test, verify it fails**

Run:
```bash
uv run pytest tests/test_reconnect.py -v
```

Expected: `ModuleNotFoundError: No module named 'signal_copier.broker.reconnect'`

- [ ] **Step 3: Create `src/signal_copier/broker/reconnect.py`**

```python
"""MT5-flavored reconnect primitives (M13.2).

Provides:
  - compute_backoff_seconds(attempt, base, cap, jitter) — exponential backoff
  - with_retry(op, *, op_name, on_retry, on_exhausted, max_attempts)
    — async function-call helper that retries `op()` on BrokerAuthError /
    OSError with exponential backoff. Notifies via the supplied callback
    hooks between attempts.
"""
from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable

from signal_copier.broker.base import BrokerAuthError


def compute_backoff_seconds(
    attempt: int,
    *,
    base: float = 1.0,
    cap: float = 30.0,
    jitter: float = 0.1,
) -> float:
    """Return `min(base * 2**attempt, cap)` with ±jitter randomization.

    `attempt` is 0-based. `jitter` is a fraction (0.1 = ±10%).
    """
    raw = base * (2 ** attempt)
    capped = min(raw, cap)
    if jitter == 0.0:
        return capped
    import random
    delta = capped * jitter * (random.random() * 2 - 1)  # noqa: S311 — not crypto
    return max(0.0, capped + delta)


async def with_retry(
    op: Callable[[], Awaitable[None]],
    *,
    op_name: str,
    on_retry: Callable[..., Awaitable[None]] | None = None,
    on_exhausted: Callable[..., Awaitable[None]] | None = None,
    max_attempts: int = 5,
) -> None:
    """Call `await op()` up to `max_attempts` times with exponential backoff.

    Retries on `BrokerAuthError` and `OSError` (the latter catches MT5
    IPC socket drops). Other exceptions are re-raised immediately.

    On each retry: optionally awaits `on_retry(attempt, max_attempts,
    downtime_seconds, next_delay_seconds)`. On final exhaustion:
    optionally awaits `on_exhausted(attempts, total_downtime_seconds)`
    then raises `BrokerAuthError(f"{op_name} failed after {max_attempts} attempts")`.
    """
    downtime_start = time.monotonic()
    delay = 0.0
    last_exc: BaseException | None = None

    for attempt in range(max_attempts):
        try:
            await op()
            return  # success
        except (BrokerAuthError, OSError) as exc:
            last_exc = exc
            if attempt + 1 >= max_attempts:
                break
            delay = compute_backoff_seconds(attempt)
            if on_retry is not None:
                await on_retry(
                    attempt=attempt + 1,
                    max_attempts=max_attempts,
                    downtime_seconds=time.monotonic() - downtime_start,
                    next_delay_seconds=delay,
                )
            await asyncio.sleep(delay)

    total_downtime = time.monotonic() - downtime_start
    if on_exhausted is not None:
        await on_exhausted(
            attempts=max_attempts,
            total_downtime_seconds=total_downtime,
        )
    raise BrokerAuthError(
        f"{op_name} failed after {max_attempts} attempts: {last_exc}"
    )
```

- [ ] **Step 4: Run the tests, verify they pass**

Run:
```bash
uv run pytest tests/test_reconnect.py -v
```

Expected: 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/signal_copier/broker/reconnect.py tests/test_reconnect.py
git commit -m "feat(reconnect): add compute_backoff_seconds + with_retry helpers"
```

---

### Task 3: TDD `with_retry` async helper

**Files:**
- Modify: `tests/test_reconnect.py` (add 4 tests)
- Modify: `src/signal_copier/broker/reconnect.py` (already has stubs from Task 2; refine if needed)

The `with_retry` function was already implemented in Task 2 Step 3 (so the imports work). This task adds the behavioral tests.

- [ ] **Step 1: Append 4 tests to `tests/test_reconnect.py`**

```python
from unittest.mock import AsyncMock

from signal_copier.broker.base import BrokerAuthError


@pytest.mark.asyncio
async def test_with_retry_succeeds_first_try_no_callbacks_called() -> None:
    """If op succeeds immediately, no retry callbacks fire."""
    op = AsyncMock(return_value=None)
    on_retry = AsyncMock()
    on_exhausted = AsyncMock()

    await with_retry(op, op_name="test", on_retry=on_retry, on_exhausted=on_exhausted)

    op.assert_awaited_once()
    on_retry.assert_not_awaited()
    on_exhausted.assert_not_awaited()


@pytest.mark.asyncio
async def test_with_retry_retries_on_broker_auth_error_then_succeeds() -> None:
    """First two calls raise BrokerAuthError; third succeeds; on_retry fires 2x."""
    op = AsyncMock(side_effect=[BrokerAuthError("e1"), BrokerAuthError("e2"), None])
    on_retry = AsyncMock()
    on_exhausted = AsyncMock()

    await with_retry(op, op_name="mt5.initialize", on_retry=on_retry, on_exhausted=on_exhausted)

    assert op.await_count == 3
    assert on_retry.await_count == 2
    assert on_exhausted.await_count == 0


@pytest.mark.asyncio
async def test_with_retry_exhausts_then_raises_broker_auth_error() -> None:
    """Five consecutive failures → BrokerAuthError + on_exhausted called once."""
    op = AsyncMock(side_effect=[BrokerAuthError(f"e{i}") for i in range(5)])
    on_retry = AsyncMock()
    on_exhausted = AsyncMock()

    with pytest.raises(BrokerAuthError, match="mt5.initialize failed after 5 attempts"):
        await with_retry(
            op, op_name="mt5.initialize",
            on_retry=on_retry, on_exhausted=on_exhausted,
            max_attempts=5,
        )

    assert op.await_count == 5
    assert on_retry.await_count == 4
    on_exhausted.assert_awaited_once()
    on_exhausted.assert_awaited_with(attempts=5, total_downtime_seconds=pytest.approx(0.0, abs=5.0))


@pytest.mark.asyncio
async def test_with_retry_re_raises_non_retryable_exception_immediately() -> None:
    """ValueError (not BrokerAuthError/OSError) is re-raised without retry."""
    op = AsyncMock(side_effect=ValueError("boom"))
    on_retry = AsyncMock()
    on_exhausted = AsyncMock()

    with pytest.raises(ValueError, match="boom"):
        await with_retry(op, op_name="test", on_retry=on_retry, on_exhausted=on_exhausted)

    op.assert_awaited_once()
    on_retry.assert_not_awaited()
    on_exhausted.assert_not_awaited()
```

The `pytest.mark.asyncio` decorator is implicit because the project's `pyproject.toml` declares `asyncio_mode = "auto"` (verified by reading the file). However, to be safe, I'll add it explicitly. If your `pytest-asyncio` is configured with `asyncio_mode = "auto"`, the decorator is redundant but harmless.

- [ ] **Step 2: Run the new tests, verify all 4 pass**

Run:
```bash
uv run pytest tests/test_reconnect.py -v
```

Expected: 7 tests pass (3 from Task 2 + 4 from this task).

- [ ] **Step 3: If a test fails, update `src/signal_copier/broker/reconnect.py` to satisfy it**

The `with_retry` from Task 2 Step 3 should pass all 4 new tests. If any test fails, update the helper inline.

Common adjustments:
- If `on_exhausted.assert_awaited_with(attempts=5, ...)` fails on the kwargs, ensure your call is `await on_exhausted(attempts=N, total_downtime_seconds=T)` with keyword arguments.
- If `test_with_retry_retries_on_broker_auth_error_then_succeeds` complains about sleep timing, increase backoff base OR use `time.sleep` patching — but the test only checks `await_count`, not timing, so it should pass.

- [ ] **Step 4: Commit (only if Step 3 modified `reconnect.py`)**

```bash
git add src/signal_copier/broker/reconnect.py tests/test_reconnect.py
git commit -m "test(reconnect): add behavioral tests for with_retry helper"
```

If `reconnect.py` was unchanged in Step 3, this commit is a no-op; just add and commit only the test file:

```bash
git add tests/test_reconnect.py
git commit -m "test(reconnect): add behavioral tests for with_retry helper"
```

---

### Task 4: Replace `Mt5Broker` stub with the real implementation

**Files:**
- Replace: `src/signal_copier/broker/mt5.py` (M13.1 stub → M13.2 real)
- Delete: `tests/test_mt5_broker_stub.py`
- Create: `tests/test_mt5_broker.py` (NEW, replaces stub tests)

This is the load-bearing task. The new `Mt5Broker` is one class with all 5 Protocol methods. Tests use `monkeypatch.setattr` to mock the module-level `mt5linux as mt5` import.

- [ ] **Step 1: Create the scaffolding test file**

Create `tests/test_mt5_broker.py`:

```python
"""M13.2 tests for the real Mt5Broker impl.

All MT5 calls are mocked via `monkeypatch.setattr` on the module-level
`mt5linux as mt5` import in `signal_copier.broker.mt5`. No real
MetaTrader 5 terminal is required to run these.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from signal_copier.broker import Broker
from signal_copier.broker.dry_run import DryRunBroker
from signal_copier.broker.mt5 import Mt5Broker
from signal_copier.domain.signal import Signal


def _signal(**overrides: object) -> Signal:
    """Build a minimal Signal for place() tests."""
    defaults: dict[str, object] = dict(
        signal_id="test-mt5",
        pair="EUR/USD",
        direction="up",
        trigger_hhmm="10:00",
        expiration_seconds=300,
        received_at_unix=1_700_000_000.0,
        source_message_id=1,
        source_chat_id=1,
        raw_text="EUR/USD;10:00;CALL🟩",
        trigger_unix_initial=1_700_000_000.0,
        trigger_unix_gale1=1_700_000_300.0,
        trigger_unix_gale2=1_700_000_600.0,
    )
    defaults.update(overrides)
    return Signal(**defaults)  # type: ignore[arg-type]


def _broker() -> Mt5Broker:
    return Mt5Broker(
        login=12345678,
        password="dummy",
        server="VTMarkets-Demo",
        terminal_path=None,
        notifier=None,
    )


def test_mt5_broker_satisfies_protocol() -> None:
    assert isinstance(_broker(), Broker)
    assert isinstance(DryRunBroker(), Broker)


def _install_fake_mt5(
    monkeypatch: pytest.MonkeyPatch,
    *,
    initialize_returns: bool = True,
    init_error: tuple[int, str] | None = None,
    login_info_returns: tuple[str, str] | None = None,
    account_info_returns: object | None = None,
    symbol_info_returns: dict[str, object | None] | None = None,
    symbols_get_returns: list[SimpleNamespace] | None = None,
    order_send_returns: object | None = None,
    last_error_returns: tuple[int, str] | None = None,
) -> MagicMock:
    """Install a fake `mt5linux` module into `signal_copier.broker.mt5`.

    Returns the fake module so tests can adjust call_counts / side_effects.
    """
    fake_mt5 = MagicMock(name="mt5linux")

    fake_mt5.initialize.return_value = initialize_returns
    fake_mt5.last_error.return_value = init_error or (0, "")
    if login_info_returns is None:
        login_info_returns = ("12345678", "VTMarkets-Demo")
    fake_mt5.login_info.return_value = login_info_returns
    if account_info_returns is None:
        account_info_returns = SimpleNamespace(balance=10000.0, leverage=500, currency="USD")
    fake_mt5.account_info.return_value = account_info_returns

    if symbol_info_returns is None:
        symbol_info_returns = {"EURUSD-STD": SimpleNamespace(name="EURUSD-STD")}
    if symbol_info_returns:
        fake_mt5.symbol_info.side_effect = lambda name: symbol_info_returns.get(name)
    else:
        fake_mt5.symbol_info.return_value = None

    if symbols_get_returns is None:
        symbols_get_returns = []
    fake_mt5.symbols_get.return_value = symbols_get_returns

    if order_send_returns is None:
        # default: success, returns integer ticket 12345
        order_send_returns = SimpleNamespace(
            retcode=10009, comment="OK", order=12345,
        )
    fake_mt5.order_send.return_value = order_send_returns

    fake_mt5.last_error.return_value = last_error_returns or (-1, "n/a")

    # Constants used by Mt5Broker
    fake_mt5.ORDER_TYPE_BUY = 0
    fake_mt5.ORDER_TYPE_SELL = 1
    fake_mt5.TRADE_ACTION_DEAL = 1
    fake_mt5.ORDER_FILLING_IOC = 1

    fake_mt5.positions_get.return_value = []
    fake_mt5.Close.return_value = SimpleNamespace(retcode=10009, comment="OK")
    fake_mt5.shutdown.return_value = None

    monkeypatch.setattr("signal_copier.broker.mt5.mt5", fake_mt5)
    return fake_mt5
```

- [ ] **Step 2: Run the test file, verify it fails to import / fails all tests**

Run:
```bash
uv run pytest tests/test_mt5_broker.py -v
```

Expected: Many failures — `Mt5Broker` is still the M13.1 stub; the new tests don't exist yet but `test_mt5_broker_satisfies_protocol` should still pass (stub still implements Protocol); the rest will fail with errors. This is fine — Step 3 implements them.

- [ ] **Step 3: Replace `src/signal_copier/broker/mt5.py` with the real implementation**

```python
"""MT5 broker implementation (M13.2).

Connects to MT5 via the `mt5linux` drop-in client. Implements the Broker
Protocol. Account-specific: all pairs end with `-STD` (VT Markets STD
demo). Lots hardcoded by stage (docs/refactor.md §1.3).

`mt5linux` is imported at module scope so tests can mock it via
`monkeypatch.setattr`. The real package is a thin drop-in for the
official `MetaTrader5` PyPI package — same API surface.
"""
from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from typing import Any

import mt5linux as mt5

from signal_copier.broker.base import (
    BrokerAuthError,
    UnsupportedPairError,
)
from signal_copier.broker.reconnect import with_retry
from signal_copier.domain.gale import Stage
from signal_copier.domain.signal import Signal
from signal_copier.domain.state import StageResult

_log = logging.getLogger(__name__)

# --- Module-level constants ---

LOTS_BY_STAGE: dict[Stage, Decimal] = {
    "initial": Decimal("0.01"),
    "gale1":   Decimal("0.02"),
    "gale2":   Decimal("0.04"),
}

SYMBOL_SUFFIX: str = "-STD"  # VT Markets STD demo (per user account choice)

_POLL_INTERVAL_SEC: float = 0.25

# Pairs known at startup (pre-populated in `_load_symbol_cache` after connect).
_KNOWN_INPUT_PAIRS: tuple[str, ...] = (
    "EUR/JPY", "EUR/USD", "EUR/GBP", "GBP/USD", "GBP/JPY",
    "USD/JPY", "USD/CHF", "USD/CAD", "AUD/USD", "NZD/USD",
)

# MT5 retcodes we recognize (subset of MQL5 TRADE_RETCODE_*).
class _Retcode:
    OK = 10009
    NO_MONEY = 10018
    REQUOTE = 10004
    PRICE_CHANGED = 10019
    REJECT = 10006
    INVALID_PRICE = 10003


# --- Helper functions (testable in isolation) ---

def _resolve_symbol_name(
    input_pair: str,
    *,
    symbol_info_fn,
    symbols_get_fn,
) -> str | None:
    """Translate `EUR/USD` → `EURUSD-STD` (or None).

    Tries the canonical suffixed name first, then falls back to a
    prefix-match via `symbols_get`. Pure function (callers inject the
    MT5 functions for testability).
    """
    base = input_pair.replace("/", "")
    target = base + SYMBOL_SUFFIX
    if symbol_info_fn(target) is not None:
        return target
    matches = symbols_get_fn(f"*{base}*") or []
    if not matches:
        return None
    for s in matches:
        if getattr(s, "name", None) == target:
            return s.name
    return getattr(matches[0], "name", None)


# --- Mt5Broker class ---

class Mt5Broker:
    """Real MT5 broker. Satisfies Broker Protocol."""

    def __init__(
        self,
        *,
        login: int,
        password: str,
        server: str,
        terminal_path: str | None,
        notifier: object,
    ) -> None:
        self._login = login
        self._password = password
        self._server = server
        self._terminal_path = terminal_path
        self._notifier = notifier
        self._connected = False
        self._symbol_cache: dict[str, str] = {}
        self._last_known_profit: dict[str, Decimal] = {}
        self._start_of_day_balance: Decimal | None = None

    # -- connect / close --

    async def connect(self) -> None:
        async def _initialize() -> None:
            await asyncio.to_thread(self._sync_initialize)

        try:
            await with_retry(
                _initialize,
                op_name="mt5.initialize",
                on_retry=self._emit_reconnecting,
                on_exhausted=self._emit_reconnect_failed,
            )
        except BrokerAuthError as exc:
            raise
        self._connected = True
        self._cache_start_of_day_balance()
        self._load_symbol_cache()
        on_reconnected = getattr(self._notifier, "on_broker_reconnected", None)
        if on_reconnected is not None:
            await on_reconnected(
                attempts_used=1,
                total_downtime_seconds=0.0,
            )

    def _sync_initialize(self) -> None:
        ok = mt5.initialize(
            path=self._terminal_path,
            server=self._server,
            login=self._login,
            password=self._password,
        )
        if not ok:
            err = mt5.last_error()
            _log.warning(
                "mt5.initialize failed: login=%s server=%s last_error=%s",
                self._login, self._server, err,
            )
            raise BrokerAuthError(f"mt5.initialize failed: {err}")

    def _cache_start_of_day_balance(self) -> None:
        info = mt5.account_info()
        if info is None:
            _log.warning("mt5.account_info() returned None; daily_drawdown_pct falls back to USD threshold")
            return
        balance = getattr(info, "balance", None)
        if balance is None:
            _log.warning("mt5.account_info().balance is None; daily_drawdown_pct falls back")
            return
        self._start_of_day_balance = Decimal(str(balance))

    def _load_symbol_cache(self) -> None:
        for input_pair in _KNOWN_INPUT_PAIRS:
            resolved = _resolve_symbol_name(
                input_pair,
                symbol_info_fn=mt5.symbol_info,
                symbols_get_fn=mt5.symbols_get,
            )
            if resolved is not None:
                self._symbol_cache[input_pair] = resolved

    async def _emit_reconnecting(
        self, *, attempt: int, max_attempts: int,
        downtime_seconds: float, next_delay_seconds: float,
    ) -> None:
        on_reconnecting = getattr(self._notifier, "on_broker_reconnecting", None)
        if on_reconnecting is not None:
            await on_reconnecting(
                attempt=attempt, max_attempts=max_attempts,
                downtime_seconds=downtime_seconds, next_delay_seconds=next_delay_seconds,
            )

    async def _emit_reconnect_failed(
        self, *, attempts: int, total_downtime_seconds: float,
    ) -> None:
        on_exhausted = getattr(self._notifier, "on_broker_reconnect_failed", None)
        if on_exhausted is not None:
            await on_exhausted(
                attempts=attempts, total_downtime_seconds=total_downtime_seconds,
            )

    async def close(self) -> None:
        await asyncio.to_thread(mt5.shutdown)
        self._connected = False

    # -- place --

    async def place(
        self, signal: Signal, *, stage: Stage, amount: Decimal,  # noqa: ARG002 — amount ignored, lots keyed on stage
    ) -> str:
        lots = LOTS_BY_STAGE[stage]
        broker_symbol = self._symbol_cache.get(signal.pair)
        if broker_symbol is None:
            broker_symbol = _resolve_symbol_name(
                signal.pair,
                symbol_info_fn=mt5.symbol_info,
                symbols_get_fn=mt5.symbols_get,
            )
        if broker_symbol is None:
            raise UnsupportedPairError(
                f"MT5 symbol not found for {signal.pair} (tried {signal.pair.replace('/', '') + SYMBOL_SUFFIX})"
            )

        direction = mt5.ORDER_TYPE_BUY if signal.direction == "up" else mt5.ORDER_TYPE_SELL

        def _send() -> Any:
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": broker_symbol,
                "volume": float(lots),
                "type": direction,
                "magic": 0,
                "comment": f"signal-copier:{signal.signal_id}:{stage}",
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            result = mt5.order_send(request)
            if result is None:
                err = mt5.last_error()
                raise BrokerAuthError(f"mt5.order_send returned None: {err}")
            return result

        result = await asyncio.to_thread(_send)
        retcode = getattr(result, "retcode", 0)
        comment = getattr(result, "comment", "")
        if retcode != _Retcode.OK:
            _log.warning(
                "mt5.order_send non-OK: signal=%s stage=%s retcode=%s comment=%s",
                signal.signal_id, stage, retcode, comment,
            )
            if retcode in (_Retcode.NO_MONEY, _Retcode.PRICE_CHANGED):
                raise BrokerAuthError(
                    f"Insufficient funds for {stage}: retcode={retcode} comment={comment}"
                )
            if retcode == _Retcode.REJECT:
                raise UnsupportedPairError(
                    f"MT5 rejected order: retcode={retcode} comment={comment}"
                )
            raise BrokerAuthError(
                f"mt5.order_send failed: retcode={retcode} comment={comment}"
            )

        ticket = str(getattr(result, "order", ""))
        self._last_known_profit[ticket] = Decimal("0")  # overwritten on close
        return ticket

    # -- wait_result --

    async def wait_result(
        self, trade_id: str, *, timeout: float,
    ) -> StageResult:
        async def _poll_for_close() -> StageResult:
            while True:
                positions = await asyncio.to_thread(
                    mt5.positions_get, ticket=int(trade_id)
                )
                if not positions:  # position gone
                    profit = self._last_known_profit.get(trade_id, Decimal("0"))
                    if profit > 0:
                        return "win"
                    if profit < 0:
                        return "loss"
                    return "tie"
                await asyncio.sleep(_POLL_INTERVAL_SEC)

        try:
            return await asyncio.wait_for(_poll_for_close(), timeout=timeout)
        except TimeoutError:
            _log.warning(
                "Mt5Broker.wait_result timeout: trade_id=%s timeout=%.1fs",
                trade_id, timeout,
            )
            return "timeout"

    # -- close_position --

    async def close_position(
        self, trade_id: str, *, timeout: float,
    ) -> Decimal:
        def _close_and_read() -> tuple[Decimal, Any]:
            positions_before = mt5.positions_get(ticket=int(trade_id)) or []
            profit_before = Decimal("0")
            if positions_before:
                profit_before = Decimal(str(getattr(positions_before[0], "profit", 0)))
            result = mt5.Close(ticket=int(trade_id))
            return profit_before, result

        def _close() -> Any:
            profit_before, result = _close_and_read()
            retcode = getattr(result, "retcode", 0)
            comment = getattr(result, "comment", "")
            if retcode != _Retcode.OK:
                _log.warning(
                    "mt5.Close non-OK: trade_id=%s retcode=%s comment=%s",
                    trade_id, retcode, comment,
                )
            self._last_known_profit[trade_id] = profit_before
            return profit_before

        return await asyncio.wait_for(asyncio.to_thread(_close), timeout=timeout)
```

- [ ] **Step 4: Delete the M13.1 stub test file**

Run:
```bash
git rm tests/test_mt5_broker_stub.py
```

- [ ] **Step 5: Run the new test file, verify `test_mt5_broker_satisfies_protocol` passes**

Run:
```bash
uv run pytest tests/test_mt5_broker.py::test_mt5_broker_satisfies_protocol -v
```

Expected: passes — `Mt5Broker` has all 5 Protocol methods after the rewrite.

- [ ] **Step 6: Commit**

```bash
git add src/signal_copier/broker/mt5.py tests/test_mt5_broker.py tests/test_mt5_broker_stub.py
git commit -m "feat(broker): replace Mt5Broker stub with real mt5linux impl"
```

(The `tests/test_mt5_broker_stub.py` deletion is part of this commit since `git rm` staged it.)

---

### Task 5: TDD `Mt5Broker.connect` (happy path + retry exhaustion)

**Files:**
- Modify: `tests/test_mt5_broker.py` (add 3 tests)

- [ ] **Step 1: Append the connect tests to `tests/test_mt5_broker.py`**

```python
@pytest.mark.asyncio
async def test_mt5_broker_connect_succeeds_with_valid_init(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_mt5 = _install_fake_mt5(monkeypatch)
    broker = _broker()
    await broker.connect()
    assert broker._connected is True
    assert broker._start_of_day_balance == Decimal("10000.00")
    # verify symbol cache pre-population
    assert "EUR/USD" in broker._symbol_cache


@pytest.mark.asyncio
async def test_mt5_broker_connect_raises_broker_auth_error_on_init_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_mt5 = _install_fake_mt5(
        monkeypatch,
        initialize_returns=False,
        init_error=(-10005, "IPC: No IPC connection"),
    )
    broker = _broker()
    with pytest.raises(BrokerAuthError, match="mt5.initialize failed"):
        await broker.connect()
    # mt5.initialize called 5 times (max_attempts=5 default)
    assert fake_mt5.initialize.await_count == 5


@pytest.mark.asyncio
async def test_mt5_broker_connect_retries_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_mt5 = _install_fake_mt5(monkeypatch)
    fake_mt5.initialize.side_effect = [
        False,  # attempt 1 fails
        False,  # attempt 2 fails
        True,   # attempt 3 succeeds
    ]
    fake_mt5.last_error.return_value = (-10005, "transient")
    broker = _broker()
    await broker.connect()
    assert broker._connected is True
    assert fake_mt5.initialize.await_count == 3
```

- [ ] **Step 2: Run the new tests, verify all 3 pass**

Run:
```bash
uv run pytest tests/test_mt5_broker.py -v -k "connect"
```

Expected: 3 tests pass.

If `test_mt5_broker_connect_succeeds_with_valid_init` fails because `_load_symbol_cache` is called too early (before `mt5.symbol_info` is mock-installable), it means the test fixtures' monkeypatching isn't reaching the cache. In that case, run a single test in verbose mode for full diagnostics:

```bash
uv run pytest tests/test_mt5_broker.py::test_mt5_broker_connect_succeeds_with_valid_init -v
```

- [ ] **Step 3: If a test fails, update `Mt5Broker.connect` to satisfy it**

The expected behavior is already implemented in Task 4 Step 3. If any test fails here, update `connect()` inline.

Common adjustments:
- If `test_mt5_broker_connect_retries_then_succeeds` fails on the retry count: check that `with_retry` correctly passes through successes on non-exhausted attempts.
- If `test_mt5_broker_connect_raises_broker_auth_error_on_init_failure` fails on `last_error` content: ensure `_sync_initialize` reads `mt5.last_error()` AFTER each `mt5.initialize()` call.

- [ ] **Step 4: Commit (only if Step 3 modified `broker/mt5.py`)**

```bash
git add src/signal_copier/broker/mt5.py tests/test_mt5_broker.py
git commit -m "test(broker): add mocked tests for Mt5Broker.connect path"
```

---

### Task 6: TDD `Mt5Broker.place` (symbol resolution + retcode mapping)

**Files:**
- Modify: `tests/test_mt5_broker.py` (add 5 tests)

- [ ] **Step 1: Append the place tests to `tests/test_mt5_broker.py`**

```python
@pytest.mark.asyncio
async def test_mt5_broker_place_submits_market_order_with_lots_keyed_by_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_mt5 = _install_fake_mt5(monkeypatch)
    fake_mt5.symbol_info.side_effect = lambda name: (
        SimpleNamespace(name=name) if name == "EURUSD-STD" else None
    )
    broker = _broker()
    broker._symbol_cache["EUR/USD"] = "EURUSD-STD"
    ticket = await broker.place(_signal(direction="up"), stage="initial", amount=Decimal("2.00"))
    assert ticket == "12345"
    # Verify order_send was called with the right volume (0.01 for "initial")
    request = fake_mt5.order_send.await_args.args[0]
    assert request["volume"] == 0.01
    assert request["symbol"] == "EURUSD-STD"
    # Direction "up" → BUY → mt5.ORDER_TYPE_BUY which we set to 0 in _install_fake_mt5
    assert request["type"] == 0  # mt5.ORDER_TYPE_BUY


@pytest.mark.asyncio
async def test_mt5_broker_place_uses_gale_lots_not_amount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The `amount` Decimal arg is ignored — lots are keyed on stage."""
    fake_mt5 = _install_fake_mt5(monkeypatch)
    fake_mt5.symbol_info.side_effect = lambda name: (
        SimpleNamespace(name=name) if name == "EURUSD-STD" else None
    )
    broker = _broker()
    broker._symbol_cache["EUR/USD"] = "EURUSD-STD"
    # Pass 9999 USD as amount; LOTS_BY_STAGE['gale2'] = 0.04 wins
    await broker.place(_signal(), stage="gale2", amount=Decimal("9999.00"))
    request = fake_mt5.order_send.await_args.args[0]
    assert request["volume"] == 0.04


@pytest.mark.asyncio
async def test_mt5_broker_place_raises_unsupported_pair_when_symbol_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_mt5 = _install_fake_mt5(monkeypatch, symbol_info_returns={}, symbols_get_returns=[])
    broker = _broker()
    with pytest.raises(UnsupportedPairError, match="MT5 symbol not found"):
        await broker.place(_signal(pair="ZZZ/QQQ"), stage="initial", amount=Decimal("2.00"))


@pytest.mark.asyncio
async def test_mt5_broker_place_raises_broker_auth_error_on_no_money(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_mt5 = _install_fake_mt5(monkeypatch)
    fake_mt5.symbol_info.side_effect = lambda name: (
        SimpleNamespace(name=name) if name == "EURUSD-STD" else None
    )
    fake_mt5.order_send.return_value = SimpleNamespace(
        retcode=10018, comment="no money", order=0,
    )
    broker = _broker()
    broker._symbol_cache["EUR/USD"] = "EURUSD-STD"
    with pytest.raises(BrokerAuthError, match="Insufficient funds"):
        await broker.place(_signal(), stage="initial", amount=Decimal("2.00"))


@pytest.mark.asyncio
async def test_mt5_broker_place_raises_unsupported_pair_error_on_reject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_mt5 = _install_fake_mt5(monkeypatch)
    fake_mt5.symbol_info.side_effect = lambda name: (
        SimpleNamespace(name=name) if name == "EURUSD-STD" else None
    )
    fake_mt5.order_send.return_value = SimpleNamespace(
        retcode=10006, comment="rejected by server", order=0,
    )
    broker = _broker()
    broker._symbol_cache["EUR/USD"] = "EURUSD-STD"
    with pytest.raises(UnsupportedPairError, match="rejected by server"):
        await broker.place(_signal(), stage="initial", amount=Decimal("2.00"))
```

- [ ] **Step 2: Run the new tests, verify all 5 pass**

Run:
```bash
uv run pytest tests/test_mt5_broker.py -v -k "place"
```

Expected: 5 tests pass.

If `test_mt5_broker_place_uses_gale_lots_not_amount` fails on volume not being 0.04, check that `LOTS_BY_STAGE["gale2"]` is `Decimal("0.04")` (the cast `float(Decimal("0.04")) == 0.04` is exact).

- [ ] **Step 3: If a test fails, update `Mt5Broker.place` to satisfy it**

The expected behavior is already implemented in Task 4 Step 3. Adjust inline if needed.

- [ ] **Step 4: Commit (only if Step 3 modified code)**

```bash
git add src/signal_copier/broker/mt5.py tests/test_mt5_broker.py
git commit -m "test(broker): add mocked tests for Mt5Broker.place (lots + retcode)"
```

---

### Task 7: TDD `Mt5Broker.wait_result` (poll → win/loss/tie) + `Mt5Broker.close` (shutdown)

**Files:**
- Modify: `tests/test_mt5_broker.py` (add 5 tests)

- [ ] **Step 1: Append the wait_result + close tests to `tests/test_mt5_broker.py`**

```python
@pytest.mark.asyncio
async def test_mt5_broker_wait_result_returns_win_when_position_closed_positive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_mt5 = _install_fake_mt5(monkeypatch)
    broker = _broker()
    broker._last_known_profit["12345"] = Decimal("11.00")
    fake_mt5.positions_get.return_value = []  # position gone

    result = await broker.wait_result("12345", timeout=5.0)
    assert result == "win"


@pytest.mark.asyncio
async def test_mt5_broker_wait_result_returns_loss_when_position_closed_negative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_mt5 = _install_fake_mt5(monkeypatch)
    broker = _broker()
    broker._last_known_profit["12345"] = Decimal("-2.50")
    fake_mt5.positions_get.return_value = []

    result = await broker.wait_result("12345", timeout=5.0)
    assert result == "loss"


@pytest.mark.asyncio
async def test_mt5_broker_wait_result_returns_timeout_on_wait_for_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_mt5 = _install_fake_mt5(monkeypatch)
    # Position remains open: positions_get always returns a list with one element
    fake_mt5.positions_get.return_value = [SimpleNamespace(ticket=12345)]
    broker = _broker()

    # Use a tiny timeout so the wait_for fails fast
    result = await broker.wait_result("12345", timeout=0.1)
    assert result == "timeout"


@pytest.mark.asyncio
async def test_mt5_broker_close_position_returns_decimal_profit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_mt5 = _install_fake_mt5(monkeypatch)
    fake_mt5.positions_get.return_value = [SimpleNamespace(profit=7.50)]
    broker = _broker()

    profit = await broker.close_position("12345", timeout=5.0)
    assert profit == Decimal("7.50")
    assert broker._last_known_profit["12345"] == Decimal("7.50")
    fake_mt5.Close.assert_awaited_once()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_mt5_broker_close_calls_mt5_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_mt5 = _install_fake_mt5(monkeypatch)
    broker = _broker()
    await broker.close()
    await broker.close()  # idempotent: no error on second call
    assert fake_mt5.shutdown.await_count == 2  # type: ignore[attr-defined]
```

- [ ] **Step 2: Run the new tests, verify all 5 pass**

Run:
```bash
uv run pytest tests/test_mt5_broker.py -v -k "wait_result or close"
```

Expected: 5 tests pass.

If `test_mt5_broker_wait_result_returns_timeout_on_wait_for_cancellation` is flaky (sometimes returns instantly because polling iterates faster than 0.1s), add a small `await asyncio.sleep(0)` at the top of `_poll_for_close` to yield. Or run the test a few times to confirm it consistently passes.

- [ ] **Step 3: If a test fails, update `Mt5Broker.wait_result` or `Mt5Broker.close` to satisfy it**

The expected behavior is already implemented in Task 4 Step 3. Adjust inline if needed.

- [ ] **Step 4: Run the full broker test file**

```bash
uv run pytest tests/test_mt5_broker.py -v
```

Expected: All tests pass (1 isinstance + 3 connect + 5 place + 5 wait_result/close = 14 total).

- [ ] **Step 5: Commit (only if Step 3 modified code)**

```bash
git add src/signal_copier/broker/mt5.py tests/test_mt5_broker.py
git commit -m "test(broker): add mocked tests for wait_result + close_position + close"
```

---

### Task 8: Final Commit 1 verification (full test suite + lint + mypy)

**Files:**
- Modify: none (verification only)

- [ ] **Step 1: Run the FULL test suite**

```bash
uv run pytest tests/ -q
```

Expected: all in-scope tests green. The pre-existing test_db.py Docker errors and test_auth.py env-related failure are documented as out-of-scope per M13.1's final review.

- [ ] **Step 2: Run ruff**

```bash
uv run ruff check src/signal_copier/broker/ tests/test_mt5_broker.py tests/test_reconnect.py
```

Expected: clean.

- [ ] **Step 3: Run mypy on the new modules**

```bash
uv run mypy src/signal_copier/broker/mt5.py src/signal_copier/broker/reconnect.py
```

Expected: clean (the `mt5linux` package lacks py.typed markers; use `[[tool.mypy.overrides]] module = ["mt5linux"] ignore_missing_imports = true` in `pyproject.toml` if mypy errors on `import mt5linux`).

If mypy fails on `import mt5linux`, add this to `pyproject.toml` in the `[tool.mypy.overrides]]` section:

```toml
[[tool.mypy.overrides]]
# mt5linux is a drop-in for MetaTrader5; no py.typed marker. Wrapper at
# src/signal_copier/broker/mt5.py validates return shapes inline.
module = [
    "mt5linux",
]
ignore_missing_imports = true
```

Then run `uv run mypy src/signal_copier/broker/mt5.py` again. Expected: clean.

- [ ] **Step 4: If pre-commit hook fixes anything, commit the fixes**

```bash
git add pyproject.toml src/signal_copier/broker/mt5.py tests/test_mt5_broker.py
git commit -m "style(broker): pre-commit fixes from M13.2 verification"
```

If nothing changed, skip this commit.

- [ ] **Step 5: Verify the in-scope `git grep` for residual NotImplementedError / M13.1 stub traces**

```bash
git grep -n 'NotImplementedError' src/signal_copier/broker/mt5.py
git grep -n 'lands in M13' src/signal_copier/broker/mt5.py
```

Expected: both return nothing. The stub is fully replaced; no M13.*-forward references remain.

- [ ] **Step 6: Final verification — the manual pre-deploy path**

This is a manual check (not in CI):
```bash
uv run python -m tools.mt5_preflight  # exits 0 against real MT5 (manual only)
```

This will exit with code 1 (FileNotFoundError) because the tool doesn't exist yet — that's expected. **Task 9** ships the preflight.

**Commit 1 is now complete.** The real `Mt5Broker` impl + reconnect helper + mt5linux dep are all on `main`.

---

### Task 9: TDD `tools.mt5_preflight.run_preflight` (happy path)

**Files:**
- Create: `tools/mt5_preflight.py`
- Create: `tests/test_mt5_preflight.py`

- [ ] **Step 1: Write the failing test for the happy path**

Create `tests/test_mt5_preflight.py`:

```python
"""Tests for tools.mt5_preflight.

All MT5 calls are mocked via monkeypatch.setattr.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import tools.mt5_preflight as preflight


def test_preflight_prints_pass_on_successful_init(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Happy path: all 4 steps succeed → exit 0 + PASS in stdout."""
    monkeypatch.setenv("MT5_LOGIN", "12345678")
    monkeypatch.setenv("MT5_PASSWORD", "secret")
    monkeypatch.setenv("MT5_SERVER", "VTMarkets-Demo")
    monkeypatch.delenv("MT5_TERMINAL_PATH", raising=False)

    fake_mt5 = MagicMock()
    fake_mt5.initialize.return_value = True
    fake_mt5.login_info.return_value = ("12345678", "VTMarkets-Demo-STD")
    fake_mt5.account_info.return_value = SimpleNamespace(
        balance=10000.0, leverage=500, currency="USD",
    )
    fake_mt5.symbols_get.return_value = [SimpleNamespace(name="EURUSD-STD")]

    monkeypatch.setattr(preflight, "mt5", fake_mt5)

    rc = preflight.run_preflight()
    assert rc == 0
    captured = capsys.readouterr()
    assert "[OK] mt5.initialize" in captured.out
    assert "[OK] mt5.account_info" in captured.out
    assert "PASS" in captured.out
```

Note: the `monkeypatch.setattr(preflight, "mt5", fake_mt5)` requires that `tools/mt5_preflight.py` imports `mt5linux as mt5` at module scope (Task 9 Step 4 implements that).

- [ ] **Step 2: Run the test, verify it fails**

Run:
```bash
uv run pytest tests/test_mt5_preflight.py -v
```

Expected: `ModuleNotFoundError: No module named 'tools.mt5_preflight'`

- [ ] **Step 3: Write the minimal `tools/mt5_preflight.py` to make the happy-path test pass**

Create `tools/mt5_preflight.py`:

```python
"""M13.2 mt5_preflight — sanity check before live deploy.

Runs through:
  1. Read MT5_* env vars (after load_dotenv; no pydantic Config here)
  2. mt5.initialize() → connect
  3. mt5.login_info() + mt5.account_info() → snapshot
  4. mt5.symbols_get(group="*STD*") → asset-map probe
  5. mt5.shutdown()

Prints PASS/FAIL summary. Exits 0 on success, 1 on any MT5 error.

Run:    uv run python -m tools.mt5_preflight
"""
from __future__ import annotations

import os
import sys

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover — dotenv is in dev deps but not always available
    load_dotenv = None  # type: ignore[assignment]

import mt5linux as mt5

SYMBOL_SUFFIX = "-STD"  # duplicated from broker/mt5.py — local config match


def _read_required_env(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        return ""
    return value


def run_preflight() -> int:
    """Execute the preflight checks; return 0 (PASS) or 1 (FAIL)."""
    if load_dotenv is not None:
        load_dotenv()
    login = _read_required_env("MT5_LOGIN")
    password = _read_required_env("MT5_PASSWORD")
    server = _read_required_env("MT5_SERVER")
    if not login or not password or not server:
        print("[FAIL] Missing MT5_* env vars. Set MT5_LOGIN, MT5_PASSWORD, MT5_SERVER in .env.")
        return 1

    try:
        ok = mt5.initialize(
            path=os.environ.get("MT5_TERMINAL_PATH"),
            server=server,
            login=int(login),
            password=password,
        )
        if not ok:
            err = mt5.last_error()
            print(f"[FAIL] mt5.initialize → login error: {err}")
            print("       Hint: Is the MT5 terminal running with the configured server?")
            return 1
        print("[OK] mt5.initialize      → MT5 terminal reachable")

        login_info = mt5.login_info()
        print(f"[OK] mt5.login_info      → user={login_info[0]} server={login_info[1]}")

        acct = mt5.account_info()
        if acct is None:
            print("[FAIL] mt5.account_info → returned None")
            return 1
        balance = getattr(acct, "balance", None)
        leverage = getattr(acct, "leverage", None)
        currency = getattr(acct, "currency", "?")
        if balance is None:
            print("[WARN] mt5.account_info.balance is None; balance printed as ?")
            balance_str = "?"
        else:
            balance_str = f"{balance:.2f}"
        print(
            f"[OK] mt5.account_info    → balance={balance_str} "
            f"leverage=1:{leverage} currency={currency}"
        )

        symbols = mt5.symbols_get(f"*{SYMBOL_SUFFIX}*")
        n = len(symbols) if symbols else 0
        print(f"[OK] mt5.symbols_get     → {n} tradeable symbols ({SYMBOL_SUFFIX.strip('-')}-named)")

        return 0
    except Exception as exc:
        print(f"[FAIL] Unexpected error: {type(exc).__name__}: {exc}")
        return 1
    finally:
        try:
            mt5.shutdown()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(run_preflight())
```

- [ ] **Step 4: Run the happy-path test, verify it passes**

Run:
```bash
uv run pytest tests/test_mt5_preflight.py::test_preflight_prints_pass_on_successful_init -v
```

Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add tools/mt5_preflight.py tests/test_mt5_preflight.py
git commit -m "feat(tools): add mt5_preflight happy path"
```

---

### Task 10: TDD `mt5_preflight` failure paths

**Files:**
- Modify: `tests/test_mt5_preflight.py` (add 3 tests)

- [ ] **Step 1: Append 3 failure-path tests to `tests/test_mt5_preflight.py`**

```python
def test_preflight_exits_1_on_init_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("MT5_LOGIN", "12345678")
    monkeypatch.setenv("MT5_PASSWORD", "secret")
    monkeypatch.setenv("MT5_SERVER", "VTMarkets-Demo")

    fake_mt5 = MagicMock()
    fake_mt5.initialize.return_value = False
    fake_mt5.last_error.return_value = (-10005, "IPC: No IPC connection")

    monkeypatch.setattr(preflight, "mt5", fake_mt5)

    rc = preflight.run_preflight()
    assert rc == 1
    captured = capsys.readouterr()
    assert "[FAIL] mt5.initialize" in captured.out
    assert "IPC: No IPC connection" in captured.out


def test_preflight_exits_1_when_credentials_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("MT5_LOGIN", raising=False)
    monkeypatch.delenv("MT5_PASSWORD", raising=False)
    monkeypatch.delenv("MT5_SERVER", raising=False)

    rc = preflight.run_preflight()
    assert rc == 1
    captured = capsys.readouterr()
    assert "MT5_LOGIN" in captured.out
    assert "MT5_PASSWORD" in captured.out


def test_preflight_handles_degraded_account_info_gracefully(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """account_info() returns object with balance=None → preflight doesn't crash, prints WARN."""
    monkeypatch.setenv("MT5_LOGIN", "12345678")
    monkeypatch.setenv("MT5_PASSWORD", "secret")
    monkeypatch.setenv("MT5_SERVER", "VTMarkets-Demo")

    fake_mt5 = MagicMock()
    fake_mt5.initialize.return_value = True
    fake_mt5.login_info.return_value = ("12345678", "VTMarkets-Demo-STD")
    fake_mt5.account_info.return_value = SimpleNamespace(balance=None, leverage=500, currency="USD")
    fake_mt5.symbols_get.return_value = []

    monkeypatch.setattr(preflight, "mt5", fake_mt5)

    rc = preflight.run_preflight()
    assert rc == 0
    captured = capsys.readouterr()
    assert "balance=?" in captured.out
```

- [ ] **Step 2: Run all 4 tests, verify all pass**

Run:
```bash
uv run pytest tests/test_mt5_preflight.py -v
```

Expected: 4 tests pass.

If `test_preflight_exits_1_when_credentials_missing` fails, ensure `run_preflight()` reads env AFTER `load_dotenv` (so the test can set them via `monkeypatch.setenv`).

- [ ] **Step 3: Commit**

```bash
git add tests/test_mt5_preflight.py
git commit -m "test(tools): add failure-path tests for mt5_preflight"
```

---

### Task 11: Final Commit 2 verification + manual smoke

**Files:**
- Modify: none (verification only)

- [ ] **Step 1: Run the FULL test suite**

```bash
uv run pytest tests/ -q
```

Expected: all in-scope tests green. Same pre-existing failures (test_db.py Docker, test_auth.py env) remain.

- [ ] **Step 2: Run ruff on all changed files**

```bash
uv run ruff check src/signal_copier/broker/mt5.py tools/mt5_preflight.py tests/test_mt5_broker.py tests/test_reconnect.py tests/test_mt5_preflight.py
```

Expected: clean.

- [ ] **Step 3: Run mypy on M13.2 modules**

```bash
uv run mypy src/signal_copier/broker/mt5.py src/signal_copier/broker/reconnect.py
```

Expected: clean.

- [ ] **Step 4: Run pre-commit hook on all changed files (if configured)**

```bash
uv run pre-commit run --files src/signal_copier/broker/mt5.py src/signal_copier/broker/reconnect.py tools/mt5_preflight.py tests/test_mt5_broker.py tests/test_reconnect.py tests/test_mt5_preflight.py pyproject.toml
```

If pre-commit hook reformats anything, commit the fixes:
```bash
git add -u
git commit -m "style(broker): pre-commit fixes from M13.2 final verification"
```

- [ ] **Step 5: Verify the implementation matches the spec §1 success criteria**

Check the spec's 10 success criteria:

```bash
# (1) Tests green — verified in Step 1.
# (2) Preflight exits 0 — verifiable only against real MT5. Manually run:
#     uv run python -m tools.mt5_preflight     # expected: exit 0 + account info printed
# (3-6) Protocol methods — covered by tests/test_mt5_broker.py
# (7) Reconnect — covered by tests/test_reconnect.py (compute_backoff_seconds + with_retry)
# (8) Logging — verified by reading broker/mt5.py source for `_log.warning` calls
# (9) mt5linux in pyproject.toml — verify via grep
grep "mt5linux" pyproject.toml
# Expected: 1 line ("mt5linux>=1.0.0",  # M13.2: MT5 client (drop-in for MetaTrader5))
# (10) No new exception class — verify
git diff e10c541..main -- src/signal_copier/broker/base.py
# Expected: empty — no new exceptions added in M13.2
```

- [ ] **Step 6: M13.2 is complete**

Two commits now on `main`:
```
<sha>  chore(tools): pre-flight + failure path tests (M13.2 commit 2)
<sha>  test(tools): add failure-path tests for mt5_preflight
<sha>  feat(tools): add mt5_preflight happy path
<sha>  test(broker): add mocked tests for wait_result + close_position + close
... (intermediate test commits from Tasks 5/6/7)
<sha>  test(broker): add mocked tests for Mt5Broker.place (lots + retcode)
<sha>  test(broker): add mocked tests for Mt5Broker.connect path
<sha>  feat(broker): replace Mt5Broker stub with real mt5linux impl
<sha>  feat(reconnect): add compute_backoff_seconds + with_retry helpers
<sha>  build(deps): add mt5linux>=1.0.0 for M13.2 MT5 broker impl
```

---

## Final verification (after Task 11)

```bash
# 1. Full test suite green
uv run pytest tests/ -q

# 2. Lint + type checks clean
uv run ruff check src/ tools/ tests/
uv run mypy src/signal_copier/

# 3. No NotImplementedError or M13.*-forward-references in mt5.py
git grep -n 'NotImplementedError\|lands in M' src/signal_copier/broker/mt5.py

# 4. New constants present where expected
grep "SYMBOL_SUFFIX\|LOTS_BY_STAGE\|_POLL_INTERVAL_SEC" src/signal_copier/broker/mt5.py

# 5. Module-level mt5linux import (so monkeypatch.setattr works in tests)
grep "^import mt5linux\|^from mt5linux" src/signal_copier/broker/mt5.py

# 6. Reconnect helpers exist with the right signatures
grep "def compute_backoff_seconds\|async def with_retry" src/signal_copier/broker/reconnect.py

# 7. Preflight exits 0 against real MT5 (MANUAL CHECK — requires MT5 terminal up)
uv run python -m tools.mt5_preflight
```

All 7 must succeed (item 7 manually, against the user's MT5 demo account).

---

## Acceptance checklist

- [ ] Commit: `build(deps): add mt5linux>=1.0.0 for M13.2 MT5 broker impl`
- [ ] Commit: `feat(reconnect): add compute_backoff_seconds + with_retry helpers`
- [ ] Commit: `feat(broker): replace Mt5Broker stub with real mt5linux impl`
- [ ] Commit(s): `test(broker): add mocked tests for connect/place/wait_result/close…`
- [ ] Commit: `feat(tools): add mt5_preflight`
- [ ] Commit(s): `test(tools): add failure-path tests for mt5_preflight`
- [ ] `pytest tests/` is green (with documented pre-existing `test_db.py` Docker errors + `test_auth.py` env-related failure)
- [ ] `python -m tools.mt5_preflight` exits 0 against real MT5 with user's VT Markets demo creds
- [ ] No `NotImplementedError` or `lands in M*` forward-references remain in `src/signal_copier/broker/mt5.py`
- [ ] `LOTS_BY_STAGE`, `SYMBOL_SUFFIX`, `_POLL_INTERVAL_SEC` constants present
- [ ] `mt5linux as mt5` imported at module top of `broker/mt5.py` (testable via `monkeypatch.setattr`)

---

*End of M13.2 implementation plan.*
