# M7 Design Spec — Telegram Self-DM Notifications

**Date:** 2026-06-21
**Status:** Implemented (M7 complete; 235/235 tests pass, mypy --strict clean, ruff clean)
**Milestone:** M7
**PRD reference:** `docs/PRD.md` §4.7 (FR-7.1, FR-7.2, FR-7.4), §6, §15

---

## 1. Purpose

Implement rich Telegram self-DM notifications for every FR-7.1 event, migrate the local log infrastructure to loguru with 10 MB × 5 rotation, and extend the `Notifier` Protocol with the three FR-7.1 events that the M6 Protocol omitted. M7 makes the tool **observable to the user** without a web dashboard and gives the daily log file the durability and rotation it needs for unattended Railway operation.

## 2. Scope

In scope for M7:

- `TelegramDMNotifier` implementing the full `Notifier` Protocol (10 existing methods + 3 new).
- Extension of the `Notifier` Protocol in `notify/protocol.py` with three new methods: `on_parse_failure`, `on_telegram_disconnect`, `on_olymp_disconnect`.
- `send_to_self()` method on the M5 `TelegramClient` wrapper (single-connection principle per FR-7.4).
- Migration of `infra/log.py` from stdlib `logging` to **loguru** with three sinks (stderr, rotating file, parse-failures file) and an `InterceptHandler` that preserves all existing stdlib `logging.getLogger(__name__)` call sites.
- Wiring in `__main__.py` to choose `TelegramDMNotifier` vs `NoOpNotifier` based on `config.telegram_self_dm_notifications`.
- Wiring in `telegram/listener.py` to emit `on_parse_failure`.
- Wiring in `telegram/client.py` to emit `on_telegram_disconnect` from the reconnect loop.

Explicitly out of scope for M7:

- The actual emission point for `on_olymp_disconnect` — that is M8/M10's responsibility (broker + reconnect supervisor). M7 ships the method, DM template, and `NoOpNotifier` log line only.
- Desktop notifications, sound alerts, CSV export, inline-keyboard confirmations (PRD §14 deferred items).
- Real-money integration (FR-6.6 guardrail unchanged).

## 3. Architecture

Three components, layered. None of them cross concerns:

1. **`notify/protocol.py` (extend existing)** — three new methods on `Notifier` + matching `NoOpNotifier` methods. `isinstance(_, Notifier)` still works everywhere; M6 scheduler and M5 listener both take the same `Notifier` parameter type.

2. **`notify/telegram_dm.py` (new)** — `TelegramDMNotifier` class implementing the full Protocol. One async method per event. Each builds the FR-7.1 message string and calls a private `_send(text)` helper that does the Telegram send plus the loguru INFO mirror. `_send` swallows all exceptions (log-and-swallow per D-5) so a DM failure can never abort a cascade.

3. **`infra/log.py` (rewrite)** — loguru configuration with three sinks (stderr, rotating file `logs/signal_copier.log`, parse-failures file `logs/parse_failures.log`) plus an `InterceptHandler` that forwards stdlib logging records to loguru. Existing modules (`NoOpNotifier`, scheduler, broker, db) need no changes — their `logging.getLogger(__name__).info(...)` calls flow through the bridge.

Supporting changes (surgical, minimal):

- `telegram/client.py` — `send_to_self(text)` calls `self._client.send_message("me", text)`. Optional `notifier` param on `start()` emits `on_telegram_disconnect` from the reconnect loop.
- `telegram/listener.py` — `notifier: Notifier` ctor param; emits `on_parse_failure` in the parse-failure branch of `on_new_message` and `on_message_edited`.
- `__main__.py` — config-driven selection of `TelegramDMNotifier` vs `NoOpNotifier`; both `Scheduler` and `Listener` get the same instance.

## 4. Protocol extension

Add three async methods to `Notifier` in `src/signal_copier/notify/protocol.py`:

```python
async def on_parse_failure(
    self,
    raw_text: str,
    reason: FailureReason,
) -> None:
    """FR-7.1 row 'Parse failure'. Fires from the M5 Listener when a
    message doesn't match the signal regex."""

async def on_telegram_disconnect(self) -> None:
    """FR-7.1 row 'Telegram disconnect'. Fires from the M5 TelegramClient
    wrapper on ConnectionError before reconnect."""

async def on_olymp_disconnect(self) -> None:
    """FR-7.1 row 'OlympTrade disconnect'. Fires from M8/M10's
    reconnect supervisor. M7 ships the method only — emission wiring
    lands in M8 (broker) and M10 (reconnect supervisor)."""
```

**Type imports added:** `FailureReason` from `signal_copier.domain.signal` (already a `StrEnum` with the PRD's reason strings: `missing_header_line`, `missing_signal_line`, etc.).

**`NoOpNotifier`** gains three matching methods. Parse failure logs at INFO; disconnect events log at WARNING (they signal operational anomalies).

**`tests/_scheduler_fixtures.py:RecordingNotifier`** gains three matching `_record()` calls so M6 scheduler tests can verify the new events are never emitted from scheduler paths.

**Rationale:** Keeping these on the Protocol (vs. adding a second interface) means the M6 scheduler, M5 listener, and M8 broker all take the same `Notifier` parameter type — no type-narrowing, no second swap point. `FailureReason` is already a `StrEnum`, so the notifier can render reason names without an extra mapping.

## 5. `TelegramDMNotifier` implementation

**File:** `src/signal_copier/notify/telegram_dm.py` (~350 lines, mostly the 13 message templates).

### 5.1 Class shape

```python
class TelegramDMNotifier:
    """Notifier that sends FR-7.1 messages to the user's 'Saved Messages'.

    Uses the SAME Telethon client as the listener (FR-7.4) via the
    TelegramClient wrapper's send_to_self() method. All exceptions
    raised by send_to_self() are caught and logged at WARNING; the
    cascade must never be aborted by a DM failure (D-5).
    """

    def __init__(
        self,
        *,
        tg_client: TelegramClient,
        config: Config,
    ) -> None:
        self._tg = tg_client
        self._config = config

    async def _send(self, text: str) -> None:
        try:
            await self._tg.send_to_self(text)
        except Exception as exc:
            _loguru_logger.bind(dm_event=True).warning(
                "DM send failed: text_preview={!r} exc={}", text[:80], exc
            )
            return
        _loguru_logger.bind(dm_event=True).info(text)
```

### 5.2 Message templates (PRD §4.7 FR-7.1, verbatim)

Plain text only — no `parse_mode`. Emoji + newlines for visual structure. `(UTC-3)` is a **literal string** in every timestamp line — it is **not** derived from `config.timezone`. R-3 confirmed the analyst's timezone is fixed at `America/Sao_Paulo` (UTC-3 year-round, no DST since 2019). If the analyst ever changes TZ, the DM templates would need a manual update (acceptable v1 trade-off — keeps the templates simple and matches the PRD's literal examples verbatim).

| Event | Template |
|---|---|
| `on_signal_received` | `🟢 Signal received\nPair: {pair}\nDirection: {dir_str}\nTrigger: {hhmm} (UTC-3)\nExpiration: {N} min` |
| `on_trade_placed` (initial) | `⏱️ Trade placed (INITIAL)\nPair: {pair}\nDirection: {dir_str}\nAmount: ${amount:.2f}\nExpires: {hhmm} (UTC-3)\nTrade ID: {trade_id}` |
| `on_trade_placed` (gale1) | `⏱️ Trade placed (1st GALE)\nAmount: ${amount:.2f}\nExpires: {hhmm} (UTC-3)\nTriggered by: loss on initial\nTrade ID: {trade_id}` |
| `on_trade_placed` (gale2) | `⏱️ Trade placed (2nd GALE)\nAmount: ${amount:.2f}\nExpires: {hhmm} (UTC-3)\nTriggered by: loss on 1st gale\nTrade ID: {trade_id}` |
| `on_win` (initial) | `✅ WIN (INITIAL)\nPair: {pair}\nPnL: ${pnl:+.2f}\nSignal closed: done_win\nNext: stop (cascade ends)` |
| `on_win` (gale1) | `✅ WIN (1st GALE)\nPair: {pair}\nPnL: ${pnl:+.2f}\nCascade: stopped after gale1 — total recovered` |
| `on_win` (gale2) | `✅ WIN (2nd GALE)\nPair: {pair}\nPnL: ${pnl:+.2f}\nCascade: stopped after gale2 — full recovery` |
| `on_loss` (initial, next=gale1) | `❌ LOSS (INITIAL)\nPair: {pair}\nPnL: ${pnl:+.2f}\nNext: scheduling 1st gale at {hhmm} (UTC-3), ${gale_amount:.2f}` |
| `on_loss` (gale1, next=gale2) | `❌ LOSS (1st GALE)\nPair: {pair}\nPnL: ${pnl:+.2f}\nNext: scheduling 2nd gale at {hhmm} (UTC-3), ${gale_amount:.2f}` |
| `on_loss` (gale2, next=None) | `❌ LOSS (2nd GALE)\nPair: {pair}\nPnL: ${pnl:+.2f}\nCascade: ended — full loss (${cumulative_pnl_abs:.2f} total)` |
| `on_signal_expired` (initial) | `⏰ Signal EXPIRED (INITIAL)\nPair: {pair}\nTrigger was: {hhmm} (UTC-3)\nReason: time window passed before fire\nAction: no trades placed; signal invalid` |
| `on_signal_expired` (gale1) | `⏰ Signal EXPIRED (1st GALE)\nPair: {pair}\nGale1 trigger was: {hhmm} (UTC-3)\nReason: time window passed before fire\nAction: no gale2 placed — cascade ended` |
| `on_signal_expired` (gale2) | `⏰ Signal EXPIRED (2nd GALE)\nPair: {pair}\nGale2 trigger was: {hhmm} (UTC-3)\nReason: time window passed before fire\nAction: cascade ended, no recovery attempted` |
| `on_cascade_complete` | `🏁 Cascade complete: {final_state}\nSignal ID: {signal_id}\nTotal PnL: ${cumulative_pnl:+.2f}\nDuration: {human_dur}` |
| `on_signal_rejected_by_limit` (loss) | `⚠️ Daily loss limit reached\nLosses today: ${summary.realized_pnl:.2f}\nLimit: ${config.daily_loss_limit:.2f}\nAction: no new signals until 00:00 (UTC-3)` |
| `on_signal_rejected_by_limit` (count) | `⚠️ Daily trade limit reached\nTrades today: {summary.trades_count}\nLimit: {config.daily_trade_limit}\nAction: no new signals until 00:00 (UTC-3)` |
| `on_signal_rejected_by_limit` (drawdown) | `⚠️ Daily drawdown limit reached\nDrawdown today: ${abs_pnl:.2f}\nLimit: ${config.daily_drawdown_pct}%\nAction: no new signals until 00:00 (UTC-3)` |
| `on_bot_started` | `🟢 Bot started\nMode: {mode}\nWatching: {watching}\nTimezone: {timezone}` |
| `on_bot_stopping` | `🔴 Bot stopping\nOpen cascades: {open_cascades}` |
| `on_parse_failure` | `⚠️ Skipped message (not a valid signal)\nReason: {reason.value}\nPreview: {raw_text[:80]}` |
| `on_telegram_disconnect` | `🔌 Telegram disconnected. Reconnecting…` |
| `on_olymp_disconnect` | `🔌 OlympTrade disconnected. Process will exit; supervisor will restart.` |

### 5.3 Private formatting helpers

Four small private methods on `TelegramDMNotifier`:

- `_fmt_hhmm(unix_ts: float) -> str` — calls `signal_copier.infra.clock.format_local_hhmm(ts, self._config.tz())` (new helper, ~4 lines; added to `infra/clock.py`). Returns e.g. `"10:25"`. The `(UTC-3)` suffix is appended by the caller template, so this helper returns only the time.
- `_fmt_pnl(decimal: Decimal) -> str` — `f"${decimal:+.2f}"`. E.g., `Decimal("1.84")` → `"+$1.84"`; `Decimal("-2.00")` → `"$-2.00"`.
- `_stage_label(stage: Stage) -> str` — `"initial"` → `"INITIAL"`, `"gale1"` → `"1st GALE"`, `"gale2"` → `"2nd GALE"`.
- `_stage_gale_unix(stage: Stage) -> float` — returns `self._signal.trigger_unix_initial + stage_index * self._signal.expiration_seconds`, where `stage_index` is `0` for initial, `1` for gale1, `2` for gale2. Used to compute the `Expires:` / `Next: scheduling ... at HH:MM` timestamps.

Duration helper (used in `on_cascade_complete`):
- `_duration_human(start_unix: float, end_unix: float) -> str` — `f"{int(delta // 60)}m{int(delta % 60):02d}s"`. E.g., 452 seconds → `"7m32s"`. `start_unix` is the signal's `received_at_unix`; `end_unix` is `now_unix()` at the time of the DM.

Direction mapping for `on_signal_received` and `on_trade_placed`:
- `Signal.direction == "up"` → `"CALL"`, `"down"` → `"PUT"` (matches the source signal text).

### 5.4 Method signatures (for the type checker)

These are the full Protocol signatures `TelegramDMNotifier` implements:

```python
async def on_signal_received(self, signal: Signal) -> None
async def on_trade_placed(self, signal: Signal, stage: Stage, amount: Decimal, trade_id: str) -> None
async def on_win(self, signal: Signal, stage: Stage, pnl: Decimal, cumulative_pnl: Decimal) -> None
async def on_loss(self, signal: Signal, stage: Stage, pnl: Decimal, cumulative_pnl: Decimal, next_stage: Stage | None) -> None
async def on_signal_expired(self, signal: Signal, stage: Stage, trigger_hhmm: str) -> None
async def on_cascade_complete(self, signal: Signal, final_state: TerminalState, cumulative_pnl: Decimal) -> None
async def on_signal_rejected_by_limit(self, signal: Signal, limit_type: str, summary: DailySummaryRow) -> None
async def on_bot_started(self, *, mode: str, watching: str, timezone: str) -> None
async def on_bot_stopping(self, *, open_cascades: int) -> None
async def on_parse_failure(self, raw_text: str, reason: FailureReason) -> None
async def on_telegram_disconnect(self) -> None
async def on_olymp_disconnect(self) -> None
```

The first seven and `on_parse_failure` are **positional-or-keyword** (matches the M6 Protocol exactly). `on_bot_started` and `on_bot_stopping` are **keyword-only** (also matches M6). The new `on_parse_failure` is positional-or-keyword to match the M5 listener's natural call site (`notifier.on_parse_failure(raw, reason)`).

## 6. `TelegramClient.send_to_self`

**File:** `src/signal_copier/telegram/client.py` (existing, +20 lines)

```python
async def send_to_self(self, text: str) -> None:
    """Send a Telegram DM to the user's own 'Saved Messages' chat.

    Uses the same connection as the listener (FR-7.4). Plain text
    only — no parse_mode. Telegram's 'me' is a fixed chat alias
    resolved client-side by the MTProto layer.

    Raises whatever Telethon's send_message raises (FloodWaitError,
    ConnectionError, OSError). Callers are responsible for handling.
    """
    if self._client is None:
        raise RuntimeError("send_to_self() called before connect()")
    await self._client.send_message("me", text)
```

**Why "me" and not a cached entity:** Telethon's `"me"` alias is resolved client-side on every call (special MTProto identifier). No need to cache `await client.get_entity("me")` at startup; that would add a `get_me` round-trip on every restart. The `client.send_message("me", text)` pattern is documented in Telethon's examples and works in all 1.44.x versions against user accounts.

**No `parse_mode` parameter:** plain text only — formatting decision in §5. Adding it later is one keyword arg.

**No rate-limiting at this layer:** Telethon handles `FloodWaitError` internally for short waits. The notifier's `_send` wrapper logs and swallows (per D-5). For a long `FloodWaitError` (>60s) on a DM, the warning is logged and the cascade continues without the DM — consistent with "DMs are best-effort".

## 7. `infra/log.py` loguru rewrite

**File:** `src/signal_copier/infra/log.py` (rewrite, ~90 lines, replaces the 46-line stdlib version).

### 7.1 `setup_logging(log_path: Path) -> None`

Three sinks:

1. **Stderr** (Railway live tail, `railway logs --tail`) — colored, INFO+.
2. **Rotating file** `logs/signal_copier.log` — 10 MB × 5, ZIP compression, UTF-8, INFO+. Uses `enqueue=True` for async writes so logging can't block the event loop.
3. **`_InterceptHandler`** — forwards stdlib `logging` records to loguru. Standard pattern from loguru's docs. Enables existing `logging.getLogger(__name__).info(...)` call sites to flow through the new infrastructure without any code change.

Format strings:

- Stderr: `<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> <level>{level: <8}</level> <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>` (colorized).
- File: `{time:YYYY-MM-DD HH:mm:ss.SSS} {level: <8} {name}:{function}:{line} - {message}` (no colors).

Idempotent: `_loguru_logger.remove()` is called first, so calling `setup_logging` twice (e.g., in a test) is safe.

### 7.2 `setup_parse_failures_log(log_dir: Path) -> logging.Logger`

Returns a stdlib `logging.Logger` named `"signal_copier.parse_failures"` whose `WARNING`+ records flow to a dedicated non-rotating loguru sink at `<log_dir>/parse_failures.log`. The listener's existing `parse_failures_logger.warning(...)` call site is unchanged.

Implementation: a dedicated loguru logger sink (no rotation — parse failures are rare, matches M5 contract) wrapped in a stdlib `logging.Handler` (`_ParseFailuresHandler`) so the listener's `parse_failures_logger.warning(...)` call works without edits.

### 7.3 DM mirror pattern

`TelegramDMNotifier._send` calls `_loguru_logger.bind(dm_event=True).info(text)` after a successful send. The `dm_event=True` bind tag enables future filtering (e.g., a quieter operational log in v2) via loguru's `filter=` predicate. v1 keeps everything.

### 7.4 Dependencies

Add `loguru>=0.7,<1.0` to `pyproject.toml`. Loguru is pure-Python, no platform wheels needed.

### 7.5 Impact on existing code

Every module's `logging.getLogger(__name__).info(...)` and `_log.exception(...)` calls flow through the `InterceptHandler` into loguru. **No existing code changes needed** for the loguru migration. Verified by running the existing M0–M6 test suite unchanged — every assertion about log output keeps passing because loguru preserves the message text verbatim.

## 8. Wiring in `__main__.py`, `Listener`, and `TelegramClient.start()`

### 8.1 `__main__.py` (~10 lines changed)

Replace the hardcoded `notifier = NoOpNotifier()` with:

```python
from signal_copier.notify.telegram_dm import TelegramDMNotifier

# ... inside _run(), after `await tg.connect()`:
if config.telegram_self_dm_notifications:
    notifier = TelegramDMNotifier(tg_client=tg, config=config)
    _log.info("Notifications: TelegramDMNotifier (self-DM enabled)")
else:
    notifier = NoOpNotifier()
    _log.info("Notifications: NoOpNotifier (self-DM disabled via TELEGRAM_SELF_DM_NOTIFICATIONS=false)")
```

The same `notifier` instance is passed to both the `Scheduler` (existing wiring) and the `Listener` (new wiring in §8.2). The `telegram_task = asyncio.create_task(tg.start(), ...)` line becomes `telegram_task = asyncio.create_task(tg.start(notifier=notifier), ...)`.

### 8.2 `telegram/listener.py` (~15 lines added)

Add `notifier: Notifier` parameter to `Listener.__init__`. In the parse-failure branches of `on_new_message` and `on_message_edited`:

```python
match parse_signal(raw, allowed_expirations=...):
    case ParseFailure(reason=reason):
        self._parse_failures_logger.warning(
            "parse failure: reason=%s preview=%r",
            reason.value, raw[:80],
        )
        await self._notifier.on_parse_failure(raw_text=raw, reason=reason)
        return
    # ... existing ParsedSignal handling ...
```

### 8.3 `telegram/client.py:start()` (~10 lines added)

```python
async def start(self, *, notifier: Notifier | None = None) -> None:
    # ... existing code ...
    except ConnectionError as exc:
        attempt += 1
        if attempt > _MAX_RECONNECT_ATTEMPTS:
            _log.error(...)
            raise
        delay = compute_backoff_seconds(attempt - 1)
        _log.warning(
            "Telegram ConnectionError: %s. Reconnect attempt %d/%d in %.1fs",
            type(exc).__name__, attempt, _MAX_RECONNECT_ATTEMPTS, delay,
        )
        if notifier is not None:
            await notifier.on_telegram_disconnect()
        await asyncio.sleep(delay)
```

`notifier=None` default keeps existing tests passing without modification.

### 8.4 `on_olymp_disconnect` emission — stub only

M8 (broker) and M10 (reconnect supervisor) own the emission wiring. M7 ships the Protocol method, the DM template, and a one-line `# TODO(M8): emit from OlympTradeBroker.reconnect_supervisor` comment in `notify/telegram_dm.py` near `on_olymp_disconnect` so the next milestone owner finds the seam.

## 9. Testing strategy

One new test file + extensions to six existing test files. Total new tests: ~35. No existing test gets deleted or weakened.

### 9.1 `tests/test_telegram_dm.py` (NEW, 25 tests, ~450 lines)

`FakeTelegramClient` wrapper (~25 lines) duck-types the subset of `TelegramClient` that `TelegramDMNotifier` calls. Holds `sent: list[str]` recorder and `raise_on_call: Exception | None` injection point.

Coverage matrix — one test per event × stage combination:

| Test | Asserts exact PRD string for |
|---|---|
| `test_signal_received` | signal_received template |
| `test_trade_placed_initial` | trade_placed initial stage |
| `test_trade_placed_gale1` | trade_placed gale1 stage |
| `test_trade_placed_gale2` | trade_placed gale2 stage |
| `test_win_initial` | win initial stage |
| `test_win_gale1` | win gale1 stage |
| `test_win_gale2` | win gale2 stage |
| `test_loss_initial_with_next_stage` | loss initial → gale1 |
| `test_loss_gale1_with_next_stage` | loss gale1 → gale2 |
| `test_loss_gale2_no_next_stage` | loss gale2 → cascade ends |
| `test_signal_expired_initial` | expired initial |
| `test_signal_expired_gale1` | expired gale1 |
| `test_signal_expired_gale2` | expired gale2 |
| `test_cascade_complete` | cascade complete template + duration |
| `test_rejected_by_loss_limit` | rejected by loss limit |
| `test_rejected_by_count_limit` | rejected by count limit |
| `test_rejected_by_drawdown_limit` | rejected by drawdown limit |
| `test_bot_started` | bot started template |
| `test_bot_stopping` | bot stopping template |
| `test_parse_failure` | parse failure template + 80-char preview |
| `test_telegram_disconnect` | telegram disconnect template |
| `test_olymp_disconnect` | olymp disconnect template |
| `test_satisfies_notifier_protocol` | `isinstance(_, Notifier)` returns True |
| `test_send_failure_logged_and_swallowed` | FakeTgClient raises → method returns, WARNING logged |

**Test fixtures use `tz=ZoneInfo("America/Sao_Paulo")` throughout**, with `trigger_unix_initial` set to a fixed UTC instant that maps to `"10:20"` in that TZ (e.g., `2026-06-21T13:20:00Z`). This avoids flaky tests under CI's UTC default.

### 9.2 `tests/test_notifier.py` (extend, +3 tests)

Add `test_noop_notifier_logs_parse_failure`, `test_noop_notifier_logs_telegram_disconnect`, `test_noop_notifier_logs_olymp_disconnect`. Same `caplog` pattern as the existing seven tests.

### 9.3 `tests/_scheduler_fixtures.py` (extend `RecordingNotifier`, +18 lines)

Add three matching `_record()` calls so M6 scheduler tests can verify the new events are never emitted from scheduler paths and so `isinstance(RecordingNotifier(), Notifier)` keeps returning True.

### 9.4 `tests/test_telegram_client.py` (extend, +2 tests)

- `test_send_to_self_calls_send_message_with_me` — patches underlying `_TelethonClient.send_message` with `AsyncMock`, calls `send_to_self("hello")`, asserts `send_message.assert_awaited_once_with("me", "hello")`.
- `test_send_to_self_raises_before_connect` — RuntimeError when called pre-`connect()`.
- Existing `start()` test gets a 3-line update to pass `notifier=None` and verify no behavior change.

### 9.5 `tests/test_telegram_listener.py` (extend, +2 tests)

- `test_listener_emits_on_parse_failure` — feed a malformed message; verify `RecordingNotifier.calls` contains the new `("on_parse_failure", {raw_text=..., reason=...})` tuple.
- `test_listener_does_not_emit_on_parse_failure_for_valid_signal` — feed a valid signal; verify no `on_parse_failure` call.

### 9.6 `tests/test_log.py` (rewrite, 4 tests, ~80 lines)

Replace the existing two stdlib tests (which become irrelevant) with:

- `test_setup_logging_writes_to_log_file` — setup → `logger.info("hello")` → file contains "hello".
- `test_setup_logging_writes_to_stderr` — capsys captures stderr.
- `test_intercept_handler_forwards_stdlib_log` — stdlib `logging.getLogger("x").info("y")` ends up in the same file.
- `test_setup_parse_failures_log_writes_to_separate_file` — parse-failure warning appears in `parse_failures.log` and NOT in `signal_copier.log`.

### 9.7 `tests/test_log_rotation.py` (NEW, slow marker, ~30 lines)

- `test_rotation_at_10mb` — synthesize 10 MB+ of output, assert ≥1 archive zip file exists. Marked `@pytest.mark.slow`; default `pytest` skips it.

### 9.8 `tests/_telegram_fixtures.py` (minor update, ~6 lines)

Extend `fake_listener(...)` factory to accept and pass a `notifier: Notifier = NoOpNotifier()` parameter. Existing tests that don't pass one get the no-op default and keep passing unchanged.

## 10. Data flow

### 10.1 Signal received → DM sent

```
TelegramChannel (admin posts signal)
    ↓ NewMessage event
Listener.on_new_message(event)
    ↓ parse_signal() succeeds
    ↓ Notifier.on_signal_received(signal)  [M7]
        ↓ TelegramDMNotifier._send(text)
            ↓ TelegramClient.send_to_self(text)
                ↓ underlying Telethon send_message("me", text)
                ↓ loguru.info(text)  [M7 mirror]
```

### 10.2 Broker rejects pair → DM sent (UnsupportedPairError path)

```
Scheduler._drive_cascade()
    ↓ broker.place() raises UnsupportedPairError
    ↓ _apply_error_transition()
        ↓ Notifier.on_cascade_complete(signal, final_state="error", cumulative_pnl=Decimal("0"))
            ↓ TelegramDMNotifier._send("🏁 Cascade complete: error\n...")
```

### 10.3 Connection blip → DM sent

```
TelegramClient.start() loop
    ↓ run_until_disconnected() raises ConnectionError
    ↓ log warning, compute backoff
    ↓ Notifier.on_telegram_disconnect()  [M7]
        ↓ TelegramDMNotifier._send("🔌 Telegram disconnected. Reconnecting…")
    ↓ asyncio.sleep(delay)
    ↓ loop continues
```

### 10.4 Loguru sink pipeline

```
stdlib logging.getLogger("signal_copier.scheduler").info(...)
    ↓ InterceptHandler.emit()
    ↓ loguru.opt(...).log(INFO, ...)
    ↓ stderr sink + rotating file sink

stdlib logging.getLogger("signal_copier.parse_failures").warning(...)
    ↓ ParseFailuresHandler.emit()
    ↓ loguru.bind(parse_failure=True).log(WARNING, ...)
    ↓ parse-failures file sink only (not the main rotating file)

TelegramDMNotifier._send(text)
    ↓ loguru.bind(dm_event=True).info(text)
    ↓ stderr sink + rotating file sink
```

## 11. Error handling

| Failure | Behavior |
|---|---|
| `TelegramDMNotifier._send` raises from `send_to_self` | Logged at WARNING with first 80 chars of text + exc. Method returns normally. The cascade continues. (D-5: notifier exceptions must not abort the cascade.) |
| `send_to_self` raises `FloodWaitError` (>60s) | Same as above — WARNING logged, DM dropped. Cascade continues. The reconnect loop in `TelegramClient.start()` handles its own FloodWaitError separately. |
| Loguru file sink fails to write (disk full) | Loguru logs to stderr instead; `enqueue=True` means writes don't block the event loop. |
| Parse failure raised in listener | Two paths: existing `parse_failures_logger.warning(...)` AND new `notifier.on_parse_failure(raw, reason)`. Both fire. The listener continues to the next message. |
| `TelegramClient.send_to_self` called before `connect()` | `RuntimeError`. Caught by `TelegramDMNotifier._send`'s broad `except Exception`. Logged at WARNING. |
| `Notifier.on_olymp_disconnect` called before M8 ships | `NoOpNotifier` logs at WARNING; `TelegramDMNotifier` sends a DM. No emission wiring exists yet, so the method is unreachable from v1 code paths. |

## 12. File summary

### 12.1 Files to add (3 new files)

| Path | Approx lines | Purpose |
|---|---|---|
| `src/signal_copier/notify/telegram_dm.py` | ~350 | `TelegramDMNotifier` class + 13 message templates |
| `tests/test_telegram_dm.py` | ~450 | 25 tests covering every FR-7.1 event |
| `tests/test_log_rotation.py` | ~30 | Slow marker; rotation behavior |

### 12.2 Files to modify (10 existing files)

| Path | Change | Lines added |
|---|---|---|
| `src/signal_copier/notify/protocol.py` | Add 3 methods to `Notifier` Protocol + matching `NoOpNotifier` methods | ~+50 |
| `src/signal_copier/infra/log.py` | Rewrite with loguru (stderr + rotating file + InterceptHandler + parse-failures sink) | ~+45 net |
| `src/signal_copier/infra/clock.py` | Add `format_local_hhmm(unix_ts, tz)` helper | ~+8 |
| `src/signal_copier/telegram/client.py` | Add `send_to_self()`; add optional `notifier` param to `start()` | ~+25 |
| `src/signal_copier/telegram/listener.py` | Add `notifier: Notifier` ctor param; emit `on_parse_failure` | ~+15 |
| `src/signal_copier/__main__.py` | Build `TelegramDMNotifier` when `telegram_self_dm_notifications=true`; pass to listener + scheduler; pass to `tg.start()` | ~+10 |
| `tests/test_notifier.py` | +3 tests for new NoOpNotifier methods | ~+30 |
| `tests/_scheduler_fixtures.py` | Extend `RecordingNotifier` with 3 new methods | ~+18 |
| `tests/test_telegram_client.py` | +2 tests for `send_to_self`; +1 minor update to `start()` test | ~+30 |
| `tests/test_telegram_listener.py` | +2 tests for `on_parse_failure` emission | ~+25 |
| `tests/test_log.py` | Rewrite 2 existing tests + add 4 new | ~+80 net |
| `tests/_telegram_fixtures.py` | Accept optional notifier in factory | ~+6 |
| `pyproject.toml` | Add `loguru>=0.7,<1.0` to dependencies | +1 |

**Total:** 13 files touched, ~1100 lines added (code + tests + comments). **No file deleted, no test weakened, no public API removed.**

## 13. Acceptance criteria

M7 is complete when **all** of the following hold:

1. **All tests pass:** `pytest` shows the existing M0–M6 test count plus the new M7 tests, zero failures. Existing tests pass without modification (only fixture extensions).
2. **Type-clean:** `mypy --strict src/signal_copier` exits 0.
3. **Lint-clean:** `ruff check .` exits 0.
4. **Protocol completeness:** `isinstance(TelegramDMNotifier(...), Notifier)` returns True. `isinstance(RecordingNotifier(), Notifier)` returns True. `isinstance(NoOpNotifier(), Notifier)` returns True. `isinstance(object(), Notifier)` returns False.
5. **End-to-end smoke (manual, Railway `DRY_RUN=true`):**
   - Start the bot → DM contains "🟢 Bot started / Mode: dry_run / Watching: @... / Timezone: America/Sao_Paulo".
   - Post a valid signal in the channel → DM contains "🟢 Signal received", then at trigger time "⏱️ Trade placed (INITIAL)", then 5 min later "❌ LOSS (INITIAL) / Next: scheduling 1st gale at HH:MM (UTC-3), $4.00", then "⏱️ Trade placed (1st GALE)", then "🏁 Cascade complete: done_loss / Total PnL: $-X.XX / Duration: XmXXs".
   - Post a malformed message → DM contains "⚠️ Skipped message (not a valid signal) / Reason: ... / Preview: ...".
6. **Log file verification:** `logs/signal_copier.log` contains the same DM text (plain, no formatting) at INFO, rotates at 10 MB, retains 5 files.
7. **FR-7.1 row coverage:** every row in the PRD's §4.7 FR-7.1 table has a corresponding `TelegramDMNotifier` method + `test_telegram_dm.py` test. Verified by reading the test file's parametrize IDs and cross-referencing the message templates in §5.2.
8. **Soak test:** a 24h soak with `DRY_RUN=true` against the real Telegram channel produces zero `DM send failed` log lines (i.e., the `log-and-swallow` path was never triggered). Failure rate of Telegram's `send_message` over 24h is the proxy for the test passing.

## 14. Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| `loguru.InterceptHandler` mangles stack traces from stdlib logging | Low | Use the exact pattern from loguru docs; existing `_log.exception(...)` calls preserve `exc_info`. |
| `client.send_message("me", ...)` raises in some Telethon version on user accounts | Low | Verified pattern (Telethon 1.44.x); pinned in pyproject. |
| HH:MM formatting differs across DST boundaries | Low | `America/Sao_Paulo` has been DST-free since 2019 per R-3. Tests use fixed TZ. |
| 10 MB rotation in tests slows CI | Medium | Rotation test marked `@pytest.mark.slow`; default `pytest` skip. |
| FloodWaitError during DM send blocks the cascade | Mitigated | Notifier's `_send` swallows; reconnect loop in `TelegramClient.start()` handles the long-wait case separately. |
| Migration of `infra/log.py` breaks a downstream test that asserts on logger configuration | Low | Existing tests assert on captured log messages (`caplog`), not on the handler list. `InterceptHandler` preserves message text verbatim. Verified by re-running M0–M6 tests on a draft. |
| `TELEGRAM_SELF_DM_NOTIFICATIONS=false` regression — someone disables it and forgets | Low | Default `True`; `__main__.py` logs which notifier is wired at startup. |

## 15. Out of scope

- **Real money integration.** No broker-related DMs are affected by real vs demo; both modes emit identical messages. FR-6.6's demo-only guardrail is unchanged.
- **Desktop notifications (S-3 in PRD).** Deferred to v2 per PRD §14.
- **Multi-channel DMs (different DMs per signal source).** Out of scope; v1 is single-channel.
- **Inline-keyboard confirmations (S-4 in PRD).** Deferred to v2.
- **Sound alerts, CSV export, daily summary DM.** Deferred.
- **`on_olymp_disconnect` emission wiring.** M8/M10 own this — M7 ships the method + DM template only.

---

*End of M7 design spec. Next step: user review, then writing-plans.*