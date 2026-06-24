# Design Spec — M12: Type & Format Cleanup

**Date:** 2026-06-23
**Status:** Approved (sections 1–8) — pending user review of this written spec
**Milestone:** M12 (post-M11)
**Author:** opencode brainstorming session
**Related PRD sections:** §6 Tech Stack (ruff + mypy --strict as project quality gates), §15 Build Plan (M12 is post-M11), §18 Changelog (new v0.10 entry)

---

## 1. Problem & Motivation

M11 shipped the Railway deployment infrastructure (`.github/workflows/ci.yml`, Dockerfile, `docker-compose.yml`, README runbook, license, etc.) but the CI workflow's `typecheck` and `format` jobs currently **fail on every push** because the project has 229 pre-existing mypy errors and 5 files needing `ruff format` reformatting.

These issues were latent in the codebase before M11 — they were inherited from earlier milestones (M1–M10) and were not introduced by M11. The M11 plan acknowledged this in its final-review report (commit `5d49b51` had 229 errors at HEAD before M11 work). M11 shipped anyway because the user accepted the risk and pushed to main; CI failed as predicted, and the user explicitly accepted that outcome.

M12 fixes both issues definitively, unblocking the CI gates and enabling the M11 auto-deploy feature end-to-end. M12 also includes a focused "weak-point sweep" of `src/` per user request: tighten `cast()` calls, `Any` returns, `# type: ignore` comments, and public API typing — even though `src/` passes `--strict` cleanly today.

The pyproject.toml currently has a `[[tool.mypy.overrides]]` block listing ~15 test modules with `ignore_errors = true`. This block is **dead code**: the `module` field uses bare names like `"test_config"` instead of `tests.test_config`, so the override doesn't actually match any module. The 229 errors have been visible all along. M12 removes this dead block (after annotations are added) so tests get full strict checking going forward.

---

## 2. Goals & Non-Goals

### 2.1 Goals

1. **CI gates green.** `uv run mypy --strict src tests` and `uv run ruff format --check` both exit 0.
2. **Zero mypy errors.** All 229 errors resolved via proper type annotations (no `cast()` proliferation, no `# type: ignore` silences added).
3. **Tests fully type-checked.** The override block is removed; tests get full strict-mode checking going forward.
4. **Format compliance.** All 5 files reformatted.
5. **src/ weak points addressed.** `cast()`, `Any` returns, `# type: ignore` comments, and public API surface reviewed; each item either fixed in place or commented with rationale for keeping.
6. **No regressions.** All existing tests still pass; no new lint violations.
7. **R-15 compliance.** No edits to vendored `src/olymptrade_ws/`.

### 2.2 Non-Goals

- **Refactoring src/ logic.** Type fixes only; no behavior changes.
- **Adding new tests.** M12 only annotates existing tests.
- **Pre-commit hook setup.** Already deferred to a future milestone.
- **Tightening src/ beyond `--strict`.** E.g., enabling `disallow-any-explicit`, `warn-unused-ignores`, etc. — out of scope.
- **Modifying the CI workflow.** M11's `.github/workflows/ci.yml` is correct; M12 just makes the gates pass.
- **Documentation changes** beyond a one-line PRD §18 v0.10 changelog entry.

---

## 3. Architecture

### 3.1 Repo layout (changes)

```
signal-copier/
├── pyproject.toml                       MODIFIED: -[[tool.mypy.overrides]] block (~26 lines)
├── tests/
│   ├── test_state_machine.py            MODIFIED: +annotations (81 errors → 0)
│   ├── test_olymp_broker.py             MODIFIED: +annotations (53 errors → 0)
│   ├── test_db.py                       MODIFIED: +annotations (28 errors → 0)
│   ├── test_telegram_dm.py              MODIFIED: +annotations (27 errors → 0)
│   ├── test_reconnect_supervisor.py     MODIFIED: +annotations + format (13 errors → 0)
│   ├── test_recovery.py                 MODIFIED: +annotations (9 errors → 0)
│   ├── test_scheduler.py                MODIFIED: +annotations (6 errors → 0)
│   ├── test_gale_math.py                MODIFIED: +annotations (4 errors → 0)
│   ├── test_config.py                   MODIFIED: +annotations (3 errors → 0)
│   ├── test_main.py                     MODIFIED: +annotations (3 errors → 0)
│   ├── test_telegram_client.py          MODIFIED: +annotations + format (3 errors → 0)
│   ├── conftest.py                      MODIFIED: +annotations (2 errors → 0)
│   ├── test_telegram_listener.py        MODIFIED: +annotations + format (2 errors → 0)
│   ├── _scheduler_fixtures.py           MODIFIED: +annotations (1 error → 0)
│   ├── test_log.py                      MODIFIED: +annotations + format (1 error → 0)
│   └── test_recording_notifier_protocol.py  MODIFIED: format only (0 errors)
├── src/signal_copier/                   MODIFIED (sweep): cast(), Any, # type: ignore, public API
└── docs/PRD.md                          MODIFIED: +5 lines (v0.10 changelog entry)
```

### 3.2 Commit strategy

**Order matters** (see Risk R-7): annotations FIRST, override removal LAST, so CI stays green between commits.

| # | Commit | Purpose | Approx. files |
|---|---|---|---|
| 1 | `test: fix no-untyped-def annotations (30 errors)` | Add return type + parameter type annotations to test functions | 7 files |
| 2 | `test: fix arg-type annotations (52 errors)` | Fix argument type mismatches in test code | 12 files |
| 3 | `test: fix assignment annotations (34 errors)` | Add explicit type annotations to variable assignments | 9 files |
| 4 | `test: fix union-attr narrowing (72 errors + 41 remaining)` | Add `assert not None` or `cast()` to narrow unions | 15 files |
| 5 | `chore: remove obsolete mypy test override` | Delete the dead `[[tool.mypy.overrides]]` block from pyproject.toml | 1 file |
| 6 | `chore: ruff format 5 test files` | Run `ruff format` on the 5 files needing reformatting | 5 files |
| 7 | `chore: tighten src/ weak points` *(conditional — only if sweep finds fixable items)* | Address `cast()`, `Any`, `# type: ignore`, public API typing | varies |
| 8 | `docs(prd): add v0.10 changelog entry for M12 type & format cleanup` | One-line changelog | 1 file |

Total: 7 commits (or 6 if src/ sweep yields nothing).

### 3.3 No new runtime behavior

M12 is purely about static type checks and formatting. No production code paths change. All behavioral tests should pass unchanged.

---

## 4. Workstream A — Mypy override removal

### 4.1 What to delete

In `pyproject.toml` lines 60–85 (the entire `[[tool.mypy.overrides]]` block including its preceding comment):

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

### 4.2 Why this block isn't silencing errors

The `module` field expects module PATHS. The config uses bare names like `"test_config"` which would match a top-level module named `test_config`. The actual modules are `tests.test_config`, `tests.test_state_machine`, etc. — the bare names don't match. The 229 errors have been visible all along; the block is dead code with a misleading comment.

### 4.3 Why we remove it (instead of fixing it)

Once all 229 errors are annotated properly, the override isn't needed. Removing it:
- Gives tests full strict checking going forward (regression prevention)
- Removes dead code with a misleading comment
- Simplifies the config

### 4.4 Order

**Annotations first, override removal last** (commit 5). This ensures CI stays green between commits: the override continues silencing remaining errors during annotation work; once annotations are complete, removing the override changes nothing.

---

## 5. Workstream B — Test type annotations

### 5.1 The 229 errors, by category

| Category | Count | Fix pattern |
|---|---|---|
| `union-attr` | 72 | `assert not None` or `cast()` after calls that may return None |
| `arg-type` | 52 | Fix the calling code's types, or add `cast()` as last resort |
| `assignment` | 34 | Add explicit type annotations to variables |
| `no-untyped-def` | 30 | Add return type + parameter type annotations |
| `unused-ignore` | 9 | Delete unused `# type: ignore` comments |
| `method-assign` | 8 | Fix method override signatures |
| (others) | 24 | Various smaller fixes (attr-defined, call-arg, import-untyped, misc) |

### 5.2 Fix patterns (concrete examples)

**`no-untyped-def`** — add annotations:

```python
# Before
def test_foo(x, y):
    assert x == y

# After
def test_foo(x: int, y: int) -> None:
    assert x == y
```

```python
# Before (fixture)
@pytest.fixture
def some_fixture():
    return SomeClass()

# After
@pytest.fixture
def some_fixture() -> SomeClass:
    return SomeClass()
```

**`union-attr`** — narrow the type:

```python
# Before
result = state_machine.transition(signal)
result.next_action  # mypy: result could be None

# After
result = state_machine.transition(signal)
assert result is not None  # narrow the type for mypy
result.next_action
```

**`arg-type`** — fix the call site:

```python
# Before
direction = "up"  # inferred as str
signal = Signal(pair="EUR/JPY", direction=direction, ...)  # str vs Literal["up","down"]

# After
direction: Literal["up", "down"] = "up"
signal = Signal(pair="EUR/JPY", direction=direction, ...)
```

**`assignment`** — annotate the variable:

```python
# Before
result = some_func()  # inferred as Any

# After
result: SomeType = some_func()
```

### 5.3 Per-file error counts

| File | Errors |
|---|---|
| `tests/test_state_machine.py` | 81 |
| `tests/test_olymp_broker.py` | 53 |
| `tests/test_db.py` | 28 |
| `tests/test_telegram_dm.py` | 27 |
| `tests/test_reconnect_supervisor.py` | 13 |
| `tests/test_recovery.py` | 9 |
| `tests/test_scheduler.py` | 6 |
| `tests/test_gale_math.py` | 4 |
| `tests/test_config.py` | 3 |
| `tests/test_main.py` | 3 |
| `tests/test_telegram_client.py` | 3 |
| `tests/conftest.py` | 2 |
| `tests/test_telegram_listener.py` | 2 |
| `tests/_scheduler_fixtures.py` | 1 |
| `tests/test_log.py` | 1 |

### 5.4 Implementation approach

Each commit fixes ONE error category across multiple files. After each commit:
1. Run `uv run mypy --strict src tests 2>&1 | tail -1` — error count drops
2. Run `uv run pytest` — all tests still pass
3. Run `uv run ruff check` — no new violations

If a test fails after a commit, investigate (it may surface a real bug previously masked by `Any`).

### 5.5 What NOT to do

- Don't add `# type: ignore` comments as a substitute for fixing types (defeats the purpose).
- Don't use `cast()` unless it's a known-correct narrowing (rare).
- Don't change test logic — annotations only.
- Don't refactor test fixtures just to satisfy mypy.

---

## 6. Workstream C — Format fixes

### 6.1 The 5 files

```
tests/test_log.py
tests/test_reconnect_supervisor.py
tests/test_recording_notifier_protocol.py
tests/test_telegram_client.py
tests/test_telegram_listener.py
```

### 6.2 Action

```bash
uv run ruff format tests/test_log.py tests/test_reconnect_supervisor.py tests/test_recording_notifier_protocol.py tests/test_telegram_client.py tests/test_telegram_listener.py
```

### 6.3 Verification

```bash
uv run ruff format --check
```

Expected: `N files already formatted` (zero reformats needed).

### 6.4 Bundling

This workstream is bundled with Workstream D (src/ weak-point sweep) into commit 6 — both are simple, mechanical cleanups.

---

## 7. Workstream D — src/ weak-point sweep

### 7.1 The four targeted searches

Even though `src/` passes `--strict` cleanly (zero errors), the user wants to tighten latent type weak points. Four searches:

#### Search 1: `cast()` calls

```bash
rtk grep -rn "cast(" src/signal_copier/ --include="*.py"
```

For each hit, evaluate:
- Can the cast be removed by adding proper types upstream? → fix upstream, remove cast
- Is the cast for a third-party library that returns `Any`? → keep cast, add comment explaining why

**Out of scope**: refactoring third-party type stubs.

#### Search 2: `Any` returns

```bash
rtk grep -rn "-> Any" src/signal_copier/ --include="*.py"
rtk grep -rn ": Any =" src/signal_copier/ --include="*.py"
```

For each hit:
- Can the return type be made concrete?
- Common offenders: JSON parsing helpers, Protocol methods that legitimately return `Any`.

#### Search 3: `# type: ignore` comments

```bash
rtk grep -rn "type: ignore" src/signal_copier/ --include="*.py"
```

For each hit, try removing the comment and re-running mypy:
- If mypy still passes → the comment was unused → delete it
- If mypy fails → evaluate if a proper type fix can replace the suppression
- Last resort: keep the suppression but add a comment explaining why

#### Search 4: Public API surface

Public API surface = `__init__.py` re-exports, Protocol methods, dataclass fields, factory functions.

For each:
- Are all Protocol methods properly typed?
- Are all dataclass fields typed (no untyped `field: ... = ...`)?
- Are `__init__.py` re-exports using `from X import Y as Y` (preserves type info) vs `from X import Y` (loses it)?

### 7.2 What gets committed

Each meaningful change gets its own commit. Grouping rules:
- If 5+ weak points are found, group into one commit (`chore: tighten src/ type weak points`).
- If <5, individual commits per fix.
- If none found, skip this workstream entirely (commit 7 is conditional).

### 7.3 Out of scope

- Refactoring src/ logic
- Adding new types/Protocols that aren't currently needed
- Speculative type improvements

---

## 8. Verification

### 8.1 Per-commit verification

After each commit (1–4, 6, 7):

```bash
# 1. mypy error count
uv run mypy --strict src tests 2>&1 | tail -1
# Expected trajectory: 229 → 199 → 147 → 113 → 0

# 2. ruff check
uv run ruff check
# Expected: All checks passed!

# 3. full test suite
uv run pytest
# Expected: all tests pass

# 4. ruff format --check (after commit 6)
uv run ruff format --check
# Expected: 0 files would be reformatted
```

### 8.2 Final verification (after M12's last commit)

```bash
uv run mypy --strict src tests 2>&1 | tail -1
# Expected: Success: no issues found in N source files

uv run ruff check && uv run ruff format --check
# Expected: All checks passed! / 0 files would be reformatted

uv run pytest
# Expected: all tests pass
```

### 8.3 CI gate verification (post-M12, on next push to main)

- CI's `typecheck` job → PASS (was failing pre-M12)
- CI's `format` job → PASS (was failing pre-M12)
- CI's `lint` and `test` jobs → PASS (already passing pre-M12)
- CI's `deploy` job → fires (was blocked by typecheck/format failures)

---

## 9. Risks

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R-1 | Annotation changes subtly alter test semantics (e.g., a `cast()` masks a real bug) | Medium | Medium | Run `uv run pytest` after each commit; if a previously-passing test now fails, investigate before proceeding |
| R-2 | Adding type annotations to test fixtures breaks test isolation | Low | Medium | Type annotations don't change runtime behavior; should be safe. If a test fails, it's a real issue surfaced by stricter checking |
| R-3 | `cast()` proliferation (adding casts instead of fixing types) | Medium | Low | Code review should flag this; the goal is proper types, not silencing errors |
| R-4 | src/ weak-point sweep turns into a refactor | Low | Medium | Strict out-of-scope rules: no logic changes, no new types unless required. Stop at first "I'd need to refactor X to fix this" |
| R-5 | M12 takes longer than expected (1-3 days becomes 1-2 weeks) | Low | Low | Better to ship incomplete than to expand scope; user can defer remaining categories to M12b |
| R-6 | A test annotation requires changes to a fixture in `_scheduler_fixtures.py` (a shared test helper) | Medium | Low | Centralized fixture changes affect multiple tests; run the full suite after fixture changes |
| R-7 | Removing the override BEFORE annotations land causes CI to fail on every commit in between | Medium | Medium | **Mitigated by commit ordering: annotations first (commits 1-4), override removal last (commit 5)**. CI stays green throughout |

---

## 10. File-by-file implementation summary

### 10.1 Files modified

| Path | Approx. delta | Notes |
|---|---|---|
| `pyproject.toml` | -26 lines | Delete `[[tool.mypy.overrides]]` block |
| `tests/test_state_machine.py` | +50 / -10 | 81 errors |
| `tests/test_olymp_broker.py` | +30 / -5 | 53 errors |
| `tests/test_db.py` | +15 / -3 | 28 errors |
| `tests/test_telegram_dm.py` | +15 / -3 | 27 errors |
| `tests/test_reconnect_supervisor.py` | +8 / -2 + format | 13 errors + format |
| `tests/test_recovery.py` | +5 / -1 | 9 errors |
| `tests/test_scheduler.py` | +3 / -1 | 6 errors |
| `tests/test_gale_math.py` | +2 / -1 | 4 errors |
| `tests/test_config.py` | +2 / -0 | 3 errors |
| `tests/test_main.py` | +2 / -0 | 3 errors |
| `tests/test_telegram_client.py` | +2 / -0 + format | 3 errors + format |
| `tests/conftest.py` | +1 / -0 | 2 errors |
| `tests/test_telegram_listener.py` | +1 / -0 + format | 2 errors + format |
| `tests/_scheduler_fixtures.py` | +1 / -0 | 1 error |
| `tests/test_log.py` | +1 / -0 + format | 1 error + format |
| `tests/test_recording_notifier_protocol.py` | format only | 0 errors, just format |
| `src/signal_copier/**/*.py` | varies (weak-point sweep) | TBD during implementation |
| `docs/PRD.md` | +5 lines | v0.10 changelog entry |

**Total: ~17 files modified, ~140 lines added, ~25 lines deleted** (rough estimate; actual depends on annotation density and weak-point sweep findings).

### 10.2 Files NOT touched

- `src/olymptrade_ws/**` (vendored, R-15)
- `.github/workflows/ci.yml` (M11 ships correct; M12 just makes the gates pass)
- `Dockerfile`, `docker-compose.yml`, `LICENSE`, `README.md`, `pyproject.toml` (other than the override removal)
- All `src/signal_copier/**/*.py` files EXCEPT for the weak-point sweep targets

### 10.3 Commit summary

| # | SHA type | Commit message |
|---|---|---|
| 1 | `test:` | `fix no-untyped-def annotations across 7 test files (30 errors)` |
| 2 | `test:` | `fix arg-type annotations across 12 test files (52 errors)` |
| 3 | `test:` | `fix assignment annotations across 9 test files (34 errors)` |
| 4 | `test:` | `fix union-attr narrowing across 15 test files (72 errors + 41 remaining)` |
| 5 | `chore:` | `remove obsolete mypy test override` |
| 6 | `chore:` | `ruff format 5 test files` |
| 7 | `chore:` | `tighten src/ weak points (cast/Any/type: ignore/public API)` *(conditional)* |
| 8 | `docs(prd):` | `add v0.10 changelog entry for M12 type & format cleanup` |

---

## 11. Acceptance criteria

M12 is **done** when **all** of the following are true:

1. ✅ `[[tool.mypy.overrides]]` block removed from `pyproject.toml`
2. ✅ `uv run mypy --strict src tests` exits 0 with `Success: no issues found in N source files`
3. ✅ `uv run ruff check` exits 0 (no new violations introduced)
4. ✅ `uv run ruff format --check` exits 0 (zero files need reformatting)
5. ✅ `uv run pytest` passes (no test regressions from annotation changes)
6. ✅ All 229 original mypy errors resolved
7. ✅ src/ weak-point sweep complete: `cast()`, `Any` returns, `# type: ignore` comments, public API surface reviewed; each item either fixed or commented with rationale for keeping
8. ✅ CI's `typecheck` and `format` jobs pass on next push to main (verified after the M12 PR is merged)
9. ✅ CI's `deploy` job fires (auto-deploy to Railway works end-to-end for the first time)
10. ✅ No new commits added to vendored `src/olymptrade_ws/` (R-15)
11. ✅ PRD §18 gets a `v0.10` changelog entry for M12

---

## 12. Out of scope / Deferred

Items explicitly **not** included in M12 (per §2.2):

- Refactoring src/ logic (annotations only; no behavior changes)
- Adding new tests
- Pre-commit hook setup (deferred to a future milestone)
- Tightening src/ beyond `--strict` (e.g., `disallow-any-explicit`, `warn-unused-ignores`)
- Modifying the CI workflow (M11 ships correct; M12 just makes the gates pass)
- Documentation changes beyond a one-line PRD §18 v0.10 changelog entry
- Migrating to `dependency-groups.dev` (the `tool.uv.dev-dependencies` deprecation warning visible during mypy runs) — out of scope, belongs in a separate "tooling" milestone

---

## 13. References

- PRD v0.7, §6 Tech Stack (`ruff` and `mypy --strict` as quality gates)
- PRD v0.7, §15 Build Plan (M12 is post-M11; not enumerated but implied)
- PRD v0.7, §18 Changelog (new v0.10 entry for M12)
- M11 spec `docs/superpowers/specs/2026-06-23-m11-railway-deployment-design.md` (M11 introduced CI workflow that M12 makes pass)
- M11 final-review report (commit `5d49b51` had 229 mypy errors + 5 format issues at HEAD before M11 work)
- Existing `pyproject.toml` lines 60-85 (the dead `[[tool.mypy.overrides]]` block)
- Existing pyproject.toml lines 87-93 (`[tool.pytest.ini_options]` with `asyncio_mode = "auto"`, etc. — unchanged)

---

*End of spec.*