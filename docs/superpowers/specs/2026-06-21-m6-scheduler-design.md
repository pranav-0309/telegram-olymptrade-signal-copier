# M6 — Scheduler & Notifier Protocol Design

**Date:** 2026-06-21
**Status:** Draft (pending user review)
**PRD reference:** `docs/PRD.md` v0.7 (§4.3 FR-3.1–3.7, §4.4 FR-4.4–4.6, §4.5 FR-5.1–5.9, §4.6 FR-6.1–6.6, §4.7 FR-7.1, §6 Tech Stack, §7 Architecture, §15 M6 row, §10 Error Handling)
**Build plan reference:** PRD §15, M6 row
**M5 spec reference:** `docs/superpowers/specs/2026-06-21-m5-telegram-listener-design.md` (M5's `dump_consumer` is replaced by M6's `Scheduler.run()`)
**M4 spec reference:** `docs/superpowers/specs/2026-06-20-m4-database-infrastructure-design.md` (StateStore methods M6 uses)
**M3 spec reference:** `docs/superpowers/specs/2026-06-20-m3-broker-protocol-design.md` (Broker Protocol + DryRunBroker M6 invokes)
**M2 spec reference:** `docs/superpowers/specs/2026-06-19-m2-state-machine-design.md` (pure state machine M6 drives)

---

## 1. Purpose & Scope

M6 is the seventh milestone of the Telegram → OlympTrade Signal Copier (PRD v0.7). It ships the **scheduler** that drives the existing M2 state machine through the full cascade (`initial` → optional `gale1` → optional `gale2` → terminal), placing trades on the M3 `DryRunBroker` at the prescribed HH:MM, and the **Notifier Protocol** that M7's `TelegramDMNotifier` will implement.

**M6 turns the assembled M0–M5 pipeline into an end-to-end demoable system** under `DRY_RUN=true`. With M6 in place, a signal flowing from Telegram listener → StateStore → Scheduler → DryRunBroker → Notifier (NoOp) exercises the complete v1 happy path against a deterministic broker, with no real-money code paths yet (M8 adds `OlympTradeBroker`).

**In scope for M6 (2 new packages, 4 new files, 3 modified files, 3 new test files, 1 modified test file):**

| # | File | Type | Purpose |
|---|---|---|---|
| 1 | `src/signal_copier/scheduler/__init__.py` | NEW | Empty package marker |
| 2 | `src/signal_copier/scheduler/trigger.py` | NEW | `Scheduler` (queue consumer) + `SignalSupervisor` (per-signal coroutine) + `compute_target_monotonic` helper |
| 3 | `src/signal_copier/notify/__init__.py` | NEW | Empty package marker (M7 adds `telegram_dm.py` here) |
| 4 | `src/signal_copier/notify/protocol.py` | NEW | `Notifier` Protocol + `NoOpNotifier` (logging at INFO with structured payload) |
| 5 | `src/signal_copier/domain/state.py` | MODIFY | Extend `ErrorReason` literal to include `'daily_limit_hit'` (D-2; no DB migration — `error_reason` column is unconstrained TEXT) |
| 6 | `src/signal_copier/__main__.py` | MODIFY | Replace M5 `dump_consumer` with `Scheduler.run()`; add notifier to wiring; emit `on_bot_started`/`on_bot_stopping` notifications |
| 7 | `pyproject.toml` | MODIFY | Add 2 new test modules to mypy override (no new runtime deps) |
| 8 | `tests/_scheduler_fixtures.py` | NEW | Shared helpers: `FakeBroker`, `RecordingNotifier`, `make_signal_with_future_trigger`, `assert_within_skew` |
| 9 | `tests/test_scheduler.py` | NEW | ~17 tests for `Scheduler.run()` + `SignalSupervisor` (happy path, full cascade, signal-expired, daily limits, idempotency, broker errors, notifier isolation, cancellation) |
| 10 | `tests/test_notifier.py` | NEW | ~6 tests for `Notifier` Protocol runtime checkability + `NoOpNotifier` + `RecordingNotifier` |
| 11 | `tests/test_main.py` | MODIFY | +3 tests for M6 wiring in `__main__.main()` (scheduler replaces dump_consumer; bot-started/stopping notifications) |

**Out of scope (deferred to later milestones):**

| Concern | Lands in |
|---|---|
| Real broker (`broker/olymp.py`), push events, `wait_result` integration, pair auto-discover | M8 |
| Telegram DM sending (`notify/telegram_dm.py`) | M7 |
| Restart recovery (`get_active_signals` → resume cascades after process death) | M10 (tied to WS reconnect story; PRD §10 row "Process killed mid-cascade") |
| Loguru setup with rotation, FR-7.2 mirror log, `logs/signal_copier.log` | M7 (M6 keeps stdlib logging per M3 D-6, M4 D-15, M5 D-8 convention) |
| Desktop notifications (FR-7.3) | v2 |
| Trigger-precision spin-loop for the last 50ms (S-7) | v1.0 follow-on |
| M9 end-to-end test (real Telegram channel → dry-run broker cascade) | M9 |
| Daily-limit config validator refinements (FR-6.1–6.3) | Already in M2's Config; M6 just calls the values |
| Self-healing WS reconnect for OlympTrade | M10 (S-5) |

---

## 2. Resolved Decisions (M6-specific)

The PRD resolves all architectural questions (R-1 through R-15). The following are M6-specific scoping and design calls, confirmed during brainstorming on 2026-06-21.

| # | Decision | Rationale |
|---|---|---|
| D-1 | **M6 trusts the state machine's pre-fire guard; no redundant M6-level check** | The state machine's `_check_time_window` (state.py:128–138) and `transition` FireEvent branch (state.py:311–314) are the single source of truth for window enforcement. M6 schedules `call_at` with the raw wall-clock target; if the call_at callback fires after the tolerance window, the state machine dispatches `error (signal_expired)`. Adding a parallel M6 check duplicates logic and creates a place where the two checks can disagree. |
| D-2 | **`error_reason='daily_limit_hit'` added to `ErrorReason` literal in `domain/state.py`** | PRD §9.1 lists 5 error reasons; daily-limit rejection is a 6th. Adding it explicitly (a) matches PRD §9.1's pattern of named reasons, (b) is a code-only change (the `error_reason` column is unconstrained TEXT in migrations/001_initial.sql:441, no migration needed), and (c) preserves the alternative `'signal_expired'` for actual time-window failures. |
| D-3 | **`daily_summary` updated after every `record_stage_result` (not lazily on read)** | FR-6.1/6.2/6.3 limit checks need current totals. M4's `update_daily_summary` is `INSERT ... ON CONFLICT (date) DO UPDATE SET ... = ... + EXCLUDED....` — atomic and cheap (PRD §9.2 confirmed). Lazy aggregation would make `limit_hit` checks stale until next read. |
| D-4 | **`UnsupportedPairError` from broker → `ResultEvent(result="error")` → state machine → `error`** | The state machine is the single source of truth for the cascade. M6's supervisor translates the broker's exception type into the state machine's vocabulary (`StageResult("error")`); the state machine handles the transition uniformly per state.py:276–277. |
| D-5 | **Supervisor catches broker + notifier exceptions, logs, and exits; DB exceptions are re-raised to `__main__`** | Broker and notifier errors are operational (network blips, Telegram DM failures) and must not abort the process. DB errors are rare and indicate real problems (auth failure, broken connection pool); re-raising exits non-zero so Railway restarts (PRD §17.3). |
| D-6 | **`asyncio.Queue` maxsize stays at 1000 from M5** | One signal per ~5 minutes is the analyst's cadence; 1000 is ~3 days of headroom. The scheduler drains immediately. No queue-size tuning in M6. |
| D-7 | **Scheduler tracks active supervisor tasks in `set[asyncio.Task]` for clean shutdown** | `__main__` SIGINT cancels the scheduler task; the scheduler iterates and cancels each supervisor. Cancelled supervisors skip DB cleanup (per error-handling row in §6) and exit. Prevents orphan tasks holding broker connections or DB pool entries. |
| D-8 | **Test clock uses real `time`/`asyncio` for sub-second windows; no `freezegun` or similar** | M6's "≤500ms skew" deliverable is a real-time property. Freezing time wouldn't exercise the actual scheduling path; it would test a mocked version. Sub-second windows are short enough that CI stays fast. |
| D-9 | **`FakeBroker` fixture allows per-stage outcome programming (`{initial: 'win', gale1: 'loss', gale2: 'win'}`)** | One fixture covers all cascade-path tests. No subclass-per-path. Outcomes are looked up by `stage`; missing stages default to the broker's `_default_outcome`. |
| D-10 | **`RecordingNotifier` collects all method calls as `(method_name, kwargs)` tuples** | One fixture covers all notifier-side tests. Lets tests assert "the WIN notification fired for the gale2 trade" without parsing log output. |
| D-11 | **Scheduler reads `state_store.get_signal(signal_id)` on intake to detect duplicates from M5 retries** | Belt-and-suspenders for M5's `upsert_signal` + `ON CONFLICT DO NOTHING`. A retried signal would re-enter the queue; the second supervisor finds a non-pending row and exits cleanly. |
| D-12 | **`__main__.py` calls `await notifier.on_bot_started(...)` after wiring** | Matches FR-7.1 row "Bot startup". M6 owns the wiring; notification happens once everything is connected and ready. |
| D-13 | **`__main__.py` calls `await notifier.on_bot_stopping(open_cascades=...)` on SIGINT** | Matches FR-7.1 row "Bot shutdown". `open_cascades` is `len(scheduler.active_tasks)` at cancel time — number of in-flight cascades. |
| D-14 | **No loguru in M6 (M7 owns that)** | Stdlib logging per M3 D-6 / M4 D-15 / M5 D-8 convention. M7's setup will route stdlib `logging` records through loguru's sinks (D-8 cross-ref). M6's `NoOpNotifier` logs at INFO via stdlib. |
| D-15 | **The Notifier Protocol lives in `notify/protocol.py`** (not in `scheduler/`) | M7's `telegram_dm.py` imports the Protocol; `notify/` is the home for notification concerns. Keeps the scheduler package free of cross-cutting interface definitions it doesn't own. |
| D-16 | **`Scheduler.run()` and `TelegramClient.start()` are both `asyncio.create_task(...)`'d in `__main__.py`; the entrypoint awaits both** | Standard asyncio pattern; cancel-on-SIGINT for both. `Scheduler.run()` is the consumer that drains the queue; `TelegramClient.start()` (M5) is the listener. Both run forever until disconnect or cancel. |
| D-17 | **Negative wall-clock delta at intake (signal already past trigger): dispatch `FireEvent` immediately with `now_unix > trigger_unix`** | State machine handles → `error (signal_expired)`. No `call_at` scheduled. Catches stale signals (FR-3.3) at intake rather than waiting for the impossible `call_at` to fire. |
| D-18 | **`pytest-asyncio` mode stays `auto` (configured in M0); M6 tests use `async def test_...` directly** | Matches M3 / M4 / M5 test conventions. No `pyproject.toml` pytest changes. |
| D-19 | **M6 does not extend `mypy --strict`; one-line test override added per M5's pattern** | M5 added 4 test modules to the mypy override; M6 adds 2 more (`test_scheduler`, `test_notifier`). M6 source modules (`scheduler/trigger.py`, `notify/protocol.py`) ship under strict. |
| D-20 | **The `SignalSupervisor` is a class, not a free function** | Same pattern as M5's `Listener` and M3's `DryRunBroker`. Holds stateful dependencies (`broker`, `state_store`, `notifier`, `config`, `signal`) — class is the natural unit. Free function would re-pass on every method call. |

---

## 3. Repository Layout (post-M6)

```
olymptrade/
├── pyproject.toml                          # MODIFY: +2 test modules to mypy override
├── migrations/                             # (unchanged from M4)
├── src/
│   ├── olymptrade_ws/                      # (unchanged, vendored)
│   └── signal_copier/
│       ├── __init__.py                     # (unchanged)
│       ├── __main__.py                     # MODIFY: full M6 wiring (Scheduler replaces dump_consumer)
│       ├── config.py                       # (unchanged from M2)
│       ├── broker/                         # (unchanged from M3)
│       ├── domain/                         # MODIFY
│       │   ├── signal.py                   # (unchanged from M1)
│       │   ├── state.py                    # MODIFY: ErrorReason literal +'daily_limit_hit' (D-2)
│       │   └── gale.py                     # (unchanged from M2)
│       ├── infra/                          # (unchanged from M5)
│       ├── telegram/                       # (unchanged from M5)
│       ├── scheduler/                      # NEW package
│       │   ├── __init__.py                 # NEW: empty
│       │   └── trigger.py                  # NEW: Scheduler + SignalSupervisor + compute_target_monotonic
│       └── notify/                         # NEW package
│           ├── __init__.py                 # NEW: empty
│           └── protocol.py                 # NEW: Notifier Protocol + NoOpNotifier
└── tests/
    ├── _scheduler_fixtures.py              # NEW: FakeBroker, RecordingNotifier, make_signal_with_future_trigger, assert_within_skew
    ├── test_scheduler.py                   # NEW: ~17 tests
    ├── test_notifier.py                    # NEW: ~6 tests
    ├── conftest.py                         # (unchanged from M4)
    ├── _telegram_fixtures.py               # (unchanged from M5)
    ├── test_main.py                        # MODIFY: +3 M6 wiring tests
    ├── test_db.py                          # (unchanged from M4)
    ├── test_broker_protocol.py             # (unchanged from M3)
    ├── test_dry_run_broker.py              # (unchanged from M3)
    ├── test_parser.py                      # (unchanged from M1)
    ├── test_gale_math.py                   # (unchanged from M2)
    ├── test_state_machine.py               # (unchanged from M2)
    ├── test_config.py                      # (unchanged from M2)
    ├── test_clock.py                       # (unchanged from M5)
    ├── test_log.py                         # (unchanged from M5)
    ├── test_telegram_listener.py           # (unchanged from M5)
    ├── test_telegram_client.py             # (unchanged from M5)
    └── test_auth.py                        # (unchanged from M5)
```

**Notable choices:**
- `scheduler/` is a new top-level package. `__init__.py` is empty (matches M4 infra/, M5 telegram/ conventions).
- `notify/` is also a new package, separate from `scheduler/` so M7's `telegram_dm.py` lives in its concern's home (D-15).
- `scheduler/trigger.py` holds both `Scheduler` and `SignalSupervisor`. They are tightly coupled (the scheduler spawns supervisors), and separating them would force an artificial interface for a one-line `create_task(...)` call.
- `notify/protocol.py` is small (~120 lines including the `NoOpNotifier`); a separate file gives M7 a single import target.

---

## 4. Key File Contents

### 4.1 `src/signal_copier/scheduler/__init__.py` (NEW)

```python
# Empty. Callers import from submodules:
#   from signal_copier.scheduler.trigger import Scheduler, SignalSupervisor
#
# No top-level re-exports — the package is a namespace, not a facade.
# Matches the M4 / M5 convention.
```

### 4.2 `src/signal_copier/notify/__init__.py` (NEW)

```python
# Empty. Callers import from submodules:
#   from signal_copier.notify.protocol import Notifier, NoOpNotifier
#
# No top-level re-exports. M7's telegram_dm.py lives here too.
```

### 4.3 `src/signal_copier/notify/protocol.py` (NEW)

```python
"""The Notifier Protocol — the cross-cutting interface between M6's scheduler
and M7's Telegram DM notifier.

M6 ships a `NoOpNotifier` (logs every event at INFO). M7 implements
`TelegramDMNotifier` that satisfies the Protocol and sends the FR-7.1
messages. Tests substitute `RecordingNotifier` (in tests/_scheduler_fixtures.py).

Design contract:
  - Every method is async (M7 may need to await Telegram API calls).
  - Methods never raise. If a method body raises, M6's supervisor catches
    the exception, logs it, and continues. A failing DM must not abort
    a cascade.
  - All methods receive a frozen dataclass (Signal, SignalState, etc.);
    notifiers must not mutate them. Notifiers may hold their own state
    internally.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from signal_copier.domain.gale import Stage
from signal_copier.domain.signal import Signal

if TYPE_CHECKING:
    from signal_copier.domain.state import TerminalState
    from signal_copier.infra.state_store import DailySummaryRow

_log = logging.getLogger(__name__)


@runtime_checkable
class Notifier(Protocol):
    """One method per FR-7.1 event that the scheduler emits.

    Each method's docstring cites the FR-7.1 row it implements.
    """

    async def on_signal_received(self, signal: Signal) -> None:
        """FR-7.1 row 'Signal received'. Fires immediately on parser match."""

    async def on_trade_placed(
        self, signal: Signal, stage: Stage, amount: Decimal, trade_id: str,
    ) -> None:
        """FR-7.1 rows 'Trade placed — initial/1st gale/2nd gale' (parameterized by `stage`)."""

    async def on_win(
        self,
        signal: Signal,
        stage: Stage,
        pnl: Decimal,
        cumulative_pnl: Decimal,
    ) -> None:
        """FR-7.1 rows 'WIN — initial/1st gale/2nd gale' (parameterized by `stage`)."""

    async def on_loss(
        self,
        signal: Signal,
        stage: Stage,
        pnl: Decimal,
        cumulative_pnl: Decimal,
        next_stage: Stage | None,
    ) -> None:
        """FR-7.1 rows 'LOSS — initial/1st gale/2nd gale'. `next_stage` is None
        if the loss ended the cascade (e.g., gale2 loss → done_loss)."""

    async def on_signal_expired(
        self, signal: Signal, stage: Stage, trigger_hhmm: str,
    ) -> None:
        """FR-7.1 rows 'Signal expired — initial/1st gale/2nd gale'."""

    async def on_cascade_complete(
        self,
        signal: Signal,
        final_state: "TerminalState",
        cumulative_pnl: Decimal,
    ) -> None:
        """FR-7.1 row 'Cascade end (terminal)'."""

    async def on_signal_rejected_by_limit(
        self, signal: Signal, limit_type: str, summary: "DailySummaryRow",
    ) -> None:
        """FR-7.1 rows 'Daily loss/trade limit hit'. `limit_type` is 'loss',
        'count', or 'drawdown'. Fires once per rejected signal."""

    async def on_bot_started(
        self, *, mode: str, watching: str, timezone: str,
    ) -> None:
        """FR-7.1 row 'Bot startup'. Fires once per process, after all
        components are connected."""

    async def on_bot_stopping(self, *, open_cascades: int) -> None:
        """FR-7.1 row 'Bot shutdown'. Fires once per process, on SIGINT/SIGTERM."""


class NoOpNotifier:
    """Logs every method call at INFO with a structured payload. The default
    notifier for v1 (M6's wiring uses this until M7 wires TelegramDMNotifier).

    Log lines use the same payload shape M7 will write to loguru's sinks;
    the `event` key identifies the FR-7.1 row.
    """

    async def on_signal_received(self, signal: Signal) -> None:
        _log.info(
            "notify: event=signal_received signal_id=%s pair=%s direction=%s "
            "trigger=%s expiration=%ds",
            signal.signal_id, signal.pair, signal.direction,
            signal.trigger_hhmm, signal.expiration_seconds,
        )

    async def on_trade_placed(
        self, signal: Signal, stage: Stage, amount: Decimal, trade_id: str,
    ) -> None:
        _log.info(
            "notify: event=trade_placed signal_id=%s stage=%s amount=%s trade_id=%s",
            signal.signal_id, stage, amount, trade_id,
        )

    async def on_win(
        self,
        signal: Signal,
        stage: Stage,
        pnl: Decimal,
        cumulative_pnl: Decimal,
    ) -> None:
        _log.info(
            "notify: event=win signal_id=%s stage=%s pnl=%s cumulative_pnl=%s",
            signal.signal_id, stage, pnl, cumulative_pnl,
        )

    async def on_loss(
        self,
        signal: Signal,
        stage: Stage,
        pnl: Decimal,
        cumulative_pnl: Decimal,
        next_stage: Stage | None,
    ) -> None:
        _log.info(
            "notify: event=loss signal_id=%s stage=%s pnl=%s cumulative_pnl=%s "
            "next_stage=%s",
            signal.signal_id, stage, pnl, cumulative_pnl, next_stage,
        )

    async def on_signal_expired(
        self, signal: Signal, stage: Stage, trigger_hhmm: str,
    ) -> None:
        _log.info(
            "notify: event=signal_expired signal_id=%s stage=%s trigger_hhmm=%s",
            signal.signal_id, stage, trigger_hhmm,
        )

    async def on_cascade_complete(
        self,
        signal: Signal,
        final_state: "TerminalState",
        cumulative_pnl: Decimal,
    ) -> None:
        _log.info(
            "notify: event=cascade_complete signal_id=%s final_state=%s "
            "cumulative_pnl=%s",
            signal.signal_id, final_state, cumulative_pnl,
        )

    async def on_signal_rejected_by_limit(
        self, signal: Signal, limit_type: str, summary: "DailySummaryRow",
    ) -> None:
        _log.warning(
            "notify: event=signal_rejected_by_limit signal_id=%s limit_type=%s "
            "losses=%s trades=%s pnl=%s",
            signal.signal_id, limit_type,
            summary.losses, summary.trades_count, summary.realized_pnl,
        )

    async def on_bot_started(
        self, *, mode: str, watching: str, timezone: str,
    ) -> None:
        _log.info(
            "notify: event=bot_started mode=%s watching=%s timezone=%s",
            mode, watching, timezone,
        )

    async def on_bot_stopping(self, *, open_cascades: int) -> None:
        _log.info(
            "notify: event=bot_stopping open_cascades=%d",
            open_cascades,
        )
```

**Notes:**
- `runtime_checkable` lets tests `isinstance(notifier, Notifier)` for cheap Protocol checks. The `Protocol` members are all `async def` with `...` bodies, so the runtime check is structural.
- `TYPE_CHECKING` import of `TerminalState` and `DailySummaryRow` avoids an import cycle (state.py and state_store.py may transitively import the notifier module in the future). Notifier implementations need concrete imports.
- `_log` is a module-level logger; tests use `caplog` to assert log lines if needed.
- All `NoOpNotifier` payloads mirror the FR-7.1 message text fields (signal_id, pair, pnl, etc.). M7's TelegramDMNotifier will format these into the exact FR-7.1 message strings.
- `NoOpNotifier` does NOT actually implement the `Notifier` Protocol as a class (it's a duck-typed class, not a subclass). Python's Protocol with `runtime_checkable` allows structural subtyping — `isinstance(NoOpNotifier(), Notifier)` returns True because all methods are present with matching signatures.

### 4.4 `src/signal_copier/scheduler/trigger.py` (NEW)

```python
"""The scheduler and per-signal supervisor.

`Scheduler` consumes signals from the M5 listener's asyncio.Queue and spawns
one `SignalSupervisor` task per signal. Each supervisor owns its signal's
full lifecycle (initial → optional gales → terminal), invoking the M2 state
machine, the M3 broker, the M4 StateStore, and the M6 Notifier at each
transition.

Concurrency model: one Supervisor coroutine per in-flight signal. The
scheduler tracks them in a set for clean shutdown. Each supervisor runs
its full cascade (~15 minutes for 3 stages × 5min expiration) and exits.
At the analyst's typical cadence (1 signal/5min), max ~3 supervisors
in flight — bounded memory.

Schedule precision: pure asyncio.loop.call_at. No spin-loop (S-7
deferred to v1.0). Python 3.13's asyncio on Windows meets ≤500ms
precision natively (PRD NFR-1).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from decimal import Decimal
from typing import TYPE_CHECKING, cast

from signal_copier.config import Config
from signal_copier.domain.gale import Stage, amount_for_stage
from signal_copier.domain.signal import Signal
from signal_copier.domain.state import (
    FireEvent,
    ResultEvent,
    SignalState,
    StageResult,
    TerminalState,
    transition,
)
from signal_copier.infra.clock import monotonic, now_unix
from signal_copier.notify.protocol import Notifier

if TYPE_CHECKING:
    from signal_copier.broker.base import Broker, UnsupportedPairError
    from signal_copier.infra.state_store import StateStore

_log = logging.getLogger(__name__)


# StageResult-grace timeout per PRD FR-5.3: expiration_seconds + 30s.
_RESULT_GRACE_SECONDS: float = 30.0


# Stage → (signal.trigger_unix_* field name) mapping for the schedule targets.
# Kept as a module-level dict so tests can assert the mapping without
# constructing a Signal. (Alternative: derive from state.stage; the explicit
# mapping is faster and avoids attribute chains.)
_STAGE_TO_TRIGGER_ATTR: dict[Stage, str] = {
    "initial": "trigger_unix_initial",
    "gale1": "trigger_unix_gale1",
    "gale2": "trigger_unix_gale2",
}


def compute_target_monotonic(target_wall_unix: float) -> float:
    """Return the monotonic-clock target for `loop.call_at(...)`.

    Converts a wall-clock Unix epoch to monotonic time, anchored to the
    current event loop. If `target_wall_unix` is in the past, returns
    `loop.time()` so the call_at fires immediately (D-17).
    """
    now_wall = now_unix()
    now_mono = monotonic()
    delta = target_wall_unix - now_wall
    if delta <= 0:
        return now_mono
    return now_mono + delta


class Scheduler:
    """Consumes signals from the M5 listener's asyncio.Queue and spawns
    `SignalSupervisor` tasks. Runs forever until cancelled.

    Construction is sync (config + dependencies). `run()` is the single
    async entry point. Tracks active supervisor tasks for clean shutdown.
    """

    def __init__(
        self,
        *,
        queue: asyncio.Queue[Signal],
        broker: "Broker",
        state_store: "StateStore",
        notifier: Notifier,
        config: Config,
    ) -> None:
        self._queue = queue
        self._broker = broker
        self._state_store = state_store
        self._notifier = notifier
        self._config = config
        self._active_tasks: set[asyncio.Task[None]] = set()

    @property
    def active_task_count(self) -> int:
        """Number of supervisor tasks currently running (or scheduled).
        Used by __main__.on_bot_stopping for the FR-7.1 'open_cascades' field.
        """
        return len(self._active_tasks)

    async def run(self) -> None:
        """Drain the queue; spawn a SignalSupervisor per signal. Runs forever.

        On CancelledError (SIGINT from __main__), cancels all active
        supervisors and re-raises so __main__ can exit cleanly. The
        supervisors' own CancelledError handlers skip DB cleanup and exit.
        """
        try:
            while True:
                signal = await self._queue.get()
                task = asyncio.create_task(
                    self._supervise(signal),
                    name=f"supervisor-{signal.signal_id}",
                )
                self._active_tasks.add(task)
                task.add_done_callback(self._active_tasks.discard)
                self._queue.task_done()
        except asyncio.CancelledError:
            _log.info(
                "Scheduler cancelled; cancelling %d active supervisors",
                len(self._active_tasks),
            )
            for task in list(self._active_tasks):
                task.cancel()
            # Wait for all to finish (or timeout) so resources clean up.
            if self._active_tasks:
                await asyncio.gather(
                    *self._active_tasks, return_exceptions=True,
                )
            raise

    async def _supervise(self, signal: Signal) -> None:
        """Spawn a SignalSupervisor and await it. Wrapped for testability —
        tests can patch this to inject a mock supervisor.
        """
        supervisor = SignalSupervisor(
            signal=signal,
            broker=self._broker,
            state_store=self._state_store,
            notifier=self._notifier,
            config=self._config,
        )
        await supervisor.run()


class SignalSupervisor:
    """Owns one signal's full cascade: initial → gale1 → gale2 → terminal.

    Per Q4: one supervisor per signal. Lifecycle:
      1. Daily-limit check at intake.
      2. Idempotency check (get_signal).
      3. Build initial SignalState; emit on_signal_received.
      4. Schedule initial call_at; on fire, dispatch FireEvent →
         place trade → wait_result → dispatch ResultEvent →
         record_stage_result → update_signal_state.
      5. On placed_gale1/gale2: schedule next call_at; GOTO step 4.
      6. On terminal (done_win/done_loss/error): emit on_cascade_complete;
         exit.

    All exceptions are caught (D-5) except DB errors, which are re-raised
    so __main__ exits non-zero (Railway restarts).
    """

    def __init__(
        self,
        *,
        signal: Signal,
        broker: "Broker",
        state_store: "StateStore",
        notifier: Notifier,
        config: Config,
    ) -> None:
        self._signal = signal
        self._broker = broker
        self._state_store = state_store
        self._notifier = notifier
        self._config = config

    async def run(self) -> None:
        """The supervisor's main coroutine. Returns on terminal state or
        CancelledError. Never raises non-CancelledError (D-5).
        """
        try:
            await self._run_inner()
        except asyncio.CancelledError:
            _log.debug(
                "supervisor cancelled: signal_id=%s", self._signal.signal_id,
            )
            raise
        except Exception as exc:
            # Defensive: anything unexpected is logged and absorbed.
            # Real DB errors should re-raise in the inner; if they reach
            # here they were wrapped (e.g., asyncpg InternalClientError
            # inside a StateStore helper). Log at ERROR.
            _log.exception(
                "supervisor unexpected error: signal_id=%s exc=%s",
                self._signal.signal_id, exc,
            )

    async def _run_inner(self) -> None:
        # Step 1: daily-limit check.
        limit_type = await self._check_daily_limit()
        if limit_type is not None:
            await self._handle_limit_rejection(limit_type)
            return

        # Step 2: idempotency check (D-11).
        existing = await self._state_store.get_signal(self._signal.signal_id)
        if existing is not None and existing.status != "pending":
            _log.info(
                "duplicate signal at supervisor intake: signal_id=%s status=%s",
                self._signal.signal_id, existing.status,
            )
            return

        # Step 3: build initial state, emit signal_received.
        state = SignalState.from_signal(self._signal, self._config)
        await self._safe_notify(self._notifier.on_signal_received(self._signal))

        # Step 4-6: drive the cascade.
        await self._drive_cascade(state)

    async def _drive_cascade(self, initial_state: SignalState) -> None:
        """Run the cascade from `initial_state` until terminal or error.

        Each iteration:
          a. Schedule the next call_at for state.stage's trigger_unix.
          b. Wait for the call_at callback to fire (via asyncio.Future).
          c. Dispatch FireEvent to the state machine.
          d. Place the trade via broker.place().
          e. Wait for the result.
          f. Apply the result via _apply_result_and_finalize().
          g. Re-check state — if terminal, exit; otherwise loop to next stage.

        The loop body's exit is checked after each iteration by the
        `while state.stage is not None` guard at the top.
        """
        state = initial_state
        loop = asyncio.get_running_loop()

        while state.stage is not None:
            # a. Schedule the next fire.
            stage = state.stage
            target_wall = getattr(self._signal, _STAGE_TO_TRIGGER_ATTR[stage])
            target_mono = compute_target_monotonic(target_wall)

            # D-17: negative delta at intake → fire immediately with stale now.
            fired = loop.create_future()
            try:
                loop.call_at(target_mono, fired.set_result, True)
            except Exception:  # pragma: no cover — defensive
                _log.exception(
                    "call_at failed: signal_id=%s stage=%s",
                    self._signal.signal_id, stage,
                )
                return

            try:
                await fired
            except asyncio.CancelledError:
                raise

            # c. Dispatch FireEvent.
            now_wall = now_unix()
            result = transition(state, FireEvent(now_unix=now_wall), config=self._config)
            if not result.success or result.new_state is None:
                _log.error(
                    "FireEvent failed: signal_id=%s stage=%s reason=%s",
                    self._signal.signal_id, stage, result.reason,
                )
                return
            state = result.new_state

            # Persist the state transition.
            await self._state_store.update_signal_state(
                signal_id=self._signal.signal_id,
                status=state.state,
                stage=state.stage,
                error_reason=state.error_reason,
                cumulative_pnl=state.cumulative_pnl,
            )

            # If the FireEvent drove us to error, notify and exit.
            if state.state == "error":
                await self._safe_notify(
                    self._notifier.on_signal_expired(
                        self._signal, stage=stage,
                        trigger_hhmm=self._signal.trigger_hhmm,
                    )
                )
                await self._safe_notify(
                    self._notifier.on_cascade_complete(
                        self._signal,
                        final_state="error",
                        cumulative_pnl=state.cumulative_pnl,
                    )
                )
                return

            # d. Place the trade. Capture amount BEFORE place() — after the
            # ResultEvent transition, terminal states have amount=Decimal("0").
            placed_amount = state.amount
            try:
                trade_id = await self._broker.place(
                    self._signal, stage=state.stage, amount=placed_amount,
                )
            except UnsupportedPairError as exc:
                _log.warning(
                    "broker rejected pair: signal_id=%s pair=%s exc=%s",
                    self._signal.signal_id, self._signal.pair, exc,
                )
                # D-4: translate broker exception into state machine's
                # vocabulary (ResultEvent("error")). No trade_id exists
                # (place() raised before returning one), so the helper
                # skips record_stage_result. The cascade ends uniformly
                # with error (broker_unavailable).
                await self._apply_error_transition(
                    state, stage, "error", placed_amount, trade_id=None,
                )
                return

            await self._state_store.record_stage_placed(
                signal_id=self._signal.signal_id,
                stage=stage,
                trade_id=trade_id,
                placed_at_unix=now_unix(),
                expires_at_unix=state.expires_at_unix,
                pair=self._signal.pair,
                direction=self._signal.direction,
                amount=placed_amount,
            )
            await self._safe_notify(
                self._notifier.on_trade_placed(
                    self._signal, stage=stage, amount=placed_amount,
                    trade_id=trade_id,
                )
            )

            # e. Wait for the result.
            stage_result = await self._wait_for_stage_result(trade_id, state)

            # f. Apply the result; returns the new (possibly terminal) state.
            state = await self._apply_result_and_finalize(
                state, stage, stage_result, placed_amount, trade_id,
            )
            # While-loop guard re-checks state.stage next iteration; if the
            # state is terminal (done_win/done_loss/error) the loop exits.

    async def _apply_result_and_finalize(
        self,
        state: SignalState,
        stage: Stage,
        stage_result: StageResult,
        placed_amount: Decimal,
        trade_id: str,
    ) -> SignalState:
        """Dispatch a ResultEvent to the state machine; persist + notify.

        Returns the new (possibly terminal) SignalState. The caller uses
        the return value to update its loop variable and re-check the
        `while state.stage is not None` guard.

        Side effects (in order):
          1. transition(state, ResultEvent(...)) → new state.
          2. record_stage_result(trade_id, result=stage_result, pnl=...)
          3. update_signal_state(... new state's status/stage/pnl ...).
          4. update_daily_summary(...) (D-3: after every stage result).
          5. Emit per-result notification (on_win / on_loss / on_cascade_complete).
        """
        now_wall = now_unix()
        result = transition(
            state, ResultEvent(result=stage_result, now_unix=now_wall),
            config=self._config,
        )
        if not result.success or result.new_state is None:
            _log.error(
                "ResultEvent failed: signal_id=%s stage=%s result=%s reason=%s",
                self._signal.signal_id, stage, stage_result, result.reason,
            )
            return state  # unchanged; caller exits

        new_state = result.new_state

        # Persist stage result + state transition + daily summary.
        await self._state_store.record_stage_result(
            trade_id=trade_id,
            result=stage_result,
            closed_at_unix=now_wall,
            pnl=self._compute_stage_pnl_for_result(stage_result, placed_amount),
        )
        await self._state_store.update_signal_state(
            signal_id=self._signal.signal_id,
            status=new_state.state,
            stage=new_state.stage,
            error_reason=new_state.error_reason,
            cumulative_pnl=new_state.cumulative_pnl,
        )
        await self._state_store.update_daily_summary(
            on_date=self._signal_date(),
            deltas=self._daily_deltas_for(stage_result, placed_amount),
        )

        # Emit per-result notification, then on_cascade_complete if terminal.
        if stage_result == "win":
            await self._safe_notify(
                self._notifier.on_win(
                    self._signal, stage=stage,
                    pnl=self._compute_stage_pnl_for_result(stage_result, placed_amount),
                    cumulative_pnl=new_state.cumulative_pnl,
                )
            )
        elif stage_result in {"loss", "tie", "timeout"}:
            await self._safe_notify(
                self._notifier.on_loss(
                    self._signal, stage=stage,
                    pnl=-placed_amount,
                    cumulative_pnl=new_state.cumulative_pnl,
                    next_stage=new_state.stage,  # None if terminal
                )
            )
        # stage_result == "error" → on_cascade_complete is the only notify.

        if new_state.state in {"done_win", "done_loss", "done_tie", "error"}:
            await self._safe_notify(
                self._notifier.on_cascade_complete(
                    self._signal,
                    final_state=cast(TerminalState, new_state.state),
                    cumulative_pnl=new_state.cumulative_pnl,
                )
            )

        return new_state

    async def _apply_error_transition(
        self,
        state: SignalState,
        stage: Stage,
        stage_result: StageResult,
        placed_amount: Decimal,
        *,
        trade_id: str | None,
    ) -> SignalState:
        """Variant of _apply_result_and_finalize for the no-trade_id path.

        Used when broker.place() raises (e.g., UnsupportedPairError) — the
        state machine needs to be told via ResultEvent("error"), but no
        stage row was written (no trade_id). Same persistence and
        notification as the main path, minus record_stage_result.
        """
        now_wall = now_unix()
        result = transition(
            state, ResultEvent(result=stage_result, now_unix=now_wall),
            config=self._config,
        )
        if not result.success or result.new_state is None:
            _log.error(
                "ResultEvent (error path) failed: signal_id=%s stage=%s reason=%s",
                self._signal.signal_id, stage, result.reason,
            )
            return state

        new_state = result.new_state

        if trade_id is not None:
            await self._state_store.record_stage_result(
                trade_id=trade_id,
                result=stage_result,
                closed_at_unix=now_wall,
                pnl=self._compute_stage_pnl_for_result(stage_result, placed_amount),
            )
        # If trade_id is None (UnsupportedPairError), no stage row was ever
        # written, so no record_stage_result is needed. The signal-level
        # state still gets updated to error.
        await self._state_store.update_signal_state(
            signal_id=self._signal.signal_id,
            status=new_state.state,
            stage=new_state.stage,
            error_reason=new_state.error_reason,
            cumulative_pnl=new_state.cumulative_pnl,
        )
        await self._safe_notify(
            self._notifier.on_cascade_complete(
                self._signal,
                final_state=cast(TerminalState, new_state.state),
                cumulative_pnl=new_state.cumulative_pnl,
            )
        )
        return new_state

    async def _wait_for_stage_result(
        self, trade_id: str, state: SignalState,
    ) -> StageResult:
        """Wrap broker.wait_result in asyncio.wait_for with the FR-5.3 timeout.

        On TimeoutError: return 'timeout' (treated as loss-equivalent by the
        state machine). On any other broker exception: return 'error'
        (state machine ends the cascade with broker_unavailable).
        """
        timeout = max(
            0.1,
            state.expires_at_unix - now_unix() + _RESULT_GRACE_SECONDS,
        )
        try:
            return await asyncio.wait_for(
                self._broker.wait_result(trade_id, timeout=timeout),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            _log.warning(
                "broker.wait_result timeout: trade_id=%s timeout=%.1fs",
                trade_id, timeout,
            )
            return "timeout"
        except Exception as exc:  # noqa: BLE001 — map to error per D-5
            _log.warning(
                "broker.wait_result error: trade_id=%s exc=%s",
                trade_id, exc,
            )
            return "error"

    async def _check_daily_limit(self) -> str | None:
        """Return 'loss' | 'count' | 'drawdown' if a daily limit is hit;
        None if all clear (FR-6.1/6.2/6.3). 0 = disabled (D-3).

        M6 simplification: `daily_drawdown_pct` is treated as a USD threshold
        (not a percentage of starting balance). The full percentage-of-balance
        computation requires fetching the broker balance at startup, which is
        M8 territory (the real OlympTradeBroker.balance() call). M8 will fix
        the semantics to: halt if realized_pnl <= -(balance * pct / 100).
        For M6 the config is documented as "halt at this USD loss" in
        `.env.example` (M6 adds the comment).
        """
        summary = await self._state_store.get_daily_summary(self._signal_date())
        # No summary row yet → no trades today → no limit hit.
        if summary is None:
            return None

        cfg = self._config
        if cfg.daily_loss_limit > 0 and summary.realized_pnl <= -cfg.daily_loss_limit:
            return "loss"
        if cfg.daily_trade_limit > 0 and summary.trades_count >= cfg.daily_trade_limit:
            return "count"
        if (
            cfg.daily_drawdown_pct > 0
            and summary.realized_pnl <= -cfg.daily_drawdown_pct
        ):
            return "drawdown"
        return None

    async def _handle_limit_rejection(self, limit_type: str) -> None:
        """Mark the signal 'error (daily_limit_hit)' and emit notification."""
        from signal_copier.infra.state_store import DailySummaryRow

        await self._state_store.update_signal_state(
            signal_id=self._signal.signal_id,
            status="error",
            stage=None,
            error_reason="daily_limit_hit",
            cumulative_pnl=Decimal("0.00"),
        )
        summary = await self._state_store.get_daily_summary(self._signal_date())
        if summary is None:
            summary = DailySummaryRow(
                date=self._signal_date(), signals_count=0, trades_count=0,
                wins=0, losses=0, realized_pnl=Decimal("0.00"),
                limit_hit=limit_type,
            )
        await self._safe_notify(
            self._notifier.on_signal_rejected_by_limit(
                self._signal, limit_type=limit_type, summary=summary,
            )
        )

    def _signal_date(self) -> date:
        """The signal's date in the configured timezone (matches M5's derivation)."""
        from datetime import datetime

        return datetime.fromtimestamp(
            self._signal.trigger_unix_initial,
            tz=self._config.tz(),
        ).date()

    async def _safe_notify(self, coro: Awaitable[None]) -> None:
        """Await a notifier call; absorb exceptions (D-5: notifier errors
        must not abort a cascade)."""
        try:
            await coro
        except Exception as exc:  # noqa: BLE001 — defensive isolation
            _log.warning(
                "notifier raised, continuing: exc=%s", exc,
            )

    def _compute_stage_pnl_for_result(
        self, result: StageResult, amount: Decimal,
    ) -> Decimal:
        """Mirror state.py's _stage_pnl — duplicated here so M6's DB writes
        don't depend on importing state machine internals. Matches the
        v1 approximation (92% payout for win; full loss for loss/tie/timeout).
        M8 will replace with broker-reported PnL.
        """
        if result == "win":
            return amount * Decimal("0.92")
        if result in {"loss", "tie", "timeout"}:
            return -amount
        return Decimal("0.00")  # 'error' contributes nothing

    def _daily_deltas_for(
        self, result: StageResult, placed_amount: Decimal,
    ) -> dict[str, int | Decimal]:
        """Build the deltas dict for StateStore.update_daily_summary (D-3).

        `placed_amount` is the dollar amount of the just-completed stage's
        bet. Used to compute realized_pnl: +0.92*amount for win, -amount
        for loss/tie/timeout. `error` results contribute nothing.
        """
        deltas: dict[str, int | Decimal] = {
            "trades_count": 1,
            "signals_count": 0,
            "realized_pnl": Decimal("0.00"),
            "wins": 0,
            "losses": 0,
        }
        if result == "win":
            deltas["wins"] = 1
            deltas["realized_pnl"] = placed_amount * Decimal("0.92")
        elif result in {"loss", "tie", "timeout"}:
            deltas["losses"] = 1
            deltas["realized_pnl"] = -placed_amount
        return deltas
```

**Notes:**
- The `_drive_cascade` loop is the heart of M6. The state machine's transitions are pure (M2 D-1); M6 owns the side effects (broker calls, DB writes, notifications).
- `_STAGE_TO_TRIGGER_ATTR` is a small indirection — could be replaced by direct attribute access (`signal.trigger_unix_gale1`), but the dict form lets tests assert the mapping and makes future changes (e.g., a 4th stage) one-line.
- The `loop.call_at(target_mono, fired.set_result, True)` pattern uses a Future as a one-shot wake-up. `call_at` schedules the callback; the callback resolves the Future; the supervisor `await`s it.
- `_RESULT_GRACE_SECONDS = 30.0` per PRD FR-5.3.
- `cast(TerminalState, state.state)` is needed because `state.state` is typed as `AllStates` (a union of pre-terminal + terminal); after the terminal check at line `if new_state.state in {"done_win", ...}` the type checker doesn't narrow without a cast.
- `UnsupportedPairError` is imported under `TYPE_CHECKING` (in the broker.base conditional block) so it can be referenced in the except clause without a runtime import.
- `_apply_error_transition` is the no-trade-id variant for the UnsupportedPairError path (D-4). It does the same persistence + notification as `_apply_result_and_finalize` minus `record_stage_result`, because no stage row was ever written.
- **Daily drawdown simplification (D-2 note):** M6 treats `DAILY_DRAWDOWN_PCT` as a USD threshold (`pnl <= -pct`), not a percentage of starting balance. The starting-balance fetch is M8 territory (broker.balance() at startup). M6's `.env.example` will document this. M8 fixes the semantics.
- `_safe_notify` wraps every notifier call. A Telegram DM failure must not abort the cascade.
- `_compute_stage_pnl_for_result` duplicates state.py's `_stage_pnl`. The duplication is intentional: state.py's helper is private (`_stage_pnl`); M6's public call computes the same value for DB writes. Both will be replaced by broker-reported PnL in M8.
- `_daily_deltas_for` takes `placed_amount` directly (not derived from stage) — the just-completed stage's amount is the authoritative number.

### 4.5 `src/signal_copier/domain/state.py` (MODIFY — D-2)

The only change is to the `ErrorReason` literal on lines 42–46:

```python
ErrorReason = Literal[
    "signal_expired",  # FR-3.3 / FR-3.5 / FR-3.6 / FR-5.9: time window passed
    "broker_unavailable",  # FR-4.4: broker dropped / token expired
    "daily_limit_hit",  # M6 D-2: DAILY_LOSS_LIMIT/TRADE_LIMIT/DRAWDOWN_PCT tripped
    "unknown",
]
```

No other changes to `state.py`. The new literal is consumed by the supervisor's `_handle_limit_rejection` and stored in `signals.error_reason` (TEXT column, no CHECK constraint to update).

### 4.6 `src/signal_copier/__main__.py` (MODIFY)

```python
from __future__ import annotations

import asyncio
import sys

from pydantic import ValidationError

from signal_copier.broker.base import Broker
from signal_copier.broker.dry_run import DryRunBroker
from signal_copier.config import Config
from signal_copier.infra.db import Database, DatabaseConnectionError
from signal_copier.infra.log import setup_logging, setup_parse_failures_log
from signal_copier.notify.protocol import NoOpNotifier
from signal_copier.scheduler.trigger import Scheduler
from signal_copier.telegram.client import TelegramClient, TelegramConfigError
from signal_copier.telegram.listener import Listener

# Bounded as a safety net (M5 D-1). M6's Scheduler drains immediately;
# the cap is never hit at the analyst's typical 1 signal/5min cadence.
_SIGNALS_QUEUE_MAXSIZE: int = 1000


async def _run(config: Config) -> int:
    """Async main: wire up the pipeline and run until cancelled or fatal error."""
    db: Database | None = None
    tg: TelegramClient | None = None
    scheduler: Scheduler | None = None
    scheduler_task: asyncio.Task[None] | None = None
    telegram_task: asyncio.Task[None] | None = None
    notifier = NoOpNotifier()
    broker: Broker | None = None
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

        # Build the broker. M6 uses DryRunBroker unconditionally;
        # M8 will add the DRY_RUN=false → OlympTradeBroker branch.
        broker = DryRunBroker()
        await broker.connect()

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

        scheduler = Scheduler(
            queue=signals_queue,
            broker=broker,
            state_store=db.state_store,
            notifier=notifier,
            config=config,
        )

        await notifier.on_bot_started(
            mode="dry_run" if config.dry_run else "live demo",
            watching=config.telegram_target_chat,
            timezone=config.timezone,
        )

        print(
            f"🟢 signal_copier M6 started\n"
            f"   Mode: {'dry_run' if config.dry_run else 'live demo'}\n"
            f"   Timezone: {config.timezone}\n"
            f"   Target chat: {config.telegram_target_chat} (chat_id={tg.target_chat_id})\n"
            f"   Watching for new messages and edits...\n"
        )

        # Both run forever; either cancelling will trigger cleanup.
        scheduler_task = asyncio.create_task(scheduler.run(), name="scheduler")
        telegram_task = asyncio.create_task(tg.start(), name="telegram")

        # Wait for either to finish (clean exit) or raise (error).
        done, pending = await asyncio.wait(
            {scheduler_task, telegram_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in done:
            if exc := task.exception():
                raise exc
        return 0
    finally:
        for task in (scheduler_task, telegram_task):
            if task is not None and not task.done():
                task.cancel()
        for task in (scheduler_task, telegram_task):
            if task is not None:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        if scheduler is not None:
            await notifier.on_bot_stopping(
                open_cascades=scheduler.active_task_count,
            )
        if broker is not None:
            await broker.close()
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

**Notes:**
- The M5 `dump_consumer` is gone — replaced by `Scheduler.run()`. `dump_task` is removed.
- `broker` is built conditionally (`DryRunBroker` for M6); M8 will add the `OlympTradeBroker` branch on `config.dry_run`.
- The `asyncio.wait(...)` with `FIRST_COMPLETED` lets either the scheduler or Telegram client terminate the run. If Telegram disconnects (M5 raises), the scheduler is cancelled; if the scheduler raises (unlikely), Telegram is cancelled. This matches M5's "either component can exit the process" pattern.
- `on_bot_stopping` fires before broker/tg/db close, so the notification can reference the open-cascade count.
- The `if exc := task.exception()` pattern (walrus operator) re-raises the first completed task's exception to surface it in the error handler.

### 4.7 `tests/_scheduler_fixtures.py` (NEW)

```python
"""Shared test fixtures for M6's scheduler tests.

Helpers:
  - FakeBroker: drop-in replacement for the Broker Protocol with programmable
    per-stage outcomes.
  - RecordingNotifier: drop-in replacement for Notifier that collects calls.
  - make_signal_with_future_trigger(seconds): build a Signal whose initial
    trigger is `seconds` from now. Used for sub-second skew tests.
  - assert_within_skew(actual, target, max_ms): assert the difference is
    within `max_ms` milliseconds.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from collections.abc import Awaitable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from signal_copier.broker.base import Broker, UnsupportedPairError
from signal_copier.domain.gale import Stage, amount_for_stage
from signal_copier.domain.signal import Signal
from signal_copier.domain.state import StageResult, TerminalState
from signal_copier.infra.state_store import DailySummaryRow
from signal_copier.notify.protocol import Notifier


@dataclass(slots=True)
class FakeBroker(Broker):
    """Records every place() and wait_result() call. Outcomes are programmable
    per-stage via `program_outcomes` dict. Unknown stages use the default
    (win). Supports 'unsupported_pair' injection for error-path tests.
    """

    program_outcomes: dict[Stage, StageResult] = field(default_factory=dict)
    default_outcome: StageResult = "win"
    force_unsupported_pair: bool = False

    place_calls: list[tuple[Signal, Stage, Decimal]] = field(default_factory=list)
    wait_result_calls: list[tuple[str, float]] = field(default_factory=list)
    _placed: dict[str, tuple[Signal, Stage]] = field(default_factory=dict)

    async def connect(self) -> None:
        return None

    async def place(
        self, signal: Signal, *, stage: Stage, amount: Decimal,
    ) -> str:
        self.place_calls.append((signal, stage, amount))
        if self.force_unsupported_pair:
            raise UnsupportedPairError(
                f"{signal.pair} not available on this broker"
            )
        trade_id = f"fake-{signal.signal_id}-{stage}"
        self._placed[trade_id] = (signal, stage)
        return trade_id

    async def wait_result(
        self, trade_id: str, *, timeout: float,
    ) -> StageResult:
        self.wait_result_calls.append((trade_id, timeout))
        await asyncio.sleep(0)  # yield to event loop
        signal, stage = self._placed[trade_id]
        return self.program_outcomes.get(stage, self.default_outcome)

    async def close(self) -> None:
        return None


@dataclass(slots=True)
class RecordingNotifier(Notifier):
    """Collects every notifier method call as a (method_name, kwargs_dict) tuple.

    Tests assert against `self.calls`. Raises injected exceptions via the
    `raise_on` field — e.g., `raise_on = {"on_bot_started": RuntimeError("x")}`
    causes the next on_bot_started call to raise.
    """

    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    raise_on: dict[str, BaseException] = field(default_factory=dict)

    async def _record(self, method: str, **kwargs: Any) -> None:
        self.calls.append((method, kwargs))
        if method in self.raise_on:
            raise self.raise_on[method]

    async def on_signal_received(self, signal: Signal) -> None:
        await self._record("on_signal_received", signal=signal)

    async def on_trade_placed(
        self, signal: Signal, stage: Stage, amount: Decimal, trade_id: str,
    ) -> None:
        await self._record(
            "on_trade_placed", signal=signal, stage=stage,
            amount=amount, trade_id=trade_id,
        )

    async def on_win(
        self, signal: Signal, stage: Stage, pnl: Decimal, cumulative_pnl: Decimal,
    ) -> None:
        await self._record(
            "on_win", signal=signal, stage=stage, pnl=pnl,
            cumulative_pnl=cumulative_pnl,
        )

    async def on_loss(
        self, signal: Signal, stage: Stage, pnl: Decimal,
        cumulative_pnl: Decimal, next_stage: Stage | None,
    ) -> None:
        await self._record(
            "on_loss", signal=signal, stage=stage, pnl=pnl,
            cumulative_pnl=cumulative_pnl, next_stage=next_stage,
        )

    async def on_signal_expired(
        self, signal: Signal, stage: Stage, trigger_hhmm: str,
    ) -> None:
        await self._record(
            "on_signal_expired", signal=signal, stage=stage, trigger_hhmm=trigger_hhmm,
        )

    async def on_cascade_complete(
        self, signal: Signal, final_state: TerminalState, cumulative_pnl: Decimal,
    ) -> None:
        await self._record(
            "on_cascade_complete", signal=signal, final_state=final_state,
            cumulative_pnl=cumulative_pnl,
        )

    async def on_signal_rejected_by_limit(
        self, signal: Signal, limit_type: str, summary: DailySummaryRow,
    ) -> None:
        await self._record(
            "on_signal_rejected_by_limit", signal=signal, limit_type=limit_type,
            summary=summary,
        )

    async def on_bot_started(
        self, *, mode: str, watching: str, timezone: str,
    ) -> None:
        await self._record(
            "on_bot_started", mode=mode, watching=watching, timezone=timezone,
        )

    async def on_bot_stopping(self, *, open_cascades: int) -> None:
        await self._record(
            "on_bot_stopping", open_cascades=open_cascades,
        )


def make_signal_with_future_trigger(
    *,
    trigger_in_seconds: float,
    signal_id: str = "test-sig-1",
    pair: str = "EUR/JPY",
    direction: str = "down",
) -> Signal:
    """Build a Signal whose initial trigger is `trigger_in_seconds` from now.

    Gale triggers are computed arithmetically (initial + 5min, +10min).
    Used by tests to drive sub-second scheduling and the cascade.
    """
    now = time.time()
    trigger_initial = now + trigger_in_seconds
    expiration = 300
    return Signal(
        signal_id=signal_id,
        pair=pair,
        direction=direction,
        trigger_hhmm="00:00",  # unused in tests; only trigger_unix_* matters
        expiration_seconds=expiration,
        received_at_unix=now,
        source_message_id=1,
        source_chat_id=1,
        raw_text="(test)",
        trigger_unix_initial=trigger_initial,
        trigger_unix_gale1=trigger_initial + expiration,
        trigger_unix_gale2=trigger_initial + 2 * expiration,
    )


def assert_within_skew(
    actual_unix: float, target_unix: float, *, max_skew_ms: float = 500.0,
) -> None:
    """Assert that `actual_unix` is within `max_skew_ms` of `target_unix`.

    The M6 deliverable is "fires a (dry-run) trade at HH:MM with ≤500ms skew."
    Tests call this with the recorded broker.place() time vs. the signal's
    trigger_unix_initial.
    """
    skew_ms = abs(actual_unix - target_unix) * 1000.0
    assert skew_ms <= max_skew_ms, (
        f"skew {skew_ms:.1f}ms exceeds {max_skew_ms}ms "
        f"(actual={actual_unix:.3f}, target={target_unix:.3f})"
    )
```

**Notes:**
- `FakeBroker` is structurally compatible with the `Broker` Protocol (D-9). Tests use `isinstance(broker, Broker)` if they need to assert.
- `RecordingNotifier` is also structurally compatible with `Notifier` (`@runtime_checkable` allows it).
- `make_signal_with_future_trigger` is the test signal source (Q3 confirmed: synthetic injection).
- `assert_within_skew` is the assertion helper for the M6 deliverable.

### 4.8 `tests/test_scheduler.py` (NEW, ~17 tests)

Test list (each one async):

1. `test_scheduler_drains_queue_and_spawns_supervisor` — push one signal, assert one supervisor task tracked.
2. `test_supervisor_initial_win_terminal` — program `default_outcome='win'`, assert broker.place() called once with stage='initial' and amount=$2, signal state ends in 'done_win', cumulative_pnl > 0.
3. `test_supervisor_full_cascade_initial_loss_gale1_loss_gale2_win` — program outcomes, assert all 3 broker.place() calls in order, terminal 'done_win', cumulative_pnl = +$3.36 (approximately).
4. `test_supervisor_initial_loss_gale1_win` — program gale1='win', assert gale2 NOT placed, terminal 'done_win'.
5. `test_supervisor_initial_loss_gale1_loss_gale2_loss` — all losses, terminal 'done_loss', cumulative_pnl = -$14.
6. `test_initial_within_500ms_skew` — push signal with trigger in 200ms, run for ~600ms, assert broker.place() time ≤ trigger_unix_initial + 500ms.
7. `test_initial_signal_expired_at_fire_time` — push signal with trigger in -5s (already past), run, assert signal status='error' with error_reason='signal_expired', broker.place() NOT called, `on_signal_expired` fired.
8. `test_gale1_signal_expired_after_initial_loss` — program initial='loss', gale1 trigger already past, assert status='error' (signal_expired), gale2 NOT scheduled, cascade ends.
9. `test_unsupported_pair_error` — set `FakeBroker.force_unsupported_pair=True`, assert status='error' (broker_unavailable per state machine), `on_cascade_complete` fired.
10. `test_wait_result_timeout_treated_as_loss` — patch FakeBroker.wait_result to `asyncio.sleep(timeout+1)`, assert result='timeout', state machine maps to gale1.
11. `test_wait_result_exception_treated_as_error` — patch FakeBroker.wait_result to raise, assert result='error', status='error'.
12. `test_daily_loss_limit_halts_signal` — pre-populate daily_summary with realized_pnl=-$50, DAILY_LOSS_LIMIT=$50, assert signal marked 'error' (daily_limit_hit), broker.place() NOT called, `on_signal_rejected_by_limit` fired.
13. `test_daily_trade_limit_halts_signal` — pre-populate trades_count=N with N >= limit, assert same behavior.
14. `test_daily_drawdown_limit_halts_signal` — pre-populate realized_pnl=-$50, DAILY_DRAWDOWN_PCT=50 (USD simplification D-2), assert same behavior.
15. `test_duplicate_signal_at_supervisor_intake` — pre-populate signals.status='placed_initial' for the signal's id, assert supervisor exits without placing.
16. `test_notifier_exception_does_not_abort_cascade` — RecordingNotifier raises on `on_trade_placed`, assert broker.wait_result still called and cascade completes.
17. `test_scheduler_cancellation_cancels_supervisors` — start scheduler, push a signal, cancel scheduler task, assert supervisors are cancelled.

### 4.9 `tests/test_notifier.py` (NEW, ~6 tests)

1. `test_noop_notifier_logs_signal_received` — call `NoOpNotifier().on_signal_received(signal)`, assert log line at INFO with expected payload.
2. `test_noop_notifier_logs_trade_placed` — similar for `on_trade_placed`.
3. `test_noop_notifier_logs_loss_with_next_stage` — assert next_stage field is logged.
4. `test_recording_notifier_collects_calls` — push 3 method calls, assert `calls` list has 3 entries in order.
5. `test_recording_notifier_raise_on` — set `raise_on={'on_win': RuntimeError("x")}`, call `on_win`, assert RuntimeError propagates (callers like `_safe_notify` will absorb it).
6. `test_protocol_isinstance_check` — `isinstance(NoOpNotifier(), Notifier)` returns True; `isinstance(object(), Notifier)` returns False.

### 4.10 `tests/test_main.py` (MODIFY)

Add 3 tests to the M5 test file:

1. `test_main_no_dump_consumer_in_m6` — assert `__main__._run` does not create a `_build_dump_consumer` task (the M5 implementation).
2. `test_main_starts_scheduler` — assert `__main__._run` creates an `asyncio.Task` named "scheduler" running `Scheduler.run()`.
3. `test_main_emits_bot_started_and_stopping` — assert `notifier.on_bot_started` called once after wiring, `notifier.on_bot_stopping` called on cleanup with `open_cascades=N`.

---

## 5. Dependency Changes

### 5.1 `pyproject.toml` modifications

**a. `dependencies` (runtime):** **No new runtime dependencies for M6.** All M6 code uses stdlib (`asyncio`, `logging`, `time`, `datetime`, `decimal`) + already-imported modules (`signal_copier.config`, `signal_copier.domain.*`, `signal_copier.infra.*`, `signal_copier.broker.*`, `signal_copier.notify.protocol`).

**b. `project.scripts` (entry points):** No new entry points (the `signal-copier` and `signal-copier-auth` scripts are unchanged from M5).

**c. `mypy` overrides (add M6 test modules):**

```toml
[[tool.mypy.overrides]]
module = [
    "test_config", "test_db", "test_gale_math", "test_main", "test_parser",
    "test_state_machine",
    "test_clock", "test_telegram_listener", "test_telegram_client",
    "test_scheduler", "test_notifier",  # M6: NEW
]
ignore_errors = true
```

**d. `pytest` config:** No changes (`asyncio_mode = "auto"` from M0 remains).

---

## 6. Error Handling Matrix (M6-specific)

| Failure | M6 behavior |
|---|---|
| `broker.place()` raises `UnsupportedPairError` | Caught in `_place_for_stage`; re-raised. Caller (`_drive_cascade`) catches and translates to `ResultEvent("error")` → state machine → `error` (per state.py:276–277). `on_cascade_complete` fires. |
| `broker.wait_result` raises any exception (broker disconnect, token expired) | Caught in `_wait_for_stage_result`; returned as `StageResult("error")`. State machine ends cascade with `broker_unavailable`. |
| `asyncio.wait_for(broker.wait_result, timeout)` raises `asyncio.TimeoutError` | Caught in `_wait_for_stage_result`; returned as `StageResult("timeout")`. State machine treats as loss-equivalent (state.py:263); gale cascade proceeds or terminal as appropriate. |
| `state_store.*` raises (DB transient error) | Re-raised by supervisor's `_run_inner` → caught by `run()`'s outer handler → logged at ERROR. Railway restart policy kicks in. (D-5: DB errors are real problems.) |
| Notifier raises | Caught in `_safe_notify`; logged at WARNING; cascade continues. DM failure must not abort trading. (D-5.) |
| `signal.trigger_unix_initial` already past at intake | `compute_target_monotonic` returns `loop.time()`; `call_at` fires immediately. State machine's pre-fire guard catches the expired state → `error (signal_expired)`. (D-17, FR-3.3.) |
| Pre-fire guard at fire-time: wall_now − trigger_unix > tolerance | Handled by state machine's `_check_time_window` (state.py:128–138, 311–314). M6 does not duplicate the check (D-1). |
| `state_store.get_signal(signal_id)` returns non-`pending` | Supervisor logs "duplicate signal at intake" and returns. M5's `upsert_signal` + `ON CONFLICT DO NOTHING` is the primary dedup; this is belt-and-suspenders. (D-11.) |
| Daily limit hit at intake (`daily_loss_limit > 0` and realized_pnl ≤ −limit) | `_check_daily_limit` returns `'loss'`. `_handle_limit_rejection` marks signal `error (daily_limit_hit)` and fires `on_signal_rejected_by_limit`. Broker is NOT called. (D-2, FR-6.1/6.2/6.3.) |
| Daily limit hit at intake (`daily_trade_limit > 0` and trades_count ≥ limit) | Same as above with `'count'`. |
| Daily limit hit at intake (`daily_drawdown_pct > 0` and pnl ≤ −pct) | Same as above with `'drawdown'`. **M6 simplification:** `pct` is treated as USD (D-2 note). M8 fixes the semantics. |
| `asyncio.CancelledError` (scheduler shutdown) | `Scheduler.run()` catches, cancels all active supervisors, awaits them with `return_exceptions=True`, re-raises. Each supervisor's `_run_inner` lets CancelledError propagate (no DB cleanup). |
| Supervisor's `loop.call_at` fails (defensive) | Logged at ERROR; supervisor exits. (Should not happen — call_at is robust. Branch is defensive.) |
| `transition(...)` returns `success=False` (invalid event for state) | Logged at ERROR with reason; supervisor exits. Indicates a bug in M6's event ordering. |

---

## 7. Test Strategy

| Test file | ~tests | Coverage focus |
|---|---|---|
| `tests/test_scheduler.py` | 17 | `Scheduler.run()` queue draining; `SignalSupervisor` happy paths (initial-win, full cascade, gale1-only); expiration at intake and at gale fire; pre-fire guard; daily limits (3 variants); idempotency; broker errors (UnsupportedPairError, wait_result exception, timeout); notifier isolation; cancellation propagation |
| `tests/test_notifier.py` | 6 | NoOpNotifier log payloads; RecordingNotifier collection; raise_on injection; Protocol runtime check |
| `tests/test_main.py` (MODIFY) | +3 | M6 wiring: dump_consumer gone, scheduler running, bot-started/stopping notifications |
| `tests/test_dry_run_broker.py` (unchanged) | (M3) | No M6 changes; M6 calls DryRunBroker as-is |
| `tests/test_state_machine.py` (unchanged) | (M2) | No M6 changes; M6 calls state machine as-is |
| `tests/test_db.py` (unchanged) | (M4) | No M6 changes; M6 calls StateStore methods as-is |

**Sub-second skew test pattern (test #6):**

```python
async def test_initial_within_500ms_skew(scheduler_with_fakes):
    signal = make_signal_with_future_trigger(trigger_in_seconds=0.2)
    scheduler = scheduler_with_fakes(broker=FakeBroker(default_outcome="win"))
    asyncio.create_task(scheduler.run())
    await scheduler.queue.put(signal)
    await asyncio.sleep(0.6)  # let initial fire and complete

    assert len(scheduler.broker.place_calls) == 1
    place_signal, place_stage, _ = scheduler.broker.place_calls[0]
    assert place_stage == "initial"
    # Skew = (recorded place_time) - (signal.trigger_unix_initial)
    # We record place_time by patching FakeBroker.place to capture time.time().
    place_time = scheduler.broker.place_times[0]
    assert_within_skew(place_time, signal.trigger_unix_initial, max_skew_ms=500.0)
```

**Integration-style test (test #3 full cascade):**

```python
async def test_supervisor_full_cascade(scheduler_with_fakes):
    signal = make_signal_with_future_trigger(trigger_in_seconds=0.1)
    broker = FakeBroker(program_outcomes={
        "initial": "loss", "gale1": "loss", "gale2": "win",
    })
    scheduler = scheduler_with_fakes(broker=broker)
    asyncio.create_task(scheduler.run())
    await scheduler.queue.put(signal)
    await asyncio.sleep(2.0)  # ~15min cascade compressed via short expirations

    stages_placed = [s for _, s, _ in broker.place_calls]
    assert stages_placed == ["initial", "gale1", "gale2"]
    final_signal_state = scheduler.state_store.get_signal(signal.signal_id)
    assert final_signal_state.status == "done_win"
```

(The plan will refine the exact test helpers and durations.)

**Coverage target:** ≥90% line coverage on `scheduler/trigger.py` and `notify/protocol.py`. The state machine (`domain/state.py`) and StateStore (`infra/state_store.py`) are M2/M4 territory; M6 tests don't need to cover them again.

---

## 8. Open Items for the Implementation Plan

1. **`amount_for_stage` usage**: the spec's `_drive_cascade` uses `placed_amount` (captured from `state.amount` before the ResultEvent). The plan can additionally use `amount_for_stage(stage, config)` from `domain/gale.py` as a cross-check, but `placed_amount` is the authoritative value (it's what the broker was actually told). Plan should use `placed_amount` everywhere.
2. **`FakeBroker.place_times` field**: add to the fixture for the skew assertion (test #6 in `test_scheduler.py`).
3. **`Scheduler.queue` property**: expose for tests to push signals without going through `run()`.
4. **Exact test durations**: the plan will calibrate sub-second timings (200ms initial, 200ms gale1, 200ms gale2) so the full cascade test runs in ~1 second.
5. **CI timing tolerance**: 500ms target on local Windows is comfortable; CI Linux may need 800–1000ms tolerance to avoid flake. Plan will set the assertion to 800ms (NFR-1 leeway) and document.
6. **`_signal_date()` returns a date in the configured timezone** — the plan should verify this matches M5's derivation (which uses `signal_date_in_tz` from `infra/clock.py`). The plan may choose to use `signal_date_in_tz(self._signal.trigger_unix_initial, self._config.tz())` directly to avoid duplicating the helper.
7. **`.env.example` documentation**: add a comment to `DAILY_DRAWDOWN_PCT` explaining the M6 USD-threshold simplification and that M8 will fix the semantics.

These are implementation refinements, not design changes. They belong in the plan, not the spec.

---

## 9. References

- PRD: `docs/PRD.md` v0.7 (especially §4.3 FR-3.1–3.7, §4.4 FR-4.4–4.6, §4.5 FR-5.1–5.9, §4.6 FR-6.1–6.6, §4.7 FR-7.1, §10, §15 M6 row)
- M5 spec: `docs/superpowers/specs/2026-06-21-m5-telegram-listener-design.md`
- M4 spec: `docs/superpowers/specs/2026-06-20-m4-database-infrastructure-design.md`
- M3 spec: `docs/superpowers/specs/2026-06-20-m3-broker-protocol-design.md`
- M2 spec: `docs/superpowers/specs/2026-06-19-m2-state-machine-design.md`
- M2 plan: `docs/superpowers/plans/2026-06-19-m2-state-machine.md`
- State machine: `src/signal_copier/domain/state.py`
- Broker protocol: `src/signal_copier/broker/base.py`
- DryRunBroker: `src/signal_copier/broker/dry_run.py`
- StateStore: `src/signal_copier/infra/state_store.py`
- Clock helpers: `src/signal_copier/infra/clock.py`

---

*End of M6 design spec — pending user review before implementation planning.*
