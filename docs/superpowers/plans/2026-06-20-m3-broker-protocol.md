# M3 — Broker Protocol & DryRunBroker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the M3 broker abstraction layer — a `Broker` Protocol with 4 async methods (`connect` / `place` / `wait_result` / `close`), the `UnsupportedPairError` exception, and a `DryRunBroker` implementation with a pluggable async outcome provider (default always-win) that logs intended trades without ever touching a real broker. No new dependencies (stdlib only); 100% line + branch coverage on `broker/base.py` and `broker/dry_run.py`.

**Architecture:** Two new files under a new `src/signal_copier/broker/` package: `base.py` defines the `Broker` Protocol and `UnsupportedPairError`; `dry_run.py` defines `DryRunBroker` as a `@dataclass(slots=True)` (not frozen) holding an internal `_placed: dict[str, tuple[Signal, Stage]]` that `wait_result` pops from so the dict stays bounded. Trade-ids are encoded `dryrun-{signal_id}-{stage}-{uuid4hex[:8]}` so DB rows are human-greppable. Logging uses stdlib `logging.getLogger(__name__)`. Re-export `Broker` and `UnsupportedPairError` at both `signal_copier.broker` (canonical) and `signal_copier` (top-level convenience).

**Tech Stack:** Python 3.13+ stdlib (`logging`, `dataclasses`, `decimal`, `uuid`, `typing.Protocol`, `collections.abc`). No new runtime deps. No new dev deps. `pytest` + `pytest-asyncio` (already in dev deps, `asyncio_mode = "auto"` already configured in M0). `ruff` + `mypy --strict` (configured).

**Spec reference:** `docs/superpowers/specs/2026-06-20-m3-broker-protocol-design.md`

---

## How to use this plan

1. Work through tasks in order. Each task is self-contained but builds on the previous.
2. Every step has explicit commands with expected output.
3. After every code change, run the verification step before committing.
4. If a verification fails, STOP. Read the error. Fix the issue. Re-run. Do not proceed to the next step until the current step's verification passes.
5. Commit frequently — one commit per task is the minimum.
6. Coverage check at the end is mandatory.
7. **Pre-commit hook:** the repo has `.pre-commit-config.yaml` with `ruff-format` + `ruff --fix`. Commits may auto-format files. If a `git commit` fails because pre-commit reformatted your code, re-stage (`git add`) and commit again with `--no-verify` only if the auto-format is intentional.

**Working directory:** all commands assume you are at the project root (`olymptrade/`).

**Platform notes:** Use `uv run pytest ...` and `uv run mypy ...` to run inside the venv. PowerShell syntax on Windows; bash on macOS/Linux. File paths use forward slashes (works in both shells).

**TDD order rationale:** Protocol first (foundational — defines what conformance means). Then `DryRunBroker` skeleton (Protocol conformance proves the shape). Then lifecycle (`connect` / `close`), then `place` (returns trade_id, formats it, logs it), then `wait_result` (default win, custom provider, dict-bounded, unknown-id). Top-level re-exports last. Final task verifies the entire Definition of Done from the spec §10.

---

## File Map

| File | Type | Created in | Purpose |
|---|---|---|---|
| `src/signal_copier/broker/__init__.py` | NEW | T1 | Empty package marker + re-export `Broker`, `UnsupportedPairError` |
| `src/signal_copier/broker/base.py` | NEW | T1 | `Broker` Protocol + `UnsupportedPairError` exception |
| `src/signal_copier/broker/dry_run.py` | NEW | T2, T3, T4, T5, T6, T7 | `DryRunBroker` dataclass with 4 async methods + `_placed` dict |
| `tests/test_broker_protocol.py` | NEW | T1, T2, T8 | Protocol conformance + exception tests (~6 tests) |
| `tests/test_dry_run_broker.py` | NEW | T3, T4, T5, T6, T7 | DryRunBroker behavior tests (~12 tests) |
| `src/signal_copier/__init__.py` | MODIFY | T8 | Re-export `Broker`, `UnsupportedPairError` at top level |
| `pyproject.toml`, `uv.lock` | (unchanged) | — | No new deps for M3 |
| `docs/PRD.md`, `docs/superpowers/specs/2026-06-20-m3-broker-protocol-design.md` | (unchanged) | — | Reference only |

**Out of scope for M3 (do not create or modify):** `broker/olymp.py` (M8), `infra/log.py` (M7), `__main__.py`, `config.py`, `domain/`, `tests/test_main.py`, `tests/test_parser.py`, `tests/test_gale_math.py`, `tests/test_state_machine.py`, `tests/test_config.py`, `Dockerfile`, `railway.toml`, `.dockerignore`, `.python-version`, `.env.example`, `.pre-commit-config.yaml`, `src/olymptrade_ws/`, `OlympTradeAPI/`, `.gitignore`, `README.md`, `docs/PRD.md`, `docs/tool-idea.md`, `migrations/`, `docs/superpowers/specs/2026-06-20-m3-broker-protocol-design.md`.

---

## Task 1: Create broker package + Broker Protocol + UnsupportedPairError

**Files:**
- Create: `src/signal_copier/broker/__init__.py`
- Create: `src/signal_copier/broker/base.py`
- Create: `tests/test_broker_protocol.py`

- [ ] **Step 1: Write the failing test for `UnsupportedPairError`**

Create `tests/test_broker_protocol.py`:

```python
from __future__ import annotations

from signal_copier.broker import Broker, UnsupportedPairError


def test_unsupported_pair_error_is_exception() -> None:
    assert issubclass(UnsupportedPairError, Exception)


def test_unsupported_pair_error_has_meaningful_message() -> None:
    err = UnsupportedPairError("USD/EGP not available")
    assert "USD/EGP" in str(err)


def test_broker_protocol_is_importable() -> None:
    # Protocol type exists and is a Protocol.
    assert Broker is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
uv run pytest tests/test_broker_protocol.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'signal_copier.broker'`.

- [ ] **Step 3: Create empty broker package marker**

Create `src/signal_copier/broker/__init__.py`:

```python
"""Broker abstraction layer.

Provides the Broker Protocol (M3) and concrete implementations:
  - DryRunBroker      (M3, default for v1)
  - OlympTradeBroker  (M8, wraps vendored olymptrade_ws)
"""
```

- [ ] **Step 4: Create `broker/base.py` with `UnsupportedPairError` and `Broker` Protocol**

Create `src/signal_copier/broker/base.py`:

```python
from __future__ import annotations

from decimal import Decimal
from typing import Protocol, runtime_checkable

from signal_copier.domain.gale import Stage
from signal_copier.domain.signal import Signal
from signal_copier.domain.state import StageResult


class UnsupportedPairError(Exception):
    """Raised by Broker.place() when the signal's pair is not available on the broker.

    The state machine catches this and marks the signal status='error'
    with error_reason='unsupported_pair' (PRD §10). M8's OlympTradeBroker
    is the canonical raiser; M3's DryRunBroker never raises this.
    """


@runtime_checkable
class Broker(Protocol):
    """Broker-agnostic trading surface used by the scheduler (M6) and state
    machine (M2). Two concrete implementations exist in v1:

      - DryRunBroker       (M3, default for v1, FR-6.5: DRY_RUN=true)
      - OlympTradeBroker   (M8, wraps the vendored olymptrade_ws client)

    All methods are async because real brokers are I/O-bound (M8). M3's
    DryRunBroker is also async to keep the Protocol uniform; tests use
    pytest-asyncio (asyncio_mode="auto", already configured in M0).
    """

    async def connect(self) -> None:
        """Establish any required connection (Telethon session, WS handshake,
        asset-map fetch). Idempotent — second call is a no-op."""

    async def place(
        self,
        signal: Signal,
        *,
        stage: Stage,
        amount: Decimal,
    ) -> str:
        """Submit a trade for `signal` at `stage` for `amount` USD.

        Returns the broker's trade_id, which the scheduler uses to identify
        the trade in `wait_result` and which M6 persists as `stages.trade_id`.

        Raises UnsupportedPairError if `signal.pair` is not available on this
        broker. The state machine catches this and ends the cascade with
        status='error' (PRD §10).
        """

    async def wait_result(
        self,
        trade_id: str,
        *,
        timeout: float,
    ) -> StageResult:
        """Block until the broker reports a terminal result for `trade_id`,
        or until `timeout` seconds elapse.

        Returns one of M2's StageResult literals: 'win' | 'loss' | 'tie'
        | 'timeout' | 'error'. The 'timeout' literal here means the
        *broker-reporting* timeout — distinct from the per-stage
        expiration-grace timeout in PRD FR-5.3, which M6 owns.
        """

    async def close(self) -> None:
        """Tear down any connection. Idempotent. Called on shutdown and on
        unhandled broker errors so M6 can reconnect cleanly."""
```

- [ ] **Step 5: Update `broker/__init__.py` to re-export both names**

Modify `src/signal_copier/broker/__init__.py` (replace the docstring-only version):

```python
"""Broker abstraction layer.

Provides the Broker Protocol (M3) and concrete implementations:
  - DryRunBroker      (M3, default for v1)
  - OlympTradeBroker  (M8, wraps vendored olymptrade_ws)
"""

from signal_copier.broker.base import Broker, UnsupportedPairError

__all__ = ["Broker", "UnsupportedPairError"]
```

- [ ] **Step 6: Run test to verify it passes**

Run:
```bash
uv run pytest tests/test_broker_protocol.py -v
```

Expected: PASS — 3 tests passed.

- [ ] **Step 7: Run mypy to verify strict typing**

Run:
```bash
uv run mypy src/signal_copier/broker/
```

Expected: `Success: no issues found in 2 source files`. Exit 0.

- [ ] **Step 8: Run ruff to verify lint + format**

Run:
```bash
uv run ruff check src/signal_copier/broker/ tests/test_broker_protocol.py
uv run ruff format --check src/signal_copier/broker/ tests/test_broker_protocol.py
```

Expected: both commands exit 0. If `ruff format` reports files needing reformat, run `uv run ruff format src/signal_copier/broker/ tests/test_broker_protocol.py` and re-verify.

- [ ] **Step 9: Verify regression — M0/M1/M2 tests still pass**

Run:
```bash
uv run pytest tests/test_main.py tests/test_parser.py tests/test_gale_math.py tests/test_state_machine.py tests/test_config.py -v
```

Expected: all previously passing tests still pass (no regressions).

- [ ] **Step 10: Commit**

```bash
git add src/signal_copier/broker/__init__.py src/signal_copier/broker/base.py tests/test_broker_protocol.py
git commit -m "M3 T1: scaffold broker package + Broker Protocol + UnsupportedPairError"
```

If pre-commit auto-formats, re-run `git add` for the changed files and commit again.

---

## Task 2: Stub DryRunBroker satisfying the Protocol

**Files:**
- Create: `src/signal_copier/broker/dry_run.py`
- Modify: `tests/test_broker_protocol.py` (add Protocol conformance tests)

- [ ] **Step 1: Add failing Protocol conformance tests**

Replace the existing imports at the top of `tests/test_broker_protocol.py` with the imports below (adds `typing`, `Broker` alias, and `DryRunBroker`), then append the 4 conformance tests:

> **Note on `typing.get_type_hints`:** With `from __future__ import annotations`, all annotations become strings at runtime. `inspect.signature(...).parameters["amount"].annotation` therefore returns the string `'Decimal'`, not the class. We use `typing.get_type_hints()` to resolve back to actual types. The keyword-only check uses `Parameter.kind` which still works on string-form annotations.

```python
from __future__ import annotations

import inspect
import typing
from decimal import Decimal
from inspect import Parameter

from signal_copier.broker.base import Broker as BrokerCanonical
from signal_copier.broker.dry_run import DryRunBroker
```


def test_dry_run_broker_satisfies_protocol() -> None:
    assert isinstance(DryRunBroker(), Broker)


def test_dry_run_broker_satisfies_canonical_protocol_path() -> None:
    # Both import paths resolve to the same Protocol object.
    assert Broker is BrokerCanonical


def test_place_signature_accepts_decimal_amount() -> None:
    # typing.get_type_hints resolves PEP 563 string annotations back to actual
    # types (works correctly with `from __future__ import annotations`).
    hints = typing.get_type_hints(DryRunBroker.place)
    assert hints["amount"] is Decimal


def test_place_signature_keyword_only_stage_and_amount() -> None:
    sig = inspect.signature(DryRunBroker.place)
    assert sig.parameters["stage"].kind == Parameter.KEYWORD_ONLY
    assert sig.parameters["amount"].kind == Parameter.KEYWORD_ONLY
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
uv run pytest tests/test_broker_protocol.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'signal_copier.broker.dry_run'`.

- [ ] **Step 3: Create minimal `DryRunBroker` skeleton**

Create `src/signal_copier/broker/dry_run.py`:

```python
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import ClassVar

from signal_copier.broker.base import Broker
from signal_copier.domain.gale import Stage
from signal_copier.domain.signal import Signal
from signal_copier.domain.state import StageResult


# OutcomeProvider is async so M8's real broker (or future tests needing IO)
# can be a drop-in. The default and most test providers are sync internally;
# they still need `async def` to match this signature.
OutcomeProvider = Callable[[Signal, Stage], Awaitable[StageResult]]


async def _default_outcome(signal: Signal, stage: Stage) -> StageResult:
    """Default outcome provider: every trade wins.

    Matches the analyst's signal strategy in real-world conditions
    (90%+ of signals hit before gale2). M9 soak uses this default.
    """
    _ = signal, stage
    return "win"


@dataclass(slots=True)
class DryRunBroker:
    """Logs intended trades and returns a configurable outcome without ever
    touching a real broker. Default for v1 (FR-6.5: DRY_RUN=true).

    Not frozen: holds an internal _placed dict mapping trade_id to
    (signal, stage) so wait_result can call outcome_provider(signal, stage).
    The dict is bounded — wait_result pops its entry, so growth is
    O(in-flight trades), not O(all-time trades).
    """

    outcome_provider: OutcomeProvider = _default_outcome
    account_group: str = "demo"  # informational only; M2's Config guardrail
                                  # is the authoritative enforcement
    _placed: dict[str, tuple[Signal, Stage]] = field(
        default_factory=dict, init=False, repr=False,
    )

    _PREFIX: ClassVar[str] = "dryrun"  # trade_id prefix; makes DB rows identifiable

    async def connect(self) -> None:
        # Implementation lands in Task 3.
        pass

    async def place(
        self,
        signal: Signal,
        *,
        stage: Stage,
        amount: Decimal,
    ) -> str:
        # Implementation lands in Task 4.
        _ = signal, stage, amount
        return ""

    async def wait_result(
        self,
        trade_id: str,
        *,
        timeout: float,
    ) -> StageResult:
        # Implementation lands in Tasks 5-7.
        _ = trade_id, timeout
        return "win"

    async def close(self) -> None:
        # Implementation lands in Task 3.
        pass
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
uv run pytest tests/test_broker_protocol.py -v
```

Expected: PASS — 7 tests passed (3 from T1 + 4 from T2).

- [ ] **Step 5: Run mypy**

Run:
```bash
uv run mypy src/signal_copier/broker/
```

Expected: `Success: no issues found in 3 source files`. Exit 0.

- [ ] **Step 6: Run ruff**

Run:
```bash
uv run ruff check src/signal_copier/broker/ tests/test_broker_protocol.py
uv run ruff format --check src/signal_copier/broker/ tests/test_broker_protocol.py
```

Expected: both exit 0. Reformat if needed.

- [ ] **Step 7: Commit**

```bash
git add src/signal_copier/broker/dry_run.py tests/test_broker_protocol.py
git commit -m "M3 T2: stub DryRunBroker satisfying the Broker Protocol"
```

---

## Task 3: connect() and close() lifecycle with logging

**Files:**
- Modify: `src/signal_copier/broker/dry_run.py:55-72` (replace stubs for `connect` and `close`)
- Create: `tests/test_dry_run_broker.py`

- [ ] **Step 1: Create `test_dry_run_broker.py` with failing lifecycle tests**

Create `tests/test_dry_run_broker.py`:

```python
from __future__ import annotations

import logging

import pytest

from signal_copier.broker.dry_run import DryRunBroker


async def test_connect_logs_and_is_idempotent() -> None:
    broker = DryRunBroker()
    await broker.connect()
    await broker.connect()  # second call must not raise


async def test_close_is_idempotent() -> None:
    broker = DryRunBroker()
    await broker.close()
    await broker.close()  # second call must not raise


async def test_account_group_logged_on_connect(caplog: pytest.LogCaptureFixture) -> None:
    broker = DryRunBroker(account_group="demo")
    with caplog.at_level(logging.INFO):
        await broker.connect()
    assert any(
        "account_group=demo" in record.message
        for record in caplog.records
    )


async def test_default_account_group_is_demo(caplog: pytest.LogCaptureFixture) -> None:
    # The default constructor argument is "demo" — confirms the field default.
    broker = DryRunBroker()
    with caplog.at_level(logging.INFO):
        await broker.connect()
    assert any(
        "account_group=demo" in record.message
        for record in caplog.records
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
uv run pytest tests/test_dry_run_broker.py -v
```

Expected: at least one test FAILs. Specifically `test_account_group_logged_on_connect` will FAIL because the stub `connect()` doesn't log anything.

- [ ] **Step 3: Add module-level logger to `dry_run.py`**

Add at the top of `src/signal_copier/broker/dry_run.py` (just below the imports, before `OutcomeProvider`):

```python
_log = logging.getLogger(__name__)
```

Also add `import logging` at the top of the file's import block.

- [ ] **Step 4: Replace `connect()` and `close()` stubs with real implementations**

In `src/signal_copier/broker/dry_run.py`, replace the `connect()` and `close()` methods (currently `pass`) with:

```python
    async def connect(self) -> None:
        _log.info(
            "DryRunBroker connected (account_group=%s)", self.account_group,
        )

    async def close(self) -> None:
        _log.info("DryRunBroker closed")
```

(The `place()` and `wait_result()` stubs from T2 remain in place; they get replaced in T4-T7.)

- [ ] **Step 5: Run test to verify it passes**

Run:
```bash
uv run pytest tests/test_dry_run_broker.py -v
```

Expected: PASS — 4 tests passed.

- [ ] **Step 6: Run mypy + ruff**

Run:
```bash
uv run mypy src/signal_copier/broker/
uv run ruff check src/signal_copier/broker/ tests/test_dry_run_broker.py
uv run ruff format --check src/signal_copier/broker/ tests/test_dry_run_broker.py
```

Expected: all exit 0.

- [ ] **Step 7: Commit**

```bash
git add src/signal_copier/broker/dry_run.py tests/test_dry_run_broker.py
git commit -m "M3 T3: connect() and close() lifecycle with account_group logging"
```

---

## Task 4: place() returns trade_id with correct format and structured logging

**Files:**
- Modify: `src/signal_copier/broker/dry_run.py` (replace `place()` stub)
- Modify: `tests/test_dry_run_broker.py` (add 3 place() tests)

- [ ] **Step 1: Add a `_signal()` test helper at the top of `test_dry_run_broker.py`**

First, update the imports at the top of `tests/test_dry_run_broker.py` to add `Decimal` and `Signal`:

```python
from __future__ import annotations

import logging
from decimal import Decimal

import pytest

from signal_copier.broker.dry_run import DryRunBroker
from signal_copier.domain.signal import Signal
```

(Replace the existing import block — the additions are `from decimal import Decimal` and `from signal_copier.domain.signal import Signal`.)

Then, insert this helper just below the imports and above the first existing test (`test_connect_logs_and_is_idempotent`):

```python
def _signal(signal_id: str = "abc123def456") -> Signal:
    """Factory for a minimal valid Signal used across dry-run broker tests.

    All numeric fields use round numbers so tests are easy to read. The
    trigger_unix_* fields are pre-computed per M2's contract (see M2 spec D-5).
    """
    return Signal(
        signal_id=signal_id,
        pair="EUR/JPY",
        direction="down",
        trigger_hhmm="10:20",
        expiration_seconds=300,
        received_at_unix=1_700_000_000.0,
        source_message_id=1,
        source_chat_id=1,
        raw_text="EUR/JPY;10:20;PUT🟥",
        trigger_unix_initial=1_700_000_000.0,
        trigger_unix_gale1=1_700_000_300.0,
        trigger_unix_gale2=1_700_000_600.0,
    )
```

- [ ] **Step 2: Add the 3 failing `place()` tests**

Append these tests to `tests/test_dry_run_broker.py`:

```python
async def test_place_returns_string_trade_id() -> None:
    broker = DryRunBroker()
    sig = _signal()
    trade_id = await broker.place(
        sig, stage="initial", amount=Decimal("2.00"),
    )
    assert isinstance(trade_id, str)
    assert len(trade_id) > 0


async def test_place_trade_id_has_dryrun_prefix_and_signal_id() -> None:
    broker = DryRunBroker()
    sig = _signal(signal_id="a1b2c3d4e5f6")
    trade_id = await broker.place(
        sig, stage="initial", amount=Decimal("2.00"),
    )
    assert trade_id.startswith("dryrun-a1b2c3d4e5f6-initial-")


async def test_place_logs_intended_trade(
    caplog: pytest.LogCaptureFixture,
) -> None:
    broker = DryRunBroker()
    sig = _signal()
    with caplog.at_level(logging.INFO):
        trade_id = await broker.place(
            sig, stage="initial", amount=Decimal("2.00"),
        )
    assert any(
        "DRY-RUN place" in record.message
        and "EUR/JPY" in record.message
        and trade_id in record.message
        for record in caplog.records
    )
```

- [ ] **Step 3: Run test to verify the new tests fail**

Run:
```bash
uv run pytest tests/test_dry_run_broker.py -v
```

Expected: the 3 new tests FAIL. The current `place()` stub returns `""`, which fails `test_place_returns_string_trade_id` (length 0) and `test_place_trade_id_has_dryrun_prefix_and_signal_id` (doesn't start with `dryrun-`).

- [ ] **Step 4: Replace `place()` stub with real implementation**

In `src/signal_copier/broker/dry_run.py`, replace the current `place()` method (which returns `""`) with:

```python
    async def place(
        self,
        signal: Signal,
        *,
        stage: Stage,
        amount: Decimal,
    ) -> str:
        trade_id = (
            f"{self._PREFIX}-{signal.signal_id}-{stage}-{uuid4().hex[:8]}"
        )
        self._placed[trade_id] = (signal, stage)
        _log.info(
            "DRY-RUN place: pair=%s direction=%s stage=%s "
            "amount=%s signal_id=%s trade_id=%s",
            signal.pair, signal.direction, stage, amount,
            signal.signal_id, trade_id,
        )
        return trade_id
```

Add `from uuid import uuid4` to the imports at the top of `dry_run.py`.

- [ ] **Step 5: Run test to verify it passes**

Run:
```bash
uv run pytest tests/test_dry_run_broker.py -v
```

Expected: PASS — 7 tests passed (4 from T3 + 3 from T4).

- [ ] **Step 6: Run mypy + ruff**

Run:
```bash
uv run mypy src/signal_copier/broker/
uv run ruff check src/signal_copier/broker/ tests/test_dry_run_broker.py
uv run ruff format --check src/signal_copier/broker/ tests/test_dry_run_broker.py
```

Expected: all exit 0.

- [ ] **Step 7: Commit**

```bash
git add src/signal_copier/broker/dry_run.py tests/test_dry_run_broker.py
git commit -m "M3 T4: place() returns dryrun-prefixed trade_id with structured logging"
```

---

## Task 5: wait_result() with default and custom outcome providers

**Files:**
- Modify: `src/signal_copier/broker/dry_run.py` (replace `wait_result()` stub)
- Modify: `tests/test_dry_run_broker.py` (add 3 wait_result tests)

- [ ] **Step 1: Add the failing `wait_result()` tests**

Append these tests to `tests/test_dry_run_broker.py`:

```python
from signal_copier.domain.gale import Stage
from signal_copier.domain.state import StageResult


async def test_wait_result_default_returns_win() -> None:
    broker = DryRunBroker()
    sig = _signal()
    for stage in ("initial", "gale1", "gale2"):
        tid = await broker.place(
            sig, stage=stage, amount=Decimal("2.00"),
        )
        result = await broker.wait_result(tid, timeout=330.0)
        assert result == "win"


async def test_wait_result_uses_custom_provider() -> None:
    async def loss_all(s: Signal, st: Stage) -> StageResult:
        return "loss"

    broker = DryRunBroker(outcome_provider=loss_all)
    sig = _signal()
    tid = await broker.place(
        sig, stage="initial", amount=Decimal("2.00"),
    )
    result = await broker.wait_result(tid, timeout=330.0)
    assert result == "loss"


async def test_wait_result_provider_receives_signal_and_stage() -> None:
    captured: list[tuple[Signal, Stage]] = []

    async def capture(s: Signal, st: Stage) -> StageResult:
        captured.append((s, st))
        return "win"

    broker = DryRunBroker(outcome_provider=capture)
    sig = _signal()
    tid = await broker.place(
        sig, stage="gale1", amount=Decimal("4.00"),
    )
    await broker.wait_result(tid, timeout=330.0)
    assert len(captured) == 1
    assert captured[0][0] is sig
    assert captured[0][1] == "gale1"
```

Note: the `Stage` and `StageResult` imports must be added at the top of `tests/test_dry_run_broker.py` (alongside the existing imports). Add them just below the `from signal_copier.broker.dry_run import DryRunBroker` import:

```python
from signal_copier.broker.dry_run import DryRunBroker
from signal_copier.domain.gale import Stage
from signal_copier.domain.signal import Signal
from signal_copier.domain.state import StageResult
```

(Since T4 added `from signal_copier.domain.signal import Signal`, just add the two missing lines.)

- [ ] **Step 2: Run test to verify the new tests fail**

Run:
```bash
uv run pytest tests/test_dry_run_broker.py::test_wait_result_uses_custom_provider tests/test_dry_run_broker.py::test_wait_result_provider_receives_signal_and_stage -v
```

Expected: 2 tests FAIL. The current `wait_result()` stub returns `"win"` regardless of `outcome_provider` and never invokes it with `(signal, stage)`. (`test_wait_result_default_returns_win` would PASS even with the stub, since the stub also returns `"win"`, so it does not appear in this RED step — it's covered by the GREEN step.)

- [ ] **Step 3: Replace `wait_result()` stub with real implementation**

In `src/signal_copier/broker/dry_run.py`, replace the current `wait_result()` method (which returns `"win"` and ignores inputs) with:

```python
    async def wait_result(
        self,
        trade_id: str,
        *,
        timeout: float,  # noqa: ARG002 — dry-run ignores timeout (D-7)
    ) -> StageResult:
        _log.info("DRY-RUN wait_result: trade_id=%s (instant)", trade_id)
        try:
            signal, stage = self._placed.pop(trade_id)
        except KeyError:
            # Defensive: unknown trade_id means a caller bug. M6 is the only
            # caller; this should never fire in production. Surface it as
            # 'error' so the state machine ends the cascade cleanly.
            _log.warning(
                "DRY-RUN wait_result: unknown trade_id=%s; returning 'error'",
                trade_id,
            )
            return "error"
        return await self.outcome_provider(signal, stage)
```

(The `try/except KeyError` is part of T7; it's harmless to include here since T7 just adds the test for it.)

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
uv run pytest tests/test_dry_run_broker.py -v
```

Expected: PASS — 10 tests passed (7 from T3-T4 + 3 from T5).

- [ ] **Step 5: Run mypy + ruff**

Run:
```bash
uv run mypy src/signal_copier/broker/
uv run ruff check src/signal_copier/broker/ tests/test_dry_run_broker.py
uv run ruff format --check src/signal_copier/broker/ tests/test_dry_run_broker.py
```

Expected: all exit 0.

- [ ] **Step 6: Commit**

```bash
git add src/signal_copier/broker/dry_run.py tests/test_dry_run_broker.py
git commit -m "M3 T5: wait_result() with default and custom outcome providers"
```

---

## Task 6: _placed dict is bounded; multiple in-flight places don't collide

**Files:**
- Modify: `tests/test_dry_run_broker.py` (add 2 tests)
- (No source changes — the `dict.pop` + `uuid4()` semantics were already established in T4-T5.)

- [ ] **Step 1: Add the failing tests**

Append these tests to `tests/test_dry_run_broker.py`:

```python
async def test_place_then_wait_pops_trade_id_dict() -> None:
    broker = DryRunBroker()
    sig = _signal()
    tid = await broker.place(
        sig, stage="initial", amount=Decimal("2.00"),
    )
    assert tid in broker._placed
    await broker.wait_result(tid, timeout=330.0)
    assert tid not in broker._placed


async def test_multiple_in_flight_places_do_not_collide() -> None:
    broker = DryRunBroker()
    sig = _signal()
    tid1 = await broker.place(
        sig, stage="initial", amount=Decimal("2.00"),
    )
    tid2 = await broker.place(
        sig, stage="gale1", amount=Decimal("4.00"),
    )
    assert tid1 != tid2
    assert await broker.wait_result(tid1, timeout=330.0) == "win"
    assert await broker.wait_result(tid2, timeout=330.0) == "win"
    assert broker._placed == {}  # both popped
```

- [ ] **Step 2: Run test to verify it passes immediately**

Run:
```bash
uv run pytest tests/test_dry_run_broker.py::test_place_then_wait_pops_trade_id_dict tests/test_dry_run_broker.py::test_multiple_in_flight_places_do_not_collide -v
```

Expected: PASS — both tests pass without any source changes (the `_placed.pop` was implemented in T4-T5). This step documents the dict-bounded contract via tests rather than introducing new behavior. The tests serve as a regression guard: if a future refactor changes `pop` to `__getitem__` (read-only access) or removes the `uuid4` suffix, these tests will FAIL.

- [ ] **Step 3: Run mypy + ruff**

Run:
```bash
uv run mypy src/signal_copier/broker/
uv run ruff check tests/test_dry_run_broker.py
uv run ruff format --check tests/test_dry_run_broker.py
```

Expected: all exit 0.

- [ ] **Step 4: Commit**

```bash
git add tests/test_dry_run_broker.py
git commit -m "M3 T6: regression tests for _placed dict bounded semantics + uuid4 uniqueness"
```

---

## Task 7: wait_result() unknown trade_id returns "error"

**Files:**
- Modify: `tests/test_dry_run_broker.py` (add 1 test)
- (No source changes — the `try/except KeyError → "error"` was already implemented in T5.)

- [ ] **Step 1: Add the failing test**

Append this test to `tests/test_dry_run_broker.py`:

```python
async def test_wait_result_unknown_trade_id_returns_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    broker = DryRunBroker()
    with caplog.at_level(logging.WARNING):
        result = await broker.wait_result("unknown-id", timeout=330.0)
    assert result == "error"
    assert any(
        "unknown trade_id" in record.message
        for record in caplog.records
    )
```

- [ ] **Step 2: Run test to verify it passes immediately**

Run:
```bash
uv run pytest tests/test_dry_run_broker.py::test_wait_result_unknown_trade_id_returns_error -v
```

Expected: PASS — the test passes because the `try/except KeyError` was implemented in T5. This step documents the defensive behavior via a test: if a future refactor removes the `KeyError` handler (e.g., switches to `dict[trade_id]` which raises `KeyError` uncaught), the test will FAIL and surface the regression.

- [ ] **Step 3: Commit**

```bash
git add tests/test_dry_run_broker.py
git commit -m "M3 T7: regression test for wait_result() defensive 'error' on unknown trade_id"
```

---

## Task 8: Top-level re-exports (signal_copier.broker → signal_copier)

**Files:**
- Modify: `src/signal_copier/__init__.py` (currently empty)
- Modify: `tests/test_broker_protocol.py` (add 1 test)

- [ ] **Step 1: Add the failing test for top-level re-export**

Append this test to `tests/test_broker_protocol.py`:

```python
def test_broker_importable_from_top_level() -> None:
    from signal_copier import Broker as TopLevelBroker
    assert TopLevelBroker is Broker


def test_unsupported_pair_error_importable_from_top_level() -> None:
    from signal_copier import UnsupportedPairError as TopLevel
    assert TopLevel is UnsupportedPairError
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
uv run pytest tests/test_broker_protocol.py::test_broker_importable_from_top_level tests/test_broker_protocol.py::test_unsupported_pair_error_importable_from_top_level -v
```

Expected: FAIL with `ImportError: cannot import name 'Broker' from 'signal_copier'` (the current top-level `__init__.py` is empty).

- [ ] **Step 3: Add top-level re-exports to `signal_copier/__init__.py`**

Modify `src/signal_copier/__init__.py` (replace the empty file) with:

```python
"""signal_copier — Telegram → OlympTrade signal copier (demo only, v1).

Top-level convenience re-exports. The canonical import path is the
submodule (e.g., `from signal_copier.broker import Broker`); the
top-level path (`from signal_copier import Broker`) is provided as a
shorthand for callers that prefer it.
"""

from signal_copier.broker.base import Broker, UnsupportedPairError

__all__ = ["Broker", "UnsupportedPairError"]
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
uv run pytest tests/test_broker_protocol.py -v
```

Expected: PASS — 9 tests passed (7 from T1-T2 + 2 from T8).

- [ ] **Step 5: Run mypy + ruff**

Run:
```bash
uv run mypy src/signal_copier/
uv run ruff check src/signal_copier/__init__.py
uv run ruff format --check src/signal_copier/__init__.py
```

Expected: all exit 0.

- [ ] **Step 6: Verify M0/M1/M2 regression — `test_main.py` still passes**

`tests/test_main.py` does `from signal_copier.__main__ import main` — confirm this still works (it should, since `__main__.py` is unchanged):

Run:
```bash
uv run pytest tests/test_main.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/signal_copier/__init__.py tests/test_broker_protocol.py
git commit -m "M3 T8: top-level re-exports for Broker and UnsupportedPairError"
```

---

## Task 9: Final verification (lint + mypy + coverage + smoke test)

**Files:**
- (No source or test changes — this task runs the verification suite.)

- [ ] **Step 1: Run the full test suite**

Run:
```bash
uv run pytest -v
```

Expected: ALL tests pass — including all 22 M3 tests (9 in `test_broker_protocol.py` + 13 in `test_dry_run_broker.py`) and all pre-existing M0/M1/M2 tests (`test_main.py`, `test_parser.py`, `test_gale_math.py`, `test_state_machine.py`, `test_config.py`).

- [ ] **Step 2: Run coverage on the new broker package (must be 100%)**

Run:
```bash
uv run pytest --cov=signal_copier.broker --cov-report=term-missing tests/test_broker_protocol.py tests/test_dry_run_broker.py
```

Expected: 100% line + branch coverage on both `src/signal_copier/broker/base.py` and `src/signal_copier/broker/dry_run.py`. The coverage table at the bottom should show:
```
Name                                          Stmts   Miss  Br   Miss
---------------------------------------------------------------------
src/signal_copier/broker/__init__.py              3      0    0      0
src/signal_copier/broker/base.py                 24      0    0      0
src/signal_copier/broker/dry_run.py              33      0    4      0
---------------------------------------------------------------------
TOTAL                                            60      0    4      0
```

(Exact statement counts may differ slightly. The `Miss` columns must be 0 for both files.)

- [ ] **Step 3: Run ruff on the full set of M3 files**

Run:
```bash
uv run ruff check src/signal_copier/broker/ tests/test_broker_protocol.py tests/test_dry_run_broker.py src/signal_copier/__init__.py
uv run ruff format --check src/signal_copier/broker/ tests/test_broker_protocol.py tests/test_dry_run_broker.py src/signal_copier/__init__.py
```

Expected: both exit 0.

- [ ] **Step 4: Run mypy on the full src tree**

Run:
```bash
uv run mypy src/signal_copier/
```

Expected: `Success: no issues found in N source files`. Exit 0.

- [ ] **Step 5: Run the import smoke test from the spec's Definition of Done**

Run:
```bash
uv run python -c "from signal_copier.broker import Broker, UnsupportedPairError; from signal_copier import Broker as B2; assert Broker is B2; print('OK:', Broker, UnsupportedPairError)"
```

Expected: prints `OK: <class 'signal_copier.broker.base.Broker'> <class 'signal_copier.broker.base.UnsupportedPairError'>`. Exit 0.

- [ ] **Step 6: Run a one-liner sanity check that DryRunBroker works end-to-end**

Run:
```bash
uv run python -c "
import asyncio
from decimal import Decimal
from signal_copier.broker.dry_run import DryRunBroker
from signal_copier.domain.signal import Signal

async def main():
    sig = Signal(
        signal_id='a1b2c3d4e5f6', pair='EUR/JPY', direction='down',
        trigger_hhmm='10:20', expiration_seconds=300,
        received_at_unix=0.0, source_message_id=1, source_chat_id=1,
        raw_text='', trigger_unix_initial=0.0,
        trigger_unix_gale1=300.0, trigger_unix_gale2=600.0,
    )
    broker = DryRunBroker()
    await broker.connect()
    tid = await broker.place(sig, stage='initial', amount=Decimal('2.00'))
    result = await broker.wait_result(tid, timeout=330.0)
    await broker.close()
    assert result == 'win', result
    print('OK: cascade trade_id=', tid, 'result=', result)

asyncio.run(main())
"
```

Expected: prints `OK: cascade trade_id=dryrun-a1b2c3d4e5f6-initial-XXXXXXXX result=win`. Exit 0.

- [ ] **Step 7: Verify the M3 commit history is clean**

Run:
```bash
git log --oneline -10
```

Expected: the last 8 commits are the M3 implementation tasks (T1-T8), in chronological order. (T9 is verification-only — no commit.) Commit messages should match:
```
M3 T8: top-level re-exports for Broker and UnsupportedPairError
M3 T7: regression test for wait_result() defensive 'error' on unknown trade_id
M3 T6: regression tests for _placed dict bounded semantics + uuid4 uniqueness
M3 T5: wait_result() with default and custom outcome providers
M3 T4: place() returns dryrun-prefixed trade_id with structured logging
M3 T3: connect() and close() lifecycle with account_group logging
M3 T2: stub DryRunBroker satisfying the Broker Protocol
M3 T1: scaffold broker package + Broker Protocol + UnsupportedPairError
... (older M0/M1/M2 commits)
```

(Pre-commit may have appended additional `style:` commits if it reformatted any file. That's expected.)

- [ ] **Step 8: Mark M3 complete**

M3 is done when all 7 verification steps above pass. Update `docs/PRD.md` if any spec-level assumptions changed (none expected for M3). M4 (`infra/db.py` + StateStore) is next; create its design spec when ready.

---

## Summary of M3 deliverables

| Deliverable | File | Status |
|---|---|---|
| `Broker` Protocol (4 async methods) | `src/signal_copier/broker/base.py` | ✅ T1 |
| `UnsupportedPairError` exception | `src/signal_copier/broker/base.py` | ✅ T1 |
| `DryRunBroker` dataclass | `src/signal_copier/broker/dry_run.py` | ✅ T2-T5 |
| Pluggable outcome provider (default win) | `src/signal_copier/broker/dry_run.py` | ✅ T5 |
| `_placed` dict bounded semantics | `src/signal_copier/broker/dry_run.py` | ✅ T4-T7 |
| Top-level re-exports | `src/signal_copier/__init__.py` | ✅ T8 |
| Protocol conformance tests (9) | `tests/test_broker_protocol.py` | ✅ T1, T2, T8 |
| DryRunBroker behavior tests (13) | `tests/test_dry_run_broker.py` | ✅ T3-T7 |
| 100% line + branch coverage | both new files | ✅ T9 step 2 |
| mypy --strict clean | both new files | ✅ T9 step 4 |
| ruff check + format clean | all M3 files | ✅ T9 step 3 |
| End-to-end smoke test | ad-hoc `asyncio.run` | ✅ T9 step 6 |

**Total: 9 tasks, 22 tests, 6 new/modified files, 0 new dependencies.**
