# MT5 Refactor — End-to-End Setup Guide

> **Goal:** swap the trading broker from OlympTrade (vendored `olymptrade_ws`) to MetaTrader 5 (`mt5linux` client + Wine-headless server) end to end — GitHub repo rename, Railway project rename, full code refactor, broker account setup, deploy, and first demo trade.

---

## 0. Status, scope, and success criteria

| Field | Value |
|---|---|
| Status | **PROPOSED — no code yet.** Implementation gated on Section 1 decisions. |
| Scope | Broker layer only. Telegram listener, parser, state machine, scheduler, DB, notifier, recovery — all kept. |
| Out of scope (v2+) | Multiple brokers, real-money trading, strategy optimization, multi-channel, Web UI. |
| Success criteria | A demo signal arrives → MT5 demo account opens and closes a 0.01-lot position within the signal's expiration → user receives a Telegram DM with `✅ WIN` or `❌ LOSS`, the Daily Summary row updates, and a restart mid-cascade resumes cleanly. |

---

## 1. Decisions to make before any code is written

These four items are blocking. Settle them once, lock them into Section 6's `.env`, and don't change them casually (each change after deploy means a Railway redeploy + local restart).
### 1.1 MT5 broker choice — **LOCKED: VT Markets**

- **Broker:** [VT Markets](https://www.vtmarkets.com/) (MT5, ASIC-regulated)
- **Demo server name:** `VTMarkets-Demo` *(confirmed from VT Markets welcome email / MT5 terminal server list)*
- **Demo login:** numeric 7-8 digit, e.g. `10012345`
- **Pair coverage:** all majors + most crosses (EUR/USD, EUR/JPY, EUR/GBP, GBP/USD, GBP/JPY, USD/JPY, USD/CHF, USD/CAD, AUD/USD, NZD/USD, plus cross pairs)
- **Supports automated trading on demo:** yes. Enable via `MT5 → Tools → Options → Expert Advisors → ☑ Allow algorithmic trading`.

**Why VT Markets:**
- Standard MT5 broker — no custom protocol, no reverse-engineering.
- Demo account is free, no expiry on inactivity (some brokers' demos expire after 30-90 days).
- `mt5linux` client lib works against VT Markets the same way as it works against any standard MT5 broker — no broker-specific code paths.
- Lot sizing proposal in 1.3 below is valid (micro-lots supported).

### 1.2 Demo server URL — **LOCKED: `VTMarkets-Demo`**

Confirmed: VT Markets demo server string is `VTMarkets-Demo` (verbatim from the welcome email / MT5 terminal server list).

- Value for `.env` and Railway env vars: `MT5_SERVER=VTMarkets-Demo`
- The guardrail at `src/signal_copier/config.py:_validate_demo_server` (Section 4.6) accepts this because `"demo"` is a case-insensitive substring match.

The rest of the refactor is broker-agnostic and proceeds identically to any other MT5 demo broker once `MT5_SERVER=VTMarkets-Demo` is set.
### 1.3 Lot sizing (mapped from current $ amounts)
The current code uses stage amounts `$2 / $4 / $8`. Translated to MT5 lots (this is the new default; change only if your broker's micro-lot requirements differ):

| Stage | Current $ amount | New lot size | USD notional on EUR/USD @ 1.10 |
|---|---|---|---|
| initial | $2.00 | `0.01` | ≈ $1.10 pip × 100,000 × 0.01 = $11.00/0.0001 |
| gale1  | $4.00 | `0.02` | ≈ $22.00/0.0001 |
| gale2  | $8.00 | `0.04` | ≈ $44.00/0.0001 |

> **Why not literal $ → lots?** OlympTrade binary options = fixed USD payout. MT5 = leveraged FX lot. There is no clean 1:1 mapping. The above is a sensible "same risk profile" default. Adjust after a few demo trades.

### 1.4 Default expiration if the signal doesn't include a `💰N-minute expiration` line
Parser already requires that line (per PRD FR-2.2). If it's missing → ParseFailure (`MISSING_HEADER_LINE`). So **no new behavior** is needed; your current parser is strict.

---

## 2. GitHub repository rename

Current repo name (on disk and on GitHub): `olymptrade` (folder path `/home/user/olymptrade`).

> **LOCKED: `telegram-mt5-copier`** for both the GitHub repo name and the Railway project name. The same name is also recommended for the Docker image tag and the Railway Service A name (Section 3.2).

### 2.1 Rename on GitHub
1. Go to <https://github.com/<you>/olymptrade> → **Settings** → **General** → **Repository name** → change to `telegram-mt5-copier` → **Rename**.
2. GitHub redirects the old name automatically (HTTP 301 on clone URLs). Existing clones still work via the redirect.

### 2.2 Update your local clone
```bash
# From your local checkout
git remote set-url origin https://github.com/<you>/telegram-mt5-copier.git

# Verify
git remote -v
# origin  https://github.com/<you>/telegram-mt5-copier.git (fetch)
# origin  https://github.com/<you>/telegram-mt5-copier.git (push)
```

### 2.3 Find/replace in tracked files (these are all the textual references — see Appendix A for the full list)

```bash
# From project root
rg -l 'olymptrade|OlympTrade|OLYMP_' --hidden --glob '!.git' --glob '!OlympTradeAPI' --glob '!API-Quotex'
```

Open each file and edit. **Do NOT do this with a one-shot script** — every change has a context-dependent decision (e.g., README narrative vs. config field). See Appendix A for the per-file mapping.

---

## 3. Railway project rename

### 3.1 Rename the project
1. In Railway dashboard → click your project → **Settings** (right column) → **Project Name** → change to `telegram-mt5-copier`.
2. URL `railway.app/project/<old-id>` stays the same; only the human-readable name changes. No service downtime.

### 3.2 Rename the existing service
1. Inside the project → click the `signal-copier` service → **Settings** → **Service Name** → `telegram-mt5-copier`.
2. **Important:** if any env var references the service by name (e.g., `RAILWAY_SERVICE_NAME`), update those too. `${{Postgres.DATABASE_URL}}` resolves by service ID, **not** name, so that's safe.

### 3.3 (Later) add a second service for mt5linux-server
Section 5 adds a new Dockerfile. After the code refactor lands, you'll create **Service B** (`mt5linux-server`) in the same Railway project from the same GitHub repo. Until then the project has only the renamed Service A.

---

## 4. Code refactor

### 4.1 Files to delete

| Path | Why |
|---|---|
| `src/olymptrade_ws/` (entire tree) | Vendored third-party (PRD R-15). Replaced by `mt5linux` PyPI package. |
| `OlympTradeAPI/` (sibling checkout at repo root) | Already in `.gitignore:53`. Local reference for diffs. Delete after you confirm `src/olymptrade_ws/` removal doesn't lose anything. |
| `API-Quotex/` (sibling checkout) | Unrelated reference checkout. Not in `.gitignore` — add it (Section 6.3). |
| `src/signal_copier/broker/olymp.py` | Replaced by `broker/mt5.py`. |
| `src/signal_copier/broker/reconnect.py` | Replace with broker-agnostic or MT5-specific reconnect wrapper. |

### 4.2 Files to modify

| Path | Change |
|---|---|
| `pyproject.toml` | Line 4 — description; Line 9-16 — replace vendored socket deps with `mt5linux`; Line 8 — keep PolyForm Strict license (still compatible). |
| `README.md` | Title, every `OlympTrade` mention, the runbook (Section: "First-time setup" → MT5 broker), "Third-party dependency — vendored" → "Third-party dependency — pip package". |
| `.env.example` | Replace `OLYMP_*` block (lines 12-17) with `MT5_*` block. See Section 6. |
| `src/signal_copier/config.py` | Lines 31-34: drop `olymp_access_token / olymp_account_group / olymp_account_id`; add `mt5_login / mt5_password / mt5_server / mt5_terminal_path`. Lines 68-83: replace `_validate_account_group` and `_demo_only_guardrail` with `_validate_demo_server` that refuses to start if `mt5_server` (case-insensitive) does not contain `demo`. |
| `src/signal_copier/__main__.py` | Lines 49-56, 95-111, 232-234: replace OlympTrade validation + broker selection block with MT5 logic. See Section 4.7. |
| `src/signal_copier/notify/protocol.py` | Lines 126-161: rename `on_olymp_disconnect` / `on_olymp_reconnecting` / `on_olymp_reconnected` / `on_olymp_reconnect_failed` → `on_broker_disconnect` / `on_broker_reconnecting` / `on_broker_reconnected` / `on_broker_reconnect_failed`. Update docstring references (currently cite "M8/M10"). |
| `src/signal_copier/notify/telegram_dm.py` | Lines 319-323, 325-365: rename methods; update DM text — "OlympTrade" → "broker"/"MT5" depending on context. |
| `src/signal_copier/domain/state.py` | Lines 142-154: drop the `* Decimal("0.92")` binary-payout approximation. The MT5 broker will pass the broker-reported PnL through `Broker.close(trade_id) -> Decimal`; the state machine accepts PnL as a parameter on `ResultEvent` (Protocol change, see Section 4.4). |
| `src/signal_copier/scheduler/trigger.py` | Lines 444-505: replace `_drive_cascade` step `(d) place → (e) wait_result` with `(d) place → (d') schedule close at expiration → (e) call_close → resolve result + PnL`. See Section 4.4. |
| `docs/PRD.md` | Header line 5; FR-4.1-4.6; §4.7 reconnect events (DR/RR/RF labels); §6 tech stack; §7 tree (remove `olymptrade_ws/`, add `broker/mt5.py`); §10 reconnect row; §12.6 (delete); §13.1 R-5 / R-6 / R-15 (rework); §15 M8/M10; §17.1 hosting comparison. |
| `docs/refactor.md` | This file. Keep it next to PRD as the migration log. |
| `.gitignore` | Line 53 keeps `OlympTradeAPI`; add `API-Quotex/` adjacent. |

### 4.3 Files to create

| Path | Purpose |
|---|---|
| `src/signal_copier/broker/mt5.py` | New MT5 broker implementation. Same shape as `olymp.py` (connect → place → wait_result → close) plus the `close(trade_id, *, pnl)` method. Uses `mt5linux` client. ~280 lines. |
| `src/signal_copier/broker/reconnect.py` | MT5-flavored reconnect. MT5's TCP socket is more cooperative than the reverse-engineered WS, so the wrapper is ~150 lines (vs the current 293). Exponential backoff reuses `compute_backoff_seconds` from the deleted file. |
| `mt5linux/Dockerfile` | Wine + MT5 + mt5linux-server image. See Section 5. ~60 lines. |
| `mt5linux/entrypoint.sh` | Starts `xvfb-run` (virtual display), launches MT5 terminal headless, waits for `~/.mt5/config.json` to indicate the account is loaded, then starts `mt5linux-server`. ~30 lines. |
| `mt5linux/requirements.txt` | `mt5linux-server==X.Y.Z` pinned. |
| `tools/mt5_preflight.py` | Runnable sanity check — opens the broker connection, prints account info, closes. Used for first-deploy verification. ~80 lines. |
| `docs/superpowers/specs/<date>-mt5-broker-swap-design.md` | Design spec (mirror the existing `2026-06-21-m8-olymptrade-broker-design.md` shape). |
| `docs/superpowers/plans/<date>-mt5-broker-swap.md` | Implementation plan (mirror `2026-06-21-m8-olymptrade-broker.md`). |

### 4.4 Broker Protocol design decision — add `close(trade_id)`

This is the **one Protocol change** the refactor introduces. Current Protocol (PRD FR-4.4):

```python
class Broker(Protocol):
    async def connect(self) -> None: ...
    async def place(self, signal: Signal, *, stage: Stage, amount: Decimal) -> str: ...
    async def wait_result(self, trade_id: str, *, timeout: float) -> StageResult: ...
    async def close(self) -> None: ...
```

OlympTrade has a built-in expiration (binary options close themselves). MT5 does not — we open a position and explicitly close it later. To keep the broker shape symmetric, **add** one method:

```python
async def close_position(self, trade_id: str, *, timeout: float) -> Decimal:
    """Close an open position identified by trade_id.

    Returns the broker-reported realized PnL (Decimal, signed).
    The scheduler uses this PnL for record_stage_result (overrides the
    state machine's binary-payout approximation in domain/state.py:142).

    For OlympTrade-backed implementations, this is a no-op + return 0
    (binary options closed themselves before wait_result returned).
    """
```

Protocol change scope: 8 lines in `broker/base.py`. No callers need to change beyond the one place that calls it (`scheduler/trigger.py:_drive_cascade`).

Updated scheduler pattern (pseudo-code, replaces lines 446-505):

```python
# d. Open the position at scheduled time.
broker_trade_id = await self._broker.place(signal, stage=stage, amount=lots)

# d'. Schedule the auto-close at trigger_unix + expiration_seconds.
#     Same call_at pattern as the open — the scheduler already owns this loop.
loop.call_at(compute_target_monotonic(state.expires_at_unix),
             auto_close_future.set_result, True)

# e. Wait for either:
#     - the broker's result event (OlympTrade push semantics), OR
#     - the auto-close deadline (MT5 semantics, triggers close_position).
#     Whichever fires first wins.
done, pending = await asyncio.wait(
    {result_future, auto_close_future},
    timeout=state.expires_at_unix - now_unix() + RESULT_GRACE,
    return_when=asyncio.FIRST_COMPLETED,
)

if result_future in done:
    stage_result = await result_future
    pnl = broker._broker_reported_pnl  # set in push callback
elif auto_close_future in done:
    pnl = await asyncio.wait_for(
        self._broker.close_position(broker_trade_id, timeout=5.0),
        timeout=5.0,
    )
    stage_result = "win" if pnl > 0 else "loss" if pnl < 0 else "tie"
```

### 4.5 PnL handling — broker-reported, not approximated

The current state machine has at `domain/state.py:142-154`:

```python
def _stage_pnl(state, result):
    if result == "win":
        return state.amount * Decimal("0.92")      # ← OlympTrade binary payout
    if result in {"loss", "tie", "timeout"}:
        return -state.amount
```

OlympTrade's `place_order` returns an order with a fixed ~92% payout. MT5 fills have a `.profit` field filled by the broker — never approximate; trust it.

After the refactor:
- `domain/state.py:_stage_pnl` is removed.
- `ResultEvent` gains a `pnl: Decimal | None = None` field.
- The PnL flow is: broker reports → `Broker.close_position` returns `Decimal` → `scheduler/trigger.py:_apply_result_and_finalize` (line 540) writes `record_stage_result(trade_id, result, pnl=..., closed_at_unix=...)`. The state machine trusts whatever Decimal it receives.
- For `result = "win"` with `pnl < 0` (rare but possible — slippage at close), we still call it a win; the PnL is what it is. The `Daily Summary` row reflects the actual realized value.

### 4.6 Config — field-level changes

`src/signal_copier/config.py` lines 31-34 become:

```python
# --- MT5 broker (M13 — replaces OLYMP_* block) ---
mt5_login: int = 0                       # numeric account login
mt5_password: str = ""                   # account password
mt5_server: str = ""                     # broker server name (must contain "demo")
mt5_terminal_path: str | None = None     # optional: explicit path to MT5 terminal64.exe
                                         # only needed if auto-discovery fails on Linux/Wine
```

`src/signal_copier/config.py:68-83` becomes:

```python
@field_validator("mt5_server")
@classmethod
def _validate_demo_server(cls, v: str) -> str:
    # FR-6.6 equivalent for MT5: refuse any non-demo server. Real-account
    # login + real server = financial loss. The check is intentionally
    # strict (case-insensitive substring "demo" in server name).
    if "demo" not in v.lower():
        raise ValueError(
            f"mt5_server must be a demo server (contain 'demo'); got {v!r}. "
            f"Real-money trading is a v2 feature gated behind a clean demo soak test."
        )
    return v
```

This replaces `_validate_account_group` and `_demo_only_guardrail` with a single check at config-load time.

### 4.7 `__main__.py` — broker selection block

`src/signal_copier/__main__.py:95-111` becomes:

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

Validation at lines 49-56 changes from "OLYMP_ACCESS_TOKEN empty" to "MT5_LOGIN == 0 or MT5_PASSWORD empty or MT5_SERVER empty". Same exit code 2.

### 4.8 Notifier rename — `on_olymp_*` → `on_broker_*`

`src/signal_copier/notify/protocol.py:126-161` and corresponding implementations in `telegram_dm.py:319-365`. The four methods are renamed, and their DM text changes:

| Old | New |
|---|---|
| `on_olymp_disconnect` | `on_broker_disconnect` |
| `on_olymp_reconnecting` | `on_broker_reconnecting` |
| `on_olymp_reconnected` | `on_broker_reconnected` |
| `on_olymp_reconnect_failed` | `on_broker_reconnect_failed` |

DM text changes:
- `"🔌 OlympTrade disconnected. Reconnecting…"` → `"🔌 Broker disconnected. Reconnecting…"`
- `"🔁 OlympTrade reconnecting (attempt X/Y)…"` → `"🔁 Broker reconnecting (attempt X/Y)…"`
- `"✅ OlympTrade reconnected"` → `"✅ Broker reconnected"`
- `"❌ OlympTrade reconnect failed after X attempts"` → `"❌ Broker reconnect failed after X attempts"`

For MT5 the "broker" is generic. If you want to be more specific, you can use "MT5" instead — but the abstraction is now broker-agnostic, so the generic wording is the better long-term call.

---

## 5. mt5linux Dockerfile (Service B on Railway)

> Railway service name: `mt5linux-server`. Code path: `mt5linux/` at the repo root. This is a separate Dockerfile from the signal-copier's main one.

### 5.1 Why not just `pip install MetaTrader5`?
The official `MetaTrader5` PyPI package is a C extension that links against Windows DLLs. It does **not** work on Linux, even with Wine, even with patches. `mt5linux` is a community-maintained Python alternative that talks to a headless MT5 terminal via TCP.

### 5.2 Directory layout

```
mt5linux/
├── Dockerfile           # ~60 lines, ubuntu + wine + mt5 + server
├── entrypoint.sh        # ~30 lines, runs mt5linux-server inside Wine
├── requirements.txt     # mt5linux-server==X.Y.Z
└── README.md            # how to bake the .mt5 config in
```

### 5.3 `mt5linux/Dockerfile`

```dockerfile
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV WINEDEBUG=-all
ENV DISPLAY=:99

# Wine + Xvfb (virtual display) + Python 3 for mt5linux-server
RUN apt-get update && apt-get install -y --no-install-recommends \
        wget xvfb python3 python3-pip cabextract \
    && rm -rf /var/lib/apt/lists/*

# Wine (staging branch, latest stable)
RUN dpkg --add-architecture i386 && apt-get update \
    && apt-get install -y --no-install-recommends winehq-stable \
    && rm -rf /var/lib/apt/lists/*

# MetaTrader5 terminal installer (the official .exe)
# Pin a specific build; update on MT5 platform upgrades (Section 11.x).
ARG MT5_BUILD=5070
RUN wget -q "https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe" \
       -O /tmp/mt5setup.exe \
    && xvfb-run -a wine /tmp/mt5setup.exe /auto \
    && rm /tmp/mt5setup.exe

WORKDIR /mt5linux
COPY mt5linux/requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Pre-baked MT5 config: account login, server, chart-preset.
# This is built at image-build time from build args (see README.md).
ARG MT5_LOGIN
ARG MT5_SERVER
RUN mkdir -p /root/.mt5 && \
    echo "host=localhost" > /root/.mt5/config.ini && \
    echo "port=8001"       >> /root/.mt5/config.ini && \
    echo "login=${MT5_LOGIN}"     >> /root/.mt5/config.ini && \
    echo "server=${MT5_SERVER}"   >> /root/.mt5/config.ini

COPY mt5linux/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 8001
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
```

### 5.4 `mt5linux/entrypoint.sh`

```bash
#!/bin/bash
set -e

# Start virtual display for Wine
Xvfb :99 -screen 0 1024x768x24 &

# Wait for the display
sleep 2

# Run MT5 terminal headless under Wine (it logs itself in via /root/.mt5/)
cd /root/.wine/drive_c/Program\ Files/MetaTrader\ 5
wine terminal64.exe /portable /config:"/root/.mt5" &

# Wait for MT5 to come up and listen on its socket
echo "[entrypoint] Waiting for MT5 to be reachable..."
for i in {1..60}; do
    if (echo > /dev/tcp/localhost/8001) 2>/dev/null; then
        echo "[entrypoint] MT5 listening on port 8001"
        break
    fi
    sleep 2
done

# Start the mt5linux-server (talks MT5 ↔ Python clients)
echo "[entrypoint] Starting mt5linux-server..."
exec python3 -m mt5linux.server --host 0.0.0.0 --port 8001
```

### 5.5 Signal-copier side: `src/signal_copier/broker/mt5.py` connection

```python
import mt5linux as mt5  # drop-in for the official MetaTrader5 package

class Mt5Broker:
    def __init__(self, *, login: int, password: str, server: str,
                 terminal_path: str | None, notifier: Notifier):
        self._login = login
        self._password = password
        self._server = server
        self._notifier = notifier

    async def connect(self) -> None:
        # mt5.initialize() is blocking; run in a thread.
        ok = await asyncio.to_thread(
            mt5.initialize,
            path=self._terminal_path,
            server=self._server,
            login=self._login,
            password=self._password,
        )
        if not ok:
            err = await asyncio.to_thread(mt5.last_error)
            raise BrokerAuthError(f"mt5.initialize failed: {err}")
        ...
```

Connection target: `mt5.initialize(path=None, server="ICMarkets-Demo01", ...)` resolves to the MT5 terminal running inside Service B via TCP. The actual MT5 socket is set up automatically by `mt5linux`'s `__init__`. See the spec doc for details.

---

## 6. Config / .env migration

### 6.1 `.env.example` — old block (lines 12-17)
```bash
# --- OlympTrade ---
OLYMP_ACCESS_TOKEN=replace_me
OLYMP_ACCOUNT_GROUP=demo
OLYMP_ACCOUNT_ID=
```

### 6.2 `.env.example` — new block
```bash
# --- MetaTrader 5 (M13 — replaces OLYMP_*) ---
# Numeric account login (e.g., 12345678). Demo logins only.
MT5_LOGIN=00000000
# Account password (treat as secret).
MT5_PASSWORD=replace_me
# Server name as given by your broker. Must contain "demo" (case-insensitive).
# Examples: ICMarkets-Demo01, Pepperstone-Demo01, MetaQuotes-Demo
MT5_SERVER=MetaQuotes-Demo
# Optional: explicit path to MT5 terminal binary. Leave blank for auto-discovery.
# Local Windows: C:\Program Files\MetaTrader 5\terminal64.exe
# Railway (Service B): /root/.wine/drive_c/Program Files/MetaTrader 5/terminal64.exe
MT5_TERMINAL_PATH=
```

### 6.3 `.gitignore` additions
Adjacent to line 53, add:
```gitignore
# Reference-only checkout
/MT5_API_REFERENCE/   # if you keep a sibling MT5 API reference (optional)
/API-Quotex/          # unused sibling (cleanup)
```

---

## 7. Local development setup

### 7.1 Prerequisites
- Python 3.13+ (matches `pyproject.toml:6`)
- PostgreSQL 16+ (local or remote — Railway plugin works from local too)
- **Option A** (recommended on Windows): MetaTrader 5 desktop terminal installed from <https://www.metatrader5.com>
- **Option B** (cross-platform): Docker + the same `mt5linux/Dockerfile` you deploy with

### 7.2 Option A — Windows local with native MT5

1. Install MT5 terminal: <https://www.metatrader5.com/en/download> (default installer location `C:\Program Files\MetaTrader 5\terminal64.exe`).
2. In MT5: **File → Open an Account** → select your broker → enter the demo login from Section 8 → enable algorithmic trading (**Tools → Options → Expert Advisors → Allow algorithmic trading**).
3. From project root:
   ```bash
   uv sync
   cp .env.example .env
   # Edit .env: set TELEGRAM_*, MT5_LOGIN, MT5_PASSWORD, MT5_SERVER.
   # Leave MT5_TERMINAL_PATH blank — MetaTrader5 auto-discovers the standard install path.
   ```
4. Sanity check — does MT5 Python see your account?
   ```bash
   uv run python -c "import MetaTrader5 as mt5; mt5.initialize(); print(mt5.account_info())"
   ```
5. Run signal-copier:
   ```bash
   uv run python -m signal_copier
   ```
   The Telegram self-DM will fire `🟢 Bot started` once everything is alive.

### 7.3 Option B — Linux/macOS via mt5linux

1. Pull the docker-compose snippet that bundles Service A + Service B:
   ```bash
   docker compose up -d signal-copier mt5linux-server
   ```
   (The `docker-compose.yml` is added in this refactor; mirrored from `mt5linux/Dockerfile`.)
2. Set `MT5_TERMINAL_PATH=/usr/bin/wine` so the client points at the Wine shim, not a real Windows binary.

### 7.4 Pre-flight helper

```bash
uv run python -m tools.mt5_preflight
```

Sample output (success):
```
[OK] mt5.initialize  → MT5 terminal reachable
[OK] mt5.login_info  → user=12345 server=ICMarkets-Demo01
[OK] mt5.symbols_get → EURUSD, GBPUSD, USDJPY, EURJPY, EURGBP, USDCHF, USDCAD, AUDUSD, ... (104 total)
[OK] account_info     → balance=10000.00 leverage=1:500
PASS
```

---

## 8. Broker account setup (one-time, broker-side)

This section is the only one that lives outside the codebase. Do it once.

### 8.1 Create a demo account
1. Go to your chosen broker's signup page (e.g., <https://www.icmarkets.com/en/open-trading-account/demo>).
2. Fill out the form: name, email, account type = "MT5 Demo", leverage = 1:500 (or your preference), deposit = any virtual amount.
3. Broker emails you: `login` (8-9 digit number), `password`, `Investor password` (read-only).
4. Save login + main password into your password manager.

### 8.2 Enable automated trading (one-time per terminal)
1. Open MT5 desktop terminal.
2. **Tools → Options → Expert Advisors** tab.
3. Check ☑ **Allow algorithmic trading** and ☑ **Allow DLL imports** (latter is needed for `mt5linux-server`).
4. Click OK.
5. **Tools → Options → Notifications** — disable any "demo account expired" email warnings; they're noisy.

### 8.3 Capture credentials for the signal-copier
You have three secrets to put into `.env`/Railway:
- `MT5_LOGIN` — the numeric account login.
- `MT5_PASSWORD` — the main password.
- `MT5_SERVER` — the broker's demo server name (e.g., `ICMarkets-Demo01`).

**Treat `MT5_PASSWORD` like `OLYMP_ACCESS_TOKEN` — never commit, never share, never log.**

### 8.4 (Optional) Verify order placement works
Inside MT5's "Strategy Tester" or via the Tools → MetaQuotes Language Editor:
1. Open `MQL5/Scripts/MyFirstTrade.mq5`.
2. Run on EURUSD, volume=0.01, market order.
3. Confirm the position appears in the Trade tab.
4. If yes — broker allows automated orders on this demo. Proceed.

If that fails, your broker requires additional setup (some brokers' demo accounts start with "Allow algo trading = OFF" until first manual trade). Place one manual market order, then close it — that usually unlocks it.

---

## 9. Deploy to Railway

### 9.1 Push the renamed repo
```bash
git add -A
git commit -m "refactor(broker): OlympTrade → MetaTrader 5 (mt5linux)"
git push origin main
```

### 9.2 Service A — `signal-copier`
1. Railway dashboard → your renamed project → **+ New** → **GitHub Repo** → pick the renamed repo.
2. First deploy will fail (no env vars yet) — that's fine.
3. **Variables** tab on the service: copy from your local `.env` (omit `MT5_PASSWORD` if you'd rather set it via dashboard, which is recommended):
   - `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_PHONE`, `TELEGRAM_SESSION_STRING`
   - `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER`
   - `DATABASE_URL` — auto-injected by Postgres service (already done in M11; **verify** the auto-injection still works after rename).
4. Restart the service.
5. `railway logs --service telegram-mt5-copier --tail` — wait for `🟢 Bot started`.

### 9.3 Service B — `mt5linux-server`
1. In the same Railway project → **+ New** → **GitHub Repo** → pick the same repo.
2. **Settings** → **Service Name** → `mt5linux-server`.
3. **Settings** → **Source** → **Dockerfile Path** → `mt5linux/Dockerfile` (defaults to root `Dockerfile`, you need to override).
4. **Settings** → **Watch Paths** → `mt5linux/**` (so changes to that subtree only trigger rebuilds here, not in Service A).
5. **Variables** tab:
   - `MT5_LOGIN` (build arg) — bake into image at deploy time
   - `MT5_SERVER` (build arg) — bake into image at deploy time
   - **MT5_PASSWORD**: pass at runtime via an `MT5_PASSWORD` env var; read by `entrypoint.sh` and injected into the MT5 config at first launch (this avoids baking it into the image layers).
6. Set **memory** to at least 1 GB (Wine + MT5 idle ≈ 400 MB; with chart load it spikes).
7. **Networking**: the service should NOT be exposed publicly. Railway's default private-network setting is correct; just confirm there's no public domain.
8. Deploy. First build is slow (~10 min — Wine pulls + MT5 install). Subsequent deploys are fast.
9. Wait for the service to be **Running** (not "Deploying"). Then `railway logs --service mt5linux-server --tail` — look for:
   ```
   [entrypoint] Waiting for MT5 to be reachable...
   [entrypoint] MT5 listening on port 8001
   [entrypoint] Starting mt5linux-server...
   ```

### 9.4 Wire Service A to Service B
Service A (signal-copier) needs to find Service B's hostname. Railway auto-discovers services within a project on the private network.

Two options:
- **Option 1 (recommended):** set `MT5_SERVER` in Service A's env to the **private** URL of Service B, e.g., `mt5linux-server.railway.internal:8001` (Railway synthesizes these per service). The signal-copier's mt5 client accepts `host:port` strings.

  Wait — `MT5_SERVER` in MT5 terminology is the **broker's** server, not the local mt5linux-server. They are different. To keep them distinct:
  - Rename Service A's "MT5 broker server" env to e.g. `MT5_BROKER_SERVER`. (Spec required; see Section 4.6.)
  - Add a new env `MT5_SERVER_HOST` = `mt5linux-server.railway.internal:8001` (or `localhost:8001` for local Docker Compose).

- **Option 2:** wire via the `RAILWAY_PRIVATE_DOMAIN` env var that Railway exposes for each service. The mt5 client gets passed this dynamically.

I'll finalize which option in the design spec. **Default to Option 1 with explicit env names** — keeps Railway abstractions out of the codebase.

### 9.5 Update the existing service definition
`railway.toml` at the repo root stays the same — Railway uses it for Service A. Add a second `mt5linux/railway.toml` (pointed at by Service B's "Watch Paths + Dockerfile Path" config) with the same content. Two-service monorepo is supported out of the box by Railway.

---

## 10. Verification checklist

Work through these in order. Stop and debug if any fails.

### 10.1 Service health
- [ ] `mt5linux-server` deploys, builds the MT5 image, starts `mt5linux-server`. `railway logs` shows `MT5 listening on port 8001`.
- [ ] `mt5linux-server` is reachable from `telegram-mt5-copier` over the private network.
- [ ] `telegram-mt5-copier` deploys successfully. `railway logs` shows `🟢 Bot started` DM via self-message.
- [ ] PostgreSQL migrations applied — check the Data tab on the Postgres service, tables `signals / stages / daily_summary` exist.

### 10.2 MT5 <-> signal-copier liveness
- [ ] `mt5.account_info()` returns `{login: <your login>, server: '<demo server>', balance: ...}` when invoked from the signal-copier process. (Logged at startup.)
- [ ] `mt5.symbols_get('EURUSD')` returns the symbol spec. (Logged on connect.)
- [ ] `Bot started` self-DM shows `Mode: live demo` and `Server: <broker name>`.

### 10.3 First end-to-end trade (dry-run)
- [ ] With `DRY_RUN=true`, send a test signal to the channel (or use `SOAK_REPLAY` from existing M9 tooling, see `src/signal_copier/replay.py`).
- [ ] Self-DM shows: `🟢 Signal received` → `⏱️ Trade placed (INITIAL)` (note: still says "Trade" not "Order" — keep word consistent) → at expiration → `✅ WIN` or `❌ LOSS`.
- [ ] `daily_summary` row shows the trade incremented.

### 10.4 First end-to-end trade (live demo)
- [ ] Set `DRY_RUN=false`, redeploy.
- [ ] Confirm `🟢 Bot started` says `Mode: live demo`.
- [ ] Place one test signal. Confirm on the MT5 terminal Trade tab that a position opened and closed at the expected time with the expected PnL.
- [ ] Confirm `daily_summary.realized_pnl` updated.
- [ ] Confirm the Telegram self-DM matches the broker's `profit` field exactly (no rounding difference).

### 10.5 Restart resilience
- [ ] While a signal cascade is mid-flight (gale1 placed, awaiting result), kill the `telegram-mt5-copier` container.
- [ ] Railway restarts it. `recovery.py` runs at boot, finds the live `placed_gale1` row, calls `Scheduler.adopt(signal_row)`.
- [ ] The supervisor's `wait_result` correctly closes the position (since it's still open on MT5) and reports the result.
- [ ] No duplicate trade is placed (deterministic `trade_id` collides if the cascade were to re-insert — it doesn't, thanks to `StateStore.record_stage_placed` raising `StageAlreadyExistsError`).

---

## 11. Rollback plan

If anything goes catastrophically wrong mid-cutover, you can roll back to OlympTrade with a single revert + redeploy.

### 11.1 Code-side
1. `git revert` the refactor commit (or `git checkout <last-good-sha>`).
2. Restore `src/signal_copier/broker/olymp.py` and `broker/reconnect.py` from git history.
3. Restore `src/olymptrade_ws/` from git history (it was committed at v0.7).
4. Redeploy. Service A picks up `OLYMP_*` env vars again.

### 11.2 Data-side
- `signals / stages / daily_summary` schema is unchanged. No data migration needed.
- Open cascades on MT5 will need manual cleanup: in the MT5 terminal, close any open positions the signal-copier left behind during the failed cutover.

### 11.3 Broker-side
- Do **NOT** delete your MT5 demo account on rollback — keep it around in case you switch back or run both side-by-side for a comparison period.

### 11.4 Timeline
Rollback takes ≈ 10 minutes (git revert + Railway redeploy + manual position close). The tool was already designed to survive restarts, so the data is safe throughout.

---

## 12. Known limitations & open questions

### 12.1 Limitations (after the refactor)

| Limitation | Mitigation |
|---|---|
| Demo accounts may be throttled or expire after 30-90 days. | Re-create demo every quarter; keep MT5_LOGIN/PASSWORD in a password manager with a reminder. |
| Wine + MT5 ≈ 500 MB memory. | Railway Hobby plan covers this; one Service B adds ~$2-3/mo to your bill. |
| MT5 doesn't have all OlympTrade's exotic pairs (OTC, LATAM_X, etc.). | The signal parser (`domain/signal.py:148`) accepts any `XXX/YYY` format; unsupported pairs will fail at `Broker.place()` with `UnsupportedPairError`, the supervisor ends the cascade cleanly, and the user gets a self-DM (`notify/protocol.py:117`). |
| First cold start of `mt5linux-server` is 30-60 s. | `recovery.py`'s backoff in `broker/reconnect.py` (renamed pattern) covers this — the signal-copier retries `mt5.initialize()` with exponential backoff. |
| Some MT5 brokers disable automated trading by default on demo. | Manual one-time setup per Section 8.2 — not a runtime issue. |

### 12.2 Open questions

These don't block the refactor but are worth deciding later:

- **Q-A.** Should Service A's `MT5_BROKER_SERVER` env var differ from the README/examples? Per Section 9.4, I'm proposing to rename `MT5_SERVER` → `MT5_BROKER_SERVER` to keep broker-server and mt5linux-server distinct. Confirm.
- **Q-B.** Lot sizing per Section 1.3 — is 0.01 / 0.02 / 0.04 right? After 24 h of demo, review the daily PnL and bump it up or down.
- **Q-C.** Want a separate Telegram channel for MT5-only notifications, or keep the same `Saved Messages` self-DM? Recommend same — less operational surface.
- **Q-D.** Should the bot emit `🟢 Bot started` to include which broker is connected (ICMarkets, MetaQuotes, etc.)? Easy win — propose yes.

---

## Appendix A — File-by-file scope cheat sheet

Use this as a checklist while making the changes. The change column uses simple action verbs.

| File | Action | What changes |
|---|---|---|
| `pyproject.toml` | modify | description, deps |
| `README.md` | modify | rewrite broker story + runbook |
| `.env.example` | modify | OLYMP_* → MT5_* |
| `.gitignore` | modify | add API-Quotex/, keep MT5_API_REFERENCE/ ignore |
| `Dockerfile` | keep | unchanged (Service A) |
| `railway.toml` | keep | unchanged |
| `mt5linux/Dockerfile` | create | Wine + MT5 image |
| `mt5linux/entrypoint.sh` | create | start xvfb + MT5 + mt5linux-server |
| `mt5linux/requirements.txt` | create | mt5linux-server pin |
| `mt5linux/railway.toml` | create | Service B config |
| `src/signal_copier/__init__.py` | modify | version bump (0.1.0 → 0.2.0) |
| `src/signal_copier/config.py` | modify | OLYMP_* fields → MT5_*; new `_validate_demo_server` |
| `src/signal_copier/__main__.py` | modify | broker selection block (lines 49-56, 95-111, 232-234) |
| `src/signal_copier/broker/base.py` | modify | +8 lines for `close_position` method |
| `src/signal_copier/broker/olymp.py` | delete | replaced by `mt5.py` |
| `src/signal_copier/broker/mt5.py` | create | new MT5 broker impl |
| `src/signal_copier/broker/reconnect.py` | delete + create | MT5 reconnect (~150 lines, new file) |
| `src/signal_copier/broker/dry_run.py` | modify | add `close_position` no-op + return Decimal(0) |
| `src/signal_copier/domain/state.py` | modify | drop `_stage_pnl`, add `pnl: Decimal \| None = None` to `ResultEvent` |
| `src/signal_copier/scheduler/trigger.py` | modify | lines 444-505 (`_drive_cascade`), lines 522-562 (`_apply_result_and_finalize`) |
| `src/signal_copier/notify/protocol.py` | modify | rename 4 methods: `on_olymp_*` → `on_broker_*` |
| `src/signal_copier/notify/telegram_dm.py` | modify | rename methods + DM text |
| `src/signal_copier/telegram/auth.py` | keep | unchanged (MT5 doesn't affect Telegram auth) |
| `src/signal_copier/telegram/listener.py` | keep | unchanged |
| `src/signal_copier/telegram/client.py` | keep | unchanged |
| `src/signal_copier/telegram/channel_resolver.py` | keep | unchanged |
| `src/signal_copier/infra/db.py` | keep | unchanged (schema broker-agnostic) |
| `src/signal_copier/infra/state_store.py` | keep | unchanged |
| `src/signal_copier/infra/clock.py` | keep | unchanged |
| `src/signal_copier/infra/log.py` | keep | unchanged |
| `src/signal_copier/infra/db_rows.py` | keep | unchanged |
| `src/signal_copier/migrations/001_initial.sql` | keep | unchanged |
| `src/signal_copier/scheduler/trigger.py:107` (`record_timeout`) | modify | add `pnl_override` semantics to ResultEvent |
| `tools/soak.py`, `tools/soak_assertions.py` | keep | unchanged |
| `scripts/cascade_test.py`, `scripts/e26_test.py`, `scripts/olymp_diag*` | keep | drop `olymp_diag*` (OlympTrade-specific) |
| `docs/PRD.md` | modify | header, FR-4.x, §6, §7, §10, §12.6 (delete), §13.1 R-5/R-6/R-15, §15 M8/M10/M13 |
| `docs/refactor.md` | create | **this file** |
| `docs/superpowers/specs/<date>-mt5-broker-swap-design.md` | create | design spec |
| `docs/superpowers/plans/<date>-mt5-broker-swap.md` | create | implementation plan |
| `src/olymptrade_ws/` (entire tree) | delete | replaced by PyPI dep |
| `OlympTradeAPI/` | delete | not committed; remove from disk |
| `API-Quotex/` | delete | not committed; remove from disk |

**Total to write:** ~580 lines of new Python (mt5.py, reconnect.py, mt5_preflight.py), ~120 lines of Dockerfile + entrypoint. **Total to edit:** ~12 files. **Total to delete:** ~3 directories.

---

## Appendix B — Milestone mapping

The refactor isn't a single PR — it splits cleanly into **M13** (broker swap) + **M14** (signal-copier + mt5linux Railway deployment).

| Milestone | Deliverable | Verifiable outcome |
|---|---|---|
| **M13.1** | Broker Protocol change (`close_position`) + config rename | `pytest tests/` still green; no functional change yet |
| **M13.2** | `broker/mt5.py` implementation against local MT5 (Option A in Section 7.2) | Demo trade placed + closed; PnL matches MT5 Trade tab |
| **M13.3** | `broker/mt5.py` against mt5linux in Docker (Option B in Section 7.3) | Same as M13.2 but via Wine |
| **M13.4** | Notifier rename (`on_olymp_*` → `on_broker_*`) | All DM tests still green |
| **M13.5** | PRD + doc updates (Appendix A "modify" rows) | All cross-references resolve |
| **M13.6** | Repo + Railway renames (Sections 2, 3) | Both names match in dashboard, README, git remote |
| **M14.1** | `mt5linux/Dockerfile` image builds locally | `docker build mt5linux/` succeeds; size ≈ 1.2 GB |
| **M14.2** | Service B deployed to Railway | `railway logs --service mt5linux-server` shows MT5 listening |
| **M14.3** | Service A connected to Service B | `mt5.account_info()` from Service A returns real values |
| **M14.4** | First live-demo trade end-to-end | Self-DM `✅ WIN` or `❌ LOSS` matches MT5 Trade tab |
| **M14.5** | Restart resilience test (Section 10.5) | Kill Service A mid-cascade; no duplicate trades |
| **M14.6** | Rollback smoke test (Section 11) | `git revert` → OlympTrade restarts cleanly |

Each milestone is independently testable per the project's milestone discipline (PRD §15).

---

*End of refactor guide. Implementation begins after Section 1's four decisions are locked in.*
