# Telegram → OlympTrade Signal Copier

Personal tool that listens to a Telegram channel for forex trading signals, parses them, and automatically copies the trades to an OlympTrade **demo** account using a martingale-style strategy ($2 → $4 → $8, stop on first profit or after 2nd gale).

> **v1 is demo-only by mandate.** The app refuses to start with real-money config. See `docs/PRD.md` for the full spec, build plan, and decisions.

## Status

Pre-implementation scaffold. Spec lives in [`docs/PRD.md`](docs/PRD.md) (v0.7) and the original idea in [`docs/tool-idea.md`](docs/tool-idea.md).

## Contents
- [How it works (TL;DR)](#how-it-works-tldr)
- [Third-party dependency — vendored](#third-party-dependency--vendored)
- [First-time setup (deploy to Railway)](#first-time-setup-deploy-to-railway)
- [Local development](#local-development)
- [Operations](#operations)
- [Verify the deployment](#verify-the-deployment)
- [Risks](#%E2%9A%A0%EF%B8%8F-risks)
- [License](#license)

## How it works (TL;DR)

1. Connect to a personal Telegram account (Telethon, MTProto)
2. Watch one admin-only channel for signals in a strict format
3. Parse `PUT🟥` / `CALL🟩` signals with pair, trigger time, expiration
4. At the trigger HH:MM, place a CALL or PUT on OlympTrade with the configured amount
5. On loss → schedule 1st gale (2×) at trigger + 5 min
6. On loss again → schedule 2nd gale (3× stage amount = 4× initial) at trigger + 10 min
7. Stop on first win or after 2nd gale
8. DM the user at every state transition

Full details: [`docs/PRD.md`](docs/PRD.md).

## Third-party dependency — vendored

This project uses a reverse-engineered WebSocket client for the broker (originally by **Chipa, 2025, MIT-licensed**). The `olymptrade_ws/` source is **vendored** at `src/olymptrade_ws/`:

- It is **not** installed as a Python package
- It is **not** a git submodule
- It is committed in-tree so deployment is a single `COPY . .` and local patches are obvious

See [`src/olymptrade_ws/VENDORED.md`](src/olymptrade_ws/VENDORED.md) for the upstream source, license, import contract, and re-vendoring instructions.

## First-time setup (deploy to Railway)

> Time required: ~20 minutes. You need a Railway account, this repo, your Telegram API credentials, and your OlympTrade JWT.

1. **Create a Railway project** at <https://railway.app> → New Project → Deploy from GitHub repo (select this repo). Railway auto-detects the Dockerfile and begins a first build. The first deploy will fail with "Telegram not authorized" — this is expected. Continue to step 2.

2. **Add Postgres** in the Railway project dashboard → **+ New** → **Database** → **PostgreSQL**. Railway auto-injects `DATABASE_URL` into your `signal-copier` service. No manual wiring needed.

3. **Set environment variables** on the `signal-copier` service → Variables tab. Required:
   - `TELEGRAM_API_ID` (from <https://my.telegram.org>)
   - `TELEGRAM_API_HASH`
   - `TELEGRAM_PHONE` (e.g. `+12345678900`)
   - `OLYMP_ACCESS_TOKEN` (extract from your OlympTrade session — DevTools → Network → any authenticated request → JWT in the Authorization header)
   - `OLYMP_ACCOUNT_ID` (numeric, visible in OlympTrade dashboard URL)
   - Optional but recommended: `OLYMP_ACCOUNT_GROUP=demo`, `DRY_RUN=true` (defaults to safe paper-trading; app refuses to start if both are off)
   - `DATABASE_URL` is **auto-injected** by the Postgres service. Don't set it manually.

4. **Trigger a redeploy.** Railway's dashboard shows the first deploy failed. Click "Restart" to re-run the now-configured container. The bot will start, run migrations on the Postgres (idempotent `CREATE TABLE IF NOT EXISTS`), then fail with "Telegram session string missing". Expected — proceed to step 5.

5. **Generate the Telegram session string locally** (one-time):

   ```bash
   git clone <this-repo> ~/signal-copier-auth   # or use your existing clone
   cd ~/signal-copier-auth
   uv sync
   cp .env.example .env
   # Edit .env: set TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_PHONE.
   # Leave TELEGRAM_SESSION_STRING empty.
   uv run python -m signal_copier.telegram.auth
   ```

   The helper prompts for code from your Telegram app and 2FA password (if enabled). On success, it prints:

   ```
   ======================================================================
   Authenticated as: Your Name (@yourhandle)
   User ID: 123456789
   ======================================================================
   Set this as TELEGRAM_SESSION_STRING in your Railway Variables:

   TELEGRAM_SESSION_STRING=<a long base64 string>
   ```

   **Treat the session string like a password.** Anyone with it can read and send messages from your Telegram account.

6. **Paste the session string into Railway** → Variables → `TELEGRAM_SESSION_STRING`. Click "Restart" on the service.

7. **Verify** by checking Railway logs (`railway logs --tail` or the dashboard's Logs tab). You should see:
   - `Bot started`
   - A Telegram self-DM with the asset map resolved by `OlympTradeBroker`
   - Empty cascade tables in the Postgres service's Data tab (or three empty tables: `signals`, `stages`, `daily_summary`)

8. **Send a test signal** to your analyst channel in the format specified by the PRD §4.2. Verify the bot DMs you "Signal received" within ~1 second and, at the trigger HH:MM, "Trade placed (INITIAL)".

Done. The tool runs unattended; Railway restarts on crash (PRD §17.3); PG state survives redeploys.

## Local development

Prereqs: Python 3.13, Docker, uv (`pip install uv` or `brew install uv`).

```bash
git clone <this-repo>
cd signal-copier
uv sync                       # creates .venv, installs deps
cp .env.example .env          # then edit: TELEGRAM_*, OLYMP_*
docker compose up -d          # starts postgres on :5432
echo 'DATABASE_URL=postgresql://copier:copier@localhost:5432/copier' >> .env
uv run python -m signal_copier
```

To stop Postgres (data persists in the `copier-pg-data` volume):

```bash
docker compose down
```

To wipe and recreate from scratch:

```bash
docker compose down -v && docker compose up -d
```

### Running tests

```bash
uv run pytest                    # full suite
uv run pytest tests/test_parser.py  # one file
uv run pytest -k "test_state"    # one test pattern
```

Tests that hit the DB (`tests/test_db.py`) need Postgres running via `docker compose up -d`. The test suite uses `DATABASE_URL` from `.env`.

### Linting and typechecking

```bash
uv run ruff check                # lint
uv run ruff format --check       # format check (CI fails if unformatted)
uv run mypy --strict src tests   # type check (CI fails on any error)
```

## ⚠️ Risks

- **Telegram ToS:** uses a personal user account, not a bot. Ban risk is real and accepted by the owner.
- **OlympTrade ToS:** reverse-engineered WS protocol. Token can be revoked, protocol can change.
- **Real money:** disabled in v1. Hard guardrail in config — no bypass.

## License

This project is licensed under [PolyForm Strict 1.0.0](LICENSE). Free to use, modify, and distribute; you may not sell this work or any derivative work.

The vendored `olymptrade_ws/` retains its original MIT license — see [`src/olymptrade_ws/LICENSE`](src/olymptrade_ws/LICENSE).
