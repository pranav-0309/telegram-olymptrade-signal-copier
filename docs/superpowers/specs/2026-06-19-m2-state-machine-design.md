# M2 — State Machine, Gale Math, & Config Layer Design

**Date:** 2026-06-19
**Status:** Approved (all 12 sections reviewed by user)
**PRD reference:** `docs/PRD.md` v0.7 (§4.3 Trade Scheduler, §4.5 Result Monitor & Gale State Machine, §4.6 Safety & Limits, §4.7 Notifications, §6 Tech Stack, §9 Data Model, §15 Build Plan M2 row)
**Build plan reference:** PRD §15, M2 row

---

## 1. Purpose & Scope

M2 is the third milestone of the Telegram → OlympTrade Signal Copier (PRD v0.7). It ships the **per-signal state machine**, the **gale amount math**, the **time-window enforcement hooks**, and the **pydantic-settings config layer** — all pure, broker-agnostic, fully-tested Python.

**M2 ships no I/O.** No asyncpg, no Telethon, no broker, no real clock. The state machine takes a `now_unix: float` parameter so all transitions are testable with a fake clock. The scheduler (M6) and broker (M8) wire in the real loop time and push events.

**In scope for M2 (8 new files + 4 modifications):**

| # | File | Type | Purpose |
|---|---|---|---|
| 1 | `src/signal_copier/config.py` | NEW | pydantic-settings: 13 env-driven fields + demo-only guardrail |
| 2 | `src/signal_copier/domain/gale.py` | NEW | `amount_for_stage()`, `compute_gale_triggers()` (HH:MM arithmetic) |
| 3 | `src/signal_copier/domain/state.py` | NEW | Frozen `SignalState` dataclass, 4 event types, pure `transition()` fn, `TransitionResult` |
| 4 | `src/signal_copier/infra/__init__.py` | NEW | Empty package marker |
| 5 | `src/signal_copier/infra/log.py` | NEW | Stub `setup_logging()` (replaced by M7 with loguru) |
| 6 | `src/signal_copier/domain/__init__.py` | MODIFY | Re-export new public symbols |
| 7 | `src/signal_copier/__main__.py` | MODIFY | Load config, log `🟢 Bot started` startup message |
| 8 | `src/signal_copier/domain/signal.py` | MODIFY | Add 3 `trigger_unix_*` fields to `Signal` dataclass |
| 9 | `tests/test_gale_math.py` | NEW | ~10 tests for gale math |
| 10 | `tests/test_state_machine.py` | NEW | ~50 tests for state transitions + time-window enforcement |
| 11 | `tests/test_config.py` | NEW | ~12 tests for config loading + demo guardrail |
| 12 | `pyproject.toml` | MODIFY | Add `pydantic-settings` runtime dep |

**Out of scope (deferred to later milestones):**

| Concern | Lands in |
|---|---|
| Real `__main__.py` wiring with Telethon/broker/DB | M6 (scheduler) + M8 (broker) + M5 (listener) |
| Database persistence of `SignalState` transitions | M4 (StateStore) + M6 (wired at fire time) |
| Telegram self-DM notifications on every transition | M7 (per FR-7.1) |
| `asyncio.call_at` trigger scheduling | M6 (scheduler) |
| Real broker calls (`OlympTradeClient.place_order`, e:26 push) | M8 |
| Daily-limit enforcement (DAILY_LOSS_LIMIT, etc.) | M6 (uses M4's `daily_summary`) |
| Restart-from-persisted-state | M6 (queries M4 `StateStore`) |
| `infra/clock.py` (per PRD §7 tree) | Deferred — M2 puts time helpers in `domain/gale.py`; M6 can move to `infra/clock.py` if needed |

**What M2's state machine validates (per FR-5.1 + time-window subset of FR-3.3 / FR-3.5 / FR-3.6 / FR-5.9):**
- Stage transitions are valid for the current state (e.g., `pending` → `placed_initial` is OK, `pending` → `placed_gale1` is a `TransitionError`).
- Pre-fire guard: if `now_unix` is more than `TRIGGER_SKEW_TOLERANCE_SECONDS` past the current stage's trigger time, the cascade ends with `error (signal_expired)` (FR-3.5).
- Result event applied to the correct stage: a `WIN` after `placed_gale2` → `done_win`; a `LOSS` after `placed_gale1` → `placed_gale2` (not `done_win`).
- Tie / timeout at any stage → treated as `LOSS` for that stage (FR-5.3), but the stage's `result` field records the actual outcome for observability.

**What M2's state machine does NOT validate (deferred):**
- Whether the broker is connected (M8).
- Whether the pair is supported (M8).
- Whether daily limits are hit (M6 + M4).
- Whether the process is alive (out of scope — `asyncio` handles that).

---

## 2. Resolved Decisions (M2-specific)

The PRD resolves all architectural questions (R-1 through R-15). The following are M2-specific scoping calls, confirmed during brainstorming on 2026-06-19.

| # | Decision | Rationale |
|---|---|---|
| D-1 | **Pure functional state machine** | M1's parser is a pure function (`parse_signal`); state machine follows the same pattern. `SignalState` is a frozen dataclass; `transition()` is a pure function. State is a value — easy to test, serialize, persist. M5/M6 thread state through `asyncio` callbacks. |
| D-2 | **Time-window enforcement lives in M2's state machine** | FR-5.1 says "every transition between stages checks the strict time window". Co-locating the check with the transition logic keeps the invariant visible. `transition()` takes `now_unix: float`; the test suite uses fake clocks. M6 just passes the loop clock in. |
| D-3 | **Pydantic-settings config layer in M2** | M0 spec §5.2 lists pydantic-settings as "M1" (deferred). State machine + gale math need `AMOUNT_INITIAL`/`AMOUNT_GALE1`/`AMOUNT_GALE2`/`TRIGGER_SKEW_TOLERANCE_SECONDS`/`TIMEZONE`/expiration — natural to land the config module with its first consumer. M5/M6 import `Config`. |
| D-4 | **Daily limits deferred to M6** | Limits require daily-summary state from M4 (DB) and a gate at signal-acceptance time. M2 declares the config fields (`DAILY_LOSS_LIMIT`, `DAILY_TRADE_LIMIT`, `DAILY_DRAWDOWN_PCT`) so the schema is complete, but enforcement lands in M6. |
| D-5 | **Add `trigger_unix_*` fields to M1's `Signal`** | M1's `Signal.trigger_hhmm: str` is a string. M2's state machine compares against `now_unix: float`. The cleanest solution: M5 (listener) computes the 3 trigger epochs at construction time using the configured TZ. M2's `Signal` carries `trigger_unix_initial`, `trigger_unix_gale1`, `trigger_unix_gale2`. M1's `ParsedSignal` stays unchanged. |
| D-6 | **Time helpers in `domain/gale.py`**, not a separate `clock.py` | PRD §7 puts `clock.py` in `infra/`, but for M2's purposes, time math is a pure domain operation. M6 (scheduler) can extract to `infra/clock.py` if its needs diverge. YAGNI for M2. |
| D-7 | **8 states, not 9** — `done_timeout` is the stage-level result, not a signal-level terminal state | The PRD FR-5.1 diagram lists 9 states including `done_timeout`. Re-read carefully: `done_timeout` describes a single stage's result when the trade-result push event never arrives. The *signal-level* terminal states are: `done_win`, `done_loss`, `done_tie`, `error`. A `done_timeout` at a stage cascades to the next stage (treated as loss per FR-5.3) — it's not a signal terminal. M2 keeps `done_win`/`done_loss`/`done_tie`/`error` as signal terminals; the per-stage `result` field records `win`/`loss`/`tie`/`timeout`/`error` distinctly. The `done_tie` and `done_timeout` Literal values are kept in the type union for v2 API completeness. |
| D-8 | **`TransitionError` is a value, not an exception** | Invalid events (e.g., `record_result` on a `pending` state) return `TransitionResult.failure(reason)`, not raise. Caller (M6) decides what to do — log, DM, abort. Exceptions for control flow are an anti-pattern in async code; the state machine is pure and total. |
| D-9 | **State mutability: `SignalState` is `@dataclass(frozen=True, slots=True)`** | M1's `ParsedSignal`/`Signal`/`ParseFailure` are frozen. M2's `SignalState` follows the same convention. M6 mutates by rebinding: `state = transition(state, event, config).new_state`. One-line in `asyncio` callbacks. |
| D-10 | **Demo-only guardrail lives in `config.py` validator** | FR-6.6: app refuses to start if `OLYMP_ACCOUNT_GROUP=real` and `DRY_RUN=false`. M2's `Config` model validator raises `ValueError` at boot time. The user (in M11 / first-deploy runbook) must explicitly set both flags to enable real trading. M2's `__main__.py` surfaces the error and exits 2. |
| D-11 | **No `asyncio` imports in M2** | M2 is sync-only — no event loop, no `async def`. The state machine is a pure data structure; the scheduler (M6) is the async layer. This makes M2's tests trivially fast and isolated. |
| D-12 | **State machine does not persist** | FR-5.8 says "persist state on every transition to PostgreSQL via the `infra/db.py` `StateStore`". M4 ships the `StateStore`; M6 wires the persistence call at each transition. M2's `transition()` returns a `TransitionResult` (new state + side-effect hints); M6 translates the hints into `state_store.upsert_signal()` calls. |

---

## 3. Repository Layout (post-M2)

```
olymptrade/
├── pyproject.toml                 # MODIFY: add pydantic-settings
├── .env.example                   # (unchanged from M0)
├── src/
│   ├── olymptrade_ws/             # (unchanged, vendored)
│   └── signal_copier/
│       ├── __init__.py            # (unchanged)
│       ├── __main__.py            # MODIFY: load config, log startup
│       ├── config.py              # NEW: pydantic-settings Config
│       ├── domain/
│       │   ├── __init__.py        # MODIFY: re-export new symbols
│       │   ├── signal.py          # MODIFY: add 3 trigger_unix_* fields to Signal
│       │   ├── gale.py            # NEW: amount_for_stage(), compute_gale_triggers()
│       │   └── state.py           # NEW: SignalState, events, transition()
│       └── infra/
│           ├── __init__.py        # NEW (empty package marker)
│           └── log.py             # NEW (setup_logging stub, replaced by M7)
├── tests/
│   ├── test_main.py               # (unchanged, M0 stub test)
│   ├── test_parser.py             # (unchanged, M1)
│   ├── test_gale_math.py          # NEW
│   ├── test_state_machine.py      # NEW
│   └── test_config.py             # NEW
└── docs/
    ├── superpowers/
    │   ├── specs/
    │   │   ├── 2026-06-19-m0-scaffold-design.md
    │   │   ├── 2026-06-19-m1-parser-design.md
    │   │   └── 2026-06-19-m2-state-machine-design.md  # NEW (this doc)
    │   └── plans/
    │       ├── 2026-06-19-m0-scaffold.md
    │       ├── 2026-06-19-m1-parser.md
    │       └── 2026-06-19-m2-state-machine.md         # NEW (later)
    └── PRD.md
```

**Notable choices:**
- `tests/test_config.py` lives at the top level (next to `test_parser.py`), not under `tests/config/`. M0/M1 don't have a `tests/config/` subdir; keeping all tests at the top level matches the existing convention.
- `src/signal_copier/domain/gale.py` and `state.py` are sibling files in `domain/`, following the M1 `signal.py` pattern.
- `src/signal_copier/config.py` is at the package root (sibling of `__main__.py`), not under `domain/`. The PRD §7 architecture tree doesn't show it explicitly, but `infra/` is reserved for I/O concerns (db, log, clock per §7). Config is a top-level concern.
- `src/signal_copier/infra/__init__.py` + `infra/log.py` are created now (with a stub `setup_logging`) so M2's `__main__.py` compiles. M7 replaces `log.py` with the loguru setup; M4 adds `infra/db.py`. The `infra/` directory is empty in M0 (PRD §7 tree shows it; M0 didn't create it because M0 had no infra concerns).

---

## 4. Key File Contents

### 4.1 `src/signal_copier/config.py` (NEW)

```python
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Telegram (not used by M2, declared for schema completeness) ------
    telegram_api_id: int = 0
    telegram_api_hash: str = ""
    telegram_phone: str = ""
    telegram_session_string: str = ""
    telegram_target_chat: str = "@analyst_channel"
    telegram_self_dm_notifications: bool = True

    # --- OlympTrade (not used by M2, declared for schema completeness) ----
    olymp_access_token: str = ""
    olymp_account_group: str = "demo"  # FR-6.6: must be "demo" for v1
    olymp_account_id: str = ""

    # --- Database (not used by M2, declared for schema completeness) ------
    database_url: str = "postgresql://user:pass@localhost:5432/copier"

    # --- Trading (used by M2) ---------------------------------------------
    dry_run: bool = True
    require_confirm: bool = False
    amount_initial: Decimal = Field(default=Decimal("2.00"), gt=0)
    amount_gale1: Decimal = Field(default=Decimal("4.00"), gt=0)
    amount_gale2: Decimal = Field(default=Decimal("8.00"), gt=0)
    expiration_seconds: int = Field(default=300, gt=0)
    trigger_skew_tolerance_seconds: float = Field(default=2.0, ge=0)

    # --- Optional safety limits (FR-6.1/6.2/6.3, deferred to M6) ----------
    daily_loss_limit: Decimal = Field(default=Decimal("0.00"), ge=0)
    daily_trade_limit: int = Field(default=0, ge=0)
    daily_drawdown_pct: int = Field(default=0, ge=0, le=100)

    # --- Schedule / Timezone (used by M2) ---------------------------------
    timezone: str = "America/Sao_Paulo"
    log_path: Path = Path("./logs/signal_copier.log")

    # --- Validators --------------------------------------------------------

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown timezone: {v!r}") from exc
        return v

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

    def tz(self) -> ZoneInfo:
        """Convenience accessor for the configured timezone."""
        return ZoneInfo(self.timezone)
```

**Notes:**
- `pydantic-settings` reads from env vars + `.env`. Field names are lower-snake-case; pydantic-settings auto-maps env vars to upper-snake-case (e.g., `AMOUNT_INITIAL` → `amount_initial`).
- `Decimal` (not `float`) for amounts — money math. `float` causes precision drift; `Decimal` is exact.
- `Decimal` is also used for `daily_loss_limit` (USD). `daily_trade_limit` and `daily_drawdown_pct` are `int` (counts and percentages).
- The `_demo_only_guardrail` validator runs at instantiation. M2's `__main__.py` catches the `ValueError` and exits 2 with the message.
- M2 doesn't *use* the Telegram/OlympTrade/DB fields, but they're declared so the schema is complete and M5/M6 don't need to add them. Extra fields are `extra="ignore"` to avoid breaking the .env parser if it sees an unknown key.
- `trigger_skew_tolerance_seconds` is `float` (not `Decimal`) because it's a time duration, not money.

### 4.2 `src/signal_copier/domain/signal.py` (MODIFY)

Add 3 new fields to the `Signal` dataclass (per D-5):

```python
@dataclass(frozen=True, slots=True)
class Signal:
    signal_id: str
    pair: str
    direction: Literal["up", "down"]
    trigger_hhmm: str
    expiration_seconds: int
    received_at_unix: float
    source_message_id: int
    source_chat_id: int
    raw_text: str
    # --- Added in M2 (D-5) ---
    trigger_unix_initial: float    # epoch for trigger_hhmm on the signal's date in config TZ
    trigger_unix_gale1: float      # trigger_unix_initial + expiration_seconds
    trigger_unix_gale2: float      # trigger_unix_initial + 2 * expiration_seconds
```

**Notes:**
- Surgical addition. M1's 32 tests still pass — they don't construct `Signal` (only `ParsedSignal`).
- `expiration_seconds` already exists; gales are 1× and 2× the expiration, computed once at signal-construction time.
- The M5 listener (not M2) computes these from `trigger_hhmm` + `signal_date` + `TIMEZONE`. M2 just consumes them.
- M2's tests construct `Signal` directly via `dataclass(...)`, so they bypass M5's construction logic.

### 4.3 `src/signal_copier/domain/gale.py` (NEW)

```python
from __future__ import annotations

from decimal import Decimal
from typing import Final, Literal

from signal_copier.config import Config


Stage = Literal["initial", "gale1", "gale2"]


# Stage → config field name. The mapping is fixed; we use a table instead of
# a chain of if/elif so adding a future "gale3" only touches the table.
_STAGE_AMOUNT_FIELD: Final[dict[str, str]] = {
    "initial": "amount_initial",
    "gale1": "amount_gale1",
    "gale2": "amount_gale2",
}


def amount_for_stage(stage: Stage, config: Config) -> Decimal:
    """Return the bet amount for a stage. Stage amounts, not increments (R-2)."""
    field = _STAGE_AMOUNT_FIELD.get(stage)
    if field is None:  # pragma: no cover — Literal type blocks this
        raise ValueError(f"unknown stage: {stage!r}")
    return getattr(config, field)


def compute_gale_triggers(
    trigger_unix_initial: float,
    expiration_seconds: int,
) -> tuple[float, float]:
    """Return (trigger_unix_gale1, trigger_unix_gale2) for a signal's initial trigger.

    Gale1 fires at initial + 1 expiration. Gale2 at initial + 2 expirations.
    R-2 + FR-5.5/5.6: stage times, not absolute offsets.
    """
    gale1 = trigger_unix_initial + float(expiration_seconds)
    gale2 = trigger_unix_initial + 2.0 * float(expiration_seconds)
    return (gale1, gale2)
```

**Notes:**
- `amount_for_stage` reads from `Config` so the values are configurable (R-2 confirms $2/$4/$8 for v1 but the function is generic).
- `compute_gale_triggers` is the math. M5 calls it at signal-construction time; M2's `SignalState` then carries the pre-computed values (D-5).
- `Decimal` for amounts, `float` for time (Unix epoch is a float by convention).
- No `zoneinfo` usage here — D-6 says time helpers in `gale.py`, but the HH:MM → unix conversion needs a date and TZ; that's an M5 (listener) concern, not M2. M2 just operates on the pre-computed floats.

### 4.4 `src/signal_copier/domain/state.py` (NEW)

```python
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Union

from signal_copier.config import Config
from signal_copier.domain.gale import Stage, amount_for_stage
from signal_copier.domain.signal import Signal


# --- Top-level signal state machine states (PRD FR-5.1) ------------------

# Pre-terminal: the cascade is in flight.
State = Literal[
    "pending",           # signal received, not yet fired
    "placed_initial",    # initial trade placed, awaiting result
    "placed_gale1",      # gale1 trade placed, awaiting result
    "placed_gale2",      # gale2 trade placed, awaiting result
]

# Terminal: cascade complete.
# Per D-7: done_tie is reserved for v2 (unreachable in M2's transitions).
# done_win / done_loss are the only signal-level terminals in v1.
# error carries error_reason for signal_expired / broker_unavailable / unknown.
TerminalState = Literal[
    "done_win",
    "done_loss",
    "done_tie",          # reserved for v2; M2 transitions never reach this
    "error",
]

AllStates = Union[State, TerminalState]

# Per-stage result (recorded in SignalState.result, not the top-level state).
# Tie and timeout at a non-final stage are treated as LOSS for cascade purposes (FR-5.3);
# the original outcome is still recorded here for observability.
StageResult = Literal["win", "loss", "tie", "timeout", "error"]


# --- Error reason enum (subset of PRD §9.1 + FR-5.1) ---------------------

ErrorReason = Literal[
    "signal_expired",     # FR-3.3 / FR-3.5 / FR-3.6 / FR-5.9: time window passed
    "broker_unavailable", # FR-4.4: broker dropped / token expired
    "unknown",
]


# --- Event types (M6 dispatches these based on scheduler / broker signals) ---

@dataclass(frozen=True, slots=True)
class FireEvent:
    """Try to fire the current stage's trade. Triggered by M6's asyncio.call_at."""
    now_unix: float


@dataclass(frozen=True, slots=True)
class ResultEvent:
    """The broker reports a result for the current stage's trade."""
    result: StageResult
    now_unix: float


Event = Union[FireEvent, ResultEvent]


# --- The state value (frozen, replaceable) --------------------------------

@dataclass(frozen=True, slots=True)
class SignalState:
    signal_id: str
    pair: str
    direction: Literal["up", "down"]
    state: AllStates                  # one of the State / TerminalState values
    stage: Stage | None               # current stage; None at terminal states
    amount: Decimal                   # bet amount for the current stage
    trigger_unix: float               # trigger time for the current stage
    expires_at_unix: float            # trigger_unix + expiration_seconds
    result: StageResult | None        # last stage result (None until first result)
    cumulative_pnl: Decimal            # sum of stage PnLs (signed; losses negative)
    error_reason: ErrorReason | None   # populated only when state == "error"

    @classmethod
    def from_signal(cls, signal: Signal, config: Config) -> SignalState:
        """Construct the initial state for a newly-received signal.

        The signal is 'pending' — the scheduler hasn't fired it yet.
        Gale trigger times come from the Signal's pre-computed fields (D-5).
        """
        return cls(
            signal_id=signal.signal_id,
            pair=signal.pair,
            direction=signal.direction,
            state="pending",
            stage="initial",
            amount=amount_for_stage("initial", config),
            trigger_unix=signal.trigger_unix_initial,
            expires_at_unix=signal.trigger_unix_initial + float(signal.expiration_seconds),
            result=None,
            cumulative_pnl=Decimal("0.00"),
            error_reason=None,
        )


# --- Transition result ---------------------------------------------------

@dataclass(frozen=True, slots=True)
class TransitionResult:
    """Outcome of a transition attempt.

    Always returns a value — never raises. Invalid events return
    success=False with a reason. The caller (M6) decides what to do.
    """
    success: bool
    new_state: SignalState | None   # set on success; None on failure
    reason: str | None              # set on failure; human-readable


# --- Pure transition function (D-1) --------------------------------------

def _check_time_window(
    state: SignalState, now_unix: float, tolerance: float,
) -> bool:
    """Return True if the current stage's time window has already passed.

    Per FR-3.3 / FR-3.5 / FR-3.6 / FR-5.9: a missed window ends the cascade.
    Tolerance is `trigger_skew_tolerance_seconds` from config (default 2.0s).
    """
    return now_unix > state.trigger_unix + tolerance


def _stage_pnl(state: SignalState, result: StageResult) -> Decimal:
    """Compute this stage's PnL contribution.

    For v1, broker PnL is approximated from amount + result; M8 will replace
    with the broker's reported PnL. Until then, this is the contract.
    """
    if result == "win":
        # OlympTrade typical payout is ~92% for 5-min digital — v1 approximation.
        # M8 will replace with broker-reported PnL.
        return state.amount * Decimal("0.92")
    if result in {"loss", "tie", "timeout"}:
        return -state.amount
    return Decimal("0.00")  # pragma: no cover


def _to_placed(
    state: SignalState, next_stage: Stage, result: StageResult,
    cumulative: Decimal, config: Config,
) -> SignalState:
    """Move from a placed_X state to the next placed_X state after a loss.

    Per FR-5.5/5.6: gale1 fires at trigger+5min, gale2 at trigger+10min.
    This couples M2 to the 5-minute expiration — see Risk #10.
    """
    trigger_unix = {
        "gale1": state.trigger_unix + 5 * 60,  # 5 min later
        "gale2": state.trigger_unix + 10 * 60,  # 10 min later
    }[next_stage]
    return SignalState(
        signal_id=state.signal_id,
        pair=state.pair,
        direction=state.direction,
        state=f"placed_{next_stage}",
        stage=next_stage,
        amount=amount_for_stage(next_stage, config),
        trigger_unix=trigger_unix,
        expires_at_unix=trigger_unix + 5 * 60,  # default 5-min expiration
        result=result,
        cumulative_pnl=cumulative,
        error_reason=None,
    )


def _next_stage_trigger_unix(state: SignalState) -> float:
    """Compute the next stage's trigger_unix from the current state.

    Returns gale1's trigger if current stage is initial, gale2's if gale1,
    or the current trigger_unix if already at gale2 (no next stage).
    """
    if state.stage == "initial":
        return state.trigger_unix + 5 * 60  # gale1 = initial + 5min
    if state.stage == "gale1":
        return state.trigger_unix + 5 * 60  # gale2 = gale1 + 5min
    return state.trigger_unix  # gale2 has no next stage


def _to_terminal(
    state: SignalState, terminal: TerminalState, result: StageResult, cumulative: Decimal,
) -> SignalState:
    return SignalState(
        signal_id=state.signal_id,
        pair=state.pair,
        direction=state.direction,
        state=terminal,
        stage=None,
        amount=Decimal("0.00"),
        trigger_unix=state.trigger_unix,
        expires_at_unix=state.expires_at_unix,
        result=result,
        cumulative_pnl=cumulative,
        error_reason=None,
    )


def _to_error(
    state: SignalState, reason: ErrorReason, result: StageResult | None, cumulative: Decimal,
) -> SignalState:
    return SignalState(
        signal_id=state.signal_id,
        pair=state.pair,
        direction=state.direction,
        state="error",
        stage=None,
        amount=Decimal("0.00"),
        trigger_unix=state.trigger_unix,
        expires_at_unix=state.expires_at_unix,
        result=result,
        cumulative_pnl=cumulative,
        error_reason=reason,
    )


def _advance_after_result(
    state: SignalState, result: StageResult, now_unix: float, config: Config,
) -> SignalState:
    """Compute the next state after a stage result.

    Per FR-3.6 / FR-5.9: when advancing to the next stage (gale1 or gale2),
    check the next stage's time window. If the next stage's window has
    already passed, the cascade ends with `error (signal_expired)`.

    Per FR-5.3: tie and timeout are treated as loss for cascade purposes.
    Per FR-5.4: a win at any stage ends the cascade as done_win.
    Per FR-5.7: a loss at gale2 ends the cascade as done_loss.
    """
    pnl_delta = _stage_pnl(state, result)
    cumulative = state.cumulative_pnl + pnl_delta

    if result == "win":
        return _to_terminal(state, "done_win", result, cumulative)

    if result in {"loss", "tie", "timeout"}:
        if state.stage == "gale2":
            # FR-5.7: gale2 loss = done_loss (terminal; no time check needed)
            return _to_terminal(state, "done_loss", result, cumulative)
        # Pre-fire guard for the next stage (FR-3.6 / FR-5.9).
        next_trigger = _next_stage_trigger_unix(state)
        if now_unix > next_trigger + config.trigger_skew_tolerance_seconds:
            return _to_error(state, "signal_expired", result, cumulative)
        if state.stage == "initial":
            return _to_placed(state, "gale1", result, cumulative, config)
        if state.stage == "gale1":
            return _to_placed(state, "gale2", result, cumulative, config)

    if result == "error":
        return _to_error(state, "broker_unavailable", result, cumulative)

    return _to_error(state, "unknown", result, cumulative)  # pragma: no cover


def transition(
    state: SignalState, event: Event, *, config: Config,
) -> TransitionResult:
    """Apply an event to the current state. Returns the new state (or failure).

    This is a pure function. Same (state, event, config) → same result.
    """
    # Terminal states are absorbing — no further events accepted.
    if state.state in {"done_win", "done_loss", "done_tie", "error"}:
        return TransitionResult(
            success=False, new_state=None,
            reason=f"invalid_event: state is terminal ({state.state}); event {event} ignored",
        )

    # FireEvent: try to fire the current stage's trade.
    if isinstance(event, FireEvent):
        # Pre-fire guard (FR-3.5 / FR-3.6 / FR-5.9): every stage's window matters.
        if _check_time_window(state, event.now_unix, config.trigger_skew_tolerance_seconds):
            new_state = _to_error(state, "signal_expired", None, state.cumulative_pnl)
            return TransitionResult(success=True, new_state=new_state, reason=None)
        if state.state == "pending" and state.stage == "initial":
            new_state = SignalState(
                signal_id=state.signal_id,
                pair=state.pair,
                direction=state.direction,
                state="placed_initial",
                stage="initial",
                amount=state.amount,
                trigger_unix=state.trigger_unix,
                expires_at_unix=state.expires_at_unix,
                result=None,
                cumulative_pnl=state.cumulative_pnl,
                error_reason=None,
            )
            return TransitionResult(success=True, new_state=new_state, reason=None)
        # placed_gale1 / placed_gale2 cannot be re-fired — they're awaiting result.
        return TransitionResult(
            success=False, new_state=None,
            reason=f"invalid_event: FireEvent on placed state ({state.state}); record_result first",
        )

    # ResultEvent: apply result to the current placed stage.
    if isinstance(event, ResultEvent):
        if state.state not in {"placed_initial", "placed_gale1", "placed_gale2"}:
            return TransitionResult(
                success=False, new_state=None,
                reason=f"invalid_event: ResultEvent on non-placed state ({state.state})",
            )
        new_state = _advance_after_result(state, event.result, event.now_unix, config)
        return TransitionResult(success=True, new_state=new_state, reason=None)

    return TransitionResult(success=False, new_state=None, reason="unknown event type")  # pragma: no cover
```

**Notes:**
- The state machine is **~180 LOC** including helpers. Half the file is transition logic; the rest is type definitions and dataclasses.
- `transition()` is total — no exceptions. Invalid events return `success=False`.
- Time-window enforcement is in `_check_time_window()`. On failure, we still return `success=True` with `state="error"` and `error_reason="signal_expired"` — the cascade ends, which is what FR-5.9 says. The caller (M6) can inspect `state.error_reason` to DM the user.
- The `_to_placed` helper advances the trigger by hardcoded 5/10 minutes. This couples M2 to the 5-minute expiration. For v1 (FR-5.5/5.6: gales are at trigger + 5min and trigger + 10min), this is correct. M3+ could parameterize if v2 supports configurable expirations.
- `_stage_pnl()` approximates broker PnL as `amount * 0.92` for wins (typical OlympTrade 5-min digital payout). M8 replaces with broker-reported PnL — M2's contract is "result + amount → PnL", not "exact broker math".

### 4.5 `src/signal_copier/domain/__init__.py` (MODIFY)

```python
from signal_copier.config import Config
from signal_copier.domain.gale import (
    Stage,
    amount_for_stage,
    compute_gale_triggers,
)
from signal_copier.domain.signal import (
    FailureReason,
    ParseFailure,
    ParsedSignal,
    ParseResult,
    Signal,
    derive_signal_id,
    parse_signal,
)
from signal_copier.domain.state import (
    AllStates,
    ErrorReason,
    Event,
    FireEvent,
    ResultEvent,
    SignalState,
    StageResult,
    State,
    TerminalState,
    TransitionResult,
    transition,
)

__all__ = [
    # Config
    "Config",
    # Gale
    "Stage",
    "amount_for_stage",
    "compute_gale_triggers",
    # Signal (M1)
    "FailureReason",
    "ParseFailure",
    "ParsedSignal",
    "ParseResult",
    "Signal",
    "derive_signal_id",
    "parse_signal",
    # State machine (M2)
    "AllStates",
    "ErrorReason",
    "Event",
    "FireEvent",
    "ResultEvent",
    "SignalState",
    "StageResult",
    "State",
    "TerminalState",
    "TransitionResult",
    "transition",
]
```

### 4.6 `src/signal_copier/__main__.py` (MODIFY)

```python
from __future__ import annotations

import sys

from pydantic import ValidationError

from signal_copier.config import Config
from signal_copier.infra.log import setup_logging


def main() -> None:
    try:
        config = Config()
    except ValidationError as exc:
        # Demo-only guardrail (FR-6.6) and other validation errors land here.
        sys.stderr.write(f"❌ Config validation failed:\n{exc}\n")
        sys.exit(2)

    setup_logging(config.log_path)  # stub; replaced by M7

    print(
        f"🟢 signal_copier M2 started (config loaded)\n"
        f"   Mode: {'dry_run' if config.dry_run else 'live demo'}\n"
        f"   Timezone: {config.timezone}\n"
        f"   Amounts: initial=${config.amount_initial} "
        f"gale1=${config.amount_gale1} gale2=${config.amount_gale2}\n"
        f"   (state machine + gale math ready; broker/listener/scheduler pending M5+)"
    )


if __name__ == "__main__":
    main()
```

**Notes:**
- `Config()` reads from env + `.env`. If validation fails (including the demo-only guardrail), exit 2 with the error.
- `setup_logging` is a forward reference to M7. M2 defines a minimal stub at `src/signal_copier/infra/log.py` (just `def setup_logging(path): pass`) so M2's `__main__.py` compiles. M7 replaces with loguru.
- This is M0/M1's print style — temporary until M7 wires loguru.

### 4.7 `src/signal_copier/infra/__init__.py` and `infra/log.py` (NEW stubs)

```python
# src/signal_copier/infra/__init__.py
# Empty package marker.
```

```python
# src/signal_copier/infra/log.py
from __future__ import annotations
from pathlib import Path


def setup_logging(log_path: Path) -> None:
    """Stub. Replaced by M7 with loguru setup."""
    _ = log_path  # unused until M7
```

**Note:** M2 creates the `infra/` package with a `log.py` stub so `__main__.py` can import. This matches the PRD §7 architecture tree (`infra/log.py` is listed).

### 4.8 `pyproject.toml` (MODIFY)

Add `pydantic-settings` to runtime deps:

```toml
[project]
name = "signal-copier"
version = "0.1.0"
description = "Telegram → OlympTrade signal copier (demo only, v1)"
readme = "README.md"
requires-python = ">=3.13"
license = { text = "Proprietary" }
authors = [{ name = "<owner>" }]
dependencies = [
    "pydantic-settings>=2.6",  # M2: config layer (D-3)
]

[project.scripts]
signal-copier = "signal_copier.__main__:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

# ... (rest of M0's pyproject.toml unchanged)
```

---

## 5. Dependency Changes

| Package | Version | Added in | Purpose |
|---|---|---|---|
| `pydantic-settings` | `>=2.6` | M2 | Config layer (D-3). Transitively pulls in `pydantic>=2.6` and `python-dotenv>=1.0`. |
| (transitive) `pydantic` | `>=2.6` | via pydantic-settings | Field validation, `Field()`, `field_validator`, `model_validator` |
| (transitive) `python-dotenv` | `>=1.0` | via pydantic-settings | Reads `.env` file |
| (stdlib) `zoneinfo` | — | M2 | TZ handling in `Config._validate_timezone` and `Config.tz()`. Available in Python 3.9+; no dep. |
| (stdlib) `decimal` | — | M2 | Money math (Decimal for amounts, PnL). |
| (stdlib) `dataclasses`, `typing`, `enum` | — | M1 | Already used. |

**No new dev dependencies.** `pytest`, `pytest-asyncio`, `ruff`, `mypy` are already in M0's `pyproject.toml`. M2's tests are sync (D-11), so `pytest-asyncio` isn't exercised yet — still in dev deps for M6.

**`uv.lock` regenerates** when `pydantic-settings` is added. Run `uv lock` after editing `pyproject.toml` and commit the new `uv.lock` (mirroring M0's process).

**Docker image size impact:** pydantic-settings + pydantic + python-dotenv add ~5 MB. Negligible against the 900 MB `python:3.13` base.

---

## 6. State Machine Architecture

### 6.1 States (PRD FR-5.1 + D-7)

The state machine has **8 top-level states** (4 pre-terminal + 4 terminal):

```
            ┌──────────────────────────────────────────────────────┐
            │                                                      │
            ▼                                                      │
        pending ──[FireEvent @ trigger_unix_initial]──▶ placed_initial
                                                            │
                                                            ├─[ResultEvent win]──▶ done_win  (terminal)
                                                            │
                                                            ├─[ResultEvent loss/tie/timeout]──▶ placed_gale1
                                                            │                                          │
                                                            │                                          ├─[ResultEvent win]──▶ done_win
                                                            │                                          │
                                                            │                                          ├─[ResultEvent loss/tie/timeout]──▶ placed_gale2
                                                            │                                          │                          │
                                                            │                                          │                          ├─[ResultEvent win]──▶ done_win
                                                            │                                          │                          │
                                                            │                                          │                          └─[ResultEvent loss/tie/timeout]──▶ done_loss
                                                            │                                          │
                                                            └─[ResultEvent error]──▶ error
            ▲                                                      │
            │                                                      │
            │   On FireEvent @ pending: initial window checked.     │
            │   On ResultEvent(loss) @ placed_X: next-stage window │
            │   checked.                                           │
            │   If the relevant trigger_unix has passed:           │
            │       ┌──────────────────────────────────────────────┐ │
            │       │ pre-terminal state ──▶ error (signal_expired)│ │
            │       └──────────────────────────────────────────────┘ │
            │                                                      │
            │   Terminal states are absorbing. No further events.  │
            └──────────────────────────────────────────────────────┘
```

| State | Stage | Trigger time | Result | Notes |
|---|---|---|---|---|
| `pending` | `initial` | `trigger_unix_initial` | `None` | Newly received; not yet fired |
| `placed_initial` | `initial` | `trigger_unix_initial` | `None` | Initial trade placed; awaiting result |
| `placed_gale1` | `gale1` | `trigger_unix_gale1` | `'loss'\|'tie'\|'timeout'` | Gale1 trade placed; awaiting result |
| `placed_gale2` | `gale2` | `trigger_unix_gale2` | `'loss'\|'tie'\|'timeout'` | Gale2 trade placed; awaiting result |
| `done_win` | `None` | — | `'win'` | Terminal. Any stage won. |
| `done_loss` | `None` | — | `'loss'\|'tie'\|'timeout'` | Terminal. Gale2 lost. |
| `done_tie` | `None` | — | `'tie'` | **Reserved** for v2; M2 doesn't reach this state. Per FR-5.3 tie at any stage cascades as loss, so the only way to reach `done_tie` is a future broker behavior change. |
| `error` | `None` | — | `StageResult` or `None` | Terminal. `error_reason` set. |

**D-7 rationale:** the PRD §4.5 diagram shows `done_tie` and `done_timeout` as terminals. Re-reading the requirements: a `tie` at any stage is **treated as loss** for cascade purposes (FR-5.3). A `timeout` (no result push within `expiration + 30s`) is also treated as loss (FR-5.3). Neither produces a *signal-level* "tie" or "timeout" terminal — the cascade just continues. The per-stage `result` field records the actual outcome (`win`/`loss`/`tie`/`timeout`/`error`) for observability; the *signal* moves to `done_loss` (or `done_win` if it eventually wins).

The `done_tie` Literal value is kept in the type union for API completeness — v2 may add a "tie as a real terminal" if the broker behavior changes. M2's transitions never reach it.

### 6.2 Events

| Event | Dispatched by | Effect |
|---|---|---|
| `FireEvent(now_unix)` | M6 scheduler at `trigger_unix` via `asyncio.call_at` | Triggers pre-fire check; if window open → moves to `placed_<stage>` |
| `ResultEvent(result, now_unix)` | M8 broker push event e:26 (or timeout) | Records the result; advances cascade or ends |

Future event types (not M2): `CancelEvent` (for REQUIRE_CONFIRM in v2), `BrokerErrorEvent` (when broker drops, distinct from a result event with `result="error"`).

### 6.3 Transition rules

| Current state | Event | Result |
|---|---|---|
| `pending` | `FireEvent` (initial window open) | `placed_initial` |
| `pending` | `FireEvent` (initial window expired) | `error (signal_expired)` |
| `pending` | `ResultEvent` | Invalid: `TransitionResult(success=False, reason="invalid_event")` |
| `placed_initial` | `FireEvent` | Invalid: must record result first |
| `placed_initial` | `ResultEvent win` | `done_win` |
| `placed_initial` | `ResultEvent loss/tie/timeout` (gale1 window open) | `placed_gale1` |
| `placed_initial` | `ResultEvent loss/tie/timeout` (gale1 window expired) | `error (signal_expired)` |
| `placed_initial` | `ResultEvent error` | `error (broker_unavailable)` |
| `placed_gale1` | `FireEvent` | Invalid: must record result first |
| `placed_gale1` | `ResultEvent win` | `done_win` |
| `placed_gale1` | `ResultEvent loss/tie/timeout` (gale2 window open) | `placed_gale2` |
| `placed_gale1` | `ResultEvent loss/tie/timeout` (gale2 window expired) | `error (signal_expired)` |
| `placed_gale1` | `ResultEvent error` | `error (broker_unavailable)` |
| `placed_gale2` | `FireEvent` | Invalid: gale2 is the last stage |
| `placed_gale2` | `ResultEvent win` | `done_win` |
| `placed_gale2` | `ResultEvent loss/tie/timeout` | `done_loss` (FR-5.7; no time check — gale2 is terminal) |
| `placed_gale2` | `ResultEvent error` | `error (broker_unavailable)` |
| any terminal | any | Invalid: `TransitionResult(success=False, reason="invalid_event: state is terminal")` |

### 6.4 Cumulative PnL

The state carries `cumulative_pnl: Decimal`. Each result event updates it:

- `win`: + `amount * 0.92` (v1 approximation; M8 replaces with broker PnL)
- `loss/tie/timeout`: − `amount`
- `error`: ± 0 (PnL is unknown when broker errors out)

The total cumulative PnL at terminal states:
- `done_win` (initial win): +$1.84
- `done_win` (gale1 win, after initial loss): -$2.00 + $4.00 * 0.92 = +$1.68
- `done_win` (gale2 win, after two losses): -$2.00 - $4.00 + $8.00 * 0.92 = +$1.36
- `done_loss` (gale2 loss): -$2.00 - $4.00 - $8.00 = -$14.00

(These match the FR-7.1 DM message amounts.)

### 6.5 Time-window enforcement (FR-3.3 / FR-3.5 / FR-3.6 / FR-5.9)

The time-window check fires at two distinct points in the cascade:

1. **`FireEvent` on `pending`** — checks the initial stage's window. This is the moment the scheduler fires the initial trade. If the window has already passed, the cascade ends.

2. **`ResultEvent(loss/tie/timeout)` on `placed_initial` or `placed_gale1`** — checks the *next* stage's window. This is the moment the scheduler would have fired the next gale trade. If gale1's window has passed (when transitioning from placed_initial), or gale2's window has passed (when transitioning from placed_gale1), the cascade ends.

The check is: `now_unix > next_stage_trigger_unix + trigger_skew_tolerance_seconds`.

`placed_gale2` is terminal-cascade — a loss at gale2 always goes to `done_loss` with no time check (gale2 has no successor).

**Walkthrough of a missed gale1 (FR-3.6):**
1. `pending` + `FireEvent(now=10:20)` → `placed_initial` (initial's window OK; gale1 trigger is 10:25, gale2 trigger is 10:30).
2. The initial trade's result is delayed. The broker reports a loss at `now=10:30`.
3. M6 calls `transition(placed_initial, ResultEvent(loss, now=10:30))` to advance the cascade.
4. `_advance_after_result` computes gale1's trigger: `state.trigger_unix + 5*60 = 10:20 + 5*60 = 10:25`.
5. Check: `10:30 > 10:25 + 2.0` → True. The gale1 window has passed.
6. Cascade ends with `error (signal_expired)`. No gale1 trade placed.

**Walkthrough of a missed gale2 (FR-3.6 / FR-5.9):**
1. `pending` + `FireEvent(now=10:20)` → `placed_initial`.
2. Initial loses at `now=10:25`. M6 calls `transition(placed_initial, ResultEvent(loss, 10:25))` → gale1 window OK → `placed_gale1` (trigger_unix=10:30).
3. Gale1 loses at `now=10:35`. M6 calls `transition(placed_gale1, ResultEvent(loss, 10:35))`.
4. `_advance_after_result` computes gale2's trigger: `state.trigger_unix + 5*60 = 10:30 + 5*60 = 10:35`. Wait, gale2 trigger was supposed to be 10:30 (per FR-5.6: gale2 = trigger + 10min from initial = 10:20 + 10min = 10:30).

Hmm — there's a subtle issue. `_to_placed` computes `gale2 = state.trigger_unix + 5*60` (where `state.trigger_unix` is gale1's, not initial's). So gale2 = 10:30 + 5*60 = 10:35. But PRD says gale2 = 10:30 (initial + 10min).

Wait, looking at FR-5.5/5.6: gale1 = trigger + 5min, gale2 = trigger + 10min. So gale2 = gale1 + 5min (since gale1 = trigger + 5min, gale2 = trigger + 10min = gale1 + 5min). So gale2 = gale1_trigger + 5min is correct.

In the walkthrough, gale1's trigger is 10:25. So gale2 = 10:25 + 5min = 10:30. ✓

But the issue with my walkthrough is: in step 3, `now=10:35` is past gale2's window (10:30 + 2.0 = 10:30:02). So the cascade ends with error.

5. Check: `10:35 > 10:30 + 2.0` → True.
6. Cascade ends with `error (signal_expired)`. No gale2 trade placed.

### 6.6 Idempotency

M2's `SignalState` is keyed on `signal_id` (from M1's `derive_signal_id()`). M6's persistence layer (M4's `StateStore`) uses `signal_id` as the PK with `ON CONFLICT DO NOTHING` for idempotency. M2 doesn't persist — that's M4/M6. But the state machine's contract is "one `SignalState` per signal_id at any time" — M6 enforces this.

Restart-safety: M2's state machine can be reconstructed from M4's persisted rows. M2's `SignalState.from_signal()` builds a fresh `pending` state; M6's restart logic queries `StateStore.get_active_signals()` to reconstruct in-flight cascades (M6's concern, not M2's).

---

## 7. Test Plan

M2 targets **100% line + branch coverage** on `src/signal_copier/config.py`, `domain/gale.py`, and `domain/state.py` (per the M0/M1 spec convention).

### 7.1 `tests/test_gale_math.py` (~10 tests)

```python
from decimal import Decimal
import pytest
from signal_copier.config import Config
from signal_copier.domain.gale import amount_for_stage, compute_gale_triggers


def _config(**overrides) -> Config:
    return Config(_env_file=None, **overrides)  # _env_file=None ignores .env


# --- amount_for_stage -----------------------------------------------------

def test_amount_for_initial_stage_returns_configured_value() -> None:
    cfg = _config(amount_initial=Decimal("2.00"))
    assert amount_for_stage("initial", cfg) == Decimal("2.00")


def test_amount_for_gale1_stage_returns_configured_value() -> None:
    cfg = _config(amount_gale1=Decimal("4.00"))
    assert amount_for_stage("gale1", cfg) == Decimal("4.00")


def test_amount_for_gale2_stage_returns_configured_value() -> None:
    cfg = _config(amount_gale2=Decimal("8.00"))
    assert amount_for_stage("gale2", cfg) == Decimal("8.00")


def test_amount_for_stage_returns_decimal_not_float() -> None:
    cfg = _config()
    result = amount_for_stage("initial", cfg)
    assert isinstance(result, Decimal)


@pytest.mark.parametrize(
    ("initial", "gale1", "gale2"),
    [
        (Decimal("2.00"), Decimal("4.00"), Decimal("8.00")),   # v1 default
        (Decimal("1.00"), Decimal("2.00"), Decimal("3.00")),   # non-default
        (Decimal("5.50"), Decimal("11.00"), Decimal("22.00")), # arbitrary
    ],
)
def test_amount_for_stage_reads_from_config(initial, gale1, gale2) -> None:
    cfg = _config(amount_initial=initial, amount_gale1=gale1, amount_gale2=gale2)
    assert amount_for_stage("initial", cfg) == initial
    assert amount_for_stage("gale1", cfg) == gale1
    assert amount_for_stage("gale2", cfg) == gale2


# --- compute_gale_triggers -----------------------------------------------

def test_compute_gale_triggers_for_5_minute_expiration() -> None:
    initial_unix = 1_700_000_000.0
    gale1, gale2 = compute_gale_triggers(initial_unix, 300)
    assert gale1 == initial_unix + 300.0
    assert gale2 == initial_unix + 600.0


def test_compute_gale_triggers_for_non_5_minute_expiration() -> None:
    initial_unix = 1_700_000_000.0
    gale1, gale2 = compute_gale_triggers(initial_unix, 60)  # 1-min expiration
    assert gale1 == initial_unix + 60.0
    assert gale2 == initial_unix + 120.0


def test_compute_gale_triggers_returns_floats() -> None:
    gale1, gale2 = compute_gale_triggers(1_700_000_000.0, 300)
    assert isinstance(gale1, float)
    assert isinstance(gale2, float)


def test_compute_gale_triggers_handles_zero_initial() -> None:
    gale1, gale2 = compute_gale_triggers(0.0, 300)
    assert gale1 == 300.0
    assert gale2 == 600.0
```

**Total: ~11 tests** (3 stage-amount + 1 type-check + 1 parametrized × 3 cases + 4 trigger-math = 11 tests run; 9 test functions).

### 7.2 `tests/test_state_machine.py` (~50 tests)

The state machine has 4 pre-terminal + 4 terminal states, 2 event types, 13 valid transitions, ~7 invalid-event branches. The test suite uses helper builders to keep tests concise.

```python
from __future__ import annotations

from decimal import Decimal
import pytest

from signal_copier.config import Config
from signal_copier.domain.gale import Stage
from signal_copier.domain.signal import Signal
from signal_copier.domain.state import (
    ErrorReason,
    FireEvent,
    ResultEvent,
    SignalState,
    StageResult,
    TerminalState,
    transition,
)


# --- Helpers --------------------------------------------------------------

INITIAL_UNIX = 1_700_000_000.0  # 2023-11-14 22:13:20 UTC, an arbitrary Tuesday
GALE1_UNIX = INITIAL_UNIX + 300.0
GALE2_UNIX = INITIAL_UNIX + 600.0


def _config(**overrides) -> Config:
    return Config(
        _env_file=None,
        amount_initial=Decimal("2.00"),
        amount_gale1=Decimal("4.00"),
        amount_gale2=Decimal("8.00"),
        trigger_skew_tolerance_seconds=2.0,
        **overrides,
    )


def _signal(**overrides) -> Signal:
    defaults = dict(
        signal_id="test-sig-001",
        pair="EUR/JPY",
        direction="down",
        trigger_hhmm="10:20",
        expiration_seconds=300,
        received_at_unix=INITIAL_UNIX - 60.0,
        source_message_id=12345,
        source_chat_id=-1001234567890,
        raw_text="💰5-minute expiration\nEUR/JPY;10:20;PUT🟥",
        trigger_unix_initial=INITIAL_UNIX,
        trigger_unix_gale1=GALE1_UNIX,
        trigger_unix_gale2=GALE2_UNIX,
    )
    defaults.update(overrides)
    return Signal(**defaults)


def _initial_state(**overrides) -> SignalState:
    return SignalState.from_signal(_signal(**overrides), _config())


# --- Initial state construction -------------------------------------------

def test_from_signal_creates_pending_state() -> None:
    state = _initial_state()
    assert state.state == "pending"
    assert state.stage == "initial"
    assert state.amount == Decimal("2.00")
    assert state.trigger_unix == INITIAL_UNIX
    assert state.expires_at_unix == INITIAL_UNIX + 300.0
    assert state.result is None
    assert state.cumulative_pnl == Decimal("0.00")
    assert state.error_reason is None


def test_from_signal_uses_config_amounts() -> None:
    cfg = _config(amount_initial=Decimal("5.00"))
    state = SignalState.from_signal(_signal(), cfg)
    assert state.amount == Decimal("5.00")


# --- Pending → placed_initial (happy path) -------------------------------

def test_pending_with_fire_event_at_exact_trigger_moves_to_placed_initial() -> None:
    state = _initial_state()
    result = transition(state, FireEvent(now_unix=INITIAL_UNIX), config=_config())
    assert result.success is True
    assert result.new_state is not None
    assert result.new_state.state == "placed_initial"
    assert result.new_state.stage == "initial"
    assert result.new_state.amount == Decimal("2.00")


def test_pending_with_fire_event_within_tolerance_window_moves_to_placed_initial() -> None:
    state = _initial_state()
    result = transition(state, FireEvent(now_unix=INITIAL_UNIX + 1.5), config=_config())
    assert result.success is True
    assert result.new_state.state == "placed_initial"


def test_pending_with_fire_event_at_tolerance_boundary_succeeds() -> None:
    state = _initial_state()
    result = transition(state, FireEvent(now_unix=INITIAL_UNIX + 2.0), config=_config())
    assert result.success is True
    assert result.new_state.state == "placed_initial"


# --- Pre-fire guard (FR-3.5) ---------------------------------------------

def test_pending_with_fire_event_past_tolerance_ends_cascade_with_error() -> None:
    state = _initial_state()
    result = transition(state, FireEvent(now_unix=INITIAL_UNIX + 3.0), config=_config())
    assert result.success is True
    assert result.new_state.state == "error"
    assert result.new_state.error_reason == "signal_expired"


def test_pending_with_fire_event_far_past_trigger_ends_cascade_with_error() -> None:
    state = _initial_state()
    result = transition(state, FireEvent(now_unix=INITIAL_UNIX + 3600), config=_config())
    assert result.success is True
    assert result.new_state.state == "error"
    assert result.new_state.error_reason == "signal_expired"


def test_pre_fire_guard_uses_config_tolerance() -> None:
    state = _initial_state()
    cfg = _config(trigger_skew_tolerance_seconds=10.0)
    result = transition(state, FireEvent(now_unix=INITIAL_UNIX + 5.0), config=cfg)
    assert result.success is True
    assert result.new_state.state == "placed_initial"


# --- Pending → invalid events --------------------------------------------

def test_pending_with_result_event_returns_invalid_event() -> None:
    state = _initial_state()
    result = transition(state, ResultEvent(result="win", now_unix=INITIAL_UNIX), config=_config())
    assert result.success is False
    assert result.new_state is None
    assert "invalid_event" in (result.reason or "")


# --- Placed_initial transitions ------------------------------------------

def _placed_initial_state() -> SignalState:
    state = _initial_state()
    r = transition(state, FireEvent(now_unix=INITIAL_UNIX), config=_config())
    assert r.success and r.new_state
    return r.new_state


def test_placed_initial_with_win_result_moves_to_done_win() -> None:
    state = _placed_initial_state()
    result = transition(state, ResultEvent(result="win", now_unix=INITIAL_UNIX + 60), config=_config())
    assert result.success
    assert result.new_state.state == "done_win"
    assert result.new_state.stage is None
    assert result.new_state.result == "win"
    assert result.new_state.cumulative_pnl == Decimal("2.00") * Decimal("0.92")


def test_placed_initial_with_loss_result_moves_to_placed_gale1() -> None:
    state = _placed_initial_state()
    result = transition(state, ResultEvent(result="loss", now_unix=INITIAL_UNIX + 300), config=_config())
    assert result.success
    assert result.new_state.state == "placed_gale1"
    assert result.new_state.stage == "gale1"
    assert result.new_state.amount == Decimal("4.00")
    assert result.new_state.trigger_unix == GALE1_UNIX
    assert result.new_state.cumulative_pnl == Decimal("-2.00")


def test_placed_initial_with_tie_result_moves_to_placed_gale1() -> None:
    """FR-5.3: tie at non-final stage is treated as loss for cascade purposes."""
    state = _placed_initial_state()
    result = transition(state, ResultEvent(result="tie", now_unix=INITIAL_UNIX + 300), config=_config())
    assert result.success
    assert result.new_state.state == "placed_gale1"
    assert result.new_state.result == "tie"  # original outcome recorded


def test_placed_initial_with_timeout_result_moves_to_placed_gale1() -> None:
    """FR-5.3: timeout at non-final stage is treated as loss for cascade purposes."""
    state = _placed_initial_state()
    result = transition(state, ResultEvent(result="timeout", now_unix=INITIAL_UNIX + 330), config=_config())
    assert result.success
    assert result.new_state.state == "placed_gale1"
    assert result.new_state.result == "timeout"


def test_placed_initial_with_error_result_moves_to_error_state() -> None:
    state = _placed_initial_state()
    result = transition(state, ResultEvent(result="error", now_unix=INITIAL_UNIX + 60), config=_config())
    assert result.success
    assert result.new_state.state == "error"
    assert result.new_state.error_reason == "broker_unavailable"


def test_placed_initial_with_fire_event_returns_invalid() -> None:
    state = _placed_initial_state()
    result = transition(state, FireEvent(now_unix=INITIAL_UNIX + 60), config=_config())
    assert result.success is False
    assert "invalid_event" in (result.reason or "")


# --- Placed_gale1 transitions --------------------------------------------

def _placed_gale1_state() -> SignalState:
    state = _placed_initial_state()
    r = transition(state, ResultEvent(result="loss", now_unix=INITIAL_UNIX + 300), config=_config())
    assert r.success and r.new_state
    return r.new_state


def test_placed_gale1_with_win_result_moves_to_done_win() -> None:
    state = _placed_gale1_state()
    result = transition(state, ResultEvent(result="win", now_unix=GALE1_UNIX + 60), config=_config())
    assert result.success
    assert result.new_state.state == "done_win"
    # PnL: -2 + 4*0.92 = -2 + 3.68 = +1.68
    assert result.new_state.cumulative_pnl == Decimal("1.68")


def test_placed_gale1_with_loss_result_moves_to_placed_gale2() -> None:
    state = _placed_gale1_state()
    result = transition(state, ResultEvent(result="loss", now_unix=GALE1_UNIX + 300), config=_config())
    assert result.success
    assert result.new_state.state == "placed_gale2"
    assert result.new_state.stage == "gale2"
    assert result.new_state.amount == Decimal("8.00")
    assert result.new_state.trigger_unix == GALE2_UNIX
    assert result.new_state.cumulative_pnl == Decimal("-6.00")


def test_placed_gale1_with_tie_result_moves_to_placed_gale2() -> None:
    state = _placed_gale1_state()
    result = transition(state, ResultEvent(result="tie", now_unix=GALE1_UNIX + 60), config=_config())
    assert result.success
    assert result.new_state.state == "placed_gale2"


def test_placed_gale1_with_fire_event_returns_invalid() -> None:
    state = _placed_gale1_state()
    result = transition(state, FireEvent(now_unix=GALE1_UNIX), config=_config())
    assert result.success is False


def test_placed_gale1_with_error_result_moves_to_error_state() -> None:
    state = _placed_gale1_state()
    result = transition(state, ResultEvent(result="error", now_unix=GALE1_UNIX + 60), config=_config())
    assert result.success
    assert result.new_state.error_reason == "broker_unavailable"


# --- Placed_gale2 transitions --------------------------------------------

def _placed_gale2_state() -> SignalState:
    state = _placed_gale1_state()
    r = transition(state, ResultEvent(result="loss", now_unix=GALE1_UNIX + 300), config=_config())
    assert r.success and r.new_state
    return r.new_state


def test_placed_gale2_with_win_result_moves_to_done_win() -> None:
    state = _placed_gale2_state()
    result = transition(state, ResultEvent(result="win", now_unix=GALE2_UNIX + 60), config=_config())
    assert result.success
    assert result.new_state.state == "done_win"
    # PnL: -2 - 4 + 8*0.92 = -6 + 7.36 = +1.36
    assert result.new_state.cumulative_pnl == Decimal("1.36")


def test_placed_gale2_with_loss_result_moves_to_done_loss() -> None:
    """FR-5.7: gale2 loss = done_loss terminal."""
    state = _placed_gale2_state()
    result = transition(state, ResultEvent(result="loss", now_unix=GALE2_UNIX + 300), config=_config())
    assert result.success
    assert result.new_state.state == "done_loss"
    assert result.new_state.cumulative_pnl == Decimal("-14.00")


def test_placed_gale2_with_tie_result_moves_to_done_loss() -> None:
    """FR-5.3 + FR-5.7: gale2 tie cascades to done_loss (no further stages)."""
    state = _placed_gale2_state()
    result = transition(state, ResultEvent(result="tie", now_unix=GALE2_UNIX + 60), config=_config())
    assert result.success
    assert result.new_state.state == "done_loss"


def test_placed_gale2_with_timeout_result_moves_to_done_loss() -> None:
    state = _placed_gale2_state()
    result = transition(state, ResultEvent(result="timeout", now_unix=GALE2_UNIX + 330), config=_config())
    assert result.success
    assert result.new_state.state == "done_loss"


def test_placed_gale2_with_fire_event_returns_invalid() -> None:
    state = _placed_gale2_state()
    result = transition(state, FireEvent(now_unix=GALE2_UNIX), config=_config())
    assert result.success is False


# --- Time-window enforcement on gale cascade -----------------------------

def test_placed_gale1_with_loss_result_past_gale2_window_ends_cascade() -> None:
    """FR-3.6 / FR-5.9: gale2's window passed before its fire → error.

    At placed_gale1, receiving a loss result should advance to gale2 — but
    if gale2's window has already passed, the cascade ends with error.
    """
    state = _placed_gale1_state()
    # gale1 lost at GALE1_UNIX + 300, gale2 trigger is GALE2_UNIX = GALE1_UNIX + 300.
    # now_unix is well past gale2's trigger + tolerance.
    result = transition(
        state,
        ResultEvent(result="loss", now_unix=GALE2_UNIX + 100.0),
        config=_config(),
    )
    assert result.success
    assert result.new_state.state == "error"
    assert result.new_state.error_reason == "signal_expired"


def test_placed_initial_with_loss_result_past_gale1_window_ends_cascade() -> None:
    """FR-3.6: gale1's window passed before its fire → error."""
    state = _placed_initial_state()
    # initial lost at INITIAL_UNIX + 300, gale1 trigger is GALE1_UNIX = INITIAL_UNIX + 300.
    # now_unix is well past gale1's trigger + tolerance.
    result = transition(
        state,
        ResultEvent(result="loss", now_unix=GALE1_UNIX + 100.0),
        config=_config(),
    )
    assert result.success
    assert result.new_state.state == "error"
    assert result.new_state.error_reason == "signal_expired"


def test_placed_gale2_with_loss_result_at_gale2_window_boundary_succeeds() -> None:
    """At gale2, a loss always goes to done_loss (no time check; gale2 is terminal)."""
    state = _placed_gale2_state()
    result = transition(
        state,
        ResultEvent(result="loss", now_unix=GALE2_UNIX + 100.0),
        config=_config(),
    )
    assert result.success
    assert result.new_state.state == "done_loss"


# --- Terminal states are absorbing ---------------------------------------

@pytest.mark.parametrize("terminal", ["done_win", "done_loss", "error"])
def test_terminal_states_reject_fire_event(terminal: str) -> None:
    """done_tie is excluded — reserved for v2 (D-7), unreachable in M2."""
    state = _initial_state()
    if terminal == "done_win":
        placed = transition(state, FireEvent(now_unix=INITIAL_UNIX), config=_config()).new_state
        r = transition(placed, ResultEvent(result="win", now_unix=INITIAL_UNIX + 60), config=_config())
    elif terminal == "done_loss":
        s1 = transition(state, FireEvent(now_unix=INITIAL_UNIX), config=_config()).new_state
        s2 = transition(s1, ResultEvent(result="loss", now_unix=INITIAL_UNIX + 300), config=_config()).new_state
        s3 = transition(s2, FireEvent(now_unix=GALE1_UNIX), config=_config()).new_state
        s4 = transition(s3, ResultEvent(result="loss", now_unix=GALE1_UNIX + 300), config=_config()).new_state
        s5 = transition(s4, FireEvent(now_unix=GALE2_UNIX), config=_config()).new_state
        r = transition(s5, ResultEvent(result="loss", now_unix=GALE2_UNIX + 300), config=_config())
    else:  # error
        r = transition(state, FireEvent(now_unix=INITIAL_UNIX + 100), config=_config())
    final = r.new_state
    assert final is not None
    result = transition(final, FireEvent(now_unix=INITIAL_UNIX + 60), config=_config())
    assert result.success is False
    assert "invalid_event" in (result.reason or "")


# --- Full cascade tests (end-to-end through the state machine) ----------

def test_full_cascade_initial_win_path() -> None:
    """pending → placed_initial → done_win (win at initial)."""
    s0 = _initial_state()
    s1 = transition(s0, FireEvent(now_unix=INITIAL_UNIX), config=_config()).new_state
    s2 = transition(s1, ResultEvent(result="win", now_unix=INITIAL_UNIX + 60), config=_config()).new_state
    assert s2.state == "done_win"
    assert s2.cumulative_pnl == Decimal("1.84")


def test_full_cascade_gale1_win_path() -> None:
    """pending → placed_initial → placed_gale1 → done_win."""
    s0 = _initial_state()
    s1 = transition(s0, FireEvent(now_unix=INITIAL_UNIX), config=_config()).new_state
    s2 = transition(s1, ResultEvent(result="loss", now_unix=INITIAL_UNIX + 300), config=_config()).new_state
    s3 = transition(s2, FireEvent(now_unix=GALE1_UNIX), config=_config()).new_state
    s4 = transition(s3, ResultEvent(result="win", now_unix=GALE1_UNIX + 60), config=_config()).new_state
    assert s4.state == "done_win"
    # PnL: -2 + 4*0.92 = -2 + 3.68 = +1.68
    assert s4.cumulative_pnl == Decimal("1.68")


def test_full_cascade_gale2_win_path() -> None:
    """pending → placed_initial → placed_gale1 → placed_gale2 → done_win."""
    s0 = _initial_state()
    s1 = transition(s0, FireEvent(now_unix=INITIAL_UNIX), config=_config()).new_state
    s2 = transition(s1, ResultEvent(result="loss", now_unix=INITIAL_UNIX + 300), config=_config()).new_state
    s3 = transition(s2, FireEvent(now_unix=GALE1_UNIX), config=_config()).new_state
    s4 = transition(s3, ResultEvent(result="loss", now_unix=GALE1_UNIX + 300), config=_config()).new_state
    s5 = transition(s4, FireEvent(now_unix=GALE2_UNIX), config=_config()).new_state
    s6 = transition(s5, ResultEvent(result="win", now_unix=GALE2_UNIX + 60), config=_config()).new_state
    assert s6.state == "done_win"
    # PnL: -2 - 4 + 8*0.92 = -6 + 7.36 = +1.36
    assert s6.cumulative_pnl == Decimal("1.36")


def test_full_cascade_full_loss_path() -> None:
    """pending → placed_initial → placed_gale1 → placed_gale2 → done_loss."""
    s0 = _initial_state()
    s1 = transition(s0, FireEvent(now_unix=INITIAL_UNIX), config=_config()).new_state
    s2 = transition(s1, ResultEvent(result="loss", now_unix=INITIAL_UNIX + 300), config=_config()).new_state
    s3 = transition(s2, FireEvent(now_unix=GALE1_UNIX), config=_config()).new_state
    s4 = transition(s3, ResultEvent(result="loss", now_unix=GALE1_UNIX + 300), config=_config()).new_state
    s5 = transition(s4, FireEvent(now_unix=GALE2_UNIX), config=_config()).new_state
    s6 = transition(s5, ResultEvent(result="loss", now_unix=GALE2_UNIX + 300), config=_config()).new_state
    assert s6.state == "done_loss"
    assert s6.cumulative_pnl == Decimal("-14.00")


def test_full_cascade_signal_expired_at_initial() -> None:
    s0 = _initial_state()
    s1 = transition(s0, FireEvent(now_unix=INITIAL_UNIX + 100), config=_config()).new_state
    assert s1.state == "error"
    assert s1.error_reason == "signal_expired"


def test_full_cascade_signal_expired_at_gale1() -> None:
    """Initial loses; gale1's window has already passed (per FR-3.6).

    In the new model, the time check happens on ResultEvent(loss) at
    placed_initial. The ResultEvent carries now_unix past gale1's window.
    """
    s0 = _initial_state()
    s1 = transition(s0, FireEvent(now_unix=INITIAL_UNIX), config=_config()).new_state
    s2 = transition(
        s1,
        ResultEvent(result="loss", now_unix=GALE1_UNIX + 100),  # past gale1's window
        config=_config(),
    ).new_state
    assert s2.state == "error"
    assert s2.error_reason == "signal_expired"


def test_full_cascade_signal_expired_at_gale2() -> None:
    """Gale1 loses; gale2's window has already passed (per FR-3.6)."""
    s0 = _initial_state()
    s1 = transition(s0, FireEvent(now_unix=INITIAL_UNIX), config=_config()).new_state
    s2 = transition(s1, ResultEvent(result="loss", now_unix=INITIAL_UNIX + 300), config=_config()).new_state
    s3 = transition(
        s2,
        ResultEvent(result="loss", now_unix=GALE2_UNIX + 100),  # past gale2's window
        config=_config(),
    ).new_state
    assert s3.state == "error"
    assert s3.error_reason == "signal_expired"
```

**Total: ~38 tests** (36 test functions + 1 parametrized × 3 cases) covering: initial state (2), pending→placed_initial (3), pre-fire guard (3), invalid events on pending (1), placed_initial (6), placed_gale1 (5), placed_gale2 (5), time-window on gale cascade (3 — gale1-loss-past-gale2, initial-loss-past-gale1, gale2-loss-at-boundary), terminal-state absorbing (1 parametrized × 3 cases; `done_tie` excluded per D-7), full cascade paths (7 — 4 happy paths + 3 signal_expired at initial/gale1/gale2).

### 7.3 `tests/test_config.py` (~12 tests)

```python
from decimal import Decimal
import pytest
from pydantic import ValidationError
from signal_copier.config import Config


def _config(**overrides) -> Config:
    return Config(_env_file=None, **overrides)


# --- Defaults -------------------------------------------------------------

def test_default_amount_initial_is_2_00() -> None:
    assert _config().amount_initial == Decimal("2.00")


def test_default_amount_gale1_is_4_00() -> None:
    assert _config().amount_gale1 == Decimal("4.00")


def test_default_amount_gale2_is_8_00() -> None:
    assert _config().amount_gale2 == Decimal("8.00")


def test_default_dry_run_is_true() -> None:
    assert _config().dry_run is True


def test_default_olymp_account_group_is_demo() -> None:
    assert _config().olymp_account_group == "demo"


def test_default_timezone_is_sao_paulo() -> None:
    assert _config().timezone == "America/Sao_Paulo"


# --- TZ validation --------------------------------------------------------

def test_valid_timezone_passes() -> None:
    cfg = _config(timezone="UTC")
    assert cfg.tz().key == "UTC"


def test_invalid_timezone_raises() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _config(timezone="Mars/Olympus_Mons")
    assert "unknown timezone" in str(exc_info.value)


# --- Account group validation --------------------------------------------

def test_account_group_real_with_dry_run_true_is_allowed() -> None:
    cfg = _config(olymp_account_group="real", dry_run=True)
    assert cfg.olymp_account_group == "real"


def test_account_group_real_with_dry_run_false_refuses_to_start() -> None:
    """FR-6.6: real account + dry_run off → app refuses to start."""
    with pytest.raises(ValidationError) as exc_info:
        _config(olymp_account_group="real", dry_run=False)
    msg = str(exc_info.value)
    assert "Refusing to start" in msg
    assert "DRY_RUN=true" in msg


def test_account_group_demo_with_dry_run_false_is_allowed() -> None:
    cfg = _config(olymp_account_group="demo", dry_run=False)
    assert cfg.olymp_account_group == "demo"


def test_account_group_invalid_value_raises() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _config(olymp_account_group="sandbox")
    assert "must be 'demo' or 'real'" in str(exc_info.value)
```

**Total: ~12 tests** covering: defaults (6), TZ validation (2), account group + FR-6.6 guardrail (4).

### 7.4 Test running

```bash
# All M2 tests
uv run pytest tests/ -v

# With coverage on M2's new modules
uv run pytest tests/ \
  --cov=signal_copier.config \
  --cov=signal_copier.domain.gale \
  --cov=signal_copier.domain.state \
  --cov-report=term-missing
# Expect: 100% line + branch coverage on config.py, gale.py, state.py
```

---

## 8. Handoff to M3+

**M3 (broker/dry_run.py + Broker Protocol)** — PRD §15 M3 row: "`broker/dry_run.py` + `Broker` Protocol".

- M3 implements the `Broker` Protocol. The `DryRunBroker` is the default for v1.
- M2 doesn't define a `Broker` Protocol — that's M3's job. M3 reads `Config.dry_run` to choose between `DryRunBroker` and the real `OlympTradeBroker` (M8).
- M3 may use `Stage`, `amount_for_stage` from `signal_copier.domain.gale` to know the bet amount for a stage.

**M4 (DB + migrations)** — PRD §15 M4 row: asyncpg pool + schema + `StateStore`.

- M4 reads `Config.database_url`. The M4 `StateStore` is broker-agnostic; it persists `Signal` (with the new `trigger_unix_*` fields from D-5) and stage results.
- M2's `SignalState` is the in-memory shape; M4 defines its own `StageRecord` / `DailySummary` types based on the schema in PRD §9.

**M5 (Telegram listener)** — PRD §15 M5 row: Telethon + message parsing.

- M5 constructs the full `Signal` dataclass: calls `parse_signal()` (M1) → `derive_signal_id()` (M1) → converts `trigger_hhmm` to `trigger_unix_initial/gale1/gale2` using `Config.timezone` and the signal's date (D-5) → sets `received_at_unix`, `source_message_id`, `source_chat_id`, `raw_text`.
- M5 imports `Config` from `signal_copier.config` and validates the listener's chat at startup.

**M6 (scheduler + `__main__.py` glue)** — PRD §15 M6 row: `asyncio.call_at` + glue.

- M6 reads `Config.trigger_skew_tolerance_seconds` to know how much pre-fire slack to allow.
- M6 dispatches `FireEvent(now_unix=loop.time())` at the stage's `trigger_unix`.
- M6 receives `ResultEvent` from M8 (broker push) and dispatches to `transition()`.
- M6 wires the daily-limit gate (FR-6.1/6.2/6.3) using M4's `daily_summary` table — per D-4 this is M6's concern.
- M6 calls `state_store.upsert_signal(state)` on every `TransitionResult` where `success=True` (FR-5.8).

**M7 (notifications)** — PRD §15 M7 row: Telegram self-DM per FR-7.1.

- M7 replaces the `infra/log.py` stub with loguru.
- M7 reads `state.cumulative_pnl` and `state.error_reason` to format the DM messages (per FR-7.1 table).
- M2's `cumulative_pnl` field is what the WIN/LOSS DMs reference.

**M8 (broker/olymp.py)** — PRD §15 M8 row: vendored `olymptrade_ws` + pair-mapping.

- M8 calls `OlympTradeClient.place_order()` and listens for the e:26 push event.
- M8 returns the broker's reported PnL (replacing M2's `amount * 0.92` approximation).
- M8 dispatches `ResultEvent(result=..., now_unix=loop.time())` to M6's scheduler.

**M11 (deployment)** — Runs M2's `__main__.py`. The FR-6.6 guardrail fires if the env is misconfigured (e.g., `OLYMP_ACCOUNT_GROUP=real` + `DRY_RUN=false`).

---

## 9. Verification Criteria (M2 Done = all of these pass)

| # | Command | Expected |
|---|---|---|
| V-1 | `uv lock && uv sync` | Succeeds; new `pydantic-settings` dep installed; `uv.lock` updated |
| V-2 | `uv run pytest tests/ -v` | All tests pass (1 M0 + 32 M1 + 11 gale + 38 state machine + 12 config = 94 total) |
| V-3 | `uv run pytest tests/ --cov=signal_copier.config --cov=signal_copier.domain.gale --cov=signal_copier.domain.state --cov-report=term-missing` | **100% line + branch coverage** on the three new modules |
| V-4 | `uv run pytest tests/test_parser.py -v` | All 32 M1 tests still pass (M2's `Signal` extension didn't break them) |
| V-5 | `uv run pytest tests/test_main.py -v` | M0's stub test still passes |
| V-6 | `uv run ruff check src/signal_copier tests` | "All checks passed!" |
| V-7 | `uv run ruff format --check src/signal_copier tests` | No output (formatted) |
| V-8 | `uv run mypy src/signal_copier tests` | "Success: no issues found in N source files" |
| V-9 | `uv run python -m signal_copier` | Prints `🟢 signal_copier M2 started (config loaded)` + config summary; exit 0 |
| V-10 | `uv run python -m signal_copier` (with `OLYMP_ACCOUNT_GROUP=real` + `DRY_RUN=false` in env) | Exits 2 with "Refusing to start: OLYMP_ACCOUNT_GROUP=real requires DRY_RUN=true" |
| V-11 | `uv run signal-copier` | Same as V-9 (console-script entry) |
| V-12 | `docker build -t signal-copier-m2 .` | Succeeds (Dockerfile from M0 still works) |
| V-13 | `docker run --rm signal-copier-m2` | Same as V-9 (printed to stdout, exit 0) |

**Definition of Done for M2:** all V-1 through V-13 pass. No scope creep into M3+ concerns. Surgical changes only to M0/M1 files explicitly named in §4.2 (Signal dataclass), §4.5 (domain `__init__.py`), §4.6 (`__main__.py`), and §4.8 (`pyproject.toml`).

---

## 10. Risks & Edge Cases

1. **PnL approximation drift.** M2's `_stage_pnl()` uses `amount * 0.92` for wins (v1). Real OlympTrade payouts vary by asset + category (digital 92%, forex ~85%, OTC ~80%). M8 replaces this with broker-reported PnL. **Risk:** if a test or DM uses M2's approximate PnL and someone trusts it as authoritative, they'll be surprised. **Mitigation:** the comment on `_stage_pnl()` explicitly states "M8 will replace with broker-reported PnL", and the WIN DMs (M7) should use the broker's reported PnL — M2's approximation is for cascade logic only (cascade math is the same regardless of PnL precision).

2. **Frozen state and async restarts.** `SignalState` is `@dataclass(frozen=True, slots=True)`. M6 restarts require reconstructing state from M4's persisted rows. **Risk:** a field that's `frozen=True` can't be patched if M4's schema evolves. **Mitigation:** M4's `StateStore` is the source of truth; `SignalState` is reconstructed via `from_signal()`. If the schema changes, the constructor signature changes too, forcing an explicit update.

3. **Time-window tolerance edge case.** `trigger_skew_tolerance_seconds=0.0` means the pre-fire check rejects any fire at `now_unix > trigger_unix` (inclusive). M2's check is `> trigger_unix + tolerance`, so `tolerance=0` rejects at the trigger time itself. **Mitigation:** the config default is `2.0` seconds. The test `test_pending_with_fire_event_at_tolerance_boundary_succeeds` confirms the boundary case works.

4. **`done_tie` is unreachable in M2.** D-7 reserves the type value for v2. M2's transitions never produce a `done_tie` state. **Risk:** future maintainers might think `done_tie` is reachable. **Mitigation:** the type union includes it with a comment, and the `terminal_state_parametrize` test in §7.2 documents its synthetic-only construction (excluded from the parametrized list).

5. **`Decimal` vs `float` in PnL computation.** M2 uses `Decimal` for amounts and PnL (precision-safe), but `_stage_pnl` does `state.amount * Decimal("0.92")` which produces a Decimal. The `cumulative_pnl` field stays Decimal throughout. **Risk:** mypy might flag implicit conversions. **Mitigation:** all arithmetic in M2 is `Decimal` (no mixing). M6 reads `cumulative_pnl` as `Decimal`; if it needs to display as a float, it converts at the boundary.

6. **Config field name vs env var name.** pydantic-settings maps `amount_initial` (Python field) to `AMOUNT_INITIAL` (env var). Case-insensitive. **Risk:** a future maintainer might assume a different mapping. **Mitigation:** `case_sensitive=False` is set; `.env.example` uses the exact env var names that pydantic-settings expects.

7. **`_env_file=None` in test helpers.** `Config(_env_file=None, ...)` disables `.env` reading. **Risk:** tests don't accidentally load a real `.env` from the developer's machine. **Mitigation:** explicit `_env_file=None` in every test helper. If M2+ tests need to load a fixture `.env`, a `tests/conftest.py` pattern can land then.

8. **No `pytest-asyncio` exercised.** M2 is sync (D-11). `pytest-asyncio` is in dev deps (from M0) but unused. **Mitigation:** the import is still valid for M6+; removal can happen if it's still unused after M6.

9. **`Signal.trigger_unix_*` field addition breaks M1's spec.** M1's spec §3 says `Signal` has 9 fields. M2 adds 3. The M1 test file doesn't construct `Signal` (only `ParsedSignal`), so tests don't break — but the M1 spec is now slightly out of date. **Mitigation:** document the change in this spec; M1's spec remains a snapshot of M1's deliverable.

10. **Time arithmetic in `_to_placed` hardcodes 5/10 minutes.** This couples M2 to the 5-minute expiration. **Mitigation:** M2's behavior matches PRD FR-5.5/5.6 (gales at trigger+5min, +10min). v2's `require_confirm` or per-channel strategies may change this; that's a future M2+ iteration. M2 documents the assumption in a code comment on `_to_placed`.

---

## 11. Out of Scope (deferred to future milestones)

- ❌ `infra/clock.py` (PRD §7). M2 puts time helpers in `domain/gale.py`; M6 can extract to `infra/clock.py` if its needs diverge.
- ❌ Real async support. M2 is sync; M6 introduces `asyncio` and dispatches events to `transition()`.
- ❌ Persistence. M2's `SignalState` lives in memory; M4's `StateStore` + M6 wire it to PostgreSQL.
- ❌ `psycopg` / `asyncpg` / `loguru` / `telethon` / extra `pydantic-settings` features. Only `pydantic-settings` (and its transitive deps) lands in M2.
- ❌ Telegram self-DM notifications. M7 wires per FR-7.1.
- ❌ Real broker integration. M8 wraps `olymptrade_ws.OlympTradeClient`.
- ❌ Daily-limit enforcement (FR-6.1/6.2/6.3). M6 uses M4's `daily_summary` table.
- ❌ Restart-from-persisted-state. M6 queries `StateStore.get_active_signals()` on boot.
- ❌ `infra/db.py`. M4 creates it.
- ❌ Replacing `infra/log.py` stub with loguru. M7 does it.
- ❌ Modifying `src/olymptrade_ws/` (vendored). Hard rule per R-15.
- ❌ Modifying `Dockerfile`, `railway.toml`, `.dockerignore`, `.python-version`, `.gitignore`, `README.md`. M0's deploy shape stays intact.
- ❌ CI / GitHub Actions. Per M0 D-5; revisit in M3+ if test surface justifies it.

---

## 12. Transition to Implementation

After this spec is approved, the next step is to invoke the **writing-plans** skill to produce a detailed, step-by-step implementation plan. The plan will enumerate:

- File creation order (`config.py` → `gale.py` → `state.py` → `__init__.py` re-exports → tests)
- TDD test-by-test breakdown for each module
- `pyproject.toml` and `uv.lock` updates
- Per-step verification (V-1 through V-13)
- M0/M1 regression checks (V-4, V-5)
