# Design Spec — M10: Self-Healing Reconnect Supervisor for OlympTrade WS

**Date:** 2026-06-23
**Status:** Approved (sections 1–5) — pending user review of this written spec
**Milestone:** M10 (per PRD §15)
**Author:** opencode brainstorming session
**Related PRD sections:** §10 Error Handling, §13.1 R-12 (Railway), §14 S-5 (self-healing reconnect), §14 S-11 (circuit breaker), §15 Build Plan M10, §17 Hosting

---

## 1. Problem & Motivation

The vendored `olymptrade_ws` WebSocket client (PRD R-15, §12.6) does **not** auto-reconnect when the connection drops. In the current (pre-M10) behavior:

- A single network blip or broker restart during a trading session terminates the WS.
- All pending `_pending` Futures in `OlympTradeBroker` (broker/olymp.py:117) are abandoned.
- The next `place()` or `wait_result()` call raises `ConnectionError`.
- M8's `wait_result()` (broker/olymp.py:436) detects the disconnect and DM-notifies `on_olymp_disconnect()` with copy "Process will exit; supervisor will restart."
- M6's `SignalSupervisor._wait_for_stage_result` (scheduler/trigger.py:662) catches the ConnectionError and maps it to `'error'`, ending the cascade with `error_reason='broker_unavailable'`.
- `__main__` then exits non-zero (broker/base.py:31 + __main__.py:209), and **Railway restarts the entire container** — losing Telethon session, asset map, in-flight cascades, and ~10–30s of startup time.

PRD §15 M10's verifiable outcome: **"Kill network mid-trade; tool reconnects within 30s."** PRD §14 S-5 specifies: *"Self-healing reconnect for OlympTrade (v1.0). The vendored olymptrade_ws library does NOT auto-reconnect on WS drop. Wrap it in a supervisor coroutine that detects drops and reconnects with backoff. Otherwise one network blip kills the tool until restart."*

PRD §14 S-11 (circuit breaker) is folded into this milestone: *"If OlympTrade rejects 3 tokens in a row, halt and DM the user rather than hammering the server."*

---

## 2. Goals & Non-Goals

### 2.1 Goals

1. **Survive transient WS drops** without process restart: a 5-second network blip during an active session shall not require a Railway container restart.
2. **Match M10's verifiable outcome**: reconnect within 30s of the disconnect.
3. **Preserve all non-broker state across reconnect**: Telethon session, DB pool, scheduler, notifier, current Telegram listener task.
4. **Bound blast radius**: 5 consecutive failed reconnect attempts → terminal halt (Railway restart as backstop, matching existing behavior).
5. **Surface the reconnect lifecycle to the user** via Telegram self-DM messages.
6. **Idempotent & race-safe**: concurrent disconnect detection (watcher + an in-flight `place()`) triggers exactly one reconnect loop.

### 2.2 Non-Goals (explicitly out of scope for M10)

- **Cascade preservation across reconnect.** When a WS drop ends a cascade mid-stage, the cascade terminates with `error(broker_unavailable)` per current M6/M2 behavior. Re-arming the e:26 callback to capture the eventual broker result is a v2 enhancement.
- **Pre-emptive ping-based disconnect detection** (PRD NFR-1 already covers trigger precision; ping-based detection is a v2 enhancement).
- **Multiple parallel OlympTrade accounts** (PRD S-10, v2).
- **Token-refresh helper script** (PRD S-6, separate task in v1.0).
- **Modifying the vendored `olymptrade_ws` package** (PRD R-15 / §12.6: no edits to vendored code).

---

## 3. Architecture

### 3.1 Wrapper class

A new class `ReconnectingOlympTradeBroker` in `src/signal_copier/broker/reconnect.py` wraps the existing M8 `OlympTradeBroker`:

```
ReconnectingOlympTradeBroker  (implements Broker Protocol)
├─ _inner: OlympTradeBroker          # current live instance; replaced on each successful reconnect
├─ _watcher: asyncio.Task            # polls is_connected every 1s
├─ _reconnect_lock: asyncio.Lock     # ensures only one reconnect loop runs at a time
├─ _consecutive_failures: int        # 0..5; resets on successful reconnect
├─ _state: enum {DISCONNECTED, CONNECTED, RECONNECTING}
└─ public surface (delegates to _inner):
   ├─ connect()      → starts watcher, calls inner.connect()
   ├─ place(...)     → delegates; on ConnectionError, triggers reconnect + re-raises
   ├─ wait_result()  → delegates; on ConnectionError, triggers reconnect + re-raises
   └─ close()        → cancels watcher, closes inner (idempotent)
```

### 3.2 Lifecycle

```
DISCONNECTED
  └─ connect() succeeds                → CONNECTED
CONNECTED
  └─ watcher sees is_connected=False   → RECONNECTING
  └─ place/wait_result raises ConnErr  → RECONNECTING
RECONNECTING
  └─ next connect() succeeds           → CONNECTED (counter reset to 0)
  └─ next connect() fails; attempts<5  → RECONNECTING
  └─ 5 consecutive failures            → DISCONNECTED (terminal; raise BrokerAuthError)
```

### 3.3 Reconnect loop (when triggered)

```
1. await notifier.on_olymp_disconnect()            # existing method, fired immediately
2. attempt = 1
3. while attempt <= 5:
     a. await old_inner.close()                    # cancels old pending Futures
     b. delay = compute_backoff_seconds(attempt - 1)   # 1s, 2s, 4s, 8s, 16s, 30s cap
     c. await notifier.on_olymp_reconnecting(attempt, 5, downtime_so_far, delay)
     d. await asyncio.sleep(delay)
     e. new_inner = OlympTradeBroker(...)          # same factory, same args
     f. try:
          await new_inner.connect()                # re-registers e:21/e:22/e:26 callbacks,
                                                   # re-fetches asset map (e:1068),
                                                   # re-caches start-of-day balance (e:55)
        except Exception as exc:
          _consecutive_failures += 1
          attempt += 1
          continue
     g. self._inner = new_inner                    # atomic swap
     h. _consecutive_failures = 0
     i. await notifier.on_olymp_reconnected(attempt, total_downtime)
     j. return
4. # exhausted
   await notifier.on_olymp_reconnect_failed(5, total_downtime)
   raise BrokerAuthError("OlympTrade reconnect exhausted after 5 attempts")
```

### 3.4 Detection (hybrid: event-driven + 1s poll)

- **Watcher task** (1s polling): `_watcher_loop()` reads `self._inner._client.connection.is_connected` every `watcher_poll_seconds`. On a True→False transition (or if state was already non-CONNECTED when the poll starts), calls `_trigger_reconnect()`. Cancelled by `close()`.
- **Event-driven fast path**: `place()` and `wait_result()` wrap their inner-broker calls in `try/except ConnectionError`. On exception, they `await self._trigger_reconnect()` (the await is important: the caller of `place()` will block until reconnect completes or fails, so the NEXT signal arriving seconds later sees a live broker). After reconnect attempt completes, the original `ConnectionError` is re-raised to the caller. The caller's `except Exception` in `_wait_for_stage_result` (scheduler/trigger.py:662) maps it to `'error'`, ending the cascade per current behavior.

Both paths funnel into the same `_trigger_reconnect()` helper, which is guarded by `_reconnect_lock` and a state-check (`DISCONNECTED`/`RECONNECTING` → no-op).

### 3.5 Inner broker rebuild on each reconnect

`OlympTradeBroker.__init__` is sync; `connect()` is async and does the full startup sequence: open WS, register 3 push callbacks, run `initialize_session()` (which subscribes and gets asset list), build asset map, cache balance. **No changes to `OlympTradeBroker` are required** — M10 reuses this entire flow on every reconnect attempt.

The vendored `OlympTradeClient` instances are short-lived: one per reconnect. The wrapper's `_client_factory` (the test seam from M8, broker/olymp.py:106) is preserved through the rebuild.

### 3.6 Failure counter

- `_consecutive_failures: int = 0` initialized in `__init__`.
- Incremented at step 3(f) on each failed `new_inner.connect()`.
- Reset to 0 at step 3(h) on successful reconnect.
- At 5: cancel watcher, fire `on_olymp_reconnect_failed()`, raise `BrokerAuthError` (the existing exception that __main__ catches and exits 2). This is the same exit path as a bad token at initial startup; Railway restart is the consistent backstop for "we cannot trade right now."

---

## 4. Data Flow & Concurrency

### 4.1 State store & DB

No schema changes. The `signals` table's `error_reason='broker_unavailable'` value already exists as a valid value (PRD §9.1). The wrapper does NOT directly write to the DB; it raises `ConnectionError`, and M6's `_wait_for_stage_result` (scheduler/trigger.py:662) maps it to `'error'` which M2's state machine then persists.

### 4.2 Concurrency invariants

- **One reconnect loop at a time**: `_reconnect_lock: asyncio.Lock` wraps `_trigger_reconnect()`. Inside the lock, check `self._state` and return early if already `RECONNECTING`.
- **Watcher cancellation**: `close()` cancels `_watcher` and awaits its termination before returning.
- **Watcher re-creation**: the watcher is created ONCE in `connect()`. If the watcher task crashes (uncaught exception), it is NOT auto-respawned — the supervisor falls back to event-driven detection only. Documented as a known limitation; v2 enhancement is "self-healing watcher."
- **Inner swap atomicity**: `self._inner = new_inner` is a single Python attribute assignment, atomic under asyncio (no preemption between statements in a coroutine). All callers (watcher, place, wait_result) read `self._inner` at call time, so they always see the current inner.

### 4.3 Concurrency-safe place/wait_result

```python
async def place(self, signal, *, stage, amount) -> str:
    try:
        return await self._inner.place(signal, stage=stage, amount=amount)
    except ConnectionError:
        await self._trigger_reconnect()
        raise
```

`_trigger_reconnect()` semantics: if a reconnect loop is already running, the caller blocks on `_reconnect_lock` until that loop finishes (success or exhaustion), then returns. If no reconnect is running, it acquires the lock and runs the loop itself. Either way, after the await, `self._inner` is either the new live broker (success) or the state is `DISCONNECTED` (exhaustion). The outer `raise` always fires, preserving M6's existing error-handling path.

---

## 5. File Layout & Code Organization

```
src/signal_copier/broker/
├── base.py              # UNCHANGED (Broker Protocol, BrokerAuthError, UnsupportedPairError)
├── dry_run.py           # UNCHANGED
├── olymp.py             # UNCHANGED (M8's OlympTradeBroker, no edits per PRD R-15)
└── reconnect.py         # NEW: ReconnectingOlympTradeBroker

src/signal_copier/notify/
├── protocol.py          # MODIFIED: add on_olymp_reconnecting, on_olymp_reconnected,
│                        #           on_olymp_reconnect_failed to Notifier Protocol + NoOpNotifier
└── telegram_dm.py       # MODIFIED: implement the 3 new methods with the copy in §6.3

src/signal_copier/
├── __main__.py          # MODIFIED: wrap OlympTradeBroker construction in ReconnectingOlympTradeBroker
└── ...                  # all other modules UNCHANGED

tests/
├── _broker_fixtures.py  # MODIFIED: add FakeClientFactory (one-at-a-time fake client provider)
└── test_reconnect_supervisor.py  # NEW: ≥90% line coverage on broker/reconnect.py
```

---

## 6. API & Contract

### 6.1 Constructor signature

```python
class ReconnectingOlympTradeBroker:
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
    ) -> None: ...
```

`_client_factory` is the same test seam M8 exposes (broker/olymp.py:106). `reconnect_max_attempts` and `watcher_poll_seconds` are production-overridable knobs (defaults match the PRD's verifiable outcome window of 30s; tests override to small values for speed).

### 6.2 Broker Protocol conformance

The wrapper exposes `connect / place / wait_result / close` with identical signatures to `Broker` (broker/base.py:40). `runtime_checkable` isinstance check (broker/base.py:39) passes.

### 6.3 New Notifier methods (Protocol additions)

```python
class Notifier(Protocol):
    # ... existing methods unchanged ...

    async def on_olymp_reconnecting(
        self, *, attempt: int, max_attempts: int,
        downtime_seconds: float, next_delay_seconds: float,
    ) -> None: ...

    async def on_olymp_reconnected(
        self, *, attempts_used: int, total_downtime_seconds: float
    ) -> None: ...

    async def on_olymp_reconnect_failed(
        self, *, attempts: int, total_downtime_seconds: float
    ) -> None: ...
```

`NoOpNotifier` (used in `DRY_RUN=true` paths and in tests) implements all three by logging at WARNING level — matching the existing pattern for `on_olymp_disconnect()` (notify/protocol.py:269).

`TelegramDMNotifier` implements them with the copy in §6.4.

### 6.4 Telegram DM copy

| Event | Message |
|---|---|
| `on_olymp_disconnect()` | `🔌 OlympTrade disconnected. Reconnecting…` |
| `on_olymp_reconnecting(attempt, max_attempts, downtime)` | `🔁 OlympTrade reconnecting (attempt N/5)\nDowntime: Xs\nNext retry in Ys` |
| `on_olymp_reconnected(attempts_used, total_downtime)` | `✅ OlympTrade reconnected\nAttempts: N\nTotal downtime: Xs\nAction: resumed normal operation. In-flight cascades (if any) were ended with broker_unavailable.` |
| `on_olymp_reconnect_failed(attempts, total_downtime)` | `❌ OlympTrade reconnect failed after 5 attempts\nTotal downtime: Xs\nAction: process will exit; Railway supervisor will restart.` |

The existing `on_olymp_disconnect()` copy is softened from "Process will exit; supervisor will restart" to "Reconnecting…" — the Railway restart only happens as a backstop if reconnect permanently fails.

### 6.5 __main__.py change (one construction site)

```python
# before (M8):
broker = OlympTradeBroker(
    access_token=config.olymp_access_token,
    account_id=config.olymp_account_id,
    account_group=config.olymp_account_group,
    notifier=notifier,
)
_log.info("Broker: OlympTradeBroker (live %s, account_id=%s)", ...)
await broker.connect()

# after (M10):
from signal_copier.broker.reconnect import ReconnectingOlympTradeBroker

broker = ReconnectingOlympTradeBroker(
    access_token=config.olymp_access_token,
    account_id=config.olymp_account_id,
    account_group=config.olymp_account_group,
    notifier=notifier,
)
_log.info("Broker: ReconnectingOlympTradeBroker (live %s, account_id=%s)", ...)
await broker.connect()
```

The `except BrokerAuthError` handler at __main__.py:209 stays as-is — it now catches both "initial connect failure (bad token)" and "5-attempt reconnect exhaustion (broker unavailable)" with the same exit-2 behavior.

`DRY_RUN=true` path (lines 73–75) is unchanged — `DryRunBroker` doesn't need a reconnect supervisor (no I/O).

---

## 7. Notifications (Notifier Protocol additions)

| Notifier method | Fires when | Default NoOp behavior | TelegramDMNotifier copy |
|---|---|---|---|
| `on_olymp_reconnecting(attempt, max, downtime)` | Step 3(b) of reconnect loop, before sleep | WARNING log | See §6.4 |
| `on_olymp_reconnected(attempts, downtime)` | Step 3(i), on successful reconnect | WARNING log | See §6.4 |
| `on_olymp_reconnect_failed(attempts, downtime)` | Step 4, after 5 consecutive failures | WARNING log | See §6.4 |

`on_olymp_disconnect()` (existing) fires at step 1 of the reconnect loop — same trigger point as the current M8 behavior (broker/olymp.py:437). Its copy changes from "Process will exit; supervisor will restart" to "Reconnecting…" (see §6.4 row 1).

---

## 8. Error Handling

| Failure | Behavior |
|---|---|
| Token truly revoked (401 on every `connect()`) | Counts as a reconnect failure; 5 attempts → halt via `BrokerAuthError`. Same exit-2 path as initial-connection failure. |
| Watcher task crashes (uncaught exception in `_watcher_loop`) | Logged at ERROR; watcher is NOT auto-respawned. Supervisor falls back to event-driven detection. Known limitation; v2 = self-healing watcher. |
| `close()` called mid-reconnect | Cancels watcher task, cancels the reconnect coroutine via the held `_reconnect_lock`'s cancellation propagation, awaits both. Idempotent. |
| Reconnect succeeds, then disconnects again | New disconnect triggers a fresh reconnect cycle; `_consecutive_failures` is reset to 0 after the success. |
| Notifier raises during `on_olymp_reconnecting` | Caught by the supervisor's `try/except` around the notifier call, logged at WARNING, does NOT block the reconnect loop. Same defensive pattern as M6's `_safe_notify` (scheduler/trigger.py:755). |
| Watcher reads `is_connected` between inner swap | The watcher reads `self._inner._client.connection.is_connected` (the current inner), so it always sees the live state, not a cached old one. |
| SIGINT during reconnect | Watcher and reconnect coroutine both cancelled by the existing `finally` block in `_run()` (__main__.py:174). `broker.close()` runs and awaits both. Clean shutdown. |
| Postgres connection lost | Not M10's concern. Existing `asyncpg` pool auto-reconnects on next acquire. State persistence is transactional. |
| Process killed mid-reconnect | Railway restart; M9's `recovery.py` rehydrates `placed_*` cascades as before. |

---

## 9. Testing Strategy

### 9.1 New test file

`tests/test_reconnect_supervisor.py` covers the wrapper in isolation. Uses `FakeOlympTradeClient` from `tests/_broker_fixtures.py`.

### 9.2 Fixture extension: `FakeClientFactory`

`tests/_broker_fixtures.py` gets a new class:

```python
class FakeClientFactory:
    """Returns FakeOlympTradeClient instances one-at-a-time from a list.

    After the list is exhausted, returns the last fake (so a test can
    declare "first reconnect uses a different fake, subsequent reuses
    the recovered one"). Tests that want every reconnect to use a fresh
    fake pass a list long enough to cover the attempt count.
    """

    def __init__(self, fakes: list[FakeOlympTradeClient]) -> None: ...
    def __call__(self) -> FakeOlympTradeClient: ...
```

### 9.3 Test cases (11 tests, target ≥90% line coverage)

| # | Name | Setup | Assertion |
|---|---|---|---|
| 1 | `test_initial_connect_succeeds` | factory with one connected fake | `connect()` calls inner.connect(); `_inner` is set; `_state == CONNECTED` |
| 2 | `test_watcher_detects_disconnect_and_reconnects` | factory[0] disconnectable, factory[1] connected; flip factory[0].connection._connected = False | within ~1.5s, watcher fires; factory[1] used; `notifier.on_olymp_reconnected` called with `attempts_used=1` |
| 3 | `test_event_driven_reconnect_via_place` | inner fake starts disconnected | `place()` raises ConnectionError; `notifier.on_olymp_reconnected` called before the re-raise |
| 4 | `test_event_driven_reconnect_via_wait_result` | inner fake starts disconnected | `wait_result()` raises ConnectionError; same as test 3 |
| 5 | `test_concurrent_detection_only_one_reconnect_loop` | both watcher AND a simultaneous `place()` see disconnect | exactly ONE `on_olymp_reconnecting → on_olymp_reconnected` sequence in the notifier call log (asyncio.Lock enforces) |
| 6 | `test_reconnect_exhausts_after_max_attempts` | factory with N fakes that all fail `connect()` | `on_olymp_reconnect_failed` fired after exactly N attempts; `BrokerAuthError` raised |
| 7 | `test_reconnect_resets_failure_counter_on_success` | factory[0] fails, factory[1] succeeds, factory[2] disconnects, factory[3..7] fail | counter resets after first success; second disconnect cycle runs the full 5 attempts |
| 8 | `test_exponential_backoff_used` | capture `asyncio.sleep` calls in reconnect loop | assert `sleep(1.0), sleep(2.0), sleep(4.0), sleep(8.0), sleep(16.0)` for the 5 attempts |
| 9 | `test_close_cancels_watcher` | connect, then close | watcher task is `done()` and `cancelled()` |
| 10 | `test_close_is_idempotent` | connect, close, close | no exceptions |
| 11 | `test_place_after_reconnect_uses_new_inner` | factory[0] placeable, disconnect, factory[1] placeable | `place()` after reconnect records its call on factory[1].trade.place_order_calls, not factory[0]'s |

All tests use `watcher_poll_seconds=0.05` and `reconnect_max_attempts=3` where useful to keep runtime <2s.

### 9.4 No changes to existing tests

- `tests/test_olymp_broker.py` — M8 broker is unchanged; no edits.
- `tests/test_main.py` — `__main__` construction-site change is covered by the existing integration test (the wrapper satisfies `Broker` Protocol).
- `tests/test_soak_assertions.py` — M9 soak test runs unchanged; the wrapper is transparent at the Broker layer.

### 9.5 Coverage target

≥90% line coverage on `src/signal_copier/broker/reconnect.py` (matching the project's existing 100% norm on parser/state-machine code).

---

## 10. Backward Compatibility

| Component | Change | Breaking? |
|---|---|---|
| `Broker` Protocol (broker/base.py) | None | No |
| `BrokerAuthError` (broker/base.py:20) | None — wrapper reuses it for terminal failure | No |
| `UnsupportedPairError` (broker/base.py:11) | None | No |
| `OlympTradeBroker` (broker/olymp.py) | None — M8 is untouched | No |
| `DryRunBroker` (broker/dry_run.py) | None | No |
| M6 `Scheduler` / `SignalSupervisor` (scheduler/trigger.py) | None — `place/wait_result` raise the same `ConnectionError` | No |
| M2 state machine (domain/state.py) | None — error_reason='broker_unavailable' already valid | No |
| M9 recovery (recovery.py) | None — recovers cascades across process restarts, orthogonal to mid-run reconnect | No |
| `Notifier` Protocol (notify/protocol.py) | **3 new methods added** | Additive — existing implementations that satisfy `Protocol` will fail `isinstance` check unless they're updated. Mitigation: `NoOpNotifier` updated in the same PR; `RecordingNotifier` (test fixture) updated. `TelegramDMNotifier` updated. |
| `__main__.py` | 1-line construction-site change + 1 import | No (functional behavior is identical at startup; reconnect adds new behavior) |
| `migrations/` | None | No |
| `pyproject.toml` | None (no new dependencies) | No |

---

## 11. Out of Scope / Deferred

Items explicitly **not** included in M10 (per §2.2 and per PRD §14):

- Cascade preservation across reconnect (re-arming e:26 for in-flight trade_ids after reconnect)
- Pre-emptive ping-based disconnect detection
- Multiple parallel OlympTrade accounts (S-10, v2)
- Token-refresh helper script (S-6, separate v1.0 task)
- Self-healing watcher task (v2 — if the watcher's `_watcher_loop` raises, supervisor falls back to event-driven only)
- Modifying vendored `olymptrade_ws` (PRD R-15 — no edits)

---

## 12. Acceptance Criteria

M10 is **done** when **all** of the following are true:

1. ✅ `ReconnectingOlympTradeBroker` exists at `src/signal_copier/broker/reconnect.py`.
2. ✅ `Notifier` Protocol has 3 new methods (`on_olymp_reconnecting`, `on_olymp_reconnected`, `on_olymp_reconnect_failed`); `NoOpNotifier` and `TelegramDMNotifier` implement them.
3. ✅ `__main__.py` constructs `ReconnectingOlympTradeBroker` instead of `OlympTradeBroker` when `DRY_RUN=false`.
4. ✅ All 11 tests in `tests/test_reconnect_supervisor.py` pass.
5. ✅ Line coverage on `broker/reconnect.py` ≥ 90%.
6. ✅ `mypy --strict` passes on the new file.
7. ✅ `ruff check` and `ruff format --check` pass on the new file.
8. ✅ Existing test suite (108+ tests across the project) still passes — zero regressions.
9. ✅ Manual smoke test (M9-style soak, optional): run `DRY_RUN=true` with a fixture that simulates a disconnect mid-trade; verify the supervisor reconnects within 30s and the cascade ends with `broker_unavailable` (this is what test 3 already covers synthetically).
10. ✅ PRD §15 M10's verifiable outcome met: "Kill network mid-trade; tool reconnects within 30s." (Verified by tests 2, 3, 4, 11.)

---

## 13. Open Questions

**None.** All decisions resolved through the brainstorming session that produced this spec.

The spec reflects these decisions:

1. Cascade scope: tool reconnects, in-flight cascade ends with `error(broker_unavailable)`.
2. Detection: hybrid (event-driven + 1s poll).
3. Circuit breaker: 5 consecutive reconnect failures → halt + DM + non-zero exit (folded from PRD S-11).
4. Architecture: wrapper class `ReconnectingOlympTradeBroker` around `OlympTradeBroker`.
5. Notifications: 3 new notifier methods (`on_olymp_reconnecting`, `on_olymp_reconnected`, `on_olymp_reconnect_failed`); existing `on_olymp_disconnect` copy softened to "Reconnecting…".

---

## 14. References

- PRD v0.7, §10 Error Handling table (OlympTrade WS disconnect row)
- PRD v0.7, §12.6 Vendored third-party code (no edits to olymptrade_ws)
- PRD v0.7, §13.1 R-15 (vendoring decision)
- PRD v0.7, §14 S-5 (self-healing reconnect, the basis for this spec)
- PRD v0.7, §14 S-11 (circuit breaker, folded into this spec)
- PRD v0.7, §15 M10 (verifiable outcome)
- PRD v0.7, §17 Hosting (Railway restart policy as backstop)
- Code: `src/signal_copier/broker/olymp.py` (M8, untouched)
- Code: `src/signal_copier/broker/base.py` (Broker Protocol, BrokerAuthError)
- Code: `src/signal_copier/scheduler/trigger.py:662` (M6 error→'error' mapping)
- Code: `src/signal_copier/telegram/client.py:30` (existing exponential-backoff helper pattern to reuse)
- Code: `tests/_broker_fixtures.py` (FakeOlympTradeClient, FakeTradeAPI, FakeConnection — extended with FakeClientFactory)

---

*End of spec.*
