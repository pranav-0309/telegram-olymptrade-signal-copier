# Design Spec — M11: Railway Deployment, Runbook & Project License

**Date:** 2026-06-23
**Status:** Approved (sections 1–8) — pending user review of this written spec
**Milestone:** M11 (per PRD §15)
**Author:** opencode brainstorming session
**Related PRD sections:** §6 Tech Stack, §7 Architecture, §8 Configuration, §15 Build Plan M11 row, §17 Hosting (whole section, especially §17.5 Railway Postgres provisioning and §17.7 First-deploy runbook), §18 Changelog

---

## 1. Problem & Motivation

M0–M10 have shipped the working tool: parser, state machine, broker adapter (with M10's reconnect supervisor), Telegram listener, scheduler, notifier, and PostgreSQL persistence. M11 is the final v1 milestone before the 7-day soak test gates "done."

What M11 ships is the **operational layer** that turns the working tool into an unattended service on Railway:

1. **CI/CD workflow** — lint + format + typecheck + test (with PG service container) + auto-deploy on push to main, so every commit to `main` is a deployable artifact and every PR is a green-or-red gate.
2. **Interactive Telegram auth helper** — `python -m signal_copier.telegram.auth`, the one-time tool that turns a phone+code+2FA into a `StringSession` you paste into Railway's Variables.
3. **Local dev helper** — `docker-compose.yml` so `docker compose up -d` gives a Postgres on `:5432` that matches the CI's `services:` block exactly.
4. **README runbook** — the operational documentation: first-time deploy, daily operations, troubleshooting.
5. **Project license** — PolyForm Strict 1.0.0 (user-confirmed). Closes the "Project license TBD" hole from the current README.

PRD §15 M11's verifiable outcome is: **"Tool runs unattended on Railway with PG; restart-on-crash works; data survives redeploys."** All three are validated by a 3-step manual checklist in the README's "Verify the deployment" section, executed once after the first deploy.

---

## 2. Goals & Non-Goals

### 2.1 Goals

1. **End-to-end deployable.** A new Railway project + this repo + the README's first-time setup → a running tool within ~20 minutes.
2. **Reproducible CI.** Every push runs the same lint+format+typecheck+test gates, with the same Postgres the local dev container uses.
3. **Auto-deploy on main.** Push to `main` → Railway rebuilds and restarts. No manual step.
4. **Safe session generation.** The auth helper verifies the generated StringSession works (`client.get_me()`) before printing it, so the user doesn't paste a bad session into Railway.
5. **Operational documentation.** Daily operations (restart, redeploy, rotate session, rotate token, wipe DB, set limits) are documented in the README — no tribal knowledge.
6. **Explicit license.** PolyForm Strict 1.0.0 published at the repo root, mirrored in `pyproject.toml`, and copied into the Docker image.
7. **Zero new runtime dependencies.** Everything M11 adds uses tools already in the project (Telethon, asyncpg) or external services that are free-tier-friendly (GitHub Actions, Railway).
8. **R-15 compliance.** No edits to vendored `src/olymptrade_ws/`. The 12 currently-modified vendored files in the working tree must be reverted or documented in `src/olymptrade_ws/VENDORED.md` before M11 ships.

### 2.2 Non-Goals (explicitly out of scope for M11)

- **A `scripts/verify_deploy.py` that hits Railway's internal API** — Railway's API is undocumented and unstable; the manual 3-step checklist is more durable.
- **A Railway `/healthz` HTTP listener** — would add a port, an extra dep (aiohttp/starlette), and "healthy" would only mean "process alive," not "broker connected."
- **A smoke-test bot that posts fake signals after every deploy** — needs a second Telegram account and a test channel; overkill for a personal tool that the 7-day soak test already exercises.
- **Multi-environment Railway setup (dev + prod)** — the single `demo` service is the only target for v1.
- **Auto-rotation of the OlympTrade access_token** — PRD S-6, deferred to v1.0 as a follow-on. README documents the manual rotation procedure.
- **Pre-deploy docker-build validation as a 4th parallel job** — covered by `railway up`'s implicit build; if the Dockerfile is broken, the deploy fails and CI surfaces it on the next push.
- **Self-hosted GitHub Actions runner** — ubuntu-latest has plenty of free minutes; private-repo minutes are well within the free tier for this project's commit cadence.
- **Modifying the vendored `olymptrade_ws` package** — PRD R-15 / §12.6 forbids edits.

---

## 3. Architecture

### 3.1 Repo layout (additions and modifications)

```
signal-copier/
├── .github/                                    ← NEW
│   └── workflows/
│       └── ci.yml                              ← NEW: CI + CD on push to main
├── src/signal_copier/telegram/
│   ├── client.py                               (existing, M5)
│   ├── listener.py                             (existing, M5)
│   └── auth.py                                 ← NEW: `python -m signal_copier.telegram.auth`
├── docker-compose.yml                          ← NEW: local dev (postgres:16-alpine)
├── LICENSE                                     ← NEW: PolyForm Strict 1.0.0
├── README.md                                   MODIFIED: +First-time setup, +Local development,
│                                                            +Operations, +Verify the deployment
├── pyproject.toml                              MODIFIED: +`signal-copier-auth` console script,
│                                                            license metadata
├── Dockerfile                                  MODIFIED: +`COPY LICENSE ./LICENSE` for license
│                                                            inclusion in the image
├── railway.toml                                UNCHANGED (M0 ships correct shape)
├── .dockerignore                               UNCHANGED (M0 ships correct shape)
├── .python-version                             UNCHANGED (M0 ships correct shape)
└── docs/PRD.md                                 MODIFIED: §18 changelog entry for M11
```

### 3.2 Concurrency / runtime

No new runtime coroutines. M11 is purely an operational layer:
- **CI/CD:** a `.github/workflows/ci.yml` file with 5 jobs. Runs on `ubuntu-latest`. Triggers on push to `main`, PRs targeting `main`, and `workflow_dispatch`.
- **Auth helper:** a single-purpose asyncio script run interactively. Not started by the bot; not running in CI; not running on Railway. Lifetime: ~30 seconds.
- **docker-compose:** developer-only; not used by CI (CI uses the equivalent `services:` block).
- **License:** static file. No runtime impact.

### 3.3 Data flow

M11 introduces no new data flow. The auth helper's only side effect is to print the StringSession to stdout; the user copies it into Railway Variables manually. The `LICENSE` file is static. The CI workflow only reads from the repo.

---

## 4. CI/CD Workflow

### 4.1 File: `.github/workflows/ci.yml`

Triggered on:
- `push` to `main` (full CI + auto-deploy — no opt-in needed; every push to main deploys)
- `pull_request` targeting `main` (full CI; no deploy)
- `workflow_dispatch` with an explicit `deploy: true` input (manual opt-in deploy; default `deploy: false` so a manual re-run of CI on an existing commit doesn't accidentally redeploy)

### 4.2 Job graph

```
              lint ─┐
              format─┤
                      ├──▶ test ──▶ deploy (push to main only)
              typecheck ─┘
```

### 4.3 Job specifications

| # | Job | Depends on | Runner | Steps |
|---|---|---|---|---|
| 1 | `lint` | — | `ubuntu-latest` | checkout (shallow), `astral-sh/setup-uv@v4` with cache, `uv sync`, `uv run ruff check` |
| 2 | `format` | — | `ubuntu-latest` | checkout (shallow), `astral-sh/setup-uv@v4` with cache, `uv sync`, `uv run ruff format --check` |
| 3 | `typecheck` | — | `ubuntu-latest` | checkout (shallow), `astral-sh/setup-uv@v4` with cache, `uv sync`, `uv run mypy --strict src tests` |
| 4 | `test` | `lint`, `typecheck` | `ubuntu-latest` + `services: postgres:16-alpine` | checkout, `astral-sh/setup-uv@v4` with cache, `uv sync`, `uv run pytest` |
| 5 | `deploy` | `test` (push to main only) | `ubuntu-latest` | checkout (`fetch-depth: 0`), `ghcr.io/railwayapp/cli:latest`, `railway up --service $RAILWAY_SERVICE_ID --detach` |

The `deploy` job is conditional on `(github.event_name == 'push' && github.ref == 'refs/heads/main') || (github.event_name == 'workflow_dispatch' && inputs.deploy == 'true')`. Push-to-main deploys unconditionally; manual dispatches deploy only when the user explicitly sets the `deploy` input to `true` (default `false`). PRs never deploy.

### 4.4 Required GitHub repository secrets

| Secret | Source | Purpose |
|---|---|---|
| `RAILWAY_TOKEN` | https://railway.app/account/tokens | Auth for the `railway` CLI; deploy-scoped if possible |
| `RAILWAY_PROJECT_ID` | Railway project URL | Identifies which project to deploy to |

### 4.5 Required GitHub repository variables

| Variable | Source | Purpose |
|---|---|---|
| `RAILWAY_SERVICE_ID` | Railway service URL | Identifies which service within the project to deploy to |

### 4.6 Service container config for `test` job

```yaml
services:
  postgres:
    image: postgres:16-alpine
    env:
      POSTGRES_USER: copier
      POSTGRES_PASSWORD: copier
      POSTGRES_DB: copier
    ports: ["5432:5432"]
    options: >-
      --health-cmd pg_isready
      --health-interval 10s
      --health-timeout 5s
      --health-retries 5
env:
  DATABASE_URL: postgresql://copier:copier@localhost:5432/copier
```

Mirrors M4's `docker run` from PRD §9.4 and `docker-compose.yml` (§6). Same image, same creds, same DB name.

### 4.7 Cache strategy

All jobs use `astral-sh/setup-uv@v4` with `enable-cache: true`, which caches `~/.cache/uv` keyed on `pyproject.toml` + `uv.lock`. Cold-cache install ~30s, warm-cache ~3s.

### 4.8 Concurrency control

```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
```

Cancels in-progress CI runs on the same PR when new commits are pushed (saves runner minutes). Never cancels a `main` deploy mid-flight.

### 4.9 Why `railway up --detach`

Without `--detach`, the deploy step blocks until the deploy fully finishes (~2-5 minutes for the cold build, ~30s for warm builds). With `--detach`, the GitHub Action returns once the build is submitted to Railway; Railway handles the rest asynchronously. This prevents a 5-minute deploy from occupying a GitHub Actions runner. The user follows the actual deploy outcome in the Railway dashboard.

### 4.10 Why `fetch-depth: 0` on the deploy job

The `railway up` CLI uses git history to compute the diff for incremental deploys. Shallow clones break this; the deploy job uses a full clone. Other jobs use shallow clones for speed.

---

## 5. Auth Helper

### 5.1 File: `src/signal_copier/telegram/auth.py`

A single-purpose asyncio script that turns a phone+code+2FA into a working Telethon `StringSession`. Per M5's spec (D-3) and PRD §17.7 step 5.

### 5.2 Invocation

Two equivalent entry points (per M5 D-3):
```bash
# Module form (canonical)
python -m signal_copier.telegram.auth

# Console script (after `uv sync`)
signal-copier-auth
```

### 5.3 Behavior

1. Load `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_PHONE` from env (existing `signal_copier.config.load_config()`). Refuse to start if any are missing — print `Error: TELEGRAM_API_ID and TELEGRAM_API_HASH must be set in .env` and exit 1.
2. Defensive guard: refuse to run on Railway by detecting `RAILWAY_ENVIRONMENT` or `RAILWAY_PROJECT_ID` env vars. Print `Error: run this locally; paste the printed StringSession into Railway Variables` and exit 1.
3. Open a `TelegramClient(StringSession(), api_id, api_hash)`.
4. Call `await client.send_code_request(phone)`.
5. Prompt `Code from Telegram app:` and call `await client.sign_in(phone, code)`.
6. If `SessionPasswordNeededError`: prompt `2FA password:` and call `await client.sign_in(password=password)`.
7. Verify the session works with `me = await client.get_me()`.
8. Get `session_string = client.session.save()`.
9. Print a banner:
   ```
   ====================================================================
   Authenticated as: <first_name> <last_name> (@<username>)
   User ID: <id>
   ====================================================================
   Set this as TELEGRAM_SESSION_STRING in your Railway Variables:

   <the base64 session string>

   Treat this like a password. Do not commit it; do not share it.
   Then redeploy: git commit --allow-empty -m 'rotate session' && git push
   Or trigger a manual redeploy from the Railway dashboard.
   ====================================================================
   ```

### 5.4 Error handling

| Failure | Behavior |
|---|---|
| Missing env vars | `sys.exit("Error: TELEGRAM_API_ID and TELEGRAM_API_HASH must be set in .env")` |
| Running on Railway | `sys.exit("Error: run this locally; paste the printed StringSession into Railway Variables")` |
| `ApiIdInvalidError` | `sys.exit("Error: TELEGRAM_API_ID / TELEGRAM_API_HASH invalid")` |
| `PhoneCodeInvalidError` | `sys.exit("Error: code invalid or expired; re-run the helper")` |
| `PasswordHashInvalidError` | `sys.exit("Error: 2FA password invalid")` |
| `FloodWaitError(seconds=N)` | `sys.exit(f"Error: Telegram rate-limited for {N}s; wait then retry")` |
| `get_me()` fails | `sys.exit("Error: session generated but verify failed; re-run the helper")` |
| KeyboardInterrupt (Ctrl+C) | Clean exit, no stack trace |
| Other exceptions | Print `Error: <message>` + one-line stack trace at WARNING; exit 1 |

No full tracebacks by default — the user is in a terminal and wants one-line actions, not Python internals.

### 5.5 What it does NOT do

- Does not write to `.env` automatically (the user must paste manually so they can copy the value).
- Does not run in CI (no Telethon credentials in CI; not its purpose).
- Does not auto-redeploy after generation (caller's job, post-paste).
- Does not print the session string in non-interactive mode (the helper refuses to run non-interactively; CI doesn't have a TTY).

### 5.6 Testing

No automated tests. The helper is interactive; testing it requires mocking Telethon and simulating stdin, which would test the mocks more than the helper. PRD M5 spec line 1318: "Tests against `python -m signal_copier.telegram.auth` — requires interactive terminal. M11's runbook is the test." The README's "First-time setup" section is the manual test.

### 5.7 Console script registration (`pyproject.toml`)

```toml
[project.scripts]
signal-copier-auth = "signal_copier.telegram.auth:main"
```

`main()` is a thin wrapper around `asyncio.run(run())`.

---

## 6. Local Dev (`docker-compose.yml`)

### 6.1 File: `docker-compose.yml`

```yaml
services:
  postgres:
    image: postgres:16-alpine
    container_name: signal-copier-pg
    environment:
      POSTGRES_USER: copier
      POSTGRES_PASSWORD: copier
      POSTGRES_DB: copier
    ports:
      - "5432:5432"
    volumes:
      - copier-pg-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U copier -d copier"]
      interval: 10s
      timeout: 5s
      retries: 5

volumes:
  copier-pg-data:
```

### 6.2 Why a compose file

- `docker compose up -d` is one command vs. a long `docker run` with flags.
- The named volume `copier-pg-data` survives `docker compose down` (default behavior) and is removed only by `docker compose down -v`.
- Self-documenting: the YAML is the spec of the local dev DB.
- Matches the GitHub Actions `services:` block exactly (same image, same creds), so local dev and CI use the same DB shape.

### 6.3 README usage

```bash
# Start local Postgres
docker compose up -d

# In another terminal, set DATABASE_URL in your .env
echo 'DATABASE_URL=postgresql://copier:copier@localhost:5432/copier' >> .env

# Run the bot
uv run python -m signal_copier

# Stop Postgres (data persists)
docker compose down

# Wipe and recreate from scratch
docker compose down -v && docker compose up -d
```

---

## 7. License (PolyForm Strict 1.0.0)

### 7.1 Why PolyForm Strict

User-confirmed via brainstorming. PolyForm Strict 1.0.0 grants free use, modification, and distribution; prohibits selling the work or any derivative work. Matches the user's stated intent: "people cannot sell it to anyone else but can only use it for themselves."

Plain English summary:
- ✅ Use, copy, modify, merge, publish, distribute (including for free on GitHub)
- ❌ Sell the work or any derivative work
- ❌ Sub-license (so any derivative work inherits the same terms)

### 7.2 File: `LICENSE`

The full PolyForm Strict 1.0.0 text, fetched verbatim from https://polyformproject.org/licenses/strict/1.0.0 and committed at the repo root. The implementation plan retrieves the exact text.

### 7.3 Compatibility with vendored `olymptrade_ws` (MIT)

PolyForm Strict is compatible with MIT:
- MIT allows free redistribution with attribution.
- The vendored `src/olymptrade_ws/LICENSE` is preserved as-is (PRD R-15, §12.6).
- The combined work has PolyForm Strict terms for the user's code and MIT terms for the vendored code. Both license texts are present in the repo and both are `COPY`'d into the Docker image.

No license conflict.

### 7.4 `pyproject.toml` license metadata

```toml
[project]
license = { text = "PolyForm Strict 1.0.0" }
```

(PEP 639 / SPDX-style metadata. Some tools prefer `license = "PolyForm-1.0.0"`; the implementation plan will use whichever form `uv` accepts without warnings.)

### 7.5 Dockerfile: license inclusion

The current Dockerfile `COPY src/ ./src/` already covers the vendored license (which lives at `src/olymptrade_ws/LICENSE`). M11 adds an explicit:

```dockerfile
COPY LICENSE ./LICENSE
```

so the project license is shipped with the image alongside the source. Standard for license compliance; cheap to add.

---

## 8. README Runbook

The current README has only a TL;DR + risks + license-stub. M11 replaces it with a structured runbook. The TL;DR is preserved at the top.

### 8.1 New section: "First-time setup (deploy to Railway)"

~50 lines. Walks through the PRD §17.7 runbook step by step:

1. Create a Railway project → Deploy from GitHub repo.
2. Add Postgres.
3. Set env vars (lists each required var with a one-line "where to find it" hint).
4. Trigger a redeploy (the first deploy will fail with "Telegram session missing" — expected).
5. Generate the Telegram session string locally (`uv run python -m signal_copier.telegram.auth`).
6. Paste the session string into Railway Variables → `TELEGRAM_SESSION_STRING`.
7. Verify by checking Railway logs for `Bot started` + Telegram self-DM.
8. Send a test signal.

### 8.2 New section: "Local development"

~30 lines. Prereqs (Python 3.13, Docker, uv); setup commands; running tests; linting/typecheck commands.

### 8.3 New section: "Operations"

~80 lines. Operational reference:

| Subsection | Content |
|---|---|
| View logs | `railway logs --tail` and the Railway dashboard's Logs tab |
| Restart the bot | `railway restart` or dashboard button |
| Trigger a manual redeploy | `git commit --allow-empty -m "trigger redeploy" && git push` or dashboard button |
| Rotate the Telegram session | Re-run the auth helper, paste new value, restart |
| Rotate the OlympTrade token | Extract new JWT from browser DevTools, paste into `OLYMP_ACCESS_TOKEN`, restart |
| Wipe the database (destructive) | `DROP TABLE` via Railway PG query editor; bot recreates on next startup |
| Set daily safety limits | Edit `DAILY_LOSS_LIMIT` / `DAILY_TRADE_LIMIT` / `DAILY_DRAWDOWN_PCT`, restart |

### 8.4 New section: "Verify the deployment"

3 manual checks, each ~30 seconds:

1. **Bot connected and listening** — `railway logs --tail | grep "Bot started"` shows the startup line.
2. **Restart-on-crash works** — `railway down && railway up` (or dashboard restart) shows the process exit and a new process start within ~10s.
3. **Data survives redeploys** — insert a row, trigger a redeploy, query confirms the row is still there.

Plus a 6-row Troubleshooting table mapping symptoms to likely causes and fixes.

### 8.5 Sections preserved

The existing TL;DR, "How it works" sub-summary, "Third-party dependency — vendored" section, and "Risks" section are preserved verbatim. The "License" stub ("Project license TBD") is replaced with a reference to the new `LICENSE` file and a one-line summary of PolyForm Strict.

### 8.6 Estimated README length after M11

- Current: 42 lines.
- After: ~290 lines (+~250).

---

## 9. Verification of Deploy Outcomes

### 9.1 Verification model

M11's verifiable outcome per PRD §15 is "Tool runs unattended on Railway with PG; restart-on-crash works; data survives redeploys." Approach A verifies each manually with a 3-step checklist (folded into the README, §8.4). No new test code.

### 9.2 The three checks

| Check | Command | Pass criterion |
|---|---|---|
| Bot is connected | `railway logs --tail \| grep "Bot started"` | One matching line within 2 min of last deploy |
| Restart-on-crash | `railway down && railway up` | New `Bot started` line within ~10s; no `Token expired` or `Database connection lost` errors |
| Data survives | `INSERT INTO signals (...); git commit --allow-empty -m verify; git push`; then `SELECT` the row | The inserted row is still present after the redeploy |

### 9.3 Gate relationship

The 3 checks above prove the **first-deploy** shape. The 7-day soak test (post-M11, per PRD §15's "Definition of Done for v1.0") proves the **sustained-operation** shape. Both must pass for "v1 done."

### 9.4 CI verification

The CI workflow does NOT exercise the deploy surface; it exercises only the pre-deploy gates (lint+format+typecheck+test). The user runs the 3 manual checks once after the first production deploy; if they pass, no further manual verification is needed for routine code changes (auto-deploy handles them).

---

## 10. File-by-File Implementation Summary

### 10.1 Files created (5)

| Path | Approx. lines | Purpose |
|---|---|---|
| `.github/workflows/ci.yml` | ~80 | CI + CD workflow (§4) |
| `src/signal_copier/telegram/auth.py` | ~80 | Interactive Telegram StringSession helper (§5) |
| `docker-compose.yml` | ~17 | Local Postgres for dev (§6) |
| `LICENSE` | ~25 | PolyForm Strict 1.0.0 full text (§7) |

(`docs/superpowers/specs/2026-06-23-m11-railway-deployment-design.md` — this spec — is written after design approval but is not part of M11's shipped artifacts; it lives in the docs tree per the project's spec-organization convention.)

### 10.2 Files modified (4)

| Path | Change | Approx. delta |
|---|---|---|
| `pyproject.toml` | Add `signal-copier-auth` console script (§5.7) + `license = { text = "PolyForm Strict 1.0.0" }` (§7.4) | +3 lines |
| `README.md` | Add First-time setup, Local development, Operations, Verify the deployment sections; reword License stub (§8) | +~250 lines, -~5 lines |
| `Dockerfile` | Add `COPY LICENSE ./LICENSE` (§7.5) | +1 line |
| `docs/PRD.md` | Add v0.9 changelog entry to §18 noting M11's completion | +~15 lines |

### 10.3 Files explicitly NOT touched

| Path | Why untouched |
|---|---|
| `railway.toml` | M0's `restartPolicyType = "ON_FAILURE"` + `restartPolicyMaxRetries = 10` matches PRD §17.3 + §17.4. No edits. |
| `.dockerignore` | M0 excludes `.env`, `tests/`, `docs/`, etc. — correct for the deploy shape. |
| `.python-version` | Pins 3.13 for Nixpacks fallback; Dockerfile overrides anyway. |
| `src/olymptrade_ws/**` | Vendored, R-15 forbids edits. **The 12 currently-modified vendored files must be reverted or documented in `VENDORED.md` before M11 ships (§2.1 goal #8).** |
| `src/signal_copier/config.py` | M5 already loads `TELEGRAM_API_ID/HASH/PHONE/SESSION_STRING` — `auth.py` uses the existing loader. No edits. |
| `migrations/001_initial.sql` | Schema is correct; runs idempotently at startup on every deploy (M4). |
| `tests/**` | No automated tests for `auth.py` (interactive — runbook is the test). CI runs existing `pytest` suite unchanged. |

### 10.4 Total shipped LoC delta

~430 lines added across 9 files (5 new + 4 modified). Plus this spec doc (~600 lines, separate from shipped artifacts).

---

## 11. Risks

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R-1 | GitHub Actions runner flake fails a job that would otherwise pass | Low | Low | Default `continue-on-error: false`; rely on retry-on-re-run (workflow re-runs are cheap, ~2 min) |
| R-2 | `RAILWAY_TOKEN` secret leaked from GitHub | Low | High | Token is deploy-scoped at the Railway level; rotation procedure documented in README Operations → "Rotate the Railway deploy token" |
| R-3 | Auto-deploy on main breaks the live tool (broken commit) | Medium | Medium | README warning: "push to a feature branch first, merge to main when green"; M9 24h soak covers the same surface locally |
| R-4 | The auth helper fails on a non-standard Telegram account (e.g., no 2FA, no username) | Low | Low | Tested against the common path; edge cases documented in Troubleshooting table |
| R-5 | `docker compose` vs. GitHub `services:` block have different behavior | Low | Low | Both use the same `postgres:16-alpine` image, same env vars; the only difference is the network alias (compose uses service name, GH Actions uses `localhost`). M4's `test_db.py` suite already covers both shapes. |
| R-6 | CI passes but deploy fails because `RAILWAY_TOKEN` / `RAILWAY_PROJECT_ID` / `RAILWAY_SERVICE_ID` aren't set | Medium | High | README's "First-time setup" explicitly lists each; the deploy job fails fast with a clear error if any are missing |
| R-7 | `railway up --detach` returns success but the deploy actually fails asynchronously | Low | Medium | User follows deploy progress in the Railway dashboard; CI exit code reflects CLI success only |
| R-8 | Pasting a leaked StringSession into Railway exposes the user's Telegram account | Low | High | README warns "treat like a password"; rotation procedure documented in README Operations |
| R-9 | PolyForm Strict text is not the latest version (e.g., 1.0.1 exists) | Low | Low | Implementation plan fetches from polyformproject.org/licenses/strict/ at write time; pinned to whatever the official source serves |

---

## 12. Acceptance Criteria

M11 is **done** when **all** of the following are true:

1. ✅ `.github/workflows/ci.yml` exists with 5 jobs (lint, format, typecheck, test, deploy) wired as specified in §4.
2. ✅ On a push to `main`, CI runs all 5 jobs in order: lint+format+typecheck (parallel) → test (after lint+typecheck) → deploy (after test, only on push to main).
3. ✅ On a `pull_request` targeting `main`, CI runs lint+format+typecheck+test; deploy is skipped.
4. ✅ On `workflow_dispatch`, CI runs all 5 jobs; deploy fires only if explicitly requested via input.
5. ✅ `src/signal_copier/telegram/auth.py` exists; running `uv run python -m signal_copier.telegram.auth` produces a Telethon StringSession after interactive prompts (verified by running it once against the user's personal Telegram account).
6. ✅ `signal-copier-auth` console script registered in `pyproject.toml` under `[project.scripts]` (alternative invocation).
7. ✅ `docker-compose.yml` exists; `docker compose up -d` starts `postgres:16-alpine` on port 5432 with credentials matching M4's `test_db.py`.
8. ✅ `README.md` has new sections: First-time setup (deploy to Railway), Local development, Operations (logs/restart/redeploy/rotate-session/rotate-token/wipe-DB/set-limits), Verify the deployment (3 manual checks + Troubleshooting table). Existing TL;DR, third-party-vendored, and Risks sections preserved.
9. ✅ `LICENSE` file exists at repo root, containing the verbatim PolyForm Strict 1.0.0 text fetched from polyformproject.org.
10. ✅ `pyproject.toml` has `license = { text = "PolyForm Strict 1.0.0" }` (or the form `uv` accepts without warnings — implementation plan picks the working form).
11. ✅ `Dockerfile` includes `COPY LICENSE ./LICENSE` so the project license ships in the image.
12. ✅ `mypy --strict` passes on `src/signal_copier/telegram/auth.py`.
13. ✅ `ruff check` and `ruff format --check` pass on the new Python file.
14. ✅ No edits to vendored `src/olymptrade_ws/`. **The 12 currently-modified vendored files must be reverted (or, if intentional, documented in `src/olymptrade_ws/VENDORED.md` under "Local modifications" per §12.6).**
15. ✅ A CHANGELOG entry is added to PRD §18 (`v0.9 — M11 Railway deployment & project license`).
16. ✅ The 3 manual verification checks in §9 pass on a real Railway deployment (or the user explicitly accepts deferring to the 7-day soak test as a gate).
17. ✅ CI passes on a sample PR (verified by opening a PR before merging M11).

---

## 13. Out of Scope / Deferred

Items explicitly **not** included in M11 (per §2.2):

- `scripts/verify_deploy.py` (uses Railway's internal API; brittle)
- Railway `/healthz` HTTP listener (adds port + dep)
- A smoke-test bot that posts fake signals after every deploy (needs 2nd Telegram account)
- Multi-environment Railway setup (dev/prod)
- Auto-rotation of the OlympTrade access_token (PRD S-6; manual rotation procedure documented instead)
- Pre-deploy docker-build validation as a 4th parallel CI job (covered by `railway up`'s implicit build)
- Self-hosted runner for GitHub Actions (free public-repo runner minutes suffice)
- Modifying vendored `olymptrade_ws` (PRD R-15)

---

## 14. References

- PRD v0.7, §6 Tech Stack
- PRD v0.7, §7 Architecture
- PRD v0.7, §8 Configuration
- PRD v0.7, §9.4 Local development without Railway (the `docker run` command M11 mirrors with `docker-compose.yml`)
- PRD v0.7, §12.6 Vendored third-party code (R-15 license coexistence)
- PRD v0.7, §15 Build Plan M11 row + Definition of Done for v1.0
- PRD v0.7, §17 Hosting (entire section; §17.5 Railway Postgres provisioning; §17.7 First-deploy runbook)
- PRD v0.7, §18 Changelog (new v0.9 entry)
- M5 spec, D-3 (auth helper entry point)
- M9 spec (24h soak; same process lifecycle as Railway's restart policy)
- M10 spec, §3.1 (ReconnectingOlympTradeBroker wrapper)
- PolyForm Strict 1.0.0 — https://polyformproject.org/licenses/strict/1.0.0
- Telethon StringSession docs — https://docs.telethon.dev/en/stable/concepts/sessions.html

---

*End of spec.*