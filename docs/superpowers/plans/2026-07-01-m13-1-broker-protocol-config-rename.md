# M13.1 Broker Protocol + Config Rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the structural MT5 broker swap — additive `close_position` Protocol method, OLYMP_*→MT5_* config migration, stub `Mt5Broker` class, and absorb the M13.4 `on_olymp_*`→`on_broker_*` notifier rename — without changing `DRY_RUN=true` runtime behavior.

**Architecture:** Two commits. Commit 1 = broker Protocol + dry-run stub + config migration + entry-point wiring + package metadata; success criterion `pytest tests/` green + `DRY_RUN=true` boot unchanged. Commit 2 = cosmetic notifier rename (Protocol + NoOp + TelegramDM + test files). All Protocol changes are additive; all renames are mechanical.

**Tech Stack:** Python 3.13, pydantic-settings, asyncio, pytest, pytest-asyncio (asyncio_mode="auto").

---

## File structure

### Created (1 source file + 1 test file)
- `src/signal_copier/broker/mt5.py` — `Mt5Broker` stub class satisfying `Broker` Protocol; every method raises `NotImplementedError("…lands in M13.2; set DRY_RUN=true.")`
- `tests/test_mt5_broker_stub.py` — tests asserting `Mt5Broker()` satisfies Protocol and every public method raises `NotImplementedError`

### Modified — Commit 1
- `src/signal_copier/broker/base.py` — append `close_position` method to `Broker` Protocol
- `src/signal_copier/broker/dry_run.py` — append `close_position` no-op returning `Decimal(0)`
- `src/signal_copier/config.py` — drop `olymp_*` fields + 2 validators; add `mt5_*` fields + `_validate_demo_server` validator (allow-empty)
- `src/signal_copier/__main__.py` — add `Mt5Broker` import + replace validation block (49-56) + mirror refactor.md §4.7 broker selection block (95-111) + update `BrokerAuthError` handler message
- `src/signal_copier/__init__.py` — update docstring; add `__version__ = "0.2.0"`; expand `__all__`
- `pyproject.toml` — version 0.2.0; new description; drop `websockets` dep; drop `src/olymptrade_ws` from ruff/mypy/pytest excludes
- `tests/test_config.py` — rewrite 5 `olymp_account_group` tests per spec §3.8

### Modified — Commit 2
- `src/signal_copier/notify/protocol.py` — rename 4 Protocol methods + 4 `NoOpNotifier` methods + 4 log keys; update docstrings M8/M10 → M13+/M13.2
- `src/signal_copier/notify/telegram_dm.py` — rename 4 methods + swap DM text per refactor.md §4.8
- `tests/_scheduler_fixtures.py` — rename `on_olymp_*` (4 methods + 4 string keys) to `on_broker_*`
- `tests/test_notifier.py` — rename 4 test functions + rename `NoOpNotifier().on_olymp_*()` calls + rename `"event=olymp_*"` log-key assertions
- `tests/test_recording_notifier_protocol.py` — rename 4 string literals in the events list
- `tests/test_telegram_dm.py` — rename 4 test functions + rename method calls + update DM text assertions

### Untouched (out of M13.1 scope)
- `domain/state.py`, `scheduler/trigger.py` — M13.5 owns PnL rework
- `.env.example`, `README.md`, `docs/PRD.md` — M13.5 owns doc sweep
- `tests/test_auth.py`'s 21 `OLYMP_*` env literals — M13.5 cosmetic cleanup

---

## Tasks

### Task 1: Add `close_position` to `Broker` Protocol + `DryRunBroker`

**Files:**
- Modify: `src/signal_copier/broker/base.py:73-89` (append after `wait_result`)
- Modify: `src/signal_copier/broker/dry_run.py:80-98` (append after `wait_result`)
- Test: `tests/test_dry_run_broker.py` (add new test function at end)

- [ ] **Step 1: Write the failing test for `DryRunBroker.close_position`**

Append to `tests/test_dry_run_broker.py`:

```python
async def test_close_position_returns_decimal_zero() -> None:
    """M13.1: DryRunBroker.close_position is a no-op returning Decimal(0).

    Per docs/refactor.md §4.4: legacy/OlympTrade-style implementations treat
    this as a no-op since binary options close themselves before
    wait_result returns. Real MT5 impl (M13.2) will return position.profit.
    """
    broker = DryRunBroker()
    result = await broker.close_position("dryrun-abc123-initial-deadbeef", timeout=5.0)
    assert result == Decimal("0")
```

- [ ] **Step 2: Run the test, verify it fails**

Run from project root:
```bash
uv run pytest tests/test_dry_run_broker.py::test_close_position_returns_decimal_zero -v
```

Expected output: `AttributeError: 'DryRunBroker' object has no attribute 'close_position'`

- [ ] **Step 3: Implement `close_position` on `DryRunBroker`**

In `src/signal_copier/broker/dry_run.py`, after the `wait_result` method (around line 99), append:

```python
    async def close_position(
        self,
        trade_id: str,
        *,
        timeout: float,  # noqa: ARG002 — dry-run ignores timeout (D-7)
    ) -> Decimal:
        _log.info("DRY-RUN close_position: trade_id=%s (instant, Decimal(0))", trade_id)
        return Decimal("0")
```

(The `Decimal` symbol is already imported at the top of the file; verify line 5 is `from decimal import Decimal`.)

- [ ] **Step 4: Run the test, verify it passes**

Run:
```bash
uv run pytest tests/test_dry_run_broker.py::test_close_position_returns_decimal_zero -v
```

Expected: `1 passed`.

- [ ] **Step 5: Add `close_position` method to the `Broker` Protocol**

In `src/signal_copier/broker/base.py`, after the `wait_result` method (after the docstring closing at line 87), append:

```python
    async def close_position(
        self,
        trade_id: str,
        *,
        timeout: float,
    ) -> Decimal:
        """Close an open position identified by `trade_id`, returning realized PnL.

        Added in M13.1 (docs/refactor.md §4.4). Symmetric counterpart to
        OlympTrade's built-in expiration: OlympTrade closes the position
        itself before wait_result returns, so legacy implementations treat
        this as a no-op returning Decimal(0). Real MT5 impl (M13.2) blocks
        on the close-fill event, then reads `position.profit` from the
        broker — never approximate.

        Scheduler will call this in M13.5 (docs/refactor.md §4.4 step e),
        overriding `domain/state.py:_stage_pnl` with the broker-reported
        value. For M13.1 no caller exists; the method is added so
        `@runtime_checkable` isinstance checks pass without AttributeError.
        """
```

(The class body indentation matches surrounding methods — 4 spaces inside the `@runtime_checkable` class `Broker`.)

- [ ] **Step 6: Verify existing Protocol-compliance tests still pass**

Run:
```bash
uv run pytest tests/test_broker_protocol.py -v
```

Expected: all existing tests pass, including `test_dry_run_broker_satisfies_protocol` (which calls `isinstance(DryRunBroker(), Broker)`).

- [ ] **Step 7: Commit**

```bash
git add src/signal_copier/broker/base.py src/signal_copier/broker/dry_run.py tests/test_dry_run_broker.py
git commit -m "feat(broker): add close_position to Broker Protocol + DryRunBroker"
```

---

### Task 2: Create `Mt5Broker` stub class

**Files:**
- Create: `src/signal_copier/broker/mt5.py`
- Test: `tests/test_mt5_broker_stub.py` (NEW)

- [ ] **Step 1: Write the failing test for `Mt5Broker` stub**

Create `tests/test_mt5_broker_stub.py`:

```python
"""M13.1 stub broker tests. M13.2 replaces stub with the real Mt5Broker."""

from __future__ import annotations

import logging
from decimal import Decimal

import pytest

from signal_copier.broker import Broker
from signal_copier.broker.mt5 import Mt5Broker
from signal_copier.broker.dry_run import DryRunBroker


def _broker() -> Mt5Broker:
    return Mt5Broker(
        login=12345678,
        password="dummy",
        server="VTMarkets-Demo",
        terminal_path=None,
        notifier=None,
    )


def test_mt5_broker_satisfies_protocol() -> None:
    """isinstance(Mt5Broker(), Broker) must be True so __main__ can wire it.

    Tests both the new Mt5Broker and the existing DryRunBroker
    to confirm no regression in Protocol coverage.
    """
    assert isinstance(_broker(), Broker)
    assert isinstance(DryRunBroker(), Broker)


async def test_mt5_broker_connect_raises_not_implemented() -> None:
    with pytest.raises(NotImplementedError, match="M13.2"):
        await _broker().connect()


async def test_mt5_broker_place_raises_not_implemented() -> None:
    from signal_copier.broker.mt5 import Mt5Broker  # noqa: F401

    with pytest.raises(NotImplementedError, match="M13.2"):
        await _broker().place(  # type: ignore[arg-type]
            signal=_make_signal(),  # type: ignore[arg-type]
            stage="initial",  # type: ignore[arg-type]
            amount=Decimal("0.01"),  # type: ignore[arg-type]
        )


async def test_mt5_broker_wait_result_raises_not_implemented() -> None:
    with pytest.raises(NotImplementedError, match="M13.2"):
        await _broker().wait_result("dummy-trade", timeout=5.0)


async def test_mt5_broker_close_position_raises_not_implemented() -> None:
    with pytest.raises(NotImplementedError, match="M13.2"):
        await _broker().close_position("dummy-trade", timeout=5.0)


async def test_mt5_broker_close_raises_not_implemented() -> None:
    with pytest.raises(NotImplementedError, match="M13.2"):
        await _broker().close()


def test_mt5_broker_init_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    """Stub logs at WARNING so misconfigured deployments are visible."""
    with caplog.at_level(logging.WARNING):
        _broker()
    assert any("stub class" in record.message for record in caplog.records)


def _make_signal() -> object:
    """Minimal Signal stub — used only to satisfy the place() signature."""
    from signal_copier.domain.signal import Signal

    return Signal(
        signal_id="test-mt5-stub",
        pair="EURUSD",
        direction="up",
        trigger_hhmm="10:00",
        expiration_seconds=300,
        received_at_unix=1_700_000_000.0,
        source_message_id=1,
        source_chat_id=1,
        raw_text="EURUSD;10:00;CALL🟩",
        trigger_unix_initial=1_700_000_000.0,
        trigger_unix_gale1=1_700_000_300.0,
        trigger_unix_gale2=1_700_000_600.0,
    )
```

- [ ] **Step 2: Run the test, verify it fails**

Run:
```bash
uv run pytest tests/test_mt5_broker_stub.py -v
```

Expected: `ModuleNotFoundError: No module named 'signal_copier.broker.mt5'`

- [ ] **Step 3: Create `src/signal_copier/broker/mt5.py`**

Create `src/signal_copier/broker/mt5.py`:

```python
"""MT5 broker (M13.2). M13.1 ships a stub so __main__.py can import Mt5Broker.

Real implementation lands in M13.2 (docs/refactor.md §4.3 + §4.5):
  - mt5.initialize() in asyncio.to_thread
  - place() via mt5.order_send()
  - wait_result() via mt5.positions_get + order poll
  - close_position() via mt5.Close() — returns position.profit (Decimal)
  - reconnect via broker/reconnect.py (M13.2)

Until M13.2, every method raises NotImplementedError so a DRY_RUN=false
boot fails fast with an explicit error rather than a half-built session.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from signal_copier.broker.base import (
    BrokerAuthError,
    UnsupportedPairError,
)
from signal_copier.domain.gale import Stage
from signal_copier.domain.signal import Signal
from signal_copier.domain.state import StageResult

_log = logging.getLogger(__name__)


class Mt5Broker:
    """M13.2 implementation. M13.1 ships a stub; see module docstring."""

    def __init__(
        self,
        *,
        login: int,
        password: str,
        server: str,
        terminal_path: str | None,
        notifier: object,  # Notifier — cyclic import avoidance; M13.2 narrows to Notifier
    ) -> None:
        self._login = login
        self._password = password
        self._server = server
        self._terminal_path = terminal_path
        self._notifier = notifier
        _log.warning(
            "Mt5Broker: stub class (M13.1). Real impl lands in M13.2. "
            "Do not deploy with DRY_RUN=false."
        )

    async def connect(self) -> None:
        raise NotImplementedError(
            "Mt5Broker.connect() lands in M13.2 (docs/refactor.md §4.3 + §5). "
            "Until then, set DRY_RUN=true."
        )

    async def place(
        self, signal: Signal, *, stage: Stage, amount: Decimal,
    ) -> str:
        raise NotImplementedError(
            "Mt5Broker.place() lands in M13.2. Until then, set DRY_RUN=true."
        )

    async def wait_result(
        self, trade_id: str, *, timeout: float,
    ) -> StageResult:
        raise NotImplementedError(
            "Mt5Broker.wait_result() lands in M13.2. Until then, set DRY_RUN=true."
        )

    async def close_position(
        self, trade_id: str, *, timeout: float,
    ) -> Decimal:
        raise NotImplementedError(
            "Mt5Broker.close_position() lands in M13.2. Until then, set DRY_RUN=true."
        )

    async def close(self) -> None:
        raise NotImplementedError(
            "Mt5Broker.close() lands in M13.2."
        )
```

- [ ] **Step 4: Run the tests, verify they pass**

Run:
```bash
uv run pytest tests/test_mt5_broker_stub.py -v
```

Expected: 7 tests pass (1 isinstance + 5 NotImplementedError + 1 log warning).

- [ ] **Step 5: Run the full test suite to confirm no regression**

Run:
```bash
uv run pytest tests/ -q
```

Expected: all green (no new failures). Existing broker Protocol + DryRunBroker tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/signal_copier/broker/mt5.py tests/test_mt5_broker_stub.py
git commit -m "feat(broker): add Mt5Broker stub class for M13.1 (real impl lands in M13.2)"
```

---

### Task 3: Migrate `Config` from `OLYMP_*` to `MT5_*` fields

**Files:**
- Modify: `src/signal_copier/config.py:31-34` (drop OLYMP_* fields), `:68-83` (drop 2 validators)
- Modify: `src/signal_copier/config.py` (add MT5_* fields + `_validate_demo_server`)
- Modify: `tests/test_config.py:38-85` (rewrite 5 tests per spec §3.8)

- [ ] **Step 1: Update `tests/test_config.py` to assert on `mt5_server` instead of `olymp_account_group`**

Replace lines 38-85 of `tests/test_config.py` with:

```python
def test_default_mt5_server_is_empty() -> None:
    """M13.1: empty default allows tests/.env files with no MT5_* to load.

    The runtime guard at __main__.py:49-56 catches missing creds when
    DRY_RUN=false; the validator's allow-empty short-circuit keeps
    pytest green until then.
    """
    assert _config().mt5_server == ""


def test_mt5_server_with_demo_substring_is_allowed() -> None:
    cfg = _config(mt5_server="VTMarkets-Demo")
    assert cfg.mt5_server == "VTMarkets-Demo"


def test_mt5_server_demo_substring_is_case_insensitive() -> None:
    cfg = _config(mt5_server="vtmarkets-DEMO")
    assert cfg.mt5_server == "vtmarkets-DEMO"


def test_mt5_server_non_demo_refuses() -> None:
    """FR-6.6 equivalent for MT5 (docs/refactor.md §4.6)."""
    with pytest.raises(ValidationError) as exc_info:
        _config(mt5_server="VTMarkets-Real01")
    assert "must contain 'demo'" in str(exc_info.value)


def test_mt5_server_demo_with_dry_run_false_is_allowed() -> None:
    """A demo server + DRY_RUN=false is the M13.2 deployment shape."""
    cfg = _config(mt5_server="VTMarkets-Demo", dry_run=False)
    assert cfg.mt5_server == "VTMarkets-Demo"
    assert cfg.dry_run is False
```

- [ ] **Step 2: Run the new tests, verify they all fail**

Run:
```bash
uv run pytest tests/test_config.py -v
```

Expected: 5 tests fail with `AttributeError: 'Config' object has no attribute 'mt5_server'` (the validator pass-through tests fail because there's no field).

- [ ] **Step 3: Drop `OLYMP_*` fields and 2 validators from `config.py`**

In `src/signal_copier/config.py`:

Remove lines 31-34 (the entire OlympTrade block):
```python
    # --- OlympTrade (not used by M2, declared for schema completeness) ----
    olymp_access_token: str = ""
    olymp_account_group: str = "demo"  # FR-6.6: must be "demo" for v1
    olymp_account_id: str = ""
```

Remove lines 68-83 (both validators and the blank line above):
```python
    @field_validator("olymp_account_group")
    @classmethod
    def _validate_account_group(cls, v: str) -> str:
        if v not in {"demo", "real"}:
            raise ValueError(f"olymp_account_group must be 'demo' or 'real', got {v!r}")
        return v

    @model_validator(mode="after")
    def _demo_only_guardrail(self) -> Config:
        # FR-6.6: refuse to start with real account + dry_run off.
        if self.olymp_account_group == "real" and not self.dry_run:
            raise ValueError(
                "Refusing to start: OLYMP_ACCOUNT_GROUP=real requires DRY_RUN=true. "
                "Real-money trading is a v2 feature, gated behind a 7-day clean demo soak test."
            )
        return self
```

- [ ] **Step 4: Add `MT5_*` fields + `_validate_demo_server` validator**

In `src/signal_copier/config.py`, after the Telegram block (after line 30, before the database URL block), insert:

```python
    # --- MT5 broker (M13 — replaces OLYMP_* block; docs/refactor.md §4.6) ----
    mt5_login: int = 0
    mt5_password: str = ""
    mt5_server: str = ""
    mt5_terminal_path: str | None = None
```

Where the `_validate_account_group` validator was (around line 68, after `_validate_timezone`), insert:

```python
    @field_validator("mt5_server")
    @classmethod
    def _validate_demo_server(cls, v: str) -> str:
        """FR-6.6 equivalent for MT5: refuse non-demo server.

        Empty string is allowed at config-load time (the runtime guard at
        __main__.py:49-56 catches missing MT5_* so existing tests/.env files
        stay green through M13.1). Non-empty values must contain 'demo'
        (case-insensitive substring) so a real-account login plus real
        server cannot start the bot.
        """
        if v == "":
            return v
        if "demo" not in v.lower():
            raise ValueError(
                f"mt5_server must contain 'demo' (case-insensitive); got {v!r}. "
                "Real-money trading is a v2 feature gated behind a clean demo soak test."
            )
        return v
```

If `model_validator` is no longer referenced anywhere in the file, remove it from the imports:
```python
from pydantic import Field, field_validator, model_validator
```
→ change to:
```python
from pydantic import Field, field_validator
```

(Leave `model_validator` in the imports if the file still uses it elsewhere — check before removing.)

- [ ] **Step 5: Run the tests, verify they pass**

Run:
```bash
uv run pytest tests/test_config.py -v
```

Expected: all 5 new `mt5_*` tests pass. Other existing config tests (defaults, timezone) still pass.

- [ ] **Step 6: Run the full test suite, verify no regression**

Run:
```bash
uv run pytest tests/ -q
```

Expected: all green. Some previously-failing tests (e.g., `test_account_group_real_with_dry_run_false_refuses_to_start` if it referenced `olymp_account_group="real"`) were already deleted in Step 1; the rest of the suite must compile against the new Config.

- [ ] **Step 7: Commit**

```bash
git add src/signal_copier/config.py tests/test_config.py
git commit -m "refactor(config): migrate OLYMP_* fields to MT5_*; add _validate_demo_server"
```

---

### Task 4: Update `pyproject.toml` + `__init__.py` package metadata

**Files:**
- Modify: `pyproject.toml:3-4` (version + description), `:9-16` (deps), `:44,56,87` (excludes)
- Modify: `src/signal_copier/__init__.py` (docstring + `__version__`)

- [ ] **Step 1: Update `pyproject.toml` — version**

Edit line 3:
```toml
version = "0.1.0"
```
→ change to:
```toml
version = "0.2.0"
```

- [ ] **Step 2: Update `pyproject.toml` — description**

Edit line 4:
```toml
description = "Telegram → OlympTrade signal copier (demo only, v1)"
```
→ change to:
```toml
description = "Telegram → MT5 signal copier (demo only, v1)"
```

- [ ] **Step 3: Update `pyproject.toml` — drop `websockets` dep**

In the `dependencies = [...]` block (around lines 9-16), remove the entire line:
```toml
    "websockets>=16.0",
```

The other 5 deps (`pydantic-settings`, `tzdata`, `asyncpg`, `telethon`, `loguru`) stay — `mt5linux` lands in M13.2's `dependencies` update, not now.

- [ ] **Step 4: Update `pyproject.toml` — drop `src/olymptrade_ws` excludes**

Edit line 44 (ruff `extend-exclude`):
```toml
extend-exclude = ["src/olymptrade_ws", "OlympTradeAPI"]
```
→ change to:
```toml
extend-exclude = ["OlympTradeAPI"]
```

Edit line 56 (mypy `exclude`):
```toml
exclude = ["src/olymptrade_ws"]
```
→ remove the line entirely:
```toml
[tool.mypy]
strict = true
python_version = "3.13"

[[tool.mypy.overrides]]
```
(`exclude` becomes empty/absent; mypy defaults to walking `src/` which no longer has the `olymptrade_ws` subdir.)

Edit line 87 (pytest `addopts`):
```toml
addopts = "-ra --strict-markers --ignore=OlympTradeAPI -m 'not slow'"
```
→ change to:
```toml
addopts = "-ra --strict-markers -m 'not slow'"
```

(`--ignore=OlympTradeAPI` was for a sibling checkout that isn't a test target. It was unused; remove.)

- [ ] **Step 5: Update `src/signal_copier/__init__.py`**

Replace the entire file contents with:

```python
"""signal_copier — Telegram → MT5 signal copier (demo only, M13).

Top-level convenience re-exports. The canonical import path is the
submodule (e.g., `from signal_copier.broker import Broker`); the
top-level path (`from signal_copier import Broker`) is provided as a
shorthand for callers that prefer it.
"""

from signal_copier.broker.base import Broker, BrokerAuthError, UnsupportedPairError

__version__ = "0.2.0"

__all__ = ["Broker", "BrokerAuthError", "UnsupportedPairError", "__version__"]
```

- [ ] **Step 6: Verify imports + version are reachable**

From project root:

```bash
uv run python -c "from signal_copier import __version__, Broker; print(__version__, Broker)"
```

Expected output: `0.2.0 <class 'signal_copier.broker.base.Broker'>`

- [ ] **Step 7: Run the full test suite + ruff to confirm no breakage**

Run:
```bash
uv run pytest tests/ -q
uv run ruff check src/ tests/ pyproject.toml
uv run mypy src/signal_copier/broker/mt5.py src/signal_copier/__init__.py
```

Expected: all green; ruff clean (or only pre-existing warnings); mypy clean on the new mt5.py.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml src/signal_copier/__init__.py uv.lock
git commit -m "build: bump to 0.2.0; rewrite description for MT5; drop olymptrade_ws from tooling"
```

(`uv.lock` updates automatically on `uv sync` if any deps changed; include it if modified.)

---

### Task 5: Wire `__main__.py` to broker layer + update BrokerAuthError handler

**Files:**
- Modify: `src/signal_copier/__main__.py:13-14` (add `Mt5Broker` import)
- Modify: `src/signal_copier/__main__.py:49-56` (replace OLYMP validation block with MT5 validation)
- Modify: `src/signal_copier/__main__.py:95-111` (mirror refactor.md §4.7 broker selection block)
- Modify: `src/signal_copier/__main__.py:228` (update BrokerAuthError handler wording)
- Test: `tests/test_main.py` (add a new test for the MT5 validation message)

- [ ] **Step 1: Rewrite `tests/test_main.py`'s config-validation test to cover MT5 validation**

Locate the function `test_main_returns_2_on_config_validation_error` in `tests/test_main.py` (around line 46). Rename the function to `test_main_returns_2_on_dry_run_false_with_incomplete_mt5_creds` and replace its entire body with:

```python
def test_main_returns_2_on_dry_run_false_with_incomplete_mt5_creds(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """M13.1: validation block at __main__.py:49-56 checks MT5 creds.

    Refactor docs/refactor.md §4.7: when DRY_RUN=false, MT5_LOGIN,
    MT5_PASSWORD, and MT5_SERVER must all be set. If any is missing,
    main() exits with code 2 and prints an 'incomplete credentials'
    message.
    """
    for key in [
        "TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_PHONE",
        "TELEGRAM_SESSION_STRING", "TELEGRAM_TARGET_CHAT", "DATABASE_URL",
        "AMOUNT_INITIAL", "AMOUNT_GALE1", "AMOUNT_GALE2",
        "EXPIRATION_SECONDS", "DAILY_LOSS_LIMIT", "DAILY_TRADE_LIMIT",
        "DAILY_DRAWDOWN_PCT", "TIMEZONE", "TRIGGER_SKEW_TOLERANCE_SECONDS",
        "LOG_PATH", "DRY_RUN", "REQUIRE_CONFIRM",
        "MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER", "MT5_TERMINAL_PATH",
    ]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DRY_RUN", "false")
    # All MT5_* unset → validation must fail before reaching the broker.

    rc = m5_main.main()
    assert rc == 2
    captured = capsys.readouterr()
    assert "MT5 broker credentials are incomplete" in captured.err
```

If any sibling tests in `tests/test_main.py` reference `OLYMP_*` env vars, delete them too — they're now testing obsolete behavior.

- [ ] **Step 2: Run the rewritten test, verify it fails**

Run:
```bash
uv run pytest tests/test_main.py::test_main_returns_2_on_dry_run_false_with_incomplete_mt5_creds -v
```

Expected: `SystemExit` with code 2 (the OLD validation block runs since __main__.py hasn't been updated yet), but stderr output contains the OLD message `"DRY_RUN=false but OLYMP_ACCESS_TOKEN is empty"` instead of the new `"MT5 broker credentials are incomplete"`. The assertion `assert "MT5 broker credentials are incomplete" in captured.err` fails.

- [ ] **Step 3: Add `Mt5Broker` import to `__main__.py`**

In `src/signal_copier/__main__.py`, after line 14 (`from signal_copier.broker.dry_run import DryRunBroker`), insert:

```python
from signal_copier.broker.mt5 import Mt5Broker
```

- [ ] **Step 4: Replace the validation block (lines 49-56)**

In `src/signal_copier/__main__.py`, locate the existing block at lines 49-56:

```python
        if not config.dry_run and not config.olymp_access_token:
            sys.stderr.write(
                "❌ DRY_RUN=false but OLYMP_ACCESS_TOKEN is empty. "
                "Set OLYMP_ACCESS_TOKEN in .env or set DRY_RUN=true.\n"
            )
            return 2
```

Replace with:

```python
        if not config.dry_run and (
            config.mt5_login == 0
            or not config.mt5_password
            or not config.mt5_server
        ):
            sys.stderr.write(
                "❌ DRY_RUN=false but MT5 broker credentials are incomplete. "
                "Set MT5_LOGIN, MT5_PASSWORD, MT5_SERVER in .env, "
                "or set DRY_RUN=true.\n"
            )
            return 2
```

- [ ] **Step 5: Run the rewritten test, verify it passes**

Run:
```bash
uv run pytest tests/test_main.py::test_main_returns_2_on_dry_run_false_with_incomplete_mt5_creds -v
```

Expected: `1 passed`.

- [ ] **Step 6: Replace the broker selection block (lines 95-111) to mirror refactor.md §4.7**

In `src/signal_copier/__main__.py`, locate the existing block at lines 94-106:

```python
        if config.dry_run:
            broker = DryRunBroker()
            _log.info("Broker: DryRunBroker (DRY_RUN=true)")
            await broker.connect()
        else:
            # MT5 broker integration is the next plan (see docs/refactor.md
            # Section 4.3 and 4.7). Until broker/mt5.py lands, live demo
            # trading is not implemented. Refuse with a clear error
            # rather than silently using a stale broker reference.
            raise NotImplementedError(
                "Live trading requires the MT5 broker; set DRY_RUN=true "
                "until the MT5 broker refactor (docs/refactor.md) is complete."
            )
```

Replace with (per refactor.md §4.7 verbatim):

```python
        if config.dry_run:
            broker = DryRunBroker()
            _log.info("Broker: DryRunBroker (DRY_RUN=true)")
            await broker.connect()
        else:
            broker = Mt5Broker(
                login=config.mt5_login,
                password=config.mt5_password,
                server=config.mt5_server,
                terminal_path=config.mt5_terminal_path,
                notifier=notifier,
            )
```
            _log.info(
                "Broker: MT5 (live demo, server=%s, login=%s)",
                config.mt5_server, config.mt5_login,
            )
            await broker.connect()
```

(Behavior is unchanged: with `DRY_RUN=false` + complete creds, `Mt5Broker(...)` constructs and `await broker.connect()` raises `NotImplementedError("…M13.2…")` which bubbles to the `except Exception` at line 233 → exit code 1. Externally identical to pre-M13.1.)

- [ ] **Step 7: Update the `BrokerAuthError` handler wording (line 228)**

In `src/signal_copier/__main__.py`, locate:

```python
    except BrokerAuthError as exc:
        sys.stderr.write(f"❌ OlympTradeBroker failed to connect: {exc}\n")
        return 2
```

Replace with:

```python
    except BrokerAuthError as exc:
        sys.stderr.write(f"❌ MT5 broker failed to connect: {exc}\n")
        return 2
```

- [ ] **Step 8: Run the full test suite, verify no regression**

Run:
```bash
uv run pytest tests/ -q
uv run ruff check src/signal_copier/__main__.py
```

Expected: all green; ruff clean.

- [ ] **Step 9: Manual smoke test of `DRY_RUN=true` path**

With a fake `.env` containing only `TELEGRAM_*` (no MT5), confirm `python -m signal_copier` boots far enough to validate Config (the rest fails because Telegram creds are also missing — that's fine; we only verify Config loads without crashing on `mt5_server`).

```bash
DRY_RUN=true uv run python -c "from signal_copier.config import Config; c = Config(_env_file=None); print('mt5_server=', repr(c.mt5_server))"
```

Expected output: `mt5_server= ''`

- [ ] **Step 10: Manual smoke test of `DRY_RUN=false` validation path**

```bash
DRY_RUN=false uv run python -m signal_copier 2>&1 | head -5
```

Expected: stderr contains `"DRY_RUN=false but MT5 broker credentials are incomplete"`; process exits with code 2.

- [ ] **Step 11: Commit Commit 1 (broker+config+entry-point)**

```bash
git add src/signal_copier/__main__.py tests/test_main.py
git commit -m "refactor(__main__): wire Mt5Broker import + MT5 validation block + BrokerAuthError handler"
```

Commit 1 of the 2-commit split is now complete. Confirm `git log --oneline -5` shows the chain:
```
<sha>  feat(...)
<sha>  refactor(__main__): ...
<sha>  build: ...
<sha>  refactor(config): ...
<sha>  feat(broker): Mt5Broker stub (M13.1; real impl M13.2)
```

---

### Task 6: Rename `on_olymp_*` Protocol methods + `NoOpNotifier` methods (Commit 2 part A)

**Files:**
- Modify: `src/signal_copier/notify/protocol.py:126-161` (Protocol class — 4 methods + docstrings)
- Modify: `src/signal_copier/notify/protocol.py:313-353` (`NoOpNotifier` class — 4 methods + log keys)
- Test: `tests/test_recording_notifier_protocol.py:35-38` (4 string literals)
- Test: `tests/test_notifier.py:185-247` (4 test functions + method calls + log-key assertions)

- [ ] **Step 1: Update `tests/test_recording_notifier_protocol.py` — rename 4 string literals**

Locate the list with `on_olymp_*` strings (around lines 35-38). Replace each:
```python
        "on_olymp_disconnect",
        "on_olymp_reconnecting",
        "on_olymp_reconnected",
        "on_olymp_reconnect_failed",
```
→
```python
        "on_broker_disconnect",
        "on_broker_reconnecting",
        "on_broker_reconnected",
        "on_broker_reconnect_failed",
```

- [ ] **Step 2: Run the test, verify it fails**

Run:
```bash
uv run pytest tests/test_recording_notifier_protocol.py -v
```

Expected: the recorder-based assertion fails because the source Protocol still has `on_olymp_*` method names.

- [ ] **Step 3: Rename 4 methods on the `Broker` Protocol (lines 126-161) in `notify/protocol.py`**

Apply this rename table:

| Old (line range) | New |
|---|---|
| `async def on_olymp_disconnect(self) -> None:` | `async def on_broker_disconnect(self) -> None:` |
| `async def on_olymp_reconnecting(self, *, ...) -> None:` | `async def on_broker_reconnecting(self, *, ...) -> None:` |
| `async def on_olymp_reconnected(self, *, ...) -> None:` | `async def on_broker_reconnected(self, *, ...) -> None:` |
| `async def on_olymp_reconnect_failed(self, *, ...) -> None:` | `async def on_broker_reconnect_failed(self, *, ...) -> None:` |

Update each method's docstring: replace `M8/M10` references with `M13+/M13.2`, and replace `OlympTrade` references with `Broker` or `MT5` depending on context (cosmetic; not strictly required for tests).

- [ ] **Step 4: Rename 4 methods + 4 log keys on `NoOpNotifier` (lines 313-353) in `notify/protocol.py`**

Apply the same 4 method renames. Also update 4 log strings:

```python
        _log.warning("notify: event=olymp_disconnect")
```
→
```python
        _log.warning("notify: event=broker_disconnect")
```

```python
        _log.warning(
            "notify: event=olymp_reconnecting attempt=%d/%d downtime=%.1fs next_delay=%.1fs",
            ...
        )
```
→
```python
        _log.warning(
            "notify: event=broker_reconnecting attempt=%d/%d downtime=%.1fs next_delay=%.1fs",
            ...
        )
```

```python
        _log.warning(
            "notify: event=olymp_reconnected attempts_used=%d total_downtime=%.1fs",
            ...
        )
```
→
```python
        _log.warning(
            "notify: event=broker_reconnected attempts_used=%d total_downtime=%.1fs",
            ...
        )
```

```python
        _log.error(
            "notify: event=olymp_reconnect_failed attempts=%d total_downtime=%.1fs",
            ...
        )
```
→
```python
        _log.error(
            "notify: event=broker_reconnect_failed attempts=%d total_downtime=%.1fs",
            ...
        )
```

- [ ] **Step 5: Run `test_recording_notifier_protocol.py`, verify it passes**

Run:
```bash
uv run pytest tests/test_recording_notifier_protocol.py -v
```

Expected: green.

- [ ] **Step 6: Update `tests/test_notifier.py` — rename 4 test functions + method calls + log-key assertions**

Apply this rename per the 4 tests at lines 185-247:

| Old test name | New test name |
|---|---|
| `test_noop_notifier_logs_olymp_disconnect_at_warning` | `test_noop_notifier_logs_broker_disconnect_at_warning` |
| `test_noop_notifier_logs_olymp_reconnecting_at_warning` | `test_noop_notifier_logs_broker_reconnecting_at_warning` |
| `test_noop_notifier_logs_olymp_reconnected_at_warning` | `test_noop_notifier_logs_broker_reconnected_at_warning` |
| `test_noop_notifier_logs_olymp_reconnect_failed_at_error` | `test_noop_notifier_logs_broker_reconnect_failed_at_error` |

Inside each renamed test, update:
- `await NoOpNotifier().on_olymp_*(...)` → `await NoOpNotifier().on_broker_*(...)`
- `assert "event=olymp_*" in msg` → `assert "event=broker_*" in msg`

(Run `tests/test_notifier.py` between each rename to confirm the assert failure first, then the pass — keep the TDD rhythm tight.)

- [ ] **Step 7: Run `tests/test_notifier.py`, verify all 4 tests pass**

Run:
```bash
uv run pytest tests/test_notifier.py -v
```

Expected: all 4 renamed tests pass; other notifier tests unchanged.

- [ ] **Step 8: Commit**

```bash
git add src/signal_copier/notify/protocol.py \
        tests/test_recording_notifier_protocol.py \
        tests/test_notifier.py
git commit -m "refactor(notifier): rename on_olymp_* to on_broker_* in Protocol + NoOp (M13.4 absorbed)"
```

This is Commit 2 of the M13.1 split. The remaining rename (TelegramDM + DM text + scheduler fixture) is Task 7.

---

### Task 7: Rename `on_olymp_*` in TelegramDM notifier + DM text + scheduler fixture (Commit 2 part B)

**Files:**
- Modify: `src/signal_copier/notify/telegram_dm.py:322-365` (4 methods + DM text per spec §4.2)
- Modify: `tests/_scheduler_fixtures.py:230-271` (4 method defs + 4 `_record` calls = 8 references)
- Test: `tests/test_telegram_dm.py:524-580` (4 test functions + method calls + DM text assertions)

- [ ] **Step 1: Update `tests/test_telegram_dm.py` — rename 4 test functions + method calls + DM text assertions**

Apply per the 4 tests at lines 524-580:

| Old test name | New test name |
|---|---|
| `test_telegram_dm_on_olymp_disconnect` | `test_telegram_dm_on_broker_disconnect` |
| `test_telegram_dm_on_olymp_reconnecting` | `test_telegram_dm_on_broker_reconnecting` |
| `test_telegram_dm_on_olymp_reconnected` | `test_telegram_dm_on_broker_reconnected` |
| `test_telegram_dm_on_olymp_reconnect_failed` | `test_telegram_dm_on_broker_reconnect_failed` |

Inside each, update:
- `await notifier.on_olymp_*(...)` → `await notifier.on_broker_*(...)`
- DM-text assertions: `"🔌 OlympTrade disconnected…"` → `"🔌 Broker disconnected…"` (and analogously per spec §4.2)

- [ ] **Step 2: Run the test, verify it fails**

Run:
```bash
uv run pytest tests/test_telegram_dm.py -v
```

Expected: 4 tests fail because the source method names + DM text are still old.

- [ ] **Step 3: Rename 4 methods + DM text in `notify/telegram_dm.py`**

Apply the rename table from Task 6 Step 3 (4 method renames). Apply the DM text changes per spec §4.2:

```python
    async def on_olymp_disconnect(self) -> None:
        await self._send("🔌 OlympTrade disconnected. Reconnecting…")
```
→
```python
    async def on_broker_disconnect(self) -> None:
        await self._send("🔌 Broker disconnected. Reconnecting…")
```

```python
    async def on_olymp_reconnecting(...) -> None:
        text = (
            f"🔁 OlympTrade reconnecting (attempt {attempt}/{max_attempts})\n"
            ...
        )
```
→
```python
    async def on_broker_reconnecting(...) -> None:
        text = (
            f"🔁 Broker reconnecting (attempt {attempt}/{max_attempts})\n"
            ...
        )
```

```python
    async def on_olymp_reconnected(...) -> None:
        text = (
            f"✅ OlympTrade reconnected\n"
            ...
        )
```
→
```python
    async def on_broker_reconnected(...) -> None:
        text = (
            f"✅ Broker reconnected\n"
            ...
        )
```

```python
    async def on_olymp_reconnect_failed(...) -> None:
        text = (
            f"❌ OlympTrade reconnect failed after {attempts} attempts\n"
            ...
        )
```
→
```python
    async def on_broker_reconnect_failed(...) -> None:
        text = (
            f"❌ Broker reconnect failed after {attempts} attempts\n"
            ...
        )
```

- [ ] **Step 4: Run the tests, verify all 4 pass**

Run:
```bash
uv run pytest tests/test_telegram_dm.py -v
```

Expected: 4 renamed tests pass.

- [ ] **Step 5: Rename 8 references in `tests/_scheduler_fixtures.py`**

Apply per lines 230-271:

```python
    async def on_olymp_disconnect(self) -> None:
        await self._record("on_olymp_disconnect")
```
→
```python
    async def on_broker_disconnect(self) -> None:
        await self._record("on_broker_disconnect")
```

```python
    async def on_olymp_reconnecting(self, *, ...) -> None:
        await self._record("on_olymp_reconnecting", ...)
```
→
```python
    async def on_broker_reconnecting(self, *, ...) -> None:
        await self._record("on_broker_reconnecting", ...)
```

(Same pattern × 4. If any test currently calls the recordings with `"on_olymp_*"` string keys, those string literals also rename.)

- [ ] **Step 6: Run the full test suite, verify all green**

Run:
```bash
uv run pytest tests/ -q
```

Expected: all green. `RecordingNotifier` still satisfies `Notifier` Protocol (method names match), all `pytest tests/` assertions pass.

- [ ] **Step 7: Final grep — verify zero `olymp_*` / `on_olymp_*` references in code + tests**

Run:
```bash
git grep -n 'olymp_\|OLYMP_\|on_olymp_' src/ tests/ pyproject.toml
```

Expected output: empty (no matches; only `docs/refactor.md` and `docs/superpowers/specs/2026-06-21-m8-…` historical docs may still match — those are intentional historical references).

- [ ] **Step 8: Commit (final commit of M13.1)**

```bash
git add src/signal_copier/notify/telegram_dm.py \
        tests/_scheduler_fixtures.py \
        tests/test_telegram_dm.py
git commit -m "refactor(notifier): rename on_olymp_* to on_broker_* in TelegramDM + DM text + scheduler fixture (M13.1 complete)"
```

---

## Final verification (after Task 7)

Run from project root:

```bash
# 1. Full test suite green
uv run pytest tests/ -q

# 2. Linting + type checks clean
uv run ruff check src/ tests/ pyproject.toml
uv run mypy src/signal_copier/

# 3. No olymp_* references in code or tests
git grep -n 'olymp_\|OLYMP_\|on_olymp_' src/ tests/ pyproject.toml

# 4. Module imports cleanly under DRY_RUN=true
DRY_RUN=true uv run python -c "from signal_copier import __version__; print('version=', __version__)"
# Expected: version= 0.2.0

# 5. Module refuses DRY_RUN=false with missing creds
DRY_RUN=false uv run python -m signal_copier 2>&1 | head -3
# Expected: stderr contains "MT5 broker credentials are incomplete", exit code 2
```

All five commands must succeed before M13.1 is done.

---

## Acceptance checklist

- [ ] Commit 1: `feat(broker): add close_position to Broker Protocol + DryRunBroker`
- [ ] Commit 1: `feat(broker): add Mt5Broker stub class for M13.1`
- [ ] Commit 1: `refactor(config): migrate OLYMP_* fields to MT5_*`
- [ ] Commit 1: `build: bump to 0.2.0; rewrite description for MT5; drop olymptrade_ws from tooling`
- [ ] Commit 1: `refactor(__main__): wire Mt5Broker import + MT5 validation block + BrokerAuthError handler`
- [ ] Commit 2: `refactor(notifier): rename on_olymp_* in Protocol + NoOp`
- [ ] Commit 2: `refactor(notifier): rename on_olymp_* in TelegramDM + DM text + scheduler fixture`
- [ ] `git log --oneline -8` shows the chain above (in any order)
- [ ] `git grep -n 'olymp_\|OLYMP_\|on_olymp_' src/ tests/ pyproject.toml` returns empty
- [ ] `__version__ == "0.2.0"` reachable from `from signal_copier import __version__`
- [ ] All 6 verification commands succeed

---

*End of M13.1 implementation plan.*
