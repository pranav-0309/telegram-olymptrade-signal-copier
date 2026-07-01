# M13.1 Broker Protocol + Config Rename — Design Spec

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Land the structural broker swap (MT5 Protocol method, config migration, dry-run preservation) without any functional change to `DRY_RUN=true` behavior, plus absorb M13.4's notifier rename so M13.1 delivers one cohesive refactor commit series.

**Architecture:** Additive Protocol method + cosmetic config field rename + method rename across the Notifier surface. New code is limited to one stub class (`Mt5Broker`); everything else is rename/move/validator-rewrite.

**Tech Stack:** Python 3.13, pydantic-settings, asyncio, pytest.

---

## 1. Success criteria

1. `pytest tests/` is green.
2. `python -m signal_copier` with the existing default `.env` boots and emits `🟢 Bot started` (DRY_RUN=true path is behaviorally identical to pre-M13.1).
3. `python -m signal_copier` with `DRY_RUN=false` and missing MT5 creds exits with code 2 and a clear "incomplete credentials" message (validation block at `__main__.py:49-56`).
4. `python -m signal_copier` with `DRY_RUN=false` and complete MT5 creds exits with code 1 + `NotImplementedError("...M13.2...")` (the stub raises; behavior unchanged from pre-M13.1 `NotImplementedError` at line 99).
5. No file in `src/signal_copier/` references `olymp_*` (fields, validators, message strings).
6. No file references `on_olymp_*` (Protocol method names, notifier impls, test calls).
7. Version bumped to `0.2.0` in both `src/signal_copier/__init__.py` and `pyproject.toml`.

---

## 2. Commit structure (Approach C — 2 commits)

| # | Commit message | Includes | Verify |
|---|---|---|---|
| 1 | `refactor(broker): swap OlympTrade for MT5 protocol + config (M13.1)` | broker/base.py, broker/dry_run.py, broker/mt5.py (stub), config.py, __main__.py, __init__.py, pyproject.toml, tests/test_config.py | `pytest tests/` green; `DRY_RUN=true` boot unchanged |
| 2 | `refactor(notifier): rename on_olymp_* to on_broker_* (M13.4 absorbed)` | notify/protocol.py, notify/telegram_dm.py, tests/_scheduler_fixtures.py, tests/test_notifier.py, tests/test_recording_notifier_protocol.py, tests/test_telegram_dm.py | `pytest tests/` green; DM text uses "Broker" wording |

**Rationale:** Commit 1 holds the "test green on dry-run" invariant (M13.1's literal success criterion). Commit 2 is a cosmetic DM-text refactor with no behavior change — easy to revert independently.

---

## 3. File changes — Commit 1

### 3.1 `src/signal_copier/broker/base.py`

Append after `wait_result` (around line 87), inside the `Broker` Protocol class:

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

### 3.2 `src/signal_copier/broker/dry_run.py`

Append after `wait_result` (around line 99):

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

### 3.3 `src/signal_copier/broker/mt5.py` (CREATE)

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

### 3.4 `src/signal_copier/config.py`

**Drop** (lines 31-34 + 68-83):

```python
# REMOVE: OLYMP_* block
olymp_access_token: str = ""
olymp_account_group: str = "demo"
olymp_account_id: str = ""

# REMOVE: validators
@field_validator("olymp_account_group")
@classmethod
def _validate_account_group(cls, v: str) -> str: ...

@model_validator(mode="after")
def _demo_only_guardrail(self) -> Config: ...
```

**Add** (after line 30, before the database URL block):

```python
    # --- MT5 broker (M13 — replaces OLYMP_* block; docs/refactor.md §4.6) ----
    mt5_login: int = 0
    mt5_password: str = ""
    mt5_server: str = ""
    mt5_terminal_path: str | None = None
```

**Add validator** (where `_validate_account_group` was):

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

### 3.5 `src/signal_copier/__main__.py` — 3 edit zones

**Zone A — top-of-file import:**

Add (after the existing `from signal_copier.broker.dry_run import DryRunBroker` line):

```python
from signal_copier.broker.mt5 import Mt5Broker
```

**Zone B — validation block (replace lines 49-56):**

Before:
```python
        if not config.dry_run and not config.olymp_access_token:
            sys.stderr.write(
                "❌ DRY_RUN=false but OLYMP_ACCESS_TOKEN is empty. "
                "Set OLYMP_ACCESS_TOKEN in .env or set DRY_RUN=true.\n"
            )
            return 2
```

After:
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

**Zone C — broker selection (replace lines 95-111).** Mirror `docs/refactor.md` §4.7 verbatim:

Before:
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

After:
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
            _log.info(
                "Broker: MT5 (live demo, server=%s, login=%s)",
                config.mt5_server, config.mt5_login,
            )
            await broker.connect()
```

Behavior: on `DRY_RUN=false` with complete credentials, `Mt5Broker(...)` constructs (logs warning), then `await broker.connect()` raises `NotImplementedError("...M13.2...")`. This bubbles to `except Exception` at line 233 of `main()` → exit code 1. **Identical external behavior to pre-M13.1's `NotImplementedError` at line 99 of `__main__.py`.**

**Zone D — `BrokerAuthError` handler (replace line 228):**

Before:
```python
    except BrokerAuthError as exc:
        sys.stderr.write(f"❌ OlympTradeBroker failed to connect: {exc}\n")
        return 2
```

After:
```python
    except BrokerAuthError as exc:
        sys.stderr.write(f"❌ MT5 broker failed to connect: {exc}\n")
        return 2
```

### 3.6 `src/signal_copier/__init__.py`

Replace file contents:

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

### 3.7 `pyproject.toml` — 4 edits

| Line | Before | After |
|---|---|---|
| 3 | `version = "0.1.0"` | `version = "0.2.0"` |
| 4 | `description = "Telegram → OlympTrade signal copier (demo only, v1)"` | `description = "Telegram → MT5 signal copier (demo only, v1)"` |
| 9-16 (deps) | `"websockets>=16.0",` line present | Remove the entire line (OlympTrade vendoring was its only consumer; mt5linux lands in M13.2) |
| 44 | `extend-exclude = ["src/olymptrade_ws", "OlympTradeAPI"]` | `extend-exclude = ["OlympTradeAPI"]` |
| 56 | `exclude = ["src/olymptrade_ws"]` | Remove the line entirely (mypy default recurses nothing absent) |
| 87 | `addopts = "-ra --strict-markers --ignore=OlympTradeAPI -m 'not slow'"` | `addopts = "-ra --strict-markers -m 'not slow'"` |

### 3.8 `tests/test_config.py`

Rewrite the 5 `olymp_account_group`-touching tests per Phase 5:

| Line | Before | After |
|---|---|---|
| 38-39 | `test_default_olymp_account_group_is_demo` asserting `cfg.olymp_account_group == "demo"` | **Rename** to `test_default_mt5_server_is_empty`; assert `cfg.mt5_server == ""` |
| 63-65 | `test_account_group_real_with_dry_run_true_is_allowed` | **Delete** (no MT5 equivalent) |
| 68-74 | `test_account_group_real_with_dry_run_false_refuses_to_start` asserting `"Refusing to start"` + `"DRY_RUN=true"` | **Rename** to `test_mt5_server_non_demo_refuses`; use `_config(mt5_server="VTMarkets-Real01")`; assert `"must contain 'demo'"` in the ValidationError |
| 77-79 | `test_account_group_demo_with_dry_run_false_is_allowed` | **Rename** to `test_mt5_server_demo_with_dry_run_false_is_allowed`; use `_config(mt5_server="VTMarkets-Demo", dry_run=False)`; assert success |
| 82-85 | `test_account_group_invalid_value_raises` | **Delete** (the enum-style check is gone in the new validator) |

Net change: −2 tests deleted, +2 tests added (5→4 → +1 rename, +1 new), +1 net.

---

## 4. File changes — Commit 2

### 4.1 Method rename table

| Old | New |
|---|---|
| `on_olymp_disconnect` | `on_broker_disconnect` |
| `on_olymp_reconnecting` | `on_broker_reconnecting` |
| `on_olymp_reconnected` | `on_broker_reconnected` |
| `on_olymp_reconnect_failed` | `on_broker_reconnect_failed` |

### 4.2 DM text changes (per `docs/refactor.md` §4.8)

| Old | New |
|---|---|
| `"🔌 OlympTrade disconnected. Reconnecting…"` | `"🔌 Broker disconnected. Reconnecting…"` |
| `"🔁 OlympTrade reconnecting (attempt X/Y)…"` | `"🔁 Broker reconnecting (attempt X/Y)…"` |
| `"✅ OlympTrade reconnected"` | `"✅ Broker reconnected"` |
| `"❌ OlympTrade reconnect failed after X attempts"` | `"❌ Broker reconnect failed after X attempts"` |

Wording choice: **generic "Broker"**, not "MT5" (per refactor.md §4.8 last paragraph — broker-agnostic abstraction).

### 4.3 `src/signal_copier/notify/protocol.py`

Two changes:
1. **Protocol class** (lines 126-161) — rename the 4 reconnect methods. Update each method's docstring to reference `M13+` / `M13.2` instead of `M8`/`M10`.
2. **`NoOpNotifier` class** (lines 313-353) — rename the 4 methods. Replace the 4 log-key strings:
   - `"notify: event=olymp_disconnect"` → `"notify: event=broker_disconnect"`
   - `"notify: event=olymp_reconnecting"` → `"notify: event=broker_reconnecting"`
   - `"notify: event=olymp_reconnected"` → `"notify: event=broker_reconnected"`
   - `"notify: event=olymp_reconnect_failed"` → `"notify: event=broker_reconnect_failed"`

### 4.4 `src/signal_copier/notify/telegram_dm.py`

Lines 322-365 — rename the 4 methods. Apply the DM text changes from §4.2 above.

### 4.5 `tests/_scheduler_fixtures.py`

8 references: 4 method definitions (`async def on_olymp_*`) + 4 `_record("on_olymp_*")` calls. All renamed to `on_broker_*` / `"on_broker_*"` per §4.1.

### 4.6 `tests/test_notifier.py`

4 tests fully renamed + updated:

| Before test name | After test name |
|---|---|
| `test_noop_notifier_logs_olymp_disconnect_at_warning` | `test_noop_notifier_logs_broker_disconnect_at_warning` |
| `test_noop_notifier_logs_olymp_reconnecting_at_warning` | `test_noop_notifier_logs_broker_reconnecting_at_warning` |
| `test_noop_notifier_logs_olymp_reconnected_at_warning` | `test_noop_notifier_logs_broker_reconnected_at_warning` |
| `test_noop_notifier_logs_olymp_reconnect_failed_at_error` | `test_noop_notifier_logs_broker_reconnect_failed_at_error` |

Inside each test: both the `NoOpNotifier().on_olymp_*(...)` method call AND the `"event=olymp_*" in msg` assertion update per §4.1.

### 4.7 `tests/test_recording_notifier_protocol.py`

4 string literals in the recorded-events list (`"on_olymp_disconnect"`, etc.) renamed per §4.1.

### 4.8 `tests/test_telegram_dm.py`

4 tests renamed + method calls updated:

| Before test name | After test name |
|---|---|
| `test_telegram_dm_on_olymp_disconnect` | `test_telegram_dm_on_broker_disconnect` |
| `test_telegram_dm_on_olymp_reconnecting` | `test_telegram_dm_on_broker_reconnecting` |
| `test_telegram_dm_on_olymp_reconnected` | `test_telegram_dm_on_broker_reconnected` |
| `test_telegram_dm_on_olymp_reconnect_failed` | `test_telegram_dm_on_broker_reconnect_failed` |

---

## 5. Out of scope (deferred to later milestones)

| # | Item | Milestone | Reason |
|---|---|---|---|
| 1 | Real `Mt5Broker` body — `connect()` via `mt5.initialize()`, `place()` via `mt5.order_send()`, `wait_result()` via position polling, `close_position()` returning `position.profit` | M13.2 | Stub raises `NotImplementedError`; needs `mt5linux` client integration + asset-map + `account_info()` cached-balance wiring |
| 2 | `src/signal_copier/broker/reconnect.py` (MT5-flavored supervisor; ~150 lines) | M13.2 | Reconnect wrapper depends on a working broker. Reuses `compute_backoff_seconds`-style backoff math. |
| 3 | `domain/state.py:_stage_pnl` removal; add `pnl: Decimal \| None = None` to `ResultEvent` | M13.5 | Per refactor.md §4.5: state machine accepts broker-reported PnL only after M13.2 proves the value is trustworthy. |
| 4 | `scheduler/trigger.py:_drive_cascade` auto-close race (`call_at` deadline vs `result_future`) | M13.5 | Coupled with item 3. |
| 5 | `scheduler/trigger.py:_compute_stage_pnl_for_result` removal | M13.5 | Mirrors item 3. |
| 6 | `.env.example` rewrite — drop `OLYMP_*`, add `MT5_*` block (refactor.md §6.2) | M13.5 | Manual user migration; batched with docs sweep. |
| 7 | `README.md` — 15 `OlympTrade` references rewritten to MT5 | M13.5 | Documentation sweep. |
| 8 | `docs/PRD.md` — header + FR-4.x + §6 + §7 + §10 + §13.1 R-5/R-6/R-15 + §15 M8/M10/M13 + §17.1 | M13.5 | Historical PRD update; doesn't affect runtime. |
| 9 | `tests/test_auth.py` 21 `OLYMP_*` env-clearing literals | M13.5 | Cosmetic; harmless in M13.1 (`extra="ignore"`) |
| 10 | `mt5linux/Dockerfile`, `entrypoint.sh`, `requirements.txt`, `railway.toml` | M14.1 | Service B for Railway; needs M13.2 broker impl first |
| 11 | `tools/mt5_preflight.py` | M13.3 / M14.x | Sanity-check script |
| 12 | GitHub repo rename `olymptrade` → `telegram-mt5-copier` (web UI) | M13.6 | Operational; lower-risk after code is clean (refactor.md §2.1-2.3) |
| 13 | Railway project + service rename | M13.6 | Operational (refactor.md §3.1-3.2) |
| 14 | Wire Railway Service A → Service B (`MT5_SERVER_HOST` env) | M14.3 | Requires items 10 + 1 |
| 15 | First end-to-end demo trade, restart resilience, rollback smoke test | M14.4-14.6 | Success criterion for the whole broker swap |

---

## 6. Verification steps

After each commit:

```bash
# Commit 1 verification
uv run pytest tests/                                  # must be green
uv run python -m signal_copier --help                 # must not crash
git grep -n 'olymp_\|OLYMP_\|on_olymp_' src/signal_copier/   # must return nothing
grep '^version' pyproject.toml                        # must show 0.2.0
grep '__version__' src/signal_copier/__init__.py      # must show "0.2.0"

# Commit 2 verification
uv run pytest tests/                                  # must be green
git grep -n 'olymp_\|on_olymp_' src/ tests/           # must return nothing
```

For Commit 1's `DRY_RUN=false` path:

```bash
DRY_RUN=false MT5_LOGIN=12345678 MT5_PASSWORD=secret MT5_SERVER=VTMarkets-Demo \
    uv run python -m signal_copier
# Expected: exit code 1, stderr contains "NotImplementedError" + "M13.2"
```

---

## 7. Risk and rollback

**Commit 1 risks**
- `Mt5Broker` stub raises `NotImplementedError` if `DRY_RUN=false` is set with complete creds. The `__main__.py:49-56` validation runs first; if creds are missing, exit code 2 with "incomplete credentials" message. If creds are present, the stub's `connect()` raises → caught by `except Exception` → exit code 1. **Externally identical to pre-M13.1's `NotImplementedError` at line 99 of `__main__.py`.**
- `pyproject.toml`'s dropped `websockets` dep: no consumer in M13.1 (the only consumer was the deleted `olymptrade_ws`). If M13.2's `mt5linux` adds an indirect transitive `websockets`, `uv sync` will pull it back via dependency resolution.

**Commit 2 risks**
- Pure cosmetic. No behavior change. Renaming a string in a DM does not affect control flow.

**Rollback**
- Commit 1: `git revert <sha>` — restores OLYMP_* schema, removes `close_position` from Protocol + DryRunBroker, removes `Mt5Broker` stub. Tests return to pre-M13.1 baseline.
- Commit 2: `git revert <sha>` — restores `on_olymp_*` method names + DM text. Pure cosmetic revert.

Either commit can be reverted independently without touching the other.

---

## 8. Cross-references

- `docs/refactor.md` §4.4 — Protocol design decision rationale
- `docs/refactor.md` §4.6 — Config field-level changes
- `docs/refactor.md` §4.7 — `__main__.py` broker selection block
- `docs/refactor.md` §4.8 — Notifier rename + DM text changes
- `docs/refactor.md` Appendix A — Per-file scope cheat sheet
- `docs/refactor.md` Appendix B — Milestone mapping (M13.1-M14.6)
- `docs/superpowers/plans/2026-06-30-preflight-cleanup-for-mt5-refactor.md` — Predecessor plan

---

*End of M13.1 design spec.*
