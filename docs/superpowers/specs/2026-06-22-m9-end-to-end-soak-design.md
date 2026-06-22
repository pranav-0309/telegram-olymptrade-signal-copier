# M9 Design Spec — End-to-End Soak + Restart Recovery

**Date:** 2026-06-22
**Status:** Draft — awaiting user review
**Milestone:** M9
**PRD reference:** `docs/PRD.md` §3 (User Flow), §4.1 (FR-1.x Telegram), §4.3 (FR-3.x Scheduler), §4.5 (FR-5.x Result Monitor), §10 (Error Handling — "Process killed mid-cascade"), §15 (Build Plan M9), §17 (Hosting — restart policy), S-5 / S-11 / S-13 (Suggestions deferred to M10+)

---

## 1. Purpose

Ship the v1 validation phase: a 24-hour end-to-end soak that proves the full Telegram → OlympTrade Signal Copier pipeline runs unattended for a full day without dropping a signal, duplicating a trade, or losing a Telegram DM. M9 also ships the **restart-recovery logic** that the M8 spec explicitly delegated to it ("M9 reconciliation logic resumes from DB" — `docs/superpowers/specs/2026-06-21-m8-olymptrade-broker-design.md:723`).

**Verifiable outcome (PRD §15):** "Full pipeline tested with `DRY_RUN=true` for 24h."

M9 is the **last** milestone in the v1 build plan before deployment (M11). It does not introduce new user-facing behavior; it proves the existing behavior survives real-world conditions (time, restarts, sustained load).

## 2. Scope

In scope for M9:

1. **`signal_copier/recovery.py`** — boot-time coroutine that rehydrates in-progress cascades from the DB.
2. **`Scheduler.adopt(signal_row)`** — new public method on the scheduler that constructs a fresh `SignalSupervisor` for a persisted mid-cascade signal. Reuses M6's supervisor; no new supervisor logic.
3. **`signal_copier/replay.py`** — opt-in fixture-driven signal injector, gated by the `SOAK_REPLAY=<path>` env var. Activated only during the soak; production builds never import it.
4. **`tools/soak.py`** — the 24-hour soak harness as a separate entrypoint (`python -m tools.soak`).
5. **`tools/soak_assertions.py`** — pure pass/fail assertion functions called at soak end.
6. **`tests/fixtures/soak_recordings/soak_24h.json`** — ~20 recorded signals spread across 24h, covering the main cascade paths plus parse failures.
7. **Restart drill** — the soak forces a SIGTERM at hour 12 and verifies the cascade resumes from DB within 60 seconds.
8. **Telethon liveness probe** — a separate Telethon client owned by the soak harness, pinging `get_me()` every 30 minutes to confirm the MTProto socket is alive.

Out of scope (deferred to later milestones):

- **Self-healing reconnect supervisor** for OlympTrade WS drops without process restart (PRD S-5 → M10). M9 only handles the `process killed → Railway restart` case. Mid-trade WS drops that take the process down still get rehydrated by recovery — but in-process reconnect (no restart) is M10.
- **Real-money trading** (PRD §2.2, FR-6.6 → v2). M9 soaks strictly in `DRY_RUN=true` (which selects `DryRunBroker` per `__main__.py` wiring).
- **Token-refresh helper** (PRD S-6), **circuit breaker** (PRD S-11), **pre-flight broker validation** (PRD S-13). All M10+.
- **Multi-channel support**, **web dashboard**, **backtesting** (PRD non-goals).
- **Real-broker soak.** M9 soaks with `DryRunBroker`. Real-broker (OlympTradeBroker) soak in `demo` account is a separate future milestone gated behind M9 passing.

## 3. Architecture & file layout

### 3.1 New and modified files

```
src/signal_copier/
├── recovery.py               # NEW — boot-time rehydration coroutine
├── replay.py                 # NEW — opt-in fixture injector (gated by SOAK_REPLAY)
├── scheduler/
│   └── trigger.py            # EDIT — add public Scheduler.adopt(signal_row) method
└── __main__.py               # EDIT — call recovery.recover_active_signals() at boot, before normal listener start
│                             # EDIT — if SOAK_REPLAY set, spawn replay_runner() coroutine
tools/
├── __init__.py               # NEW — empty, makes tools/ a package
├── soak.py                   # NEW — 24h soak harness entrypoint
└── soak_assertions.py        # NEW — pure pass/fail assertion functions

tests/fixtures/soak_recordings/
└── soak_24h.json             # NEW — array of {id, inject_at_offset_seconds, raw_text, expected_outcome, notes}
                              #  ~20 entries spanning 24h, covering all cascade paths + parse failures

tests/
├── test_recovery.py          # NEW — unit tests for recovery.py
├── test_replay.py            # NEW — unit tests for replay.py
└── test_soak_assertions.py   # NEW — unit tests for soak_assertions.py
```

### 3.2 Why these locations

- **`tools/` not under `src/signal_copier/`**: the soak harness is not part of the running app. It imports the app for assertions only. Putting it under `src/signal_copier/` would force soak-only dependencies (subprocess management, signal handlers) into the production install and pollute the production import namespace.
- **`recovery.py` is a top-level module in `signal_copier/`**: recovery is a boot-time concern, distinct from per-signal scheduling lifecycle. It composes `Scheduler`, `StateStore`, and `Broker`; making it its own small unit keeps each module focused (PRD §7 design principle). It is not buried in `__main__.py` because that file already wires four concerns (config validation, DB connect, broker connect, coroutine start) and recovery deserves its own unit tests.
- **`replay.py` is gated by env var**: production builds never import it. Gate by lazy import inside `__main__.py` (e.g., `if os.environ.get("SOAK_REPLAY"): from signal_copier import replay; asyncio.create_task(replay.run(...))`).

### 3.3 Concurrency model

No new concurrency primitives. Recovery runs once at boot as an awaited coroutine *before* `telegram_listener()`, `trade_scheduler()`, and `result_monitor()` start. This guarantees supervisors are rehydrated before the scheduler begins draining the signals queue. Recovery itself is single-threaded (one async coroutine processing placed_* signals sequentially).

## 4. Restart-Recovery Design

### 4.1 Boot-time sequence

```
__main__.run() at startup:
  1. Load config (validate OLYMP_ACCOUNT_GROUP=demo, DRY_RUN, etc.)
  2. Connect asyncpg pool; run migrations
  3. Connect broker (DryRunBroker.connect() / OlympTradeBroker.connect())
  4. Construct notifier
  5. ★ NEW: await recovery.recover_active_signals(state_store, broker, scheduler)
  6. Start three long-running coroutines:
       - telegram_listener()
       - trade_scheduler()       # drains signals_queue
       - result_monitor()        # awaits push events for active cascades
```

Recovery runs **before** the listener starts so any rehydrated supervisors do not race with new signals arriving from Telegram.

### 4.2 The recovery algorithm

```python
# signal_copier/recovery.py

async def recover_active_signals(
    state_store: StateStore,
    broker: Broker,
    scheduler: Scheduler,
    *,
    now_unix: float | None = None,    # injected for testability
) -> RecoveryReport:
    """One-shot boot-time recovery of in-progress cascades.

    For each signal WHERE status IN ('placed_initial','placed_gale1','placed_gale2'):
      1. Compute stage_fire_ts from the stages table (most recent stage's placed_at_unix).
      2. If now > stage_fire_ts + expiration_seconds + grace_seconds:
         → trade's window has CLOSED while we were down.
         → scheduler.record_timeout(signal_id, stage)
            # record_timeout dispatches a ResultEvent(result='timeout') to the
            # M2 state machine, which applies FR-5.3 (timeout = loss for this
            # stage) and then the existing cascade-advancement rules (FR-5.5/
            # 5.6/5.7). In practice, when initial's expiration+grace has passed,
            # gale1's window (initial_trigger+5min) is also typically past
            # (expiration+grace = initial_trigger+5min30s), so the cascade
            # typically ends with `error (signal_expired)` per FR-3.6. The
            # state machine handles all of this transparently — recovery does
            # not need to reimplement cascade logic.
      3. Otherwise (window still open):
         → scheduler.adopt(signal_row)  # re-arm e:26 listener, resume
    Returns a RecoveryReport with counts (rehydrated, timed_out, abandoned).
    """
```

**Design choices:**

- **Step 2 handles the "broker dropped the trade while we were down" case** (per the agreed model: re-arm + trust, with the expiration-grace timer as the safety net). If `now > stage_fire_ts + expiration + grace`, we treat the trade as lost and record `timeout` — exactly as FR-5.3 would have done inline.
- **Step 3 is the happy path.** `scheduler.adopt()` re-registers the broker's e:26 callback for this trade_id and starts the supervisor's `wait_result` task fresh. If the broker delivers e:26 during the rehydration loop, the future resolves; if not, the supervisor waits the full grace window.
- **Idempotency.** Recovery is safe to run multiple times: `scheduler.adopt()` checks the signal's current status in DB before constructing a supervisor and is a no-op if status is already terminal. `record_timeout()` is also idempotent (DB UPDATE on a terminal status is a no-op).
- **`now_unix` is injectable** so unit tests can drive the algorithm with synthetic clocks without depending on `time.monotonic()`.

### 4.3 The scheduler's `adopt()` API

```python
# scheduler/trigger.py — new public method on Scheduler

async def adopt(self, signal_row: SignalRow) -> None:
    """Rehydrate a supervisor for a signal that was in-progress at last shutdown.

    Idempotent: a no-op if signal_row.status is already terminal.
    Builds a SignalState from signal_row + latest stage_row, registers a
    fresh broker e:26 callback for trade_id, and starts a SignalSupervisor
    coroutine that calls transition() on result.
    """
```

The supervisor built by `adopt()` is the *same* `SignalSupervisor` class used for fresh signals (M6). The only difference is its initial state — instead of constructing from a freshly-placed stage, it is reconstructed from `SignalRow + StageRow`. M9 ships **no new supervisor logic**; recovery reuses M6.

### 4.4 What is NOT recovered

- **`pending` signals** (scheduled but not yet fired). Their `call_at` timer died with the process; on boot, the listener will not see them arrive again (Telegram does not redeliver missed messages). These are simply lost. **Mitigation**: the listener watches `MessageEdited` events too (FR-1.5), so a re-post of the signal would be caught. There is no other way to recover a `pending` signal whose trigger time has passed — by FR-3.3 it is `error (signal_expired)` anyway.
- **Signals in `error` or terminal states** (`done_win` / `done_loss` / `done_tie` / `done_timeout`). The DB rows stay as the audit trail; no recovery action.

## 5. Replay Injector Design

`signal_copier/replay.py` — a small module loaded only when `SOAK_REPLAY=<path>` is set.

**Activation gate:** `__main__.py` checks for the env var. If set, it spawns a `replay_runner()` coroutine alongside the other three (listener, scheduler, result_monitor). If unset, the module is never imported in production.

**Behavior:**

- Reads the JSON fixture (see §8).
- For each entry: schedules an `asyncio.call_at(inject_at_offset + boot_unix, replay_callback)`.
- The callback builds a synthetic Telethon `telethon.tl.custom.Message` (with `raw_text=...`, `chat_id=config.TELEGRAM_TARGET_CHAT`, `id=<unique-integer>`, `date=<current-datetime>`) and passes it directly to the listener's message-handling coroutine — bypassing the Telethon event dispatch but exercising the parser → Signal → queue path. (Telethon's `NewMessage` is an Event that wraps a `Message`; we construct the inner `Message` and feed it to the listener's handler the same way Telethon's `NewMessage.Event` would.)
- Logs `signal_copier.replay` with `inject_at`, `raw_text`, `signal_id`.

**Why bypass Telethon but use the listener's handler:** the listener's `_handle_message()` is the function Telethon calls per event. Bypassing Telethon still exercises parsing, signal-id derivation, queue enqueue, and downstream — which is what we want to soak. We do NOT want to soak Telethon's reconnect behavior (M5 unit-tests cover that; M9 soak just probes liveness separately — see §6).

**Test:** `tests/test_replay.py` — feeds a 3-signal fixture, asserts the queue contains 3 `Signal` objects with correct fields; covers malformed-entry skip, past-dated entry skip, and the env-var gate.

## 6. Soak Harness Design

`tools/soak.py` — separate entrypoint, runnable as `python -m tools.soak`.

**CLI:**

```
python -m tools.soak \
  --duration 24h \
  --restart-at 12h \
  --fixtures tests/fixtures/soak_recordings/soak_24h.json \
  --env-file .env \
  --output-dir logs/soak_<timestamp>/
```

**Lifecycle:**

```
1. Parse CLI args.
2. Load .env into a child-env dict. Override:
     DATABASE_URL, SOAK_REPLAY=<fixtures path>, TELEGRAM_TARGET_CHAT,
     OLYMP_ACCOUNT_GROUP=demo, DRY_RUN=true, LOG_PATH=<output-dir>/app.log
3. Start Telethon liveness client (separate, see §7).
4. Subprocess.Popen(["python", "-m", "signal_copier"], env=child_env)
     capture stdout/stderr to <output-dir>/app.{out,err}
5. Spawn supervisor coroutines:
   - app_health_watcher: tails app logs, counts ERROR/FATAL/Traceback
   - assertion_scheduler: schedules final assertion pass at soak_duration
   - restart_driller: at restart_at, sends SIGTERM, waits for exit, relaunches subprocess, asserts new PID
6. Sleep until soak_duration (24h).
7. Run assertion suite (see §8). Exit 0 on pass, 1 on fail.
8. On exit: send SIGTERM to app subprocess, close liveness client, write summary report.
```

**Subprocess choice:** `subprocess.Popen` with `PIPE` for stdout/stderr. The soak harness stays simple — no `pystemd` or `pexpect`.

**Why a separate process:** in-process soak would miss "did the binary start" failures. The restart drill is also cleaner with subprocess — `os.kill(pid, SIGTERM)`. The PRD §17 hosting section commits us to Railway's restart-policy-driven process lifecycle, which is what the soak exercises.

## 7. Telethon Liveness Probe

A second Telethon client owned by the soak harness, *not* by the app.

- Connects to the real Telegram user account using the same `StringSession` from `.env`.
- Does NOT read any channel or process any messages.
- Every 30 min: `await client.get_me()` — succeeds if MTProto socket is alive.
- Every 1 min: `await client.is_connected()` — lighter check.
- Logs `tools.soak.liveness` with `connected: bool`, `timestamp`.
- Soak pass criterion: at least one `connected: true` per hour over 24 hours (see §8 invariant 8).

**Failure handling:** if `get_me()` raises `ConnectionError`, the liveness probe attempts one reconnect (Telethon's built-in); if that fails, logs WARNING. The soak does NOT treat liveness drops as fatal — it only tracks the metric. Sustained liveness failure is a sign the Telegram account is in trouble, not a soak failure.

## 8. Fixture Format

**`expected_outcome` enum** (formally defined):

| Value | Final `signals.status` | Expected `stages` rows |
|---|---|---|
| `win_at_initial` | `done_win` | 1 row: `(initial, win)` |
| `loss_initial_win_gale1` | `done_win` | 2 rows: `(initial, loss)`, `(gale1, win)` |
| `loss_initial_loss_gale1_win_gale2` | `done_win` | 3 rows: `(initial, loss)`, `(gale1, loss)`, `(gale2, win)` |
| `full_loss` | `done_loss` | 3 rows: `(initial, loss)`, `(gale1, loss)`, `(gale2, loss)` |
| `signal_expired` | `error` (`error_reason='signal_expired'`) | 0 rows (the signal never placed a trade) |
| `unsupported_pair` | `error` (`error_reason='unsupported_pair'`) | 0 rows (assertion only enforced for `OlympTradeBroker`; skipped under `DryRunBroker`) |
| `parse_failure` | (no signal row created) | 0 rows; expect a parse-failure log line in `logs/parse_failures.log` |

`tests/fixtures/soak_recordings/soak_24h.json`:

```json
[
  {
    "id": "soak_001",
    "inject_at_offset_seconds": 60,
    "raw_text": "💰5-minute expiration\nEUR/JPY;10:20;PUT🟥\n🕛TIME UNTIL 10:25\n1st GALE -> TIME UNTIL 10:25\n2nd GALE - TIME UNTIL 10:25",
    "expected_outcome": "win_at_initial",
    "notes": "happy path; first signal of the soak"
  },
  {
    "id": "soak_002",
    "inject_at_offset_seconds": 1800,
    "raw_text": "💰5-minute expiration\nGBP/USD;11:00;CALL🟩\n🕛TIME UNTIL 11:05\n1st GALE -> TIME UNTIL 11:05\n2nd GALE - TIME UNTIL 11:05",
    "expected_outcome": "loss_initial_win_gale1",
    "notes": "exercises gale1 branch"
  }
]
```

~20 entries spread across 24 hours. Outcomes chosen to cover: `win_at_initial`, `loss_initial_win_gale1`, `loss_initial_loss_gale1_win_gale2`, `full_loss`, `signal_expired`, `unsupported_pair`, `parse_failure`. ~5 of the 20 are intentionally malformed (missing semicolon, wrong emoji, etc.) to exercise the parse-failure log path.

`expected_outcome` is one of the enum values above. The fixture for the M9 24h soak contains ~20 entries spread across 24 hours, chosen so every enum value appears at least twice (with the exception of `unsupported_pair`, which appears once under dry-run and the assertion is skipped — it is exercised by `OlympTradeBroker` unit tests in M8, not by the M9 dry-run soak).

## 9. Pass/Fail Criteria (`tools/soak_assertions.py`)

```python
def assert_invariants(
    db: Database,
    app_log: Path,
    soak_log: Path,
    fixture: list[dict],
    liveness_records: list[LivenessRecord],
    restart_drill_result: RestartDrillResult,
) -> Report:
    """Returns (pass: bool, details: dict). Soak exits 0 iff pass."""
```

The 9 invariants, each a separate function returning `(passed: bool, detail: str)`:

1. **Uptime**: app_log contains the `Bot started` line; soak ran for the configured duration (≥).
2. **Zero unhandled exceptions**: no `Traceback` lines in app_log.
3. **Zero missed triggers**: count stage rows WHERE `placed_at_unix - trigger_ts_unix > 2.0` is zero (FR-3.5 tolerance).
4. **Zero duplicate trades**: no two stages with the same `(signal_id, stage)`.
5. **Zero DM failures**: no `DM send failed` log lines in app_log.
6. **Row counts match expected**: aggregate `stages` row counts match the sum of `expected_outcome` requirements from the fixture.
7. **Restart drill**: any cascade that was in flight at `restart_at` reaches a terminal state within 60 seconds of the restarted process becoming healthy. (We do not compare against a no-restart baseline — that would require running the soak twice and double the wallclock cost. The 60-second bound is the proxy for "recovery worked": a healthy rehydration must complete well within the cascade's remaining expiration window.)
8. **Telethon liveness**: at least 1 `connected: true` per hour over 24 hours (so at least 24 `connected: true` records).
9. **Per-signal outcomes**: for each fixture entry, the actual `signals.status` matches the fixture's `expected_outcome` mapping.

`assert_invariants()` aggregates. The soak prints a markdown summary at the end regardless of pass/fail.

## 10. Test Strategy

- **`tests/test_recovery.py`** — unit tests with a `FakeStateStore` returning synthetic `SignalRow`s + `StageRow`s. Covers: (a) all terminal statuses → no-op, (b) `placed_initial` with expired window → `record_timeout`, (c) `placed_gale1` within window → `adopt()`, (d) idempotency (running twice does not double-recover), (e) `now_unix` injection for deterministic time control.
- **`tests/test_replay.py`** — covers: (a) basic 3-signal fixture → 3 `Signal` objects in queue, (b) malformed entries skipped with WARNING log, (c) past-dated entries skipped, (d) gate-by-env-var (when `SOAK_REPLAY` unset, replay module is not imported even if listed in `__main__`).
- **`tests/test_soak_assertions.py`** — feeds synthetic DB rows + log files + fixture, asserts each of the 9 invariant functions returns pass/fail correctly. The "good" fixture passes; "bad" fixtures fail with the expected error message and the assertion surfaces the right invariant number.
- **`tests/test_scheduler.py` (EDIT)** — add test for `scheduler.adopt()`: fake broker, fake state store, assert a `SignalSupervisor` is constructed and `wait_result` is awaited.

**The soak itself is NOT a pytest test** — it is a runnable script, not a CI test (CI runs the unit + integration suite; the soak is run manually before a release). A short-form soak (`--duration 5m --fixtures soak_short.json`) is used as a smoke test during M9 development.

## 11. Risks & Open Questions

| Risk | Likelihood | Mitigation |
|---|---|---|
| Vendored `olymptrade_ws` has a hidden WS-close signal we miss during recovery | Low | M9 will surface this; covered in M10 self-healing supervisor. `DryRunBroker` does not have this risk (M9 soaks in dry-run). |
| Telethon account gets `FloodWaitError` during 24h soak | Medium | Liveness probe logs it; soak continues. Acceptable. |
| Restart at hour 12 lands mid-`place_order` (ambiguous state — order may or may not be at broker) | Low | `record_timeout` on the orphaned stage; cascade ends in `done_loss` for that signal. Acceptable; the M9 restart drill will demonstrate this case. |
| 24h soak fails on flaky network → flake | Medium | `tools/soak.py` is rerunnable; failure report points to the failing invariant number. Not in CI; user re-runs after fixing. |
| `tools/` not on `PYTHONPATH` — soak import fails | Low | `tools/__init__.py` + run as `python -m tools.soak`. Document in README. |
| Restored cascades from a long downtime (>stage_fire_ts + expiration + grace) all get marked `timeout`, even if some could have been re-derived | Low | Per the agreed model (re-arm + trust broker, with grace timer as safety net). Acceptable: the cascade math for a missed grace window is "we cannot know the result" → mark loss → cascade continues or ends naturally. No special handling. |

## 12. Definition of Done

M9 is complete when **all** of the following hold:

1. ✅ All new unit tests pass: `pytest tests/test_recovery.py tests/test_replay.py tests/test_soak_assertions.py` — green.
2. ✅ Modified tests pass: `pytest tests/test_scheduler.py` — green.
3. ✅ Full test suite green: `pytest` — zero failures.
4. ✅ Lint/mypy clean: `ruff check`, `mypy` — zero errors.
5. ✅ Soak script runs locally (smoke): `python -m tools.soak --duration 5m --fixtures tests/fixtures/soak_recordings/soak_short.json` exits 0.
6. ✅ Manual 24h soak with `DRY_RUN=true` against a real Telegram `StringSession` completes with exit 0. Evidence (pass/fail report + invariants 1–9 output) recorded in `docs/superpowers/evidence/2026-XX-XX-m9-soak.md`.
7. ✅ Restart-recovery proven: the 24h soak's invariant 7 (restart drill) passes.
8. ✅ No edits to `src/olymptrade_ws/` (R-15 / §12.6 unchanged).

## 13. Out-of-Milestone Handoffs

- **M10** (self-healing reconnect supervisor) builds on the M9 recovery mechanism but addresses the in-process WS drop case (no restart). M10 will also wrap `BrokerAuthError` in a circuit breaker (PRD S-11).
- **M11** (Railway deployment) consumes the M9 soak as its pre-deployment gate. Deploys only happen on a green M9 24h soak.
- **v2 (real-money trading)** is gated behind FR-6.6's "7-day clean demo soak". When that work begins, the M9 soak script + fixtures are extended (longer duration, `OlympTradeBroker` instead of `DryRunBroker`, additional invariants for broker-reported PnL accuracy).

---

*End of M9 design spec. Next step: user review, then writing-plans.*