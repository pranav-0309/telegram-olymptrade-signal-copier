# M2 — State Machine, Gale Math, & Config Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the M2 per-signal state machine, gale amount math, time-window enforcement, and pydantic-settings config layer — all pure Python, no I/O, no `asyncio`, with 100% line + branch coverage on the three new modules (`config.py`, `domain/gale.py`, `domain/state.py`).

**Architecture:** Three pure modules: `config.py` (pydantic-settings `Config` with 13 env-driven fields + demo-only guardrail), `domain/gale.py` (stage amount lookup + gale trigger math), `domain/state.py` (frozen `SignalState` + `FireEvent` / `ResultEvent` dataclasses + pure `transition()` function). State machine enforces time-window checks: `FireEvent @ pending` checks the initial window; `ResultEvent(loss) @ placed_X` checks the *next* stage's window (FR-3.6). Surgical addition of 3 `trigger_unix_*` fields to M1's `Signal` dataclass. Stub `infra/log.py` and `__main__.py` load config and print a startup banner. No I/O, no `asyncio`, no broker, no clock.

**Tech Stack:** Python 3.13+ stdlib + `pydantic-settings>=2.6` (new runtime dep, transitively pulls `pydantic` and `python-dotenv`). `zoneinfo` (stdlib) for TZ validation. `pytest`, `ruff`, `mypy` (already in dev deps from M0).

**Spec reference:** `docs/superpowers/specs/2026-06-19-m2-state-machine-design.md`

---

## How to use this plan

1. Work through tasks in order. Each task is self-contained but builds on the previous.
2. Every step has explicit commands with expected output.
3. After every code change, run the verification step before committing.
4. If a verification fails, STOP. Read the error. Fix the issue. Re-run. Do not proceed to the next step until the current step's verification passes.
5. Commit frequently — one commit per task is the minimum.
6. Coverage check at the end is mandatory.

**Working directory:** all commands assume you are at the project root (`olymptrade/`).

**Platform notes:** uv commands are cross-platform. Use PowerShell syntax on Windows, bash on macOS/Linux. File paths use forward slashes (works in both shells).

**Plan-vs-spec divergence note:** T6 implements the full `domain/state.py` (types + `transition` + helpers) in one task with a single test-driven cycle per feature group. The spec §4.4 has the complete `state.py` code; the plan breaks the implementation into 13 steps (1 RED per test group, 1 GREEN for the full impl, then 8 more test-group expansions). This keeps each step small and verifiable while avoiding the churn of separate helper-addition steps.

**TDD order rationale:** Config first (foundational — every test depends on `_config()` helper), then `gale.py` (pure functions, no state), then `signal.py` modification (one-liner, no tests added but adds fields M1 didn't have), then `state.py` types (no behavior yet), then `state.py` transitions (full TDD with cascading test expansion). `infra/` and `__main__.py` last because they're glue.

---

## File Map

| File | Type | Created in | Purpose |
|---|---|---|---|
| `pyproject.toml` | MODIFY | T1 | Add `pydantic-settings` runtime dep |
| `uv.lock` | Modified by `uv lock` | T1 | Lockfile regenerates after dep change |
| `src/signal_copier/config.py` | NEW (TDD) | T2 | `Config` class: 13 fields + 2 validators + `tz()` |
| `tests/test_config.py` | NEW (TDD) | T2 | 12 tests for config loading + demo guardrail |
| `src/signal_copier/domain/gale.py` | NEW (TDD) | T3 | `amount_for_stage()`, `compute_gale_triggers()` |
| `tests/test_gale_math.py` | NEW (TDD) | T3 | 9 test functions (11 tests with param) for gale math |
| `src/signal_copier/domain/signal.py` | MODIFY | T4 | Add 3 `trigger_unix_*` fields to `Signal` dataclass |
| `src/signal_copier/domain/state.py` | NEW (TDD) | T5, T6 | `SignalState`, events, `transition()` |
| `tests/test_state_machine.py` | NEW (TDD) | T5, T6 | 36 test functions (38 tests with param) for state machine |
| `src/signal_copier/infra/__init__.py` | NEW | T7 | Empty package marker |
| `src/signal_copier/infra/log.py` | NEW | T7 | Stub `setup_logging()` (replaced by M7) |
| `src/signal_copier/domain/__init__.py` | MODIFY | T8 | Re-export new public symbols |
| `src/signal_copier/__main__.py` | MODIFY | T9 | Load config, log `🟢 Bot started` startup message |
| `tests/test_gale_math.py`, `tests/test_config.py`, `tests/test_state_machine.py` | Verified | T10 | Coverage, lint, mypy, end-to-end run |

**Out of scope for M2 (do not create or modify):** `tests/test_main.py` (M0 stub test stays unchanged), `tests/test_parser.py` (M1 unchanged), `Dockerfile`, `railway.toml`, `.dockerignore`, `.python-version`, `.env.example`, `.pre-commit-config.yaml`, `src/olymptrade_ws/`, `OlympTradeAPI/`, `.gitignore`, `README.md`, `docs/PRD.md`, `docs/tool-idea.md`, `docs/superpowers/specs/2026-06-19-m2-state-machine-design.md`, `docs/superpowers/plans/2026-06-19-m2-state-machine.md`.

---

## Task 1: Add `pydantic-settings` runtime dependency

**Files:**
- Modify: `pyproject.toml:108-110` (add to `dependencies`)

- [ ] **Step 1: Edit `pyproject.toml` to add the dep**

Open `pyproject.toml` and add the `pydantic-settings` line. The current M0 file has an empty `dependencies` list:

```toml
dependencies = [
    "pydantic-settings>=2.6",  # M2: config layer (D-3)
]
```

- [ ] **Step 2: Run `uv lock` to regenerate the lockfile**

Run:
```bash
uv lock
```

Expected: writes new entries to `uv.lock` (resolves `pydantic-settings`, `pydantic`, `python-dotenv`). No errors.

- [ ] **Step 3: Run `uv sync` to install the new dep**

Run:
```bash
uv sync
```

Expected: installs `pydantic-settings`, `pydantic`, `python-dotenv` (and their transitive deps) into `.venv/`. No errors. Exit 0.

- [ ] **Step 4: Verify the import works**

Run:
```bash
uv run python -c "import pydantic_settings; print(pydantic_settings.__version__)"
```

Expected: prints the installed version (e.g., `2.6.0` or later). Exit 0.

- [ ] **Step 5: Verify M0 and M1 still work (regression check)**

Run:
```bash
uv run pytest tests/ -v
```

Expected: All 33 existing tests pass (1 M0 `test_main.py` + 32 M1 `test_parser.py`). Exit 0.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "Add pydantic-settings runtime dep (M2)"
```

Commit message should mention: `pydantic-settings>=2.6` added to `dependencies`; `uv.lock` regenerated; transitive deps (`pydantic`, `python-dotenv`) installed; M0/M1 tests still pass.

---

## Task 2: Create `config.py` (TDD: defaults, TZ validator, account group validator, demo guardrail)

**Files:**
- Create: `src/signal_copier/config.py`
- Create: `tests/test_config.py` (scaffold + tests)

- [ ] **Step 1: Write the test file scaffold**

Create `tests/test_config.py` with these exact contents:

```python
from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from signal_copier.config import Config


def _config(**overrides) -> Config:
    return Config(_env_file=None, **overrides)
```

Notes:
- `_env_file=None` disables `.env` reading so tests don't accidentally load a developer's real `.env`.
- All tests in this file use the `_config()` helper.
- Imports will fail at this point (T2 has no `Config` yet); that's expected.

- [ ] **Step 2: Verify pytest collects the test file**

Run:
```bash
uv run pytest tests/test_config.py --collect-only -q
```

Expected: prints `no tests ran` (or `collected 0 items`) and exits with code 5. No tests are defined yet; just imports + a helper.

- [ ] **Step 3: Write the failing tests (defaults)**

Append these tests to `tests/test_config.py`:

```python
# --- Defaults -------------------------------------------------------------

def test_default_amount_initial_is_2_00() -> None:
    assert _config().amount_initial == Decimal("2.00")


def test_default_amount_gale1_is_4_00() -> None:
    assert _config().amount_gale1 == Decimal("4.00")


def test_default_amount_gale2_is_8_00() -> None:
    assert _config().amount_gale2 == Decimal("8.00")


def test_default_dry_run_is_true() -> None:
    assert _config().dry_run is True


def test_default_olymp_account_group_is_demo() -> None:
    assert _config().olymp_account_group == "demo"


def test_default_timezone_is_sao_paulo() -> None:
    assert _config().timezone == "America/Sao_Paulo"
```

- [ ] **Step 4: Run tests to verify they fail**

Run:
```bash
uv run pytest tests/test_config.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'signal_copier.config'` (or similar). Exit non-zero.

- [ ] **Step 5: Implement `config.py`**

Create the file `src/signal_copier/config.py` with these exact contents:

```python
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Telegram (not used by M2, declared for schema completeness) ------
    telegram_api_id: int = 0
    telegram_api_hash: str = ""
    telegram_phone: str = ""
    telegram_session_string: str = ""
    telegram_target_chat: str = "@analyst_channel"
    telegram_self_dm_notifications: bool = True

    # --- OlympTrade (not used by M2, declared for schema completeness) ----
    olymp_access_token: str = ""
    olymp_account_group: str = "demo"  # FR-6.6: must be "demo" for v1
    olymp_account_id: str = ""

    # --- Database (not used by M2, declared for schema completeness) ------
    database_url: str = "postgresql://user:pass@localhost:5432/copier"

    # --- Trading (used by M2) ---------------------------------------------
    dry_run: bool = True
    require_confirm: bool = False
    amount_initial: Decimal = Field(default=Decimal("2.00"), gt=0)
    amount_gale1: Decimal = Field(default=Decimal("4.00"), gt=0)
    amount_gale2: Decimal = Field(default=Decimal("8.00"), gt=0)
    expiration_seconds: int = Field(default=300, gt=0)
    trigger_skew_tolerance_seconds: float = Field(default=2.0, ge=0)

    # --- Optional safety limits (FR-6.1/6.2/6.3, deferred to M6) ----------
    daily_loss_limit: Decimal = Field(default=Decimal("0.00"), ge=0)
    daily_trade_limit: int = Field(default=0, ge=0)
    daily_drawdown_pct: int = Field(default=0, ge=0, le=100)

    # --- Schedule / Timezone (used by M2) ---------------------------------
    timezone: str = "America/Sao_Paulo"
    log_path: Path = Path("./logs/signal_copier.log")

    # --- Validators --------------------------------------------------------

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown timezone: {v!r}") from exc
        return v

    @field_validator("olymp_account_group")
    @classmethod
    def _validate_account_group(cls, v: str) -> str:
        if v not in {"demo", "real"}:
            raise ValueError(f"olymp_account_group must be 'demo' or 'real', got {v!r}")
        return v

    @model_validator(mode="after")
    def _demo_only_guardrail(self) -> Config:
        # FR-6.6: refuse to start with real account + dry_run off.
        if self.olymp_account_group == "real" and not self.dry_run:
            raise ValueError(
                "Refusing to start: OLYMP_ACCOUNT_GROUP=real requires DRY_RUN=true. "
                "Real-money trading is a v2 feature, gated behind a 7-day clean demo soak test."
            )
        return self

    def tz(self) -> ZoneInfo:
        """Convenience accessor for the configured timezone."""
        return ZoneInfo(self.timezone)
```

- [ ] **Step 6: Run tests to verify they pass**

Run:
```bash
uv run pytest tests/test_config.py -v
```

Expected: PASS with `6 passed`. Exit 0.

- [ ] **Step 7: Add TZ validator tests + verify they pass**

Append these tests to `tests/test_config.py`:

```python
# --- TZ validation --------------------------------------------------------

def test_valid_timezone_passes() -> None:
    cfg = _config(timezone="UTC")
    assert cfg.tz().key == "UTC"


def test_invalid_timezone_raises() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _config(timezone="Mars/Olympus_Mons")
    assert "unknown timezone" in str(exc_info.value)
```

Run:
```bash
uv run pytest tests/test_config.py -v
```

Expected: PASS with `8 passed`. (TZ validator is already implemented in §Step 5; these tests verify it.)

- [ ] **Step 8: Add account group + FR-6.6 guardrail tests**

Append these tests to `tests/test_config.py`:

```python
# --- Account group validation --------------------------------------------

def test_account_group_real_with_dry_run_true_is_allowed() -> None:
    cfg = _config(olymp_account_group="real", dry_run=True)
    assert cfg.olymp_account_group == "real"


def test_account_group_real_with_dry_run_false_refuses_to_start() -> None:
    """FR-6.6: real account + dry_run off → app refuses to start."""
    with pytest.raises(ValidationError) as exc_info:
        _config(olymp_account_group="real", dry_run=False)
    msg = str(exc_info.value)
    assert "Refusing to start" in msg
    assert "DRY_RUN=true" in msg


def test_account_group_demo_with_dry_run_false_is_allowed() -> None:
    cfg = _config(olymp_account_group="demo", dry_run=False)
    assert cfg.olymp_account_group == "demo"


def test_account_group_invalid_value_raises() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _config(olymp_account_group="sandbox")
    assert "must be 'demo' or 'real'" in str(exc_info.value)
```

Run:
```bash
uv run pytest tests/test_config.py -v
```

Expected: PASS with `12 passed`. Exit 0.

- [ ] **Step 9: Verify mypy on `config.py`**

Run:
```bash
uv run mypy src/signal_copier/config.py
```

Expected: `Success: no issues found in 1 source file`. Exit 0.

If mypy reports issues with the `_env_file` parameter (it's a pydantic-settings internal), add a `# type: ignore[call-arg]` comment to the `_config()` helper in `tests/test_config.py` (NOT to `config.py`).

- [ ] **Step 10: Commit**

```bash
git add src/signal_copier/config.py tests/test_config.py
git commit -m "Add Config (pydantic-settings) with demo-only guardrail (M2)"
```

Commit message should mention: 13 env-driven fields + TZ validator + account-group validator + FR-6.6 demo-only guardrail (`OLYMP_ACCOUNT_GROUP=real` + `DRY_RUN=false` raises `ValueError`); 12 tests cover defaults, TZ validation, and account group validation.

---

## Task 3: Create `domain/gale.py` (TDD: stage amount + gale trigger math)

**Files:**
- Create: `src/signal_copier/domain/gale.py`
- Create: `tests/test_gale_math.py`

- [ ] **Step 1: Create the test file scaffold**

Create `tests/test_gale_math.py` with these exact contents:

```python
from __future__ import annotations

from decimal import Decimal

import pytest

from signal_copier.config import Config
from signal_copier.domain.gale import amount_for_stage, compute_gale_triggers


def _config(**overrides) -> Config:
    return Config(_env_file=None, **overrides)
```

- [ ] **Step 2: Write the failing test (stage amount)**

Append this test:

```python
# --- amount_for_stage -----------------------------------------------------

def test_amount_for_initial_stage_returns_configured_value() -> None:
    cfg = _config(amount_initial=Decimal("2.00"))
    assert amount_for_stage("initial", cfg) == Decimal("2.00")
```

Run:
```bash
uv run pytest tests/test_gale_math.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'signal_copier.domain.gale'`. Exit non-zero.

- [ ] **Step 3: Implement `gale.py`**

Create the file `src/signal_copier/domain/gale.py` with these exact contents:

```python
from __future__ import annotations

from decimal import Decimal
from typing import Final, Literal

from signal_copier.config import Config


Stage = Literal["initial", "gale1", "gale2"]


# Stage → config field name. The mapping is fixed; we use a table instead of
# a chain of if/elif so adding a future "gale3" only touches the table.
_STAGE_AMOUNT_FIELD: Final[dict[str, str]] = {
    "initial": "amount_initial",
    "gale1": "amount_gale1",
    "gale2": "amount_gale2",
}


def amount_for_stage(stage: Stage, config: Config) -> Decimal:
    """Return the bet amount for a stage. Stage amounts, not increments (R-2)."""
    field = _STAGE_AMOUNT_FIELD.get(stage)
    if field is None:  # pragma: no cover — Literal type blocks this
        raise ValueError(f"unknown stage: {stage!r}")
    return getattr(config, field)


def compute_gale_triggers(
    trigger_unix_initial: float,
    expiration_seconds: int,
) -> tuple[float, float]:
    """Return (trigger_unix_gale1, trigger_unix_gale2) for a signal's initial trigger.

    Gale1 fires at initial + 1 expiration. Gale2 at initial + 2 expirations.
    R-2 + FR-5.5/5.6: stage times, not absolute offsets.
    """
    gale1 = trigger_unix_initial + float(expiration_seconds)
    gale2 = trigger_unix_initial + 2.0 * float(expiration_seconds)
    return (gale1, gale2)
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
uv run pytest tests/test_gale_math.py -v
```

Expected: PASS with `1 passed`. Exit 0.

- [ ] **Step 5: Add the rest of the stage-amount tests**

Append these tests to `tests/test_gale_math.py`:

```python
def test_amount_for_gale1_stage_returns_configured_value() -> None:
    cfg = _config(amount_gale1=Decimal("4.00"))
    assert amount_for_stage("gale1", cfg) == Decimal("4.00")


def test_amount_for_gale2_stage_returns_configured_value() -> None:
    cfg = _config(amount_gale2=Decimal("8.00"))
    assert amount_for_stage("gale2", cfg) == Decimal("8.00")


def test_amount_for_stage_returns_decimal_not_float() -> None:
    cfg = _config()
    result = amount_for_stage("initial", cfg)
    assert isinstance(result, Decimal)


@pytest.mark.parametrize(
    ("initial", "gale1", "gale2"),
    [
        (Decimal("2.00"), Decimal("4.00"), Decimal("8.00")),   # v1 default
        (Decimal("1.00"), Decimal("2.00"), Decimal("3.00")),   # non-default
        (Decimal("5.50"), Decimal("11.00"), Decimal("22.00")), # arbitrary
    ],
)
def test_amount_for_stage_reads_from_config(initial, gale1, gale2) -> None:
    cfg = _config(amount_initial=initial, amount_gale1=gale1, amount_gale2=gale2)
    assert amount_for_stage("initial", cfg) == initial
    assert amount_for_stage("gale1", cfg) == gale1
    assert amount_for_stage("gale2", cfg) == gale2
```

Run:
```bash
uv run pytest tests/test_gale_math.py -v
```

Expected: PASS with `8 passed` (1 + 3 stage + 1 type + 3 param cases). Exit 0.

- [ ] **Step 6: Add gale-trigger-math tests**

Append these tests to `tests/test_gale_math.py`:

```python
# --- compute_gale_triggers -----------------------------------------------

def test_compute_gale_triggers_for_5_minute_expiration() -> None:
    initial_unix = 1_700_000_000.0
    gale1, gale2 = compute_gale_triggers(initial_unix, 300)
    assert gale1 == initial_unix + 300.0
    assert gale2 == initial_unix + 600.0


def test_compute_gale_triggers_for_non_5_minute_expiration() -> None:
    initial_unix = 1_700_000_000.0
    gale1, gale2 = compute_gale_triggers(initial_unix, 60)  # 1-min expiration
    assert gale1 == initial_unix + 60.0
    assert gale2 == initial_unix + 120.0


def test_compute_gale_triggers_returns_floats() -> None:
    gale1, gale2 = compute_gale_triggers(1_700_000_000.0, 300)
    assert isinstance(gale1, float)
    assert isinstance(gale2, float)


def test_compute_gale_triggers_handles_zero_initial() -> None:
    gale1, gale2 = compute_gale_triggers(0.0, 300)
    assert gale1 == 300.0
    assert gale2 == 600.0
```

Run:
```bash
uv run pytest tests/test_gale_math.py -v
```

Expected: PASS with `12 passed` (8 + 4). Exit 0.

- [ ] **Step 7: Verify mypy on `gale.py`**

Run:
```bash
uv run mypy src/signal_copier/domain/gale.py
```

Expected: `Success: no issues found in 1 source file`. Exit 0.

- [ ] **Step 8: Commit**

```bash
git add src/signal_copier/domain/gale.py tests/test_gale_math.py
git commit -m "Add gale math: amount_for_stage + compute_gale_triggers (M2)"
```

Commit message should mention: 9 test functions (12 tests with param expansion) covering stage-amount lookup from `Config` and gale trigger arithmetic (initial + 1× / 2× expiration); 0.92 PnL approximation deferred to state machine.

---

## Task 4: Extend M1's `Signal` dataclass with 3 `trigger_unix_*` fields

**Files:**
- Modify: `src/signal_copier/domain/signal.py` (add 3 fields to `Signal`)

- [ ] **Step 1: Read the current `Signal` definition**

Open `src/signal_copier/domain/signal.py` and find the `Signal` dataclass (declared in M1). It currently has 9 fields. The 3 new fields go at the end.

- [ ] **Step 2: Add the 3 new fields**

Edit `src/signal_copier/domain/signal.py` — find the `Signal` dataclass and append the 3 new fields. The complete updated dataclass should look like:

```python
@dataclass(frozen=True, slots=True)
class Signal:
    signal_id: str
    pair: str
    direction: Literal["up", "down"]
    trigger_hhmm: str
    expiration_seconds: int
    received_at_unix: float
    source_message_id: int
    source_chat_id: int
    raw_text: str
    # --- Added in M2 (D-5) ---
    trigger_unix_initial: float    # epoch for trigger_hhmm on the signal's date in config TZ
    trigger_unix_gale1: float      # trigger_unix_initial + expiration_seconds
    trigger_unix_gale2: float      # trigger_unix_initial + 2 * expiration_seconds
```

Do not touch any other part of `signal.py`. The `ParsedSignal` dataclass above it stays as-is.

- [ ] **Step 3: Verify M1 tests still pass (regression check)**

Run:
```bash
uv run pytest tests/test_parser.py -v
```

Expected: All 32 M1 tests pass. The M1 tests don't construct `Signal` directly (only `ParsedSignal`), so the new required fields don't break them. Exit 0.

- [ ] **Step 4: Verify mypy on `signal.py`**

Run:
```bash
uv run mypy src/signal_copier/domain/signal.py
```

Expected: `Success: no issues found in 1 source file`. Exit 0.

- [ ] **Step 5: Commit**

```bash
git add src/signal_copier/domain/signal.py
git commit -m "Add trigger_unix_* fields to Signal dataclass (M2 D-5)"
```

Commit message should mention: 3 new `float` fields added at the end of `Signal` (per D-5); M1's 32 tests still pass; the M5 listener (not M2) is responsible for setting these at construction time from `trigger_hhmm` + `signal_date` + `TIMEZONE`.

---

## Task 5: Create `domain/state.py` — types + `SignalState.from_signal` (TDD)

**Files:**
- Create: `src/signal_copier/domain/state.py` (types + `from_signal` only; transition function comes in T6)
- Create: `tests/test_state_machine.py` (scaffold + initial state tests)

- [ ] **Step 1: Create the test file scaffold**

Create `tests/test_state_machine.py` with these exact contents:

```python
from __future__ import annotations

from decimal import Decimal

import pytest

from signal_copier.config import Config
from signal_copier.domain.signal import Signal
from signal_copier.domain.state import SignalState


# --- Helpers --------------------------------------------------------------

INITIAL_UNIX = 1_700_000_000.0  # 2023-11-14 22:13:20 UTC, an arbitrary Tuesday
GALE1_UNIX = INITIAL_UNIX + 300.0
GALE2_UNIX = INITIAL_UNIX + 600.0


def _config(**overrides) -> Config:
    return Config(
        _env_file=None,
        amount_initial=Decimal("2.00"),
        amount_gale1=Decimal("4.00"),
        amount_gale2=Decimal("8.00"),
        trigger_skew_tolerance_seconds=2.0,
        **overrides,
    )


def _signal(**overrides) -> Signal:
    defaults = dict(
        signal_id="test-sig-001",
        pair="EUR/JPY",
        direction="down",
        trigger_hhmm="10:20",
        expiration_seconds=300,
        received_at_unix=INITIAL_UNIX - 60.0,
        source_message_id=12345,
        source_chat_id=-1001234567890,
        raw_text="💰5-minute expiration\nEUR/JPY;10:20;PUT🟥",
        trigger_unix_initial=INITIAL_UNIX,
        trigger_unix_gale1=GALE1_UNIX,
        trigger_unix_gale2=GALE2_UNIX,
    )
    defaults.update(overrides)
    return Signal(**defaults)


def _initial_state(**overrides) -> SignalState:
    return SignalState.from_signal(_signal(**overrides), _config())
```

- [ ] **Step 2: Write the failing test (`from_signal`)**

Append this test to `tests/test_state_machine.py`:

```python
# --- Initial state construction -------------------------------------------

def test_from_signal_creates_pending_state() -> None:
    state = _initial_state()
    assert state.state == "pending"
    assert state.stage == "initial"
    assert state.amount == Decimal("2.00")
    assert state.trigger_unix == INITIAL_UNIX
    assert state.expires_at_unix == INITIAL_UNIX + 300.0
    assert state.result is None
    assert state.cumulative_pnl == Decimal("0.00")
    assert state.error_reason is None


def test_from_signal_uses_config_amounts() -> None:
    cfg = _config(amount_initial=Decimal("5.00"))
    state = SignalState.from_signal(_signal(), cfg)
    assert state.amount == Decimal("5.00")
```

- [ ] **Step 3: Run tests to verify they fail**

Run:
```bash
uv run pytest tests/test_state_machine.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'signal_copier.domain.state'`. Exit non-zero.

- [ ] **Step 4: Implement `state.py` with types + `from_signal` (no transitions yet)**

Create the file `src/signal_copier/domain/state.py` with these exact contents (transition function is added in T6):

```python
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Union

from signal_copier.config import Config
from signal_copier.domain.gale import Stage, amount_for_stage
from signal_copier.domain.signal import Signal


# --- Top-level signal state machine states (PRD FR-5.1) ------------------

# Pre-terminal: the cascade is in flight.
State = Literal[
    "pending",           # signal received, not yet fired
    "placed_initial",    # initial trade placed, awaiting result
    "placed_gale1",      # gale1 trade placed, awaiting result
    "placed_gale2",      # gale2 trade placed, awaiting result
]

# Terminal: cascade complete.
# Per D-7: done_tie is reserved for v2 (unreachable in M2's transitions).
# done_win / done_loss are the only signal-level terminals in v1.
# error carries error_reason for signal_expired / broker_unavailable / unknown.
TerminalState = Literal[
    "done_win",
    "done_loss",
    "done_tie",          # reserved for v2; M2 transitions never reach this
    "error",
]

AllStates = Union[State, TerminalState]

# Per-stage result (recorded in SignalState.result, not the top-level state).
# Tie and timeout at a non-final stage are treated as LOSS for cascade purposes (FR-5.3);
# the original outcome is still recorded here for observability.
StageResult = Literal["win", "loss", "tie", "timeout", "error"]


# --- Error reason enum (subset of PRD §9.1 + FR-5.1) ---------------------

ErrorReason = Literal[
    "signal_expired",     # FR-3.3 / FR-3.5 / FR-3.6 / FR-5.9: time window passed
    "broker_unavailable", # FR-4.4: broker dropped / token expired
    "unknown",
]


# --- Event types (M6 dispatches these based on scheduler / broker signals) ---

@dataclass(frozen=True, slots=True)
class FireEvent:
    """Try to fire the current stage's trade. Triggered by M6's asyncio.call_at."""
    now_unix: float


@dataclass(frozen=True, slots=True)
class ResultEvent:
    """The broker reports a result for the current stage's trade."""
    result: StageResult
    now_unix: float


Event = Union[FireEvent, ResultEvent]


# --- The state value (frozen, replaceable) --------------------------------

@dataclass(frozen=True, slots=True)
class SignalState:
    signal_id: str
    pair: str
    direction: Literal["up", "down"]
    state: AllStates                  # one of the State / TerminalState values
    stage: Stage | None               # current stage; None at terminal states
    amount: Decimal                   # bet amount for the current stage
    trigger_unix: float               # trigger time for the current stage
    expires_at_unix: float            # trigger_unix + expiration_seconds
    result: StageResult | None        # last stage result (None until first result)
    cumulative_pnl: Decimal            # sum of stage PnLs (signed; losses negative)
    error_reason: ErrorReason | None   # populated only when state == "error"

    @classmethod
    def from_signal(cls, signal: Signal, config: Config) -> SignalState:
        """Construct the initial state for a newly-received signal.

        The signal is 'pending' — the scheduler hasn't fired it yet.
        Gale trigger times come from the Signal's pre-computed fields (D-5).
        """
        return cls(
            signal_id=signal.signal_id,
            pair=signal.pair,
            direction=signal.direction,
            state="pending",
            stage="initial",
            amount=amount_for_stage("initial", config),
            trigger_unix=signal.trigger_unix_initial,
            expires_at_unix=signal.trigger_unix_initial + float(signal.expiration_seconds),
            result=None,
            cumulative_pnl=Decimal("0.00"),
            error_reason=None,
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run:
```bash
uv run pytest tests/test_state_machine.py -v
```

Expected: PASS with `2 passed`. Exit 0.

- [ ] **Step 6: Verify mypy on `state.py` (TDD stage 1)**

Run:
```bash
uv run mypy src/signal_copier/domain/state.py
```

Expected: `Success: no issues found in 1 source file`. Exit 0.

- [ ] **Step 7: Commit**

```bash
git add src/signal_copier/domain/state.py tests/test_state_machine.py
git commit -m "Add state machine types + SignalState.from_signal (M2 T5)"
```

Commit message should mention: types (`State`, `TerminalState`, `AllStates`, `StageResult`, `ErrorReason`), event types (`FireEvent`, `ResultEvent`), `SignalState` frozen dataclass, `SignalState.from_signal()` classmethod; 2 initial-state tests pass; transition function comes in T6.

---

## Task 6: Add `transition()` and helpers to `state.py` (TDD: full state machine)

**Files:**
- Modify: `src/signal_copier/domain/state.py` (append helpers + `transition()`)
- Modify: `tests/test_state_machine.py` (add ~34 more tests)

- [ ] **Step 1: Update test imports and add first batch of tests (TDD RED)**

Open `tests/test_state_machine.py`. The current imports section has only `SignalState` from `state.py`. Replace that line with the expanded import:

```python
from signal_copier.domain.state import (
    FireEvent,
    ResultEvent,
    SignalState,
    transition,
)
```

Notes: only the names actually used in the test code are imported (`ErrorReason` and `StageResult` are not needed because the test assertions use string literals like `"signal_expired"` directly).

Then append these tests after the existing `test_from_signal_uses_config_amounts` test:

```python
# --- Pending → placed_initial (happy path) -------------------------------

def test_pending_with_fire_event_at_exact_trigger_moves_to_placed_initial() -> None:
    state = _initial_state()
    result = transition(state, FireEvent(now_unix=INITIAL_UNIX), config=_config())
    assert result.success is True
    assert result.new_state is not None
    assert result.new_state.state == "placed_initial"
    assert result.new_state.stage == "initial"
    assert result.new_state.amount == Decimal("2.00")


def test_pending_with_fire_event_within_tolerance_window_moves_to_placed_initial() -> None:
    state = _initial_state()
    result = transition(state, FireEvent(now_unix=INITIAL_UNIX + 1.5), config=_config())
    assert result.success is True
    assert result.new_state.state == "placed_initial"


def test_pending_with_fire_event_at_tolerance_boundary_succeeds() -> None:
    state = _initial_state()
    result = transition(state, FireEvent(now_unix=INITIAL_UNIX + 2.0), config=_config())
    assert result.success is True
    assert result.new_state.state == "placed_initial"


# --- Pre-fire guard (FR-3.5) ---------------------------------------------

def test_pending_with_fire_event_past_tolerance_ends_cascade_with_error() -> None:
    state = _initial_state()
    result = transition(state, FireEvent(now_unix=INITIAL_UNIX + 3.0), config=_config())
    assert result.success is True
    assert result.new_state.state == "error"
    assert result.new_state.error_reason == "signal_expired"


def test_pending_with_fire_event_far_past_trigger_ends_cascade_with_error() -> None:
    state = _initial_state()
    result = transition(state, FireEvent(now_unix=INITIAL_UNIX + 3600), config=_config())
    assert result.success is True
    assert result.new_state.state == "error"
    assert result.new_state.error_reason == "signal_expired"


def test_pre_fire_guard_uses_config_tolerance() -> None:
    state = _initial_state()
    cfg = _config(trigger_skew_tolerance_seconds=10.0)
    result = transition(state, FireEvent(now_unix=INITIAL_UNIX + 5.0), config=cfg)
    assert result.success is True
    assert result.new_state.state == "placed_initial"


# --- Pending → invalid events --------------------------------------------

def test_pending_with_result_event_returns_invalid_event() -> None:
    state = _initial_state()
    result = transition(state, ResultEvent(result="win", now_unix=INITIAL_UNIX), config=_config())
    assert result.success is False
    assert result.new_state is None
    assert "invalid_event" in (result.reason or "")
```

- [ ] **Step 2: Run tests to verify they fail (RED)**

Run:
```bash
uv run pytest tests/test_state_machine.py -v
```

Expected: FAIL with `ImportError: cannot import name 'FireEvent' from 'signal_copier.domain.state'` (the test file's expanded imports reference symbols not yet in `state.py`). Exit non-zero.

- [ ] **Step 3: Implement the full `state.py` (TDD GREEN)**

Open `src/signal_copier/domain/state.py` and append (after the `SignalState` class) the entire transition module:

```python
# --- Transition result ---------------------------------------------------

@dataclass(frozen=True, slots=True)
class TransitionResult:
    """Outcome of a transition attempt.

    Always returns a value — never raises. Invalid events return
    success=False with a reason. The caller (M6) decides what to do.
    """
    success: bool
    new_state: SignalState | None   # set on success; None on failure
    reason: str | None              # set on failure; human-readable


# --- Pure transition function (D-1) --------------------------------------

def _check_time_window(
    state: SignalState, now_unix: float, tolerance: float,
) -> bool:
    """Return True if the current stage's time window has already passed.

    Per FR-3.3 / FR-3.5 / FR-3.6 / FR-5.9: a missed window ends the cascade.
    Tolerance is `trigger_skew_tolerance_seconds` from config (default 2.0s).
    """
    return now_unix > state.trigger_unix + tolerance


def _stage_pnl(state: SignalState, result: StageResult) -> Decimal:
    """Compute this stage's PnL contribution.

    For v1, broker PnL is approximated from amount + result; M8 will replace
    with the broker's reported PnL. Until then, this is the contract.
    """
    if result == "win":
        # OlympTrade typical payout is ~92% for 5-min digital — v1 approximation.
        # M8 will replace with broker-reported PnL.
        return state.amount * Decimal("0.92")
    if result in {"loss", "tie", "timeout"}:
        return -state.amount
    return Decimal("0.00")  # pragma: no cover


def _to_placed(
    state: SignalState, next_stage: Stage, result: StageResult,
    cumulative: Decimal, config: Config,
) -> SignalState:
    """Move from a placed_X state to the next placed_X state after a loss.

    Per FR-5.5/5.6: gale1 fires at trigger+5min, gale2 at trigger+10min.
    This couples M2 to the 5-minute expiration — see Risk #10.
    """
    trigger_unix = {
        "gale1": state.trigger_unix + 5 * 60,  # 5 min later
        "gale2": state.trigger_unix + 10 * 60,  # 10 min later
    }[next_stage]
    return SignalState(
        signal_id=state.signal_id,
        pair=state.pair,
        direction=state.direction,
        state=f"placed_{next_stage}",
        stage=next_stage,
        amount=amount_for_stage(next_stage, config),
        trigger_unix=trigger_unix,
        expires_at_unix=trigger_unix + 5 * 60,  # default 5-min expiration
        result=result,
        cumulative_pnl=cumulative,
        error_reason=None,
    )


def _next_stage_trigger_unix(state: SignalState) -> float:
    """Compute the next stage's trigger_unix from the current state.

    Returns gale1's trigger if current stage is initial, gale2's if gale1,
    or the current trigger_unix if already at gale2 (no next stage).
    """
    if state.stage == "initial":
        return state.trigger_unix + 5 * 60  # gale1 = initial + 5min
    if state.stage == "gale1":
        return state.trigger_unix + 5 * 60  # gale2 = gale1 + 5min
    return state.trigger_unix  # gale2 has no next stage


def _to_terminal(
    state: SignalState, terminal: TerminalState, result: StageResult, cumulative: Decimal,
) -> SignalState:
    return SignalState(
        signal_id=state.signal_id,
        pair=state.pair,
        direction=state.direction,
        state=terminal,
        stage=None,
        amount=Decimal("0.00"),
        trigger_unix=state.trigger_unix,
        expires_at_unix=state.expires_at_unix,
        result=result,
        cumulative_pnl=cumulative,
        error_reason=None,
    )


def _to_error(
    state: SignalState, reason: ErrorReason, result: StageResult | None, cumulative: Decimal,
) -> SignalState:
    return SignalState(
        signal_id=state.signal_id,
        pair=state.pair,
        direction=state.direction,
        state="error",
        stage=None,
        amount=Decimal("0.00"),
        trigger_unix=state.trigger_unix,
        expires_at_unix=state.expires_at_unix,
        result=result,
        cumulative_pnl=cumulative,
        error_reason=reason,
    )


def _advance_after_result(
    state: SignalState, result: StageResult, now_unix: float, config: Config,
) -> SignalState:
    """Compute the next state after a stage result.

    Per FR-3.6 / FR-5.9: when advancing to the next stage (gale1 or gale2),
    check the next stage's time window. If the next stage's window has
    already passed, the cascade ends with `error (signal_expired)`.

    Per FR-5.3: tie and timeout are treated as loss for cascade purposes.
    Per FR-5.4: a win at any stage ends the cascade as done_win.
    Per FR-5.7: a loss at gale2 ends the cascade as done_loss.
    """
    pnl_delta = _stage_pnl(state, result)
    cumulative = state.cumulative_pnl + pnl_delta

    if result == "win":
        return _to_terminal(state, "done_win", result, cumulative)

    if result in {"loss", "tie", "timeout"}:
        if state.stage == "gale2":
            # FR-5.7: gale2 loss = done_loss (terminal; no time check needed)
            return _to_terminal(state, "done_loss", result, cumulative)
        # Pre-fire guard for the next stage (FR-3.6 / FR-5.9).
        next_trigger = _next_stage_trigger_unix(state)
        if now_unix > next_trigger + config.trigger_skew_tolerance_seconds:
            return _to_error(state, "signal_expired", result, cumulative)
        if state.stage == "initial":
            return _to_placed(state, "gale1", result, cumulative, config)
        if state.stage == "gale1":
            return _to_placed(state, "gale2", result, cumulative, config)

    if result == "error":
        return _to_error(state, "broker_unavailable", result, cumulative)

    return _to_error(state, "unknown", result, cumulative)  # pragma: no cover


def transition(
    state: SignalState, event: Event, *, config: Config,
) -> TransitionResult:
    """Apply an event to the current state. Returns the new state (or failure).

    This is a pure function. Same (state, event, config) → same result.
    """
    # Terminal states are absorbing — no further events accepted.
    if state.state in {"done_win", "done_loss", "done_tie", "error"}:
        return TransitionResult(
            success=False, new_state=None,
            reason=f"invalid_event: state is terminal ({state.state}); event {event} ignored",
        )

    # FireEvent: try to fire the current stage's trade.
    if isinstance(event, FireEvent):
        # Pre-fire guard (FR-3.5 / FR-3.6 / FR-5.9): every stage's window matters.
        if _check_time_window(state, event.now_unix, config.trigger_skew_tolerance_seconds):
            new_state = _to_error(state, "signal_expired", None, state.cumulative_pnl)
            return TransitionResult(success=True, new_state=new_state, reason=None)
        if state.state == "pending" and state.stage == "initial":
            new_state = SignalState(
                signal_id=state.signal_id,
                pair=state.pair,
                direction=state.direction,
                state="placed_initial",
                stage="initial",
                amount=state.amount,
                trigger_unix=state.trigger_unix,
                expires_at_unix=state.expires_at_unix,
                result=None,
                cumulative_pnl=state.cumulative_pnl,
                error_reason=None,
            )
            return TransitionResult(success=True, new_state=new_state, reason=None)
        # placed_gale1 / placed_gale2 cannot be re-fired — they're awaiting result.
        return TransitionResult(
            success=False, new_state=None,
            reason=f"invalid_event: FireEvent on placed state ({state.state}); record_result first",
        )

    # ResultEvent: apply result to the current placed stage.
    if isinstance(event, ResultEvent):
        if state.state not in {"placed_initial", "placed_gale1", "placed_gale2"}:
            return TransitionResult(
                success=False, new_state=None,
                reason=f"invalid_event: ResultEvent on non-placed state ({state.state})",
            )
        new_state = _advance_after_result(state, event.result, event.now_unix, config)
        return TransitionResult(success=True, new_state=new_state, reason=None)

    return TransitionResult(success=False, new_state=None, reason="unknown event type")  # pragma: no cover
```

- [ ] **Step 4: Run tests to verify they pass (GREEN)**

Run:
```bash
uv run pytest tests/test_state_machine.py -v
```

Expected: PASS with `9 passed` (2 from T5 + 7 from this task). Exit 0.

- [ ] **Step 5: Add the `placed_initial` transition tests**

Append these tests to `tests/test_state_machine.py`:

```python
# --- Placed_initial transitions ------------------------------------------

def _placed_initial_state() -> SignalState:
    state = _initial_state()
    r = transition(state, FireEvent(now_unix=INITIAL_UNIX), config=_config())
    assert r.success and r.new_state
    return r.new_state


def test_placed_initial_with_win_result_moves_to_done_win() -> None:
    state = _placed_initial_state()
    result = transition(state, ResultEvent(result="win", now_unix=INITIAL_UNIX + 60), config=_config())
    assert result.success
    assert result.new_state.state == "done_win"
    assert result.new_state.stage is None
    assert result.new_state.result == "win"
    assert result.new_state.cumulative_pnl == Decimal("2.00") * Decimal("0.92")


def test_placed_initial_with_loss_result_moves_to_placed_gale1() -> None:
    state = _placed_initial_state()
    result = transition(state, ResultEvent(result="loss", now_unix=INITIAL_UNIX + 300), config=_config())
    assert result.success
    assert result.new_state.state == "placed_gale1"
    assert result.new_state.stage == "gale1"
    assert result.new_state.amount == Decimal("4.00")
    assert result.new_state.trigger_unix == GALE1_UNIX
    assert result.new_state.cumulative_pnl == Decimal("-2.00")


def test_placed_initial_with_tie_result_moves_to_placed_gale1() -> None:
    """FR-5.3: tie at non-final stage is treated as loss for cascade purposes."""
    state = _placed_initial_state()
    result = transition(state, ResultEvent(result="tie", now_unix=INITIAL_UNIX + 300), config=_config())
    assert result.success
    assert result.new_state.state == "placed_gale1"
    assert result.new_state.result == "tie"  # original outcome recorded


def test_placed_initial_with_timeout_result_moves_to_placed_gale1() -> None:
    """FR-5.3: timeout at non-final stage is treated as loss for cascade purposes."""
    state = _placed_initial_state()
    result = transition(state, ResultEvent(result="timeout", now_unix=INITIAL_UNIX + 330), config=_config())
    assert result.success
    assert result.new_state.state == "placed_gale1"
    assert result.new_state.result == "timeout"


def test_placed_initial_with_error_result_moves_to_error_state() -> None:
    state = _placed_initial_state()
    result = transition(state, ResultEvent(result="error", now_unix=INITIAL_UNIX + 60), config=_config())
    assert result.success
    assert result.new_state.state == "error"
    assert result.new_state.error_reason == "broker_unavailable"


def test_placed_initial_with_fire_event_returns_invalid() -> None:
    state = _placed_initial_state()
    result = transition(state, FireEvent(now_unix=INITIAL_UNIX + 60), config=_config())
    assert result.success is False
    assert "invalid_event" in (result.reason or "")
```

Run:
```bash
uv run pytest tests/test_state_machine.py -v
```

Expected: PASS with `15 passed` (9 + 6). Exit 0.

- [ ] **Step 6: Add the `placed_gale1` transition tests**

Append these tests:

```python
# --- Placed_gale1 transitions --------------------------------------------

def _placed_gale1_state() -> SignalState:
    state = _placed_initial_state()
    r = transition(state, ResultEvent(result="loss", now_unix=INITIAL_UNIX + 300), config=_config())
    assert r.success and r.new_state
    return r.new_state


def test_placed_gale1_with_win_result_moves_to_done_win() -> None:
    state = _placed_gale1_state()
    result = transition(state, ResultEvent(result="win", now_unix=GALE1_UNIX + 60), config=_config())
    assert result.success
    assert result.new_state.state == "done_win"
    # PnL: -2 + 4*0.92 = -2 + 3.68 = +1.68
    assert result.new_state.cumulative_pnl == Decimal("1.68")


def test_placed_gale1_with_loss_result_moves_to_placed_gale2() -> None:
    state = _placed_gale1_state()
    result = transition(state, ResultEvent(result="loss", now_unix=GALE1_UNIX + 300), config=_config())
    assert result.success
    assert result.new_state.state == "placed_gale2"
    assert result.new_state.stage == "gale2"
    assert result.new_state.amount == Decimal("8.00")
    assert result.new_state.trigger_unix == GALE2_UNIX
    assert result.new_state.cumulative_pnl == Decimal("-6.00")


def test_placed_gale1_with_tie_result_moves_to_placed_gale2() -> None:
    state = _placed_gale1_state()
    result = transition(state, ResultEvent(result="tie", now_unix=GALE1_UNIX + 60), config=_config())
    assert result.success
    assert result.new_state.state == "placed_gale2"


def test_placed_gale1_with_fire_event_returns_invalid() -> None:
    state = _placed_gale1_state()
    result = transition(state, FireEvent(now_unix=GALE1_UNIX), config=_config())
    assert result.success is False


def test_placed_gale1_with_error_result_moves_to_error_state() -> None:
    state = _placed_gale1_state()
    result = transition(state, ResultEvent(result="error", now_unix=GALE1_UNIX + 60), config=_config())
    assert result.success
    assert result.new_state.error_reason == "broker_unavailable"
```

Run:
```bash
uv run pytest tests/test_state_machine.py -v
```

Expected: PASS with `20 passed` (15 + 5). Exit 0.

- [ ] **Step 7: Add the `placed_gale2` transition tests**

Append these tests:

```python
# --- Placed_gale2 transitions --------------------------------------------

def _placed_gale2_state() -> SignalState:
    state = _placed_gale1_state()
    r = transition(state, ResultEvent(result="loss", now_unix=GALE1_UNIX + 300), config=_config())
    assert r.success and r.new_state
    return r.new_state


def test_placed_gale2_with_win_result_moves_to_done_win() -> None:
    state = _placed_gale2_state()
    result = transition(state, ResultEvent(result="win", now_unix=GALE2_UNIX + 60), config=_config())
    assert result.success
    assert result.new_state.state == "done_win"
    # PnL: -2 - 4 + 8*0.92 = -6 + 7.36 = +1.36
    assert result.new_state.cumulative_pnl == Decimal("1.36")


def test_placed_gale2_with_loss_result_moves_to_done_loss() -> None:
    """FR-5.7: gale2 loss = done_loss terminal."""
    state = _placed_gale2_state()
    result = transition(state, ResultEvent(result="loss", now_unix=GALE2_UNIX + 300), config=_config())
    assert result.success
    assert result.new_state.state == "done_loss"
    assert result.new_state.cumulative_pnl == Decimal("-14.00")


def test_placed_gale2_with_tie_result_moves_to_done_loss() -> None:
    """FR-5.3 + FR-5.7: gale2 tie cascades to done_loss (no further stages)."""
    state = _placed_gale2_state()
    result = transition(state, ResultEvent(result="tie", now_unix=GALE2_UNIX + 60), config=_config())
    assert result.success
    assert result.new_state.state == "done_loss"


def test_placed_gale2_with_timeout_result_moves_to_done_loss() -> None:
    state = _placed_gale2_state()
    result = transition(state, ResultEvent(result="timeout", now_unix=GALE2_UNIX + 330), config=_config())
    assert result.success
    assert result.new_state.state == "done_loss"


def test_placed_gale2_with_fire_event_returns_invalid() -> None:
    state = _placed_gale2_state()
    result = transition(state, FireEvent(now_unix=GALE2_UNIX), config=_config())
    assert result.success is False
```

Run:
```bash
uv run pytest tests/test_state_machine.py -v
```

Expected: PASS with `25 passed` (20 + 5). Exit 0.

- [ ] **Step 8: Add the time-window-on-gale-cascade tests**

Append these tests:

```python
# --- Time-window enforcement on gale cascade -----------------------------

def test_placed_gale1_with_loss_result_past_gale2_window_ends_cascade() -> None:
    """FR-3.6 / FR-5.9: gale2's window passed before its fire → error.

    At placed_gale1, receiving a loss result should advance to gale2 — but
    if gale2's window has already passed, the cascade ends with error.
    """
    state = _placed_gale1_state()
    # gale1 lost at GALE1_UNIX + 300, gale2 trigger is GALE2_UNIX = GALE1_UNIX + 300.
    # now_unix is well past gale2's trigger + tolerance.
    result = transition(
        state,
        ResultEvent(result="loss", now_unix=GALE2_UNIX + 100.0),
        config=_config(),
    )
    assert result.success
    assert result.new_state.state == "error"
    assert result.new_state.error_reason == "signal_expired"


def test_placed_initial_with_loss_result_past_gale1_window_ends_cascade() -> None:
    """FR-3.6: gale1's window passed before its fire → error."""
    state = _placed_initial_state()
    # initial lost at INITIAL_UNIX + 300, gale1 trigger is GALE1_UNIX = INITIAL_UNIX + 300.
    # now_unix is well past gale1's trigger + tolerance.
    result = transition(
        state,
        ResultEvent(result="loss", now_unix=GALE1_UNIX + 100.0),
        config=_config(),
    )
    assert result.success
    assert result.new_state.state == "error"
    assert result.new_state.error_reason == "signal_expired"


def test_placed_gale2_with_loss_result_at_gale2_window_boundary_succeeds() -> None:
    """At gale2, a loss always goes to done_loss (no time check; gale2 is terminal)."""
    state = _placed_gale2_state()
    result = transition(
        state,
        ResultEvent(result="loss", now_unix=GALE2_UNIX + 100.0),
        config=_config(),
    )
    assert result.success
    assert result.new_state.state == "done_loss"
```

Run:
```bash
uv run pytest tests/test_state_machine.py -v
```

Expected: PASS with `28 passed` (25 + 3). Exit 0.

- [ ] **Step 9: Add the terminal-state absorbing tests**

Append these tests:

```python
# --- Terminal states are absorbing ---------------------------------------

@pytest.mark.parametrize("terminal", ["done_win", "done_loss", "error"])
def test_terminal_states_reject_fire_event(terminal: str) -> None:
    """done_tie is excluded — reserved for v2 (D-7), unreachable in M2."""
    state = _initial_state()
    if terminal == "done_win":
        placed = transition(state, FireEvent(now_unix=INITIAL_UNIX), config=_config()).new_state
        r = transition(placed, ResultEvent(result="win", now_unix=INITIAL_UNIX + 60), config=_config())
    elif terminal == "done_loss":
        s1 = transition(state, FireEvent(now_unix=INITIAL_UNIX), config=_config()).new_state
        s2 = transition(s1, ResultEvent(result="loss", now_unix=INITIAL_UNIX + 300), config=_config()).new_state
        s3 = transition(s2, FireEvent(now_unix=GALE1_UNIX), config=_config()).new_state
        s4 = transition(s3, ResultEvent(result="loss", now_unix=GALE1_UNIX + 300), config=_config()).new_state
        s5 = transition(s4, FireEvent(now_unix=GALE2_UNIX), config=_config()).new_state
        r = transition(s5, ResultEvent(result="loss", now_unix=GALE2_UNIX + 300), config=_config())
    else:  # error
        r = transition(state, FireEvent(now_unix=INITIAL_UNIX + 100), config=_config())
    final = r.new_state
    assert final is not None
    result = transition(final, FireEvent(now_unix=INITIAL_UNIX + 60), config=_config())
    assert result.success is False
    assert "invalid_event" in (result.reason or "")
```

Run:
```bash
uv run pytest tests/test_state_machine.py -v
```

Expected: PASS with `31 passed` (28 + 3 parametrized cases). Exit 0.

- [ ] **Step 10: Add the full cascade tests**

Append these tests:

```python
# --- Full cascade tests (end-to-end through the state machine) ----------

def test_full_cascade_initial_win_path() -> None:
    """pending → placed_initial → done_win (win at initial)."""
    s0 = _initial_state()
    s1 = transition(s0, FireEvent(now_unix=INITIAL_UNIX), config=_config()).new_state
    s2 = transition(s1, ResultEvent(result="win", now_unix=INITIAL_UNIX + 60), config=_config()).new_state
    assert s2.state == "done_win"
    assert s2.cumulative_pnl == Decimal("1.84")


def test_full_cascade_gale1_win_path() -> None:
    """pending → placed_initial → placed_gale1 → done_win."""
    s0 = _initial_state()
    s1 = transition(s0, FireEvent(now_unix=INITIAL_UNIX), config=_config()).new_state
    s2 = transition(s1, ResultEvent(result="loss", now_unix=INITIAL_UNIX + 300), config=_config()).new_state
    s3 = transition(s2, FireEvent(now_unix=GALE1_UNIX), config=_config()).new_state
    s4 = transition(s3, ResultEvent(result="win", now_unix=GALE1_UNIX + 60), config=_config()).new_state
    assert s4.state == "done_win"
    # PnL: -2 + 4*0.92 = -2 + 3.68 = +1.68
    assert s4.cumulative_pnl == Decimal("1.68")


def test_full_cascade_gale2_win_path() -> None:
    """pending → placed_initial → placed_gale1 → placed_gale2 → done_win."""
    s0 = _initial_state()
    s1 = transition(s0, FireEvent(now_unix=INITIAL_UNIX), config=_config()).new_state
    s2 = transition(s1, ResultEvent(result="loss", now_unix=INITIAL_UNIX + 300), config=_config()).new_state
    s3 = transition(s2, FireEvent(now_unix=GALE1_UNIX), config=_config()).new_state
    s4 = transition(s3, ResultEvent(result="loss", now_unix=GALE1_UNIX + 300), config=_config()).new_state
    s5 = transition(s4, FireEvent(now_unix=GALE2_UNIX), config=_config()).new_state
    s6 = transition(s5, ResultEvent(result="win", now_unix=GALE2_UNIX + 60), config=_config()).new_state
    assert s6.state == "done_win"
    # PnL: -2 - 4 + 8*0.92 = -6 + 7.36 = +1.36
    assert s6.cumulative_pnl == Decimal("1.36")


def test_full_cascade_full_loss_path() -> None:
    """pending → placed_initial → placed_gale1 → placed_gale2 → done_loss."""
    s0 = _initial_state()
    s1 = transition(s0, FireEvent(now_unix=INITIAL_UNIX), config=_config()).new_state
    s2 = transition(s1, ResultEvent(result="loss", now_unix=INITIAL_UNIX + 300), config=_config()).new_state
    s3 = transition(s2, FireEvent(now_unix=GALE1_UNIX), config=_config()).new_state
    s4 = transition(s3, ResultEvent(result="loss", now_unix=GALE1_UNIX + 300), config=_config()).new_state
    s5 = transition(s4, FireEvent(now_unix=GALE2_UNIX), config=_config()).new_state
    s6 = transition(s5, ResultEvent(result="loss", now_unix=GALE2_UNIX + 300), config=_config()).new_state
    assert s6.state == "done_loss"
    assert s6.cumulative_pnl == Decimal("-14.00")


def test_full_cascade_signal_expired_at_initial() -> None:
    s0 = _initial_state()
    s1 = transition(s0, FireEvent(now_unix=INITIAL_UNIX + 100), config=_config()).new_state
    assert s1.state == "error"
    assert s1.error_reason == "signal_expired"


def test_full_cascade_signal_expired_at_gale1() -> None:
    """Initial loses; gale1's window has already passed (per FR-3.6).

    In the new model, the time check happens on ResultEvent(loss) at
    placed_initial. The ResultEvent carries now_unix past gale1's window.
    """
    s0 = _initial_state()
    s1 = transition(s0, FireEvent(now_unix=INITIAL_UNIX), config=_config()).new_state
    s2 = transition(
        s1,
        ResultEvent(result="loss", now_unix=GALE1_UNIX + 100),  # past gale1's window
        config=_config(),
    ).new_state
    assert s2.state == "error"
    assert s2.error_reason == "signal_expired"


def test_full_cascade_signal_expired_at_gale2() -> None:
    """Gale1 loses; gale2's window has already passed (per FR-3.6)."""
    s0 = _initial_state()
    s1 = transition(s0, FireEvent(now_unix=INITIAL_UNIX), config=_config()).new_state
    s2 = transition(s1, ResultEvent(result="loss", now_unix=INITIAL_UNIX + 300), config=_config()).new_state
    s3 = transition(
        s2,
        ResultEvent(result="loss", now_unix=GALE2_UNIX + 100),  # past gale2's window
        config=_config(),
    ).new_state
    assert s3.state == "error"
    assert s3.error_reason == "signal_expired"
```

Run:
```bash
uv run pytest tests/test_state_machine.py -v
```

Expected: PASS with `38 passed` (31 + 7). Exit 0.

- [ ] **Step 11: Verify mypy on the full `state.py`**

Run:
```bash
uv run mypy src/signal_copier/domain/state.py
```

Expected: `Success: no issues found in 1 source file`. Exit 0.

- [ ] **Step 12: Verify coverage on `state.py`**

Run:
```bash
uv run pytest tests/test_state_machine.py --cov=signal_copier.domain.state --cov-report=term-missing
```

Expected: `signal_copier/domain/state.py` shows `100%`. Other files may show lower. Exit 0.

If coverage on `state.py` is below 100%, look at the missing lines and add tests. The `# pragma: no cover` markers cover intentionally-unreachable branches.

- [ ] **Step 13: Commit**

```bash
git add src/signal_copier/domain/state.py tests/test_state_machine.py
git commit -m "Add transition() + helpers: time-window checks at FireEvent + ResultEvent (M2 T6)"
```

Commit message should mention: `transition()` is pure, takes `now_unix` via events; time-window check fires on `FireEvent @ pending` (initial window) AND on `ResultEvent(loss) @ placed_X` (next-stage window, per FR-3.6/FR-5.9); gale2's loss is terminal (`done_loss`); 36 test functions / 38 tests with parametrization, 100% line + branch coverage on `state.py`.

---

## Task 7: Create `infra/` package stubs

**Files:**
- Create: `src/signal_copier/infra/__init__.py` (empty)
- Create: `src/signal_copier/infra/log.py` (stub)

- [ ] **Step 1: Create the `infra/` package marker**

Create the file `src/signal_copier/infra/__init__.py` with **no contents** (empty file). The empty file marks `infra/` as a Python subpackage so `from signal_copier.infra.log import setup_logging` works.

- [ ] **Step 2: Create the `infra/log.py` stub**

Create the file `src/signal_copier/infra/log.py` with these exact contents:

```python
from __future__ import annotations

from pathlib import Path


def setup_logging(log_path: Path) -> None:
    """Stub. Replaced by M7 with loguru setup."""
    _ = log_path  # unused until M7
```

- [ ] **Step 3: Verify the stub imports cleanly**

Run:
```bash
uv run python -c "from signal_copier.infra.log import setup_logging; setup_logging('/tmp/test.log'); print('ok')"
```

Expected: prints `ok`. Exit 0.

- [ ] **Step 4: Verify mypy on `infra/log.py`**

Run:
```bash
uv run mypy src/signal_copier/infra/log.py
```

Expected: `Success: no issues found in 1 source file`. Exit 0.

- [ ] **Step 5: Commit**

```bash
git add src/signal_copier/infra/__init__.py src/signal_copier/infra/log.py
git commit -m "Add infra/ package + setup_logging stub (M2, replaced by M7)"
```

Commit message should mention: `infra/` package marker + `setup_logging()` stub that takes a `Path` and is a no-op until M7 replaces it with loguru setup.

---

## Task 8: Update `domain/__init__.py` re-exports

**Files:**
- Modify: `src/signal_copier/domain/__init__.py` (populate with re-exports)

- [ ] **Step 1: Read the current `domain/__init__.py`**

Open `src/signal_copier/domain/__init__.py`. It currently re-exports the M1 symbols (`FailureReason`, `ParseFailure`, `ParsedSignal`, `ParseResult`, `Signal`, `derive_signal_id`, `parse_signal`).

- [ ] **Step 2: Add the new M2 re-exports**

Edit `src/signal_copier/domain/__init__.py` to add the M2 imports. The complete updated file should look like:

```python
from signal_copier.domain.gale import (
    Stage,
    amount_for_stage,
    compute_gale_triggers,
)
from signal_copier.domain.signal import (
    FailureReason,
    ParseFailure,
    ParsedSignal,
    ParseResult,
    Signal,
    derive_signal_id,
    parse_signal,
)
from signal_copier.domain.state import (
    AllStates,
    ErrorReason,
    Event,
    FireEvent,
    ResultEvent,
    SignalState,
    StageResult,
    State,
    TerminalState,
    TransitionResult,
    transition,
)

__all__ = [
    # Gale
    "Stage",
    "amount_for_stage",
    "compute_gale_triggers",
    # Signal (M1)
    "FailureReason",
    "ParseFailure",
    "ParsedSignal",
    "ParseResult",
    "Signal",
    "derive_signal_id",
    "parse_signal",
    # State machine (M2)
    "AllStates",
    "ErrorReason",
    "Event",
    "FireEvent",
    "ResultEvent",
    "SignalState",
    "StageResult",
    "State",
    "TerminalState",
    "TransitionResult",
    "transition",
]
```

- [ ] **Step 3: Verify the public API is importable**

Run:
```bash
uv run python -c "
from signal_copier.domain import (
    Stage, amount_for_stage, compute_gale_triggers,
    FailureReason, ParseFailure, ParsedSignal, ParseResult, Signal,
    derive_signal_id, parse_signal,
    AllStates, ErrorReason, Event, FireEvent, ResultEvent, SignalState,
    StageResult, State, TerminalState, TransitionResult, transition,
)
print('OK: all 21 public symbols importable')
"
```

Expected: prints `OK: all 21 public symbols importable`. Exit 0.

- [ ] **Step 4: Verify all tests still pass (regression check)**

Run:
```bash
uv run pytest tests/ -v
```

Expected: All tests pass (1 M0 + 32 M1 + 11 gale + 38 state + 12 config = 94 total). Exit 0.

- [ ] **Step 5: Commit**

```bash
git add src/signal_copier/domain/__init__.py
git commit -m "Wire domain/__init__.py re-exports for M2 public API"
```

Commit message should mention: 14 new M2 symbols re-exported (`Stage`, `amount_for_stage`, `compute_gale_triggers`, `AllStates`, `ErrorReason`, `Event`, `FireEvent`, `ResultEvent`, `SignalState`, `StageResult`, `State`, `TerminalState`, `TransitionResult`, `transition`); M1's 7 re-exports unchanged; 21 total public symbols.

---

## Task 9: Update `__main__.py` to load config and log startup

**Files:**
- Modify: `src/signal_copier/__main__.py`

- [ ] **Step 1: Read the current `__main__.py`**

Open `src/signal_copier/__main__.py`. It currently has M0's stub: prints "signal_copier M0 scaffold: not implemented yet" and exits 0.

- [ ] **Step 2: Replace the contents**

Replace the entire file with these exact contents:

```python
from __future__ import annotations

import sys

from pydantic import ValidationError

from signal_copier.config import Config
from signal_copier.infra.log import setup_logging


def main() -> None:
    try:
        config = Config()
    except ValidationError as exc:
        # Demo-only guardrail (FR-6.6) and other validation errors land here.
        sys.stderr.write(f"❌ Config validation failed:\n{exc}\n")
        sys.exit(2)

    setup_logging(config.log_path)  # stub; replaced by M7

    print(
        f"🟢 signal_copier M2 started (config loaded)\n"
        f"   Mode: {'dry_run' if config.dry_run else 'live demo'}\n"
        f"   Timezone: {config.timezone}\n"
        f"   Amounts: initial=${config.amount_initial} "
        f"gale1=${config.amount_gale1} gale2=${config.amount_gale2}\n"
        f"   (state machine + gale math ready; broker/listener/scheduler pending M5+)"
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run the M2 startup**

Run:
```bash
uv run python -m signal_copier
```

Expected output:

```
🟢 signal_copier M2 started (config loaded)
   Mode: dry_run
   Timezone: America/Sao_Paulo
   Amounts: initial=$2.00 gale1=$4.00 gale2=$8.00
   (state machine + gale math ready; broker/listener/scheduler pending M5+)
```

Exit 0.

- [ ] **Step 4: Verify FR-6.6 demo-only guardrail fires on bad config**

Run (Windows PowerShell):
```powershell
$env:OLYMP_ACCOUNT_GROUP="real"; $env:DRY_RUN="false"; uv run python -m signal_copier; Remove-Item Env:OLYMP_ACCOUNT_GROUP; Remove-Item Env:DRY_RUN
```

Or on macOS/Linux:
```bash
OLYMP_ACCOUNT_GROUP=real DRY_RUN=false uv run python -m signal_copier
```

Expected: exits with code 2, prints to stderr:
```
❌ Config validation failed:
1 validation error for Config
  Value error, Refusing to start: OLYMP_ACCOUNT_GROUP=real requires DRY_RUN=true. Real-money trading is a v2 feature, gated behind a 7-day clean demo soak test. [type=value_error, input_value={'olymp_account_group': 'real', ...}, input_type=dict]
```

- [ ] **Step 5: Verify the console-script entry also works**

Run:
```bash
uv run signal-copier
```

Expected: same output as Step 3. Exit 0.

- [ ] **Step 6: Verify mypy on `__main__.py`**

Run:
```bash
uv run mypy src/signal_copier/__main__.py
```

Expected: `Success: no issues found in 1 source file`. Exit 0.

- [ ] **Step 7: Commit**

```bash
git add src/signal_copier/__main__.py
git commit -m "Wire __main__.py: load Config, log startup banner (M2)"
```

Commit message should mention: `Config()` instantiated with try/except around `ValidationError` (FR-6.6 demo-only guardrail surfaces as exit 2); `setup_logging` stub called (M7 replaces); startup banner includes mode, timezone, amounts.

---

## Task 10: Final verification (coverage, lint, mypy, regression)

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite with coverage on M2's new modules**

Run:
```bash
uv run pytest tests/ \
  --cov=signal_copier.config \
  --cov=signal_copier.domain.gale \
  --cov=signal_copier.domain.state \
  --cov-report=term-missing
```

Expected: prints test summary + coverage report. The three M2 modules show `100%`. Other files (M0/M1) may show `100%` too if exercised by tests. Exit 0.

If any of the three target modules is below 100%:
- Look at the missing lines in the term-missing report.
- Add a test that covers the line.
- Re-run.

- [ ] **Step 2: Run ruff lint**

Run:
```bash
uv run ruff check src/signal_copier tests
```

Expected: prints `All checks passed!` and exits 0. If you see violations (unused imports, missing newlines, etc.), stop and fix them.

Common fixes:
- Unused import: remove it.
- Missing newline: add blank line at end of file.
- Wrong import order: stdlib imports before third-party.

- [ ] **Step 3: Run ruff format check**

Run:
```bash
uv run ruff format --check src/signal_copier tests
```

Expected: prints nothing and exits 0. If it reports files needing reformatting:
```bash
uv run ruff format src/signal_copier tests
```
Re-run `--check` to confirm. If any files were reformatted, commit them:
```bash
git add -A
git commit -m "style: apply ruff format"
```

- [ ] **Step 4: Run mypy on the full src + tests**

Run:
```bash
uv run mypy src/signal_copier tests
```

Expected: `Success: no issues found in N source files` (N is the total file count). Exit 0.

If mypy reports issues:
- "Function is missing a return type annotation": add `-> None` to the function.
- "Argument 1 has incompatible type": check the `Literal["up", "down"]` annotation on `direction` in the `if/elif` branches.
- "Module not found": run `uv sync` to refresh the venv.

- [ ] **Step 5: Verify M0 still works (V-6, V-7)**

Run:
```bash
uv run pytest tests/test_main.py -v
```

Expected: PASS with `1 passed` (M0's stub test). Exit 0.

- [ ] **Step 6: Verify M1 still works (V-4)**

Run:
```bash
uv run pytest tests/test_parser.py -v
```

Expected: PASS with `32 passed`. Exit 0. (M2's `Signal` extension didn't break M1.)

- [ ] **Step 7: Run the consolidated verification (V-2)**

Run:
```bash
uv run pytest tests/ -v
```

Expected: PASS with `94 passed` (1 M0 + 32 M1 + 11 gale + 38 state + 12 config — exact count may vary by ±1 if the parametrized tests are counted differently). All V-1 through V-13 from the spec §9 pass.

- [ ] **Step 8: Run the demo**

Run:
```bash
uv run python -m signal_copier
```

Expected: prints the M2 startup banner, exits 0.

Then run the FR-6.6 guardrail test:
```bash
# Windows PowerShell:
$env:OLYMP_ACCOUNT_GROUP="real"; $env:DRY_RUN="false"; uv run python -m signal_copier; $?; Remove-Item Env:OLYMP_ACCOUNT_GROUP; Remove-Item Env:DRY_RUN
# macOS/Linux:
OLYMP_ACCOUNT_GROUP=real DRY_RUN=false uv run python -m signal_copier; echo "exit=$?"
```

Expected: exits 2 with the "Refusing to start" message.

- [ ] **Step 9: Verify Docker build still works (V-12, V-13)**

Run:
```bash
docker build -t signal-copier-m2 .
```

Expected: succeeds. The Dockerfile from M0 still works (no changes needed).

Run:
```bash
docker run --rm signal-copier-m2
```

Expected: prints the M2 startup banner, exits 0.

If you don't have Docker installed, skip this step. The M0 Dockerfile is unchanged, so M2's additions (`pydantic-settings`, `infra/`, new modules) all flow through `uv sync` in the Dockerfile.

- [ ] **Step 10: Final commit (if anything was reformatted or fixed)**

If Steps 2-4 required any code changes, commit them. Otherwise skip.

```bash
git status
# If clean, no commit needed. If dirty:
git add -A
git commit -m "M2: final verification fixes (lint/format/mypy)"
```

---

## Final summary

After completing all tasks, M2 is done. The full set of M2 deliverables:

**New files (committed):**
- `src/signal_copier/config.py` (pydantic-settings, ~80 LOC)
- `src/signal_copier/domain/gale.py` (gale math, ~40 LOC)
- `src/signal_copier/domain/state.py` (state machine, ~280 LOC)
- `src/signal_copier/infra/__init__.py` (empty package marker)
- `src/signal_copier/infra/log.py` (stub, replaced by M7)
- `tests/test_config.py` (12 tests)
- `tests/test_gale_math.py` (9 functions, 11 tests with param)
- `tests/test_state_machine.py` (36 functions, 38 tests with param)

**Files NOT modified (intentional, in addition to the M2-scope list):** `src/olymptrade_ws/`, `Dockerfile`, `railway.toml`, `.dockerignore`, `.python-version`, `.gitignore`, `README.md`, `docs/PRD.md`, `docs/tool-idea.md`, `.env.example`, `.pre-commit-config.yaml`, `src/signal_copier/__init__.py` (M0's empty marker stays empty).

**Expected git log after M2:**
```
<this commit>  M2: final verification fixes (if any)
<this commit>  Wire __main__.py: load Config, log startup banner (M2)
<this commit>  Wire domain/__init__.py re-exports for M2 public API
<this commit>  Add infra/ package + setup_logging stub (M2, replaced by M7)
<this commit>  Add transition() + helpers: time-window checks at FireEvent + ResultEvent (M2 T6)
<this commit>  Add state machine types + SignalState.from_signal (M2 T5)
<this commit>  Add trigger_unix_* fields to Signal dataclass (M2 D-5)
<this commit>  Add gale math: amount_for_stage + compute_gale_triggers (M2)
<this commit>  Add Config (pydantic-settings) with demo-only guardrail (M2)
<this commit>  Add pydantic-settings runtime dep (M2)
```

**Verifications summary (all should pass per spec §9 V-1 through V-13):**
- V-1: `uv lock && uv sync` — succeeds; pydantic-settings installed
- V-2: `uv run pytest tests/ -v` — 94 tests pass
- V-3: 100% line + branch coverage on config.py, gale.py, state.py
- V-4: M1's 32 tests still pass
- V-5: M0's stub test still passes
- V-6: `uv run ruff check src/signal_copier tests` — all clean
- V-7: `uv run ruff format --check src/signal_copier tests` — clean
- V-8: `uv run mypy src/signal_copier tests` — success
- V-9: `uv run python -m signal_copier` — M2 banner, exit 0
- V-10: With `OLYMP_ACCOUNT_GROUP=real` + `DRY_RUN=false` — exit 2 with guardrail
- V-11: `uv run signal-copier` — same as V-9
- V-12: `docker build -t signal-copier-m2 .` — succeeds
- V-13: `docker run --rm signal-copier-m2` — M2 banner, exit 0

**What's next:** M3 — `broker/dry_run.py` + `Broker` Protocol (PRD §15 M3 row). M3 imports `Stage`, `amount_for_stage` from `signal_copier.domain.gale` and `Config` from `signal_copier.config`. The state machine is fully wired and ready for the broker layer to dispatch `ResultEvent` callbacks.
