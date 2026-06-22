# M8 OlympTradeBroker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the M6 `dry_run` placeholder in `__main__.py` with `OlympTradeBroker`, a real implementation of the `Broker` Protocol that wraps the vendored `olymptrade_ws` client and enables end-to-end demo trading.

**Architecture:** Single class (`OlympTradeBroker`) in `src/signal_copier/broker/olymp.py` with three sub-components — asset-map cache, push-event router, and per-trade `Future` surface. All broker logic lives in one file. Tests use a duck-typed `FakeOlympTradeClient` (no vendored library at test time). One new exception (`BrokerAuthError`) added to `base.py`; `Broker` Protocol unchanged. `__main__.py` gains a config-driven broker-selection branch.

**Tech Stack:** Python 3.13, pytest-asyncio (asyncio_mode="auto"), mypy --strict, ruff. Uses vendored `olymptrade_ws` package — no edits to it.

---

## File Structure

| Path | Status | Lines | Responsibility |
|---|---|---|---|
| `src/signal_copier/broker/olymp.py` | NEW | ~280 | `OlympTradeBroker` class + private helpers + `_normalize_key` |
| `src/signal_copier/broker/base.py` | MODIFY | +12 | Add `BrokerAuthError` exception |
| `src/signal_copier/broker/__init__.py` | MODIFY | +2 | Re-export `BrokerAuthError` |
| `src/signal_copier/__main__.py` | MODIFY | +20/-3 | Config-driven broker selection (DRY_RUN branch) |
| `src/signal_copier/scheduler/trigger.py` | MODIFY | +5/-2 | Fix `daily_drawdown_pct` semantics via `broker.start_of_day_balance` |
| `tests/_broker_fixtures.py` | NEW | ~80 | `FakeOlympTradeClient`, `FakeTradeAPI`, `FakeConnection`, `make_signal` |
| `tests/test_olymp_broker.py` | NEW | ~400 | ~30 unit tests against `FakeOlympTradeClient` |
| `tests/test_olymp_broker_recorded.py` | NEW | ~80 | 1 slow `@pytest.mark.slow` integration test |
| `tests/fixtures/olymp_e26_sample.json` | NEW | ~15 | Recorded e:26 payload (committed fixture) |
| `tests/test_broker_protocol.py` | MODIFY | +30 | +2 tests (BrokerAuthError importable, OlympTradeBroker satisfies Protocol) |
| `tests/test_main.py` | MODIFY | +50 | +2 tests (dry-run branch, olymp branch with token) |

**Total:** 11 files touched, ~880 lines added.

---

## Task 1: Add `BrokerAuthError` exception

**Files:**
- Modify: `src/signal_copier/broker/base.py:1-17`
- Modify: `src/signal_copier/broker/__init__.py:8`
- Modify: `tests/test_broker_protocol.py:1-9`

- [ ] **Step 1: Write failing test for `BrokerAuthError`**

Append to `tests/test_broker_protocol.py` (after line 58):

```python
def test_broker_auth_error_importable() -> None:
    from signal_copier.broker.base import BrokerAuthError

    assert issubclass(BrokerAuthError, Exception)


def test_broker_auth_error_has_meaningful_message() -> None:
    from signal_copier.broker.base import BrokerAuthError

    err = BrokerAuthError("token rejected")
    assert "token rejected" in str(err)


def test_broker_auth_error_importable_from_top_level() -> None:
    from signal_copier import BrokerAuthError as TopLevel

    assert TopLevel is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_broker_protocol.py -v -k "broker_auth_error"`
Expected: 3 collection errors (`ImportError: cannot import name 'BrokerAuthError'`)

- [ ] **Step 3: Add `BrokerAuthError` to `src/signal_copier/broker/base.py`**

Insert after line 17 (the `UnsupportedPairError` class block):

```python
class BrokerAuthError(Exception):
    """Raised by Broker.place()/connect() when the broker rejects the token,
    the session is invalid, or the WS disconnects unexpectedly.

    Distinct from UnsupportedPairError: BrokerAuthError is an authentication
    or connectivity failure (the broker is reachable but the auth/session is
    not), whereas UnsupportedPairError is a missing-asset failure (the auth
    works but the requested pair doesn't exist on this account).

    The scheduler maps both to status='error', but BrokerAuthError triggers:
      1. notifier.on_olymp_disconnect() — only on disconnect mid-trade
      2. process exit non-zero — so Railway restarts the container

    S-11 (M10+) will wrap BrokerAuthError in a circuit-breaker counter so a
    bad token doesn't trigger an infinite restart loop. For v1, one bad
    token = manual investigation.
    """
```

- [ ] **Step 4: Re-export `BrokerAuthError` from `src/signal_copier/broker/__init__.py`**

Replace line 8:
```python
from signal_copier.broker.base import Broker, BrokerAuthError, UnsupportedPairError

__all__ = ["Broker", "BrokerAuthError", "UnsupportedPairError"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_broker_protocol.py -v -k "broker_auth_error"`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add src/signal_copier/broker/base.py src/signal_copier/broker/__init__.py tests/test_broker_protocol.py
git commit -m "feat(broker): add BrokerAuthError exception for M8"
```

---

## Task 2: Create test fixtures (`FakeOlympTradeClient`)

**Files:**
- Create: `tests/_broker_fixtures.py`

- [ ] **Step 1: Create `tests/_broker_fixtures.py` with fakes**

Write the file:

```python
"""Shared test fixtures for M8's OlympTradeBroker tests.

Helpers:
  - FakeOlympTradeClient: duck-typed stub for olymptrade_ws.OlympTradeClient.
    Records place_order calls; exposes _deliver_event(event_code, payload)
    to simulate push events; supports connection.is_connected polling.
  - FakeConnection: stub for vendored Connection class (.is_connected property).
  - FakeTradeAPI: stub for vendored TradeAPI; .place_order(...) recorder.
  - make_signal: factory for a minimal valid Signal used across tests.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from signal_copier.domain.signal import Signal


class FakeConnection:
    """Stub for olymptrade_ws.core.connection.Connection."""

    def __init__(self) -> None:
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected


class FakeTradeAPI:
    """Stub for olymptrade_ws.api.trade.TradeAPI. .place_order(...) recorder."""

    def __init__(self, client: FakeOlympTradeClient) -> None:
        self._client = client
        self.place_order_calls: list[dict[str, Any]] = []
        self.next_response: dict[str, Any] | None = None
        self.raise_on_call: BaseException | None = None

    async def place_order(self, **kwargs: Any) -> dict[str, Any] | None:
        self.place_order_calls.append(kwargs)
        if self.raise_on_call is not None:
            raise self.raise_on_call
        if self.next_response is not None:
            return self.next_response
        self._client._next_trade_id += 1
        return {"id": self._client._next_trade_id, "status": "open"}


class FakeOlympTradeClient:
    """Duck-typed stub for olymptrade_ws.OlympTradeClient used by M8 tests.

    Records place_order calls; exposes _deliver_event(event_code, payload) to
    simulate push events. Supports connection.is_connected polling for the
    disconnect-detection tests.
    """

    def __init__(
        self,
        *,
        account_group: str = "demo",
        account_id: int = 12345,
    ) -> None:
        self.account_group = account_group
        self.account_id = account_id
        self.connection = FakeConnection()
        self._callbacks: dict[int, list[Callable[..., Any]]] = defaultdict(list)
        self.trade = FakeTradeAPI(self)
        self.current_balance: dict[str, Any] | None = None
        self.start_called = False
        self.stop_called = False
        self.initialize_session_called = False
        self._next_trade_id = 1000

    async def start(self) -> None:
        self.start_called = True
        self.connection._connected = True

    async def stop(self) -> None:
        self.stop_called = True
        self.connection._connected = False

    async def initialize_session(self) -> None:
        self.initialize_session_called = True

    def register_callback(self, code: int, cb: Callable[..., Any]) -> None:
        self._callbacks[code].append(cb)

    def unregister_callback(self, code: int, cb: Callable[..., Any]) -> None:
        self._callbacks[code].remove(cb)

    async def _deliver_event(self, event_code: int, payload: dict[str, Any]) -> None:
        """Test helper: deliver a push event as if from the broker."""
        for cb in self._callbacks.get(event_code, []):
            await cb(payload)


def make_signal(
    *,
    signal_id: str = "test-sig-1",
    pair: str = "EUR/JPY",
    direction: str = "down",
    expiration_seconds: int = 300,
) -> Signal:
    """Build a minimal valid Signal used across broker tests."""
    return Signal(
        signal_id=signal_id,
        pair=pair,
        direction=direction,  # type: ignore[arg-type]
        trigger_hhmm="10:20",
        expiration_seconds=expiration_seconds,
        received_at_unix=1_700_000_000.0,
        source_message_id=1,
        source_chat_id=1,
        raw_text=f"{pair};10:20;PUT🟥",
        trigger_unix_initial=1_700_000_000.0,
        trigger_unix_gale1=1_700_000_300.0,
        trigger_unix_gale2=1_700_000_600.0,
    )


def make_balance_message(*, account_group: str = "demo", balance: float = 10000.0) -> dict[str, Any]:
    """Build a fake e:55 balance push for our account_group."""
    return {"d": [{"group": account_group, "balance": balance}]}
```

- [ ] **Step 2: Verify imports work**

Run: `python -c "from tests._broker_fixtures import FakeOlympTradeClient, FakeTradeAPI, FakeConnection, make_signal, make_balance_message; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add tests/_broker_fixtures.py
git commit -m "test(broker): add FakeOlympTradeClient fixtures for M8"
```

---

## Task 3: `_normalize_key()` helper + tests

**Files:**
- Create: `src/signal_copier/broker/olymp.py:1-40`
- Create: `tests/test_olymp_broker.py:1-25`

- [ ] **Step 1: Create `tests/test_olymp_broker.py` with the helper tests**

```python
from __future__ import annotations

from decimal import Decimal

from signal_copier.broker.olymp import _normalize_key


def test_normalize_key_handles_plain() -> None:
    assert _normalize_key("EURJPY") == "EUR/JPY"


def test_normalize_key_handles_otc_suffix() -> None:
    assert _normalize_key("EURJPY-OTC") == "EUR/JPY"


def test_normalize_key_handles_lowercase() -> None:
    assert _normalize_key("eurjpy") == "EUR/JPY"


def test_normalize_key_handles_lowercase_otc() -> None:
    # The lowercase suffix must be normalized to uppercase first
    assert _normalize_key("eurjpy-otc") == "EUR/JPY"


def test_normalize_key_passes_through_unknown_shape() -> None:
    assert _normalize_key("LATAM_X") == "LATAM_X"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_olymp_broker.py -v`
Expected: ImportError on `signal_copier.broker.olymp`

- [ ] **Step 3: Create `src/signal_copier/broker/olymp.py` with `_normalize_key`**

```python
"""OlympTradeBroker — concrete Broker implementation wrapping the vendored
olymptrade_ws client. Implements the M3 Broker Protocol with real I/O for
end-to-end demo trading.

Architecture (3 sub-components in one class):
  1. Asset-map cache (_build_asset_map) — built once at connect() from the
     e:1068 push that arrives during initialize_session().
  2. Push-event router (_on_trade_closed/accepted/interim) — registered as
     persistent callbacks on the vendored client at connect().
  3. Trade-result surface (place/wait_result) — per-trade Future keyed by
     broker trade_id; the e:26 callback resolves the matching Future.

Vendored library contract:
  - Imports use `from olymptrade_ws import OlympTradeClient, BalanceAPI,
    MarketAPI, TradeAPI` (see src/olymptrade_ws/__init__.py re-exports).
  - Event codes use `olymptrade_ws.olympconfig.parameters.E_*` constants.
  - NO edits to files under src/olymptrade_ws/ — this is vendored code
    per PRD R-15 / §12.6.
"""

from __future__ import annotations

import logging

_log = logging.getLogger(__name__)


def _normalize_key(broker_pair: str) -> str:
    """Convert broker-internal pair string to the slash form used in signals.

    Examples:
        "EURJPY" → "EUR/JPY"
        "EURJPY-OTC" → "EUR/JPY"
        "eurjpy-otc" → "EUR/JPY" (case-insensitive)
        "LATAM_X" → "LATAM_X" (no slash for non-forex assets)
    """
    base = broker_pair.upper()
    if base.endswith("-OTC"):
        base = base[: -len("-OTC")]
    if len(base) == 6 and base.isalpha():
        return f"{base[:3]}/{base[3:]}"
    return broker_pair
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_olymp_broker.py -v -k "normalize_key"`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/signal_copier/broker/olymp.py tests/test_olymp_broker.py
git commit -m "feat(broker): add _normalize_key helper for asset map"
```

---

## Task 4: `OlympTradeBroker.__init__()` constructor + tests

**Files:**
- Modify: `src/signal_copier/broker/olymp.py:30-90`
- Modify: `tests/test_olymp_broker.py:1-40`

- [ ] **Step 1: Write failing tests for constructor**

Append to `tests/test_olymp_broker.py`:

```python
import pytest

from signal_copier.broker.olymp import OlympTradeBroker


def test_constructor_rejects_empty_access_token() -> None:
    from signal_copier.notify.protocol import NoOpNotifier

    with pytest.raises(ValueError, match="access_token"):
        OlympTradeBroker(
            access_token="",
            account_id="12345",
            account_group="demo",
            notifier=NoOpNotifier(),
        )


def test_constructor_initializes_state(notifier) -> None:
    broker = OlympTradeBroker(
        access_token="fake",
        account_id="12345",
        account_group="demo",
        notifier=notifier,
    )
    assert broker._connected is False
    assert broker._client is None
    assert broker._assets == {}
    assert broker._pending == {}
    assert broker._results == {}
    assert broker._start_of_day_balance is None


def test_constructor_stores_config(notifier) -> None:
    broker = OlympTradeBroker(
        access_token="fake",
        account_id="99999",
        account_group="real",
        notifier=notifier,
    )
    assert broker._access_token == "fake"
    assert broker._account_id == "99999"
    assert broker._account_group == "real"
```

(The `notifier` fixture is defined in Step 3 — see below.)

- [ ] **Step 2: Add `notifier` fixture to `tests/test_olymp_broker.py`**

Insert at the top of `tests/test_olymp_broker.py` (after the imports):

```python
import pytest

from tests._scheduler_fixtures import RecordingNotifier


@pytest.fixture
def notifier() -> RecordingNotifier:
    return RecordingNotifier()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_olymp_broker.py -v -k "constructor"`
Expected: ImportError (`cannot import name 'OlympTradeBroker'`) or AttributeError on `broker._connected`

- [ ] **Step 4: Add `__init__` to `src/signal_copier/broker/olymp.py`**

Append after the `_normalize_key` function (replace lines 38-41 with):

```python
import asyncio
from decimal import Decimal

from olymptrade_ws import OlympTradeClient
from olymptrade_ws.olympconfig import parameters

from signal_copier.broker.base import Broker, BrokerAuthError, UnsupportedPairError
from signal_copier.domain.gale import Stage
from signal_copier.domain.signal import Signal
from signal_copier.domain.state import StageResult
from signal_copier.notify.protocol import Notifier


# Event code for the e:1068 asset-list push (per spec §5.3; not in
# olympconfig.parameters constants as a named constant).
ASSET_LIST_EVENT: int = 1068


def _normalize_key(broker_pair: str) -> str:
    """Convert broker-internal pair string to the slash form used in signals.

    Examples:
        "EURJPY" → "EUR/JPY"
        "EURJPY-OTC" → "EUR/JPY"
        "eurjpy-otc" → "EUR/JPY" (case-insensitive)
        "LATAM_X" → "LATAM_X" (no slash for non-forex assets)
    """
    base = broker_pair.upper()
    if base.endswith("-OTC"):
        base = base[: -len("-OTC")]
    if len(base) == 6 and base.isalpha():
        return f"{base[:3]}/{base[3:]}"
    return broker_pair


class OlympTradeBroker:
    """Real broker implementation wrapping the vendored olymptrade_ws client.

    See module docstring for architecture. Lifecycle:
      - connect(): open WS, register callbacks, fetch asset map, cache
        start-of-day balance. Idempotent.
      - place(signal, *, stage, amount): resolve pair → submit trade →
        register Future → return broker trade_id.
      - wait_result(trade_id, *, timeout): await Future resolved by e:26.
      - close(): stop client, cancel pending futures. Idempotent.

    Raises:
      BrokerAuthError: token rejected, WS disconnected mid-trade, asset
        map didn't arrive, or place_order returned a malformed response.
      UnsupportedPairError: signal.pair not in the cached asset map.
    """

    def __init__(
        self,
        *,
        access_token: str,
        account_id: str,
        account_group: str = "demo",
        notifier: Notifier,
    ) -> None:
        if not access_token:
            raise ValueError("OlympTradeBroker: access_token is required")
        self._access_token = access_token
        self._account_id = account_id
        self._account_group = account_group
        self._notifier = notifier
        self._client: OlympTradeClient | None = None
        self._assets: dict[str, tuple[str, str]] = {}
        self._pending: dict[str, asyncio.Future[dict[str, object]]] = {}
        self._results: dict[str, dict[str, object]] = {}
        self._pending_lock = asyncio.Lock()
        self._start_of_day_balance: Decimal | None = None
        self._connected = False
```

Note: replace the existing `_normalize_key` and module docstring with the above combined version.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_olymp_broker.py -v -k "constructor"`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add src/signal_copier/broker/olymp.py tests/test_olymp_broker.py
git commit -m "feat(broker): add OlympTradeBroker constructor"
```

---

## Task 5: `_map_status()` helper + tests

**Files:**
- Modify: `src/signal_copier/broker/olymp.py` (add `_map_status`)
- Modify: `tests/test_olymp_broker.py` (add tests)

- [ ] **Step 1: Write failing tests for `_map_status`**

Append to `tests/test_olymp_broker.py`:

```python
from signal_copier.broker.olymp import _map_status


def test_map_status_win() -> None:
    assert _map_status("win") == "win"


def test_map_status_loss() -> None:
    assert _map_status("loss") == "loss"


def test_map_status_tie_becomes_loss() -> None:
    # FR-5.3: tie treated as loss for cascade purposes
    assert _map_status("tie") == "loss"


def test_map_status_equal_becomes_loss() -> None:
    # Alternate broker spelling of tie
    assert _map_status("equal") == "loss"


def test_map_status_unknown_returns_error() -> None:
    assert _map_status("weird") == "error"


def test_map_status_none_returns_error() -> None:
    assert _map_status(None) == "error"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_olymp_broker.py -v -k "map_status"`
Expected: ImportError on `_map_status`

- [ ] **Step 3: Add `_map_status` to `src/signal_copier/broker/olymp.py`**

Insert after `_normalize_key` and before the `OlympTradeBroker` class:

```python
def _map_status(status: str | None) -> StageResult:
    """Map broker status string to StageResult literal.

    Broker status values observed in upstream logs:
      - "win"     → trade closed in profit
      - "loss"    → trade closed in loss
      - "tie"     → broker reports tie (rare; treated as loss for cascade)
      - "equal"   → alternate broker spelling of tie
      - anything else → 'error' (cascade ends with broker_unavailable)
    """
    if status == "win":
        return "win"
    if status in {"loss", "tie", "equal"}:
        return "loss"
    return "error"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_olymp_broker.py -v -k "map_status"`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/signal_copier/broker/olymp.py tests/test_olymp_broker.py
git commit -m "feat(broker): add _map_status helper for broker results"
```

---

## Task 6: `connect()` lifecycle (idempotency + callback registration)

**Files:**
- Modify: `src/signal_copier/broker/olymp.py` (add `connect`)
- Modify: `tests/test_olymp_broker.py` (add tests)

- [ ] **Step 1: Write failing test for `connect()` (idempotency)**

Tests for `connect()` need a way to inject the fake client without making a real WS connection. We use a private `_client_factory` parameter (not in the Broker Protocol) for this. Tests will also stub out `_build_asset_map` and `_cache_start_of_day_balance` (covered in later tasks) so this test stays focused on the connect() lifecycle itself.

Append to `tests/test_olymp_broker.py`:

```python
from tests._broker_fixtures import FakeOlympTradeClient


def _make_broker(
    notifier: RecordingNotifier,
    *,
    fake_client: FakeOlympTradeClient,
    account_group: str = "demo",
) -> OlympTradeBroker:
    """Build a broker wired to a fake client (no I/O)."""
    return OlympTradeBroker(
        access_token="fake",
        account_id="12345",
        account_group=account_group,
        notifier=notifier,
        _client_factory=lambda: fake_client,
    )


async def _async_noop() -> None:
    return None


async def test_connect_is_idempotent(notifier) -> None:
    """Second connect() does not re-call fake_client.start()."""
    fake_client = FakeOlympTradeClient()
    state = {"start_calls": 0}
    real_start = fake_client.start

    async def counting_start() -> None:
        state["start_calls"] += 1
        await real_start()

    fake_client.start = counting_start  # type: ignore[method-assign]

    broker = _make_broker(notifier, fake_client=fake_client)
    broker._build_asset_map = _async_noop  # type: ignore[method-assign]
    broker._cache_start_of_day_balance = _async_noop  # type: ignore[method-assign]

    await broker.connect()
    await broker.connect()
    assert state["start_calls"] == 1
```

- [ ] **Step 2: Refactor `__init__` to support a client factory**

The test above uses a private `_client_factory` parameter. Add it to `__init__`:

Replace the `__init__` method body in `src/signal_copier/broker/olymp.py`:

```python
    def __init__(
        self,
        *,
        access_token: str,
        account_id: str,
        account_group: str = "demo",
        notifier: Notifier,
        _client_factory: Callable[[], OlympTradeClient] | None = None,
    ) -> None:
        if not access_token:
            raise ValueError("OlympTradeBroker: access_token is required")
        self._access_token = access_token
        self._account_id = account_id
        self._account_group = account_group
        self._notifier = notifier
        self._client_factory = _client_factory or self._default_client_factory
        self._client: OlympTradeClient | None = None
        self._assets: dict[str, tuple[str, str]] = {}
        self._pending: dict[str, asyncio.Future[dict[str, object]]] = {}
        self._results: dict[str, dict[str, object]] = {}
        self._pending_lock = asyncio.Lock()
        self._start_of_day_balance: Decimal | None = None
        self._connected = False

    def _default_client_factory(self) -> OlympTradeClient:
        return OlympTradeClient(
            access_token=self._access_token,
            account_id=int(self._account_id) if self._account_id else None,
            account_group=self._account_group,
            log_raw_messages=False,
        )
```

Add `Callable` to the imports at the top of `olymp.py`:

```python
from collections.abc import Callable
```

The existing `test_constructor_*` tests pass unchanged (the new parameter has a default).

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_olymp_broker.py -v -k "idempotent or constructor"`
Expected: AttributeError on `broker.connect`

- [ ] **Step 4: Add `connect()` method to `OlympTradeBroker`**

Append to the class in `src/signal_copier/broker/olymp.py`:

```python
    async def connect(self) -> None:
        """Open WS, register push callbacks, fetch asset map, cache balance.

        Idempotent: a second call is a no-op. Raises BrokerAuthError if:
          - vendored client's WS start fails (auth rejected, network error)
          - asset map (e:1068 push) doesn't arrive within 15s
          - asset map arrives but contains no usable assets
          - account_group reported by broker != configured account_group
        """
        if self._connected:
            return

        # 1. Build vendored client (sync factory call)
        self._client = self._client_factory()

        # 2. Open the WebSocket
        await self._client.start()

        # 3. Register persistent push callbacks BEFORE initialize_session
        self._client.register_callback(
            parameters.E_TRADE_CLOSED, self._on_trade_closed
        )
        self._client.register_callback(
            parameters.E_TRADE_ACCEPTED, self._on_trade_accepted
        )
        self._client.register_callback(
            parameters.E_TRADE_UPDATE_INTERIM, self._on_trade_interim
        )

        # 4. Send startup subscriptions + account-info + balance requests
        await self._client.initialize_session()

        # 5. Build the asset map from the e:1068 push
        await self._build_asset_map()

        # 6. Guardrail: vendored client must agree with config on account group
        if self._client.account_group != self._account_group:
            raise BrokerAuthError(
                f"broker reports account_group={self._client.account_group!r} "
                f"but config says {self._account_group!r}"
            )

        # 7. Cache start-of-day balance for FR-6.3 drawdown calculation
        await self._cache_start_of_day_balance()

        self._connected = True
        _log.info(
            "OlympTradeBroker connected: account_id=%s group=%s assets=%d",
            self._account_id,
            self._account_group,
            len(self._assets),
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_olymp_broker.py -v -k "idempotent or constructor"`
Expected: 4 passed (3 constructor + 1 idempotent)

- [ ] **Step 6: Commit**

```bash
git add src/signal_copier/broker/olymp.py tests/test_olymp_broker.py
git commit -m "feat(broker): add connect() lifecycle with idempotency + client factory"
```

---

## Task 7: `_build_asset_map()` + tests

**Files:**
- Modify: `src/signal_copier/broker/olymp.py` (add `_build_asset_map`)
- Modify: `tests/test_olymp_broker.py` (add tests)

- [ ] **Step 1: Write failing tests for `_build_asset_map`**

Append to `tests/test_olymp_broker.py`:

```python
async def test_build_asset_map_populates_assets(notifier) -> None:
    """Captures the e:1068 push and populates the asset map."""
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)

    # Schedule an e:1068 delivery to fire shortly after _build_asset_map starts
    async def deliver_assets() -> None:
        await asyncio.sleep(0.05)
        await fake_client._deliver_event(
            ASSET_LIST_EVENT,
            {"d": [{"pair": "EURJPY", "cat": "forex"}, {"pair": "GBPUSD-OTC", "cat": "otc"}]},
        )

    asyncio.create_task(deliver_assets())
    await broker._build_asset_map()

    assert broker._assets == {
        "EUR/JPY": ("EURJPY", "forex"),
        "GBP/USD": ("GBPUSD-OTC", "otc"),
    }


async def test_build_asset_map_timeout_raises_broker_auth_error(notifier) -> None:
    """No e:1068 push within 15s → BrokerAuthError.

    Override the timeout to keep this test fast.
    """
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)
    # Monkey-patch the timeout by patching asyncio.wait_for
    import unittest.mock as _mock

    with _mock.patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
        with pytest.raises(BrokerAuthError, match="asset map"):
            await broker._build_asset_map()


async def test_build_asset_map_empty_raises_broker_auth_error(notifier) -> None:
    """e:1068 arrives with empty list → BrokerAuthError."""
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)

    async def deliver_empty() -> None:
        await asyncio.sleep(0.05)
        await fake_client._deliver_event(ASSET_LIST_EVENT, {"d": []})

    asyncio.create_task(deliver_empty())
    with pytest.raises(BrokerAuthError, match="no usable assets"):
        await broker._build_asset_map()


async def test_build_asset_map_skips_malformed_entries(notifier) -> None:
    """Entries missing 'pair' are skipped; valid entries still land in the map."""
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)

    async def deliver_mixed() -> None:
        await asyncio.sleep(0.05)
        await fake_client._deliver_event(
            ASSET_LIST_EVENT,
            {
                "d": [
                    {"pair": "EURJPY", "cat": "forex"},
                    {"cat": "forex"},  # missing 'pair'
                    "not-a-dict",
                    {"pair": "GBPUSD", "cat": "forex"},
                ]
            },
        )

    asyncio.create_task(deliver_mixed())
    await broker._build_asset_map()

    assert "EUR/JPY" in broker._assets
    assert "GBP/USD" in broker._assets
```

Add the import for `BrokerAuthError` at the top of `tests/test_olymp_broker.py`:

```python
from signal_copier.broker.base import BrokerAuthError
from signal_copier.broker.olymp import ASSET_LIST_EVENT, _map_status, _normalize_key
```

(Replace the existing import line.)

Add `asyncio` import if not already present.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_olymp_broker.py -v -k "asset_map"`
Expected: AttributeError on `broker._build_asset_map`

- [ ] **Step 3: Add `_build_asset_map()` to `OlympTradeBroker`**

Append to the class in `src/signal_copier/broker/olymp.py`:

```python
    async def _build_asset_map(self) -> None:
        """One-shot capture of the e:1068 asset list during initialize_session().

        The vendored client's initialize_session() triggers an e:1068 push.
        We capture it via a temporary callback registered before the timeout.

        Times out after 15s. On timeout, every place() will fail. Fail loud.
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future[list[object]] = loop.create_future()

        async def capture(message: dict[str, object]) -> None:
            if not future.done():
                future.set_result(message.get("d", []))

        self._client.register_callback(ASSET_LIST_EVENT, capture)
        try:
            raw_assets = await asyncio.wait_for(future, timeout=15.0)
        except TimeoutError as exc:
            raise BrokerAuthError(
                "asset map: e:1068 push did not arrive within 15s of initialize_session()"
            ) from exc
        finally:
            self._client.unregister_callback(ASSET_LIST_EVENT, capture)

        for asset in raw_assets:
            if not isinstance(asset, dict):
                continue
            broker_pair = asset.get("pair")
            if not isinstance(broker_pair, str):
                continue
            category = asset.get("cat", "digital")
            if not isinstance(category, str):
                category = "digital"
            key = _normalize_key(broker_pair)
            self._assets[key] = (broker_pair, category)

        if not self._assets:
            raise BrokerAuthError(
                "asset map: e:1068 push arrived but contained no usable assets"
            )

        _log.info(
            "asset map built: %d entries (sample: %s)",
            len(self._assets),
            list(self._assets.keys())[:5],
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_olymp_broker.py -v -k "asset_map"`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/signal_copier/broker/olymp.py tests/test_olymp_broker.py
git commit -m "feat(broker): add _build_asset_map with timeout handling"
```

---

## Task 8: `_cache_start_of_day_balance()` + tests

**Files:**
- Modify: `src/signal_copier/broker/olymp.py` (add `_cache_start_of_day_balance`)
- Modify: `tests/test_olymp_broker.py` (add tests)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_olymp_broker.py`:

```python
async def test_cache_start_of_day_balance_success(notifier) -> None:
    fake_client = FakeOlympTradeClient()
    fake_client.current_balance = {"d": [{"group": "demo", "balance": 10000.0}]}
    broker = _make_broker(notifier, fake_client=fake_client)
    await broker._cache_start_of_day_balance()
    assert broker._start_of_day_balance == Decimal("10000.0")


async def test_cache_start_of_day_balance_timeout_leaves_none(notifier) -> None:
    fake_client = FakeOlympTradeClient()
    fake_client.current_balance = None
    broker = _make_broker(notifier, fake_client=fake_client)
    # Speed up the test by patching sleep
    real_sleep = asyncio.sleep

    async def fast_sleep(seconds: float) -> None:
        await real_sleep(0.001)

    with unittest.mock.patch("signal_copier.broker.olymp.asyncio.sleep", side_effect=fast_sleep):
        await broker._cache_start_of_day_balance()
    assert broker._start_of_day_balance is None


async def test_cache_start_of_day_balance_skips_wrong_group(notifier) -> None:
    fake_client = FakeOlympTradeClient()
    # Broker reports real, but we configured demo
    fake_client.current_balance = {"d": [{"group": "real", "balance": 5000.0}]}
    broker = _make_broker(notifier, fake_client=fake_client, account_group="demo")
    await broker._cache_start_of_day_balance()
    assert broker._start_of_day_balance is None
```

Add the import at the top:

```python
import unittest.mock
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_olymp_broker.py -v -k "start_of_day_balance"`
Expected: AttributeError

- [ ] **Step 3: Add `_cache_start_of_day_balance()` to `OlympTradeBroker`**

Append to the class:

```python
    async def _cache_start_of_day_balance(self) -> None:
        """Read the e:55 balance push and cache it for FR-6.3 drawdown.

        The vendored client stores the latest balance in `current_balance`.
        The balance update fires once at session start; we poll briefly so
        the value is populated. If not populated within 3s, set None
        (FR-6.3 then falls back to M6 placeholder behavior).
        """
        # Brief delay to let the e:55 push arrive (typically <500ms)
        for _ in range(30):  # 30 * 100ms = 3s total
            if self._client is not None and self._client.current_balance:
                break
            await asyncio.sleep(0.1)

        if self._client is None:
            self._start_of_day_balance = None
            return

        balance_msg = self._client.current_balance
        if not balance_msg:
            _log.warning(
                "could not read start-of-day balance from e:55 within 3s; "
                "FR-6.3 drawdown check will use 0 baseline (M6 behavior)"
            )
            self._start_of_day_balance = None
            return

        for entry in balance_msg.get("d", []):
            if isinstance(entry, dict) and entry.get("group") == self._account_group:
                balance = entry.get("balance")
                if balance is not None:
                    self._start_of_day_balance = Decimal(str(balance))
                    _log.info(
                        "start-of-day balance cached: %s %s",
                        self._start_of_day_balance,
                        self._account_group,
                    )
                    return

        _log.warning(
            "balance message arrived but no entry matches group=%s",
            self._account_group,
        )
        self._start_of_day_balance = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_olymp_broker.py -v -k "start_of_day_balance"`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/signal_copier/broker/olymp.py tests/test_olymp_broker.py
git commit -m "feat(broker): add _cache_start_of_day_balance for FR-6.3"
```

---

## Task 9: `place()` method + tests

**Files:**
- Modify: `src/signal_copier/broker/olymp.py` (add `place`)
- Modify: `tests/test_olymp_broker.py` (add tests)

- [ ] **Step 1: Write failing tests for `place()`**

Append to `tests/test_olymp_broker.py`:

```python
from signal_copier.broker.base import UnsupportedPairError


async def test_place_resolves_pair_via_asset_map(notifier) -> None:
    """EUR/JPY → fake.place_order called with pair='EURJPY', category='forex'."""
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)
    broker._assets = {"EUR/JPY": ("EURJPY", "forex")}

    sig = make_signal(pair="EUR/JPY", direction="down")
    trade_id = await broker.place(sig, stage="initial", amount=Decimal("2.00"))

    assert len(fake_client.trade.place_order_calls) == 1
    call = fake_client.trade.place_order_calls[0]
    assert call["pair"] == "EURJPY"
    assert call["category"] == "forex"
    assert call["direction"] == "down"
    assert call["amount"] == 2.00  # float conversion for vendored client
    assert trade_id == str(call["id"])


async def test_place_otc_pair_resolves_correctly(notifier) -> None:
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)
    broker._assets = {"EUR/JPY": ("EURJPY-OTC", "otc")}

    sig = make_signal(pair="EUR/JPY", direction="up")
    trade_id = await broker.place(sig, stage="initial", amount=Decimal("2.00"))

    call = fake_client.trade.place_order_calls[0]
    assert call["pair"] == "EURJPY-OTC"
    assert call["category"] == "otc"
    assert isinstance(trade_id, str)


async def test_place_unsupported_pair_raises(notifier) -> None:
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)
    broker._assets = {"EUR/JPY": ("EURJPY", "forex")}

    sig = make_signal(pair="USD/EGP")
    with pytest.raises(UnsupportedPairError, match="USD/EGP"):
        await broker.place(sig, stage="initial", amount=Decimal("2.00"))


async def test_place_records_pending_future(notifier) -> None:
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)
    broker._assets = {"EUR/JPY": ("EURJPY", "forex")}

    sig = make_signal()
    trade_id = await broker.place(sig, stage="initial", amount=Decimal("2.00"))

    assert trade_id in broker._pending
    assert broker._pending[trade_id] is not None


async def test_place_returns_broker_trade_id_as_string(notifier) -> None:
    fake_client = FakeOlympTradeClient()
    fake_client.trade.next_response = {"id": 12345, "status": "open"}
    broker = _make_broker(notifier, fake_client=fake_client)
    broker._assets = {"EUR/JPY": ("EURJPY", "forex")}

    sig = make_signal()
    trade_id = await broker.place(sig, stage="initial", amount=Decimal("2.00"))
    assert trade_id == "12345"


async def test_place_none_response_raises_broker_auth_error(notifier) -> None:
    fake_client = FakeOlympTradeClient()
    fake_client.trade.next_response = None
    broker = _make_broker(notifier, fake_client=fake_client)
    broker._assets = {"EUR/JPY": ("EURJPY", "forex")}

    sig = make_signal()
    with pytest.raises(BrokerAuthError, match="returned None"):
        await broker.place(sig, stage="initial", amount=Decimal("2.00"))


async def test_place_missing_id_in_response_raises(notifier) -> None:
    fake_client = FakeOlympTradeClient()
    fake_client.trade.next_response = {"status": "win"}
    broker = _make_broker(notifier, fake_client=fake_client)
    broker._assets = {"EUR/JPY": ("EURJPY", "forex")}

    sig = make_signal()
    with pytest.raises(BrokerAuthError, match="missing 'id'"):
        await broker.place(sig, stage="initial", amount=Decimal("2.00"))


async def test_place_connection_error_propagates(notifier) -> None:
    fake_client = FakeOlympTradeClient()
    fake_client.trade.raise_on_call = ConnectionError("WS down")
    broker = _make_broker(notifier, fake_client=fake_client)
    broker._assets = {"EUR/JPY": ("EURJPY", "forex")}

    sig = make_signal()
    with pytest.raises(ConnectionError, match="WS down"):
        await broker.place(sig, stage="initial", amount=Decimal("2.00"))


async def test_place_before_connect_raises_broker_auth_error(notifier) -> None:
    broker = OlympTradeBroker(
        access_token="fake",
        account_id="12345",
        account_group="demo",
        notifier=notifier,
    )
    # Note: no _client wiring, no _connected=True
    sig = make_signal()
    with pytest.raises(BrokerAuthError, match="before connect"):
        await broker.place(sig, stage="initial", amount=Decimal("2.00"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_olymp_broker.py -v -k "place_"`
Expected: AttributeError on `broker.place`

- [ ] **Step 3: Add `place()` to `OlympTradeBroker`**

Append to the class:

```python
    async def place(
        self,
        signal: Signal,
        *,
        stage: Stage,
        amount: Decimal,
    ) -> str:
        """Submit a trade for `signal` at `stage` for `amount` USD.

        Returns the broker's trade_id as a string. Registers a Future in
        `_pending` keyed by trade_id; the e:26 callback resolves it.

        Raises:
          BrokerAuthError: client not connected, response is None or
            missing 'id'.
          UnsupportedPairError: signal.pair not in the cached asset map.
          ConnectionError: vendored client raised it (propagated).
        """
        if not self._connected or self._client is None:
            raise BrokerAuthError("place() called before connect()")

        key = signal.pair
        if key not in self._assets:
            raise UnsupportedPairError(
                f"{key!r} not in broker asset map ({len(self._assets)} available)"
            )
        broker_pair, category = self._assets[key]

        response = await self._client.trade.place_order(
            pair=broker_pair,
            amount=float(amount),
            direction=signal.direction,
            duration=signal.expiration_seconds,
            account_id=int(self._account_id),
            group=self._account_group,
            category=category,
        )

        if response is None:
            raise BrokerAuthError("place_order returned None (token rejected?)")
        trade_id = response.get("id")
        if trade_id is None:
            raise BrokerAuthError(
                f"place_order response missing 'id': {response!r}"
            )
        broker_trade_id = str(trade_id)

        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, object]] = loop.create_future()
        async with self._pending_lock:
            if broker_trade_id in self._pending:
                _log.warning(
                    "duplicate broker trade_id=%s; replacing pending future",
                    broker_trade_id,
                )
            self._pending[broker_trade_id] = future

        _log.info(
            "place: signal_id=%s pair=%s→%s stage=%s amount=%s broker_trade_id=%s",
            signal.signal_id,
            signal.pair,
            broker_pair,
            stage,
            amount,
            broker_trade_id,
        )
        return broker_trade_id
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_olymp_broker.py -v -k "place_"`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/signal_copier/broker/olymp.py tests/test_olymp_broker.py
git commit -m "feat(broker): add place() with asset-map lookup and Future registration"
```

---

## Task 10: `_on_trade_closed` callback + tests

**Files:**
- Modify: `src/signal_copier/broker/olymp.py` (add `_on_trade_closed`)
- Modify: `tests/test_olymp_broker.py` (add tests)

- [ ] **Step 1: Write failing tests for `_on_trade_closed`**

Append to `tests/test_olymp_broker.py`:

```python
async def test_on_trade_closed_resolves_pending_future(notifier) -> None:
    """Delivering e:26 with matching trade_id resolves the pending Future."""
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)
    broker._assets = {"EUR/JPY": ("EURJPY", "forex")}

    sig = make_signal()
    trade_id = await broker.place(sig, stage="initial", amount=Decimal("2.00"))

    # Deliver e:26 BEFORE wait_result — covers the race
    await fake_client._deliver_event(
        parameters.E_TRADE_CLOSED,
        {"d": [{"id": int(trade_id), "status": "win", "balance_change": 1.84}]},
    )

    future = broker._pending[trade_id]
    assert future.done()
    result = future.result()
    assert result["result"] == "win"
    assert result["pnl"] == Decimal("1.84")


async def test_on_trade_closed_caches_when_no_pending(notifier) -> None:
    """Delivering e:26 with NO pending entry caches to _results for late wait_result."""
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)

    # No place() called — _pending is empty
    await fake_client._deliver_event(
        parameters.E_TRADE_CLOSED,
        {"d": [{"id": 99999, "status": "loss", "balance_change": -2.0}]},
    )

    assert "99999" in broker._results
    assert broker._results["99999"]["result"] == "loss"


async def test_on_trade_closed_ignores_empty_d_list(notifier) -> None:
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)

    await fake_client._deliver_event(parameters.E_TRADE_CLOSED, {"d": []})
    # No exception; no state mutation
    assert broker._pending == {}
    assert broker._results == {}


async def test_on_trade_closed_ignores_missing_id(notifier) -> None:
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)

    await fake_client._deliver_event(
        parameters.E_TRADE_CLOSED, {"d": [{"status": "win"}]}
    )
    assert broker._pending == {}
    assert broker._results == {}


async def test_on_trade_closed_ignores_duplicate_delivery(notifier) -> None:
    """Second e:26 for the same trade_id is a no-op (WARNING logged)."""
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)

    await fake_client._deliver_event(
        parameters.E_TRADE_CLOSED,
        {"d": [{"id": 12345, "status": "win", "balance_change": 1.84}]},
    )
    assert "12345" in broker._results

    # Second delivery — future is None, _results already has it
    await fake_client._deliver_event(
        parameters.E_TRADE_CLOSED,
        {"d": [{"id": 12345, "status": "win", "balance_change": 1.84}]},
    )
    # No exception; _results not duplicated or overwritten
    assert "12345" in broker._results
```

Add the import at the top:

```python
from olymptrade_ws.olympconfig import parameters
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_olymp_broker.py -v -k "on_trade_closed"`
Expected: AttributeError on `_on_trade_closed`

- [ ] **Step 3: Add `_on_trade_closed()` to `OlympTradeBroker`**

Append to the class:

```python
    async def _on_trade_closed(self, message: dict[str, object]) -> None:
        """Persistent e:26 callback. Resolves the matching per-trade Future.

        Race handling: e:26 may arrive BEFORE wait_result() is called. In
        that case, _pending has no entry, and we cache the payload in
        _results so wait_result's first check finds it.
        """
        trade_data = message.get("d", [])
        if not isinstance(trade_data, list) or not trade_data:
            return
        info = trade_data[0]
        if not isinstance(info, dict):
            return
        raw_id = info.get("id")
        if raw_id is None:
            return
        broker_trade_id = str(raw_id)

        status = info.get("status")
        pnl = info.get("balance_change")
        stage_result = _map_status(status if isinstance(status, str) else None)
        pnl_decimal = Decimal(str(pnl)) if pnl is not None else Decimal("0.00")

        async with self._pending_lock:
            future = self._pending.pop(broker_trade_id, None)
            if future is not None and not future.done():
                future.set_result({"result": stage_result, "pnl": pnl_decimal})
                return
            # Future is None (wait_result hasn't been called yet — race) OR
            # future is done (duplicate e:26). Cache for late wait_result.
            self._results[broker_trade_id] = {"result": stage_result, "pnl": pnl_decimal}
        _log.info(
            "e:26 cached for late wait_result: trade_id=%s status=%s",
            broker_trade_id,
            status,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_olymp_broker.py -v -k "on_trade_closed"`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/signal_copier/broker/olymp.py tests/test_olymp_broker.py
git commit -m "feat(broker): add _on_trade_closed with race recovery via _results"
```

---

## Task 11: `_on_trade_accepted` + `_on_trade_interim` callbacks + tests

**Files:**
- Modify: `src/signal_copier/broker/olymp.py` (add callbacks)
- Modify: `tests/test_olymp_broker.py` (add tests)

- [ ] **Step 1: Write failing tests for the log-only callbacks**

Append to `tests/test_olymp_broker.py`:

```python
async def test_on_trade_accepted_logs_only(notifier, caplog) -> None:
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)

    with caplog.at_level(logging.INFO):
        await fake_client._deliver_event(
            parameters.E_TRADE_ACCEPTED,
            {"d": [{"id": 12345}]},
        )

    assert any("e:22" in record.message for record in caplog.records)
    # No state mutation
    assert broker._pending == {}
    assert broker._results == {}


async def test_on_trade_interim_logs_only(notifier, caplog) -> None:
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)

    with caplog.at_level(logging.INFO):
        await fake_client._deliver_event(
            parameters.E_TRADE_UPDATE_INTERIM,
            {"d": [{"id": 12345, "interim_status": "open"}]},
        )

    assert any("e:21" in record.message for record in caplog.records)
    assert broker._pending == {}
    assert broker._results == {}
```

Add the import at the top:

```python
import logging
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_olymp_broker.py -v -k "on_trade_accepted or on_trade_interim"`
Expected: AttributeError

- [ ] **Step 3: Add the two callbacks to `OlympTradeBroker`**

Append to the class:

```python
    async def _on_trade_accepted(self, message: dict[str, object]) -> None:
        """e:22 — trade-placed acknowledgement from broker.

        Informational only. We already got the trade_id from place_order()'s
        response; e:22 confirms the broker registered the order.
        """
        trade_data = message.get("d", [])
        if isinstance(trade_data, list) and trade_data:
            info = trade_data[0]
            if isinstance(info, dict) and info.get("id") is not None:
                _log.info("e:22 trade accepted: trade_id=%s", info["id"])

    async def _on_trade_interim(self, message: dict[str, object]) -> None:
        """e:21 — interim trade update (live balance during the trade).

        Informational only. Does not mutate state.
        """
        trade_data = message.get("d", [])
        if isinstance(trade_data, list) and trade_data:
            info = trade_data[0]
            if isinstance(info, dict) and info.get("id") is not None:
                _log.info(
                    "e:21 trade interim: trade_id=%s interim_status=%s",
                    info["id"],
                    info.get("interim_status"),
                )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_olymp_broker.py -v -k "on_trade_accepted or on_trade_interim"`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add src/signal_copier/broker/olymp.py tests/test_olymp_broker.py
git commit -m "feat(broker): add e:22/e:21 log-only callbacks"
```

---

## Task 12: `wait_result()` method + tests

**Files:**
- Modify: `src/signal_copier/broker/olymp.py` (add `wait_result`)
- Modify: `tests/test_olymp_broker.py` (add tests)

- [ ] **Step 1: Write failing tests for `wait_result()`**

Append to `tests/test_olymp_broker.py`:

```python
async def test_wait_result_resolves_on_e26_win(notifier) -> None:
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)
    broker._assets = {"EUR/JPY": ("EURJPY", "forex")}

    sig = make_signal()
    trade_id = await broker.place(sig, stage="initial", amount=Decimal("2.00"))

    async def deliver() -> None:
        await asyncio.sleep(0.01)
        await fake_client._deliver_event(
            parameters.E_TRADE_CLOSED,
            {"d": [{"id": int(trade_id), "status": "win", "balance_change": 1.84}]},
        )

    asyncio.create_task(deliver())
    result = await broker.wait_result(trade_id, timeout=2.0)
    assert result == "win"


async def test_wait_result_resolves_on_e26_loss(notifier) -> None:
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)
    broker._assets = {"EUR/JPY": ("EURJPY", "forex")}

    sig = make_signal()
    trade_id = await broker.place(sig, stage="initial", amount=Decimal("2.00"))

    async def deliver() -> None:
        await asyncio.sleep(0.01)
        await fake_client._deliver_event(
            parameters.E_TRADE_CLOSED,
            {"d": [{"id": int(trade_id), "status": "loss", "balance_change": -2.0}]},
        )

    asyncio.create_task(deliver())
    result = await broker.wait_result(trade_id, timeout=2.0)
    assert result == "loss"


async def test_wait_result_resolves_on_e26_tie(notifier) -> None:
    """tie → loss (FR-5.3 cascade treatment)."""
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)
    broker._assets = {"EUR/JPY": ("EURJPY", "forex")}

    sig = make_signal()
    trade_id = await broker.place(sig, stage="initial", amount=Decimal("2.00"))

    async def deliver() -> None:
        await asyncio.sleep(0.01)
        await fake_client._deliver_event(
            parameters.E_TRADE_CLOSED,
            {"d": [{"id": int(trade_id), "status": "tie"}]},
        )

    asyncio.create_task(deliver())
    result = await broker.wait_result(trade_id, timeout=2.0)
    assert result == "loss"


async def test_wait_result_resolves_after_e26_already_arrived(notifier) -> None:
    """Race recovery: e:26 cached in _results when wait_result is called."""
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)
    broker._assets = {"EUR/JPY": ("EURJPY", "forex")}

    sig = make_signal()
    trade_id = await broker.place(sig, stage="initial", amount=Decimal("2.00"))

    # Deliver e:26 BEFORE wait_result
    await fake_client._deliver_event(
        parameters.E_TRADE_CLOSED,
        {"d": [{"id": int(trade_id), "status": "win", "balance_change": 1.84}]},
    )

    result = await broker.wait_result(trade_id, timeout=2.0)
    assert result == "win"
    # _results popped
    assert trade_id not in broker._results


async def test_wait_result_timeout_when_connected_returns_timeout(notifier) -> None:
    fake_client = FakeOlympTradeClient()
    fake_client.connection._connected = True
    broker = _make_broker(notifier, fake_client=fake_client)
    broker._assets = {"EUR/JPY": ("EURJPY", "forex")}

    sig = make_signal()
    trade_id = await broker.place(sig, stage="initial", amount=Decimal("2.00"))

    result = await broker.wait_result(trade_id, timeout=0.05)
    assert result == "timeout"


async def test_wait_result_timeout_when_disconnected_raises(notifier) -> None:
    """Disconnection mid-trade → ConnectionError after DM-notify on_olymp_disconnect."""
    fake_client = FakeOlympTradeClient()
    fake_client.connection._connected = False  # broker disconnected
    broker = _make_broker(notifier, fake_client=fake_client)
    broker._assets = {"EUR/JPY": ("EURJPY", "forex")}

    sig = make_signal()
    trade_id = await broker.place(sig, stage="initial", amount=Decimal("2.00"))

    with pytest.raises(ConnectionError, match="olymp_disconnected"):
        await broker.wait_result(trade_id, timeout=0.05)

    # Notifier was called
    method_names = [m for m, _ in notifier.calls]
    assert "on_olymp_disconnect" in method_names


async def test_wait_result_unknown_trade_id_returns_error(notifier) -> None:
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)

    result = await broker.wait_result("nope", timeout=2.0)
    assert result == "error"


async def test_wait_result_before_connect_raises(notifier) -> None:
    broker = OlympTradeBroker(
        access_token="fake",
        account_id="12345",
        account_group="demo",
        notifier=notifier,
    )
    with pytest.raises(BrokerAuthError, match="before connect"):
        await broker.wait_result("12345", timeout=2.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_olymp_broker.py -v -k "wait_result"`
Expected: AttributeError on `broker.wait_result`

- [ ] **Step 3: Add `wait_result()` to `OlympTradeBroker`**

Append to the class:

```python
    async def wait_result(
        self,
        trade_id: str,
        *,
        timeout: float,
    ) -> StageResult:
        """Block until the broker reports a terminal result for `trade_id`.

        Returns 'win' | 'loss' | 'timeout' | 'error'. Distinguishes:
          - 'timeout' (broker connected, no e:26 within timeout)
          - ConnectionError (broker disconnected — DM-notifies, propagates)
          - 'error' (unknown trade_id, defensive)
        """
        if not self._connected:
            raise BrokerAuthError("wait_result() called before connect()")

        # 1. Check _results first (handles the race where e:26 arrived
        # before wait_result was called).
        async with self._pending_lock:
            if trade_id in self._results:
                payload = self._results.pop(trade_id)
                return _map_status(
                    payload.get("result")
                    if isinstance(payload.get("result"), str)
                    else None
                )
            future = self._pending.get(trade_id)
        if future is None:
            _log.warning("wait_result: no pending future for trade_id=%s", trade_id)
            return "error"

        # 2. Await the future with the configured timeout
        try:
            payload = await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError:
            if self._client is not None and not self._client.connection.is_connected:
                await self._notifier.on_olymp_disconnect()
                _log.warning(
                    "wait_result: broker disconnected before reporting trade_id=%s",
                    trade_id,
                )
                raise ConnectionError("olymp_disconnected") from None
            _log.warning(
                "wait_result timeout: trade_id=%s timeout=%.1fs", trade_id, timeout
            )
            return "timeout"
        except asyncio.CancelledError:
            await self._notifier.on_olymp_disconnect()
            raise ConnectionError("olymp_disconnected") from None

        # 3. Clean up and map to StageResult
        async with self._pending_lock:
            self._pending.pop(trade_id, None)
        status = payload.get("result")
        return _map_status(status if isinstance(status, str) else None)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_olymp_broker.py -v -k "wait_result"`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/signal_copier/broker/olymp.py tests/test_olymp_broker.py
git commit -m "feat(broker): add wait_result with disconnect detection"
```

---

## Task 13: `close()` method + tests

**Files:**
- Modify: `src/signal_copier/broker/olymp.py` (add `close`)
- Modify: `tests/test_olymp_broker.py` (add tests)

- [ ] **Step 1: Write failing tests for `close()`**

Append to `tests/test_olymp_broker.py`:

```python
async def test_close_is_idempotent(notifier) -> None:
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)
    broker._connected = True

    await broker.close()
    await broker.close()  # second call must not raise
    assert fake_client.stop_called is True  # only once


async def test_close_stops_underlying_client(notifier) -> None:
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)
    broker._connected = True

    await broker.close()
    assert fake_client.stop_called is True
    assert broker._connected is False


async def test_close_cancels_pending_futures(notifier) -> None:
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)
    broker._assets = {"EUR/JPY": ("EURJPY", "forex")}
    broker._connected = True

    sig = make_signal()
    trade_id = await broker.place(sig, stage="initial", amount=Decimal("2.00"))

    await broker.close()

    # The Future is cancelled; wait_result should raise CancelledError
    with pytest.raises(asyncio.CancelledError):
        await broker.wait_result(trade_id, timeout=1.0)


async def test_close_clears_results_cache(notifier) -> None:
    fake_client = FakeOlympTradeClient()
    broker = _make_broker(notifier, fake_client=fake_client)
    broker._connected = True
    broker._results["some_id"] = {"result": "win", "pnl": Decimal("1.0")}

    await broker.close()
    assert broker._results == {}


async def test_close_without_connect_is_safe(notifier) -> None:
    """close() before connect() is a no-op."""
    broker = OlympTradeBroker(
        access_token="fake",
        account_id="12345",
        account_group="demo",
        notifier=notifier,
    )
    await broker.close()
    assert broker._connected is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_olymp_broker.py -v -k "close_"`
Expected: AttributeError on `broker.close`

- [ ] **Step 3: Add `close()` to `OlympTradeBroker`**

Append to the class:

```python
    async def close(self) -> None:
        """Stop the vendored client and cancel pending futures.

        Idempotent. Cancels any pending Futures so wait_result callers
        don't hang on the timeout.
        """
        if not self._connected or self._client is None:
            return
        try:
            await self._client.stop()
        finally:
            self._connected = False
            async with self._pending_lock:
                for future in self._pending.values():
                    if not future.done():
                        future.cancel("OlympTradeBroker closed")
                self._pending.clear()
                self._results.clear()
        _log.info("OlympTradeBroker closed")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_olymp_broker.py -v -k "close_"`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/signal_copier/broker/olymp.py tests/test_olymp_broker.py
git commit -m "feat(broker): add close() with pending-future cancellation"
```

---

## Task 14: Protocol compliance test + remaining broker tests

**Files:**
- Modify: `tests/test_broker_protocol.py` (add protocol test)
- Modify: `tests/test_olymp_broker.py` (add remaining tests)

- [ ] **Step 1: Add Protocol compliance test**

Append to `tests/test_broker_protocol.py`:

```python
def test_olymp_broker_satisfies_protocol() -> None:
    from signal_copier.broker.olymp import OlympTradeBroker
    from signal_copier.notify.protocol import NoOpNotifier

    broker = OlympTradeBroker(
        access_token="fake",
        account_id="12345",
        account_group="demo",
        notifier=NoOpNotifier(),
    )
    assert isinstance(broker, Broker)
```

- [ ] **Step 2: Add connect() callback registration test**

Append to `tests/test_olymp_broker.py`:

```python
async def test_connect_registers_three_callbacks(notifier) -> None:
    """connect() registers e:21/e:22/e:26 callbacks on the vendored client."""
    fake_client = FakeOlympTradeClient()
    broker = OlympTradeBroker(
        access_token="fake",
        account_id="12345",
        account_group="demo",
        notifier=notifier,
        _client_factory=lambda: fake_client,
    )
    broker._build_asset_map = _async_noop  # type: ignore[method-assign]
    broker._cache_start_of_day_balance = _async_noop  # type: ignore[method-assign]

    await broker.connect()

    # Check that the three callbacks were registered
    assert any(
        cb == broker._on_trade_closed for cb in fake_client._callbacks.get(parameters.E_TRADE_CLOSED, [])
    )
    assert any(
        cb == broker._on_trade_accepted for cb in fake_client._callbacks.get(parameters.E_TRADE_ACCEPTED, [])
    )
    assert any(
        cb == broker._on_trade_interim for cb in fake_client._callbacks.get(parameters.E_TRADE_UPDATE_INTERIM, [])
    )


async def test_connect_calls_initialize_session(notifier) -> None:
    fake_client = FakeOlympTradeClient()
    broker = OlympTradeBroker(
        access_token="fake",
        account_id="12345",
        account_group="demo",
        notifier=notifier,
        _client_factory=lambda: fake_client,
    )
    broker._build_asset_map = _async_noop  # type: ignore[method-assign]
    broker._cache_start_of_day_balance = _async_noop  # type: ignore[method-assign]

    await broker.connect()

    assert fake_client.initialize_session_called is True


async def test_connect_account_group_mismatch_raises(notifier) -> None:
    """Broker reports different account_group than configured → BrokerAuthError."""
    fake_client = FakeOlympTradeClient(account_group="real")
    broker = OlympTradeBroker(
        access_token="fake",
        account_id="12345",
        account_group="demo",  # mismatch
        notifier=notifier,
        _client_factory=lambda: fake_client,
    )
    broker._build_asset_map = _async_noop  # type: ignore[method-assign]
    broker._cache_start_of_day_balance = _async_noop  # type: ignore[method-assign]

    with pytest.raises(BrokerAuthError, match="account_group"):
        await broker.connect()
```

Add the `_async_noop` helper at the top of `tests/test_olymp_broker.py` (after the imports):

```python
async def _async_noop() -> None:
    return None
```

- [ ] **Step 3: Run all M8 broker tests**

Run: `pytest tests/test_olymp_broker.py tests/test_broker_protocol.py -v`
Expected: all tests pass (39+ total)

- [ ] **Step 4: Commit**

```bash
git add tests/test_olymp_broker.py tests/test_broker_protocol.py src/signal_copier/broker/olymp.py
git commit -m "test(broker): add protocol + connect() callback tests"
```

---

## Task 15: Recorded-session integration test (slow marker)

**Files:**
- Create: `tests/fixtures/olymp_e26_sample.json`
- Create: `tests/test_olymp_broker_recorded.py`

- [ ] **Step 1: Create the e:26 fixture file**

Write `tests/fixtures/olymp_e26_sample.json`:

```json
{
  "d": [
    {
      "id": 98765,
      "status": "win",
      "balance_change": 1.84,
      "pair": "EURJPY",
      "open_price": 165.432,
      "close_price": 165.512,
      "direction": "down",
      "amount": 2.0,
      "group": "demo"
    }
  ]
}
```

- [ ] **Step 2: Write the recorded-session test**

Create `tests/test_olymp_broker_recorded.py`:

```python
"""Recorded-session integration test for M8's e:26 parsing.

This test replays a captured e:26 payload through OlympTradeBroker
to catch regressions when the upstream WS protocol shape changes.

Marked `@pytest.mark.slow` so it doesn't run in the default suite.
Run with: `pytest -m slow tests/test_olymp_broker_recorded.py`
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path

import pytest

from signal_copier.broker.olymp import OlympTradeBroker
from tests._broker_fixtures import FakeOlympTradeClient
from tests._scheduler_fixtures import RecordingNotifier

FIXTURE = Path(__file__).parent / "fixtures" / "olymp_e26_sample.json"


@pytest.mark.slow
async def test_recorded_e26_message_resolves_correctly() -> None:
    """Replay a captured e:26 payload through _on_trade_closed.

    Uses FakeOlympTradeClient but with a real captured e:26 payload.
    """
    payload = json.loads(FIXTURE.read_text())
    notifier = RecordingNotifier()
    broker = OlympTradeBroker(
        access_token="fake",
        account_id="12345",
        account_group="demo",
        notifier=notifier,
    )
    # Skip connect(); wire up the dicts directly
    broker._client = FakeOlympTradeClient()
    broker._connected = True
    broker._assets = {"EUR/JPY": ("EURJPY", "forex")}

    future = asyncio.get_event_loop().create_future()
    broker._pending["98765"] = future

    await broker._on_trade_closed(payload)

    assert future.done()
    result = future.result()
    assert result["result"] in {"win", "loss", "tie"}
    assert isinstance(result["pnl"], Decimal)
```

- [ ] **Step 3: Add the `slow` marker to `pyproject.toml`**

Modify `pyproject.toml` to add `slow` to `markers`:

In the `[tool.pytest.ini_options]` section, add:

```toml
markers = [
    "slow: marks tests as slow (deselect with '-m \"not slow\"')",
]
```

(The existing `asyncio_mode`, `testpaths`, `addopts` keys remain.)

- [ ] **Step 4: Run the slow test**

Run: `pytest -m slow tests/test_olymp_broker_recorded.py -v`
Expected: 1 passed

- [ ] **Step 5: Verify default pytest run skips it**

Run: `pytest tests/test_olymp_broker_recorded.py -v`
Expected: 1 deselected (skipped because slow marker)

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures/olymp_e26_sample.json tests/test_olymp_broker_recorded.py pyproject.toml
git commit -m "test(broker): add recorded-session integration test for e:26"
```

---

## Task 16: `__main__.py` integration — config-driven broker selection

**Files:**
- Modify: `src/signal_copier/__main__.py:36-58`
- Modify: `tests/test_main.py:1-10` (add tests)

- [ ] **Step 1: Write failing tests for both branches**

Append to `tests/test_main.py`:

```python
@pytest.mark.asyncio
async def test_main_picks_dry_run_broker_when_dry_run_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DRY_RUN=true → DryRunBroker (M6 behavior unchanged)."""
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc")
    monkeypatch.setenv("TELEGRAM_PHONE", "+1234567890")
    monkeypatch.setenv("TELEGRAM_SESSION_STRING", "fake-session")
    monkeypatch.setenv("TELEGRAM_TARGET_CHAT", "@test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h:5432/d")
    monkeypatch.setenv("LOG_PATH", "/tmp/test.log")
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("OLYMP_ACCESS_TOKEN", "")
    monkeypatch.setenv("OLYMP_ACCOUNT_GROUP", "demo")
    monkeypatch.setenv("OLYMP_ACCOUNT_ID", "")

    from signal_copier import __main__
    from signal_copier.broker.dry_run import DryRunBroker

    fake_db = MagicMock()
    fake_db.state_store = MagicMock()
    fake_db.close = AsyncMock()
    fake_tg = MagicMock()
    fake_tg.target_chat_id = -100
    fake_tg.start = AsyncMock(side_effect=asyncio.CancelledError)
    fake_tg.connect = AsyncMock()
    fake_tg.close = AsyncMock()
    fake_scheduler = MagicMock()
    fake_scheduler.run = AsyncMock(side_effect=asyncio.CancelledError)
    fake_scheduler.active_task_count = 0

    with (
        patch.object(__main__, "Database") as MockDatabase,
        patch.object(__main__, "TelegramClient") as MockTelegramClient,
        patch.object(__main__, "DryRunBroker") as MockBroker,
        patch.object(__main__, "Scheduler", return_value=fake_scheduler),
    ):
        MockDatabase.connect = AsyncMock(return_value=fake_db)
        MockTelegramClient.return_value = fake_tg
        MockBroker.return_value.connect = AsyncMock()
        MockBroker.return_value.close = AsyncMock()

        with contextlib.suppress(TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(__main__._run(Config()), timeout=1.0)

        # DryRunBroker was constructed
        assert MockBroker.called


@pytest.mark.asyncio
async def test_main_picks_olymp_broker_when_dry_run_false_with_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DRY_RUN=false + OLYMP_ACCESS_TOKEN set → OlympTradeBroker constructed."""
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc")
    monkeypatch.setenv("TELEGRAM_PHONE", "+1234567890")
    monkeypatch.setenv("TELEGRAM_SESSION_STRING", "fake-session")
    monkeypatch.setenv("TELEGRAM_TARGET_CHAT", "@test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h:5432/d")
    monkeypatch.setenv("LOG_PATH", "/tmp/test.log")
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("OLYMP_ACCESS_TOKEN", "valid-token")
    monkeypatch.setenv("OLYMP_ACCOUNT_GROUP", "demo")
    monkeypatch.setenv("OLYMP_ACCOUNT_ID", "12345")

    from signal_copier import __main__
    from signal_copier.broker.olymp import OlympTradeBroker

    fake_db = MagicMock()
    fake_db.state_store = MagicMock()
    fake_db.close = AsyncMock()
    fake_tg = MagicMock()
    fake_tg.target_chat_id = -100
    fake_tg.start = AsyncMock(side_effect=asyncio.CancelledError)
    fake_tg.connect = AsyncMock()
    fake_tg.close = AsyncMock()
    fake_scheduler = MagicMock()
    fake_scheduler.run = AsyncMock(side_effect=asyncio.CancelledError)
    fake_scheduler.active_task_count = 0

    fake_olymp_broker = MagicMock(spec=OlympTradeBroker)
    fake_olymp_broker.connect = AsyncMock()
    fake_olymp_broker.close = AsyncMock()

    with (
        patch.object(__main__, "Database") as MockDatabase,
        patch.object(__main__, "TelegramClient") as MockTelegramClient,
        patch.object(__main__, "OlympTradeBroker", return_value=fake_olymp_broker) as MockOlymp,
        patch.object(__main__, "Scheduler", return_value=fake_scheduler),
    ):
        MockDatabase.connect = AsyncMock(return_value=fake_db)
        MockTelegramClient.return_value = fake_tg

        with contextlib.suppress(TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(__main__._run(Config()), timeout=1.0)

        # OlympTradeBroker was constructed with the token
        assert MockOlymp.called
        call_kwargs = MockOlymp.call_args.kwargs
        assert call_kwargs["access_token"] == "valid-token"
        assert call_kwargs["account_id"] == "12345"
        assert call_kwargs["account_group"] == "demo"


def test_main_returns_2_when_olymp_token_missing_with_dry_run_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DRY_RUN=false but OLYMP_ACCESS_TOKEN empty → exit code 2."""
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc")
    monkeypatch.setenv("TELEGRAM_PHONE", "+1234567890")
    monkeypatch.setenv("TELEGRAM_SESSION_STRING", "fake-session")
    monkeypatch.setenv("TELEGRAM_TARGET_CHAT", "@test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h:5432/d")
    monkeypatch.setenv("LOG_PATH", "/tmp/test.log")
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("OLYMP_ACCESS_TOKEN", "")
    monkeypatch.setenv("OLYMP_ACCOUNT_GROUP", "demo")
    monkeypatch.setenv("OLYMP_ACCOUNT_ID", "")

    from signal_copier import __main__

    rc = __main__.main()
    assert rc == 2


@pytest.mark.asyncio
async def test_main_returns_2_when_olymp_broker_auth_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """connect() raises BrokerAuthError → _run propagates (mapped to exit 2 by main())."""
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abc")
    monkeypatch.setenv("TELEGRAM_PHONE", "+1234567890")
    monkeypatch.setenv("TELEGRAM_SESSION_STRING", "fake-session")
    monkeypatch.setenv("TELEGRAM_TARGET_CHAT", "@test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h:5432/d")
    monkeypatch.setenv("LOG_PATH", "/tmp/test.log")
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("OLYMP_ACCESS_TOKEN", "valid-token")
    monkeypatch.setenv("OLYMP_ACCOUNT_GROUP", "demo")
    monkeypatch.setenv("OLYMP_ACCOUNT_ID", "12345")

    from signal_copier import __main__
    from signal_copier.broker.base import BrokerAuthError

    fake_db = MagicMock()
    fake_db.state_store = MagicMock()
    fake_db.close = AsyncMock()
    fake_tg = MagicMock()
    fake_tg.target_chat_id = -100
    fake_tg.start = AsyncMock(side_effect=asyncio.CancelledError)
    fake_tg.connect = AsyncMock()
    fake_tg.close = AsyncMock()

    fake_olymp_broker = MagicMock()
    fake_olymp_broker.connect = AsyncMock(
        side_effect=BrokerAuthError("token rejected")
    )
    fake_olymp_broker.close = AsyncMock()

    with (
        patch.object(__main__, "Database") as MockDatabase,
        patch.object(__main__, "TelegramClient") as MockTelegramClient,
        patch.object(__main__, "OlympTradeBroker", return_value=fake_olymp_broker),
    ):
        MockDatabase.connect = AsyncMock(return_value=fake_db)
        MockTelegramClient.return_value = fake_tg

        with pytest.raises(BrokerAuthError):
            await __main__._run(Config())
```

This asserts `_run` propagates the error so `main()`'s top-level handler can map it to exit code 2. The existing `test_main_returns_2_on_config_validation_error` proves the same exit-code pattern works for a different exception class — we don't duplicate the assertion here.

Add the missing import at the top of `tests/test_main.py`:

```python
from signal_copier.config import Config
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_main.py -v -k "olymp_broker or picks_olymp or picks_dry_run"`
Expected: errors because `__main__` doesn't yet import `OlympTradeBroker` / `BrokerAuthError`

- [ ] **Step 3: Modify `src/signal_copier/__main__.py`**

Replace lines 10-58 (the broker construction block):

Replace the import block at the top:

```python
from signal_copier.broker.base import Broker, BrokerAuthError
from signal_copier.broker.dry_run import DryRunBroker
from signal_copier.broker.olymp import OlympTradeBroker
```

Replace the broker construction block in `_run()` (lines 55-58):

```python
        # M8: config-driven broker selection. DRY_RUN=true keeps the M6
        # behavior (DryRunBroker, no I/O). DRY_RUN=false uses OlympTradeBroker
        # wrapping the vendored olymptrade_ws client.
        if config.dry_run:
            broker = DryRunBroker()
            _log.info("Broker: DryRunBroker (DRY_RUN=true)")
            await broker.connect()
        else:
            if not config.olymp_access_token:
                sys.stderr.write(
                    "❌ DRY_RUN=false but OLYMP_ACCESS_TOKEN is empty. "
                    "Set OLYMP_ACCESS_TOKEN in .env or set DRY_RUN=true.\n"
                )
                return 2
            broker = OlympTradeBroker(
                access_token=config.olymp_access_token,
                account_id=config.olymp_account_id,
                account_group=config.olymp_account_group,
                notifier=notifier,
            )
            _log.info(
                "Broker: OlympTradeBroker (live %s, account_id=%s)",
                config.olymp_account_group,
                config.olymp_account_id,
            )
            try:
                await broker.connect()
            except BrokerAuthError as exc:
                sys.stderr.write(f"❌ OlympTradeBroker failed to connect: {exc}\n")
                return 2
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_main.py -v -k "olymp_broker or picks_olymp or picks_dry_run"`
Expected: 4 passed

- [ ] **Step 5: Run the full test_main suite to ensure no regression**

Run: `pytest tests/test_main.py -v`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add src/signal_copier/__main__.py tests/test_main.py
git commit -m "feat(main): config-driven broker selection with DRY_RUN branch"
```

---

## Task 17: Scheduler `daily_drawdown_pct` semantics fix

**Files:**
- Modify: `src/signal_copier/scheduler/trigger.py:554-573`
- Modify: `tests/test_scheduler.py` (add tests)

- [ ] **Step 1: Write failing tests for the new behavior**

Append to `tests/test_scheduler.py`:

```python
async def test_daily_drawdown_uses_percentage_of_start_of_day_balance() -> None:
    """When broker.start_of_day_balance is set, drawdown_pct is a percentage."""
    # Build a supervisor whose broker reports start_of_day_balance=1000
    from tests._scheduler_fixtures import FakeStateStore, make_signal_with_future_trigger
    from signal_copier.scheduler.trigger import SignalSupervisor
    from decimal import Decimal

    signal = make_signal_with_future_trigger(trigger_in_seconds=3600.0)
    fake_broker = FakeBroker()
    fake_broker.start_of_day_balance = Decimal("1000.0")  # type: ignore[attr-defined]
    fake_state = FakeStateStore()
    # Pre-populate daily_summary with realized_pnl = -50 (5% loss)
    from datetime import date
    from signal_copier.infra.db_rows import DailySummaryRow
    fake_state.daily_summaries[date(2026, 6, 21)] = DailySummaryRow(
        date=date(2026, 6, 21),
        signals_count=0,
        trades_count=1,
        wins=0,
        losses=1,
        realized_pnl=Decimal("-50.00"),
    )

    from signal_copier.config import Config
    config = Config(
        daily_drawdown_pct=4,  # 4% of 1000 = 40 threshold; -50 breaches it
        # ... other fields use defaults
    )

    supervisor = SignalSupervisor(
        signal=signal,
        broker=fake_broker,
        state_store=fake_state,
        notifier=RecordingNotifier(),
        config=config,
    )

    limit = await supervisor._check_daily_limit()
    assert limit == "drawdown"


async def test_daily_drawdown_falls_back_to_usd_when_balance_none() -> None:
    """When broker.start_of_day_balance is None, treat drawdown_pct as USD (M6 behavior)."""
    from tests._scheduler_fixtures import FakeStateStore, make_signal_with_future_trigger
    from signal_copier.scheduler.trigger import SignalSupervisor
    from decimal import Decimal

    signal = make_signal_with_future_trigger(trigger_in_seconds=3600.0)
    fake_broker = FakeBroker()
    # Don't set start_of_day_balance — getattr returns None
    fake_state = FakeStateStore()
    from datetime import date
    from signal_copier.infra.db_rows import DailySummaryRow
    fake_state.daily_summaries[date(2026, 6, 21)] = DailySummaryRow(
        date=date(2026, 6, 21),
        signals_count=0,
        trades_count=1,
        wins=0,
        losses=1,
        realized_pnl=Decimal("-50.00"),
    )

    from signal_copier.config import Config
    config = Config(daily_drawdown_pct=50)  # M6: USD threshold; -50 breaches it

    supervisor = SignalSupervisor(
        signal=signal,
        broker=fake_broker,
        state_store=fake_state,
        notifier=RecordingNotifier(),
        config=config,
    )

    limit = await supervisor._check_daily_limit()
    assert limit == "drawdown"
```

(The `Config(daily_drawdown_pct=...)` syntax works because `Config` is a pydantic-settings model — kwargs override env defaults. Verify existing tests use the same pattern; if not, adapt.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scheduler.py -v -k "daily_drawdown"`
Expected: existing tests pass (placeholder still USD); new tests fail (the percentage test should return `None` instead of `"drawdown"` because current code is `summary.realized_pnl <= -cfg.daily_drawdown_pct` which treats 4 as USD)

- [ ] **Step 3: Modify `src/signal_copier/scheduler/trigger.py`**

Replace lines 554-573 (`_check_daily_limit` docstring + body) with:

```python
    async def _check_daily_limit(self) -> str | None:
        """Return 'loss' | 'count' | 'drawdown' if a daily limit is hit;
        None if all clear (FR-6.1/6.2/6.3). 0 = disabled (D-3).

        M8 fix: when `self._broker` exposes `start_of_day_balance` (only
        OlympTradeBroker does — DryRunBroker doesn't), `daily_drawdown_pct`
        is interpreted as a percentage of that balance. When the attribute
        is absent or None, the M6 placeholder behavior is preserved
        (treat `daily_drawdown_pct` as a USD threshold). Duck-typed via
        getattr to avoid editing the M3 Broker Protocol.
        """
        summary = await self._state_store.get_daily_summary(self._signal_date())
        if summary is None:
            return None

        cfg = self._config
        if cfg.daily_loss_limit > 0 and summary.realized_pnl <= -cfg.daily_loss_limit:
            return "loss"
        if cfg.daily_trade_limit > 0 and summary.trades_count >= cfg.daily_trade_limit:
            return "count"
        if cfg.daily_drawdown_pct > 0:
            starting = getattr(self._broker, "start_of_day_balance", None)
            if starting is not None:
                threshold = starting * Decimal(cfg.daily_drawdown_pct) / Decimal(100)
                if summary.realized_pnl <= -threshold:
                    return "drawdown"
            else:
                # M6 fallback: daily_drawdown_pct is a USD threshold
                if summary.realized_pnl <= -cfg.daily_drawdown_pct:
                    return "drawdown"
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scheduler.py -v -k "daily_drawdown"`
Expected: all M6 + M8 tests pass

- [ ] **Step 5: Commit**

```bash
git add src/signal_copier/scheduler/trigger.py tests/test_scheduler.py
git commit -m "fix(scheduler): use broker.start_of_day_balance for FR-6.3 percentage"
```

---

## Task 18: Type checking + lint + full test suite

**Files:**
- (no source changes; verification only)

- [ ] **Step 1: Run mypy on the new broker module**

Run: `mypy src/signal_copier/broker/olymp.py src/signal_copier/broker/base.py`
Expected: `Success: no issues found in 2 source files`

If errors appear, fix them. Common issues:
- `dict[str, tuple[str, str]]` annotation on `_assets` — verify
- `Callable[[], OlympTradeClient]` annotation on `_client_factory` — verify
- `asyncio.Future[dict[str, object]]` — generic on Future may need explicit type

- [ ] **Step 2: Run ruff on the modified files**

Run: `ruff check src/signal_copier/broker/ tests/test_olymp_broker.py tests/test_olymp_broker_recorded.py tests/test_broker_protocol.py tests/test_main.py tests/test_scheduler.py`
Expected: `All checks passed!`

- [ ] **Step 3: Run the full test suite**

Run: `pytest`
Expected: all tests pass (M0–M8), including the slow-marked test (which runs only when `-m slow` is passed; default run skips it)

- [ ] **Step 4: Run the slow test specifically**

Run: `pytest -m slow tests/test_olymp_broker_recorded.py -v`
Expected: 1 passed

- [ ] **Step 5: Verify no vendored-source edits**

Run: `git diff --stat src/olymptrade_ws/`
Expected: empty (no files modified under the vendored directory)

- [ ] **Step 6: Final commit (if any fixes were made)**

```bash
git status
# If there are changes:
git add -A
git commit -m "chore(broker): fix type/lint issues found by mypy+ruff"
```

---

## Acceptance criteria

M8 is complete when all of the following hold:

1. `pytest` passes with all M0–M8 tests, zero failures.
2. `mypy --strict src/signal_copier` exits 0.
3. `ruff check .` exits 0.
4. `isinstance(OlympTradeBroker(...), Broker)` returns True (in `tests/test_broker_protocol.py`).
5. `git diff src/olymptrade_ws/` is empty for the M8 commit history.
6. `pytest -m slow tests/test_olymp_broker_recorded.py` passes (1 test).
7. The 4 new files exist (`olymp.py`, `_broker_fixtures.py`, `test_olymp_broker.py`, `test_olymp_broker_recorded.py`, `tests/fixtures/olymp_e26_sample.json`).
8. The 5 modified files show the expected diffs (`base.py`, `broker/__init__.py`, `__main__.py`, `trigger.py`, `test_broker_protocol.py`, `test_main.py`).

Manual smoke (acceptance criteria 5–8 in spec §11) is **out of scope** for this plan — those require a Railway deployment with a real `OLYMP_ACCESS_TOKEN`. They are listed here as documentation; execute them per the spec after the plan completes.