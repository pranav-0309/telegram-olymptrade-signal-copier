# PRD — Telegram → OlympTrade Signal Copier

**Status:** Draft v0.7 — `olymptrade_ws` is now vendored at `src/olymptrade_ws/` (MIT); see changelog in §18
**Owner:** Personal tool
**Target broker:** OlympTrade (via **vendored `olymptrade_ws`** at `src/olymptrade_ws/` — MIT-licensed, originally by Chipa, 2025; reverse-engineered WebSocket client)
**Target messenger:** Telegram (user account via MTProto, NOT a bot)
**Target hosting:** Railway.app (persistent container) + Railway PostgreSQL add-on
**Last update:** v0.7 — `olymptrade_ws` vendored in-tree at `src/olymptrade_ws/` (see §6, §7, §12.6, §18). All import paths, architecture tree, tech-stack table, build-plan M8, and pair-mapping notes updated to reflect the vendored layout. No source code from the vendored package was modified.

---

## 1. Overview

A single-process, asyncio-based personal tool that:

1. Connects to a personal Telegram account (Telethon, MTProto)
2. Monitors a specific Telegram channel/group where an admin posts forex trading signals
3. Parses each incoming message; if it matches the broker-signal format, extracts the structured trade parameters
4. At the prescribed `HH:MM`, places a CALL (up) or PUT (down) trade on OlympTrade for the given currency pair, expiration, and amount
5. Monitors the trade's result. On loss, executes the **1st gale** (2× the previous amount). On loss again, executes the **2nd gale** (3× the initial amount). Stops at first profit or after the 2nd gale.
6. Notifies the user (Telegram DM / log / desktop) at each state transition.

The tool mirrors the martingale-style strategy used by the analyst exactly: $2 → $4 → $8, single bet size, single asset per signal, fixed 5-minute expiration.

---

## 2. Goals & Non-Goals

### 2.1 Goals
- **Correctness:** Place the trade at the exact signal time, with the correct pair/direction/expiration/amount. Never place a duplicate trade for the same signal.
- **Idempotency:** Restart-safe. Process can be killed and resumed without doubling up.
- **Auditability:** Every signal received, every trade placed, every state transition is logged with timestamp and IDs.
- **Safety:** Hard daily-loss limit, hard daily-trade limit, dry-run mode, optional human confirmation before each trade.
- **Simplicity:** One Python process, one config file, one log file, runnable with a single `python -m signal_copier` command.

### 2.2 Non-Goals (v1)
- Multiple broker support (only OlympTrade)
- Web UI / dashboard (CLI + log file + Telegram DM only)
- Backtesting / historical replay
- Strategy optimization or signal generation
- Mobile app
- Cloud deployment / multi-user
- **Real-money trading** — v1 is **demo-only by mandate**. Real-money trading is a v2 feature, gated behind a 7-day clean demo soak test.

---

## 3. User Flow

```
Analyst (admin in Telegram group)
        │
        │ posts message: "💰5-minute expiration\nEUR/JPY;10:20;PUT🟥\n🕛TIME UNTIL 10:25\n1st GALE..."
        ▼
┌──────────────────────────────────────────┐
│ Tool: Telegram Listener (Telethon)       │
│  - Validates sender = admin              │
│  - Parses signal                         │
│  - Builds Signal dataclass               │
└────────────────┬─────────────────────────┘
                 │ enqueue(signal)
                 ▼
┌──────────────────────────────────────────┐
│ Tool: Trade Scheduler (asyncio)          │
│  - Computes target_ts for HH:MM          │
│  - loop.call_at(target_ts, place_trade)  │
└────────────────┬─────────────────────────┘
                 │ at HH:MM:00
                 ▼
┌──────────────────────────────────────────┐
│ Tool: Trade Executor (vendored olymptrade_ws) │
│  - place_order(pair, amount, dir, dur)   │
│  - Persists trade_id                     │
│  - Updates state machine                 │
└────────────────┬─────────────────────────┘
                 │ 5-minute wait
                 ▼
┌──────────────────────────────────────────┐
│ Tool: Result Monitor (push events)       │
│  - Listens for e:26 (TRADE_CLOSED)       │
│  - Updates state machine                 │
│  - WIN  → done                           │
│  - LOSS → schedule 1st gale              │
│  - LOSS after gale1 → schedule gale2     │
│  - LOSS after gale2 → done               │
└──────────────────────────────────────────┘
```

---

## 4. Functional Requirements

### 4.1 Telegram Signal Listener

**FR-1.1** Connect to a Telegram user account using MTProto (Telethon).
**FR-1.2** Authenticate once interactively (phone + code + 2FA), then load a persistent `StringSession` so subsequent runs need no interactive prompt.
**FR-1.3** Watch exactly one channel/group (configured by `@username` or numeric `chat_id`).
**FR-1.4** **No sender-allowlist check.** The Telegram channel is admin-only by platform design — only the analyst can post messages. The bot is a member, not a participant, so it cannot post. The parser's strict regex (§4.2) is the sole defense-in-depth against malformed messages.
**FR-1.5** Accept both `NewMessage` and `MessageEdited` events (so edited signals are picked up).
**FR-1.6** Emit a structured `Signal` event into an `asyncio.Queue` for the scheduler.
**FR-1.7** Handle `FloodWaitError` automatically (Telethon handles ≤60s; raise + log for longer).
**FR-1.8** Reconnect on transient `ConnectionError` with exponential backoff (1s → 2s → 4s → ... → cap 30s).

### 4.2 Signal Parser

**FR-2.1** Parse messages matching the exact format:
```
💰N-minute expiration
<PAIR>;<HH:MM>;<PUT🟥|CALL🟩>
🕛TIME UNTIL <HH:MM>
1st GALE -> TIME UNTIL <HH:MM>
2nd GALE - TIME UNTIL <HH:MM>
```
**FR-2.2** Use an **anchored line regex** strategy (not full-document regex) for robustness against embedded ads and CTAs:
- Strict pattern for the signal line: `^(?P<pair>[A-Z]{3}/[A-Z]{3});(?P<time>\d{2}:\d{2});(?P<dir>PUT🟥|CALL🟩)\s*$`
- Validate that the `💰N-minute expiration` line appears in the same message.
- Tolerate varying whitespace, blank lines, and trailing newlines.
- Strip trailing UTF-8 BOM if present.
**FR-2.3** Reject and log any message that:
- Lacks the signal line.
- Has `pair` not in the broker's available instruments.
- Has `time` more than 1 minute in the past or more than 30 minutes in the future (catches stale + typos).
- Has `expiration` not in the configured allowed set (e.g., only `5` for v1).
**FR-2.4** Derive gale times arithmetically (initial time + 5min, +10min) rather than parsing them from the message — the message format already gives them but the math is canonical.
**FR-2.5** Emit a `Signal` dataclass:
```python
@dataclass
class Signal:
    signal_id: str           # sha1(pair + time + dir + date)[:12]
    pair: str                # "EUR/JPY"
    direction: Literal["up", "down"]   # mapped from PUT/CALL
    trigger_hhmm: str        # "10:20"
    expiration_seconds: int  # 300 for 5-minute
    received_at_unix: float
    source_message_id: int
    source_chat_id: int
    raw_text: str
```

### 4.3 Trade Scheduler

**FR-3.1** Consume `Signal` objects from the listener's queue.
**FR-3.2** Compute `target_ts` = absolute epoch for `trigger_hhmm` on the **current day** in the configured timezone.
> **Confirmed:** signals are posted in **UTC−3** (analyst's clock, e.g. Brasília/Buenos Aires). Default `TIMEZONE=America/Sao_Paulo` (UTC−3 year-round, no DST since 2019). If the analyst changes, update the config.
**FR-3.3** **Strict time-window enforcement.** If the signal's trigger time has already passed before we get to schedule it (late arrival, clock skew, queued behind another signal, etc.), the signal is **invalid**. Mark it `status='error'` with `error_reason='signal_expired'`, DM-notify the user, and **do NOT place the trade — not now, not tomorrow**. There is no "shift to tomorrow" behavior. The signal is only valid at the exact HH:MM and is invalid before or after.
**FR-3.4** Schedule placement via `asyncio.get_event_loop().call_at(target_ts, ...)` (monotonic-clock-anchored, not affected by NTP wall-clock drift).
**FR-3.5** Pre-fire guard at trigger time: if `abs(loop.time() - target) > 2.0s`, treat the signal as expired (same as FR-3.3) — mark `status='error'`, `error_reason='signal_expired'`, DM-notify the user, do NOT place the trade. Same outcome whether the slip happened before scheduling (FR-3.3) or at fire-time (FR-3.5).
**FR-3.6** **Gale cascade inherits strict timing.** Each gale (`gale1` at `trigger + 5min`, `gale2` at `trigger + 10min`) has its own valid time window. If the scheduler detects that a gale's window has already passed before its fire time, the cascade ends: mark the signal `status='error'` with `error_reason='signal_expired'`, DM-notify the user. We never skip a gale and proceed to the next stage — the recovery math assumes consecutive stages, so skipping gale1 and firing gale2 as a standalone bet is meaningless.
**FR-3.7** Idempotency check before scheduling: if `state[signal_id]` already exists in persistence, skip (catches duplicate signal messages and process restarts).

### 4.4 Trade Executor (Broker Adapter)

**FR-4.1** Connect to OlympTrade using the **vendored** `olymptrade_ws` package at `src/olymptrade_ws/` (`from olymptrade_ws import OlympTradeClient`). The vendored package is byte-identical to the upstream `OlympTradeAPI` source (MIT, Chipa 2025); see §6, §12.6, and `src/olymptrade_ws/VENDORED.md`.
**FR-4.2** Authenticate using a pre-extracted `access_token` JWT (read from env var or config file). See Open Question Q-2.
**FR-4.3** Select the **demo** account group only for v1. Config validation must refuse to start if `OLYMP_ACCOUNT_GROUP != "demo"` (hard guardrail — see FR-6.6).
**FR-4.4** Provide a `Broker` interface so the executor is swappable:
```python
class Broker(Protocol):
    async def connect(self) -> None: ...
    async def place(self, signal: Signal, amount: float) -> str: ...  # returns trade_id
    async def wait_result(self, trade_id: str, timeout: float) -> TradeResult: ...
    async def close(self) -> None: ...
```
**FR-4.5** Concrete implementations:
- `OlympTradeBroker` — wraps the **vendored** `olymptrade_ws.core.client.OlympTradeClient` (imported as `from olymptrade_ws import OlympTradeClient`) + push event listener. No vendored source is modified; all broker-specific logic lives in `broker/olymp.py`.
- `DryRunBroker` — logs intended trades, never connects to broker. **Default for v1.**
**FR-4.6** Translate the signal:
- `PUT🟥` → `direction="down"`
- `CALL🟩` → `direction="up"`
- `EUR/JPY` → broker pair string (may need mapping; see Open Question Q-5)
- `5-minute expiration` → `duration=300`
- **Gale amounts (confirmed):** initial = `$2`, gale1 = `$4`, gale2 = `$8`. Stage amounts, not increments.

### 4.5 Result Monitor & Gale State Machine

**FR-5.1** State machine per signal. Every transition between stages checks the strict time window (FR-3.3 / FR-3.6); a missed window ends the cascade with `status='error'`, `error_reason='signal_expired'`.
```
pending
  │
  ├─ [trigger time already passed] ─────────────────────────────────────▶ error (signal_expired)
  │
  └─ [trigger time OK] ──fire──▶ placed_initial
                                   │
                                   ├─ [gale1 trigger time already passed] ▶ error (signal_expired)
                                   │
                                   ├─ WIN ──────────────────────────────────▶ done_win
                                   │
                                   └─ LOSS ──[gale1 trigger time OK]──fire──▶ placed_gale1
                                                                      │
                                                                      ├─ [gale2 trigger time already passed] ▶ error (signal_expired)
                                                                      │
                                                                      ├─ WIN ──────────────────────▶ done_win
                                                                      │
                                                                      └─ LOSS ──[gale2 trigger time OK]──fire──▶ placed_gale2
                                                                                                            │
                                                                                                            ├─ WIN ──────▶ done_win
                                                                                                            │
                                                                                                            └─ LOSS ─────▶ done_loss
```
Terminal states: `done_win`, `done_loss`, `done_tie`, `done_timeout`, `error` (with `error_reason` set).

**FR-5.2** Use **push events** (`E_TRADE_CLOSED = 26`) from `OlympTradeClient` to detect trade completion — register callbacks **before** placing each trade.
**FR-5.3** Wait for the result with a hard timeout (`expiration_seconds + 30s` grace). A timeout is treated as a loss for that stage (not as a timing failure of the next stage's window).
**FR-5.4** On `WIN` at any stage → set state to `done_win` and emit a Telegram DM notification.
**FR-5.5** On `LOSS` after `placed_initial` → schedule `placed_gale1` at `trigger + 5min` with amount `$4` (2× initial). If `gale1` cannot fire at its scheduled time, cascade ends with `error (signal_expired)` (see FR-3.6 / FR-5.1).
**FR-5.6** On `LOSS` after `placed_gale1` → schedule `placed_gale2` at `trigger + 10min` with amount `$8` (4× initial). [Confirmed: stage amounts, not increments.] If `gale2` cannot fire at its scheduled time, cascade ends with `error (signal_expired)`.
**FR-5.7** On `LOSS` after `placed_gale2` → terminal state `done_loss`, no further trades. (This is the **natural-loss** terminal state — different from `error (signal_expired)` which is a **timing-failure** terminal state.)
**FR-5.8** Persist state on **every transition** to PostgreSQL via the `infra/db.py` `StateStore` (signals/stages/daily_summary tables, see §9). Each write is wrapped in `conn.transaction()` for atomicity. Never persist inside the trade-result polling loop — only on transitions.
**FR-5.9** **Cascade-end due to timing failure.** At any stage, if the pre-fire guard trips (FR-3.5) or the scheduler detects the stage's window has already passed (FR-3.3), the cascade ends immediately at that stage: `status='error'`, `error_reason='signal_expired'`, DM-notify the user. No further stages attempt. Applies uniformly: missed initial kills the whole signal; missed gale1 prevents gale2 from firing; missed gale2 ends the cascade. No retry, no shifting, no skip-to-next-stage.

### 4.6 Safety & Limits

**FR-6.1** **Daily loss limit** — **OPTIONAL.** Env var `DAILY_LOSS_LIMIT`; **default `0.0` = disabled.** If set to a positive value (e.g., `50.00`), the tool stops accepting new signals for the rest of the day once realized losses exceed that value (in USD). At `0.0` (default) no loss-based halt occurs.
**FR-6.2** **Daily trade count limit** — **OPTIONAL.** Env var `DAILY_TRADE_LIMIT`; **default `0` = disabled.** If set to a positive integer (e.g., `50`), the tool stops accepting new signals once trade count reaches that value for the day. At `0` (default) no count-based halt occurs.
**FR-6.3** **Daily drawdown limit** — **OPTIONAL** (same pattern, for consistency). Env var `DAILY_DRAWDOWN_PCT`; **default `0` = disabled.** If set to a positive integer percentage (e.g., `20`), the tool stops accepting new signals if the day's realized PnL drops below `-DAILY_DRAWDOWN_PCT%` of the starting balance for the day.
**FR-6.4** **Auto-execute** — confirmed for v1. No per-trade confirmation; trades are placed automatically at the trigger time. A `REQUIRE_CONFIRM` flag exists in config but defaults to `false` and is reserved for v2.
**FR-6.5** **Dry-run mode** — config flag `dry_run: bool`. If true, the `DryRunBroker` is used and **no broker connection is made**. v1 ships with `DRY_RUN=true` by default.
**FR-6.6** **Demo-only hard guardrail** — the app refuses to start if `OLYMP_ACCOUNT_GROUP=real` while `DRY_RUN=false`. Implemented as a hard check in `config.py` validator. No bypass flag. To unlock real-money trading, you must explicitly change `OLYMP_ACCOUNT_GROUP=demo` → `real` AND `DRY_RUN=true` → `false` AND restart.

> **Semantics of "0 = disabled":** the check is `if limit > 0 and condition: halt`. A `0` (or unset, since pydantic-settings fills the default) means the limit is never evaluated. This lets the user ship with no limits while testing, then turn them on by setting any positive value — no code changes needed.

### 4.7 Notifications

All notifications go via **Telegram DM to the bot's own user account** ("Send message to myself"). Logged locally as well.

**FR-7.1** Telegram DM events (v1 — confirmed scope):

| Event | Message format | When |
|---|---|---|
| Signal received | `🟢 Signal received\n` `Pair: EUR/JPY\n` `Direction: PUT\n` `Trigger: 10:20 (UTC-3)\n` `Expiration: 5 min` | Immediately on parser match |
| Trade placed — **initial** | `⏱️ Trade placed (INITIAL)\n` `Pair: EUR/JPY\n` `Direction: PUT\n` `Amount: $2.00\n` `Expires: 10:25 (UTC-3)\n` `Trade ID: abc123` | Right after broker accepts order |
| Trade placed — **1st gale** | `⏱️ Trade placed (1st GALE)\n` `Amount: $4.00\n` `Expires: 10:30 (UTC-3)\n` `Triggered by: loss on initial\n` `Trade ID: def456` | Same as above, with `stage` tag |
| Trade placed — **2nd gale** | `⏱️ Trade placed (2nd GALE)\n` `Amount: $8.00\n` `Expires: 10:35 (UTC-3)\n` `Triggered by: loss on 1st gale\n` `Trade ID: ghi789` | Same as above, with `stage` tag |
| WIN — initial | `✅ WIN (INITIAL)\n` `Pair: EUR/JPY\n` `PnL: +$1.84\n` `Signal closed: done_win\n` `Next: stop (cascade ends)` | On push event e:26 |
| WIN — gale1 | `✅ WIN (1st GALE)\n` `Pair: EUR/JPY\n` `PnL: +$3.68\n` `Cascade: stopped after gale1 — total recovered` | Same |
| WIN — gale2 | `✅ WIN (2nd GALE)\n` `Pair: EUR/JPY\n` `PnL: +$7.36\n` `Cascade: stopped after gale2 — full recovery` | Same |
| LOSS — initial | `❌ LOSS (INITIAL)\n` `Pair: EUR/JPY\n` `PnL: -$2.00\n` `Next: scheduling 1st gale at 10:25 (UTC-3), $4.00` | On push event e:26 |
| LOSS — gale1 | `❌ LOSS (1st GALE)\n` `Pair: EUR/JPY\n` `PnL: -$4.00\n` `Next: scheduling 2nd gale at 10:30 (UTC-3), $8.00` | Same |
| LOSS — gale2 | `❌ LOSS (2nd GALE)\n` `Pair: EUR/JPY\n` `PnL: -$8.00\n` `Cascade: ended — full loss ($14.00 total)` | Same |
| Signal expired — initial | `⏰ Signal EXPIRED (INITIAL)\n` `Pair: EUR/JPY\n` `Trigger was: 10:20 (UTC-3)\n` `Reason: time window passed before fire\n` `Action: no trades placed; signal invalid` | When initial stage cannot fire at scheduled time (FR-3.3 / FR-3.5) |
| Signal expired — gale1 | `⏰ Signal EXPIRED (1st GALE)\n` `Pair: EUR/JPY\n` `Gale1 trigger was: 10:25 (UTC-3)\n` `Reason: time window passed before fire\n` `Action: no gale2 placed — cascade ended` | When gale1 stage cannot fire (FR-3.6 / FR-5.9) |
| Signal expired — gale2 | `⏰ Signal EXPIRED (2nd GALE)\n` `Pair: EUR/JPY\n` `Gale2 trigger was: 10:30 (UTC-3)\n` `Reason: time window passed before fire\n` `Action: cascade ended, no recovery attempted` | When gale2 stage cannot fire (FR-3.6 / FR-5.9) |
| Cascade end (terminal) | `🏁 Cascade complete: <status>\n` `Signal ID: …\n` `Total PnL: …\n` `Duration: …` | After any terminal state reached |
| Daily loss limit hit | `⚠️ Daily loss limit reached\n` `Losses today: $X.XX\n` `Limit: $Y.YY\n` `Action: no new signals until 00:00 (UTC-3)` | **Only fired when `DAILY_LOSS_LIMIT > 0` (FR-6.1).** On threshold cross |
| Daily trade limit hit | `⚠️ Daily trade limit reached\n` `Trades today: N\n` `Limit: M\n` `Action: no new signals until 00:00 (UTC-3)` | **Only fired when `DAILY_TRADE_LIMIT > 0` (FR-6.2).** On threshold cross |
| Telegram disconnect | `🔌 Telegram disconnected. Reconnecting…` | On `ConnectionError` |
| OlympTrade disconnect | `🔌 OlympTrade disconnected. Process will exit; supervisor will restart.` | On WS close |
| Parse failure | `⚠️ Skipped message (not a valid signal)\n` `Reason: …\n` `Preview: <first 80 chars>` | On regex mismatch |
| Bot startup | `🟢 Bot started\n` `Mode: dry_run / live demo\n` `Watching: @channel\n` `Timezone: America/Sao_Paulo` | On `__main__` boot |
| Bot shutdown | `🔴 Bot stopping\n` `Open cascades: N` | On SIGINT/SIGTERM |

**FR-7.2** Local log file (rotating, `loguru`, 10 MB × 5 files) — `logs/signal_copier.log`. Mirror every DM at INFO level with same payload (text only — no formatting).

**FR-7.3** Desktop notifications — **DEFERRED to v2**. User did not request. Easy to add later via `plyer` behind a config flag.

**FR-7.4** DM sending uses the **same Telethon client** as the listener (single connection). Sent messages go to `await client.send_message('me', text)`. Telegram's "Saved Messages" chat.

---

## 5. Non-Functional Requirements

**NFR-1 Performance:** Trigger precision ≤ 500ms vs. signal HH:MM on Python 3.13+ Windows.
**NFR-2 Reliability:** Process restart should resume from the last persisted state without duplicating trades.
**NFR-3 Observability:** Every signal, every trade, every state transition has a structured log line with a `correlation_id = signal_id`.
**NFR-4 Configurability:** All magic numbers (amounts, limits, timezone, chat_id, admin IDs) live in a single `.env` / `.yaml` config file. **No hardcoded secrets in source.**
**NFR-5 Testability:** Core logic (parser, state machine, gale math) covered by unit tests. Broker integration tested against `DryRunBroker` and against a recorded session.
**NFR-6 Portability:** Runs on Windows 10/11 + Python 3.11+. Tested on 3.13.

---

## 6. Tech Stack

| Concern | Choice | Rationale |
|---|---|---|
| Language | **Python 3.13+** | Best Windows monotonic-clock precision; required for sub-second scheduling |
| Telegram client | **Telethon 1.44.x** | Only actively-maintained MTProto user-account library; Pyrogram is abandoned |
| Broker | **`olymptrade_ws`** (vendored at `src/olymptrade_ws/`, MIT, originally by Chipa 2025) | Reverse-engineered WebSocket client. **Vendored, not installed as a package** — see §12.6 and `src/olymptrade_ws/VENDORED.md`. Imported as `from olymptrade_ws import OlympTradeClient`. |
| Async runtime | **`asyncio` stdlib** | All deps are asyncio-native; no need for `trio` |
| Scheduling | **`asyncio.loop.call_at`** | Built-in, monotonic-clock-anchored; **no APScheduler** |
| Config | **`.env` + `pydantic-settings`** | Type-safe, validated, no surprises |
| Persistence | **PostgreSQL via `asyncpg`** | Railway provides a managed Postgres add-on (free tier available). Async-native driver, connection pool, atomic transactions, queryable. Schema migrations via `CREATE TABLE IF NOT EXISTS` for v1; upgrade path to a real migration tool (`pg-migrate` or `yoyo-migrations`) in v2 if schema evolves. |
| Logging | **`loguru`** | Simpler than stdlib `logging`; built-in rotation |
| Notifications (Telegram DM) | **Telethon** (same client) | No extra dependency; messages sent to "Saved Messages" |
| Testing | **`pytest` + `pytest-asyncio`** | Industry standard |
| Linting/formatting | **`ruff`** | Fast, replaces flake8+black+isort |
| Type checking | **`mypy --strict`** | Catch errors before runtime |
| Process supervision | **Railway restart policy** | Auto-restart on crash via Railway dashboard; matches hosting choice in §17 |

**Explicitly NOT used:**
- `python-telegram-bot` — bot-only, can't read user-account channels
- `Pyrogram` — abandoned, fragmented forks
- `APScheduler` — overkill, adds misfire handling complexity we don't need
- `Celery` / `Redis` / RabbitMQ — single-process tool, no queues needed
- Web framework / dashboard — non-goal for v1
- `plyer` / desktop notifications — DEFERRED to v2 (user did not request)
- `aiosqlite` / JSON file storage — replaced by PostgreSQL (R-13)
- `psycopg` / `psycopg2` — using `asyncpg` instead (faster, async-native, no DB-API overhead)
- Vercel / serverless platforms — incompatible with persistent WS design (see §17)

---

## 7. Architecture

Single-process, three concurrent asyncio coroutines coordinated by a state machine with PostgreSQL persistence.

```
signal_copier/
├── pyproject.toml
├── .env.example
├── README.md
├── Dockerfile                    # Railway deploy
├── railway.toml                  # Railway service config
├── .dockerignore
├── .python-version               # 3.13 pin for Nixpacks fallback
├── src/
│   ├── olymptrade_ws/            # VENDORED — third-party, do not edit (see §12.6)
│   │   ├── VENDORED.md           # upstream source, license, re-vendoring instructions
│   │   ├── LICENSE               # MIT, Copyright 2025 Chipa
│   │   ├── __init__.py           # re-exports OlympTradeClient, BalanceAPI, MarketAPI, TradeAPI
│   │   ├── main.py
│   │   ├── api/                  # balance.py, market.py, trade.py, utils.py
│   │   ├── core/                 # client.py, connection.py, protocol.py
│   │   ├── logs/                 # upstream's message_logbook.md
│   │   └── olympconfig/          # parameters.py
│   └── signal_copier/
│       ├── __init__.py
│       ├── __main__.py           # entrypoint: `python -m signal_copier`
│       ├── config.py             # pydantic-settings models
│       ├── domain/
│       │   ├── signal.py         # Signal dataclass + parser
│       │   ├── state.py          # State machine logic (no persistence here)
│       │   └── gale.py           # Gale math (amount per stage)
│       ├── telegram/
│       │   ├── client.py         # Telethon wrapper, StringSession mgmt
│       │   └── listener.py       # events.NewMessage handler
│       ├── broker/
│       │   ├── base.py           # Broker Protocol
│       │   ├── dry_run.py        # DryRunBroker
│       │   └── olymp.py          # OlympTradeBroker (wraps vendored olymptrade_ws.OlympTradeClient)
│       ├── scheduler/
│       │   └── trigger.py        # call_at scheduler, gale cascade
│       ├── notify/
│       │   └── telegram_dm.py    # self-DM notifier (single client)
│       └── infra/
│           ├── log.py            # loguru config
│           ├── db.py             # asyncpg pool + schema bootstrap + StateStore
│           └── clock.py          # tz helpers, monotonic helpers
├── migrations/
│   └── 001_initial.sql           # CREATE TABLE IF NOT EXISTS for signals / stages / daily_summary
├── tests/
│   ├── test_parser.py
│   ├── test_state_machine.py
│   ├── test_gale_math.py
│   ├── test_db.py                # asyncpg integration tests against a test PG instance
│   └── fixtures/
│       └── sample_signals.txt
└── docs/
    ├── PRD.md  ← this file
    └── tool-idea.md  ← original idea
```

> **`src/olymptrade_ws/` is third-party vendored code.** No file under it may be edited as part of feature work. If a broker-protocol change forces a patch, the modification must be recorded in `src/olymptrade_ws/VENDORED.md` under "Local modifications" (§12.6).

**Concurrency model:** one asyncio loop, one process. Three top-level coroutines started in `__main__.py`:
1. `telegram_listener()` — runs forever, drains Telethon events into `signals_queue`
2. `trade_scheduler()` — drains `signals_queue`, registers `call_at` triggers, places trades on fire
3. `result_monitor()` — per active signal, awaits push events, updates state, schedules gales

**State persistence:** `infra/db.py` opens an `asyncpg.create_pool()` against `DATABASE_URL` at startup, runs `migrations/001_initial.sql` (idempotent `CREATE TABLE IF NOT EXISTS`), and exposes a `StateStore` class with async methods (`get_signal`, `upsert_signal`, `record_stage_result`, `get_daily_summary`, `update_daily_summary`). All writes go through this pool — Postgres handles atomicity via transactions, no file-locking needed. The connection pool handles concurrency; no `asyncio.Lock` is required at the app layer.

---

## 8. Configuration (`.env` example)

```bash
# --- Telegram ---
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=abcdef0123456789abcdef0123456789
TELEGRAM_PHONE=+12345678900
TELEGRAM_SESSION_STRING=                          # generated on first run, saved here
TELEGRAM_TARGET_CHAT=@analyst_channel             # OR numeric chat_id (e.g., -1001234567890)
TELEGRAM_SELF_DM_NOTIFICATIONS=true

# --- OlympTrade ---
OLYMP_ACCESS_TOKEN=eyJhbGciOiJSUzI1NiIs...       # user has this ready
OLYMP_ACCOUNT_GROUP=demo                         # demo ONLY for v1; app refuses to start if 'real' + DRY_RUN=false
OLYMP_ACCOUNT_ID=                                 # user has this ready; auto-detect if blank

# --- Database ---
DATABASE_URL=postgresql://user:pass@host:5432/dbname    # Railway injects this automatically when you add the Postgres plugin

# --- Trading ---
DRY_RUN=true                                      # v1 default; safe paper-trading mode
REQUIRE_CONFIRM=false                             # v1 auto-executes; reserved for v2
AMOUNT_INITIAL=2.00                              # stage amounts (confirmed): $2, $4, $8
AMOUNT_GALE1=4.00
AMOUNT_GALE2=8.00
EXPIRATION_SECONDS=300
DAILY_LOSS_LIMIT=0.00                            # OPTIONAL: 0 = disabled, any positive value = halt at this USD loss
DAILY_TRADE_LIMIT=0                              # OPTIONAL: 0 = disabled, any positive integer = halt at this trade count
DAILY_DRAWDOWN_PCT=0                             # OPTIONAL: 0 = disabled, any positive % of starting balance = halt threshold

# --- Schedule / Timezone ---
TIMEZONE=America/Sao_Paulo                        # analyst posts in UTC-3 (Brasília); DST-free since 2019
TRIGGER_SKEW_TOLERANCE_SECONDS=2.0
LOG_PATH=./logs/signal_copier.log
```

> **Removed in v0.4:**
> - `TELEGRAM_ADMIN_IDS` — channel is admin-only by Telegram design; allowlist check dropped (R-14).
> - `PERSISTENCE_PATH` — replaced by `DATABASE_URL` (R-13). The Railway Postgres plugin injects `DATABASE_URL` automatically.

---

## 9. Data Model

**v1 persistence: PostgreSQL** via the `asyncpg` driver. The Railway Postgres add-on (R-13) injects `DATABASE_URL` automatically. Schema lives in `migrations/001_initial.sql` and runs idempotently at startup.

### 9.0 Schema (DDL)

```sql
-- migrations/001_initial.sql

CREATE TABLE IF NOT EXISTS signals (
    signal_id          TEXT PRIMARY KEY,
    pair               TEXT NOT NULL,
    broker_pair        TEXT,
    broker_category    TEXT,
    direction          TEXT NOT NULL CHECK (direction IN ('up', 'down')),
    trigger_hhmm       TEXT NOT NULL,
    trigger_ts_unix    DOUBLE PRECISION NOT NULL,
    expiration_seconds INTEGER NOT NULL,
    received_at_unix   DOUBLE PRECISION NOT NULL,
    source_message_id  BIGINT,
    source_chat_id     BIGINT,
    raw_text           TEXT,
    status             TEXT NOT NULL
        CHECK (status IN (
            'pending', 'placed_initial', 'placed_gale1', 'placed_gale2',
            'done_win', 'done_loss', 'done_tie', 'done_timeout', 'error'
        )),
    error_reason       TEXT,
    created_at_unix    DOUBLE PRECISION NOT NULL,
    updated_at_unix    DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS stages (
    trade_id           TEXT PRIMARY KEY,
    signal_id          TEXT NOT NULL REFERENCES signals(signal_id) ON DELETE CASCADE,
    stage              TEXT NOT NULL CHECK (stage IN ('initial', 'gale1', 'gale2')),
    pair               TEXT NOT NULL,
    direction          TEXT NOT NULL,
    amount             DOUBLE PRECISION NOT NULL,
    placed_at_unix     DOUBLE PRECISION NOT NULL,
    expires_at_unix    DOUBLE PRECISION NOT NULL,
    closed_at_unix     DOUBLE PRECISION,
    pnl                DOUBLE PRECISION,
    result             TEXT CHECK (result IN ('open', 'win', 'loss', 'tie', 'timeout', 'error')),
    broker_trade_id    TEXT
);

CREATE TABLE IF NOT EXISTS daily_summary (
    date              DATE PRIMARY KEY,
    signals_count     INTEGER NOT NULL DEFAULT 0,
    trades_count      INTEGER NOT NULL DEFAULT 0,
    wins              INTEGER NOT NULL DEFAULT 0,
    losses            INTEGER NOT NULL DEFAULT 0,
    realized_pnl      DOUBLE PRECISION NOT NULL DEFAULT 0,
    limit_hit         TEXT
);

CREATE INDEX IF NOT EXISTS idx_signals_status      ON signals(status);
CREATE INDEX IF NOT EXISTS idx_signals_trigger_ts  ON signals(trigger_ts_unix);
CREATE INDEX IF NOT EXISTS idx_stages_signal_id    ON stages(signal_id);
CREATE INDEX IF NOT EXISTS idx_stages_placed_at    ON stages(placed_at_unix);
CREATE INDEX IF NOT EXISTS idx_stages_result       ON stages(result);
```

### 9.1 Field semantics

| Table / Field | Notes |
|---|---|
| `signals.signal_id` | `sha1(pair + "|" + trigger_hhmm + "|" + direction + "|" + date)[:12]` — deterministic, idempotent across restarts and duplicate Telegram messages. PRIMARY KEY catches duplicates with `ON CONFLICT DO NOTHING`. |
| `signals.broker_pair` | Resolved at scheduling time via auto-discover (§13.4). `NULL` if pair lookup failed (signal will be in `error` state with `error_reason='unsupported_pair'`). |
| `signals.broker_category` | `"digital"`, `"forex"`, or `"otc"` — also resolved at scheduling time. |
| `signals.status` | Top-level state machine state (see §4.5). CHECK constraint enforces valid values at the DB layer. |
| `signals.error_reason` | Nullable. Populated only when `status='error'`. Values: `'signal_expired'` (FR-3.3/3.6/5.9 — any stage's time window passed), `'unsupported_pair'`, `'broker_unavailable'`, `'token_expired'`, `'unknown'`. |
| `stages.result` | `"open" | "win" | "loss" | "tie" | "timeout" | "error"`. CHECK constraint enforces. |
| `stages.signal_id` | Foreign key to `signals.signal_id`. `ON DELETE CASCADE` so deleting a signal cleans up its stages (useful for testing; v1 production code never deletes). |
| `daily_summary.date` | `DATE` in the configured `TIMEZONE`. PRIMARY KEY. UPSERT on every transition that affects the day. |
| `daily_summary.limit_hit` | NULL or `'loss' | 'count' | 'drawdown'`. When non-NULL, no new signals are accepted for that date. **Stays NULL if all limits are set to `0` (disabled, see FR-6.1/6.2/6.3).** |

### 9.2 Connection & transaction model

```
# infra/db.py (conceptual)

import asyncpg

class Database:
    pool: asyncpg.Pool

    async def connect(self, dsn: str) -> None:
        self.pool = await asyncpg.create_pool(
            dsn=dsn,
            min_size=2,
            max_size=10,
            command_timeout=30,
        )
        # Run migrations
        async with self.pool.acquire() as conn:
            with open("migrations/001_initial.sql") as f:
                await conn.execute(f.read())

    async def close(self) -> None:
        await self.pool.close()

    async def upsert_signal(self, signal: Signal) -> None:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """INSERT INTO signals (...) VALUES (...)
                       ON CONFLICT (signal_id) DO NOTHING""",
                    ...
                )

    async def record_stage(self, signal_id: str, stage: StageRecord) -> None:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """INSERT INTO stages (...) VALUES (...)
                       ON CONFLICT (trade_id) DO UPDATE SET ...""",
                    ...
                )

    async def get_active_signals(self) -> list[dict]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                "SELECT * FROM signals WHERE status IN ('placed_initial','placed_gale1','placed_gale2')"
            )
```

**Properties:**
- All writes are inside `conn.transaction()` — atomic per call.
- The connection pool handles concurrency (no `asyncio.Lock` needed at the app layer).
- `ON CONFLICT (PK) DO NOTHING` makes inserts idempotent for duplicate `signal_id`s.
- `command_timeout=30s` aborts hung queries (defense against a stuck PG).
- Migrations are `CREATE TABLE IF NOT EXISTS` only for v1 — no schema evolution yet. Adding `migrations/002_*.sql` files with proper migration tooling (e.g., `yoyo-migrations`) is a v2 task.

### 9.3 What changed from JSON design

The JSON shape (signals → stages embedded as sub-objects → daily_summary) is now normalized into three relational tables. Conceptual model is identical; storage shifts from nested JSON to relational rows. **No data migration is needed** — v1 ships with empty DB; the JSON plan was never deployed.

### 9.4 Local development without Railway

For local testing without a Railway Postgres, run a local PG via Docker:
```bash
docker run -d --name signal-copier-pg -p 5432:5432 \
  -e POSTGRES_USER=copier -e POSTGRES_PASSWORD=copier -e POSTGRES_DB=copier \
  postgres:16-alpine
export DATABASE_URL=postgresql://copier:copier@localhost:5432/copier
```

Or use a free-tier Neon / Supabase / ElephantSQL URL — any Postgres 13+ works.

---

## 10. Error Handling & Recovery

| Failure | Behavior |
|---|---|
| Telegram disconnect | Auto-reconnect with exponential backoff (1s → 30s cap) |
| OlympTrade WS disconnect | **No auto-reconnect** in the vendored `olymptrade_ws` package. We add a wrapper: on disconnect, persist state then exit non-zero so Railway's restart policy brings the container back (see §17). |
| Token expired | Detect on first `place_order` failure → send Telegram DM + halt |
| Trade placed but no result within `expiration + 30s` | Mark stage `result='timeout'`, treat as loss, schedule gale |
| Signal trigger time passed (any stage: initial, gale1, or gale2) | **Cascade ends at the failed stage** (FR-5.9). Mark `status='error'`, `error_reason='signal_expired'`, DM-notify the user. No retry, no shifting to next day, no skipping to next stage. Applies uniformly to all three stages. |
| Trade-result timeout (not the same as trigger-time slip) | Treated as LOSS for that stage; gale cascade proceeds as normal — different from FR-3.6 timing-failure. |
| Process killed mid-cascade | On restart, query `signals` table for `status IN ('placed_initial','placed_gale1','placed_gale2')`. For each, check broker open trades / history to determine outcome and resume cascade. |
| Daily limit hit | **Only when limit > 0 (FR-6.1/6.2/6.3).** Stop accepting signals; user must restart explicitly to override. With default `DAILY_LOSS_LIMIT=0` and `DAILY_TRADE_LIMIT=0`, this branch is never taken. |
| Parse error on message | Log raw message to `logs/parse_failures.log` with timestamp; do not crash |
| Two signals for same trigger | First wins (`signal_id` PRIMARY KEY + `ON CONFLICT DO NOTHING`); second logged as duplicate |
| Postgres connection lost | `asyncpg` pool auto-reconnects on next acquire. If reconnect fails repeatedly, halt + DM user. No data loss (writes are transactional). |
| Postgres migration failure on startup | Refuse to start; print SQL error and halt. Fix migrations and redeploy. |
| Unsupported pair (e.g. `USD/EGP` not on broker) | `OlympTradeBroker.place()` raises `UnsupportedPairError`. State machine marks signal `status='error'`, `error_reason='unsupported_pair'`. DM-notify the user. No crash, no missed trade. |
| Invalid `DATABASE_URL` at startup | Refuse to start with clear error pointing to Railway Postgres setup (see §17.4) |

---

## 11. Logging & Observability

- `loguru` to `logs/signal_copier.log` (rotating, 10 MB × 5 files)
- Structured fields: `signal_id`, `stage`, `trade_id`, `pnl`, `event`
- Separate `logs/parse_failures.log` for malformed messages
- Separate `logs/raw_telegram.log` for raw Telethon events at DEBUG
- All state transitions logged at INFO with correlation ID

---

## 12. Security & Compliance

⚠️ **Risks the user must accept:**

- **Telegram ToS (PERSONAL account, accepted):** User confirmed they will use their **personal** Telegram account. This carries a real ToS-ban risk. Mitigations baked into the tool:
  - `flood_sleep_threshold` left at Telethon's default of 60s
  - Bot never sends messages on its own (only `client.send_message('me', ...)` for self-DM notifications)
  - Bot never joins extra channels, never forwards content, never interacts with other users
  - On repeated `FloodWaitError`, the bot halts and DMs the user to investigate
  - **If the account is banned, the tool is dead until a new account is provisioned.** This is a single point of failure; no retry path.
- **OlympTrade ToS:** Automated trading via reverse-engineered WS protocol is a ToS violation. The token can be revoked at any time, the protocol can change overnight and break the tool.
- **Real money — DISABLED for v1:** the app refuses to start with `OLYMP_ACCOUNT_GROUP=real`. Real money is a v2 feature, gated behind a 7-day clean demo soak test. Do not attempt to bypass the guardrail.
- **Credential storage:** Tokens/session strings, `OLYMP_ACCESS_TOKEN`, and `DATABASE_URL` are credentials. Store in `.env` (local) / Railway Variables dashboard (hosted). Never commit. `.gitignore` must include `.env`, `*.session`, `data/`, `logs/`. Consider OS keyring (`keyring` lib) as a v2 enhancement.
- **No sender-allowlist (R-14):** the channel is admin-only by Telegram platform design — only the analyst can post. The bot is a read-only member. The parser's strict regex (FR-2.2) is the sole defense-in-depth against malformed messages. If the analyst ever invites other posters, the bot will silently ignore non-signal messages and log parse failures — no security boundary is crossed.

### 12.6 Vendored third-party code — `olymptrade_ws`

The broker client is a reverse-engineered WebSocket library written by **Chipa (2025)** and is **vendored in-tree** at `src/olymptrade_ws/`. The vendoring decision is recorded as a project resolution (R-15, see §13.1).

**Licensing:**
- Upstream license: **MIT** — Copyright (c) 2025 Chipa.
- MIT permits redistribution and modification provided the copyright notice and license text are preserved.
- The upstream `LICENSE` file is copied verbatim to `src/olymptrade_ws/LICENSE` and is **never** to be removed or altered.

**Attribution requirements (MIT compliance):**
- The copyright notice (`Copyright (c) 2025 Chipa`) must remain in `src/olymptrade_ws/LICENSE`.
- Any distribution of the project (e.g., a Docker image, a public release) must include the LICENSE file in the vendored directory — the Dockerfile must `COPY src/olymptrade_ws/LICENSE` along with the rest of the source (see §17.4).
- The top-level project `README.md` must reference the vendored license (it currently does; verify on every release).

**Operational rules:**
- No file under `src/olymptrade_ws/` may be edited as part of normal feature work. The directory layout (`api/`, `core/`, `olympconfig/`, `__init__.py`, `main.py`, `logs/`) is upstream-defined.
- If a broker-protocol change forces a code patch, the patch must:
  1. Be recorded in `src/olymptrade_ws/VENDORED.md` under **"Local modifications"** (date, what, why, upstream link if any).
  2. Be reviewed against the upstream snapshot at `../OlympTradeAPI/` (kept locally, gitignored — see `.gitignore`).
  3. Be merged into the broker adapter (`signal_copier/broker/olymp.py`) wherever possible, so the vendored layer stays minimal and easy to re-vendor from upstream.
- Re-vendoring workflow is documented in `src/olymptrade_ws/VENDORED.md`. After re-vendoring, the **"Local modifications"** section must be reviewed and any patches re-applied + re-documented.

**Why vendored instead of a Python package:** see R-15 in §13.1.

---

## 13. Open Questions (all resolved — R-1 through R-15)

> Resolved answers are listed first. All architectural decisions resolved through v0.7 (R-1 through R-15). Build can start.

### 13.1 Resolved (confirmed by user)

- ✅ **R-1. Demo vs real** — **demo-only for v1.** Hard guardrail in config validator; no bypass.
- ✅ **R-2. Gale math** — **stage amounts:** $2 → $4 → $8 (not increments). Total exposure if all 3 lose = $14.
- ✅ **R-3. Timezone** — signals posted in **UTC−3**. Default `TIMEZONE=America/Sao_Paulo` (DST-free since 2019).
- ✅ **R-4. Execution mode** — **auto-execute** for v1. No per-trade confirmation; trades fire at trigger time.
- ✅ **R-5. OlympTrade credentials** — user has `access_token` + `account_id` ready. No helper script needed for v1. (Suggestion S-6 still listed for future.)
- ✅ **R-6. Telegram account** — user will use their **personal Telegram account**. ToS ban risk is real and accepted. Mitigations: keep `flood_sleep_threshold=60`, do not post anything from the bot account (only self-DM), do not join extra channels, monitor for `FloodWaitError` and halt on persistent errors. **Single point of failure:** if banned, tool is dead until a new account is provisioned.
- ✅ **R-7. Multiple channels** — one channel only.
- ✅ **R-8. Persistence** — **PostgreSQL** via Railway Postgres add-on + `asyncpg` driver. Three tables (`signals`, `stages`, `daily_summary`) with CHECK constraints and indexes. Migrations via `migrations/001_initial.sql` (idempotent `CREATE TABLE IF NOT EXISTS`). Connection pool handles concurrency; no app-level locking needed. See §9 for full schema and §17.4 for Railway provisioning.
- ✅ **R-9. Notifications** — **Telegram self-DM only** with rich content (see §4.7 FR-7.1 for the full table). Desktop notifications DEFERRED to v2.
- ✅ **R-10. Asset whitelist** — none; allow all OlympTrade-available instruments.
- ✅ **R-11. Pair mapping** — **auto-discover at startup.** `broker/olymp.py` calls `client.market.select_asset()` / waits for the e:1068 instrument listing, builds a `dict[str, str]` map from `EUR/JPY` → broker symbol. Bot DMs the resolved list on startup. See §4.4 + §13.4 for the implementation contract.
- ✅ **R-12. Hosting** — **Railway.app** persistent container + Railway Postgres add-on. Dockerfile-based deploy (clean control over Python 3.13). Auto-restart on crash via Railway's restart policy. **No Volume needed** — PG state lives in the managed Postgres service, not the container's ephemeral filesystem.
- ✅ **R-13. Database** — **PostgreSQL** (replaces the earlier JSON-file plan, R-8 v0.3). Rationale: Railway provides a managed Postgres add-on with a free tier, removing the need for a Volume, atomic-write dance, and migration-to-SQLite story. The asyncpg driver is async-native, fast, and dependency-light.
- ✅ **R-14. Telegram sender verification** — **no allowlist check.** The Telegram channel is admin-only by platform design — only the analyst can post. The bot is a read-only member and cannot post. The parser's strict regex (FR-2.2) is the sole defense-in-depth against malformed messages. The `TELEGRAM_ADMIN_IDS` config field is removed. FR-1.4 updated.
- ✅ **R-15. Broker client packaging — VENDORED in-tree.** The third-party `olymptrade_ws` package is committed in-tree at `src/olymptrade_ws/` (sibling of `signal_copier/`, not nested inside it). **Not** installed as a PyPI package, **not** a git submodule, **not** a path dependency. **Rationale:** (a) upstream has no `pyproject.toml`/`setup.py` — it's source, not a distributable package; (b) MIT license permits redistribution with attribution, so vendoring is legally clean; (c) the package name is preserved at top level (`olymptrade_ws`), so internal absolute imports inside the vendored code work without modification; (d) in-place patching is required because the reverse-engineered protocol breaks often — vendoring = local patches, no PR round-trip; (e) deployment is a single `COPY . .` in the Dockerfile, no build-time git network calls or submodule init. The vendored LICENSE is preserved at `src/olymptrade_ws/LICENSE`. See §6 (Tech Stack), §7 (Architecture), §12.6 (license compliance), and `src/olymptrade_ws/VENDORED.md`.

### 13.2 Open questions

**None.** All decisions resolved. Build can start.

### 13.3 (Formerly here — resolved, kept as reference)

Both Q-5 and Q-11 are now resolved (see R-11 and R-12 above). The reasoning that drove each choice is preserved below for future readers.

- **Q-5 was driving:** `broker/olymp.py` (M8) — auto-discover needs a startup hook before `place_order`. Static map would have been a one-liner constant.
- **Q-11 was driving:** M11 (deployment runbook) — Railway uses Dockerfile + Volume; Hetzner would have used `systemd` + bare VPS.

### 13.4 Pair Mapping — Q5 (resolved: auto-discover)

**The problem:** your analyst writes `EUR/JPY` (slash notation, ISO-style). The vendored `olymptrade_ws` library expects **broker-internal pair strings** like `"EURUSD"`, `"USDJPY"`, `"LATAM_X"`, or asset IDs like `"EURUSD-OTC"` (these strings are what `olymptrade_ws.api.market.MarketAPI` returns from the e:1068 push). The slash form is never accepted by the broker — the tool has to translate.

**Resolved approach (a):** auto-discover at startup. `OlympTradeBroker.connect()` first runs `initialize_session()` (which subscribes to the broker's instrument listing), then waits for the e:1068 push that contains the full asset table. Builds a dict keyed by normalized slash-form (`EUR/JPY`, `EURJPY`, `EURJPY-OTC`, all collapse to the same key). On `place_order`, looks up the broker symbol + category from this map.

**Pair availability fallback:** if the signal asks for `USD/EGP` and the broker doesn't carry it, `OlympTradeBroker.place()` raises `UnsupportedPairError`. The state machine catches this, marks the signal as `error` with reason `unsupported_pair`, and DM-notifies the user. No crash, no missed trade.

---

## 14. Suggestions (improvements on top of the spec)

These are features/enhancements I'd recommend considering but did not include in v1 scope.

> **Deferred from v1 (per user feedback):**
> - Desktop notifications (would have used `plyer`) — not requested by user
> - SQLite persistence — no longer relevant (using PostgreSQL via R-13)
> - Real-money trading — gated behind demo soak test
> - Manual trade confirmation — deferred (auto-execute for v1)
> - Telegram sender-allowlist — not needed (channel is admin-only by design, R-14)

- **S-1. Web dashboard (v2).** Local Flask/FastAPI UI showing live state machine, recent trades, P/L chart. ~1 day of work.
- **S-2. Backtest mode (v2).** Replay a folder of saved Telegram messages through the state machine against historical OlympTrade candle data. Useful for validating the strategy before risking real money.
- **S-3. Strategy parameters per channel (v2).** Different channels can have different amounts, gale multipliers, daily limits.
- **S-4. Optional confirmation buttons (v1.1).** Telegram inline-keyboard `[✅ Confirm] [⛔ Skip]` in a self-DM, with 30s timeout. Useful for high-stakes sessions.
- **S-5. Self-healing reconnect for OlympTrade (v1.0).** The vendored `olymptrade_ws` library does NOT auto-reconnect on WS drop. Wrap it in a supervisor coroutine that detects drops and reconnects with backoff. Otherwise one network blip kills the tool until restart.
- **S-6. Token-refresh helper (v1.0).** A small script that opens a Playwright browser, logs into OlympTrade, extracts the JWT, and writes it to `.env`. Saves the manual DevTools step. ~30 minutes of work.
- **S-7. Tighter schedule precision (v1.0).** Use `time.monotonic_ns()` with a tight spin-loop for the last 50ms before trigger — `asyncio.sleep` can wake up to 15ms early on Windows Python 3.12. Not necessary on 3.13+.
- **S-8. Sound alerts (v1.1).** Play a `.wav` on WIN/LOSS so you don't need to watch the log.
- **S-9. State export (v1.1).** End-of-day CSV of trades + P/L written to `data/YYYY-MM-DD.csv` for spreadsheet analysis.
- **S-10. Multi-account support (v2).** Run on both demo and real in parallel to compare fills. Adds complexity (two brokers, two connections) — skip for v1.
- **S-11. Circuit breaker for repeated connection failures (v1.0).** If OlympTrade rejects 3 tokens in a row, halt and DM the user rather than hammering the server.
- **S-12. Photo/screenshot signal support (v2).** Some analysts post screenshots. OCR them with `pytesseract` and pipe through the same parser. Adds dependency + edge cases — skip unless requested.
- **S-13. Pre-flight broker validation (v1.0).** Before placing the first trade of the day, do a $1 dry-trade to confirm the broker connection is alive and the account is in `demo` mode (defense against accidentally trading real money).

---

## 15. Build Plan / Milestones

> Order is deliberately chosen so each milestone is independently testable.

| # | Milestone | Verifiable outcome |
|---|---|---|
| **M0** | Repo scaffold: `pyproject.toml`, ruff/mypy config, `.env.example`, README, `Dockerfile`, `railway.toml`, `.dockerignore`, `.python-version` | `pip install -e .` works; `pytest` runs (0 tests); `docker build` succeeds |
| **M1** | `domain/signal.py` + parser + unit tests for all message variants (happy path, missing semicolon, wrong emoji, ad-only, multi-signal) | `pytest tests/test_parser.py` passes; 100% line coverage on parser |
| **M2** | `domain/state.py` + `domain/gale.py` + state machine tests | State transitions tested with `pytest-asyncio`; gale math parametrized |
| **M3** | `broker/dry_run.py` + `Broker` Protocol | Dry-run broker logs intended trades; usable for end-to-end test |
| **M4** | `infra/db.py` + `migrations/001_initial.sql` (asyncpg pool, schema bootstrap, `StateStore` with `upsert_signal`, `record_stage`, `get_active_signals`, `update_daily_summary`) | Migrations run idempotently against a test PG (Docker); round-trip CRUD tested; `command_timeout` and connection-loss recovery tested |
| **M5** | `telegram/client.py` + `telegram/listener.py` | Connects to Telegram, parses real channel messages, dumps to stdout (no sender-allowlist, R-14) |
| **M6** | `scheduler/trigger.py` + `__main__.py` glue | On a test signal, fires a (dry-run) trade at HH:MM with ≤500ms skew |
| **M7** | `notify/telegram_dm.py` (rich notifications per §4.7 FR-7.1) | Self-DM fires for every event in the table; desktop notifications DEFERRED to v2 |
| **M8** | `broker/olymp.py` — wraps the **vendored** `olymptrade_ws.OlympTradeClient` (`from olymptrade_ws import OlympTradeClient`) + registers push callbacks + pair-mapping lookup (auto-discover, R-11) | Demo trade placed; result received via e:26 |
| **M9** | End-to-end test: real Telegram channel → dry-run broker cascade | Full pipeline tested with `DRY_RUN=true` for 24h |
| **M10** | Self-healing reconnect supervisor for OlympTrade WS | Kill network mid-trade; tool reconnects within 30s |
| **M11** | Railway deployment (Dockerfile, Postgres add-on provisioning, env-var setup, restart policy) + runbook in README | Tool runs unattended on Railway with PG; restart-on-crash works; data survives redeploys |

**Definition of Done for v1.0:** All M0–M11 milestones complete, 7-day soak test in `demo` with zero missed triggers and zero duplicate trades.

---

## 16. Glossary

- **Signal** — a structured trade instruction posted by the analyst in Telegram
- **Gale** — a recovery trade with multiplied stake (martingale-style)
- **Stage** — one of `initial`, `gale1`, `gale2` in the cascade
- **Trigger** — the moment a trade is placed (HH:MM)
- **Expiration** — the trade duration; 5 min = 300 seconds
- **MTProto** — Telegram's native protocol; required for user-account (vs. bot) access
- **Demo account** — OlympTrade practice account with virtual money
- **Real account** — OlympTrade account with real money; risk of financial loss
- **Serverless** — a cloud execution model where code runs in short-lived, stateless functions (e.g., AWS Lambda, Vercel Functions). Incompatible with persistent WebSocket connections and long-running event loops.
- **VPS** — Virtual Private Server. A persistent virtual machine that runs 24/7 with full OS access. The standard hosting model for tools like this one.
- **Container platform** — PaaS that runs Docker containers persistently (Railway, Fly.io, Render). Bridges serverless DX with persistent execution.
- **PostgreSQL** — open-source relational database with ACID transactions, SQL, and rich indexing. Used for state persistence (R-13) via `asyncpg` driver.
- **asyncpg** — async-native PostgreSQL driver for Python. Faster than `psycopg`/`psycopg2` for asyncio code; no DB-API overhead.
- **Connection pool** — a managed set of reusable DB connections (`asyncpg.create_pool`). Eliminates per-query connection setup; handles concurrency.
- **Railway Postgres add-on** — Railway's one-click managed PostgreSQL service. Auto-injects `DATABASE_URL` into linked services; backups, restarts, and scaling are managed.
- **Migration** — a versioned SQL change applied to the DB schema. v1 uses idempotent `CREATE TABLE IF NOT EXISTS` only; v2 will introduce a real migration tool (e.g., `yoyo-migrations`).

---

## 17. Hosting — Railway.app (confirmed)

> **Locked decision:** this tool runs as a **persistent Docker container on Railway.app**. **Do NOT use Vercel** — see §17.1.

### 17.1 Why Vercel does NOT work

Vercel is a **serverless platform**: code runs in short-lived, stateless "Vercel Functions." This design is fundamentally incompatible with what this tool needs:

| Requirement | Vercel reality |
|---|---|
| Persistent WebSocket to Telegram (Telethon MTProto) | ❌ Function execution capped at 10s (Hobby) / 60s (Pro) / 900s (Pro with `maxDuration`). Telethon needs a connection that lives for hours/days. |
| Persistent WebSocket to OlympTrade (vendored `olymptrade_ws`) | ❌ Same. Trade result arrives via push event `e:26` seconds-to-minutes later; the function would be torn down before then. |
| Continuous event loop | ❌ Vercel functions scale to zero when not invoked. There's no "always-on" event loop. |
| Sub-second scheduling precision (`loop.call_at` for HH:MM:00) | ❌ Cold starts (100ms–2s) make timing guarantees impossible. |
| In-memory state between triggers | ❌ Serverless functions are stateless across invocations; you'd need an external DB anyway. |
| 24/7 unattended operation | ❌ Vercel does offer "Cron Jobs" and "Long-running functions" but neither matches the always-on websocket pattern. |

**Conclusion:** this tool cannot be hosted on Vercel without rewriting it as a request/response web service that polls an external scheduler. Out of scope for v1.

### 17.2 Why Railway.app fits

Railway.app is a **container platform**: each service runs as a persistent Docker container with full OS access and an always-on event loop. Pricing is usage-based — ~$5/mo for a small Python service after the $5/mo free credit.

| Requirement | Railway fits |
|---|---|
| Persistent WebSocket connections | ✅ Container runs 24/7 |
| Continuous event loop | ✅ Single process, no scaling-to-zero |
| Sub-second scheduling | ✅ Cold starts are absent for an existing container; restarts are infrequent |
| Persistent state | ✅ Via Railway **Postgres add-on** (managed database, separate from container) — state survives container restarts and redeploys |
| Easy deploys | ✅ `git push` → auto-deploy; or `railway up` from CLI |
| Secrets management | ✅ Env vars in Railway dashboard; encrypted at rest |
| Logs | ✅ `railway logs` (live tail) or dashboard |

### 17.3 Railway deployment shape

```
┌──────────────────────────────────────────────────────────────────┐
│ Railway Service: "signal-copier"                                 │
│                                                                   │
│  Source:  GitHub repo (auto-deploy on push to main)              │
│           OR  `railway up` from CLI                               │
│  Runtime: Docker (we provide a Dockerfile)                        │
│  Start:   python -m signal_copier                                │
│  Restart: ON_FAILURE (auto-restart on crash)                     │
│  Env:     DATABASE_URL  ← auto-injected by Postgres add-on      │
│                                                                   │
│  ┌─ Filesystem: ephemeral ──────────────────────────────────────┐ │
│  │  /app/logs/   ← rotating log files (LOG_PATH)                │ │
│  │  NOTE: no state.json, no Volume — state lives in Postgres    │ │
│  └──────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
                              │
                              │ DATABASE_URL (auto-injected)
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│ Railway Postgres: "signal-copier-db"                             │
│                                                                   │
│  Engine: PostgreSQL 16                                           │
│  State: signals, stages, daily_summary (3 tables, see §9)        │
│  Backups: daily (managed by Railway)                             │
│  Connection: via asyncpg pool (min 2 / max 10 connections)       │
└──────────────────────────────────────────────────────────────────┘
```

### 17.4 Railway-specific files in the repo

| File | Purpose |
|---|---|
| `Dockerfile` | Python 3.13 image; copies `src/`, `pyproject.toml`, `migrations/`; runs `pip install` then `python -m signal_copier`. **Must `COPY src/olymptrade_ws/LICENSE`** with the rest of the source (MIT license compliance — see §12.6). |
| `railway.toml` | Railway service config: `builder = "DOCKERFILE"`, `restartPolicyType = "ON_FAILURE"`, `restartPolicyMaxRetries = 10` |
| `.dockerignore` | Excludes `.env`, `.git/`, `tests/`, `__pycache__/`, `logs/` |
| `.python-version` | Pins Python 3.13.x for Nixpacks fallback (Dockerfile overrides) |
| `migrations/001_initial.sql` | Schema DDL — runs idempotently at startup (see §9) |

### 17.5 Railway Postgres add-on — provisioning (M11 deliverable)

Railway provides managed PostgreSQL with one click. Setup:

1. In the Railway project dashboard, click **+ New** → **Database** → **PostgreSQL**.
2. Railway creates the database and auto-injects a `DATABASE_URL` env var into any service in the same project.
3. Confirm: open the `signal-copier` service → **Variables** tab → `DATABASE_URL` should appear with reference syntax `${{Postgres.DATABASE_URL}}`.
4. No schema setup needed: the app runs `migrations/001_initial.sql` on every startup (idempotent `CREATE TABLE IF NOT EXISTS`).
5. To inspect the DB during development: click the Postgres service → **Data** tab → query editor, or connect via `psql` using the connection string.

**Why no Volume needed:** state lives in the managed Postgres service, which is independent of the container's lifecycle. Restarting, redeploying, or recreating the `signal-copier` service never loses data. The only thing in the container filesystem is logs (which we can ignore — Railway captures stdout to its log stream anyway).

### 17.6 Local development vs Railway run

The same code runs locally and on Railway. `python -m signal_copier` works on:
- **Windows 10/11 (Python 3.13+)** — local development & testing
- **Linux container (Python 3.13)** — production on Railway

Database setup for local development:
- **Option A (Docker):** `docker run -d -p 5432:5432 -e POSTGRES_USER=copier -e POSTGRES_PASSWORD=copier -e POSTGRES_DB=copier postgres:16-alpine`
- **Option B (free hosted):** Neon / Supabase / Railway (dev plan) free-tier Postgres — paste the connection string into `.env` as `DATABASE_URL`.

Differences in deployment:
- **Local:** terminal window open; SIGINT (Ctrl+C) to stop.
- **Railway:** container runs as PID 1; `railway logs --tail` to follow logs; auto-restart on non-zero exit.

### 17.7 First-deploy runbook (M11 deliverable)

1. **Create Railway project** at <https://railway.app> → New Project → Deploy from GitHub repo (or empty + `railway init`).
2. **Add Postgres**: in the project dashboard → **+ New** → **Database** → **PostgreSQL**. Railway auto-injects `DATABASE_URL`.
3. **Set environment variables** on the `signal-copier` service → Variables tab (copy from `.env.example`; never commit a populated `.env`). Required: `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_PHONE`, `OLYMP_ACCESS_TOKEN`, `OLYMP_ACCOUNT_ID`. `DATABASE_URL` is auto-injected from the Postgres service.
4. **First deploy** will start the container, run migrations on PG, and fail with "Telegram not authorized" because there's no `TELEGRAM_SESSION_STRING` yet. This is expected — see step 5.
5. **One-time interactive auth** (local only): run `python -m signal_copier.auth` locally. This prompts for phone + code + 2FA, prints the resulting `StringSession`, you paste it into the Railway env var, redeploy.
6. **Verify** by checking Railway logs for `Bot started` and the Telegram self-DM confirming asset map resolution (from auto-discover, R-11). Check the Postgres service's Data tab to confirm tables exist (`signals`, `stages`, `daily_summary`).

### 17.8 Connection requirements

The host must allow outbound HTTPS/WSS:
- `wss://ws.olymptrade.com/otp` (port 443) — OlympTrade WebSocket
- `*.web.telegram.org` (port 443) — Telegram MTProto
- `*.railway.app` (port 5432) — Railway Postgres (outbound from container to Railway's internal network; usually automatic)
- Outbound DNS, NTP (for clock sync — **critical** for trigger precision; Railway containers use the host's NTP, which is reliable)

Inbound: nothing. The container makes only outbound connections. No firewall holes needed.

### 17.9 Cost projection

| Item | Cost |
|---|---|
| Railway Hobby plan | $5/mo (includes $5 usage credit) |
| Signal-copier service usage | ~$1–$3/mo (small container, low CPU) |
| Railway Postgres (Hobby tier) | **~$1–$2/mo** (256 MB RAM, 1 GB disk — far more than this app needs) |
| Net monthly cost after credit | **~$1–$3/mo** (depending on usage) |
| Annual estimate | **~$12–$36/yr** |

This is the cost-cheapest easy option. Hetzner at €3.49/mo would be ~$42/yr flat but requires manual setup + Postgres management.

---

## 18. Changelog

Significant edits that change the source of truth. Minor copy-edits are not logged.

### v0.7 — `olymptrade_ws` vendored in-tree

- **R-15 (new):** Third-party broker client `olymptrade_ws` is now **vendored** at `src/olymptrade_ws/` instead of being a separate `./OlympTradeAPI/` clone or installed package. MIT-licensed (Chipa, 2025). See §6, §7, §12.6, §13.1 R-15, and `src/olymptrade_ws/VENDORED.md`.
- **Header (line 5):** "Target broker" updated to reflect vendored layout.
- **§3 (User Flow):** Trade Executor label updated.
- **§4.4 FR-4.1:** Import path updated — `from olymptrade_ws import OlympTradeClient` resolves from the vendored `src/olymptrade_ws/`.
- **§4.4 FR-4.5:** `OlympTradeBroker` description now references the vendored module explicitly and notes that no vendored source is modified.
- **§6 (Tech Stack):** "Broker" row updated to reflect vendored path + MIT license + VENDORED.md reference.
- **§7 (Architecture):** Directory tree now includes `src/olymptrade_ws/` with a "do not edit" marker, and a callout below the tree restating the edit rule.
- **§10 (Error Handling):** "No auto-reconnect" line now references the vendored package.
- **§12.6 (new):** Full license-compliance and operational rules for the vendored code (attribution, edit prohibition, modification log, re-vendoring workflow).
- **§13.1 R-15 (new):** Vendoring decision recorded with full rationale.
- **§13.2 header:** "Two architectural items still need a decision" — line removed (all decisions resolved through R-15).
- **§13.4 (Pair Mapping Q5):** "OlympTradeAPI library" → "vendored `olymptrade_ws` library"; clarified that broker-internal strings are what `olymptrade_ws.api.market.MarketAPI` returns.
- **§14 S-5:** "OlympTradeAPI library" → "vendored `olymptrade_ws` library".
- **§15 M8 (Build Plan):** "wraps `OlympTradeClient`" → "wraps the vendored `olymptrade_ws.OlympTradeClient` (`from olymptrade_ws import OlympTradeClient`)".
- **§17.1 (Hosting comparison):** "OlympTradeAPI" → "vendored `olymptrade_ws`".
- **§17.4 (Dockerfile row):** Added requirement to `COPY src/olymptrade_ws/LICENSE` (MIT compliance).

### v0.8 — M10 self-healing OlympTrade reconnect supervisor

- **M10 complete.** New `ReconnectingOlympTradeBroker` wrapper at `src/signal_copier/broker/reconnect.py` detects WS drops via 1s polling watcher + event-driven `place/wait_result` ConnectionError path. On disconnect, runs exponential-backoff reconnect loop (1s → 30s cap, max 5 attempts). On exhaustion: `BrokerAuthError` → `__main__` exit-2 → Railway restart. In-flight cascades end with `error_reason='broker_unavailable'` (existing M6 mapping).
- **Notifier Protocol +3 methods**: `on_olymp_reconnecting`, `on_olymp_reconnected`, `on_olymp_reconnect_failed`. `TelegramDMNotifier` implements with FR-7.1-aligned DM copy. `on_olymp_disconnect` copy softened from "Process will exit; supervisor will restart" to "Reconnecting…".
- **Test surface**: 13 supervisor tests in `tests/test_reconnect_supervisor.py` (3 protocol-satisfaction, 3 connect lifecycle, 4 event-driven + watcher, 3 circuit breaker); 3 new NoOpNotifier tests; 3 new TelegramDM tests; extended `RecordingNotifier`; new `FakeClientFactory` fixture.
- **Spec**: `docs/superpowers/specs/2026-06-23-m10-olymptrade-reconnect-supervisor-design.md`. Plan: `docs/superpowers/plans/2026-06-23-m10-olymptrade-reconnect-supervisor.md`. No edits to vendored `olymptrade_ws` (R-15).

### v0.9 — M11 Railway deployment, runbook & project license

- **M11 complete.** The operational layer for shipping the tool as an unattended Railway service: GitHub Actions CI/CD (5 jobs: lint, format, typecheck, test with PG service container, deploy-on-push-to-main), interactive `python -m signal_copier.telegram.auth` helper (now with Railway-detection guard, `get_me()` session verification, and rich output banner with security warning), `docker-compose.yml` for local Postgres dev, complete README runbook (First-time setup, Local development, Operations, Verify the deployment, Troubleshooting), and **PolyForm Strict 1.0.0** as the project license (closes the "Project license TBD" hole from the README).
- **M11 spec:** `docs/superpowers/specs/2026-06-23-m11-railway-deployment-design.md`. Plan: `docs/superpowers/plans/2026-06-23-m11-railway-deployment.md`. No edits to vendored `olymptrade_ws/` (R-15).
- **License:** PolyForm Strict 1.0.0 — free use/modify/distribute; no sale of the work or any derivative. See `LICENSE` and the License section in the README. Compatible with the vendored `olymptrade_ws` MIT license; both license texts are present in the repo and `COPY`'d into the Docker image.

### v0.10 — M12 type & format cleanup

- **M12 complete.** All 229 pre-existing mypy errors resolved via proper type annotations across 15 test files. Dead `[[tool.mypy.overrides]]` block removed from pyproject.toml (the `module` field used bare names that didn't match `tests.test_*`); new override added for `asyncpg` + `testcontainers` (these libs don't ship py.typed markers), which allowed removing 2 pre-existing `# type: ignore[import-untyped]` comments from `src/`. 5 test files reformatted with `ruff format`. CI's `typecheck` and `format` jobs pass; auto-deploy to Railway works end-to-end for the first time.
- **Optional**: src/ weak-point sweep tightened 1 item per spec §7 (cast/Any/type: ignore/public API).
- **M12 spec:** `docs/superpowers/specs/2026-06-23-m12-type-and-format-cleanup-design.md`. Plan: `docs/superpowers/plans/2026-06-23-m12-type-and-format-cleanup.md`. No edits to vendored `olymptrade_ws` (R-15).

### v0.6 — Strict time-window enforcement

- Strict time-window enforcement across all 3 stages (FR-3.3, FR-3.6, FR-5.9) — a missed fire time on any stage ends the cascade with `error (signal_expired)`; no retry, no shifting, no skip-to-next-stage.
- (Earlier v0.x history not preserved here — see git log.)

---

*End of PRD v0.7 — all decisions resolved (R-1 through R-15); `olymptrade_ws` vendored; ready to build (M0 scaffold).*