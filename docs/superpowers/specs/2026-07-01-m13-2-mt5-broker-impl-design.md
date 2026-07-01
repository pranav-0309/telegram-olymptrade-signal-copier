# M13.2 MT5 Broker Implementation — Design Spec

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the M13.1 `Mt5Broker` stub with a real implementation that talks to a MetaTrader 5 terminal via the `mt5linux` drop-in client. Add the reconnect supervisor helper, the preflight sanity-check tool, and the `mt5linux` dependency. End state: a `DRY_RUN=false` boot opens market orders, polls for position status, and closes positions reporting broker-side PnL as `Decimal`.

**Architecture:** One broker class (`Mt5Broker`) with internal retry. The reconnect is a function-call helper (`with_retry`) in a sibling module — no wrapper class. Tests mock `mt5linux` calls because real MT5 requires a running terminal. Preflight is a standalone synchronous script that runs once and exits.

**Tech Stack:** Python 3.13, asyncio, pydantic-settings, mt5linux (drop-in for `MetaTrader5`), pytest + monkeypatch (for mockable MT5 calls).

---

## 1. Success criteria

1. **`pytest tests/` is green** (with all `mt5linux.*` calls mocked).
2. **Preflight exits 0 against the user's VT Markets demo** (when run with real MT5 terminal up): prints account info, leverage, currency, symbol count.
3. **`Mt5Broker.place(signal, stage="initial", amount=Decimal("2.00"))` submits a market BUY/SELL of 0.01/0.02/0.04 lots** keyed by `stage` on MT5; returns the integer ticket as a string.
4. **`Mt5Broker.wait_result(ticket, timeout=300)` polls `mt5.positions_get(ticket=…)` every ~250ms** and returns `"win"`/`"loss"`/`"tie"` from the position's last-known profit field — or `"timeout"` if the position doesn't close within the timeout.
5. **`Mt5Broker.close_position(ticket, timeout=5)` returns the broker-reported PnL as `Decimal`** by calling `mt5.Close()` and reading the resulting position's `profit` field.
6. **`Mt5Broker.close()` calls `mt5.shutdown()`** idempotently.
7. **Connection failures retry with exponential backoff** (5 attempts, base 1s, cap 30s, ±10% jitter); after exhaustion the supervisor emits `on_broker_reconnect_failed` and raises `BrokerAuthError`.
8. **All MT5 errors logged at WARNING with `retcode` + `comment` + context** (signal_id, stage, retry attempt).
9. **`pyproject.toml` lists `mt5linux>=1.0.0`** as a runtime dep.
10. **No new exception class added to `broker/base.py`** — granular errors map to existing `BrokerAuthError` or `UnsupportedPairError`.

---

## 2. Commit structure (Approach C — 2 commits)

| # | Commit message | Includes | Verify |
|---|---|---|---|
| 1 | `feat(broker): implement real Mt5Broker + reconnect helper (M13.2)` | `broker/mt5.py` (replace stub), `broker/reconnect.py` (NEW), `pyproject.toml` (+ mt5linux dep), `tests/test_mt5_broker.py` (renamed + rewritten), `uv.lock` | `pytest tests/` green; `Mt5Broker` against mocked mt5linux raises no errors. Stub retired. |
| 2 | `chore(tools): add mt5_preflight sanity check + preflight smoke test` | `tools/mt5_preflight.py` (NEW), `tests/test_mt5_preflight.py` (NEW) | `pytest tests/` green; `python -m tools.mt5_preflight` runs (real MT5 terminal required for exit-0). |

**Rationale:** Commit 1 is load-bearing (unblocks M13.5 scheduler work). Commit 2 is a verification tool — separable.

---

## 3. File changes — Commit 1

### 3.1 `src/signal_copier/broker/mt5.py` — REPLACE M13.1 stub

Module-level constants:

```python
LOTS_BY_STAGE: dict[Stage, Decimal] = {
    "initial": Decimal("0.01"),
    "gale1":   Decimal("0.02"),
    "gale2":   Decimal("0.04"),
}
SYMBOL_SUFFIX: str = "-STD"          # VT Markets STD demo
_POLL_INTERVAL_SEC: float = 0.25     # wait_result polling cadence
```

Class behavior:

| Method | Behavior |
|---|---|
| `__init__(*, login, password, server, terminal_path, notifier)` | Stores all 5 kwargs. Initializes `_symbol_cache: dict[str, str] = {}`, `_start_of_day_balance: Decimal \| None = None`, `_last_known_profit: dict[str, Decimal] = {}`, `_connected = False`, `_connect_lock = asyncio.Lock()`. |
| `async connect()` | Wraps internal `_sync_initialize()` in `asyncio.to_thread()` + `with_retry()` (from `broker/reconnect.py`). On retryable failure: emit `on_broker_reconnecting(attempt, max_attempts, downtime_so_far, next_delay_seconds)`. On exhaustion: emit `on_broker_reconnect_failed(attempts, total_downtime)` + raise `BrokerAuthError`. On success: cache `mt5.account_info().balance` → `_start_of_day_balance`, pre-populate `_symbol_cache` for the 10 known pairs (EUR/JPY, EUR/USD, EUR/GBP, GBP/USD, GBP/JPY, USD/JPY, USD/CHF, USD/CAD, AUD/USD, NZ/USD), and emit `on_broker_reconnected(attempts_used, total_downtime_seconds=0.0)`. |
| `async place(signal, *, stage, amount)` | Resolves `signal.pair` → broker symbol: cache lookup → if miss, `_resolve_symbol(input_pair, allow_fetch=True)` which appends `SYMBOL_SUFFIX` (e.g., `EURUSD-STD`), tries `mt5.symbol_info()`, falls back to `mt5.symbols_get(f"*{base}*")` prefix-match (prefers exact match); if still None → raise `UnsupportedPairError`. Builds `mt5.order_send()` request with `LOTS_BY_STAGE[stage]` volume (ignores `amount` Decimal), `type=ORDER_TYPE_BUY` if `signal.direction=="up"` else `ORDER_TYPE_SELL`, `magic=0`, `comment=f"signal-copier:{signal.signal_id}:{stage}"`, `type_filling=ORDER_FILLING_IOC`. Returns `str(result.order)`. Records `_last_known_profit[trade_id] = Decimal("0")` initially (overwritten on close). Retcode handling: 10009=OK; 10018 → `BrokerAuthError("Insufficient funds for {stage}: retcode=10018 comment={comment}")`; 10019 → same; 10006 → `UnsupportedPairError(f"MT5 rejected order: retcode=10006 comment={comment}")`; other non-OK → `BrokerAuthError(f"mt5.order_send failed: retcode={retcode} comment={comment}")`. Each branch logs at WARNING with retcode + comment + signal_id + stage. |
| `async wait_result(trade_id, *, timeout)` | Background poll task: every `_POLL_INTERVAL_SEC`, call `mt5.positions_get(ticket=int(trade_id))`. If position is gone, look up `_last_known_profit[trade_id]`: `>0` → `"win"`, `<0` → `"loss"`, `==0` → `"tie"`. Wrap in `asyncio.wait_for(...)`. On `TimeoutError` (scheduler's `wait_for` cancellation): return `"timeout"`. |
| `async close_position(trade_id, *, timeout)` | Calls `mt5.Close(ticket=int(trade_id))` via `asyncio.to_thread`. Reads `mt5.positions_get(ticket=...)` for the position's last-known `profit` field before/after close. Returns `Decimal(profit)`. Logs retcode if non-OK. |
| `async close()` | `mt5.shutdown()` via `asyncio.to_thread`. Idempotent. |

**Key lock-ins (locked during brainstorming):**
- Lot sizing ignores the `amount` Decimal param; keyed on `stage`.
- Symbol suffix is hardcoded `"-STD"` (user's VT Markets STD account).
- `wait_result` polls every 250ms; on natural TimeoutError returns `"timeout"`.
- All MT5 errors log retcode + comment + context at WARNING; mapped to existing `BrokerAuthError` or `UnsupportedPairError` (no new exception class).
- `notifier` is typed as `object` (M13.1's choice; narrows to `Notifier` is M13.5 concern) — used only if it has the optional `on_broker_*` methods (graceful degradation).

### 3.2 `src/signal_copier/broker/reconnect.py` — NEW

```python
"""MT5-flavored reconnect primitives (M13.2).

Provides:
  - compute_backoff_seconds(attempt, base=1.0, cap=30.0, jitter=0.1)
  - with_retry(op, *, op_name, on_retry, on_exhausted, max_attempts=5)

`with_retry` is an async function-call helper (NOT a class). Mt5Broker
calls it to wrap connect() and any subsequent re-initialization after
session loss.
"""
```

**Behavior:**

- `compute_backoff_seconds(attempt, base=1.0, cap=30.0, jitter=0.1)`:
  - Returns `min(base * 2**attempt, cap)` with `±jitter` randomization.
  - Example: attempts 0→4 with base=1, cap=30, jitter=0.1 → ~1, 2, 4, 8, 16, ~capped at 30.

- `async with_retry(op, *, op_name, on_retry, on_exhausted, max_attempts=5)`:
  - Tracks `_downtime_start = time.monotonic()` at first call.
  - Loops `max_attempts` times: try `await op()`. On success, return `attempts_used=current_attempt + 1` (1-based).
  - On retryable exception (subclass of `(BrokerAuthError, OSError)` — the latter catches MT5 IPC socket drop):
    1. `delay = compute_backoff_seconds(attempt)`.
    2. `await on_retry(attempt=attempt+1, max_attempts=max_attempts, downtime_seconds=monotonic()-_downtime_start, next_delay_seconds=delay)`.
    3. `await asyncio.sleep(delay)`.
  - After `max_attempts`: `await on_exhausted(attempts=max_attempts, total_downtime_seconds=…)` then raise `BrokerAuthError(f"{op_name} failed after {max_attempts} attempts")`.

- `op_name` is a human-readable label for the wrapped op (e.g., `"mt5.initialize"`, `"mt5.order_send"`).

### 3.3 `pyproject.toml`

Add one entry to `dependencies = [...]`:

```toml
dependencies = [
    "pydantic-settings>=2.6",     # M2: config layer
    "tzdata>=2024.1",             # IANA tz database on Windows
    "asyncpg>=0.30",              # M4: async PostgreSQL driver
    "telethon>=1.44",             # M5: Telegram MTProto user-account client
    "loguru>=0.7,<1.0",           # M7: rotating loguru sinks + DM mirror
    "mt5linux>=1.0.0",            # M13.2: MT5 client (drop-in for MetaTrader5)
]
```

No other `pyproject.toml` changes. No new Config fields (lots + suffix hardcoded per brainstorming). No `.env.example` change (M13.5).

### 3.4 `tests/test_mt5_broker.py` — REPLACE `tests/test_mt5_broker_stub.py`

The M13.1 stub test file (with 5 `*_raises_not_implemented` tests + isinstance check) is replaced. The new test file uses `monkeypatch.setitem(sys.modules, "mt5linux", fake_mt5)` to inject a mocked `mt5linux` module. Tests cover:

| Test name | Asserts |
|---|---|
| `test_mt5_broker_satisfies_protocol` | `isinstance(Mt5Broker(...), Broker)` + `isinstance(DryRunBroker(), Broker)` |
| `test_mt5_broker_connect_succeeds_with_valid_init` | mocked `mt5.initialize=True` → `connect()` returns without error; `_start_of_day_balance` populated |
| `test_mt5_broker_connect_raises_broker_auth_error_on_init_false` | mocked `mt5.initialize=False` + `last_error=(-10005, "IPC error")` → `BrokerAuthError` raised |
| `test_mt5_broker_place_submits_market_order_with_correct_lots` | mocked `order_send` with `LOTS_BY_STAGE[stage]` volume + BUY/SELL direction; returns ticket as string |
| `test_mt5_broker_place_returns_unsupported_pair_error_on_missing_symbol` | mocked `symbol_info=None` + `symbols_get=[]` → `UnsupportedPairError` |
| `test_mt5_broker_wait_result_returns_win_when_position_closed_positive` | mocked `positions_get` transitions from `[position]` to `[]`; `_last_known_profit` set positive → returns `"win"` |
| `test_mt5_broker_wait_result_returns_loss_when_position_closed_negative` | same, profit negative → `"loss"` |
| `test_mt5_broker_wait_result_returns_timeout_on_wait_for_cancellation` | `asyncio.wait_for` cancel → `"timeout"` |
| `test_mt5_broker_close_position_returns_decimal_profit` | mocked `mt5.Close` returns OK; mocked `positions_get` returns `profit` field → `Decimal` returned |
| `test_mt5_broker_close_calls_shutdown` | mocked `mt5.shutdown` called; idempotent (second call OK) |

(10 tests. The exact list lives in the plan, not the spec.)

The new test file's `_broker()` helper instantiates `Mt5Broker(...)` with placeholder creds + `notifier=None`. Each test installs a `fake_mt5` MagicMock into `sys.modules["mt5linux"]` to control return values.

---

## 4. File changes — Commit 2

### 4.1 `tools/mt5_preflight.py` — NEW

```python
"""M13.2 mt5_preflight — sanity check before live deploy.

Runs through:
  1. Load MT5_* env vars (.env loaded explicitly; no pydantic Config here)
  2. mt5.initialize() → connect
  3. mt5.login_info() + mt5.account_info() → snapshot
  4. mt5.symbols_get(group="*STD*") → asset-map probe
  5. mt5.shutdown()

Prints PASS/FAIL summary. Exits 0 on success, 1 on any MT5 error.

Run:    uv run python -m tools.mt5_preflight
"""
```

**Output shape (success):**

```
[OK] mt5.initialize      → MT5 terminal reachable
[OK] mt5.login_info      → user=12345678 server=VTMarkets-Demo-STD
[OK] mt5.account_info    → balance=10000.00 leverage=1:500 currency=USD
[OK] mt5.symbols_get     → 104 tradeable symbols (STD-named)
PASS
```

**Output shape (failure on auth):**

```
[FAIL] mt5.initialize    → login error: (-10005, 'IPC: No IPC connection')
       Hint: Is the MT5 terminal running with the configured server?
FAIL (exit 1)
```

**Key behavior:**

- Reads MT5 creds from `os.environ` after `load_dotenv()`. No pydantic `Config` instance.
- Uses the same `SYMBOL_SUFFIX = "-STD"` constant — duplicated from `broker/mt5.py` (acceptable: a preflight hardcoded to the user's STD account is local config, not library API).
- Module exposes a `run_preflight()` function that returns the exit code (`0` or `1`); the `__main__` block calls `sys.exit(run_preflight())`. Function extracted for testability.
- All output via `print()` (no loguru setup).
- No persistent state; clean shutdown on every path (try/finally around `mt5.shutdown()`).

### 4.2 `tests/test_mt5_preflight.py` — NEW minimal smoke

| Test | Asserts |
|---|---|
| `test_preflight_prints_pass_on_successful_init` | mocked `mt5.initialize=True` + valid `login_info`/`account_info`/`symbols_get` → `run_preflight()` returns `0` + stdout contains "[OK] mt5.initialize" + "PASS" |
| `test_preflight_exits_1_on_init_failure` | mocked `mt5.initialize=False` + `last_error=(-10005, "IPC: No IPC connection")` → `run_preflight()` returns `1` + stdout contains "[FAIL] mt5.initialize" |
| `test_preflight_exits_1_when_credentials_missing` | missing `MT5_LOGIN` env var → `run_preflight()` returns `1` + helpful error message about `.env` |
| `test_preflight_handles_partial_account_info_gracefully` | `account_info()` returns object with `balance=None` (degraded) → preflight does not crash, prints what it can |

---

## 5. Out of scope (deferred to later milestones)

| # | Item | Milestone | Reason |
|---|---|---|---|
| 1 | `domain/state.py:_stage_pnl` removal; add `pnl: Decimal \| None = None` to `ResultEvent` | M13.5 | Per `docs/refactor.md` §4.5: state machine accepts broker-reported PnL only after M13.2 proves the value is trustworthy via the preflight + a real demo trade. |
| 2 | `scheduler/trigger.py:_drive_cascade` auto-close race (`call_at` deadline vs `result_future`) | M13.5 | Coupled with #1 — needs `ResultEvent.pnl` plumbing first. |
| 3 | `scheduler/trigger.py:_compute_stage_pnl_for_result` removal | M13.5 | Mirrors #1. |
| 4 | `.env.example` rewrite — `MT5_*` block (per `docs/refactor.md` §6.2) | M13.5 | Manual user migration step, batched with docs sweep. |
| 5 | `README.md` — update to MT5 wording (15 refactor.md-flagged references) | M13.5 | Documentation sweep. |
| 6 | `docs/PRD.md` — header + FR-4.x + §6 + §7 + §10 + §13.1 + §15 + §17.1 | M13.5 | Historical PRD update; doesn't affect runtime. |
| 7 | `tests/test_auth.py` 21 `OLYMP_*` env-clearing literals (`tests/test_main.py` defensive cleanup too) | M13.5 | Cosmetic; harmless under `extra="ignore"`. |
| 8 | Add `mt5_lot_initial` / `mt5_lot_gale1` / `mt5_lot_gale2` Config fields | **REJECTED permanently** | Brainstorming Q5: lots are hardcoded by stage in `broker/mt5.py:LOTS_BY_STAGE`. User chose explicit constants over config. |
| 9 | Add `mt5_symbol_suffix` Config field | **REJECTED permanently** | Brainstorming Q2+Q3: suffix is hardcoded `"-STD"` for the user's VT Markets STD account. Single account, single suffix. |
| 10 | New `InsufficientFundsError` / granular retcode exception class | **REJECTED permanently** | Brainstorming Q6: insufficient funds → `BrokerAuthError("Insufficient funds for {stage}: …")`. Generic auth-error surface keeps the Protocol simple. |
| 11 | `mt5linux/Dockerfile` + `entrypoint.sh` + `requirements.txt` + `railway.toml` | M14.1 | Railway Service B; needs a working broker impl first. |
| 12 | Wire Railway Service A → Service B (private-network `MT5_SERVER_HOST` env per `docs/refactor.md` §9.4) | M14.3 | Requires item 11 + a working broker. |
| 13 | First live-demo trade via Railway end-to-end | M14.4 | Success criterion for the whole broker swap. |
| 14 | Restart resilience test (kill Service A mid-cascade) | M14.5 | Uses M13.5's auto-close race. |
| 15 | Rollback smoke test (`git revert` workflow) | M14.6 | M13.2 just ships; rollback is tested after a few demo trades. |
| 16 | GitHub repo rename `olymptrade` → `telegram-mt5-copier` | M13.6 | Operational; lower-risk after code is clean (`docs/refactor.md` §2.1-2.3). |
| 17 | Railway project + service rename | M13.6 | Operational (`docs/refactor.md` §3.1-3.2). |

---

## 6. Verification

```bash
# Commit 1 verification
uv run pytest tests/test_mt5_broker.py tests/test_broker_protocol.py -v       # mocked path: green
uv run ruff check src/signal_copier/broker/ tests/test_mt5_broker.py
uv run mypy src/signal_copier/broker/mt5.py src/signal_copier/broker/reconnect.py

# Commit 2 verification
uv run pytest tests/test_mt5_preflight.py -v                                    # mocked path: green

# Final pre-deploy manual smoke
uv run python -m tools.mt5_preflight                                         # exits 0 against real MT5
```

**All `uv run pytest tests/` runs must remain green at every step.**

A full pre-deploy smoke is manual (the preflight script itself). It is NOT run in CI because CI doesn't have an MT5 terminal.

---

## 7. Risk & rollback

**Commit 1 risks**
- `with_retry` exhausts after 5 attempts (~63s total per the backoff schedule). If MT5 terminal is genuinely down, the supervisor's user-facing DM (`on_broker_reconnect_failed`) fires five times before exit. Acceptable UX for first deploy; can tune `max_attempts` later if needed.
- `wait_result` polling at 250ms × 5 minutes = ~1200 polling iterations per signal stage. Light load but not zero. If MT5's terminal becomes unresponsive under polling pressure, the broker's `wait_result` will block until `timeout` and return `"timeout"` — the scheduler handles this in M13.5.
- Mock-only test coverage means a real MT5 terminal might behave differently than `MagicMock`. `tools/mt5_preflight.py` is the only path to verify against a real MT5 — defer to M14.4 (first Railway demo trade).

**Commit 2 risks**
- Preflight is a script; the `sys.modules` mocking pattern works for tests but a future contributor might miss the `notifier=None` parameter shape. Documented in `tools/mt5_preflight.py` module docstring.

**Rollback**
- Commit 1: `git revert <sha>` — restores the M13.1 stub `Mt5Broker`, deletes `broker/reconnect.py`, removes `mt5linux` dep. Project reverts to "stub-throws-NotImplementedError on live demo" behavior. Functionally reversible.
- Commit 2: `git revert <sha>` — deletes the preflight tool + test. Lossless revert.

---

## 8. Cross-references

- `docs/refactor.md` §4.4 — Protocol design decision rationale (`close_position`)
- `docs/refactor.md` §4.5 — PnL handling (broker-reported, not approximated) — gates M13.5 state machine work
- `docs/refactor.md` §5 — `mt5linux` rationale + Dockerfile template (reused in M14.1)
- `docs/refactor.md` §7 — Local development setup (Option A native MT5 vs Option B Docker)
- `docs/refactor.md` Appendix A — Per-file cheat sheet (mt5.py row)
- `docs/refactor.md` Appendix B — Milestone mapping (M13.2 + downstream)
- `docs/superpowers/specs/2026-07-01-m13-1-broker-protocol-config-rename-design.md` — M13.1 (predecessor)
- `docs/superpowers/plans/2026-07-01-m13-1-broker-protocol-config-rename.md` — M13.1 plan (already executed)

---

*End of M13.2 design spec.*
