# M11 Railway Deployment, Runbook & PolyForm Strict License Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the M11 operational layer — CI/CD workflow on GitHub Actions, PolyForm Strict 1.0.0 project license, enhanced interactive Telegram auth helper, local-dev docker-compose, and a complete README runbook — turning M0–M10's working tool into an unattended Railway-deployed service.

**Architecture:** Five new files (`.github/workflows/ci.yml`, `src/signal_copier/telegram/auth.py` enhancements, `docker-compose.yml`, `LICENSE`, plus this plan's spec doc) and four modified files (`pyproject.toml`, `README.md`, `Dockerfile`, `docs/PRD.md`). The auth helper is **already partially implemented** at the spec's expected path — this plan enhances the existing implementation to match the spec's contract (Railway guard, `get_me()` verification, richer output). CI/CD runs lint+format+typecheck in parallel, then test (with PG service container), then auto-deploy to Railway on push to `main` only. README is restructured into a 4-section runbook (First-time setup, Local development, Operations, Verify the deployment) preserving the existing TL;DR and Risks sections. License is PolyForm Strict 1.0.0 (user-confirmed) — free use/modify/distribute, no sale.

**Tech Stack:** Python 3.13, Telethon 1.44.x, asyncpg 0.30+, uv 0.4+, ruff 0.7+, mypy 1.13+, GitHub Actions (ubuntu-latest), Railway.app, PostgreSQL 16, Docker / Docker Compose, PolyForm Strict 1.0.0.

**Spec:** `docs/superpowers/specs/2026-06-23-m11-railway-deployment-design.md`

**Pre-flight: vendored-code cleanup.** Before starting Task 1, the working tree has 12 modified vendored files (R-15 forbids edits without a `VENDORED.md` log entry). Address these BEFORE M11 ships — see **Pre-Task 0: Vendored-code cleanup** at the top of the task list.

---

## File Structure

```
signal-copier/
├── .github/                                       ← NEW
│   └── workflows/
│       └── ci.yml                                 ← NEW: 5-job CI + CD workflow
├── src/signal_copier/telegram/
│   ├── client.py                                  (existing, M5)
│   ├── listener.py                                (existing, M5)
│   └── auth.py                                    ← ENHANCED: add Railway guard, get_me verify, richer output
├── tests/
│   └── test_auth.py                               ← ENHANCED: add tests for new behavior
├── docker-compose.yml                             ← NEW: local Postgres for dev
├── LICENSE                                        ← NEW: PolyForm Strict 1.0.0 full text
├── pyproject.toml                                 ← MODIFIED: license text → PolyForm Strict
├── Dockerfile                                     ← MODIFIED: +COPY LICENSE ./LICENSE
├── README.md                                      ← MODIFIED: +First-time setup, +Local dev, +Operations, +Verify
├── docs/PRD.md                                    ← MODIFIED: §18 v0.9 changelog entry
├── railway.toml                                   UNCHANGED (M0 ships correct shape)
├── .dockerignore                                  UNCHANGED (M0 ships correct shape)
└── .python-version                                UNCHANGED (M0 ships correct shape)
```

**One small surprise:** the auth helper and its tests **already exist** at the spec's expected paths. The existing implementation is correct and idiomatic but lacks three things the spec requires: (1) Railway-detection guard, (2) `get_me()` session verification, (3) richer output banner with username/ID/security warning. This plan enhances the existing files rather than replacing them.

**No changes to:** `src/olymptrade_ws/**` (vendored, R-15), `src/signal_copier/{config.py, broker/, scheduler/, domain/, infra/, notify/, __main__.py}` (all work correctly as-is), `migrations/001_initial.sql` (idempotent, runs on every deploy).

---

## Pre-Task 0: Vendored-code cleanup (R-15)

**Files:**
- Inspect: `rtk git diff --stat`
- Revert or document: `src/olymptrade_ws/**/*.py` (12 files) + `src/olymptrade_ws/LICENSE` + `docs/tool-idea.md`

The working tree has 13 files modified outside M11's scope — 12 vendored `src/olymptrade_ws/*.py` files plus `docs/tool-idea.md` and `src/olymptrade_ws/LICENSE`. Per PRD R-15 and §12.6, vendored code cannot be edited without a modification log entry. Address before M11 ships.

- [ ] **Step 0.1: Inspect the modifications**

Run: `rtk git diff --stat`
Expected output (or similar): a list showing modifications to `src/olymptrade_ws/**/*.py` and `docs/tool-idea.md`. If empty, skip the rest of this pre-task.

- [ ] **Step 0.2: Decide: revert or document**

For each modified vendored file, ask: was the modification intentional, or is it an accidental leftover from a previous session?

- If **accidental / unknown**: revert with `rtk git checkout -- <file>`. Repeat per file.
- If **intentional**: document in `src/olymptrade_ws/VENDORED.md` under a new "Local modifications" heading (PRD §12.6 requires: date, what, why, upstream link if any). The `docs/tool-idea.md` change is outside the vendored boundary but is also out of M11 scope; revert it unless intentional.

- [ ] **Step 0.3: Verify clean state**

Run: `rtk git status --short`
Expected: ONLY the staged M11 spec commit (`A  docs/superpowers/specs/2026-06-23-m11-railway-deployment-design.md`) and any vendored cleanups from Step 0.2. No other modifications.

- [ ] **Step 0.4: Commit vendored cleanups**

```bash
rtk git add -A
rtk git commit -m "chore: revert/document vendored olymptrade_ws modifications (R-15)"
```

If nothing was modified, skip this commit.

---

## Task 1: Add `LICENSE` file (PolyForm Strict 1.0.0)

**Files:**
- Create: `LICENSE`

- [ ] **Step 1.1: Download the canonical PolyForm Strict 1.0.0 text**

Run (PowerShell, from repo root):

```powershell
Invoke-WebRequest -Uri "https://polyformproject.org/licenses/strict/1.0.0" -OutFile "LICENSE"
```

Expected: a `LICENSE` file at the repo root, ~20 lines.

- [ ] **Step 1.2: Verify the file was downloaded**

Run: `Get-Content LICENSE | Select-Object -First 3`
Expected:
```
PolyForm Strict License 1.0.0

Copyright (c) 2026
```
(actual year and copyright holder will be set in Step 1.3)

- [ ] **Step 1.3: Customize the copyright line**

Edit the file (the `LICENSE` text) to set the copyright holder. The line is currently `Copyright (c) <YEAR> <COPYRIGHT HOLDER>`. Set:
- `<YEAR>` = `2026`
- `<COPYRIGHT HOLDER>` = `signal-copier authors`

Use a text editor (or PowerShell):

```powershell
(Get-Content LICENSE) -replace '<YEAR>', '2026' | Set-Content LICENSE
(Get-Content LICENSE) -replace '<COPYRIGHT HOLDER>', 'signal-copier authors' | Set-Content LICENSE
```

Verify:

```powershell
Get-Content LICENSE | Select-String -Pattern "Copyright"
```

Expected: a line containing `Copyright (c) 2026 signal-copier authors`.

- [ ] **Step 1.4: Commit**

```bash
rtk git add LICENSE
rtk git commit -m "feat: add PolyForm Strict 1.0.0 license"
```

---

## Task 2: Update `pyproject.toml` license metadata

**Files:**
- Modify: `pyproject.toml:7` (change `license = { text = "Proprietary" }` to `license = { text = "PolyForm Strict 1.0.0" }`)

- [ ] **Step 2.1: Edit the license line**

Open `pyproject.toml` and change line 7:

```toml
license = { text = "Proprietary" }
```

to:

```toml
license = { text = "PolyForm Strict 1.0.0" }
```

- [ ] **Step 2.2: Verify the change is parseable**

Run: `uv run python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['license'])"`
Expected output: `{'text': 'PolyForm Strict 1.0.0'}`

- [ ] **Step 2.3: Verify `signal-copier-auth` console script is already registered**

Run: `uv run python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['scripts'])"`
Expected output (key order may differ):

```
{'signal-copier': 'signal_copier.__main__:main', 'signal-copier-auth': 'signal_copier.telegram.auth:main'}
```

If `signal-copier-auth` is missing, add this to the `[project.scripts]` section:

```toml
signal-copier-auth = "signal_copier.telegram.auth:main"
```

(Then re-run Step 2.2 to confirm.)

- [ ] **Step 2.4: Commit**

```bash
rtk git add pyproject.toml
rtk git commit -m "chore: declare PolyForm Strict 1.0.0 license in pyproject.toml"
```

---

## Task 3: Add `COPY LICENSE` to `Dockerfile`

**Files:**
- Modify: `Dockerfile` (add `COPY LICENSE ./LICENSE` after the `migrations/` line)

- [ ] **Step 3.1: Read the current Dockerfile**

Read the current `Dockerfile` (26 lines). Note the line `COPY migrations/ ./migrations/` near the bottom of the build section.

- [ ] **Step 3.2: Add the LICENSE copy**

Insert a new line **immediately after** the `COPY migrations/ ./migrations/` line:

```dockerfile
COPY LICENSE ./LICENSE
```

The relevant block now reads:

```dockerfile
COPY src/ ./src/
COPY migrations/ ./migrations/
COPY LICENSE ./LICENSE
```

- [ ] **Step 3.3: Verify the Dockerfile syntax with a build**

Run: `docker build -t signal-copier:test .`
Expected: build succeeds. The image contains `/app/LICENSE`. (If Docker isn't available locally, run `docker run --rm -v ${PWD}:/data alpine sh -c "cat /data/Dockerfile"` to at least inspect the file; the build itself requires Docker.)

Verify the file is in the image:

```bash
docker run --rm signal-copier:test cat /app/LICENSE | head -3
```

Expected: shows the first 3 lines of the PolyForm Strict license text.

- [ ] **Step 3.4: Commit**

```bash
rtk git add Dockerfile
rtk git commit -m "chore(docker): include LICENSE in the image"
```

---

## Task 4: Auth helper — write the failing test for the Railway guard

**Files:**
- Modify: `tests/test_auth.py` (add a test for Railway detection)
- Modify: `src/signal_copier/telegram/auth.py` (add the Railway guard — but only after the test fails)

The existing `auth.py` is missing the Railway guard (spec §5.3 step 2). Add it via TDD.

- [ ] **Step 4.1: Add the failing test**

Open `tests/test_auth.py` and append this test at the end:

```python
def test_main_refuses_to_run_on_railway(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Per spec §5.3 step 2: the helper must refuse to run on Railway.

    Detected by RAILWAY_ENVIRONMENT or RAILWAY_PROJECT_ID env vars.
    Exits with code 2 and prints a one-line instruction.
    """
    # Set valid creds so the env-var check would otherwise pass.
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc123")
    monkeypatch.setenv("TELEGRAM_PHONE", "+15551234567")
    # Simulate Railway.
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    # Clean up other env vars that Config might read from previous tests.
    for key in [
        "TELEGRAM_SESSION_STRING",
        "TELEGRAM_TARGET_CHAT",
        "DATABASE_URL",
        "OLYMP_ACCESS_TOKEN",
        "OLYMP_ACCOUNT_ID",
        "OLYMP_ACCOUNT_GROUP",
    ]:
        monkeypatch.delenv(key, raising=False)

    rc = auth.main()
    assert rc == 2
    err = capsys.readouterr().err
    assert "run this locally" in err.lower()
    assert "railway" in err.lower()
```

- [ ] **Step 4.2: Run the test to verify it fails**

Run: `uv run pytest tests/test_auth.py::test_main_refuses_to_run_on_railway -v`
Expected: FAIL with an `AssertionError` on `assert rc == 2` (existing `main()` returns 0 when creds are valid, or returns 1/2 only on auth failure). The exact failure mode is the wrong return code.

- [ ] **Step 4.3: Run all auth tests to confirm no other test regressed**

Run: `uv run pytest tests/test_auth.py -v`
Expected: the new test FAILS; the 5 existing tests PASS.

---

## Task 5: Auth helper — implement the Railway guard

**Files:**
- Modify: `src/signal_copier/telegram/auth.py` (add the Railway check to `main()`)

- [ ] **Step 5.1: Add the Railway detection helper at module top**

Open `src/signal_copier/telegram/auth.py`. Immediately after the `from signal_copier.telegram.client import TelegramConfigError` import (line 12), add:

```python
def _is_running_on_railway() -> bool:
    """Return True if this process is running on Railway.app.

    Detected by the presence of either RAILWAY_ENVIRONMENT or
    RAILWAY_PROJECT_ID env vars, which Railway always injects into
    its containers.
    """
    import os
    return bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_PROJECT_ID"))
```

- [ ] **Step 5.2: Add the guard to `main()`**

In `main()`, **immediately after** the existing `_read_creds()` block (after the `except TelegramConfigError as exc:` block ending with `return 2`, before `if api_id == 0 ...`), insert:

```python
    if _is_running_on_railway():
        sys.stderr.write(
            "❌ Do not run this on Railway. Run `python -m signal_copier.telegram.auth` "
            "locally and paste the printed TELEGRAM_SESSION_STRING into your Railway "
            "Variables.\n"
        )
        return 2
```

The full `main()` now reads (showing the new structure):

```python
def main() -> int:
    """..."""
    try:
        api_id, api_hash, phone = _read_creds()
    except (ValidationError, ValueError) as exc:
        sys.stderr.write(...)
        return 2
    except TelegramConfigError as exc:
        sys.stderr.write(...)
        return 2

    if api_id == 0 or not api_hash or not phone:
        sys.stderr.write(...)
        return 2

    # NEW: Railway guard
    if _is_running_on_railway():
        sys.stderr.write(...)
        return 2

    try:
        session_str = asyncio.run(...)
    # ... rest unchanged
```

- [ ] **Step 5.3: Run the new test to verify it passes**

Run: `uv run pytest tests/test_auth.py::test_main_refuses_to_run_on_railway -v`
Expected: PASS.

- [ ] **Step 5.4: Run all auth tests to confirm no regression**

Run: `uv run pytest tests/test_auth.py -v`
Expected: all 6 tests PASS (5 existing + 1 new).

- [ ] **Step 5.5: Run mypy on the auth module**

Run: `uv run mypy --strict src/signal_copier/telegram/auth.py`
Expected: `Success: no issues found in 1 source file`.

If mypy complains about `import os` inside the function, move it to the top of the file with the other imports.

- [ ] **Step 5.6: Run ruff**

Run: `uv run ruff check src/signal_copier/telegram/auth.py tests/test_auth.py`
Expected: `All checks passed!`

Run: `uv run ruff format --check src/signal_copier/telegram/auth.py tests/test_auth.py`
Expected: `1 file would be unchanged` (or similar — both files are already formatted).

If formatting is off, run `uv run ruff format src/signal_copier/telegram/auth.py tests/test_auth.py` to fix.

- [ ] **Step 5.7: Commit**

```bash
rtk git add src/signal_copier/telegram/auth.py tests/test_auth.py
rtk git commit -m "feat(auth): refuse to run on Railway; redirect to local invocation"
```

---

## Task 6: Auth helper — write the failing test for combined auth+verify + rich banner

**Files:**
- Modify: `tests/test_auth.py` (add a test for combined auth+verify returning `(session_str, user)` and the rich banner output)

The existing `auth.py` uses `client.start(phone=phone)` for the interactive flow but does **not** call `client.get_me()` to verify the session. The spec requires verification before printing. Telethon clients are bound to the event loop they were created in, so verification must happen in the **same** event loop as auth — combine them into a single `_do_auth_and_verify` coroutine.

- [ ] **Step 6.1: Add the failing test**

Open `tests/test_auth.py` and append:

```python
def test_main_verifies_session_and_prints_rich_banner(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Per spec §5.3 step 7 + step 9: the helper must verify the session
    via get_me() and print a rich banner with user info + security warning.
    The combined _do_auth_and_verify coroutine returns (session_str, user).
    """
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc123")
    monkeypatch.setenv("TELEGRAM_PHONE", "+15551234567")
    for key in [
        "TELEGRAM_SESSION_STRING",
        "TELEGRAM_TARGET_CHAT",
        "DATABASE_URL",
        "OLYMP_ACCESS_TOKEN",
        "OLYMP_ACCOUNT_ID",
        "OLYMP_ACCOUNT_GROUP",
        "RAILWAY_ENVIRONMENT",
        "RAILWAY_PROJECT_ID",
    ]:
        monkeypatch.delenv(key, raising=False)

    SESSION = "AAAAfakebase64session=="
    fake_user = type("User", (), {
        "first_name": "Alice",
        "last_name": "Tester",
        "username": "alicehandle",
        "id": 987654321,
    })()

    async def _success_auth_and_verify(*args: object, **kwargs: object) -> tuple[str, object]:
        return SESSION, fake_user

    with patch.object(auth, "_do_auth_and_verify", side_effect=_success_auth_and_verify):
        rc = auth.main()

    assert rc == 0
    out = capsys.readouterr().out
    assert "Alice Tester" in out
    assert "@alicehandle" in out
    assert "987654321" in out
    assert f"TELEGRAM_SESSION_STRING={SESSION}" in out
    assert "Treat the session string like a password" in out
```

- [ ] **Step 6.2: Run the test to verify it fails**

Run: `uv run pytest tests/test_auth.py::test_main_verifies_session_and_prints_rich_banner -v`
Expected: FAIL. Two possible failure modes (the exact one depends on the existing state of `auth.py`):
- `AttributeError: module 'signal_copier.telegram.auth' has no attribute '_do_auth_and_verify'`
- OR `AssertionError` on one of the output assertions (if the implementation didn't add the rich banner)

Either is acceptable as the "red" step.

---

## Task 7: Auth helper — implement combined auth+verify and rich banner

**Files:**
- Modify: `src/signal_copier/telegram/auth.py` (replace `_do_auth` with `_do_auth_and_verify`; restructure `main()` to print the rich banner with user info + security warning)

- [ ] **Step 7.1: Replace `_do_auth` with `_do_auth_and_verify`**

Open `src/signal_copier/telegram/auth.py`. Replace the existing `_do_auth` (lines 33-39) with:

```python
async def _do_auth_and_verify(
    api_id: int, api_hash: str, phone: str
) -> tuple[str, object]:
    """Run Telethon interactive auth + verify session, all in one event loop.

    Telethon clients are bound to the event loop they were created in;
    calling get_me() on a client from a different loop will fail. So we
    do everything in one loop: connect → interactive auth → save session
    → verify via get_me() → disconnect. Returns (session_string, user).
    """
    client = _TelethonClient(StringSession(), api_id, api_hash)
    try:
        await client.start(phone=phone)  # interactive: prompts for code + 2FA
        session_str = cast(str, client.session.save())
        user = await client.get_me()  # verify the session works
        return session_str, user
    finally:
        await client.disconnect()
```

- [ ] **Step 7.2: Update `main()` to use the new shape**

Replace the existing `main()` body (lines 42-90) with:

```python
def main() -> int:
    """Entry point for `python -m signal_copier.telegram.auth`.

    Reads credentials from .env, refuses to run on Railway, runs the
    Telethon interactive auth flow, verifies the session via get_me(),
    and prints the resulting StringSession to stdout with a rich banner.

    Exits 0 on success, 1 on auth/verification failure, 2 on config or
    Railway-guard error.
    """
    try:
        api_id, api_hash, phone = _read_creds()
    except (ValidationError, ValueError) as exc:
        sys.stderr.write(
            f"❌ Config validation failed; check API_ID / API_HASH / PHONE in .env:\n{exc}\n"
        )
        return 2
    except TelegramConfigError as exc:
        sys.stderr.write(f"❌ {exc}\n")
        return 2

    if api_id == 0 or not api_hash or not phone:
        sys.stderr.write(
            "❌ TELEGRAM_API_ID / TELEGRAM_API_HASH / TELEGRAM_PHONE must be set in .env\n"
            "   Get API_ID and API_HASH from https://my.telegram.org\n"
        )
        return 2

    if _is_running_on_railway():
        sys.stderr.write(
            "❌ Do not run this on Railway. Run `python -m signal_copier.telegram.auth` "
            "locally and paste the printed TELEGRAM_SESSION_STRING into your Railway "
            "Variables.\n"
        )
        return 2

    try:
        session_str, user = asyncio.run(
            asyncio.wait_for(
                _do_auth_and_verify(api_id, api_hash, phone),
                timeout=_AUTH_TIMEOUT_SECONDS,
            )
        )
    except TelegramConfigError as exc:
        sys.stderr.write(f"❌ {exc}\n")
        return 2
    except TimeoutError:
        sys.stderr.write(
            f"❌ Auth timed out after {_AUTH_TIMEOUT_SECONDS}s; run again and "
            "respond to the prompts more quickly.\n"
        )
        return 1
    except Exception as exc:
        sys.stderr.write(
            f"❌ Telegram auth or verify failed: {type(exc).__name__}: {exc}\n"
        )
        return 1

    full_name = " ".join(
        filter(None, [getattr(user, "first_name", None), getattr(user, "last_name", None)])
    ).strip()
    username = f"@{user.username}" if getattr(user, "username", None) else "(no username)"
    user_id = getattr(user, "id", "?")

    print("=" * 70)
    print(f"Authenticated as: {full_name or '(no name)'} ({username})")
    print(f"User ID: {user_id}")
    print("=" * 70)
    print("Set this as TELEGRAM_SESSION_STRING in your Railway Variables:")
    print()
    print(f"TELEGRAM_SESSION_STRING={session_str}")
    print()
    print("⚠️  Treat the session string like a password. Anyone with it can read")
    print("   and send messages from your Telegram account.")
    print()
    print("Then redeploy: git commit --allow-empty -m 'rotate session' && git push")
    print("Or trigger a manual redeploy from the Railway dashboard.")
    print("=" * 70)
    return 0
```

- [ ] **Step 7.3: Update existing tests that mock `_do_auth`**

The two existing tests that mock `_do_auth` (`test_main_returns_1_on_auth_failure` and `test_main_prints_session_string_on_success`) need to mock the new `_do_auth_and_verify` instead.

Open `tests/test_auth.py` and replace the two existing tests with:

```python
def test_main_returns_1_on_auth_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc123")
    monkeypatch.setenv("TELEGRAM_PHONE", "+15551234567")
    for key in [
        "TELEGRAM_SESSION_STRING",
        "TELEGRAM_TARGET_CHAT",
        "DATABASE_URL",
        "OLYMP_ACCESS_TOKEN",
        "OLYMP_ACCOUNT_ID",
        "OLYMP_ACCOUNT_GROUP",
        "RAILWAY_ENVIRONMENT",
        "RAILWAY_PROJECT_ID",
    ]:
        monkeypatch.delenv(key, raising=False)

    async def _failing(*args: object, **kwargs: object) -> tuple[str, object]:
        raise RuntimeError("simulated auth failure")

    with patch.object(auth, "_do_auth_and_verify", side_effect=_failing):
        rc = auth.main()

    assert rc == 1
    err = capsys.readouterr().err
    assert "auth or verify failed" in err.lower()
    assert "simulated auth failure" in err


def test_main_prints_session_string_on_success(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc123")
    monkeypatch.setenv("TELEGRAM_PHONE", "+15551234567")
    for key in [
        "TELEGRAM_SESSION_STRING",
        "TELEGRAM_TARGET_CHAT",
        "DATABASE_URL",
        "OLYMP_ACCESS_TOKEN",
        "OLYMP_ACCOUNT_ID",
        "OLYMP_ACCOUNT_GROUP",
        "RAILWAY_ENVIRONMENT",
        "RAILWAY_PROJECT_ID",
    ]:
        monkeypatch.delenv(key, raising=False)

    SESSION = "AAAAfakebase64session=="
    fake_user = type("User", (), {
        "first_name": "Bob",
        "last_name": "Builder",
        "username": "bobbuilds",
        "id": 111222333,
    })()

    async def _success(*args: object, **kwargs: object) -> tuple[str, object]:
        return SESSION, fake_user

    with patch.object(auth, "_do_auth_and_verify", side_effect=_success):
        rc = auth.main()

    assert rc == 0
    out = capsys.readouterr().out
    assert f"TELEGRAM_SESSION_STRING={SESSION}" in out
    assert "Bob Builder" in out
    assert "@bobbuilds" in out
    assert "111222333" in out
    assert "Treat the session string like a password" in out
```

(The `test_main_returns_2_on_zero_api_id` and `test_main_returns_2_on_missing_config` tests do NOT need changes — they return before reaching `_do_auth_and_verify`. The `test_main_refuses_to_run_on_railway` test from Task 4 also doesn't need changes.)

- [ ] **Step 7.4: Run all auth tests**

Run: `uv run pytest tests/test_auth.py -v`
Expected: all 6 tests PASS (2 unchanged + 1 Railway guard + 1 rich banner + 2 rewritten success/failure).

- [ ] **Step 7.5: Run mypy + ruff**

Run: `uv run mypy --strict src/signal_copier/telegram/auth.py`
Expected: `Success: no issues found in 1 source file`.

Run: `uv run ruff check src/signal_copier/telegram/auth.py tests/test_auth.py`
Expected: `All checks passed!`

Run: `uv run ruff format --check src/signal_copier/telegram/auth.py tests/test_auth.py`
Expected: `2 files would be unchanged`.

- [ ] **Step 7.6: Commit**

```bash
rtk git add src/signal_copier/telegram/auth.py tests/test_auth.py
rtk git commit -m "feat(auth): verify session via get_me() and print rich banner"
```

---

## Task 8: Auth helper — verify end-to-end (smoke test in a non-interactive way)

**Files:**
- No code changes. Verification only.

- [ ] **Step 8.1: Confirm the console script works**

Run: `uv run signal-copier-auth --help 2>&1 | head -5` (or `uv run python -m signal_copier.telegram.auth --help`)
Expected: the helper runs and either prints help (if implemented) or starts prompting (it will hang on the first input prompt — that's fine). Press Ctrl+C to exit.

- [ ] **Step 8.2: Verify the import path is correct**

Run: `uv run python -c "from signal_copier.telegram import auth; print(auth.main.__name__)"`
Expected: `main`

- [ ] **Step 8.3: Run the full test suite to confirm no regression**

Run: `uv run pytest -x`
Expected: all tests pass (the 7 auth tests plus the ~108 existing tests from M0–M10).

If any non-auth test fails, the change in Task 7 is too invasive. Revert the commit and re-evaluate.

- [ ] **Step 8.4: Commit (if any test-only changes were needed)**

If Step 8.3 required test-only fixes, commit them:

```bash
rtk git add tests/
rtk git commit -m "test: fix non-auth tests broken by auth.py refactor"
```

(Usually a no-op if Step 8.3 was clean.)

---

## Task 9: Add `docker-compose.yml` for local dev

**Files:**
- Create: `docker-compose.yml`

- [ ] **Step 9.1: Write the file**

Create `docker-compose.yml` at the repo root with this exact content:

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

- [ ] **Step 9.2: Validate the YAML**

Run: `docker compose config`
Expected: a parsed-and-expanded YAML echo of the file, with no errors. (The `container_name`, ports, and volume will appear unchanged.)

- [ ] **Step 9.3: Start the Postgres container**

Run: `docker compose up -d`
Expected: `Container signal-copier-pg ... Started` within ~5 seconds.

- [ ] **Step 9.4: Verify Postgres is accepting connections**

Run: `docker compose exec postgres pg_isready -U copier`
Expected: `accepting connections`

- [ ] **Step 9.5: Verify a query works**

Run:
```bash
docker compose exec postgres psql -U copier -d copier -c "SELECT 1 AS one;"
```
Expected:
```
 one
-----
   1
(1 row)
```

- [ ] **Step 9.6: Stop the container (data persists in the named volume)**

Run: `docker compose down`
Expected: `Container signal-copier-pg ... Removed`. The `copier-pg-data` volume persists.

- [ ] **Step 9.7: Commit**

```bash
rtk git add docker-compose.yml
rtk git commit -m "feat(dev): add docker-compose for local Postgres"
```

---

## Task 10: Add `.github/workflows/ci.yml` (all 5 jobs at once)

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 10.1: Create the directory**

Run: `New-Item -ItemType Directory -Path ".github/workflows" -Force`
Expected: directory exists.

- [ ] **Step 10.2: Write the file**

Create `.github/workflows/ci.yml` with this exact content:

```yaml
name: ci

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
  workflow_dispatch:
    inputs:
      deploy:
        description: "Run the deploy job (Railway)"
        required: false
        default: "false"
        type: choice
        options:
          - "true"
          - "false"

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}

env:
  PYTHON_VERSION: "3.13"

jobs:
  lint:
    name: ruff lint
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Install uv
        uses: astral-sh/setup-uv@v4
        with:
          enable-cache: true
      - name: Sync deps
        run: uv sync --frozen
      - name: Run ruff check
        run: uv run ruff check

  format:
    name: ruff format --check
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Install uv
        uses: astral-sh/setup-uv@v4
        with:
          enable-cache: true
      - name: Sync deps
        run: uv sync --frozen
      - name: Check formatting
        run: uv run ruff format --check

  typecheck:
    name: mypy --strict
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Install uv
        uses: astral-sh/setup-uv@v4
        with:
          enable-cache: true
      - name: Sync deps
        run: uv sync --frozen
      - name: Run mypy
        run: uv run mypy --strict src tests

  test:
    name: pytest
    runs-on: ubuntu-latest
    needs: [lint, typecheck]
    services:
      postgres:
        image: postgres:16-alpine
        env:
          POSTGRES_USER: copier
          POSTGRES_PASSWORD: copier
          POSTGRES_DB: copier
        ports:
          - 5432:5432
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
    env:
      DATABASE_URL: postgresql://copier:copier@localhost:5432/copier
    steps:
      - uses: actions/checkout@v4
      - name: Install uv
        uses: astral-sh/setup-uv@v4
        with:
          enable-cache: true
      - name: Sync deps
        run: uv sync --frozen
      - name: Run tests
        run: uv run pytest

  deploy:
    name: deploy to Railway
    runs-on: ubuntu-latest
    needs: [test]
    if: |
      (github.event_name == 'push' && github.ref == 'refs/heads/main') ||
      (github.event_name == 'workflow_dispatch' && inputs.deploy == 'true')
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - name: Install Railway CLI
        run: npm install -g @railway/cli
      - name: Deploy
        env:
          RAILWAY_TOKEN: ${{ secrets.RAILWAY_TOKEN }}
        run: |
          railway up \
            --project "${{ vars.RAILWAY_PROJECT_ID }}" \
            --service "${{ vars.RAILWAY_SERVICE_ID }}" \
            --environment production \
            --detach
```

- [ ] **Step 10.3: Validate YAML syntax**

Run (PowerShell):
```powershell
uv run python -c "import yaml; print(yaml.safe_load(open('.github/workflows/ci.yml').read())['jobs']['test']['services']['postgres']['image'])"
```
Expected: `postgres:16-alpine`

- [ ] **Step 10.4: Verify ruff doesn't try to lint the workflow file**

Run: `uv run ruff check .github/workflows/ci.yml`
Expected: `All checks passed!` (or `not found` if ruff skips `.github/`). The file is YAML, not Python.

- [ ] **Step 10.5: Verify the workflow file is in the spec's required location**

Run: `Get-Content .github/workflows/ci.yml | Select-Object -First 5`
Expected: shows the `name: ci` and `on:` block at the top.

- [ ] **Step 10.6: Commit**

```bash
rtk git add .github/workflows/ci.yml
rtk git commit -m "feat(ci): add GitHub Actions workflow with lint/format/typecheck/test/deploy"
```

- [ ] **Step 10.7: Note the GitHub-side setup**

The CI workflow requires the following GitHub repository secrets and variables to actually run on a push:

| Type | Name | Source |
|---|---|---|
| Secret | `RAILWAY_TOKEN` | https://railway.app/account/tokens (generate a deploy-scoped token) |
| Variable | `RAILWAY_PROJECT_ID` | Railway project URL (numeric, in the URL) |
| Variable | `RAILWAY_SERVICE_ID` | Railway service URL (numeric) |

These are set in the GitHub repo's Settings → Secrets and variables → Actions tab. M11's README will document this.

---

## Task 11: README — preserve existing content + add the table of contents

**Files:**
- Modify: `README.md`

- [ ] **Step 11.1: Read the current README**

Open `README.md` (currently 42 lines).

- [ ] **Step 11.2: Add a Table of Contents after the "Status" section**

The current README's structure:
1. Title
2. Status
3. How it works (TL;DR)
4. Third-party dependency — vendored
5. ⚠️ Risks
6. License

Insert a new `## Contents` section **between "Status" and "How it works (TL;DR)"** with this content:

```markdown
## Contents
- [How it works (TL;DR)](#how-it-works-tldr)
- [Third-party dependency — vendored](#third-party-dependency--vendored)
- [First-time setup (deploy to Railway)](#first-time-setup-deploy-to-railway)
- [Local development](#local-development)
- [Operations](#operations)
- [Verify the deployment](#verify-the-deployment)
- [Risks](#%E2%9A%A0%EF%B8%8F-risks)
- [License](#license)
```

- [ ] **Step 11.3: Update the License section to reference the new file**

Find the current License section (the last block):

```markdown
## License

Project license TBD. The vendored `olymptrade_ws/` retains its original MIT license — see [`src/olymptrade_ws/LICENSE`](src/olymptrade_ws/LICENSE).
```

Replace with:

```markdown
## License

This project is licensed under [PolyForm Strict 1.0.0](LICENSE). Free to use, modify, and distribute; you may not sell this work or any derivative work.

The vendored `olymptrade_ws/` retains its original MIT license — see [`src/olymptrade_ws/LICENSE`](src/olymptrade_ws/LICENSE).
```

- [ ] **Step 11.4: Commit**

```bash
rtk git add README.md
rtk git commit -m "docs(readme): add table of contents and link to PolyForm Strict license"
```

---

## Task 12: README — add the "First-time setup" section

**Files:**
- Modify: `README.md` (insert the First-time setup section between the "Third-party dependency" and the "⚠️ Risks" sections)

- [ ] **Step 12.1: Insert the First-time setup section**

In `README.md`, **immediately after** the "Third-party dependency — vendored" section (which ends with the line "See [`src/olymptrade_ws/VENDORED.md`](src/olymptrade_ws/VENDORED.md) for the upstream source...") and **before** the `## ⚠️ Risks` heading, insert:

```markdown
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

```

- [ ] **Step 12.2: Verify the section is in the file**

Run (PowerShell):

```powershell
Get-Content README.md | Select-String -Pattern "First-time setup"
```

Expected: a line containing `## First-time setup (deploy to Railway)`.

- [ ] **Step 12.3: Commit**

```bash
rtk git add README.md
rtk git commit -m "docs(readme): add First-time setup (deploy to Railway) section"
```

---

## Task 13: README — add the "Local development" section

**Files:**
- Modify: `README.md` (insert the Local development section between "First-time setup" and "⚠️ Risks")

- [ ] **Step 13.1: Insert the Local development section**

In `README.md`, **immediately after** the "First-time setup" section's last paragraph (the one ending "PG state survives redeploys.") and **before** the `## ⚠️ Risks` heading, insert:

```markdown
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

```

- [ ] **Step 13.2: Verify the section is in the file**

Run: `Get-Content README.md | Select-String -Pattern "Local development"`
Expected: a line containing `## Local development`.

- [ ] **Step 13.3: Commit**

```bash
rtk git add README.md
rtk git commit -m "docs(readme): add Local development section"
```

---

## Task 14: README — add the "Operations" section

**Files:**
- Modify: `README.md` (insert the Operations section between "Local development" and "⚠️ Risks")

- [ ] **Step 14.1: Insert the Operations section**

In `README.md`, **immediately after** the Local development section's last block (the `uv run mypy --strict` block) and **before** the `## ⚠️ Risks` heading, insert:

```markdown
## Operations

### View logs

```bash
railway logs --tail              # or open the service's Logs tab in Railway
```

Logs are also mirrored to the Telegram self-DM at every state transition (PRD §4.7). Use whichever is more convenient.

### Restart the bot

```bash
railway restart                   # or click "Restart" in the dashboard
```

Useful if the bot gets into a wedged state that auto-restart didn't fix (e.g., a DB connection that the pool didn't recover from after ~30s).

### Trigger a manual redeploy

Either push a no-op commit:

```bash
git commit --allow-empty -m "trigger redeploy"
git push origin main
```

Or use the Railway dashboard's "Restart" button (same effect for a single-service app).

### Rotate the Telegram session

If your session string was leaked, or Telegram reset it (rare):

```bash
uv run python -m signal_copier.telegram.auth
# paste the new value into Railway Variables → TELEGRAM_SESSION_STRING
# restart the service
```

### Rotate the OlympTrade access_token

Tokens expire periodically (OlympTrade may force-rotate; sessions can also time out). To rotate:

1. Open OlympTrade in a browser, log in.
2. DevTools → Network → any authenticated request → copy the JWT from the `Authorization: Bearer ...` header.
3. Paste into Railway Variables → `OLYMP_ACCESS_TOKEN`.
4. Restart the service.

The M10 self-healing reconnect supervisor (PRD §15 M10) handles transient WS drops without restart; a true token rejection requires a restart.

### Wipe the database (destructive)

Use the Railway Postgres service's Data tab → query editor:

```sql
DROP TABLE stages; DROP TABLE signals; DROP TABLE daily_summary;
```

The bot recreates all three on next startup (idempotent migrations). Use this only for a clean slate; you'll lose all trade history.

### Set daily safety limits

Edit env vars (any of):

- `DAILY_LOSS_LIMIT=50.00` — halt after $50 of realized losses today
- `DAILY_TRADE_LIMIT=50` — halt after 50 trades today
- `DAILY_DRAWDOWN_PCT=20` — halt after 20% drawdown

`0` (default) = disabled. Restart the service to pick up new values.

```

- [ ] **Step 14.2: Verify the section is in the file**

Run: `Get-Content README.md | Select-String -Pattern "## Operations"`
Expected: a line containing `## Operations`.

- [ ] **Step 14.3: Commit**

```bash
rtk git add README.md
rtk git commit -m "docs(readme): add Operations section (logs, restart, redeploy, rotation)"
```

---

## Task 15: README — add the "Verify the deployment" section

**Files:**
- Modify: `README.md` (insert between "Operations" and "⚠️ Risks")

- [ ] **Step 15.1: Insert the Verify the deployment section**

In `README.md`, **immediately after** the Operations section's last block (the `DAILY_DRAWDOWN_PCT=20` block) and **before** the `## ⚠️ Risks` heading, insert:

```markdown
## Verify the deployment

Run these three checks once after the first deploy. Each takes ~30 seconds. If any fails, see the Troubleshooting table below.

### 1. Confirm the bot is connected and listening

```bash
railway logs --tail | grep "Bot started"
```

You should see one line:

```
2026-06-23 14:00:00 | INFO  | signal_copier: Bot started. Mode=dry_run, watching=@analyst_channel
```

If you don't see this within 2 minutes of the last deploy, something is wrong — see Troubleshooting.

### 2. Confirm restart-on-crash works

```bash
# Controlled restart (not a crash, but exercises the same restart path)
railway restart
```

You should see, in `railway logs --tail`:

- The process exiting (`SIGTERM received`)
- A new process starting within ~10s
- `Bot started` again with a fresh PID
- No "Token expired" or "Postgres connection lost" errors

A "real" crash (OOM, panic, segfault) is exercised by Railway's auto-restart policy on its own — you don't need to test it manually. The Railway dashboard → service → "Restart" button uses the same path.

### 3. Confirm data survives redeploys

In Railway Postgres Data tab → query editor, insert a test row:

```sql
INSERT INTO signals (signal_id, pair, direction, trigger_hhmm, trigger_ts_unix,
  expiration_seconds, received_at_unix, status, created_at_unix, updated_at_unix)
VALUES ('test-deploy-001', 'EUR/JPY', 'up', '12:00', EXTRACT(EPOCH FROM NOW()),
  300, EXTRACT(EPOCH FROM NOW()), 'pending', EXTRACT(EPOCH FROM NOW()),
  EXTRACT(EPOCH FROM NOW()));
```

Then trigger a redeploy:

```bash
git commit --allow-empty -m "verify deploy"
git push origin main
```

After the new deploy finishes (~1 minute), query again:

```sql
SELECT signal_id, status FROM signals WHERE signal_id = 'test-deploy-001';
```

You should see `test-deploy-001` still there with `status='pending'`. Clean it up:

```sql
DELETE FROM signals WHERE signal_id = 'test-deploy-001';
```

### Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `Bot started` doesn't appear in logs | Telegram session invalid; or API_ID/API_HASH wrong | Re-run `python -m signal_copier.telegram.auth`; paste new session string |
| `Database connection failed` in logs | `DATABASE_URL` not set on the service | Check Variables tab; the Postgres service should auto-inject it via `${{Postgres.DATABASE_URL}}` |
| `OLYMP_ACCOUNT_GROUP=real + DRY_RUN=false → refusing to start` | You tried to enable real-money | Set `OLYMP_ACCOUNT_GROUP=demo` AND `DRY_RUN=true` (per PRD §4.6 FR-6.6) |
| Bot DMs "Asset map empty — no instruments" | OlympTrade token expired or rejected | Extract new JWT from browser; paste into `OLYMP_ACCESS_TOKEN` |
| Restart loop (deploy → crash → restart → crash) | Usually a misconfigured env var | Check the deploy logs in Railway; look for the line just before the crash |
| `FloodWaitError: 3600` in Telegram listener | Personal account being rate-limited | Halve the signal channel's traffic; the bot is a member, not a poster |

```

- [ ] **Step 15.2: Verify the section is in the file**

Run: `Get-Content README.md | Select-String -Pattern "Verify the deployment"`
Expected: a line containing `## Verify the deployment`.

- [ ] **Step 15.3: Verify the README is well-formed**

Run: `Get-Content README.md | Measure-Object -Line`
Expected: ~290 lines (vs. the original 42).

- [ ] **Step 15.4: Commit**

```bash
rtk git add README.md
rtk git commit -m "docs(readme): add Verify the deployment section + troubleshooting table"
```

---

## Task 16: PRD §18 changelog entry for M11

**Files:**
- Modify: `docs/PRD.md` (add a new v0.9 section to §18 Changelog)

- [ ] **Step 16.1: Locate §18 Changelog**

The Changelog is at the end of `docs/PRD.md`. The most recent entry is `v0.8 — M10 self-healing OlympTrade reconnect supervisor` (lines ~909-914 of the current file). It ends with a separator `---`.

- [ ] **Step 16.2: Insert the v0.9 entry**

Insert this block **immediately after** the v0.8 entry's closing `---` and **before** the next v0.x entry or the end of the file:

```markdown
### v0.9 — M11 Railway deployment, runbook & project license

- **M11 complete.** The operational layer for shipping the tool as an unattended Railway service: GitHub Actions CI/CD (5 jobs: lint, format, typecheck, test with PG service container, deploy-on-push-to-main), interactive `python -m signal_copier.telegram.auth` helper (now with Railway-detection guard, `get_me()` session verification, and rich output banner with security warning), `docker-compose.yml` for local Postgres dev, complete README runbook (First-time setup, Local development, Operations, Verify the deployment, Troubleshooting), and **PolyForm Strict 1.0.0** as the project license (closes the "Project license TBD" hole from the README).
- **M11 spec:** `docs/superpowers/specs/2026-06-23-m11-railway-deployment-design.md`. Plan: `docs/superpowers/plans/2026-06-23-m11-railway-deployment.md`. No edits to vendored `olymptrade_ws/` (R-15).
- **License:** PolyForm Strict 1.0.0 — free use/modify/distribute; no sale of the work or any derivative. See `LICENSE` and the License section in the README. Compatible with the vendored `olymptrade_ws` MIT license; both license texts are present in the repo and `COPY`'d into the Docker image.
```

- [ ] **Step 16.3: Verify the entry was added**

Run: `Get-Content docs/PRD.md | Select-String -Pattern "v0.9 — M11"`
Expected: a line containing the v0.9 heading.

- [ ] **Step 16.4: Commit**

```bash
rtk git add docs/PRD.md
rtk git commit -m "docs(prd): add v0.9 changelog entry for M11 Railway deployment"
```

---

## Task 17: End-to-end verification (pre-shipment checklist)

**Files:**
- No code changes. Verification only.

This task walks through the M11 spec's Acceptance Criteria (§12) one by one. The CI + auto-deploy is the only criterion that **cannot** be verified locally — it requires a GitHub repo and Railway project. Mark that one as "deferred to first real deploy" and verify all others.

- [ ] **Step 17.1: Run the full local test suite**

Run: `uv run pytest`
Expected: all tests pass (existing ~108 + the 7 auth tests from Tasks 4-7). No regressions.

- [ ] **Step 17.2: Run lint, format, typecheck locally**

Run:
```bash
uv run ruff check
uv run ruff format --check
uv run mypy --strict src tests
```
Expected: all three exit 0 with no errors.

- [ ] **Step 17.3: Verify LICENSE is at the repo root**

Run: `Get-Content LICENSE | Measure-Object -Line`
Expected: ~20 lines (the PolyForm Strict text).

- [ ] **Step 17.4: Verify docker-compose works**

Run:
```bash
docker compose up -d
docker compose exec postgres pg_isready -U copier
docker compose down
```
Expected: `accepting connections` from pg_isready; clean shutdown.

- [ ] **Step 17.5: Verify Dockerfile LICENSE inclusion (if Docker is available)**

Run:
```bash
docker build -t signal-copier:m11 .
docker run --rm signal-copier:m11 cat /app/LICENSE | head -3
```
Expected: the first 3 lines of the PolyForm Strict license text. (If Docker isn't available locally, defer this to the first real deploy.)

- [ ] **Step 17.6: Verify the CI workflow file is valid**

Run: `uv run python -c "import yaml; print(list(yaml.safe_load(open('.github/workflows/ci.yml').read())['jobs'].keys()))"`
Expected: `['lint', 'format', 'typecheck', 'test', 'deploy']`

- [ ] **Step 17.7: Verify no vendored code was modified during M11**

Run: `rtk git diff --stat main..HEAD -- src/olymptrade_ws/`
Expected: empty output (or only modifications that were intentional and documented in `VENDORED.md` per the Pre-Task 0 cleanup).

- [ ] **Step 17.8: Verify the spec's 12 verifiables that can be checked locally**

Walk through the spec's §12 Acceptance Criteria and check each that can be verified locally:

| # | Criterion | Local check |
|---|---|---|
| 1 | `.github/workflows/ci.yml` exists with 5 jobs | Step 17.6 |
| 2 | Push to main runs all 5 jobs | DEFERRED (first real push) |
| 3 | PR runs 4 jobs, skips deploy | DEFERRED (first real PR) |
| 4 | workflow_dispatch opt-in deploy | DEFERRED (first real dispatch) |
| 5 | `auth.py` produces StringSession | DEFERRED (manual run with real Telegram creds) |
| 6 | `signal-copier-auth` registered | Step 2.3 (already verified) |
| 7 | docker-compose works | Step 17.4 |
| 8 | README has 4 new sections | Steps 12-15 (already committed) |
| 9 | LICENSE file exists | Step 17.3 |
| 10 | pyproject.toml license text | Step 2.2 (already verified) |
| 11 | Dockerfile includes LICENSE | Step 17.5 |
| 12 | mypy strict passes | Step 17.2 |
| 13 | ruff check + format pass | Step 17.2 |
| 14 | No vendored edits | Step 17.7 |
| 15 | PRD §18 changelog entry | Step 16.3 (already verified) |
| 16 | 3 manual deploy checks pass | DEFERRED (first real deploy) |
| 17 | CI passes on a sample PR | DEFERRED (first real PR) |

- [ ] **Step 17.9: Final commit (if any cleanup needed)**

If any step required a small fix, commit it:

```bash
rtk git status --short
# If anything shows up, address it, then:
rtk git add -A
rtk git commit -m "chore: M11 pre-shipment cleanup"
```

(Usually a no-op if the prior tasks were clean.)

- [ ] **Step 17.10: M11 is ready to ship**

All locally-verifiable criteria pass. The 4 DEFERRED criteria require:

1. A real GitHub repo with the three secrets/variables set (per Step 10.7).
2. A real Railway project linked to that repo.
3. A first real push to `main` to trigger CI.
4. The 3 manual deploy checks from the README.

Per the spec's gate model, the **definitive** M11 acceptance is the 7-day soak test that follows.

---

## Summary

| Task | Files | Approx. LoC |
|---|---|---|
| Pre-Task 0: vendored cleanup | `src/olymptrade_ws/**`, `VENDORED.md` | (varies) |
| 1: LICENSE | `LICENSE` | ~20 |
| 2: pyproject.toml license | `pyproject.toml` | +1 |
| 3: Dockerfile LICENSE | `Dockerfile` | +1 |
| 4: auth test — Railway guard (failing) | `tests/test_auth.py` | +30 |
| 5: auth — Railway guard | `src/signal_copier/telegram/auth.py` | +15 |
| 6: auth test — combined auth+verify + rich banner (failing) | `tests/test_auth.py` | +30 |
| 7: auth — combined auth+verify + rich banner | `src/signal_copier/telegram/auth.py`, `tests/test_auth.py` | +30, -10 |
| 8: auth end-to-end smoke | (verification only) | — |
| 9: docker-compose | `docker-compose.yml` | ~17 |
| 10: CI workflow | `.github/workflows/ci.yml` | ~95 |
| 11: README ToC + License section | `README.md` | +15, -2 |
| 12: README First-time setup | `README.md` | +60 |
| 13: README Local development | `README.md` | +30 |
| 14: README Operations | `README.md` | +60 |
| 15: README Verify the deployment | `README.md` | +90 |
| 16: PRD §18 v0.9 changelog | `docs/PRD.md` | +5 |
| 17: end-to-end verification | (verification only) | — |

**Total shipped LoC:** ~430 across 9 files (5 new + 4 modified). Plus this plan and the spec doc.

**Final commit on `main` will be ready to push.** The first real push triggers the auto-deploy (per Task 10's workflow), which is the only step that **cannot** be done locally — it requires the GitHub secrets/variables from Step 10.7 and a Railway project linked to the repo.

---

*End of plan.*
