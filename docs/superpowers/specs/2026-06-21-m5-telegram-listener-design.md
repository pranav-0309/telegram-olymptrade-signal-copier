# M5 — Telegram Listener Design

**Date:** 2026-06-21
**Status:** Draft (pending user review)
**PRD reference:** `docs/PRD.md` v0.7 (§4.1 FR-1.1–1.8, §4.2 FR-2.1–2.5, §4.7 FR-7.1, §6 Tech Stack, §7 Architecture, §12 Security, §15 M5 row, §17.7 first-deploy runbook)
**Build plan reference:** PRD §15, M5 row
**M4 spec reference:** `docs/superpowers/specs/2026-06-20-m4-database-infrastructure-design.md` (`StateStore.upsert_signal` is M5's primary DB write; M4 §6.3 sketches the M5 sequence diagram this spec implements)

---

## 1. Purpose & Scope

M5 is the sixth milestone of the Telegram → OlympTrade Signal Copier (PRD v0.7). It ships the **Telegram input side of the pipeline** — a Telethon-based user-account listener that watches one channel, parses incoming messages through the M1 parser, persists valid signals via the M4 `StateStore`, and puts them on an `asyncio.Queue` for the M6 scheduler to drain.

**M5 ships a self-contained, end-to-end-testable Telegram-to-stdout pipeline.** It does not yet place trades (M6 wires the scheduler; M8 wires the broker) and does not yet notify via Telegram DM (M7 owns the self-DM notifier). M5's "dumps to stdout" deliverable is implemented as a small `dump_consumer` coroutine that drains the queue and pretty-prints each signal — M6 will replace that consumer with the real scheduler.

**In scope for M5 (4 new files, 3 modified files, 3 new test files):**

| # | File | Type | Purpose |
|---|---|---|---|
| 1 | `src/signal_copier/infra/clock.py` | NEW | Pure tz/clock helpers: `hhmm_to_unix`, `signal_date_in_tz`, `is_within_window`, `now_unix`, `monotonic` |
| 2 | `src/signal_copier/telegram/client.py` | NEW | `TelegramClient` class — Telethon lifecycle, StringSession, reconnect backoff, FloodWaitError surfacing |
| 3 | `src/signal_copier/telegram/listener.py` | NEW | `Listener` class — `on_new_message` + `on_message_edited` handlers, signal builder glue, chat filter, parse-failure routing, time-window rejection |
| 4 | `src/signal_copier/telegram/auth.py` | NEW | `main()` entrypoint — interactive StringSession bootstrap (`python -m signal_copier.telegram.auth`) |
| 5 | `src/signal_copier/telegram/__init__.py` | NEW | Empty package marker (no re-exports — matches M4's `infra/__init__.py` convention) |
| 6 | `src/signal_copier/__main__.py` | MODIFY | Wire `Config` → `Database.connect` → `TelegramClient.connect` → `Listener(...)` → `dump_consumer` task → `TelegramClient.start()`; SIGINT/SIGTERM cleanup |
| 7 | `pyproject.toml` | MODIFY | Add `telethon>=1.44` to `dependencies`; add `signal-copier-auth` script; add 3 new test modules to mypy override |
| 8 | `src/signal_copier/infra/log.py` | MODIFY | Add `setup_parse_failures_log(path) -> logging.Logger` helper (stdlib `FileHandler`, deferred to loguru in M7) |
| 9 | `tests/test_clock.py` | NEW | ~10 unit tests for `infra/clock.py` (DST boundaries, time-window tolerances, epoch arithmetic) |
| 10 | `tests/test_telegram_listener.py` | NEW | ~13 unit tests for `Listener` using synthetic Telethon `NewMessage.Event` objects + `FakeStateStore` |
| 11 | `tests/test_telegram_client.py` | NEW | ~4 unit tests for the reconnect backoff math (no real Telethon objects) |
| 12 | `tests/_telegram_fixtures.py` | NEW | Shared test helpers: `make_event(...)`, `FakeStateStore`, `NullLogger` (mirrors M3's fake-broker pattern) |

**Out of scope (deferred to later milestones):**

| Concern | Lands in |
|---|---|
| Self-DM notifications on signal received / trade placed / etc. (FR-7.1) | M7 (`notify/telegram_dm.py`) |
| Real broker placement, push event handler, `wait_result` integration | M8 (`broker/olymp.py`) |
| Loguru setup with rotation, `logs/signal_copier.log` (FR-7.2) | M7 (M5 uses stdlib `logging` with plain `FileHandler` — no rotation; parse failures are rare and the file is small) |
| `asyncio.loop.call_at` scheduler draining the queue (M6) — replaces M5's `dump_consumer` | M6 (`scheduler/trigger.py`) |
| Restart-recovery via `state_store.get_active_signals()` | M10 |
| Daily-limit enforcement via `state_store.get_daily_summary(date)` | M6 |
| `SignalState.from_signal_row(row, config)` helper (M4 §6.5) | M6 |
| FloodWaitError circuit breaker + repeated-failure halts (PRD S-11) | v1.0 follow-on (M5 re-raises per FR-1.7; M11/S-5 adds the breaker) |
| Desktop notifications (FR-7.3) | v2 |
| Tighter scheduling precision (S-7) | v1.0 follow-on |

---

## 2. Resolved Decisions (M5-specific)

The PRD resolves all architectural questions (R-1 through R-15). The following are M5-specific scoping calls, confirmed during brainstorming on 2026-06-21.

| # | Decision | Rationale |
|---|---|---|
| D-1 | **asyncio.Queue + stub `dump_consumer` that prints to stdout (M5); M6 replaces the consumer with the scheduler** | Matches PRD §3's data-flow diagram (`signals_queue` is the contract between listener and scheduler). M5's end-to-end pipeline is self-contained and demoable without M6. The queue is **bounded** (`maxsize=1000`) as a safety net; M5's dump_consumer drains instantly so the cap is never hit. M6's scheduler will also drain at HH:MM rates (max ~1 signal per minute in practice). |
| D-2 | **Synthetic Telethon `NewMessage.Event` objects for tests; handler is a plain async function called directly** | M5's handler signatures `(event) -> None` are decoupled from the live Telethon client. Telethon registers via `client.on(NewMessage)(handler)` but the handler itself takes a Telethon event object whose attributes (`.text`, `.chat_id`, `.message.id`, `.message.out`, `.is_private`) are public and can be mocked. Tests call `listener.on_new_message(synthetic_event)` directly — fast, deterministic, no network, no test accounts. |
| D-3 | **Auth bootstrap is a separate subcommand: `python -m signal_copier.telegram.auth` (entry point `signal-copier-auth`)** | Listener stays non-interactive; auth is interactive. M5's `TelegramClient.connect()` refuses to run with an empty `StringSession` (raises `TelegramConfigError` with a hint). M11's PRD §17.7 runbook is unchanged: "run `python -m signal_copier.auth` locally, paste the StringSession into the env var." |
| D-4 | **`infra/clock.py` introduced as a new module in M5** (PRD §7's reserved slot) | M5 is the first place that needs `hhmm_to_unix` (signal builder), `signal_date_in_tz` (signal_id derivation), and `is_within_window` (FR-2.3 enforcement). Putting these in `telegram/listener.py` (private) would invert layering — M6's scheduler will need the same helpers and would either duplicate or import from `telegram/`, both wrong. M5 introduces the module; M6 extends it. |
| D-5 | **`Listener` is a class, not a free function** | The Listener holds stateful dependencies (`state_store`, `queue`, `config`, `parse_failures_logger`) that are all passed in once at construction. A free function would either re-pass them on every call (verbose) or use module-level globals (test-hostile). The class makes the dependencies explicit in `__init__` and lets tests inject fakes cleanly. M3 used the same pattern for `DryRunBroker` for the same reason. |
| D-6 | **Single handler body shared between `NewMessage` and `MessageEdited`** | Telethon's two event classes have identical attribute accessors (`.text`, `.chat_id`, `.message.id`, `.message.out`). A private `_process_message(text, chat_id, message_id, outgoing, received_at_unix)` method is the single source of truth; both public handlers are 1-line shims that call it. |
| D-7 | **Time-window rejection logs to `parse_failures.log` with reason `out_of_window`** | FR-2.3 lists the time-window check alongside the other parse-failure conditions. Routing through the same logger gives the user a single place to look for "messages I should have acted on but didn't" — semantically the same problem as a parse failure (signal arrived but isn't actionable). The log line includes `trigger_hhmm`, `trigger_unix`, and `now_unix` for diagnostics. |
| D-8 | **M5 uses stdlib `logging` (same as M3 D-6, M4 D-15); loguru arrives in M7** | Zero new dependency for M5's logging. M7's loguru setup will route stdlib `logging` through loguru's sinks and add rotation. M5's `setup_parse_failures_log` returns a `logging.Logger` configured with a plain `FileHandler` (no rotation); the file is small because parse failures are rare. |
| D-9 | **`StateStore.upsert_signal(signal)` is the only DB write M5 does** | M4 §6.3 explicitly designed for this. M5 does not write to `stages` (M6 owns the `record_stage_placed` lifecycle) and does not read `daily_summary` (M6 owns limit checks). M5's role is "new signal received → persist for M6 to pick up." |
| D-10 | **The chat filter uses `event.chat_id` vs. a pre-resolved target chat entity** | Telethon's `NewMessage.Event.chat_id` is the numeric ID of the chat the message arrived in. The `Config.telegram_target_chat` field is either `@username` or numeric; M5 resolves it once at startup via `client.get_entity(config.telegram_target_chat)` and caches the resulting `int` chat_id. M5 does NOT pre-resolve at config-parse time because the Telethon client must be connected for `get_entity` to work. The resolved chat_id is stored in `self._resolved_chat_id` on `TelegramClient` and exposed via a `target_chat_id` property for `Listener` to read. |
| D-11 | **Reconnect backoff: 1s → 2s → 4s → 8s → 16s → 30s (capped), max 10 attempts, then re-raise** | FR-1.8 specifies the cap at 30s. Max 10 attempts is not in FR-1.8 but is implicit (PRD §10 says "OlympTrade WS disconnect" exits non-zero so Railway restarts; the same pattern fits Telegram). After 10 attempts (~2 minutes total), re-raise `ConnectionError` so `__main__` exits non-zero and the Railway supervisor restarts the container. M11's runbook documents this. |
| D-12 | **`TelegramClient.connect()` validates required config and raises `TelegramConfigError` early** with a specific message naming the missing field | Catches misconfiguration at startup, not at first-message-arrival. Validates: `api_id != 0`, `api_hash != ""`, `phone != ""`, `session_string != ""` (last one is the M5 D-3 "no auto-prompt" contract). |
| D-13 | **No sender-allowlist in M5** (per R-14) | The Telegram channel is admin-only by platform design. The parser's strict regex (M1) is the sole defense. M5's `Listener` only filters by `chat_id` (D-10) and `event.message.out` (skip bot's own messages, defense). |
| D-14 | **Listener is filter-aware: `event.message.out=True` is silently ignored** | Defense-in-depth: if a future M5 change ever adds a "post to channel" feature, bot messages wouldn't be parsed as signals. Telethon fires `NewMessage` for both incoming and outgoing; the filter is one line. |
| D-15 | **`parse_failures.log` lives at `logs/parse_failures.log` (relative to `Config.log_path.parent`)** | Consistent with the directory the FR-7.2 main log will use (M7). `Config.log_path` is `./logs/signal_copier.log`; M5 derives `parse_failures.log` from the same `logs/` directory. |
| D-16 | **Test helpers (`make_event`, `FakeStateStore`, `NullLogger`) live in `tests/_telegram_fixtures.py`** (separate from `tests/conftest.py`) | M3's fake-broker pattern lives directly in `test_broker_protocol.py` because it's only used by one test file. M5's helpers are used by `test_telegram_listener.py` and (potentially) future M6/M7 tests; a shared module is appropriate. `tests/_telegram_fixtures.py` is named with a leading underscore so pytest's `testpaths = ["tests"]` plus default test discovery still picks up `test_*.py` files cleanly (conftest.py auto-loads, `_*.py` does not interfere with discovery). |
| D-17 | **The dump_consumer lives in `__main__.py` as a local async function** (not a module-level helper) | It's a 5-line `while True: signal = await queue.get(); print(signal)` loop. M6 will replace it with a scheduler consumer. Promoting it to a module would be over-engineering. |
| D-18 | **M5 does NOT use `target_chat` resolution as a re-try-able operation; if the chat can't be resolved at startup, the listener fails to start** | Same rationale as D-12 — fail fast on misconfiguration. The error message names the unresolved chat string and hints at "check `TELEGRAM_TARGET_CHAT`." |

---

## 3. Repository Layout (post-M5)

```
olymptrade/
├── pyproject.toml                          # MODIFY: +telethon, +signal-copier-auth script, +3 mypy overrides
├── migrations/                             # (unchanged from M4)
├── src/
│   ├── olymptrade_ws/                      # (unchanged, vendored)
│   └── signal_copier/
│       ├── __init__.py                     # (unchanged)
│       ├── __main__.py                     # MODIFY: full M5 wiring
│       ├── config.py                       # (unchanged from M2)
│       ├── broker/                         # (unchanged from M3)
│       ├── domain/                         # (unchanged from M4)
│       ├── infra/                          # MODIFY
│       │   ├── __init__.py                 # (unchanged)
│       │   ├── log.py                      # MODIFY: +setup_parse_failures_log helper
│       │   ├── db.py                       # (unchanged from M4)
│       │   ├── db_rows.py                  # (unchanged from M4)
│       │   ├── state_store.py              # (unchanged from M4)
│       │   └── clock.py                    # NEW: 5 tz/clock helpers
│       └── telegram/                       # NEW package
│           ├── __init__.py                 # NEW: empty
│           ├── client.py                   # NEW: TelegramClient + TelegramConfigError
│           ├── listener.py                 # NEW: Listener class
│           └── auth.py                     # NEW: main() entrypoint
└── tests/
    ├── _telegram_fixtures.py               # NEW: make_event, FakeStateStore, NullLogger
    ├── test_clock.py                       # NEW: ~10 tests
    ├── test_telegram_listener.py           # NEW: ~13 tests
    ├── test_telegram_client.py             # NEW: ~4 tests
    ├── conftest.py                         # (unchanged from M4)
    ├── test_db.py                          # (unchanged from M4)
    ├── test_broker_protocol.py             # (unchanged from M3)
    ├── test_dry_run_broker.py              # (unchanged from M3)
    ├── test_main.py                        # (unchanged from M2)
    ├── test_parser.py                      # (unchanged from M1)
    ├── test_gale_math.py                   # (unchanged from M2)
    ├── test_state_machine.py               # (unchanged from M2)
    └── test_config.py                      # (unchanged from M2)
```

**Notable choices:**

- `infra/clock.py` is added under the existing `infra/` package (alongside `log.py`, `db.py`, etc.). It's a pure-function module with no I/O, no async, no Telethon dependency — easily unit-testable.
- `telegram/` is a new top-level package. `__init__.py` is empty (no re-exports) per the M4 convention.
- `telegram/auth.py` is small (~30 lines) and lives in the `telegram/` package because it owns the same Telethon setup. Splitting it into a `cli/` package would be over-engineering for one entrypoint.
- `tests/_telegram_fixtures.py` is a helper module (not a `conftest.py`) because it provides named imports (`make_event`, `FakeStateStore`), not pytest fixtures. The leading underscore keeps it out of pytest's automatic test-collection.

---

## 4. Key File Contents

### 4.1 `src/signal_copier/infra/clock.py` (NEW)

```python
from __future__ import annotations

import time
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo


def hhmm_to_unix(hhmm: str, on_date: date, tz: ZoneInfo) -> float:
    """Convert an 'HH:MM' string + date in `tz` to a Unix epoch (float seconds).

    The result is the Unix timestamp for HH:MM:00 in `tz` on `on_date`. Used
    by the M5 listener to build a Signal's `trigger_unix_initial` field.
    M6's scheduler uses the same helper for `trigger_unix_gale1/gale2`
    (though those are arithmetic, not timezone-aware).

    Examples (America/Sao_Paulo, UTC-3 year-round):
        hhmm_to_unix("10:20", date(2026, 6, 20), tz) -> 1782015600.0
    """
    hour, minute = (int(x) for x in hhmm.split(":"))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"invalid HH:MM: {hhmm!r}")
    local_dt = datetime(
        on_date.year, on_date.month, on_date.day, hour, minute, tzinfo=tz,
    )
    return local_dt.timestamp()


def signal_date_in_tz(unix_ts: float, tz: ZoneInfo) -> date:
    """Return the date (in `tz`) that `unix_ts` falls on.

    Used by the M5 listener to pick the `signal_date` argument for
    `derive_signal_id()` — the date MUST be in the analyst's configured
    timezone, not UTC, so that two identical signals arriving just before
    and just after local midnight collapse to the same signal_id.
    """
    return datetime.fromtimestamp(unix_ts, tz=tz).date()


def is_within_window(
    trigger_unix: float,
    now_unix: float,
    *,
    past_tolerance: float = 60.0,
    future_tolerance: float = 1800.0,
) -> bool:
    """True if `trigger_unix` is within `[now - past_tolerance, now + future_tolerance]`.

    Enforces FR-2.3: 'time more than 1 minute in the past or more than
    30 minutes in the future' must be rejected. Defaults: 60s past,
    1800s future.
    """
    return (now_unix - past_tolerance) <= trigger_unix <= (now_unix + future_tolerance)


def now_unix() -> float:
    """Return the current wall-clock Unix time as a float.

    Thin wrapper around `time.time()` so future test mocking (e.g., a
    pytest fixture that patches this in `tests/conftest.py`) has one
    place to land. M5's `Listener` calls this in the handler to get
    `received_at_unix` and the `is_within_window` reference.
    """
    return time.time()


def monotonic() -> float:
    """Return a monotonic clock reading (seconds, float).

    No use in M5. Reserved for M6's scheduler (where `call_at` is
    monotonic-anchored per FR-3.4). Keeping the helper here now means
    M6 doesn't need a second round of "where do the clock helpers live?"
    """
    return time.monotonic()
```

**Notes:**

- All 5 functions are pure (no I/O, no async). `now_unix()` and `monotonic()` are exceptions in that they read the clock, but they take no arguments and have no side effects.
- `hhmm_to_unix` uses `datetime(...).timestamp()` which does the right thing across DST (returns the correct epoch for the local time as it would be interpreted in `tz`). DST-edge tests in `test_clock.py` cover the spring-forward case.
- `is_within_window` uses inclusive boundaries (`<=`, `>=`) so that exactly 60s past or exactly 30min future is still accepted. FR-2.3 says "more than 1 minute in the past" — "more than" implies strict, so the inclusive boundary is correct (at 60s, not yet "more than 1 minute").
- The `*` after `now_unix` in the signature forces `past_tolerance` and `future_tolerance` to be keyword-only, preventing accidental positional misuse.

### 4.2 `src/signal_copier/telegram/client.py` (NEW)

```python
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar

from telethon import TelegramClient as _TelethonClient
from telethon.errors import FloodWaitError
from telethon.events import MessageEdited, NewMessage
from telethon.sessions import StringSession

_log = logging.getLogger(__name__)


_MAX_RECONNECT_ATTEMPTS: ClassVar[int] = 10
_BACKOFF_BASE_SECONDS: ClassVar[float] = 1.0
_BACKOFF_CAP_SECONDS: ClassVar[float] = 30.0
_FLOOD_WAIT_THRESHOLD_SECONDS: ClassVar[int] = 60


class TelegramConfigError(RuntimeError):
    """Raised by TelegramClient.connect() when required config is missing or invalid.

    Caught by __main__. Prints an actionable error message naming the
    missing field, exits 2. Distinct from network errors which exit 1.
    """


def compute_backoff_seconds(attempt: int) -> float:
    """Exponential backoff with a 30s cap. attempt is 0-indexed.

    attempt=0 -> 1.0, attempt=1 -> 2.0, ..., attempt=4 -> 16.0,
    attempt>=5 -> 30.0 (capped).
    """
    return min(_BACKOFF_BASE_SECONDS * (2 ** attempt), _BACKOFF_CAP_SECONDS)


class TelegramClient:
    """Thin wrapper over the vendored Telethon client.

    Owns the StringSession lifecycle, the reconnect supervisor, and the
    FloodWaitError policy. Handler registration is pass-through to
    Telethon (we add nothing on top). Closing is idempotent.

    All async methods are coroutines; construction is sync (config only).
    """

    def __init__(
        self,
        *,
        api_id: int,
        api_hash: str,
        phone: str,
        session_string: str,
        target_chat: str,
    ) -> None:
        # D-12: validate required config eagerly so a misconfigured
        # .env fails at startup with a clear message, not at first
        # message arrival.
        if api_id == 0:
            raise TelegramConfigError(
                "TELEGRAM_API_ID is 0; set it in .env (get from my.telegram.org)"
            )
        if not api_hash:
            raise TelegramConfigError("TELEGRAM_API_HASH is empty; set it in .env")
        if not phone:
            raise TelegramConfigError("TELEGRAM_PHONE is empty; set it in .env")
        if not session_string:
            raise TelegramConfigError(
                "TELEGRAM_SESSION_STRING is empty; run "
                "'python -m signal_copier.telegram.auth' to generate one"
            )

        self._api_id = api_id
        self._api_hash = api_hash
        self._phone = phone
        self._target_chat = target_chat
        self._session_string = session_string

        self._client: _TelethonClient | None = None
        self._target_chat_id: int | None = None  # resolved in connect()

    @property
    def target_chat_id(self) -> int:
        """The resolved numeric chat_id. Available after connect()."""
        if self._target_chat_id is None:
            raise RuntimeError(
                "target_chat_id is not resolved; call TelegramClient.connect() first"
            )
        return self._target_chat_id

    async def connect(self) -> None:
        """Build the Telethon client, connect to Telegram, resolve the target chat.

        Does NOT prompt for code/2FA — the StringSession must be pre-
        generated by `python -m signal_copier.telegram.auth`. M5 does
        not auto-prompt (D-3).
        """
        self._client = _TelethonClient(
            StringSession(self._session_string),
            self._api_id,
            self._api_hash,
        )
        await self._client.connect()
        # D-18: resolve target chat at startup. Failure here is a
        # misconfiguration (wrong @username, numeric chat_id that
        # the account isn't a member of, etc.) — fail fast.
        try:
            entity = await self._client.get_entity(self._target_chat)
        except Exception as exc:
            raise TelegramConfigError(
                f"Cannot resolve TELEGRAM_TARGET_CHAT={self._target_chat!r}: "
                f"{type(exc).__name__}: {exc}. Check the value in .env."
            ) from exc
        self._target_chat_id = entity.id
        _log.info(
            "TelegramClient connected (target_chat=%r -> chat_id=%d)",
            self._target_chat, self._target_chat_id,
        )

    def add_message_handler(
        self,
        handler: Callable[[Any], Awaitable[None]],
    ) -> None:
        """Register `handler` for both NewMessage and MessageEdited events.

        D-6: a single handler body covers both event types. Telethon's
        events module is imported only here, not at module top level, to
        keep the dep lazy (M5 tests don't need it).
        """
        if self._client is None:
            raise RuntimeError(
                "add_message_handler called before connect(); call TelegramClient.connect() first"
            )
        self._client.on(NewMessage)(handler)
        self._client.on(MessageEdited)(handler)

    async def start(self) -> None:
        """Run the Telethon client until disconnect, with reconnect supervision.

        D-11: exponential backoff 1s->2s->...->30s cap, max 10 attempts.
        After the cap, re-raise so __main__ exits non-zero and the
        Railway supervisor restarts the container.
        """
        if self._client is None:
            raise RuntimeError("start() called before connect()")
        attempt = 0
        while True:
            try:
                await self._client.run_until_disconnected()
                # Clean disconnect (no exception). Exit normally.
                return
            except FloodWaitError as exc:
                # FR-1.7: Telethon auto-handles <=60s. For longer waits,
                # we get here; log and re-raise per FR-1.7 'raise + log
                # for longer'. M11/S-5 will add a circuit breaker.
                if exc.seconds > _FLOOD_WAIT_THRESHOLD_SECONDS:
                    _log.error(
                        "Telegram FloodWaitError: %ds wait requested; re-raising "
                        "(FR-1.7: 'raise + log for longer')",
                        exc.seconds,
                    )
                    raise
                # <=60s — Telethon should have handled it; this branch
                # is defensive (the library sometimes still surfaces it).
                _log.warning("FloodWaitError %ds; continuing", exc.seconds)
                continue
            except ConnectionError as exc:
                # FR-1.8: reconnect with backoff.
                attempt += 1
                if attempt > _MAX_RECONNECT_ATTEMPTS:
                    _log.error(
                        "Telegram reconnect failed after %d attempts; re-raising",
                        _MAX_RECONNECT_ATTEMPTS,
                    )
                    raise
                delay = compute_backoff_seconds(attempt - 1)
                _log.warning(
                    "Telegram ConnectionError: %s. Reconnect attempt %d/%d "
                    "in %.1fs",
                    type(exc).__name__, attempt, _MAX_RECONNECT_ATTEMPTS, delay,
                )
                await asyncio.sleep(delay)

    async def close(self) -> None:
        """Disconnect the Telethon client. Idempotent."""
        if self._client is None:
            return
        try:
            await self._client.disconnect()
        except Exception as exc:  # noqa: BLE001 — close is best-effort
            _log.debug("TelegramClient.close: disconnect raised: %s", exc)
        self._client = None
        self._target_chat_id = None
```

**Notes:**

- The vendored `_TelethonClient` is imported under an alias to avoid name collision with our wrapper class. Imports of `telethon.events` are inside methods to keep the dependency lazy for tests.
- `_FLOOD_WAIT_THRESHOLD_SECONDS = 60` matches Telethon's default `flood_sleep_threshold`. The defensive `if exc.seconds > _FLOOD_WAIT_THRESHOLD_SECONDS` branch is documented in the code as a defense — in practice Telethon handles it before we see it.
- `compute_backoff_seconds` is a module-level function (not a method) so it can be unit-tested directly without constructing a `TelegramClient`. M5's `test_telegram_client.py` exercises it.
- `add_message_handler` accepts a `Callable[[Any], Awaitable[None]]` rather than a strict `NewMessage.Event` type because the handler signature is the same for both event types. Tests pass synthetic events; Telethon passes real ones.
- `close()` is idempotent (matches M3's `DryRunBroker.close()` and M4's `Database.close()` pattern). A misbehaving shutdown handler may call it twice; we silently no-op on the second call.

### 4.3 `src/signal_copier/telegram/listener.py` (NEW)

```python
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict
from typing import Any

from signal_copier.config import Config
from signal_copier.domain.signal import (
    FailureReason,
    ParseFailure,
    Signal,
    derive_signal_id,
    parse_signal,
)
from signal_copier.infra.clock import (
    hhmm_to_unix,
    is_within_window,
    now_unix,
    signal_date_in_tz,
)
from signal_copier.infra.state_store import StateStore

_log = logging.getLogger(__name__)


# The allowed set of expiration values (in seconds) that parse_signal will
# accept. Today only 300 (5 min) — M5 reads the single config value but
# the parser takes a set so a future multi-expiration change is one-line.
def _allowed_expirations(config: Config) -> frozenset[int]:
    return frozenset({config.expiration_seconds})


class Listener:
    """Wires Telethon NewMessage/MessageEdited events into the M1 parser + M4 StateStore.

    The Listener is filter-aware (D-13, D-14): only `chat_id == target` and
    non-outgoing messages are processed. A successful parse goes through
    the M1 parser, the M5 time-window check, the M1 `derive_signal_id`
    helper, M4's `state_store.upsert_signal(signal)`, and finally lands
    on the asyncio.Queue for M6 (or M5's `dump_consumer`) to drain.

    Construction is sync (config + dependencies). `on_new_message` and
    `on_message_edited` are the public handlers; both are 1-line shims
    over the private `_process_message` (D-6).
    """

    def __init__(
        self,
        *,
        target_chat_id: int,
        state_store: StateStore,
        queue: asyncio.Queue[Signal],
        config: Config,
        parse_failures_logger: logging.Logger,
    ) -> None:
        self._target_chat_id = target_chat_id
        self._state_store = state_store
        self._queue = queue
        self._config = config
        self._parse_failures_logger = parse_failures_logger
        self._allowed_expirations = _allowed_expirations(config)

    async def on_new_message(self, event: Any) -> None:
        """Telethon NewMessage handler. Public entry point."""
        await self._process_message(event)

    async def on_message_edited(self, event: Any) -> None:
        """Telethon MessageEdited handler. Public entry point."""
        await self._process_message(event)

    async def _process_message(self, event: Any) -> None:
        """Single source of truth for both NewMessage and MessageEdited (D-6)."""
        # D-14: skip bot's own outgoing messages
        if event.message.out:
            return
        # D-13: chat filter (the ONLY filter — no sender allowlist per R-14)
        if event.chat_id != self._target_chat_id:
            return

        text: str = event.text or ""
        if not text.strip():
            # Empty or whitespace-only; ignore silently.
            return

        source_message_id: int = event.message.id
        source_chat_id: int = event.chat_id
        received_at_unix: float = now_unix()

        # Step 1: parse
        result = parse_signal(text, allowed_expirations=self._allowed_expirations)
        if isinstance(result, ParseFailure):
            self._log_parse_failure(result, text, source_message_id)
            return

        # Step 2: compute trigger times + signal_id
        tz = self._config.tz()
        signal_date = signal_date_in_tz(received_at_unix, tz)
        trigger_unix_initial = hhmm_to_unix(
            result.trigger_hhmm, signal_date, tz,
        )
        trigger_unix_gale1 = trigger_unix_initial + result.expiration_seconds
        trigger_unix_gale2 = trigger_unix_initial + 2 * result.expiration_seconds

        # Step 3: time-window check (FR-2.3; D-7 logs to parse_failures)
        if not is_within_window(trigger_unix_initial, received_at_unix):
            self._log_out_of_window(
                result.trigger_hhmm, trigger_unix_initial, received_at_unix,
                source_message_id,
            )
            return

        # Step 4: build the full Signal dataclass (M1 §4.2 FR-2.5)
        signal_id = derive_signal_id(result, signal_date=signal_date)
        signal = Signal(
            signal_id=signal_id,
            pair=result.pair,
            direction=result.direction,
            trigger_hhmm=result.trigger_hhmm,
            expiration_seconds=result.expiration_seconds,
            received_at_unix=received_at_unix,
            source_message_id=source_message_id,
            source_chat_id=source_chat_id,
            raw_text=text,
            trigger_unix_initial=trigger_unix_initial,
            trigger_unix_gale1=trigger_unix_gale1,
            trigger_unix_gale2=trigger_unix_gale2,
        )

        # Step 5: persist (M4 D-8 returns True=new, False=duplicate)
        inserted = await self._state_store.upsert_signal(signal)
        if not inserted:
            _log.info(
                "duplicate signal, ignoring: signal_id=%s pair=%s trigger=%s",
                signal.signal_id, signal.pair, signal.trigger_hhmm,
            )
            return

        # Step 6: enqueue for the consumer (M5 dump_consumer; M6 scheduler)
        await self._queue.put(signal)

        # Step 7: pretty-print to stdout (M5 deliverable per PRD §15)
        print(json.dumps(asdict(signal), indent=2, default=str))

    def _log_parse_failure(
        self, failure: ParseFailure, text: str, source_message_id: int,
    ) -> None:
        preview = text[:80].replace("\n", " ")
        self._parse_failures_logger.warning(
            "parse_failure: reason=%s message_id=%s preview=%r",
            failure.reason.value, source_message_id, preview,
        )

    def _log_out_of_window(
        self,
        trigger_hhmm: str,
        trigger_unix: float,
        now_unix_val: float,
        source_message_id: int,
    ) -> None:
        self._parse_failures_logger.warning(
            "parse_failure: reason=out_of_window message_id=%s trigger_hhmm=%s "
            "trigger_unix=%.3f now_unix=%.3f skew_sec=%.1f",
            source_message_id, trigger_hhmm, trigger_unix, now_unix_val,
            now_unix_val - trigger_unix,
        )
```

**Notes:**

- `_process_message` is the only place the listener does work; the two public handlers are shims. D-6.
- The order of operations matches the data-flow diagram from the brainstorming session: chat filter → empty check → parse → time math → time-window check → build Signal → upsert → enqueue → print.
- `asdict(signal)` converts the frozen dataclass to a dict; `json.dumps(..., default=str)` handles `Decimal` (from M2's gale math) and `datetime` (none in M5, but defensive). The default for `Decimal` and `datetime` falls back to `str(...)` which is what M7's loguru formatter will do too.
- The print is **after** the enqueue — a stdout IOError should not block the queue. (In practice stdout is a TTY or a pipe; this is a defensive ordering choice.)
- `_log_parse_failure` uses `failure.reason.value` because `FailureReason` is a `StrEnum` — `.value` is the string literal ("missing_signal_line", etc.) per M1's `domain/signal.py:14–20`.
- `event.text` is `None` for some Telethon event types (e.g., service messages); the `or ""` handles that. `event.message.id` and `event.chat_id` are always present on both `NewMessage.Event` and `MessageEdited.Event` per Telethon's API.

### 4.4 `src/signal_copier/telegram/auth.py` (NEW)

```python
from __future__ import annotations

import asyncio
import sys
from typing import NoReturn

from telethon import TelegramClient as _TelethonClient
from telethon.sessions import StringSession

from signal_copier.config import Config
from signal_copier.telegram.client import TelegramConfigError

# Interactive auth has no bound — the user may take minutes to enter
# the SMS code. We use a generous default.
_AUTH_TIMEOUT_SECONDS: int = 300


def _read_creds() -> tuple[int, str, str]:
    """Read API_ID / API_HASH / PHONE from .env via the Config validator.

    The Config class already has pydantic validators for these; we
    re-use them. If the env vars are missing, Config() raises
    ValidationError which we re-raise as a friendly error.
    """
    try:
        config = Config()
    except Exception as exc:
        sys.stderr.write(
            f"❌ Config validation failed; check API_ID / API_HASH / PHONE in .env:\n"
            f"{exc}\n"
        )
        sys.exit(2)
    return config.telegram_api_id, config.telegram_api_hash, config.telegram_phone


async def _do_auth(api_id: int, api_hash: str, phone: str) -> str:
    """Run the Telethon interactive auth flow, return the StringSession string."""
    # Pass empty session_string to auth.py-style flow: Telethon prompts
    # for phone -> code -> 2FA as needed.
    client = _TelethonClient(StringSession(), api_id, api_hash)
    await client.start(phone=phone)  # interactive: prompts for code + 2FA
    session_str = client.session.save()
    await client.disconnect()
    return session_str


def main() -> int:
    """Entry point for `python -m signal_copier.telegram.auth`.

    Reads credentials from .env, runs the Telethon interactive auth
    flow, prints the resulting StringSession to stdout with a clear
    header. Exits 0 on success, 1 on auth failure, 2 on config error.
    """
    api_id, api_hash, phone = _read_creds()
    if api_id == 0 or not api_hash or not phone:
        sys.stderr.write(
            "❌ TELEGRAM_API_ID / TELEGRAM_API_HASH / TELEGRAM_PHONE must be set in .env\n"
            "   Get API_ID and API_HASH from https://my.telegram.org\n"
        )
        return 2

    try:
        session_str = asyncio.run(
            asyncio.wait_for(
                _do_auth(api_id, api_hash, phone),
                timeout=_AUTH_TIMEOUT_SECONDS,
            )
        )
    except TelegramConfigError as exc:
        sys.stderr.write(f"❌ {exc}\n")
        return 2
    except asyncio.TimeoutError:
        sys.stderr.write(
            f"❌ Auth timed out after {_AUTH_TIMEOUT_SECONDS}s; run again and "
            "respond to the prompts more quickly.\n"
        )
        return 1
    except Exception as exc:
        sys.stderr.write(f"❌ Telegram auth failed: {type(exc).__name__}: {exc}\n")
        return 1

    # Success: print a copy-paste-ready env var line.
    print("# --- Telegram session ---")
    print(f"# Generated by `python -m signal_copier.telegram.auth`")
    print(f"TELEGRAM_SESSION_STRING={session_str}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

**Notes:**

- The function is intentionally short. The bulk of the work is delegated to Telethon's `client.start(phone=phone)` which handles the phone → code → 2FA prompts natively.
- `_AUTH_TIMEOUT_SECONDS = 300` is a safety net for users who walk away from the keyboard. 5 minutes is generous; if the user needs more, they can re-run.
- The success print format (`# comment` + `TELEGRAM_SESSION_STRING=...`) makes the output directly paste-able into a `.env` file or a Railway Variables dashboard.
- The `if __name__ == "__main__"` guard lets the file also be runnable as a script: `python src/signal_copier/telegram/auth.py`. The pyproject.toml entry point (`signal-copier-auth`) calls `main()` directly, not via `python -m`, so the guard is necessary for both paths.

### 4.5 `src/signal_copier/telegram/__init__.py` (NEW)

```python
# Empty. Callers import from submodules:
#   from signal_copier.telegram.client import TelegramClient, TelegramConfigError
#   from signal_copier.telegram.listener import Listener
#   from signal_copier.telegram.auth import main
#
# No top-level re-exports — the package is a namespace, not a facade.
# Matches the M4 convention in src/signal_copier/infra/__init__.py.
```

### 4.6 `src/signal_copier/infra/log.py` (MODIFY)

```python
from __future__ import annotations

import logging
from pathlib import Path

# M5 rotates to the `loguru` setup planned for M7. Until then, stdlib
# `logging` is the project-wide standard (M3 D-6, M4 D-15).


def setup_logging(log_path: Path) -> None:
    """Configure the root logger with a stderr handler at INFO level.

    M5 keeps the M2 stub. M7 replaces this with a loguru setup that
    adds rotation, file sinks, and the FR-7.1 DM-mirroring handler.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    _ = log_path  # unused until M7


def setup_parse_failures_log(log_dir: Path) -> logging.Logger:
    """Configure a dedicated logger for parse failures.

    Writes WARNING+ records to `<log_dir>/parse_failures.log` (D-15:
    `logs/parse_failures.log` by default). The returned logger is
    passed to the Listener constructor; tests inject a NullLogger
    (in `tests/_telegram_fixtures.py`).

    M5 uses a plain FileHandler (no rotation) because parse failures
    are rare; M7's loguru setup will add rotation along with the
    main log file.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "parse_failures.log"

    logger = logging.getLogger("signal_copier.parse_failures")
    logger.setLevel(logging.WARNING)
    # Idempotent: if called twice (e.g., in tests), don't double-add.
    if not any(
        isinstance(h, logging.FileHandler) and h.baseFilename == str(log_path)
        for h in logger.handlers
    ):
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-8s %(message)s",
            )
        )
        logger.addHandler(handler)
    # Don't propagate to root — parse failures are a separate stream.
    logger.propagate = False
    return logger
```

**Notes:**

- `setup_logging` keeps the M2 stub signature (no caller changes) but configures the root logger with a stderr handler at INFO. This is the smallest change that makes M5's logs visible.
- `setup_parse_failures_log` returns a named logger (`signal_copier.parse_failures`) with `propagate=False` so parse-failure records don't appear in stderr. Tests inject a `NullLogger` (a `logging.Logger` subclass that swallows records) so test runs don't pollute `logs/`.
- The idempotency check on the FileHandler is defensive: if `setup_parse_failures_log` is called twice (e.g., across test boundaries), the second call doesn't stack two handlers writing to the same file.

### 4.7 `src/signal_copier/__main__.py` (MODIFY)

```python
from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict

from pydantic import ValidationError

from signal_copier.config import Config
from signal_copier.infra.db import Database, DatabaseConnectionError
from signal_copier.infra.log import setup_logging, setup_parse_failures_log
from signal_copier.telegram.client import TelegramClient, TelegramConfigError
from signal_copier.telegram.listener import Listener

# Bounded as a safety net (D-1). M5's dump_consumer drains instantly;
# M6's scheduler drains at ~1 signal/min so the cap is never hit.
_SIGNALS_QUEUE_MAXSIZE: int = 1000


def _build_dump_consumer(
    queue: asyncio.Queue,
) -> "asyncio.Task[None]":
    """Return an asyncio Task that drains `queue` and pretty-prints each Signal.

    D-17: lives in __main__ as a local helper. M6 will replace this
    body with the scheduler (or delete it entirely when M6 owns the
    consumer).
    """
    async def _consume() -> None:
        while True:
            signal = await queue.get()
            try:
                print(json.dumps(asdict(signal), indent=2, default=str))
            finally:
                queue.task_done()

    return asyncio.create_task(_consume(), name="dump_consumer")


async def _run(config: Config) -> int:
    """Async main: wire up the pipeline and run until cancelled or fatal error."""
    db: Database | None = None
    tg: TelegramClient | None = None
    dump_task: asyncio.Task[None] | None = None
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

        dump_task = _build_dump_consumer(signals_queue)

        print(
            f"🟢 signal_copier M5 started\n"
            f"   Mode: {'dry_run' if config.dry_run else 'live demo'}\n"
            f"   Timezone: {config.timezone}\n"
            f"   Target chat: {config.telegram_target_chat} (chat_id={tg.target_chat_id})\n"
            f"   Watching for new messages and edits...\n"
        )

        await tg.start()  # blocks until disconnect or re-raise
        return 0
    finally:
        if dump_task is not None:
            dump_task.cancel()
            try:
                await dump_task
            except (asyncio.CancelledError, Exception):
                pass
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

- The async main is a separate `_run` function so `asyncio.run()` can drive it cleanly. `main()` (sync) handles config loading, error mapping, and the asyncio entrypoint.
- Exit codes: 0 = clean exit (or SIGINT), 1 = network/DB error, 2 = config error. Railway's `restartPolicyOnFailure` triggers on non-zero.
- The dump_task cancellation is in `finally:` to ensure cleanup on any path (clean exit, exception, KeyboardInterrupt).
- `KeyboardInterrupt` is caught at the top level (not in `_run`) because `asyncio.run()` translates SIGINT into a `CancelledError` inside the coroutine; catching it in `main` after `asyncio.run()` returns is the cleanest pattern.

### 4.8 `tests/_telegram_fixtures.py` (NEW)

```python
"""Shared test fixtures for the M5 telegram module.

Helpers:
  - make_event: build a synthetic Telethon NewMessage.Event for tests.
  - FakeStateStore: drop-in replacement for StateStore that records
    upsert_signal calls and returns a configurable bool.
  - NullLogger: a logging.Logger that swallows records; lets tests
    assert on parse-failure routing without writing to logs/parse_failures.log.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

from signal_copier.domain.signal import Signal


class _StubMessage:
    """Minimal stand-in for telethon.tl.custom.message.Message."""

    def __init__(self, *, message_id: int, outgoing: bool = False) -> None:
        self.id = message_id
        self.out = outgoing


def make_event(
    *,
    text: str,
    chat_id: int,
    message_id: int = 1,
    outgoing: bool = False,
) -> Any:
    """Build a synthetic Telethon NewMessage.Event.

    Only the attributes Listener reads are populated. Tests can call
    listener.on_new_message(make_event(...)) and assert on the side
    effects (queue contents, upsert_signal calls, parse_failures logs).
    """
    event = MagicMock()
    event.text = text
    event.chat_id = chat_id
    event.message = _StubMessage(message_id=message_id, outgoing=outgoing)
    return event


class FakeStateStore:
    """Drop-in replacement for StateStore. Records upsert_signal calls.

    Mirrors M3's fake-broker pattern in tests/test_broker_protocol.py.
    """

    def __init__(self, *, next_insert_returns: bool = True) -> None:
        self.upserted: list[Signal] = []
        self._next_returns = next_insert_returns

    async def upsert_signal(self, signal: Signal) -> bool:
        self.upserted.append(signal)
        return self._next_returns


class NullLogger(logging.Logger):
    """A logging.Logger that swallows all records. Used in tests that
    don't care about parse-failure logging.
    """

    def __init__(self, name: str = "null") -> None:
        super().__init__(name, level=logging.CRITICAL + 1)

    def handle(self, record: logging.LogRecord) -> None:  # noqa: D401
        return None
```

**Notes:**

- The use of `unittest.mock.MagicMock` is deliberate: building real Telethon `NewMessage.Event` instances is invasive and requires the Telethon metaclass machinery. A `MagicMock` with the 4 attributes Listener reads (`.text`, `.chat_id`, `.message.id`, `.message.out`) is functionally identical for our purposes and survives the Listener's attribute access pattern unchanged.
- `FakeStateStore` is a minimal shim — only `upsert_signal` is stubbed because that's the only StateStore method M5 calls. M6 will add similar fakes for the new StateStore methods it uses.
- `NullLogger` extends `logging.Logger` and overrides `handle` to a no-op. This is cleaner than mocking the `Logger` class with `MagicMock` because `Listener` calls `parse_failures_logger.warning(...)` — a mock would need `.warning()` set up; the subclass just swallows everything.

---

## 5. Dependency Changes

### 5.1 `pyproject.toml` modifications

**a. `dependencies` (runtime):**

```toml
dependencies = [
    "pydantic-settings>=2.6",  # M2: config layer
    "tzdata>=2024.1",          # IANA tz database on Windows
    "asyncpg>=0.30",           # M4: async PostgreSQL driver
    "telethon>=1.44",          # M5: Telegram MTProto user-account client
]
```

**b. `project.scripts` (entry points):**

```toml
[project.scripts]
signal-copier      = "signal_copier.__main__:main"
signal-copier-auth = "signal_copier.telegram.auth:main"  # M5: NEW
```

**c. `mypy` overrides (relax for M5 test files):**

```toml
[[tool.mypy.overrides]]
module = [
    "test_config", "test_db", "test_gale_math", "test_main", "test_parser",
    "test_state_machine",
    "test_clock", "test_telegram_listener", "test_telegram_client",  # M5: NEW
]
ignore_errors = true
```

Tests use `unittest.mock.MagicMock`, untyped Telethon `Event` objects, and pytest fixtures — all of which mypy `--strict` doesn't narrow well. Mirrors the M4 pattern.

### 5.2 New symbols

| Symbol | Source | Purpose |
|---|---|---|
| `telethon.TelegramClient` | telethon | The vendored MTProto client (re-aliased as `_TelethonClient` in our code) |
| `telethon.sessions.StringSession` | telethon | Serializable session backed by a base64 string |
| `telethon.errors.FloodWaitError` | telethon | "Slow down" error; we re-raise if `>60s` (FR-1.7) |
| `telethon.events.NewMessage`, `MessageEdited` | telethon | Event classes for new and edited messages |
| `asyncio.Queue` | stdlib | M5's signal queue (D-1) |
| `dataclasses.asdict` | stdlib | Convert `Signal` to dict for JSON pretty-print |
| `unittest.mock.MagicMock` | stdlib | Synthetic Telethon events in tests (D-2) |

### 5.3 Docker image impact

`telethon` is pure Python (no C extensions). Adding it to the Railway image increases image size by ~1–2 MB. The Dockerfile (defined in M0) needs no changes — `pip install` picks up the new dep automatically.

---

## 6. Architecture

### 6.1 Module relationships

```
                     ┌────────────────────────────┐
                     │  signal_copier/__main__.py │  (M5 wiring)
                     └─────────────┬──────────────┘
                                   │
            ┌──────────────────────┼──────────────────────┐
            │                      │                      │
            ▼                      ▼                      ▼
    ┌──────────────┐      ┌────────────────┐     ┌─────────────────┐
    │  infra/db.py │      │ telegram/      │     │ telegram/       │
    │  Database    │      │   client.py    │     │   listener.py   │
    │  - state_store ──┐  │  TelegramClient│     │  Listener       │
    └──────────────┘  │  │  - connect     │     │  - on_new_*     │
                      │  │  - start       │     │  - _process_*   │
                      ▼  │  - add_handler │◀────┤  (handler chain)│
              ┌──────────┐ │  - close       │     └────────┬────────┘
              │  State   │ └───────┬────────┘              │
              │  Store   │         │                       │
              │ (M4)     │         │                       │
              └────┬─────┘         ▼                       ▼
                   │       ┌──────────────┐      ┌──────────────────┐
                   │       │  Telethon    │      │  domain/signal   │
                   │       │  client +    │      │  parse_signal    │
                   │       │  String      │      │  derive_signal_id│
                   │       │  Session     │      └──────────────────┘
                   │       └──────┬───────┘                ▲
                   │              │                        │
                   │              │  Telegram events       │
                   │              │  (NewMessage/Edit)     │
                   │              ▼                        │
                   │       ┌──────────────┐                │
                   │       │  Telethon    │                │
                   │       │  WS / MTProto│                │
                   │       └──────┬───────┘                │
                   │              │                        │
                   │              ▼                        │
                   │       ┌──────────────┐                │
                   │       │  Telegram    │                │
                   │       │  (cloud)     │                │
                   │       └──────────────┘                │
                   │                                       │
                   │       ┌──────────────────┐            │
                   │       │  infra/clock.py  │            │
                   │       │  hhmm_to_unix    │            │
                   │       │  signal_date_*   │            │
                   │       │  is_within_*     │────────────┘
                   │       │  now_unix        │
                   │       │  monotonic       │
                   │       └──────────────────┘
                   │
                   ▼
             ┌──────────────┐
             │   asyncpg    │
             │   Pool       │
             │   (Postgres) │
             └──────────────┘
```

### 6.2 Sequence — happy path (signal arrives)

```
Telegram (cloud)        Telethon         Listener          StateStore      Queue
        │                  │                 │                  │            │
        │  NewMessage push │                 │                  │            │
        │─────────────────▶│                 │                  │            │
        │                  │ on_new_message  │                  │            │
        │                  │────────────────▶│                  │            │
        │                  │                 │ chat filter OK   │            │
        │                  │                 │ parse_signal()   │            │
        │                  │                 │ (M1: ParsedSignal)            │
        │                  │                 │                  │            │
        │                  │                 │ is_within_window │            │
        │                  │                 │ (clock.py)       │            │
        │                  │                 │                  │            │
        │                  │                 │ upsert_signal    │            │
        │                  │                 │─────────────────▶│            │
        │                  │                 │                  │ INSERT     │
        │                  │                 │                  │ signals... │
        │                  │                 │◀─────────────────│            │
        │                  │                 │ True (new)       │            │
        │                  │                 │                  │            │
        │                  │                 │ queue.put(signal)            │
        │                  │                 │─────────────────────────────▶│
        │                  │                 │                  │            │
        │                  │                 │ print(json)      │            │
        │                  │                 │ (to stdout)      │            │
        │                  │                 │                  │            │
        │                  │                 │ return           │            │
        │                  │◀────────────────│                  │            │
```

### 6.3 Sequence — parse failure (ad text arrives)

```
Telegram        Telethon         Listener         parse_failures.log
   │               │                │                    │
   │ NewMessage    │                │                    │
   │──────────────▶│                │                    │
   │               │ on_new_message │                    │
   │               │───────────────▶│                    │
   │               │                │ chat filter OK     │
   │               │                │ parse_signal()     │
   │               │                │ -> ParseFailure    │
   │               │                │ (reason=MISSING_*) │
   │               │                │                    │
   │               │                │ parse_failures_    │
   │               │                │  logger.warning()  │
   │               │                │───────────────────▶│
   │               │                │                    │
   │               │                │ return (no upsert, │
   │               │                │  no enqueue)       │
   │               │◀───────────────│                    │
```

### 6.4 Sequence — auth bootstrap (separate process)

```
$ python -m signal_copier.telegram.auth
   │
   ├─ Read .env (Config().telegram_api_id, ...)
   │    -> if missing, exit 2
   │
   ├─ Create _TelethonClient(StringSession(), api_id, api_hash)
   │
   ├─ client.start(phone=phone)
   │    -> Telethon prompts: "Please enter the code you received: ____"
   │    -> user types code
   │    -> if 2FA enabled, Telethon prompts: "Please enter your password: ____"
   │    -> user types password
   │    -> on success, Telethon populates StringSession
   │
   ├─ session_str = client.session.save()
   │
   └─ print to stdout:
        # --- Telegram session ---
        # Generated by `python -m signal_copier.telegram.auth`
        TELEGRAM_SESSION_STRING=AAAAxxx...long.base64.string...==zzz

$ # user copies the line into .env / Railway Variables
```

### 6.5 Concurrency

- Single asyncio loop (PRD §7 architecture).
- 3 coroutines: Telethon main loop, `dump_consumer` (M5; replaced by M6's scheduler), reconnect supervisor (inside `TelegramClient.start`).
- The Listener's handlers are invoked by Telethon one at a time (per-client serialization). No listener-internal lock needed.
- The asyncpg pool handles DB-write concurrency (M4 D-14).
- `asyncio.Queue(maxsize=1000)` — bounded as a safety net. M5's `dump_consumer` drains instantly, so the cap is never reached in M5; M6's scheduler drains at ~1 signal/min.

### 6.6 Error handling — `TelegramConfigError`

The only domain exception `TelegramClient.__init__` raises (D-12: at construction time) and `TelegramClient.connect()` raises (D-18: when target chat can't be resolved). Caught at the `main()` top level:

```python
except TelegramConfigError as exc:
    sys.stderr.write(f"❌ {exc}\n")
    return 2
```

Exit code 2 is distinct from network/DB errors (exit 1) so the Railway restart policy is not triggered on misconfiguration (config errors won't fix themselves on restart; the user must edit `.env`).

### 6.7 Error handling — `FloodWaitError`

Per FR-1.7, Telethon auto-handles waits ≤60s. For longer waits, the `run_until_disconnected` call re-raises `FloodWaitError`. M5's `start()` catches it, logs, and re-raises. `__main__.main()` does not catch it explicitly; it falls through to the generic `except Exception` branch (exit 1) so the container restarts.

This is intentionally minimal in M5. The full circuit-breaker behavior (PRD S-11) lands later as a follow-on:
- Repeated `FloodWaitError` → halt + DM (PRD §12)
- S-5: self-healing OlympTrade reconnect (parallel pattern for M8)
- S-11: 3-token-rejection circuit breaker (parallel pattern for M8)

M5 ships the minimum compliant with FR-1.7; the polish comes in v1.0 follow-ons.

### 6.8 Error handling — `ConnectionError` and reconnect

Per FR-1.8, `TelegramClient.start()` wraps `run_until_disconnected()` in a backoff loop. After 10 attempts (~2 minutes total wall time), re-raise. `__main__.main()` lets it fall through to the generic `except Exception` branch (exit 1).

### 6.9 Logging

M5 uses stdlib `logging` (D-8). The loguru setup lands in M7.

| Event | Log format | Level | Sink |
|---|---|---|---|
| `TelegramClient.connect()` success | `"TelegramClient connected (target_chat=%r -> chat_id=%d)"` | INFO | stderr |
| Reconnect attempt | `"Telegram ConnectionError: %s. Reconnect attempt %d/%d in %.1fs"` | WARNING | stderr |
| Reconnect gave up | `"Telegram reconnect failed after %d attempts; re-raising"` | ERROR | stderr |
| `FloodWaitError >60s` | `"Telegram FloodWaitError: %ds wait requested; re-raising (FR-1.7: ...)"` | ERROR | stderr |
| `FloodWaitError <=60s` (defensive) | `"FloodWaitError %ds; continuing"` | WARNING | stderr |
| New signal persisted | `"new signal: signal_id=%s pair=%s trigger=%s"` (implicit, via Listener stdout print) | n/a | stdout |
| Duplicate signal | `"duplicate signal, ignoring: signal_id=%s pair=%s trigger=%s"` | INFO | stderr |
| Parse failure (any reason) | `"parse_failure: reason=%s message_id=%s preview=%r"` | WARNING | `logs/parse_failures.log` |
| Out-of-window rejection | `"parse_failure: reason=out_of_window message_id=%s trigger_hhmm=%s trigger_unix=%.3f now_unix=%.3f skew_sec=%.1f"` | WARNING | `logs/parse_failures.log` |
| `dump_consumer` print | `json.dumps(asdict(signal), indent=2)` (Signal as JSON) | n/a | stdout |

No INFO log per successful parse-then-upsert call — the Listener's `print(json.dumps(asdict(signal)))` is the user-visible signal event. This keeps stderr clean and lets the user `> logs/signal_copier.log` the stderr stream separately if they want just the chatter.

---

## 7. Test Plan

M5 ships **3 new test files + 1 helper module = ~27 tests, all deterministic, no network.** M5 does not include live-Telegram integration tests; M9 covers that.

### 7.1 `tests/test_clock.py` (NEW, ~10 tests)

Pure-function tests. No I/O, no async, no fixtures.

| Test | What it verifies |
|---|---|
| `test_hhmm_to_unix_happy_path` | `"10:20"` on `date(2026, 6, 20)` in `America/Sao_Paulo` → expected epoch (computed independently) |
| `test_hhmm_to_unix_invalid_format_raises` | `"25:00"`, `"10:99"`, `"abc"`, `""` → `ValueError` |
| `test_hhmm_to_unix_at_midnight` | `"00:00"` and `"23:59"` produce valid epochs in the right date |
| `test_hhmm_to_unix_across_dst_spring_forward` | `America/New_York`, `2026-03-08 02:30` (invalid, skipped to 03:30) returns 03:30 epoch; `01:30` (unambiguous) returns 01:30 epoch |
| `test_hhmm_to_unix_across_date_line` | `"23:30"` on date X in `Asia/Tokyo` (UTC+9) → epoch that, in UTC, is on date X+1 |
| `test_signal_date_in_tz_at_local_midnight` | A unix ts that is `00:00:00` in `America/Sao_Paulo` → returns the local date (not UTC date) |
| `test_signal_date_in_tz_just_before_midnight` | A unix ts that is `23:59:59` in `America/Sao_Paulo` → returns today's local date |
| `test_is_within_window_past_boundary` | `now=1000.0`, `trigger=940.0` (60s past) → True; `trigger=939.0` (61s past) → False |
| `test_is_within_window_future_boundary` | `now=1000.0`, `trigger=2800.0` (1800s future) → True; `trigger=2801.0` (1801s future) → False |
| `test_is_within_window_default_tolerances` | `is_within_window(trigger, now)` (no kwargs) uses 60/1800 |
| `test_now_unix_close_to_time_time` | `now_unix()` within 1.0s of `time.time()` |

### 7.2 `tests/test_telegram_listener.py` (NEW, ~13 tests)

All tests use synthetic Telethon events (D-2) and the `FakeStateStore` from `tests/_telegram_fixtures.py`. A `NullLogger` from the same module is passed as `parse_failures_logger` to suppress test output.

| Test | What it verifies |
|---|---|
| `test_happy_path_valid_signal_enqueued_and_upserted` | Synthetic event with valid signal text → `FakeStateStore.upserted` has 1 entry; queue has 1 Signal; stdout has the pretty-print |
| `test_duplicate_signal_logged_not_re_enqueued` | `FakeStateStore(next_insert_returns=False)` → `upserted` has 1 entry; queue is empty; no print |
| `test_parse_failure_logged_to_parse_failures` | Synthetic event with ad text → `FakeStateStore.upserted` is empty; queue is empty; `parse_failures` logger received a WARNING record (asserted via a `LogCaptureHandler` attached to the null logger) |
| `test_parse_failure_all_reasons` | Parametrized over 7 `FailureReason` values: each produces exactly one parse_failures log line with the matching reason |
| `test_out_of_window_past_rejected` | Synthetic event with HH:MM 5 minutes in past → no upsert, no enqueue, parse_failures log has `reason=out_of_window` |
| `test_out_of_window_future_rejected` | HH:MM 45 minutes in future → same |
| `test_out_of_window_within_tolerance_accepted` | HH:MM 30s in past → accepted (within 60s tolerance) |
| `test_wrong_chat_filtered_silently` | `chat_id=999` while `target_chat_id=42` → no parse, no log, no enqueue |
| `test_outgoing_message_ignored` | `event.message.out=True` → no parse, no log, no enqueue |
| `test_new_message_and_edited_produce_identical_output` | Same text in `on_new_message` and `on_message_edited` → identical `FakeStateStore.upserted` list and identical queue contents |
| `test_empty_message_handled` | `text=""` → no parse, no upsert, no crash |
| `test_bom_message_handled` | `text="\ufeff" + valid_signal_text` → parses (M1's BOM tolerance) and produces the expected Signal |
| `test_handler_survives_parse_failure` | Bad message, then good message in sequence → second one is processed normally; first one's parse failure doesn't kill the loop |
| `test_expiration_not_allowed_rejected` | Header `💰3-minute expiration` (not 5) → ParseFailure with `EXPIRATION_NOT_ALLOWED` reason, no upsert |

### 7.3 `tests/test_telegram_client.py` (NEW, ~4 tests)

Pure backoff-math tests. No Telethon objects, no async.

| Test | What it verifies |
|---|---|
| `test_compute_backoff_seconds_exponential` | `compute_backoff_seconds(0..4)` returns 1.0, 2.0, 4.0, 8.0, 16.0 |
| `test_compute_backoff_seconds_capped_at_30` | `compute_backoff_seconds(5)` returns 30.0; `compute_backoff_seconds(20)` returns 30.0 |
| `test_compute_backoff_seconds_returns_float` | Return type is `float` (not `int`), useful for `asyncio.sleep` which accepts either |
| `test_reconnect_loop_gives_up_after_max_attempts` | A mock Telethon client that always raises `ConnectionError` on `run_until_disconnected()`; the supervisor re-raises after `_MAX_RECONNECT_ATTEMPTS = 10` calls; total elapsed time is roughly the sum of `compute_backoff_seconds(0..9)` (~150s in the test, but the test uses a monkey-patched `asyncio.sleep` to return immediately) |

### 7.4 Tests we explicitly do NOT write

- ❌ **Live Telegram connection tests** — fragile, requires test accounts, non-deterministic. M9 end-to-end covers this.
- ❌ **Tests against `python -m signal_copier.telegram.auth`** — requires interactive terminal. M11's runbook is the test.
- ❌ **End-to-end test of `__main__.py` with a real Database + real TelegramClient** — same reason as above. M9.

---

## 8. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Telethon's `NewMessage` and `MessageEdited` event classes are different but expose the same attributes | D-6: single `_process_message` body; both public handlers are 1-line shims. Tested explicitly in `test_new_message_and_edited_produce_identical_output`. |
| `Config.telegram_target_chat` could be `@username` or numeric `chat_id`; Telethon accepts both but the resolution happens at runtime | D-10 + D-18: resolve at `connect()` time, fail fast with `TelegramConfigError` if unresolvable. The error message names the unresolved value and hints at `.env`. |
| M5 uses stdlib `logging` without rotation; `logs/parse_failures.log` could grow unboundedly if the parser has a bug | M5's parser is M1's already-tested parser. The volume of parse failures is bounded by the analyst's posting rate (~10s/day). Rotation is M7's job (loguru). M5's parser regression would surface in `test_parser.py`, not from log file growth. |
| `Listener.on_new_message` and `on_message_edited` could be called concurrently by Telethon | Telethon serializes per-client (one handler invocation at a time). No internal lock needed. Verified by reading Telethon's event handler source: events are dispatched in `client._dispatch_update` which is awaited, not forked. |
| `dump_consumer` is canceled in `finally:` but its current `signal = await queue.get()` is in flight | The cancellation is graceful: `asyncio.CancelledError` is raised in the `await queue.get()`, the `finally: queue.task_done()` block runs (well — it doesn't, because the cancel happens at the await). If the queue has pending items, they're lost on shutdown. M5 doesn't care (M6's scheduler replaces this consumer and the M5 deliverable is "dumps to stdout" — losing pending items on SIGINT is acceptable). Documented in code comment. |
| Telethon is a heavy dependency; tests would slow down if it were imported at module level | `telethon` is imported lazily inside `client.py`'s method bodies (not at module top). Tests of `Listener` don't import Telethon at all. Tests of `compute_backoff_seconds` don't import Telethon. |
| `listener.print(json.dumps(...))` could block if stdout is a slow pipe | The print is after the `queue.put` (so the queue is the primary path), and the print is small (~500 bytes per signal). For `python -m signal_copier | tee logs/signals.log`, the pipe is line-buffered and the print is non-blocking in practice. M5 doesn't measure or guard against a slow stdout — if it ever matters, M6 can move the print into the `dump_consumer` itself. |

---

## 9. Out of Scope (deferred to later milestones)

| Concern | Lands in |
|---|---|
| Self-DM notifications on every FR-7.1 event | M7 (`notify/telegram_dm.py`) |
| Real broker placement, push event handler, `wait_result` integration | M8 (`broker/olymp.py`) |
| Loguru setup with rotation, `logs/signal_copier.log` | M7 |
| `asyncio.loop.call_at` scheduler draining the queue (M6) | M6 (`scheduler/trigger.py`) |
| Restart-recovery via `state_store.get_active_signals()` | M10 |
| Daily-limit enforcement via `state_store.get_daily_summary(date)` | M6 |
| `SignalState.from_signal_row(row, config)` helper (M4 §6.5) | M6 |
| FloodWaitError circuit breaker + repeated-failure halts (S-5, S-11) | v1.0 follow-on |
| Desktop notifications (FR-7.3) | v2 |
| Tighter scheduling precision (S-7) | v1.0 follow-on |
| Bot-account fallback if personal account is banned (S-6, related) | v2 |

---

## 10. Self-Review

Performed after writing the spec.

1. **Placeholder scan:** No TBD/TODO/incomplete sections. Every "deferred to M6/M7/M8" is named and linked.
2. **Internal consistency:** §2 D-1 says "asyncio.Queue + stub dump_consumer"; §4.7 implements that; §6.5 confirms the concurrency model. D-3 says "subcommand `python -m signal_copier.telegram.auth`"; §4.4 implements it; §6.4 sequence shows the flow. D-10 says "resolved at connect()"; §4.2 implements it; §6.1 diagram shows the relationship.
3. **Scope check:** M5 is a single milestone with a verifiable outcome (PRD §15: "Connects to Telegram, parses real channel messages, dumps to stdout"). The spec fits in one implementation plan. No decomposition needed.
4. **Ambiguity check:**
   - "Reconnect supervisor" — defined in §4.2 `start()` method, max 10 attempts, then re-raise.
   - "Time-window rejection" — defined in §4.3 `_log_out_of_window`, logs to `parse_failures.log` with reason `out_of_window`.
   - "What if `Config.telegram_target_chat` is a numeric chat_id vs @username?" — D-10 + §4.2 `connect()`: both are passed to `client.get_entity()`; Telethon accepts both natively.
   - "What if the user types the wrong SMS code during auth?" — §4.4: `client.start()` raises; caught by `_do_auth`'s `except Exception`; `auth.py`'s `main()` prints the error and exits 1.
   - "What if `event.text` is None?" — §4.3 `_process_message`: `text: str = event.text or ""` handles it; the empty-string check below filters it.
   - "What if the asyncio.Queue fills up?" — §6.5: bounded at 1000; the producer awaits `queue.put()` and the consumer drains instantly in M5, so the cap is never hit. M6 inherits the same cap.

No issues found. Spec is ready for user review.
