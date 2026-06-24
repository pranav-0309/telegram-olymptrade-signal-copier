# M12 Type & Format Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the 229 pre-existing mypy errors and reformat 5 test files so the M11 CI workflow's `typecheck` and `format` jobs pass, enabling auto-deploy to Railway end-to-end.

**Architecture:** 7 commits in a specific order (annotations first, override removal last, format + src sweep, then PRD changelog). Each commit's scope aligns with one of the spec's 4 workstreams. The override-removal commit is placed AFTER annotations (per Risk R-7) so CI stays green between commits. The `src/` weak-point sweep is conditional — only land if the 4 searches find fixable items; otherwise skip.

**Tech Stack:** Python 3.13, mypy 1.13+ `--strict` mode, ruff 0.7+ (check + format), pytest 8.3+, pytest-asyncio, existing test fixtures in `tests/_scheduler_fixtures.py`, `tests/conftest.py`.

**Spec:** `docs/superpowers/specs/2026-06-23-m12-type-and-format-cleanup-design.md`

**Plan:** `docs/superpowers/plans/2026-06-23-m12-type-and-format-cleanup.md`

**Pre-flight: verify the spec's baseline numbers.** Before starting Task 1, run `uv run mypy --strict src tests 2>&1 | Select-Object -Last 1` and `uv run ruff format --check 2>&1 | Select-String "Would reformat"`. Expected: `Found 229 errors in 16 files (checked 57 source files)` and 5 files needing reformat. If the numbers don't match, the spec's per-file error counts in §5.3 are stale; re-run `rtk wc -l` per the command in spec §5.3 to refresh them.

---

## File Structure

```
signal-copier/
├── pyproject.toml                            MODIFIED (-26 lines): delete [[tool.mypy.overrides]] block
├── tests/
│   ├── test_state_machine.py                 MODIFIED: +annotations (81 errors)
│   ├── test_olymp_broker.py                  MODIFIED: +annotations (53 errors)
│   ├── test_db.py                            MODIFIED: +annotations (28 errors)
│   ├── test_telegram_dm.py                   MODIFIED: +annotations (27 errors)
│   ├── test_reconnect_supervisor.py          MODIFIED: +annotations + format (13 errors + format)
│   ├── test_recovery.py                      MODIFIED: +annotations (9 errors)
│   ├── test_scheduler.py                     MODIFIED: +annotations (6 errors)
│   ├── test_gale_math.py                     MODIFIED: +annotations (4 errors)
│   ├── test_config.py                        MODIFIED: +annotations (3 errors)
│   ├── test_main.py                          MODIFIED: +annotations (3 errors)
│   ├── test_telegram_client.py               MODIFIED: +annotations + format (3 errors + format)
│   ├── conftest.py                           MODIFIED: +annotations (2 errors)
│   ├── test_telegram_listener.py             MODIFIED: +annotations + format (2 errors + format)
│   ├── _scheduler_fixtures.py                MODIFIED: +annotations (1 error)
│   ├── test_log.py                           MODIFIED: +annotations + format (1 error + format)
│   └── test_recording_notifier_protocol.py   MODIFIED: format only (0 errors)
├── src/signal_copier/                        MODIFIED (conditional sweep): cast/Any/type: ignore/public API
└── docs/PRD.md                               MODIFIED (+5 lines): v0.10 changelog entry
```

**Decomposition notes:** Each test file gets annotations in Tasks 1-4 (one task per error category). The override removal (Task 5) is a single-file change. Format + src sweep + PRD changelog are separate tasks (6, 7, 8). Total commits: 7-8.

---

## Task 1: Annotate `no-untyped-def` errors (30 errors)

**Files:**
- Modify: `tests/test_state_machine.py`
- Modify: `tests/test_olymp_broker.py`
- Modify: `tests/test_db.py`
- Modify: `tests/test_telegram_dm.py`
- Modify: `tests/test_reconnect_supervisor.py`
- Modify: `tests/test_recovery.py`
- Modify: `tests/test_main.py`

(7 files covering the bulk of `no-untyped-def` errors. Other files may also have a few — covered below.)

- [ ] **Step 1.1: Enumerate all `no-untyped-def` errors**

Run:
```bash
uv run mypy --strict src tests 2>&1 | Select-String "no-untyped-def"
```

This lists every error with `file:line:col: error: ... [no-untyped-def]`. The 30 errors are spread across ~7 files.

Expected: ~30 lines of output. Note the file:line locations — you'll edit those exact lines.

- [ ] **Step 1.2: Apply the `no-untyped-def` fix pattern from spec §5.2**

For each error, the fix is one of:

```python
# Plain function — add parameter types + return type
def test_foo(x, y):              # before
def test_foo(x: int, y: int) -> None:   # after

# Async function
async def test_async():           # before
async def test_async() -> None:   # after

# Fixture
@pytest.fixture
def some_fixture():              # before
@pytest.fixture
def some_fixture() -> SomeClass: # after

# Method (e.g., on a TestCase class)
def setUp(self):                  # before
def setUp(self) -> None:          # after
```

The return type comes from inspecting what the function actually returns. For test functions that don't `return`, use `-> None`. For fixtures, use the type of the yielded/produced value.

- [ ] **Step 1.3: Verify mypy count drops**

Run:
```bash
uv run mypy --strict src tests 2>&1 | Select-Object -Last 1
```

Expected: `Found N errors in M files` where N < 229 (the no-untyped-def category is now ~0). Total errors may have dropped by 30, OR may not have dropped if the same lines also had other categories — that's fine, fix the other categories in later tasks.

- [ ] **Step 1.4: Verify tests still pass**

Run:
```bash
uv run pytest tests/test_state_machine.py tests/test_olymp_broker.py tests/test_db.py tests/test_telegram_dm.py tests/test_reconnect_supervisor.py tests/test_recovery.py tests/test_main.py
```

Expected: all tests pass. If any test fails, the annotation changed runtime behavior (it shouldn't, but inspect to confirm).

- [ ] **Step 1.5: Verify ruff check still clean**

Run:
```bash
uv run ruff check tests/test_state_machine.py tests/test_olymp_broker.py tests/test_db.py tests/test_telegram_dm.py tests/test_reconnect_supervisor.py tests/test_recovery.py tests/test_main.py
```

Expected: `All checks passed!`.

- [ ] **Step 1.6: Commit**

```bash
rtk git add tests/test_state_machine.py tests/test_olymp_broker.py tests/test_db.py tests/test_telegram_dm.py tests/test_reconnect_supervisor.py tests/test_recovery.py tests/test_main.py
rtk git commit -m "test: fix no-untyped-def annotations across 7 test files (30 errors)"
```

---

## Task 2: Annotate `arg-type` errors (52 errors)

**Files:**
- Modify: `tests/test_olymp_broker.py`
- Modify: `tests/test_state_machine.py`
- Modify: `tests/test_db.py`
- Modify: `tests/test_telegram_dm.py`
- Modify: `tests/test_reconnect_supervisor.py`
- Modify: `tests/test_recovery.py`
- Modify: `tests/test_scheduler.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_main.py`
- Modify: `tests/test_telegram_client.py`
- Modify: `tests/test_gale_math.py`
- Modify: `tests/conftest.py`

(12 files; the same files as Task 1 may also have arg-type errors, in addition to new files.)

- [ ] **Step 2.1: Enumerate all `arg-type` errors**

Run:
```bash
uv run mypy --strict src tests 2>&1 | Select-String "arg-type"
```

- [ ] **Step 2.2: Apply the `arg-type` fix pattern from spec §5.2**

For each error, the fix is one of:

```python
# Fix the call site to pass the right type
direction = "up"                                # before — inferred as str
signal = Signal(pair="EUR/JPY", direction=direction, ...)  # str vs Literal["up","down"]
# AFTER: annotate the variable
direction: Literal["up", "down"] = "up"
signal = Signal(pair="EUR/JPY", direction=direction, ...)

# Or annotate at call site if the literal type is wrong
Signal(pair="EUR/JPY", direction="up", ...)  # mypy may complain about str
# AFTER: cast to help mypy (rare)
direction_value = cast(Literal["up", "down"], "up")
Signal(pair="EUR/JPY", direction=direction_value, ...)
```

**Preferred fix**: annotate the variable. **Last resort**: `cast()`. Do NOT add `# type: ignore[arg-type]` — the spec forbids it.

- [ ] **Step 2.3: Verify mypy count drops**

Run:
```bash
uv run mypy --strict src tests 2>&1 | Select-Object -Last 1
```

Expected: error count has dropped by ~52 (or by the number of arg-type errors you fixed).

- [ ] **Step 2.4: Verify tests still pass**

```bash
uv run pytest tests/test_olymp_broker.py tests/test_state_machine.py tests/test_db.py tests/test_telegram_dm.py tests/test_reconnect_supervisor.py tests/test_recovery.py tests/test_scheduler.py tests/test_config.py tests/test_main.py tests/test_telegram_client.py tests/test_gale_math.py
```

Expected: all pass.

- [ ] **Step 2.5: Commit**

```bash
rtk git add tests/
rtk git commit -m "test: fix arg-type annotations across 12 test files (52 errors)"
```

---

## Task 3: Annotate `assignment` errors (34 errors)

**Files:**
- Modify: `tests/test_state_machine.py`
- Modify: `tests/test_olymp_broker.py`
- Modify: `tests/test_db.py`
- Modify: `tests/test_telegram_dm.py`
- Modify: `tests/test_reconnect_supervisor.py`
- Modify: `tests/test_recovery.py`
- Modify: `tests/test_scheduler.py`
- Modify: `tests/test_gale_math.py`
- Modify: `tests/test_main.py`

(9 files. Some will be the same as Tasks 1-2; some may be new.)

- [ ] **Step 3.1: Enumerate all `assignment` errors**

```bash
uv run mypy --strict src tests 2>&1 | Select-String "error:.*\[assignment\]"
```

Note: this regex catches `[assignment]` but NOT `[assignment-overload]` (a separate category). Use the broader pattern if needed:
```bash
uv run mypy --strict src tests 2>&1 | Select-String "assignment\]"
```

- [ ] **Step 3.2: Apply the `assignment` fix pattern from spec §5.2**

For each error, the fix is one of:

```python
# Plain variable
result = some_func()              # before
result: SomeType = some_func()    # after

# Function return assignment
def helper() -> SomeType:
    x = compute()                # before
    x: SomeType = compute()      # after (annotate the local)

# Re-assignment
counter = 0                      # before — inferred as int
counter = counter + 1            # OK because type doesn't change
# If mypy complains, annotate: counter: int = 0
```

If the assigned value's type is genuinely unknown, use `cast()`:
```python
result = cast(SomeType, some_func())  # last resort
```

**Never use `# type: ignore[assignment]`** to silence.

- [ ] **Step 3.3: Verify mypy count drops**

```bash
uv run mypy --strict src tests 2>&1 | Select-Object -Last 1
```

Expected: dropped by ~34.

- [ ] **Step 3.4: Verify tests pass + ruff clean**

```bash
uv run pytest tests/test_state_machine.py tests/test_olymp_broker.py tests/test_db.py tests/test_telegram_dm.py tests/test_reconnect_supervisor.py tests/test_recovery.py tests/test_scheduler.py tests/test_gale_math.py tests/test_main.py
uv run ruff check tests/test_state_machine.py tests/test_olymp_broker.py tests/test_db.py tests/test_telegram_dm.py tests/test_reconnect_supervisor.py tests/test_recovery.py tests/test_scheduler.py tests/test_gale_math.py tests/test_main.py
```

Both: success.

- [ ] **Step 3.5: Commit**

```bash
rtk git add tests/
rtk git commit -m "test: fix assignment annotations across 9 test files (34 errors)"
```

---

## Task 4: Annotate `union-attr` errors (72 errors + 41 remaining smaller categories)

**Files:**
- Modify: all 15 test files with mypy errors (the override block is still silencing, so any remaining errors fall here):
  - `tests/test_state_machine.py`
  - `tests/test_olymp_broker.py`
  - `tests/test_db.py`
  - `tests/test_telegram_dm.py`
  - `tests/test_reconnect_supervisor.py`
  - `tests/test_recovery.py`
  - `tests/test_scheduler.py`
  - `tests/test_gale_math.py`
  - `tests/test_config.py`
  - `tests/test_main.py`
  - `tests/test_telegram_client.py`
  - `tests/conftest.py`
  - `tests/test_telegram_listener.py`
  - `tests/_scheduler_fixtures.py`
  - `tests/test_log.py`

- [ ] **Step 4.1: Enumerate remaining `union-attr` and other errors**

```bash
uv run mypy --strict src tests 2>&1 | Select-String "union-attr\|attr-defined\|call-arg\|import-untyped\|misc\|method-assign"
```

You should see ~113 remaining errors (72 union-attr + ~41 others).

- [ ] **Step 4.2: Apply the `union-attr` fix pattern from spec §5.2**

For each error, the fix is one of:

```python
# assert to narrow
result = state_machine.transition(signal)
result.next_action              # before — mypy: result could be None
# AFTER:
result = state_machine.transition(signal)
assert result is not None
result.next_action

# cast when the value is known non-None but mypy can't prove it
broker = cast(Broker, broker_factory())    # last resort
```

For `attr-defined`, `call-arg`, `import-untyped`, `method-assign`, `misc` (the ~41 smaller-category errors): inspect each error message individually. Common fixes:
- `attr-defined`: a module re-exports something mypy can't see; either fix the import or `cast()`
- `call-arg`: too many/too few args to a function; fix the call site
- `import-untyped`: a third-party library doesn't ship types; the existing `ignore_missing_imports` overrides in pyproject.toml already cover Telethon; for other libraries, add a similar override (only if it's actually a 3rd-party untyped lib, NOT for first-party code)
- `method-assign`: a method override has a different signature; fix the override signature
- `misc`: catch-all category; read the message and address case-by-case

**Never use `# type: ignore`** to silence — the spec forbids it.

- [ ] **Step 4.3: Verify mypy error count is now small (target: 0)**

```bash
uv run mypy --strict src tests 2>&1 | Select-Object -Last 1
```

Expected: `Found N errors` where N is small (ideally 0; ≤5 is acceptable). If >5, more fixing needed; loop back to Step 4.1.

If the override is currently silencing, the count may show 0 even though errors exist. Verify by temporarily removing the override block (per Task 5) and running mypy. If errors appear, fix them, then restore the override temporarily. **Do not commit the override removal in this task** — that's Task 5.

- [ ] **Step 4.4: Verify tests pass + ruff clean**

```bash
uv run pytest
uv run ruff check
```

Both: success. The full test suite must pass before committing.

- [ ] **Step 4.5: Commit**

```bash
rtk git add tests/
rtk git commit -m "test: fix union-attr narrowing across 15 test files (113 errors)"
```

---

## Task 5: Remove the obsolete `[[tool.mypy.overrides]]` block

**Files:**
- Modify: `pyproject.toml:60-85` (delete the override block + its preceding comment)

- [ ] **Step 5.1: Read the current `pyproject.toml` to find the exact lines**

```bash
Get-Content pyproject.toml | Select-String "tool.mypy.overrides"
```

The block starts with `[[tool.mypy.overrides]]` and ends with `ignore_errors = true`. The preceding comment is the 3-line explanation about "Tests use Pydantic private APIs..."

- [ ] **Step 5.2: Delete the block**

Delete these lines (verify by re-reading the file):

```toml
[[tool.mypy.overrides]]
# Tests use Pydantic private APIs (_env_file), untyped **kwargs helpers, and
# rely on mypy-not-knowable narrowing (e.g. transition() returning a state the
# caller then asserts is non-None). Keep src strict; relax tests only.
module = [
    "test_config", "test_db", "test_gale_math", "test_main", "test_parser",
    "test_state_machine",
    "test_clock", "test_log", "test_auth",
    "test_telegram_client", "test_telegram_listener",
    "test_scheduler", "test_notifier",  # M6: NEW
    "test_olymp_broker", "test_olymp_broker_recorded",  # M8: NEW
]
ignore_errors = true
```

- [ ] **Step 5.3: Verify mypy still passes**

```bash
uv run mypy --strict src tests 2>&1 | Select-Object -Last 1
```

Expected: `Success: no issues found in N source files`. If errors reappear, fix them in this task before committing (Task 4 should have caught them).

- [ ] **Step 5.4: Verify ruff + tests still pass**

```bash
uv run ruff check
uv run pytest
```

Both: success.

- [ ] **Step 5.5: Commit**

```bash
rtk git add pyproject.toml
rtk git commit -m "chore: remove obsolete mypy test override"
```

---

## Task 6: Format 5 test files with `ruff format`

**Files:**
- Modify: `tests/test_log.py`
- Modify: `tests/test_reconnect_supervisor.py`
- Modify: `tests/test_recording_notifier_protocol.py`
- Modify: `tests/test_telegram_client.py`
- Modify: `tests/test_telegram_listener.py`

- [ ] **Step 6.1: Confirm which files need format**

```bash
uv run ruff format --check 2>&1 | Select-String "Would reformat"
```

Expected: 5 files. If a different set is reported, that's fine — reformat whatever ruff says needs it.

- [ ] **Step 6.2: Run `ruff format` on the 5 files**

```bash
uv run ruff format tests/test_log.py tests/test_reconnect_supervisor.py tests/test_recording_notifier_protocol.py tests/test_telegram_client.py tests/test_telegram_listener.py
```

Expected: ruff reports success (no error output).

- [ ] **Step 6.3: Verify format is now clean**

```bash
uv run ruff format --check
```

Expected: `N files already formatted` with no `Would reformat` lines.

- [ ] **Step 6.4: Verify mypy + tests still pass**

```bash
uv run mypy --strict src tests 2>&1 | Select-Object -Last 1
uv run pytest
```

Both: success. Format changes line lengths and spacing; if a fixture's annotation spans multiple lines after format, mypy should still pass.

- [ ] **Step 6.5: Commit**

```bash
rtk git add tests/test_log.py tests/test_reconnect_supervisor.py tests/test_recording_notifier_protocol.py tests/test_telegram_client.py tests/test_telegram_listener.py
rtk git commit -m "chore: ruff format 5 test files"
```

---

## Task 7: Sweep `src/` for type weak points (conditional)

**Files:**
- Possibly modify: any `src/signal_copier/**/*.py` file that has a `cast()`, `Any`, `# type: ignore`, or weak public API typing.

This task is **conditional**. Run the searches in §7.1; if any finding can be fixed without refactoring logic, fix it. If the sweep finds nothing fixable, skip this task and proceed to Task 8.

- [ ] **Step 7.1: Search for `cast()` calls**

```bash
rtk grep -rn "cast(" src/signal_copier/ --include="*.py"
```

For each hit, evaluate per spec §7.1 search 1:
- Can the cast be removed by adding proper types upstream?
- Is it for a third-party library that returns `Any`?

If fixable without logic refactor: fix and commit. If not, leave it.

- [ ] **Step 7.2: Search for `Any` returns**

```bash
rtk grep -rn "-> Any" src/signal_copier/ --include="*.py"
rtk grep -rn ": Any =" src/signal_copier/ --include="*.py"
```

For each hit, evaluate per spec §7.1 search 2. Common offenders: JSON parsing helpers, Protocol methods that legitimately return `Any`.

- [ ] **Step 7.3: Search for `# type: ignore` comments**

```bash
rtk grep -rn "type: ignore" src/signal_copier/ --include="*.py"
```

For each hit:
- Try removing the comment and re-running mypy. If mypy passes, the comment was unused → delete it.
- If mypy fails, evaluate if a proper type fix can replace the suppression.
- Last resort: keep the suppression, add a comment explaining why (this is the ONLY place `# type: ignore` is acceptable).

- [ ] **Step 7.4: Public API surface review**

Check these for proper typing:
- `src/signal_copier/__init__.py` re-exports: should use `from X import Y as Y` to preserve type info (already implemented in most projects; verify)
- `src/signal_copier/broker/base.py` `Broker` Protocol: all methods typed?
- `src/signal_copier/domain/state.py`: dataclass fields typed?
- Other public APIs as encountered

For each finding: fix or document why it can't be fixed.

- [ ] **Step 7.5: Decide commit strategy**

If you fixed 1-4 items: one commit per item (clear, reviewable).
If you fixed 5+ items: group into one commit.
If you fixed nothing: skip this task (go to Task 8 without committing).

For a single-item commit:
```bash
rtk git add <file>
rtk git commit -m "chore(src): <one-line description of fix>"
```

For a grouped commit:
```bash
rtk git add src/
rtk git commit -m "chore: tighten src/ weak points (cast/Any/type: ignore/public API)"
```

- [ ] **Step 7.6: Verify mypy + ruff + tests**

```bash
uv run mypy --strict src tests 2>&1 | Select-Object -Last 1
uv run ruff check
uv run ruff format --check
uv run pytest
```

All: success.

---

## Task 8: PRD §18 v0.10 changelog entry

**Files:**
- Modify: `docs/PRD.md` (add a new `v0.10` section to §18 Changelog)

- [ ] **Step 8.1: Locate §18 Changelog**

Find the latest entry. M11 added `v0.9 — M11 Railway deployment, runbook & project license`. The new v0.10 entry goes immediately after v0.9.

- [ ] **Step 8.2: Insert the v0.10 entry**

```markdown
### v0.10 — M12 type & format cleanup

- **M12 complete.** All 229 pre-existing mypy errors resolved via proper type annotations across 15 test files. Override block (`[[tool.mypy.overrides]]`) removed; tests now get full strict checking. 5 test files reformatted with `ruff format`. CI's `typecheck` and `format` jobs pass; auto-deploy to Railway works end-to-end for the first time.
- **Optional**: `src/` weak-point sweep tightened `cast()`/`Any`/`# type: ignore` items per spec §7. *(Only include this line if Task 7 found and fixed anything; omit otherwise.)*
- **M12 spec:** `docs/superpowers/specs/2026-06-23-m12-type-and-format-cleanup-design.md`. Plan: `docs/superpowers/plans/2026-06-23-m12-type-and-format-cleanup.md`. No edits to vendored `olymptrade_ws` (R-15).
```

- [ ] **Step 8.3: Verify**

```bash
Get-Content docs/PRD.md | Select-String -Pattern "v0.10 — M12"
```

Expected: match found.

- [ ] **Step 8.4: Commit**

```bash
rtk git add docs/PRD.md
rtk git commit -m "docs(prd): add v0.10 changelog entry for M12 type & format cleanup"
```

---

## Final Verification (Task 9 — manual)

After all commits land on `main`:

- [ ] **Step 9.1: Run the full local verification**

```bash
uv run mypy --strict src tests 2>&1 | Select-Object -Last 1
```
Expected: `Success: no issues found in N source files`.

```bash
uv run ruff check
```
Expected: `All checks passed!`.

```bash
uv run ruff format --check
```
Expected: no `Would reformat` lines.

```bash
uv run pytest
```
Expected: all tests pass.

- [ ] **Step 9.2: Confirm no vendored modifications**

```bash
rtk git diff --stat HEAD~8..HEAD -- src/olymptrade_ws/
```
Expected: empty output.

- [ ] **Step 9.3: Confirm 7-8 commits**

```bash
rtk git log --oneline HEAD~8..HEAD
```
Expected: ~7-8 commits (Tasks 1-8 minus any skipped). Each matches the spec's commit table.

- [ ] **Step 9.4: M12 is ready to ship**

Push to main (the next push will trigger the M11 CI workflow; this time `typecheck` and `format` pass; `deploy` fires for the first time).

---

## Summary

| Task | Commit type | Files | Approx. errors fixed |
|---|---|---|---|
| 1: `no-untyped-def` | `test:` | 7 test files | 30 |
| 2: `arg-type` | `test:` | 12 test files | 52 |
| 3: `assignment` | `test:` | 9 test files | 34 |
| 4: `union-attr` + others | `test:` | 15 test files | 113 |
| 5: Remove override | `chore:` | 1 file | (cleanup) |
| 6: Format 5 files | `chore:` | 5 test files | (formatting) |
| 7: src/ sweep | `chore:` (conditional) | varies | (latent) |
| 8: PRD changelog | `docs(prd):` | 1 file | (5 lines) |

**Total: 7-8 commits. M12 ships when the user pushes to main and CI passes for the first time.**

---

*End of plan.*