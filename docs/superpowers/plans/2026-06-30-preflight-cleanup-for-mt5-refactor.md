# Preflight Cleanup for MT5 Refactor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the GitHub repository from `olymptrade` to `telegram-mt5-copier`, delete all OlympTrade-specific tracked files (vendored package, diagnostic scripts, broker implementations), and disable CI/CD so the MT5 refactor work can push to `main` without automated checks firing.

**Architecture:** Operational cleanup across three areas — GitHub repository metadata, file system / git history, GitHub Actions + Railway auto-deploy. No new feature code; the MT5 refactor (separate plan) builds on the cleaner state this plan produces.

**Tech Stack:** Git, GitHub web UI, GitHub Actions (delete only), existing Python project (no runtime behavior changes — DRY_RUN path is preserved exactly).

**Expected end state:**
- GitHub repo URL: `https://github.com/<you>/telegram-mt5-copier`
- Tree no longer contains `src/olymptrade_ws/`, `scripts/olymp_diag*`, `scripts/e26_test.py`, `broker/olymp.py`, `broker/reconnect.py`
- `.github/workflows/ci.yml` is gone (no CI runs on push)
- Railway auto-deploy from GitHub is disabled (no surprise deploys during refactor)
- `signal_copier.__main__` imports cleanly when `DRY_RUN=true` (the default)
- `signal_copier.__main__` raises `NotImplementedError` if invoked with `DRY_RUN=false` (no broker impl yet — MT5 plan will replace)

---

## File Structure

### Modified (3 tracked files)
- `src/signal_copier/__main__.py` — remove top-level OlympTrade import; replace `else:` branch with NotImplementedError
- `.gitignore` — add `/API-Quotex/`
- (No other source files modified in this plan — Task 5 is the one exception)

### Created (1 test file)
- `tests/test_main_imports_clean.py` — guards against accidental reintroduction of OlympTrade import

### Deleted (tracked files in one commit, Task 4)
- `src/olymptrade_ws/` (entire tree, ~10 files)
- `scripts/olymp_diag.py`
- `scripts/olymp_diag_pairs.py`
- `scripts/olymp_diag_accounts.py`
- `scripts/e26_test.py`
- `src/signal_copier/broker/olymp.py`
- `src/signal_copier/broker/reconnect.py`

### Deleted (untracked, no commit needed, Task 6)
- `OlympTradeAPI/` (sibling checkout at repo root, already gitignored)
- `API-Quotex/` (sibling checkout at repo root, NOT yet gitignored — fixed in Task 3)

### Deleted (1 tracked, single commit, Task 7)
- `.github/workflows/ci.yml`

### Out of scope (explicitly preserved)
- `tools/soak.py` — references `OLYMP_ACCOUNT_GROUP` in a default fixture. Will be updated by the MT5 plan when the real broker integration lands. **Do not delete here.**
- `scripts/cascade_test.py` — not OlympTrade-specific, used by recovery testing. **Keep.**
- All docs (`docs/PRD.md`, `docs/refactor.md`, `docs/superpowers/`) — historical record, explicitly kept; the MT5 plan will edit them in place.
- `pyproject.toml` references `src/olymptrade_ws` in `pyproject.toml:44,56,87`. Will fail `ruff check` and `mypy` if run on a clean tree. Two options:
  - **(preferred)** Remove these references in a follow-up commit during the MT5 plan after Task 5's TDD test confirms imports are clean. Keeping them here would require us to delete files AND edit pyproject.toml in the same plan, expanding scope.
  - Stays in `pyproject.toml` until MT5 plan lands. The `--extend-exclude` / `--exclude` flags mean `ruff` and `mypy` will silently skip the now-missing directory without erroring — leaving them is harmless.
  - **Decision: leave pyproject.toml alone.** Self-review confirmed at end of plan.

---

## Tasks

### Task 0: Verify Railway auto-deploy is disabled (PUSH FIRST)

**Files:**
- Already-applied edit: `railway.toml:3` (insert `watchPatterns = ["__never_match_during_refactor__/**"]` under `[build]`)

**Why this is Task 0:** Once the `watchPatterns` filter is on `origin/main`, Railway's GitHub-source watcher skips every subsequent push (changed files don't match the pattern → no deploy). If you skip ahead and push Task 4's deletion without this filter, Railway will auto-deploy a broken intermediate state — recoverable but noisy.

**Note (2026-06-30):** This task is already complete. The committed change is `ef439d3 chore(railway): disable auto-deploy via watchPatterns during MT5 refactor`. Verify it's on `origin/main` before continuing to Task 1.

- [ ] **Step 1: Verify the commit is on origin/main**

Run: `git log origin/main --oneline | Select-Object -First 3`
Expected output: top commit is `ef439d3 chore(railway): disable auto-deploy via watchPatterns during MT5 refactor`.

If the commit is missing:
1. Edit `railway.toml` to add this under `[build]`:
   ```toml
   [build]
   builder = "DOCKERFILE"
   # Disable Railway auto-deploy during preflight cleanup + MT5 refactor.
   watchPatterns = ["__never_match_during_refactor__/**"]
   ```
2. `git add railway.toml && git commit -m "chore(railway): disable auto-deploy via watchPatterns during MT5 refactor"`
3. `git push origin main`

- [ ] **Step 2: Verify no Railway deploy was triggered**

Open Railway dashboard → your project → `signal-copier` service → Deployments tab. There should be **no deployment** corresponding to the push (the watchPatterns filter rejects it).

**No commit for this task** — already done. Move to Task 1.

---

### Task 1: Rename the GitHub repository

**Files:** None (operational task in the web UI)

**Why:** GitHub's redirect preserves history; renaming on the web UI is the only operationally-correct sequence. The new name propagates to Docker image tags, Railway service URLs (via vars), and every commit message greppable.

- [ ] **Step 1: Open the repository settings page**

Open in a browser:
```
https://github.com/<your-username>/olymptrade/settings
```

The "olymptrade" in the URL is the OLD name. After rename the URL changes, but GitHub 301-redirects the old name so this link still works during the transition.

- [ ] **Step 2: Change the repository name**

- Scroll to the **Repository name** field (in the "General" section, near the top).
- Replace the current value (`olymptrade`) with `telegram-mt5-copier`.
- Click **Rename**.
- GitHub prompts for confirmation because the redirect will break for any number of `git clone` URLs you have out there; click **Rename repository** on the confirmation modal.

- [ ] **Step 3: Verify the rename worked**

Open in a browser:
```
https://github.com/<your-username>/telegram-mt5-copier
```

Expected: the repo loads. The URL bar should now show the new name. If it redirects from the old URL, GitHub is telling you the 301 redirect is active — that's fine.

- [ ] **Step 4: Commit nothing — this is a remote-side operation**

No code change. Move to Task 2.

---

### Task 2: Update the local git remote URL

**Files:** None (configuration change in local repo only, no file write)

**Why:** Your local clone still points at the old `olymptrade` remote. `git fetch` will still work via the redirect, but `git push` is faster and clearer against the canonical URL.

- [ ] **Step 1: Confirm the current remote URL**

Run: `git remote -v`
Expected output (two lines):
```
origin  https://github.com/<your-username>/olymptrade.git (fetch)
origin  https://github.com/<your-username>/olymptrade.git (push)
```

(If you have other remotes like `upstream`, those will also appear — that's fine; only `origin` is touched below.)

- [ ] **Step 2: Update the remote URL**

Run:
```bash
git remote set-url origin https://github.com/<your-username>/telegram-mt5-copier.git
```

No output expected on success.

- [ ] **Step 3: Verify the new URL is set**

Run: `git remote -v`
Expected:
```
origin  https://github.com/<your-username>/telegram-mt5-copier.git (fetch)
origin  https://github.com/<your-username>/telegram-mt5-copier.git (push)
```

- [ ] **Step 4: Confirm a fetch works against the new URL**

Run: `git fetch origin`
Expected: `From https://github.com/<your-username>/telegram-mt5-copier` (or similar) with no errors. If you see a redirect chain message, that's fine — GitHub 301-redirects the old repo URL.

- [ ] **Commit:** No commit for this task. Move to Task 3.

---

### Task 3: Add `/API-Quotex/` to `.gitignore`

**Files:**
- Modify: `.gitignore:53` (insert one line after the existing `/OlympTradeAPI/` entry)

**Why:** Keeps the sibling `API-Quotex/` reference checkout permanently out of git. Required before Task 6 so the deletion of the on-disk folder isn't accidentally re-tracked.

- [ ] **Step 1: Locate the existing ignore block**

Open `.gitignore`. Lines 51-54 currently read:
```gitignore
# Upstream source (kept locally as a reference for re-vendoring, see
# src/olymptrade_ws/VENDORED.md - NOT tracked in this repo)
/OlympTradeAPI/
```

- [ ] **Step 2: Add the new ignore line**

Edit `.gitignore` so lines 51-55 read:
```gitignore
# Upstream source (kept locally as a reference for re-vendoring, see
# src/olymptrade_ws/VENDORED.md - NOT tracked in this repo)
/OlympTradeAPI/
/API-Quotex/
```

Use the Edit tool:
- oldString:
  ```
  # Upstream source (kept locally as a reference for re-vendoring, see
  # src/olymptrade_ws/VENDORED.md - NOT tracked in this repo)
  /OlympTradeAPI/
  ```
- newString:
  ```
  # Upstream source (kept locally as a reference for re-vendoring, see
  # src/olymptrade_ws/VENDORED.md - NOT tracked in this repo)
  /OlympTradeAPI/
  /API-Quotex/
  ```

- [ ] **Step 3: Verify the change**

Run: `git diff .gitignore`
Expected: a two-line addition showing `/API-Quotex/` added below `/OlympTradeAPI/`.

- [ ] **Step 4: Commit**

```bash
git add .gitignore
git commit -m "chore: ignore API-Quotex sibling checkout"
```

Expected: one file changed, one insertion. No warnings.

---

### Task 4: Delete OlympTrade-related tracked files

**Files:**
- Delete (tracked, via `git rm`):
  - `src/olymptrade_ws/` (entire tree)
  - `scripts/olymp_diag.py`
  - `scripts/olymp_diag_pairs.py`
  - `scripts/olymp_diag_accounts.py`
  - `src/signal_copier/broker/olymp.py`
  - `src/signal_copier/broker/reconnect.py`
  - `tests/fixtures/olymp_e26_sample.json`
  - `tests/test_olymp_broker.py`
  - `tests/test_olymp_broker_recorded.py`

**Why:** None of these files have a future in the MT5 codebase. The vendored package is replaced by `mt5linux` PyPI package (separate plan). The diagnostic scripts reference the vendored package's import path and would break after deletion. The broker implementations will be replaced by `broker/mt5.py` (separate plan).

**⚠️ Note:** Tasks 4-7 are executed in this order so each `git status` reflects a coherent intermediate state. **After Task 4 completes, the codebase is in a TEMPORARILY BROKEN state:** `src/signal_copier/__main__.py` line 15 imports `from signal_copier.broker.reconnect import ReconnectingOlympTradeBroker` which no longer exists. `signal_copier` will fail to import. **Task 5 fixes that.** Don't run anything between Task 4 and Task 5.

- [ ] **Step 1: Confirm the files exist and are tracked**

Run:
```bash
git ls-files src/olymptrade_ws scripts/olymp_diag.py scripts/olymp_diag_pairs.py scripts/olymp_diag_accounts.py src/signal_copier/broker/olymp.py src/signal_copier/broker/reconnect.py tests/fixtures/olymp_e26_sample.json tests/test_olymp_broker.py tests/test_olymp_broker_recorded.py
```

Expected output: a list of all these paths (the `-r` recursion isn't needed because `ls-files` handles directories). If any file is missing from the output, that file is already deleted — skip its `git rm` line below.

- [ ] **Step 2: Remove all files in one `git rm` command**

Run:
```bash
git rm -r \
  src/olymptrade_ws \
  scripts/olymp_diag.py \
  scripts/olymp_diag_pairs.py \
  scripts/olymp_diag_accounts.py \
  src/signal_copier/broker/olymp.py \
  src/signal_copier/broker/reconnect.py \
  tests/fixtures/olymp_e26_sample.json \
  tests/test_olymp_broker.py \
  tests/test_olymp_broker_recorded.py
```

Expected: Git prints `rm '...'` lines for each removed path (approx. 25+ rm lines including the recursive contents of `src/olymptrade_ws/`). The command exits with status 0.

- [ ] **Step 3: Verify the deletions**

Run: `git status --short`
Expected: a long list of `D  src/olymptrade_ws/...` and `D  scripts/olymp_diag.py` etc. entries. All deletions, no additions.

Then run: `git ls-files src/olymptrade_ws src/signal_copier/broker/olymp.py`
Expected: empty output (no tracked files at those paths).

- [ ] **Step 4: Commit**

```bash
git commit -m "chore: remove OlympTrade-specific files (broker, diag scripts, vendored pkg)"
```

Expected: ~22 files deleted. The message body should reference the MT5 refactor plan (or just rely on PR-level linking if you haven't created the follow-up plan yet).

---

### Task 5: Fix `src/signal_copier/__main__.py` to import without OlympTrade deps (TDD)

**Files:**
- Create: `tests/test_main_imports_clean.py`
- Modify: `src/signal_copier/__main__.py` (remove top-level OlympTrade import; raise NotImplementedError in the live-trading branch)

**Why:** After Task 4, `__main__.py` line 15 still tries to import `ReconnectingOlympTradeBroker` from `signal_copier.broker.reconnect`, which no longer exists. Until this is fixed, `python -m signal_copier` crashes with `ModuleNotFoundError`. This task uses TDD so we never lose track of this constraint.

- [ ] **Step 1: Verify the test is currently failing**

Run: `uv run pytest tests/test_main_imports_clean.py -v`
Expected: `collected 0 items` (the test file does not exist yet). Stop here — proceed to step 2.

If pytest reports any other failure or a non-zero exit code, **STOP** — the test infrastructure may already be broken. Check that `uv sync` ran cleanly with `uv sync --frozen` first.

- [ ] **Step 2: Write the failing test**

Create `tests/test_main_imports_clean.py` with this exact content:

```python
"""Preflight cleanup guard: signal_copier.__main__ must import without OlympTrade deps.

This test fails immediately after Task 4 deletes broker/olymp.py and
broker/reconnect.py, because __main__'s top-level import is now broken.
Task 5's edit makes this test pass by removing the broken import.
"""

from __future__ import annotations

import importlib
import sys


def test_main_module_imports_clean() -> None:
    """After preflight cleanup, signal_copier.__main__ must import cleanly.

    Guards against accidental reintroduction of OlympTrade imports.
    """
    # Drop any cached module so the test reflects the current file on disk.
    sys.modules.pop("signal_copier.__main__", None)
    module = importlib.import_module("signal_copier.__main__")
    assert module is not None
    assert hasattr(module, "main")
```

Use the Write tool to create the file with this content.

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/test_main_imports_clean.py -v`
Expected:
- `FAILED tests/test_main_imports_clean.py::test_main_module_imports_clean`
- Error: `ModuleNotFoundError: No module named 'signal_copier.broker.reconnect'`
- Exit code 1

This is the expected failure (RED). The test catches the broken import.

- [ ] **Step 4: Edit `src/signal_copier/__main__.py`**

Two surgical edits:

**Edit 4a — Remove line 15 (the broken import):**

Use the Edit tool:
- oldString:
  ```
  from signal_copier.broker.dry_run import DryRunBroker
  from signal_copier.broker.reconnect import ReconnectingOlympTradeBroker
  ```
- newString:
  ```
  from signal_copier.broker.dry_run import DryRunBroker
  ```

(That `DryRunBroker` import stays — it's the only broker impl that exists after this plan.)

**Edit 4b — Replace the `else:` branch (lines 99-110) with a NotImplementedError raise:**

Use the Edit tool:
- oldString:
  ```
          if config.dry_run:
              broker = DryRunBroker()
              _log.info("Broker: DryRunBroker (DRY_RUN=true)")
              await broker.connect()
          else:
              broker = ReconnectingOlympTradeBroker(
                  access_token=config.olymp_access_token,
                  account_id=config.olymp_account_id,
                  account_group=config.olymp_account_group,
                  notifier=notifier,
              )
              _log.info(
                  "Broker: ReconnectingOlympTradeBroker (live %s, account_id=%s)",
                  config.olymp_account_group,
                  config.olymp_account_id,
              )
              await broker.connect()
  ```
- newString:
  ```
          if config.dry_run:
              broker = DryRunBroker()
              _log.info("Broker: DryRunBroker (DRY_RUN=true)")
              await broker.connect()
          else:
              # MT5 broker integration is the next plan (see docs/refactor.md
              # Section 4.3 and 4.7). Until broker/mt5.py lands, live demo
              # trading is not implemented. Refuse with a clear error
              # rather than silently using a stale broker reference.
              raise NotImplementedError(
                  "Live trading requires the MT5 broker; set DRY_RUN=true "
                  "until the MT5 broker refactor (docs/refactor.md) is complete."
              )
  ```

- [ ] **Step 5: Run the test to verify it passes (GREEN)**

Run: `uv run pytest tests/test_main_imports_clean.py -v`
Expected:
- `PASSED tests/test_main_imports_clean.py::test_main_module_imports_clean`
- Exit code 0

- [ ] **Step 6: Run the full test suite to confirm no regressions**

Run: `uv run pytest -m 'not slow'`
Expected: all previously-green tests stay green. The full suite (excluding the `slow` marker) should pass.

If any test that previously passed now fails, **STOP** — that test was probably importing from a deleted module. Investigate the failing test before proceeding.

- [ ] **Step 7: Commit**

```bash
git add tests/test_main_imports_clean.py src/signal_copier/__main__.py
git commit -m "fix(__main__): import cleanly without OlympTrade broker; live trading pending MT5 plan"
```

Expected: 2 files changed, ~10 insertions, ~13 deletions. The test message is captured in the commit body for trace.

---

### Task 6: Delete sibling checkout folders from disk (untracked)

**Files:**
- Delete (untracked, no `git rm`):
  - `OlympTradeAPI/` (relative to repo root)
  - `API-Quotex/` (relative to repo root)
  - `scripts/e26_test.py` (untracked OlympTrade-specific script; imports `olymptrade_ws.core.client`)

**Note on `scripts/cascade_test.py`:** Also untracked. Per its name (recovery testing), it's likely worth keeping. **Do not delete.** When the MT5 plan lands, decide whether to commit it as a recovery tool or discard it.

**Why:** These are sibling directories holding reference third-party code. They are not part of this project. After Task 3's `.gitignore` update, neither directory is tracked. Task 6 simply removes them from disk.

- [ ] **Step 1: Verify both folders are untracked**

Run: `git status --ignored --short | grep -E "OlympTradeAPI|API-Quotex" || echo "none-of-the-folders-fell-through-git-check-ignore"`
Expected (after Task 3):
```
!! OlympTradeAPI/
!! API-Quotex/
```

The `!!` prefix means "ignored" — exactly what we want. (`-E` extended-regex; if your grep doesn't support it, drop the `-E` flag.)

- [ ] **Step 2: Inspect the contents before deleting (safety check)**

Run:
```bash
ls OlympTradeAPI/ | head -5
echo "---"
ls API-Quotex/ | head -5
echo "---"
cat scripts/e26_test.py | head -3
```
Expected: each shows a few files / subdirectories from the reference checkouts (e.g., `olymptrade_ws/`, `api_quotex/`). The `e26_test.py` first 3 lines should reference OlympTrade (e.g., `from olymptrade_ws.core.client import OlympTradeClient`). All three are reference/throwaway content, safe to delete.

**If either folder contains files that look like they belong to your project (e.g., `signal_copier/`), STOP — investigate before proceeding.**

- [ ] **Step 3: Delete the folders and the untracked script**

Run:
```bash
rm -rf OlympTradeAPI API-Quotex scripts/e26_test.py
```

No output expected on success.

- [ ] **Step 4: Verify the deletions**

Run:
```bash
ls OlympTradeAPI API-Quotex scripts/e26_test.py 2>&1 | head -5 || echo "ok-all-gone"
```
Expected: `ok-all-gone` or `cannot access ... : No such file or directory` errors.

**Commit:** no commit for this task — the folders were never tracked, so git history has no record of them.

---

### Task 7: Disable CI/CD

**Files:**
- Delete (tracked, single commit):
  - `.github/workflows/ci.yml`

**Why:** Without this, every `git push origin main` re-runs the full CI matrix (lint, format, typecheck, test, deploy-to-Railway). During the MT5 refactor work, half-broken intermediate states will fail these checks, blocking push. Removing the workflow lets you push freely until the MT5 plan lands.

**⚠️ This deletes CI entirely — not just disables it.** The MT5 plan will re-add workflows from scratch (taking the opportunity to drop the auto-deploy-to-Railway job, since Railway watches the repo directly).

- [ ] **Step 1: Confirm only one workflow file exists**

Run: `ls .github/workflows/`
Expected: `ci.yml` (no other files). If a second workflow file exists, **STOP** and review whether it should also be deleted.

- [ ] **Step 2: Remove the workflow file**

Run: `git rm .github/workflows/ci.yml`
Expected: `rm '.github/workflows/ci.yml'` (single line).

- [ ] **Step 3: Verify the deletion**

Run: `git status --short`
Expected: `D  .github/workflows/ci.yml`

- [ ] **Step 4: Commit**

```bash
git commit -m "chore: remove CI workflow (auto-runs disabled during MT5 refactor)"
```

Expected: 1 file deleted. The `.github/` directory still exists on disk and is committed-by-history but currently empty — git records this fine; if you want it removed entirely, you'll need a follow-up commit after Task 7's history lands (git does not delete empty dirs from the working tree on its own; small cleanup that can be done in the MT5 plan).

- [ ] **Step 5: Disable Railway's GitHub auto-deploy (operational, browser-based)**

This is a separate concern from the workflow file — Railway watches the repo for pushes to `main` and auto-deploys the changed code.

Open the Railway dashboard:
```
https://railway.app/project/<your-project-id>
```

Click on the **`signal-copier`** service (or whatever it's named; the existing service). Then:
- **Settings** tab → **Source** section
- Find **"Trigger Deploy"** (or "Auto-deploy on push" depending on Railway version)
- Toggle it **OFF** (or click "Disconnect repo" if shown)

After this, pushes to `main` will **NOT** trigger a Railway deploy. The previously-deployed container continues running. When you're ready to deploy the MT5 work, you'll do a manual `railway up` or re-enable auto-deploy.

- [ ] **Step 6: Verify Railway is disconnected**

Run a no-op commit & don't push. If you want to be extra sure, the Settings tab in Railway should show "Deploys paused" or "Manual deploys only" instead of "Auto-deploy enabled."

**No commit for step 5-6** — that's a dashboard-only change.

---

### Task 8: Final smoke check and push to main

**Why:** Last sanity gate before pushing. Confirms the codebase still imports, tests still pass, and the push doesn't trigger any CI.

- [ ] **Step 1: Confirm the import smoke test still passes**

Run: `uv run pytest tests/test_main_imports_clean.py -v`
Expected: `PASSED`, exit code 0.

- [ ] **Step 2: Run the full test suite**

Run: `uv run pytest -m 'not slow'`
Expected: all tests pass. Pay attention to any test that uses `@pytest.fixture` for `OlympTradeClient` or similar — those tests have likely been removed or replaced during the original development; if any still exist, mark them with `@pytest.mark.skip(reason="broker removed pre-MT5")` and add a follow-up issue.

- [ ] **Step 3: Confirm no OlympTrade references remain in tracked files**

Run: `rg -l 'OlympTrade|olymptrade_ws' --hidden --glob '!.git' || echo "no-matches-found"`
Expected: `no-matches-found`.

If any tracked file still references OlympTrade (other than docs/ which is the historical record), investigate and fix.

- [ ] **Step 4: Push to main**

Run: `git push origin main`
Expected: the push succeeds with no warnings. The push triggers **NO** GitHub Actions runs (verified in the GitHub UI: there should be no yellow/green/red icons next to the new commit).

- [ ] **Step 5: Verify in GitHub UI**

Open `https://github.com/<your-username>/telegram-mt5-copier/commits/main` in a browser.

Expected:
- The latest commit appears.
- Clicking the commit shows the file changes (deletions + the __main__.py edit + the .gitignore edit).
- **No workflow run badge** appears next to the commit (because we removed the workflow file).

- [ ] **Step 6: Celebrate**

The MT5 refactor can now proceed freely. Runbook continuation in `docs/refactor.md` Section 4 onwards.

---

## Self-Review

I checked the plan against the user's three explicit requirements plus the writing-plans skill's quality bars.

**1. Spec coverage (the user's three asks):**
- ✅ "Repo name change" → Tasks 1-2 (operational + git remote).
- ✅ "Delete OlympTrade and Quotex files/folders" → Tasks 3-6 (tracked files in Task 4 via `git rm`; sibling checkouts in Task 6 via `rm -rf`; `.gitignore` update in Task 3).
- ✅ "Disconnect CI/CD" → Task 7 (delete GitHub Actions workflow + disable Railway auto-deploy).

**2. Placeholder scan:**
- No "TBD" / "TODO" / "implement later" in any task body. The single NotImplementedError raise in `__main__.py` is intentional, not a placeholder — it's the load-bearing behavior until the MT5 plan lands.
- Every command shows expected output.
- Every code edit shows the exact before/after strings via Edit tool invocations.
- Tasks are sequenced so each one's preconditions are met by earlier tasks' commits.

**3. Type consistency:**
- `tests/test_main_imports_clean.py::test_main_module_imports_clean` — single function in the file, called from Task 5 step 2 onward. No naming mismatch between tasks.
- `signal_copier.__main__` referenced identically in Task 5 step 1 (writes the test against this name), step 4a (removes its top-level import), and step 4b (replaces its `else:` branch).
- `OlympTradeClient` and `ReconnectingOlympTradeBroker` are used only in the `oldString` for the Edit tool calls — they're imports that exist *only* in the pre-edit state and vanish after the edit. No later task references them.

**4. Spec gaps I considered and decided out-of-scope:**
- ⚠️ `pyproject.toml:44,56,87` references `src/olymptrade_ws` in ruff/mypy/pytest config. After Task 4 these references are stale but harmless (`--exclude` to a non-existent dir is a no-op). Editing them in this plan would expand scope into the MT5 plan's territory. **Decision:** leave alone.
- ⚠️ `tools/soak.py` reads `OLYMP_ACCOUNT_GROUP` env. Not deleted; updated by the MT5 plan.
- ⚠️ `tools/soak_assertions.py` — not investigated, may have other OlympTrade ties. **Decision:** covered by the MT5 plan's "update tools/* for MT5" milestone.
- ⚠️ `docs/superpowers/specs/*.md` files reference `M8-olymptrade-broker` etc. in their filenames. Filenames are historical. **Decision:** keep; the MT5 plan will add new spec files and the old ones stay as archive.
- ⚠️ `docs/PRD.md` still says OlympTrade throughout. **Decision:** keep as historical record; `docs/refactor.md` is the operational truth until PRD is updated in the MT5 plan.
- ⚠️ Local pre-commit hook (`.pre-commit-config.yaml` runs `ruff` on commit). **Decision:** out of scope. Pre-commit only fixes formatting and doesn't block pushes. Mention to user if `git commit` produces unexpected diff churn.
- ⚠️ `src/signal_copier/config.py:31-34,68-83` still defines `olymp_access_token`/`olymp_account_group` fields. **Decision:** leave alone. They're vestigial but functional, and removing them would require touching validator code. The MT5 plan replaces them.

**5. Operational gotchas I documented explicitly:**
- ⚠️ Task 4 leaves the codebase in a temporarily-broken state. Task 5 fixes it. The plan flags this so the engineer doesn't panic between Tasks 4 and 5.
- ⚠️ Task 7 step 5 is a browser-only step (Railway dashboard). No commit covers it.
- ⚠️ Task 8 step 4 assumes GitHub is configured with deploy keys; if not, `git push` will fail with a permissions error before any CI check fires.

Plan looks complete and unambiguous. Saving and offering execution.
