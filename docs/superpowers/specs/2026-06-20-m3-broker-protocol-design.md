# M3 — Broker Protocol & DryRunBroker Design

**Date:** 2026-06-20
**Status:** Draft (pending user review)
**PRD reference:** `docs/PRD.md` v0.7 (§4.4 Trade Executor, §6 Tech Stack, §7 Architecture, §15 Build Plan M3 row, §4.6 Safety & Limits FR-6.5)
**Build plan reference:** PRD §15, M3 row

---

## 1. Purpose & Scope

M3 is the fourth milestone of the Telegram → OlympTrade Signal Copier (PRD v0.7). It ships the **broker abstraction layer** — a `Broker` Protocol that any concrete broker implementation conforms to, plus the v1-default `DryRunBroker` that logs intended trades without ever touching a real broker.

**M3 ships no I/O.** No broker connection, no Telegram, no DB, no push events. M3 is the broker-side shape that M6 (scheduler), M8 (real OlympTradeBroker), and M9 (end-to-end soak) plug into.

**In scope for M3 (6 new/modified files):**

| # | File | Type | Purpose |
|---|---|---|---|
| 1 | `src/signal_copier/broker/__init__.py` | NEW | Empty package marker + re-export `Broker`, `UnsupportedPairError` |
| 2 | `src/signal_copier/broker/base.py` | NEW | `Broker` Protocol + `UnsupportedPairError` exception |
| 3 | `src/signal_copier/broker/dry_run.py` | NEW | `DryRunBroker` with pluggable outcome provider |
| 4 | `src/signal_copier/__init__.py` | MODIFY | Re-export `Broker`, `UnsupportedPairError` for top-level access |
| 5 | `tests/test_broker_protocol.py` | NEW | Protocol conformance tests (~6 tests) |
| 6 | `tests/test_dry_run_broker.py` | NEW | DryRunBroker behavior tests (~12 tests) |

**Out of scope (deferred to later milestones):**

| Concern | Lands in |
|---|---|
| `broker/olymp.py` wrapping the vendored `olymptrade_ws.OlympTradeClient` | M8 |
| Real WebSocket connection to OlympTrade + asset-map auto-discover (R-11) | M8 |
| Push events (`E_TRADE_CLOSED = 26`) → `wait_result` bridging | M8 |
| Wiring `Broker` into the M6 scheduler (`asyncio.call_at` ↔ `broker.place`/`wait_result`) | M6 |
| `infra/log.py` (logging config, replaced with loguru) | M7 |
| Daily-limit enforcement using broker PnL | M6 + M4 |
| DB writes of `trade_id` from broker into `stages` table | M6 (uses M4's `StateStore`) |

**What M3's `Broker` Protocol defines (per PRD §4.4 + D-1/D-2):**

- 4 async methods: `connect()`, `place()`, `wait_result()`, `close()`
- `place()` returns a `trade_id: str` (used as the `stages.trade_id` PK in M4/M6)
- `place()` may raise `UnsupportedPairError` for signals whose pair the broker can't trade
- `wait_result()` returns `StageResult` (M2's existing literal union) — D-1
- Lifecycle: `connect()` before any `place()`, `close()` on shutdown, both idempotent

**What M3's `DryRunBroker` defines (per D-3/D-4/D-6/D-7):**

- Default outcome is always `"win"` — matches what the strategy would do in real use 90%+ of the time
- Pluggable `outcome_provider: Callable[[Signal, Stage], Awaitable[StageResult]]` for tests
- Logs every `place()` call (structured INFO log line) so M9 soak leaves an audit trail
- Returns a deterministic-ish `trade_id` prefixed with `dryrun-` so DB rows are identifiable
- No `Config` dependency — caller (M6) reads `Config` and passes the amount

---

## 2. Resolved Decisions (M3-specific)

The PRD resolves all architectural questions (R-1 through R-15). The following are M3-specific scoping calls, confirmed during brainstorming on 2026-06-20.

| # | Decision | Rationale |
|---|---|---|
| D-1 | **`TradeResult` is not a separate type — `Broker.wait_result()` returns `StageResult` directly** | M2 already ships `StageResult = Literal["win", "loss", "tie", "timeout", "error"]` as the state-machine contract. Introducing a separate `TradeResult` dataclass in M3 would either duplicate this union or force a mapping layer that adds no value in M3. M8 may extend M2's `SignalState` to carry broker-reported PnL (replacing `_stage_pnl`'s `amount * 0.92` approximation) — that's an M8 concern, not M3. |
| D-2 | **`Broker.place()` uses `Decimal` for `amount`** (refining PRD §4.4's `float`) | PRD §4.4 sketches `amount: float`. M2 uses `Decimal` for all money (`Config.amount_*`, `SignalState.amount`, `cumulative_pnl`). Float would force a cast at every call site and reintroduce precision drift in the cascade. Align with M2. M8's `OlympTradeBroker` converts at the broker boundary if its internal API needs cents-as-float. |
| D-3 | **Pluggable async outcome provider; default returns `"win"`** | `DryRunBroker.outcome_provider: Callable[[Signal, Stage], Awaitable[StageResult]] = _default_outcome`. Default returns `"win"` (matches real-world hit-rate assumption for the strategy). Tests inject deterministic providers (`async def loss_all(s, st): return "loss"`) to drive specific cascade paths (WIN-only, LOSS/LOSS/WIN, full LOSS, etc.) without sleeping. M9 soak uses the default. |
| D-4 | **No `Config` dependency in `Broker`** | `Broker.place(signal, *, stage, amount)` is parameterized; caller (M6) reads `Config` and passes `amount`. `Broker.wait_result(trade_id, *, timeout)` takes the timeout as a param. Keeps the abstraction broker-agnostic — a future Binance/Saxo/etc. broker implementation doesn't import `signal_copier.config`. Matches M2's "state machine is the orchestrator" pattern. |
| D-5 | **`Broker` is a `typing.Protocol` with `@runtime_checkable`** | PRD §4.4 specifies `Protocol`. `runtime_checkable` lets tests assert `isinstance(DryRunBroker(), Broker)` as a sanity gate so a future broker refactor that drifts the contract fails loudly. |
| D-6 | **`DryRunBroker` uses stdlib `logging.getLogger(__name__)`** | M7 replaces `infra/log.py` with loguru. For M3, stdlib logging is always-available, zero-dep, and loguru can route through stdlib later. No new dependency in `pyproject.toml` for M3. |
| D-7 | **`DryRunBroker.wait_result()` returns immediately (no sleep)** | The "expiration timing" is conceptually M6's concern — M6 owns scheduling (`asyncio.call_at`) and expiration-grace timeouts. M3's broker is contract-only. Tests get instant results; M9's M6 layer can wrap `broker.wait_result(...)` in `asyncio.sleep(expiration_seconds + 30)` if realistic timing matters for the soak. |
| D-8 | **`UnsupportedPairError` lives in `broker/base.py`** | It's part of the `Broker.place()` contract. M8's `OlympTradeBroker` raises it; M3's `DryRunBroker` never raises it (it accepts any pair since nothing real happens). Defining it in M3 means M8 imports a name that already exists and the Protocol's exception contract is documented from day one. |
| D-9 | **`Broker.place()` uses keyword-only `stage` and `amount`** | Positional `signal` stays first (matches how M6 calls it). `stage` and `amount` are keyword-only — they're both domain-meaningful and `place` calls are easier to grep/audit. mypy + the test suite enforce this. |
| D-10 | **`DryRunBroker` is `@dataclass(slots=True)` but NOT `frozen=True`** | Needs an internal `_placed: dict[str, tuple[Signal, Stage]]` field to map `trade_id` → `(signal, stage)` so `wait_result` can call `outcome_provider(signal, stage)` (which is the natural signature for the provider). A frozen dataclass can't hold mutable state. The internal dict is bounded — `wait_result` pops its entry, so growth is O(in-flight trades), not O(all-time trades). |
| D-11 | **Trade-id encoding: `f"dryrun-{signal_id}-{stage}-{uuid4hex[:8]}"`** | `signal_id` is human-greppable in logs and DB rows; `stage` makes the lifecycle obvious; `uuid4hex[:8]` guarantees uniqueness across re-placements of the same `(signal_id, stage)`. Real brokers (M8) generate trade-ids server-side and don't need this encoding. |
| D-12 | **`account_group` is a constructor field on `DryRunBroker` (informational, defaults to `"demo"`)** | Lets M6 (or future code) construct a `DryRunBroker` with `account_group="real"` for hypothetical real-money dry-runs. The M2 config guardrail (`OLYMP_ACCOUNT_GROUP=real + DRY_RUN=false` is refused) means a real-money `DryRunBroker` is theoretically possible (real account, no live orders). Not enforced in M3; documented for future flexibility. |

---

## 3. Repository Layout (post-M3)

```
olymptrade/
├── pyproject.toml                          # (unchanged from M2)
├── src/
│   ├── olymptrade_ws/                      # (unchanged, vendored)
│   └── signal_copier/
│       ├── __init__.py                     # MODIFY: re-export Broker, UnsupportedPairError
│       ├── __main__.py                     # (unchanged from M2)
│       ├── config.py                       # (unchanged from M2)
│       ├── broker/                         # NEW
│       │   ├── __init__.py                 # NEW: re-export Broker, UnsupportedPairError
│       │   ├── base.py                     # NEW: Broker Protocol + UnsupportedPairError
│       │   └── dry_run.py                  # NEW: DryRunBroker
│       ├── domain/
│       │   ├── __init__.py                 # (unchanged from M2)
│       │   ├── signal.py                   # (unchanged from M2)
│       │   ├── gale.py                     # (unchanged from M2)
│       │   └── state.py                    # (unchanged from M2)
│       └── infra/
│           ├── __init__.py                 # (unchanged from M2)
│           └── log.py                      # (unchanged from M2 stub)
└── tests/
    ├── test_broker_protocol.py             # NEW
    ├── test_dry_run_broker.py              # NEW
    ├── test_main.py                        # (unchanged from M2)
    ├── test_parser.py                      # (unchanged from M1)
    ├── test_gale_math.py                   # (unchanged from M2)
    ├── test_state_machine.py               # (unchanged from M2)
    └── test_config.py                      # (unchanged from M2)
```

**Notable choices:**

- `src/signal_copier/broker/` is a new top-level package (sibling of `domain/` and `infra/`). Matches PRD §7 architecture tree (`broker/` is listed as a top-level package with `base.py`, `dry_run.py`, `olymp.py`).
- `tests/test_broker_protocol.py` and `tests/test_dry_run_broker.py` are sibling files at the top of `tests/`, matching the M0/M1/M2 convention (no `tests/broker/` subdirectory).
- `src/signal_copier/__init__.py` gets a small re-export so `from signal_copier import Broker` works for callers that prefer the top-level path. The canonical import is still `from signal_copier.broker import Broker`.

---

## 4. Key File Contents

### 4.1 `src/signal_copier/broker/base.py` (NEW)

```python
from __future__ import annotations

from decimal import Decimal
from typing import Protocol, runtime_checkable

from signal_copier.domain.gale import Stage
from signal_copier.domain.signal import Signal
from signal_copier.domain.state import StageResult


class UnsupportedPairError(Exception):
    """Raised by Broker.place() when the signal's pair is not available on the broker.

    The state machine catches this and marks the signal status='error'
    with error_reason='unsupported_pair' (PRD §10). M8's OlympTradeBroker
    is the canonical raiser; M3's DryRunBroker never raises this.
    """


@runtime_checkable
class Broker(Protocol):
    """Broker-agnostic trading surface used by the scheduler (M6) and state
    machine (M2). Two concrete implementations exist in v1:

      - DryRunBroker       (M3, default for v1, FR-6.5: DRY_RUN=true)
      - OlympTradeBroker   (M8, wraps the vendored olymptrade_ws client)

    All methods are async because real brokers are I/O-bound (M8). M3's
    DryRunBroker is also async to keep the Protocol uniform; tests use
    pytest-asyncio (asyncio_mode="auto", already configured in M0).
    """

    async def connect(self) -> None:
        """Establish any required connection (Telethon session, WS handshake,
        asset-map fetch). Idempotent — second call is a no-op."""

    async def place(
        self,
        signal: Signal,
        *,
        stage: Stage,
        amount: Decimal,
    ) -> str:
        """Submit a trade for `signal` at `stage` for `amount` USD.

        Returns the broker's trade_id, which the scheduler uses to identify
        the trade in `wait_result` and which M6 persists as `stages.trade_id`.

        Raises UnsupportedPairError if `signal.pair` is not available on this
        broker. The state machine catches this and ends the cascade with
        status='error' (PRD §10).
        """

    async def wait_result(
        self,
        trade_id: str,
        *,
        timeout: float,
    ) -> StageResult:
        """Block until the broker reports a terminal result for `trade_id`,
        or until `timeout` seconds elapse.

        Returns one of M2's StageResult literals: 'win' | 'loss' | 'tie'
        | 'timeout' | 'error'. The 'timeout' literal here means the
        *broker-reporting* timeout — distinct from the per-stage
        expiration-grace timeout in PRD FR-5.3, which M6 owns.
        """

    async def close(self) -> None:
        """Tear down any connection. Idempotent. Called on shutdown and on
        unhandled broker errors so M6 can reconnect cleanly."""
```

**Notes:**

- `place()` is keyword-only on `stage` and `amount` (D-9); positional `signal` stays first.
- `amount: Decimal` aligns with M2's money type (D-2).
- `wait_result()` returns `StageResult` directly (D-1) — no separate `TradeResult` dataclass.
- `connect()`/`close()` are explicit lifecycle hooks, separate from `place()`/`wait_result()`. Real brokers (M8) need a WS handshake before the first order; M6 calls `connect()` at boot.
- `@runtime_checkable` enables `isinstance(broker, Broker)` checks in tests (D-5).

### 4.2 `src/signal_copier/broker/dry_run.py` (NEW)

```python
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import ClassVar
from uuid import uuid4

from signal_copier.broker.base import Broker
from signal_copier.domain.gale import Stage
from signal_copier.domain.signal import Signal
from signal_copier.domain.state import StageResult


_log = logging.getLogger(__name__)


async def _default_outcome(signal: Signal, stage: Stage) -> StageResult:
    """Default outcome provider: every trade wins.

    Matches the analyst's signal strategy in real-world conditions
    (90%+ of signals hit before gale2). M9 soak uses this default.
    """
    _ = signal, stage
    return "win"


# OutcomeProvider is async so M8's real broker (or future tests needing IO)
# can be a drop-in. The default and most test providers are sync internally;
# they still need `async def` to match this signature.
OutcomeProvider = Callable[[Signal, Stage], Awaitable[StageResult]]


@dataclass(slots=True)
class DryRunBroker:
    """Logs intended trades and returns a configurable outcome without ever
    touching a real broker. Default for v1 (FR-6.5: DRY_RUN=true).

    Not frozen (D-10): holds an internal _placed dict mapping trade_id to
    (signal, stage) so wait_result can call outcome_provider(signal, stage).
    The dict is bounded — wait_result pops its entry, so growth is
    O(in-flight trades), not O(all-time trades).
    """

    outcome_provider: OutcomeProvider = _default_outcome
    account_group: str = "demo"  # informational only; M2's Config guardrail
                                  # is the authoritative enforcement
    _placed: dict[str, tuple[Signal, Stage]] = field(
        default_factory=dict, init=False, repr=False,
    )

    _PREFIX: ClassVar[str] = "dryrun"  # trade_id prefix; makes DB rows identifiable

    async def connect(self) -> None:
        _log.info(
            "DryRunBroker connected (account_group=%s)", self.account_group,
        )

    async def place(
        self,
        signal: Signal,
        *,
        stage: Stage,
        amount: Decimal,
    ) -> str:
        trade_id = (
            f"{self._PREFIX}-{signal.signal_id}-{stage}-{uuid4().hex[:8]}"
        )
        self._placed[trade_id] = (signal, stage)
        _log.info(
            "DRY-RUN place: pair=%s direction=%s stage=%s "
            "amount=%s signal_id=%s trade_id=%s",
            signal.pair, signal.direction, stage, amount,
            signal.signal_id, trade_id,
        )
        return trade_id

    async def wait_result(
        self,
        trade_id: str,
        *,
        timeout: float,  # noqa: ARG002 — D-7: dry-run ignores timeout
    ) -> StageResult:
        _log.info("DRY-RUN wait_result: trade_id=%s (instant)", trade_id)
        try:
            signal, stage = self._placed.pop(trade_id)
        except KeyError:
            # Defensive: unknown trade_id means a caller bug. M6 is the only
            # caller; this should never fire in production. Surface it as
            # 'error' so the state machine ends the cascade cleanly.
            _log.warning(
                "DRY-RUN wait_result: unknown trade_id=%s; returning 'error'",
                trade_id,
            )
            return "error"
        return await self.outcome_provider(signal, stage)

    async def close(self) -> None:
        _log.info("DryRunBroker closed")
```

**Notes:**

- `@dataclass(slots=True)` but **not `frozen=True`** — D-10. The mutable `_placed` field is `init=False, repr=False` so it's internal and doesn't appear in `__repr__`.
- `_default_outcome` is `async def` returning `"win"` — matches the `OutcomeProvider` signature (D-3).
- `account_group` defaults to `"demo"` (D-12). It's logged on `connect()` for audit-trail purposes.
- Trade-id encoding per D-11: `f"dryrun-{signal_id}-{stage}-{uuid4hex[:8]}"`. Human-greppable in logs and DB rows; unique across re-placements.
- `wait_result()` pops from `_placed` (D-10) so the dict stays bounded. If a caller passes an unknown `trade_id` (a bug), we return `"error"` defensively rather than raising — keeps the contract simple for M6.
- `timeout` parameter is intentionally unused (D-7); `noqa: ARG002` documents it.

### 4.3 `src/signal_copier/broker/__init__.py` (NEW)

```python
from signal_copier.broker.base import Broker, UnsupportedPairError

__all__ = ["Broker", "UnsupportedPairError"]
```

### 4.4 `src/signal_copier/__init__.py` (MODIFY)

```python
from signal_copier.broker.base import Broker, UnsupportedPairError

__all__ = ["Broker", "UnsupportedPairError"]
```

**Notes:**
- The current `__init__.py` (from M2) is empty. M3 adds the two re-exports.
- Canonical import path is `from signal_copier.broker import Broker`; the top-level re-export is a convenience for callers that prefer it.

---

## 5. Dependency Changes

**No changes to `pyproject.toml`.** M3 uses stdlib only:

| Symbol | Source | Purpose |
|---|---|---|
| `logging.getLogger` | stdlib | Structured INFO logging (D-6) |
| `dataclasses.dataclass` / `field` | stdlib | Broker dataclasses |
| `decimal.Decimal` | stdlib | Money type (M2 alignment, D-2) |
| `uuid.uuid4` | stdlib | Trade-id uniqueness suffix (D-11) |
| `typing.Protocol`, `runtime_checkable` | stdlib | Broker Protocol (D-5) |
| `collections.abc.Awaitable`, `Callable` | stdlib | OutcomeProvider type |
| `typing.ClassVar` | stdlib | `_PREFIX` class constant (excluded from dataclass field generation) |

No new dev dependencies either. `pytest` + `pytest-asyncio` are already in M0's `pyproject.toml`; M3's tests use `asyncio_mode = "auto"` (already configured).

**Docker image size impact:** zero. No new packages.

---

## 6. Architecture

### 6.1 Sequence diagram (M3 + M2 + M6 + M8 + M9)

```
   M5 Listener                M6 Scheduler                M3 DryRunBroker (or M8 OlympTradeBroker)
        │                          │                              │
        │ enqueue Signal           │                              │
        ├─────────────────────────▶│                              │
        │                          │ build SignalState.from_signal│
        │                          │ transition(FireEvent, now)   │
        │                          │ ┌─────────────────┐          │
        │                          │ │ state machine   │          │
        │                          │ │ (M2 pure fn)    │          │
        │                          │ └─────────────────┘          │
        │                          │                              │
        │                          │ broker.connect() (M6 boot)   │
        │                          ├─────────────────────────────▶│
        │                          │                              │
        │                          │ await broker.place(sig, *,   │
        │                          │       stage="initial",       │
        │                          │       amount=Decimal("2"))   │
        │                          ├─────────────────────────────▶│
        │                          │                              │ log "DRY-RUN place..."
        │                          │◀──────────── trade_id ──────┤
        │                          │                              │
        │                          │ persist stages row (M6+M4)   │
        │                          │                              │
        │                          │ await broker.wait_result(    │
        │                          │       trade_id, *,           │
        │                          │       timeout=330.0)         │
        │                          ├─────────────────────────────▶│
        │                          │                              │ log "DRY-RUN wait_result..."
        │                          │◀───── StageResult ───────────┤  (instant for DryRun)
        │                          │                              │
        │                          │ transition(ResultEvent, now) │
        │                          │ ┌─────────────────┐          │
        │                          │ │ state machine   │          │
        │                          │ └─────────────────┘          │
        │                          │                              │
        │                          │ ... (cascade continues or    │
        │                          │      reaches terminal) ...   │
```

### 6.2 Lifecycle

| Phase | M6 calls | M3 DryRunBroker does |
|---|---|---|
| Boot | `await broker.connect()` | Logs `"DryRunBroker connected (account_group=demo)"`. Idempotent. |
| Signal arrival | (M5 → M6 queue → state machine) | — |
| Stage fire time | `await broker.place(signal, *, stage, amount)` | Generates trade_id, stores `(signal, stage)` in `_placed`, logs the intended trade, returns trade_id. |
| Stage awaiting result | `await broker.wait_result(trade_id, *, timeout)` | Pops `(signal, stage)` from `_placed`, calls `await outcome_provider(signal, stage)`, returns the result. Instant (D-7). |
| Result received | (state machine transitions, persists, DMs) | — |
| Shutdown (SIGINT/SIGTERM) | `await broker.close()` | Logs `"DryRunBroker closed"`. Idempotent. |

### 6.3 Trade-id lifecycle

```
broker.place(signal, stage="initial", amount=2)
  → trade_id = "dryrun-{signal_id}-initial-{uuid8}"
  → _placed[trade_id] = (signal, "initial")

broker.wait_result(trade_id, timeout=...)
  → signal, stage = _placed.pop(trade_id)  ← dict entry removed
  → return await outcome_provider(signal, stage)

If wait_result is never called (caller bug):
  → _placed entry leaks; dict grows by 1 per leaked place().
  → Mitigated by: M6 is the only caller; M6 tests assert pairing.
```

### 6.4 Concurrency

M3's `DryRunBroker` is **not** thread-safe — the `_placed` dict has no lock. This is fine because:
- The async event loop is single-threaded; no two coroutines mutate `_placed` simultaneously.
- M6 will own the broker instance and call `place`/`wait_result` sequentially per signal (state machine is the orchestrator).

If M6 ever spawns parallel signal cascades (it doesn't in v1 — single-channel), the dict needs an `asyncio.Lock`. Out of scope for M3.

### 6.5 Logging

M3 emits structured INFO log lines via stdlib `logging`:

| Event | Log format | Example |
|---|---|---|
| `connect()` | `"DryRunBroker connected (account_group=%s)"` | `DryRunBroker connected (account_group=demo)` |
| `place()` | `"DRY-RUN place: pair=%s direction=%s stage=%s amount=%s signal_id=%s trade_id=%s"` | `DRY-RUN place: pair=EUR/JPY direction=down stage=initial amount=2.00 signal_id=a1b2c3d4e5f6 trade_id=dryrun-a1b2c3d4e5f6-initial-1a2b3c4d` |
| `wait_result()` | `"DRY-RUN wait_result: trade_id=%s (instant)"` | `DRY-RUN wait_result: trade_id=dryrun-a1b2c3d4e5f6-initial-1a2b3c4d (instant)` |
| Unknown trade_id | `"DRY-RUN wait_result: unknown trade_id=%s; returning 'error'"` (WARNING) | — |
| `close()` | `"DryRunBroker closed"` | — |

M7's loguru setup will route stdlib `logging` through loguru's sinks. M3 doesn't depend on loguru being installed.

---

## 7. Test Plan

M3 targets **100% line + branch coverage** on `src/signal_copier/broker/base.py` and `src/signal_copier/broker/dry_run.py` (per the M0/M1/M2 spec convention).

### 7.1 `tests/test_broker_protocol.py` (~6 tests)

```python
from decimal import Decimal
import pytest

from signal_copier.broker import Broker, UnsupportedPairError
from signal_copier.broker.base import Broker as BrokerCanonical
from signal_copier.broker.dry_run import DryRunBroker


def test_dry_run_broker_satisfies_protocol() -> None:
    assert isinstance(DryRunBroker(), Broker)


def test_dry_run_broker_satisfies_canonical_protocol_path() -> None:
    # Both import paths resolve to the same Protocol object.
    assert Broker is BrokerCanonical


def test_place_signature_accepts_decimal_amount() -> None:
    broker = DryRunBroker()
    # mypy enforces Decimal; this test documents the rule at runtime.
    sig = inspect_signature(broker.place)
    assert sig.parameters["amount"].annotation is Decimal


def test_place_signature_keyword_only_stage_and_amount() -> None:
    sig = inspect_signature(broker.place)
    assert sig.parameters["stage"].kind == Parameter.KEYWORD_ONLY
    assert sig.parameters["amount"].kind == Parameter.KEYWORD_ONLY


def test_unsupported_pair_error_is_exception() -> None:
    assert issubclass(UnsupportedPairError, Exception)


def test_unsupported_pair_error_importable_from_top_level() -> None:
    # Re-export from signal_copier/__init__.py
    from signal_copier import UnsupportedPairError as TopLevel
    assert TopLevel is UnsupportedPairError
```

(`inspect_signature` from `inspect`; `Parameter.KEYWORD_ONLY` enforces D-9.)

### 7.2 `tests/test_dry_run_broker.py` (~12 tests)

```python
import logging
from decimal import Decimal

import pytest

from signal_copier.broker.dry_run import DryRunBroker
from signal_copier.domain.gale import Stage
from signal_copier.domain.signal import Signal


def _signal(signal_id: str = "abc123def456") -> Signal:
    return Signal(
        signal_id=signal_id,
        pair="EUR/JPY",
        direction="down",
        trigger_hhmm="10:20",
        expiration_seconds=300,
        received_at_unix=1_700_000_000.0,
        source_message_id=1,
        source_chat_id=1,
        raw_text="...",
        trigger_unix_initial=1_700_000_000.0,
        trigger_unix_gale1=1_700_000_300.0,
        trigger_unix_gale2=1_700_000_600.0,
    )


async def test_connect_logs_and_is_idempotent() -> None:
    broker = DryRunBroker()
    await broker.connect()
    await broker.connect()  # second call is a no-op


async def test_close_is_idempotent() -> None:
    broker = DryRunBroker()
    await broker.close()
    await broker.close()  # second call is a no-op


async def test_place_returns_string_trade_id() -> None:
    broker = DryRunBroker()
    sig = _signal()
    trade_id = await broker.place(sig, stage="initial", amount=Decimal("2.00"))
    assert isinstance(trade_id, str)


async def test_place_trade_id_has_dryrun_prefix_and_signal_id() -> None:
    broker = DryRunBroker()
    sig = _signal(signal_id="a1b2c3d4e5f6")
    trade_id = await broker.place(sig, stage="initial", amount=Decimal("2.00"))
    assert trade_id.startswith("dryrun-a1b2c3d4e5f6-initial-")


async def test_place_logs_intended_trade(caplog) -> None:
    broker = DryRunBroker()
    sig = _signal()
    with caplog.at_level(logging.INFO):
        await broker.place(sig, stage="initial", amount=Decimal("2.00"))
    assert any("DRY-RUN place" in r.message and "EUR/JPY" in r.message
               for r in caplog.records)


async def test_wait_result_default_returns_win() -> None:
    broker = DryRunBroker()
    sig = _signal()
    for stage in ("initial", "gale1", "gale2"):
        tid = await broker.place(sig, stage=stage, amount=Decimal("2.00"))
        result = await broker.wait_result(tid, timeout=330.0)
        assert result == "win"


async def test_wait_result_uses_custom_provider() -> None:
    async def loss_all(s: Signal, st: Stage) -> str:
        return "loss"
    broker = DryRunBroker(outcome_provider=loss_all)
    sig = _signal()
    tid = await broker.place(sig, stage="initial", amount=Decimal("2.00"))
    result = await broker.wait_result(tid, timeout=330.0)
    assert result == "loss"


async def test_wait_result_provider_receives_signal_and_stage() -> None:
    captured: list[tuple[Signal, Stage]] = []
    async def capture(s: Signal, st: Stage) -> str:
        captured.append((s, st))
        return "win"
    broker = DryRunBroker(outcome_provider=capture)
    sig = _signal()
    tid = await broker.place(sig, stage="gale1", amount=Decimal("4.00"))
    await broker.wait_result(tid, timeout=330.0)
    assert len(captured) == 1
    assert captured[0][0] is sig
    assert captured[0][1] == "gale1"


async def test_place_then_wait_pops_trade_id_dict() -> None:
    broker = DryRunBroker()
    sig = _signal()
    tid = await broker.place(sig, stage="initial", amount=Decimal("2.00"))
    assert tid in broker._placed
    await broker.wait_result(tid, timeout=330.0)
    assert tid not in broker._placed


async def test_multiple_in_flight_places_do_not_collide() -> None:
    broker = DryRunBroker()
    sig = _signal()
    tid1 = await broker.place(sig, stage="initial", amount=Decimal("2.00"))
    tid2 = await broker.place(sig, stage="gale1", amount=Decimal("4.00"))
    assert tid1 != tid2
    assert (await broker.wait_result(tid1, timeout=330.0)) == "win"
    assert (await broker.wait_result(tid2, timeout=330.0)) == "win"


async def test_wait_result_unknown_trade_id_returns_error(caplog) -> None:
    broker = DryRunBroker()
    with caplog.at_level(logging.WARNING):
        result = await broker.wait_result("unknown-id", timeout=330.0)
    assert result == "error"
    assert any("unknown trade_id" in r.message for r in caplog.records)


async def test_account_group_logged_on_connect(caplog) -> None:
    broker = DryRunBroker(account_group="demo")
    with caplog.at_level(logging.INFO):
        await broker.connect()
    assert any("account_group=demo" in r.message for r in caplog.records)
```

**Coverage:** all branches in `broker/base.py` (Protocol methods are abstract, so coverage = 100% by definition) and `broker/dry_run.py` (every `if`/`return` branch exercised by the above tests).

---

## 8. Risks & Non-Goals

| # | Risk | Mitigation |
|---|---|---|
| 1 | **`DryRunBroker.wait_result()` is instant; M6 may need a real delay for M9 soak** | M6 owns scheduling. M9 (24h soak) can wrap `broker.wait_result(...)` in `asyncio.sleep(expiration_seconds + 30)` if realistic timing matters for cascade-pacing observations. M3 doesn't bake this in. D-7. |
| 2 | **`trade_id` dict could grow if `wait_result` is never called** | Acceptable for v1: `place()` and `wait_result()` are M6's contract — every `place` is paired. `test_place_then_wait_pops_trade_id_dict` enforces this in M3's tests; M6 tests will fail if pairing breaks in production code. If a future broker wants long-lived trade-ids, it stores them server-side (M8). |
| 3 | **`outcome_provider` signature is `Callable[[Signal, Stage], Awaitable[StageResult]]`** — even the sync default returns an `Awaitable` via `async def` | Slight ergonomic cost (default provider is `async def` returning a literal) for the win of M8's real broker being a drop-in (it may genuinely need async work in the provider). D-3. |
| 4 | **`Broker.place()` signature differs from PRD §4.4** (`amount: float` → `Decimal`; positional `amount` → keyword `amount`; added `stage: Stage` keyword) | PRD §4.4 was a sketch; D-2 + M2's `Decimal` use makes this a clean call. M8's broker will inherit the same signature. Worth flagging in the spec commit message as "PRD §4.4 refined for M2-money-type alignment." |
| 5 | **`UnsupportedPairError` is defined but never raised in M3** | OK — it's part of the Protocol contract. M8 raises it. `test_unsupported_pair_error_is_exception` asserts it exists and inherits from `Exception`; that's enough until M8. |
| 6 | **Logging goes to stdout via stdlib `logging` default config** | M2's `__main__.py` already does `print(...)` for startup. M3's broker logs are informational at INFO. M7's loguru setup will route stdlib logging through loguru's sinks. No config code in M3. |
| 7 | **`_placed` dict is not concurrency-safe** | Single-threaded async loop is safe by construction. M6 will not run parallel signal cascades in v1 (one channel). If v2 adds parallel cascades, add `asyncio.Lock`. Out of scope for M3. |

---

## 9. Out of Scope (explicit non-goals for M3)

- **Real broker connection** (M8 — `broker/olymp.py`)
- **Asset-map auto-discover** (M8 — R-11)
- **Push event bridging** (`E_TRADE_CLOSED = 26` → `wait_result`) (M8)
- **Self-healing reconnect** (M10)
- **Daily-limit enforcement using broker PnL** (M6 + M4)
- **Logging config** (M7 — loguru replaces M2's `infra/log.py` stub)
- **DB persistence of `trade_id` from broker** (M6 uses M4's `StateStore`)
- **Wiring `Broker` into M6's scheduler** (M6)
- **End-to-end soak test** (M9)

---

## 10. Definition of Done for M3

- [ ] All 6 files created/modified per §3
- [ ] `pytest tests/test_broker_protocol.py tests/test_dry_run_broker.py` passes
- [ ] 100% line + branch coverage on `src/signal_copier/broker/base.py` and `src/signal_copier/broker/dry_run.py`
- [ ] `mypy --strict src/signal_copier/broker/` passes (D-2 enforced)
- [ ] `ruff check src/signal_copier/broker/ tests/test_broker_protocol.py tests/test_dry_run_broker.py` passes
- [ ] `ruff format --check` on the same files passes
- [ ] `python -c "from signal_copier.broker import Broker, UnsupportedPairError; from signal_copier import Broker as B2; assert Broker is B2"` succeeds (top-level re-export works)

---

## 11. References

- PRD §4.4 — Trade Executor (Broker interface sketch; refined in this spec)
- PRD §4.5 — Result Monitor & Gale State Machine (where `StageResult` originates)
- PRD §4.6 FR-6.5 — Dry-run mode default
- PRD §6 — Tech Stack (Python 3.13+ async, no extra deps)
- PRD §7 — Architecture (`broker/` package layout)
- PRD §9.1 — `error_reason='unsupported_pair'` enum value
- PRD §10 — Error handling (`UnsupportedPairError` → state machine → `error`)
- PRD §15 — Build plan, M3 row
- M2 spec `docs/superpowers/specs/2026-06-19-m2-state-machine-design.md` — `StageResult`, `Signal`, `Decimal` money conventions
- Python docs: [`typing.Protocol` and `runtime_checkable`](https://docs.python.org/3/library/typing.html#typing.Protocol)
