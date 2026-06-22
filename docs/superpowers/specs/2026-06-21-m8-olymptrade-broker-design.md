# M8 Design Spec — OlympTradeBroker (real broker adapter)

**Date:** 2026-06-21
**Status:** Draft — awaiting user review
**Milestone:** M8
**PRD reference:** `docs/PRD.md` §3 (User Flow), §4.4 (FR-4.1–4.5), §6 (Tech Stack), §7 (Architecture), §10 (Error Handling), §12.6 (Vendored code), §13.4 (Pair Mapping), §15 (Build Plan M8)

---

## 1. Purpose

Replace the `dry_run` placeholder in `__main__.py` with the **real** broker adapter: `OlympTradeBroker`, a concrete implementation of the M3 `Broker` Protocol that wraps the **vendored** `olymptrade_ws` client. M8 makes end-to-end demo trading possible: a real signal from the Telegram channel → real `place_order` on OlympTrade → real `e:26` push event → state machine transition → Telegram DM.

Without M8, the tool runs in dry-run mode forever (DRY_RUN=true is the v1 default per FR-6.5). M8 is what unlocks `DRY_RUN=false` for the eventual 7-day soak test that gates real-money trading in v2 (per FR-6.6).

**Verifiable outcome (PRD §15):** "Demo trade placed; result received via e:26."

## 2. Scope

In scope for M8:

- `OlympTradeBroker` class wrapping the vendored `OlympTradeClient` (`from olymptrade_ws import OlympTradeClient`, per R-15).
- Asset-map auto-discovery at `connect()` time (R-11): one-shot e:1068 callback → `dict[slash_form_pair, (broker_pair, category)]` → cached for the process lifetime.
- Persistent push-event routing: three callbacks (e:21, e:22, e:26) registered once at `connect()`. Only e:26 mutates state (resolves the per-trade `Future`); e:21/e:22 are log-only.
- Per-trade `asyncio.Future` keyed by broker trade_id. The e:26 callback resolves the matching future; `wait_result()` awaits it.
- Token-expiry detection: `BrokerAuthError` raised on any auth-failure signal from the vendored client.
- Mid-trade disconnect handling: detected via the vendored client's `_connection_lost_handler` → DM-notify `on_olymp_disconnect` → re-raise as `ConnectionError` → scheduler maps to `StageResult='error'` → cascade ends with `error (broker_unavailable)`. M8 does **not** attempt to reconnect (that is M10's `S-5` self-healing supervisor).
- `__main__.py` branch: `if config.dry_run: DryRunBroker else: OlympTradeBroker(...)`. The dry-run path is unchanged.
- Fix for the M6 `daily_drawdown_pct` semantics: M6 treated it as a USD threshold (a comment in `scheduler/trigger.py:560` flagged this). M8 reads the start-of-day balance from the e:55 push at boot, computes the percentage correctly.
- Two-layer test strategy: unit tests against a `FakeOlympTradeClient` duck-typed stub + one recorded-session integration test (slow marker).

Explicitly out of scope for M8:

- **Self-healing reconnect supervisor** (PRD S-5). M10 owns this. M8's disconnect behavior is "exit non-zero, Railway restarts the container, M9 reconciliation logic resumes from DB."
- **Circuit breaker for repeated connection failures** (PRD S-11). M10+ will formalize this.
- **Token-refresh helper script** (PRD S-6). Manual `.env` update for v1.
- **Pre-flight broker validation** (PRD S-13). Deferred.
- **Editing any file under `src/olymptrade_ws/`** — the package is vendored third-party code (R-15, §12.6). All M8 logic lives in `signal_copier/broker/olymp.py`.

## 3. Architecture

### 3.1 One file, one class

`src/signal_copier/broker/olymp.py` (~280 lines). The class `OlympTradeBroker` implements the M3 `Broker` Protocol:

```
M6 Scheduler._drive_cascade()
    ↓ broker.place(signal, stage=stage, amount=amount)
    ↓ broker.wait_result(trade_id, timeout=...)
OlympTradeBroker
    ├─ _assets: dict[str, (str, str)]   # slash→broker pair, category
    ├─ _pending: dict[str, Future]      # trade_id → result future
    └─ _client: OlympTradeClient        # vendored
        ↓ delegates to client.trade.place_order(...)
        ↓ registers per-trade Future in _pending
        ↓ awaits Future resolved by _on_trade_closed (e:26)
```

### 3.2 Three sub-components inside one class

1. **Asset-map cache** (`_build_asset_map`, `_normalize_key`) — built at `connect()` step 5 from the e:1068 push that arrives during `initialize_session()`. Resolves `EUR/JPY` → `(broker_pair="EURJPY", category="forex")` at `place()` time. The map is built **once** and cached for the process lifetime; no per-trade discovery.

2. **Push-event router** (`_on_trade_closed`, `_on_trade_accepted`, `_on_trade_interim`) — registered as global callbacks on the vendored client at `connect()` step 3. The vendored client holds callbacks in `_event_callbacks: Dict[int, List[Callable]]` (`olymptrade_ws/core/client.py:26`); we register three coroutine callbacks there. `_on_trade_closed` is the **only state-mutating** one: it pops the matching `Future` from `_pending` and resolves it. The other two log at INFO and return.

3. **Trade-result surface** — `place()` returns the broker's trade_id as a string (the vendored client returns a numeric id; we stringify for Protocol uniformity). `wait_result(trade_id, timeout=...)` pops `_pending[trade_id]` and awaits it wrapped in `asyncio.wait_for`.

### 3.3 Concurrency

- One `OlympTradeBroker` instance for the whole process (created in `__main__.py`).
- `asyncio.Lock` (`_pending_lock`) guards `_pending` mutation. The vendored client's `_dispatch_message` (`olymptrade_ws/core/client.py:222-267`) creates a `Task` per callback via `asyncio.create_task(cb(message))`, which means e:26 deliveries run as separate tasks. Without the lock, a race between `place()` inserting and `_on_trade_closed` popping could lose entries.
- `place()` and `wait_result()` are both async; the scheduler's `await broker.place(...)` and `await broker.wait_result(...)` calls serialize within a single `SignalSupervisor` coroutine, but **multiple supervisors can be in flight simultaneously** (one per signal). The lock + per-trade `Future` ensures each `wait_result` only sees its own trade's e:26.

### 3.4 Vendored library boundaries

- Import as `from olymptrade_ws import OlympTradeClient, BalanceAPI, MarketAPI, TradeAPI` (the `__init__.py` re-exports — see `src/olymptrade_ws/__init__.py`).
- Event codes accessed via `olymptrade_ws.olympconfig.parameters.E_*` constants (e.g., `E_TRADE_CLOSED = 26`, `E_TRADE_ACCEPTED = 22`, `E_TRADE_UPDATE_INTERIM = 21`). These are documented in `olympconfig/parameters.py:34-37`.
- **No edits to `src/olymptrade_ws/` files.** Per R-15 / §12.6, any protocol-fix patch would be recorded in `VENDORED.md` under "Local modifications" — but M8 ships with zero patches.

## 4. `Broker` Protocol extension

### 4.1 New exception class

Add one new exception class to `src/signal_copier/broker/base.py`:

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

`UnsupportedPairError` is unchanged (M3 contract). Both are part of `broker/base.py`.

### 4.2 `Broker` Protocol — no change

The Protocol stays identical (M3). `OlympTradeBroker.place()` returns `str` (the broker's trade_id); `wait_result()` returns `StageResult`; `connect()/close()` are idempotent. The Protocol is unchanged because M3 was deliberately forward-compatible — the only addition M8 needs is the new exception class, which is a *raises* extension (additive, not breaking).

## 5. `OlympTradeBroker` implementation

### 5.1 Constructor (sync, no I/O)

```python
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
    self._assets: dict[str, tuple[str, str]] = {}  # slash_key → (broker_pair, category)
    self._pending: dict[str, asyncio.Future[dict]] = {}  # active waiters
    self._results: dict[str, dict] = {}  # completed but not yet consumed (race recovery)
    self._pending_lock = asyncio.Lock()
    self._start_of_day_balance: Decimal | None = None
    self._connected = False
```

The notifier is required (no default) because M8 needs it for `on_olymp_disconnect` emission. This breaks the M3 `DryRunBroker()` no-args pattern — `__main__.py` always constructs it with a notifier.

### 5.2 `connect()` lifecycle (async)

```python
async def connect(self) -> None:
    if self._connected:
        return  # idempotent

    # 1. Build vendored client (sync)
    self._client = OlympTradeClient(
        access_token=self._access_token,
        account_id=int(self._account_id) if self._account_id else None,
        account_group=self._account_group,
        log_raw_messages=False,  # verbose; M8 only logs structured events
    )

    # 2. Open the WebSocket
    await self._client.start()

    # 3. Register persistent push callbacks BEFORE initialize_session so
    # e:21/22/26 messages that arrive during session init are captured.
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

    # 7. Cache start-of-day balance for the FR-6.3 drawdown calculation
    await self._cache_start_of_day_balance()

    self._connected = True
    _log.info(
        "OlympTradeBroker connected: account_id=%s group=%s assets=%d",
        self._account_id, self._account_group, len(self._assets),
    )
```

### 5.3 `_build_asset_map()` (private, async)

```python
async def _build_asset_map(self) -> None:
    """One-shot capture of the e:1068 asset list during initialize_session().

    The vendored client's initialize_session() sends e:1068 for both 'demo'
    and 'real' groups (olymptrade_ws/core/client.py:357-367). The response
    payload contains both the account list AND a separate asset-list push
    follows it. We capture the asset list via a temporary callback.

    Times out after 15s. On timeout, the broker cannot resolve any pair
    and every place() will fail. Fail loud — BrokerAuthError.
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future[list[dict]] = loop.create_future()

    async def capture(message: dict) -> None:
        if not future.done():
            future.set_result(message.get("d", []))

    # Event code 1068 is the asset-list push. If the upstream protocol
    # changes this code, M8 needs a vendored patch (per R-15).
    ASSET_LIST_EVENT = 1068
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
        try:
            broker_pair = asset["pair"]
            category = asset.get("cat", "digital")
        except (KeyError, TypeError):
            continue
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

**Note on event code 1068:** the upstream `Chipa/OlympTradeAPI` reference uses `E_GET_BALANCE_REQUEST_1 = 1068` (`olymptrade_ws/olympconfig/parameters.py:40`), but the e:1068 PUSH (unsolicited) carries the asset list. The request/response matching is by UUID (per `olymptrade_ws/core/client.py:233-245`); the push is identified only by event code. If the upstream protocol shape diverges from this assumption, M8 logs a WARNING and the asset map stays empty — every `place()` fails with `UnsupportedPairError`, the user sees the DM, and we ship a vendored patch in `VENDORED.md`.

### 5.4 `_normalize_key(broker_pair)` (module-level helper)

```python
def _normalize_key(broker_pair: str) -> str:
    """Convert broker-internal pair string to the slash form used in signals.

    Examples:
        "EURJPY" → "EUR/JPY"
        "EURJPY-OTC" → "EUR/JPY"
        "EURUSD-otc" → "EUR/USD" (case-insensitive)
        "LATAM_X" → "LATAM_X" (no slash for non-forex assets; signals won't use these)
    """
    base = broker_pair.upper()
    if base.endswith("-OTC"):
        base = base[: -len("-OTC")]
        suffix = "-OTC"
    else:
        suffix = ""
    if len(base) == 6 and base.isalpha():
        return f"{base[:3]}/{base[3:]}{suffix}"
    return broker_pair  # pass-through for unknown shapes
```

**Coverage rationale:** the analyst's signals always use `EUR/JPY`-style notation (PRD FR-2.2). The broker uses 6-letter ISO pairs or `-OTC` suffixes. The signal parser does **not** validate against the broker's available list — that's M8's job. A signal `EUR/JPY-OTC` and a broker entry `EURJPY-OTC` both collapse to `EUR/JPY` so the broker_pair lookup stays simple.

### 5.5 `place(signal, *, stage, amount) -> str`

```python
async def place(
    self,
    signal: Signal,
    *,
    stage: Stage,
    amount: Decimal,
) -> str:
    if not self._connected or self._client is None:
        raise BrokerAuthError("place() called before connect()")

    # 1. Resolve broker pair + category from the asset map
    key = signal.pair
    if key not in self._assets:
        raise UnsupportedPairError(
            f"{key!r} not in broker asset map ({len(self._assets)} available)"
        )
    broker_pair, category = self._assets[key]

    # 2. Submit the trade to the broker
    try:
        response = await self._client.trade.place_order(
            pair=broker_pair,
            amount=float(amount),
            direction=signal.direction,  # "up" | "down"
            duration=signal.expiration_seconds,
            account_id=int(self._account_id),
            group=self._account_group,
            category=category,
        )
    except ConnectionError:
        # Vendored client raises ConnectionError when not connected.
        # The scheduler maps this to StageResult="error".
        raise

    # 3. Validate response shape
    if response is None:
        raise BrokerAuthError("place_order returned None (token rejected?)")
    trade_id = response.get("id")
    if trade_id is None:
        raise BrokerAuthError(
            f"place_order response missing 'id': {response!r}"
        )
    broker_trade_id = str(trade_id)

    # 4. Register a Future for this trade_id so _on_trade_closed can resolve it
    loop = asyncio.get_running_loop()
    future: asyncio.Future[dict] = loop.create_future()
    async with self._pending_lock:
        if broker_trade_id in self._pending:
            # Defensive: broker reused a numeric id (shouldn't happen). Drop
            # the old future — the new trade is the one we care about.
            _log.warning(
                "duplicate broker trade_id=%s; replacing pending future",
                broker_trade_id,
            )
        self._pending[broker_trade_id] = future

    _log.info(
        "place: signal_id=%s pair=%s→%s stage=%s amount=%s broker_trade_id=%s",
        signal.signal_id, signal.pair, broker_pair, stage, amount, broker_trade_id,
    )
    return broker_trade_id
```

### 5.6 `wait_result(trade_id, *, timeout) -> StageResult`

```python
async def wait_result(
    self,
    trade_id: str,
    *,
    timeout: float,
) -> StageResult:
    # 1. Check _results first (handles the race where e:26 arrived before
    # wait_result was called). _on_trade_closed stored the result there.
    async with self._pending_lock:
        if trade_id in self._results:
            payload = self._results.pop(trade_id)
            return _map_status(payload.get("result", "error"))
        future = self._pending.get(trade_id)

    # 2. Reject only if there is no future at all AND we never connected
    # (or we're in a fully post-close state for a trade we never placed).
    # M8 fix: moved the _connected check here from the top of the function.
    # Otherwise a call to wait_result() after close() (with a pending
    # future that close() cancelled) would raise BrokerAuthError before
    # reaching the future, masking the CancelledError the caller wants.
    if future is None:
        if not self._connected:
            raise BrokerAuthError("wait_result() called before connect()")
        _log.warning("wait_result: no pending future for trade_id=%s", trade_id)
        return "error"

    # 3. Await the future with FR-5.3 timeout
    try:
        payload = await asyncio.wait_for(future, timeout=timeout)
    except TimeoutError:
        # Distinguish "broker slow" from "broker disconnected".
        if self._client is not None and not self._client.connection.is_connected:
            await self._notifier.on_olymp_disconnect()
            _log.warning(
                "wait_result: broker disconnected before reporting trade_id=%s",
                trade_id,
            )
            raise ConnectionError("olymp_disconnected") from None
        _log.warning("wait_result timeout: trade_id=%s timeout=%.1fs", trade_id, timeout)
        return "timeout"
    # Note: asyncio.CancelledError is intentionally NOT caught here.
    # close() cancels pending futures; that CancelledError should
    # propagate to the caller so they can distinguish a clean shutdown
    # from a network disconnect (which is converted to ConnectionError
    # above). The M3 contract is a StageResult for normal flow; raising
    # CancelledError is the asyncio-idiomatic way to signal
    # out-of-band cancellation.

    # 4. Clean up and map to StageResult (defensive: default to 'error').
    async with self._pending_lock:
        self._pending.pop(trade_id, None)
    return _map_status(payload.get("result", "error"))
```

**Mapping function:**

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
    if status in {"win"}:
        return "win"
    if status in {"loss", "tie", "equal"}:
        return "loss"  # FR-5.3: tie treated as loss for cascade purposes
    return "error"
```

### 5.7 `_on_trade_closed(message)` (callback, async)

```python
async def _on_trade_closed(self, message: dict) -> None:
    """Persistent e:26 callback. Resolves the matching per-trade Future.

    The vendored client's _dispatch_message creates a Task per callback
    (olymptrade_ws/core/client.py:255-261), so this coroutine runs concurrently
    with place()/wait_result(). The _pending_lock guards dict mutation.

    Race handling: e:26 may arrive BEFORE wait_result() is called (if the
    scheduler places the trade and the broker reports before wait_result
    starts polling). In that case, _pending has no entry for this trade_id,
    and we cache the payload in _results so wait_result's first check finds it.
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
    stage_result = _map_status(status)
    pnl_decimal = Decimal(str(pnl)) if pnl is not None else Decimal("0.00")

    async with self._pending_lock:
        future = self._pending.pop(broker_trade_id, None)
        # Always cache to _results (M8 fix — was an early-return in the
        # original spec draft, but that prevented `wait_result` from
        # finding the result when e:26 arrives between `place()` and
        # `wait_result()` and a future existed at delivery time). With
        # this fix, all three branches — future present / future None
        # (race) / future done (duplicate) — populate `_results` so the
        # cache check in `wait_result` always finds the payload.
        self._results[broker_trade_id] = {"result": stage_result, "pnl": pnl_decimal}
        if future is not None and not future.done():
            future.set_result({"result": stage_result, "pnl": pnl_decimal})
    _log.info(
        "e:26 cached for late wait_result: trade_id=%s status=%s",
        broker_trade_id, status,
    )
```

### 5.8 `_on_trade_accepted` / `_on_trade_interim` (callbacks, log-only)

```python
async def _on_trade_accepted(self, message: dict) -> None:
    """e:22 — trade-placed acknowledgement from broker.

    Informational only. We already got the trade_id from place_order()'s
    response; e:22 confirms the broker registered the order. Logged at INFO
    with the trade_id so Railway logs show the full lifecycle.
    """
    trade_data = message.get("d", [])
    if isinstance(trade_data, list) and trade_data:
        info = trade_data[0]
        if isinstance(info, dict) and info.get("id") is not None:
            _log.info("e:22 trade accepted: trade_id=%s", info["id"])

async def _on_trade_interim(self, message: dict) -> None:
    """e:21 — interim trade update (live balance during the trade).

    Informational only. Does not mutate state. Logged at INFO.
    """
    trade_data = message.get("d", [])
    if isinstance(trade_data, list) and trade_data:
        info = trade_data[0]
        if isinstance(info, dict) and info.get("id") is not None:
            _log.info(
                "e:21 trade interim: trade_id=%s interim_status=%s",
                info["id"], info.get("interim_status"),
            )
```

### 5.9 `close()` (async, idempotent)

```python
async def close(self) -> None:
    if not self._connected or self._client is None:
        return  # idempotent
    try:
        await self._client.stop()
    finally:
        self._connected = False
        # Cancel any pending futures so wait_result callers don't hang.
        # M8 fix: do NOT clear _pending — keep the cancelled futures
        # in place so a subsequent wait_result(trade_id) finds the
        # future, awaits it, and propagates CancelledError to the
        # caller. This lets callers distinguish a clean shutdown
        # (CancelledError) from a network disconnect (ConnectionError).
        # Cleanup happens lazily in wait_result's success path.
        async with self._pending_lock:
            for future in self._pending.values():
                if not future.done():
                    future.cancel("OlympTradeBroker closed")
            self._results.clear()
        _log.info("OlympTradeBroker closed")
```

The vendored client's `stop()` (olymptrade_ws/core/client.py:63-91) cancels `_ping_task` and `_processing_task`, then disconnects the WS. The pending futures get cancelled here in `close()` so callers' `wait_for` raises `CancelledError` immediately rather than waiting for the timeout. `_results` is cleared so a stale cached result cannot bleed into a reconnected session. Cancelled futures stay in `_pending` until the next `wait_result(trade_id)` call for that trade_id (which then awaits the cancelled future and propagates `CancelledError`); this is the asyncio-idiomatic cancellation pattern.

### 5.10 `_cache_start_of_day_balance()` (private, async)

Fixes the M6 `daily_drawdown_pct` semantics gap noted in `scheduler/trigger.py:560`:

```python
async def _cache_start_of_day_balance(self) -> None:
    """Read the e:55 balance push that arrived during initialize_session()
    and cache it for FR-6.3 drawdown calculation.

    The vendored client stores the latest balance in _latest_balance
    (olymptrade_ws/core/client.py:40). The balance update fires once at
    session start; we capture it here so the FR-6.3 daily drawdown check
    uses a real percentage of starting balance instead of the M6 placeholder
    (which treated daily_drawdown_pct as a USD threshold).
    """
    # Brief delay to let the e:55 push arrive (typically <500ms after init).
    for _ in range(30):  # 30 * 100ms = 3s total
        if self._client.current_balance:
            break
        await asyncio.sleep(0.1)

    balance_msg = self._client.current_balance
    if not balance_msg:
        _log.warning(
            "could not read start-of-day balance from e:55 within 3s; "
            "FR-6.3 drawdown check will use 0 baseline (M6 behavior)"
        )
        self._start_of_day_balance: Decimal | None = None
        return

    # Parse the balance for our account_group. Format is platform-specific
    # but generally {'d': [{'group': 'demo', 'balance': 10000.0, ...}]}.
    for entry in balance_msg.get("d", []):
        if isinstance(entry, dict) and entry.get("group") == self._account_group:
            balance = entry.get("balance")
            if balance is not None:
                self._start_of_day_balance = Decimal(str(balance))
                _log.info(
                    "start-of-day balance cached: %s %s",
                    self._start_of_day_balance, self._account_group,
                )
                return

    _log.warning(
        "balance message arrived but no entry matches group=%s",
        self._account_group,
    )
    self._start_of_day_balance = None
```

**Wire-up to M6's `_check_daily_limit`:** M8 ships a tiny patch to `scheduler/trigger.py:570-572` — replace the M6 placeholder with a property access `broker.start_of_day_balance` (read from `OlympTradeBroker`). The scheduler's `_check_daily_limit` becomes:

```python
# M8 replacement for the M6 placeholder
if cfg.daily_drawdown_pct > 0 and self._broker.start_of_day_balance is not None:
    starting = self._broker.start_of_day_balance
    threshold = starting * Decimal(cfg.daily_drawdown_pct) / Decimal(100)
    if summary.realized_pnl <= -threshold:
        return "drawdown"
```

When `start_of_day_balance` is `None` (e:55 didn't arrive in time), the M6 behavior is preserved (treat `daily_drawdown_pct` as USD threshold). `DryRunBroker` has no `start_of_day_balance` attribute; M8 adds it as `Optional[Decimal] = None` to the `Broker` Protocol via duck-typing (no Protocol edit — the scheduler does `getattr(self._broker, 'start_of_day_balance', None)`).

## 6. `__main__.py` integration

Replace the M6 hardcoded `broker = DryRunBroker()` block:

```python
# M6 (replaced):
broker = DryRunBroker()
await broker.connect()

# M8:
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
        config.olymp_account_group, config.olymp_account_id,
    )
    try:
        await broker.connect()
    except BrokerAuthError as exc:
        sys.stderr.write(f"❌ OlympTradeBroker failed to connect: {exc}\n")
        return 2
```

**Why `return 2` on `BrokerAuthError`:** distinguishes config errors (return 2) from runtime errors (return 1) per the existing convention in `main()`. The Railway restart policy (ON_FAILURE) does NOT restart on exit code 2 — only on non-zero from runtime errors. This prevents an infinite restart loop on a bad token (S-11 will formalize this in M10+).

The rest of `__main__.py` is unchanged: the same `notifier` is passed to both the dry-run and olymp branches; the same `Scheduler`, `Listener`, and `Database` wiring applies. The `broker.close()` call in the `finally` block works for both.

## 7. Data flow

### 7.1 Happy path: signal → trade → e:26 → DM

```
TelegramChannel (admin posts "EUR/JPY;10:20;PUT🟥")
    ↓ NewMessage event
Listener.on_new_message(event)
    ↓ parse_signal() succeeds
    ↓ notifier.on_signal_received(signal)
    ↓ signals_queue.put(signal)
Scheduler.run()
    ↓ queue.get() → spawn SignalSupervisor
SignalSupervisor._drive_cascade()
    ↓ loop.call_at(target_mono, fired.set_result, True)
    ↓ await fired (HH:MM arrives)
    ↓ transition(state, FireEvent) → placed_initial
    ↓ broker.place(signal, stage="initial", amount=$2)
        ↓ OlympTradeBroker.place
            ↓ self._assets["EUR/JPY"] = ("EURJPY", "forex")
            ↓ client.trade.place_order(pair="EURJPY", amount=2, direction="down", duration=300, ...)
            ↓ response.id = 12345 (broker numeric id)
            ↓ self._pending["12345"] = future
            ↓ return "12345"
    ↓ state_store.record_stage_placed(signal_id, stage, broker_trade_id="12345")
    ↓ notifier.on_trade_placed(signal, stage="initial", amount=$2, trade_id="12345")
    ↓ await broker.wait_result("12345", timeout=330)
        ↓ async with self._pending_lock:
        ↓   # _results first (handles the e:26-arrived-before-wait_result race)
        ↓   if "12345" in self._results: return _map_status(...)
        ↓   future = self._pending.get("12345")
        ↓ await asyncio.wait_for(future, timeout=330)
        ↓ # 5 minutes elapse, e:26 arrives...
        ↓ _on_trade_closed({"d": [{"id": 12345, "status": "loss", "balance_change": -2.0}]})
            ↓ async with self._pending_lock: future = self._pending.pop("12345")
            ↓ future.set_result({"result": "loss", "pnl": Decimal("-2.00")})
        ↓ # wait_for unblocks
        ↓ return _map_status("loss") → "loss"
    ↓ scheduler's _apply_result_and_finalize transitions to placed_gale1
    ↓ notifier.on_loss(...) → cascade continues to gale1
```

**Race handling.** e:26 may arrive before `wait_result` is called (if the broker reports faster than the scheduler reaches the await — rare but possible). `_on_trade_closed` writes the result to `self._results` when `_pending.pop` returns `None`; `wait_result` checks `_results` first and returns immediately. This is the only data-flow quirk; everything else is straightforward.

### 7.2 Disconnect mid-trade

```
OlympTrade WS closes (network blip)
    ↓ vendored client's Connection sets self._is_connected = False
    ↓ self._client.connection.is_connected now returns False
    ↓ # M8's wait_result polls this on TimeoutError:
    ↓ await asyncio.wait_for(future, timeout=330) → TimeoutError
    ↓ self._client.connection.is_connected == False
    ↓ await self._notifier.on_olymp_disconnect()
    ↓   # DM: "🔌 OlympTrade disconnected. Process will exit; supervisor will restart."
    ↓ raise ConnectionError("olymp_disconnected")
    ↓ # scheduler's _wait_for_stage_result catches → returns "error"
    ↓ # _apply_result_and_finalize → cascade ends with status='error', error_reason='broker_unavailable'
    ↓ # process exits non-zero from main() (M3 runtime error → exit code 1)
    ↓ # Railway restart policy → container restarts, M9 reconciliation logic resumes
```

The vendored client's `_connection_lost_handler` (`olymptrade_ws/core/client.py:94-111`) cancels `_response_futures` but does **not** notify M8's `_pending` futures. M8 detects the disconnect by polling `self._client.connection.is_connected` (a `bool` property on the vendored `Connection` class — `olymptrade_ws/core/connection.py:24-25`) when `wait_result` times out. This is the only place M8 needs to inspect the vendored client's connection state.

### 7.3 Unsupported pair

```
SignalSupervisor._drive_cascade()
    ↓ broker.place(signal, stage="initial", amount=$2)
        ↓ OlympTradeBroker.place
            ↓ self._assets["USD/EGP"] not found
            ↓ raise UnsupportedPairError("'USD/EGP' not in broker asset map...")
    ↓ M6's _drive_cascade catches UnsupportedPairError
    ↓ _apply_error_transition(state, stage, "error", placed_amount)
    ↓ notifier.on_cascade_complete(signal, final_state="error", cumulative_pnl=Decimal("0"))
    ↓ return (cascade ends; state persisted as status='error', error_reason='unsupported_pair')
```

## 8. Error handling

| Failure | Detection | Action |
|---|---|---|
| Asset map missing key | `place()` lookup miss | `raise UnsupportedPairError` → scheduler maps to `status='error', error_reason='unsupported_pair'` |
| `place_order` returns `None` | response shape check | `raise BrokerAuthError` → mapped to `broker_unavailable`, exit non-zero |
| `place_order` returns response without `id` | response shape check | `raise BrokerAuthError` → same as above |
| `place_order` raises `ConnectionError` (WS not connected) | vendored client state | propagate → scheduler catches in `_wait_for_stage_result` → `error` |
| `wait_result` timeout, broker connected | `asyncio.wait_for` timeout, `_client.connection.is_connected == True` | return `"timeout"` (M3 semantics — treated as loss for cascade) |
| `wait_result` timeout, broker disconnected | `asyncio.wait_for` timeout, `_client.connection.is_connected == False` | DM-notify `on_olymp_disconnect`, raise `ConnectionError` → scheduler maps to `error` |
| e:1068 push never arrives | `_build_asset_map` 15s timeout | raise `BrokerAuthError("asset_map_timeout")` from `connect()` → `__main__.py` exits non-zero |
| e:1068 arrives but contains no usable assets | `_assets` dict is empty after parse | raise `BrokerAuthError("asset_map_empty")` → same as above |
| e:26 received for unknown trade_id | `_pending.pop` returns None, `_results` lookup also misses | loguru WARNING, ignored |
| e:26 received twice for same trade_id | `_pending.pop` returns None or `future.done() == True` | loguru WARNING, ignored |
| e:1068 payload shape changes (upstream protocol shift) | `KeyError` from `asset["pair"]` | loguru WARNING, asset skipped, map may be partial |
| Token rejected by broker at any point | `BrokerAuthError` raised from `connect()` or `place()` | DM-notify user, exit non-zero from `__main__.py` (code 2 for config errors, 1 for runtime) |
| Vendored client `_connection_lost_handler` fires | (detected via `connection.is_connected` polling in `wait_result`) | DM-notify `on_olymp_disconnect`, raise `ConnectionError` |
| e:55 balance push never arrives (3s timeout) | `_cache_start_of_day_balance` polling loop expires | `_start_of_day_balance = None` → FR-6.3 falls back to M6 placeholder |
| Account group mismatch (config=demo, broker=real) | `connect()` step 6 check | raise `BrokerAuthError`, exit non-zero |
| `close()` called mid-trade | futures not yet resolved | cancel all `_pending` futures; `wait_result` raises `CancelledError` |
| `place()` called before `connect()` | `self._connected == False` | raise `BrokerAuthError` |
| `wait_result()` called before `connect()` | `self._connected == False` | raise `BrokerAuthError` |
| `wait_result()` called twice for same trade_id | second call's `_pending.get` returns None | return `"error"` (defensive — caller bug, same as M3) |

## 9. Testing strategy

Two layers (per the brainstorming decision): unit tests with a fake-vendored-client + one recorded-session integration test.

### 9.1 `tests/test_olymp_broker.py` (NEW, ~400 lines, ~30 tests)

`FakeOlympTradeClient` in `tests/_broker_fixtures.py` (~80 lines) duck-types the subset of `OlympTradeClient` that `OlympTradeBroker` calls:

```python
class FakeOlympTradeClient:
    """Duck-typed stub for olymptrade_ws.OlympTradeClient used by M8 tests.

    Records place_order calls; exposes _deliver_event(event_code, payload) to
    simulate push events. Supports connection.is_connected polling for the
    disconnect-detection tests.
    """
    def __init__(self, *, account_group="demo", account_id=12345):
        self.account_group = account_group
        self.account_id = account_id
        self.connection = FakeConnection()  # .is_connected property
        self._callbacks: dict[int, list[Callable]] = defaultdict(list)
        self.trade = FakeTradeAPI(self)  # .place_order(...) recorder
        self.current_balance: dict | None = None
        self.start_called = False
        self.stop_called = False
        self.initialize_session_called = False
        self._next_trade_id = 1000  # auto-incrementing numeric ids

    async def start(self): self.start_called = True; self.connection._connected = True
    async def stop(self): self.stop_called = True; self.connection._connected = False
    async def initialize_session(self): self.initialize_session_called = True
    def register_callback(self, code, cb): self._callbacks[code].append(cb)
    def unregister_callback(self, code, cb): self._callbacks[code].remove(cb)

    async def _deliver_event(self, event_code: int, payload: dict) -> None:
        """Test helper: deliver a push event as if from the broker."""
        for cb in self._callbacks.get(event_code, []):
            await cb(payload)

class FakeConnection:
    def __init__(self): self._connected = False
    @property
    def is_connected(self) -> bool: return self._connected

class FakeTradeAPI:
    def __init__(self, client: FakeOlympTradeClient):
        self._client = client
        self.place_order_calls: list[dict] = []
        self.next_response: dict | None = None  # test sets this before place()
        self.raise_on_call: Exception | None = None

    async def place_order(self, **kwargs) -> dict | None:
        self.place_order_calls.append(kwargs)
        if self.raise_on_call:
            raise self.raise_on_call
        if self.next_response is not None:
            return self.next_response
        # Default: return a valid trade response with an auto-incrementing id
        self._client._next_trade_id += 1
        return {"id": self._client._next_trade_id, "status": "open"}
```

Coverage matrix (one test per row):

| Test | Asserts |
|---|---|
| `test_connect_is_idempotent` | Second `connect()` no-ops (does not re-call vendored client's `start`) |
| `test_connect_registers_three_callbacks` | E_TRADE_CLOSED/ACCEPTED/INTERIM registered on vendored client |
| `test_connect_calls_initialize_session` | Vendored client's `initialize_session()` awaited |
| `test_connect_waits_for_asset_map` | `connect()` doesn't return until e:1068 captured (test calls `_deliver_event(1068, {"d": [...]})`) |
| `test_connect_asset_map_timeout_raises_broker_auth_error` | 15s timeout with no event → `BrokerAuthError` |
| `test_connect_asset_map_empty_raises_broker_auth_error` | e:1068 arrives with `{"d": []}` → `BrokerAuthError("asset_map_empty")` |
| `test_connect_account_group_mismatch_raises` | Fake reports "real" but config says "demo" → `BrokerAuthError` |
| `test_connect_caches_start_of_day_balance` | e:55 push delivered → `start_of_day_balance` matches |
| `test_connect_balance_timeout_leaves_start_of_day_none` | No e:55 within 3s → `start_of_day_balance is None` |
| `test_place_resolves_pair_via_asset_map` | `EUR/JPY` → fake's `place_order` called with `pair="EURJPY", category="forex"` |
| `test_place_otc_pair_resolves_correctly` | `EUR/JPY-OTC` → broker_pair=`EURJPY-OTC`, category=`otc` |
| `test_place_unsupported_pair_raises` | Pair not in map → `UnsupportedPairError` |
| `test_place_records_pending_future` | `place()` adds entry to `_pending` keyed by returned trade_id |
| `test_place_returns_broker_trade_id_as_string` | Numeric id 12345 → returns "12345" |
| `test_place_none_response_raises_broker_auth_error` | Fake's `next_response = None` → `BrokerAuthError` |
| `test_place_missing_id_in_response_raises_broker_auth_error` | Fake's `next_response = {"status": "win"}` (no id) → `BrokerAuthError` |
| `test_place_connection_error_propagates` | Fake raises `ConnectionError` → propagates unchanged |
| `test_wait_result_resolves_on_e26_win` | Deliver e:26 with `status="win"` → `wait_result` returns `"win"` |
| `test_wait_result_resolves_on_e26_loss` | Same with `status="loss"` → `"loss"` |
| `test_wait_result_resolves_on_e26_tie` | Same with `status="tie"` → `"loss"` (FR-5.3 cascade treatment) |
| `test_wait_result_resolves_on_e26_equal` | Same with `status="equal"` → `"loss"` |
| `test_wait_result_unknown_status_returns_error` | e:26 with `status="weird"` → `"error"` |
| `test_wait_result_timeout_when_broker_connected_returns_timeout` | No event, fake.connection.is_connected = True → `"timeout"` |
| `test_wait_result_timeout_when_broker_disconnected_raises_connection_error` | No event, fake.connection.is_connected = False → `ConnectionError` after DM-notify `on_olymp_disconnect` |
| `test_wait_result_unknown_trade_id_returns_error` | `wait_result("nope", ...)` → `"error"`, WARNING logged |
| `test_wait_result_resolves_after_e26_already_arrived` | Deliver e:26 BEFORE calling wait_result → wait_result returns immediately (race recovery) |
| `test_on_trade_closed_caches_result_in_results_dict` | Deliver e:26 when `_pending` has no entry for the trade_id → result stored in `_results`; subsequent `wait_result` finds it |
| `test_on_trade_closed_ignores_unknown_trade_id` | `_deliver_event(E_TRADE_CLOSED, {"d": [{"id": "nope", ...}]})` → no exception |
| `test_on_trade_closed_ignores_duplicate_delivery` | Deliver same e:26 twice → second is WARNING, future stays resolved |
| `test_on_trade_closed_handles_empty_d_list` | `_deliver_event(E_TRADE_CLOSED, {"d": []})` → no-op |
| `test_on_trade_closed_handles_missing_id` | `_deliver_event(E_TRADE_CLOSED, {"d": [{"status": "win"}]})` → no-op |
| `test_on_trade_accepted_logs_only` | INFO log emitted, no state mutation |
| `test_on_trade_interim_logs_only` | Same |
| `test_close_is_idempotent` | Double `close()` no-ops |
| `test_close_stops_underlying_client` | Vendored client's `stop()` called |
| `test_close_cancels_pending_futures` | Place, then close → wait_result raises `CancelledError` |
| `test_satisfies_broker_protocol` | `isinstance(OlympTradeBroker(...), Broker)` returns True |
| `test_normalize_key_handles_plain` | `"EURJPY"` → `"EUR/JPY"` |
| `test_normalize_key_handles_otc_suffix` | `"EURJPY-OTC"` → `"EUR/JPY"` |
| `test_normalize_key_handles_lowercase` | `"eurjpy"` → `"EUR/JPY"` |
| `test_normalize_key_passes_through_unknown_shape` | `"LATAM_X"` → `"LATAM_X"` |

### 9.2 `tests/test_olymp_broker_recorded.py` (NEW, ~80 lines, 1 test, slow marker)

```python
import json
import pytest
from pathlib import Path

from signal_copier.broker.olymp import OlympTradeBroker

FIXTURE = Path(__file__).parent / "fixtures" / "olymp_e26_sample.json"

@pytest.mark.slow
async def test_recorded_e26_message_resolves_correctly(notifier):
    """Replay a captured e:26 payload through OlympTradeBroker._on_trade_closed.

    Uses the FakeOlympTradeClient but with a real captured e:26 payload
    from the upstream Chipa/OlympTradeAPI reverse-engineering notes.
    Catches regressions when the upstream protocol shape changes.
    """
    payload = json.loads(FIXTURE.read_text())
    broker = OlympTradeBroker(
        access_token="fake", account_id="12345", account_group="demo",
        notifier=notifier,
    )
    # Skip connect(); wire up the dicts directly
    from tests._broker_fixtures import FakeOlympTradeClient
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

**Fixture file `tests/fixtures/olymp_e26_sample.json`** (~15 lines) — a captured e:26 payload from upstream logs. Committed to the repo (it's a public WS message shape, not a secret). If the upstream protocol changes, the fixture becomes outdated and the test fails — clear signal to re-capture.

### 9.3 `tests/_broker_fixtures.py` (NEW, ~80 lines)

`FakeOlympTradeClient`, `FakeConnection`, `FakeTradeAPI`, `make_signal(...)` factory. Pattern matches `tests/_scheduler_fixtures.py:RecordingNotifier`.

### 9.4 `tests/test_broker_protocol.py` (extend, +2 tests)

```python
async def test_olymp_broker_satisfies_protocol(notifier) -> None:
    from signal_copier.broker.olymp import OlympTradeBroker
    broker = OlympTradeBroker(
        access_token="fake", account_id="12345",
        account_group="demo", notifier=notifier,
    )
    assert isinstance(broker, Broker)

def test_broker_auth_error_importable() -> None:
    from signal_copier.broker.base import BrokerAuthError
    assert issubclass(BrokerAuthError, Exception)
```

### 9.5 `tests/test_main.py` (extend, +2 tests)

```python
async def test_main_picks_dry_run_broker_when_dry_run_true(monkeypatch) -> None:
    """DRY_RUN=true → DryRunBroker (M6 behavior unchanged)."""
    ...

async def test_main_picks_olymp_broker_when_dry_run_false_with_token(monkeypatch) -> None:
    """DRY_RUN=false + OLYMP_ACCESS_TOKEN set → OlympTradeBroker constructed."""
    ...
```

These tests stub out `Database.connect`, `TelegramClient`, `OlympTradeBroker.connect`, and `Scheduler.run` so the boot path runs without external dependencies.

### 9.6 Existing tests — no change

- `tests/test_dry_run_broker.py` — no edits. DryRunBroker is unchanged.
- `tests/test_scheduler.py`, `tests/test_state_machine.py` — no edits. M8 is broker-side only; the scheduler's broker call sites are already abstracted behind the Protocol.
- `tests/_scheduler_fixtures.py:RecordingNotifier` — already has `on_olymp_disconnect` from M7; no change.

## 10. File summary

### 10.1 Files to add (4 new files)

| Path | Approx lines | Purpose |
|---|---|---|
| `src/signal_copier/broker/olymp.py` | ~280 | `OlympTradeBroker` class + private helpers + `_normalize_key` |
| `tests/test_olymp_broker.py` | ~400 | 30 unit tests against `FakeOlympTradeClient` |
| `tests/test_olymp_broker_recorded.py` | ~80 | 1 slow integration test |
| `tests/_broker_fixtures.py` | ~80 | `FakeOlympTradeClient`, `FakeTradeAPI`, `FakeConnection` |
| `tests/fixtures/olymp_e26_sample.json` | ~15 | Recorded e:26 payload (committed fixture) |

### 10.2 Files to modify (4 existing files)

| Path | Change | Lines added |
|---|---|---|
| `src/signal_copier/broker/base.py` | Add `BrokerAuthError` exception class | +12 |
| `src/signal_copier/__main__.py` | Config-driven broker selection (DRY_RUN branch) | +20 / -3 |
| `src/signal_copier/scheduler/trigger.py` | Replace M6 drawdown-pct placeholder with real percentage using `broker.start_of_day_balance` | +5 / -2 |
| `tests/test_broker_protocol.py` | +2 tests (`isinstance(olymp_broker, Broker)`, `BrokerAuthError` importable) | +30 |
| `tests/test_main.py` | +2 tests (dry-run branch, olymp branch with token) | +50 |

**Total:** 10 files touched, ~880 lines added (code + tests + fixtures + comments). **No file deleted, no test weakened, no public API removed.**

## 11. Acceptance criteria

M8 is complete when **all** of the following hold:

1. **All tests pass:** `pytest` shows the existing M0–M7 test count plus the new M8 tests, zero failures. Existing tests pass without modification (only Protocol-class additions).
2. **Type-clean:** `mypy --strict src/signal_copier` exits 0.
3. **Lint-clean:** `ruff check .` exits 0.
4. **Protocol completeness:** `isinstance(OlympTradeBroker(...), Broker)` returns True. `isinstance(RecordingNotifier(), Notifier)` still returns True.
5. **End-to-end smoke (manual, Railway `DRY_RUN=false` against demo):**
   - Start the bot with `DRY_RUN=false`, `OLYMP_ACCOUNT_GROUP=demo`, valid `OLYMP_ACCESS_TOKEN` → logs contain `Broker: OlympTradeBroker (live demo, account_id=...)` and `OlympTradeBroker connected: account_id=... group=demo assets=N`.
   - Post a valid signal in the Telegram channel → at trigger time, the Railway log shows `place: signal_id=... pair=EUR/JPY→EURJPY stage=initial amount=2 broker_trade_id=N`. Telegram DM contains `⏱️ Trade placed (INITIAL) / Pair: EUR/JPY / Direction: PUT / Amount: $2.00 / Expires: ... / Trade ID: N`.
   - 5 minutes later, when the broker closes the trade via e:26, the log shows `e:26 trade closed: trade_id=N status=...` (or `loss`/`win`). Telegram DM contains `✅ WIN (INITIAL)` or `❌ LOSS (INITIAL) / Next: scheduling 1st gale at HH:MM, $4.00`.
   - The stages row in PostgreSQL has `result` and `pnl` populated.
6. **Asset-map failure smoke:** start with `OLYMP_ACCESS_TOKEN=invalid` → `BrokerAuthError` raised in `connect()` → process exits with code 2 → Railway does NOT restart (because code 2 ≠ 0).
7. **Pair-mismatch smoke:** post a signal with `USD/EGP` (not on broker) → Telegram DM contains `🏁 Cascade complete: error` (no trade placed). Database `signals.error_reason = 'unsupported_pair'`.
8. **Disconnection smoke:** kill the network mid-trade → `wait_result` detects `connection.is_connected == False` → Telegram DM contains `🔌 OlympTrade disconnected. Process will exit; supervisor will restart.` → process exits with code 1 → Railway restarts.
9. **Recorded-session test:** `pytest tests/test_olymp_broker_recorded.py` passes (slow marker; default pytest skips it).
10. **No vendored-source edits:** `git diff src/olymptrade_ws/` is empty for the M8 commit.

## 12. Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| Upstream protocol changes — e:1068 payload shape diverges from `{d: [{pair, cat}, ...]}` | Medium | `_build_asset_map` catches `KeyError` per entry, logs WARNING, asset skipped. Empty map → `BrokerAuthError("asset_map_empty")` → fail loud. |
| Upstream protocol changes — e:26 payload shape diverges | Low | Recorded-session test catches the regression. Unit tests cover the `unknown_status` path. |
| Numeric trade_id reused by broker | Very low | `place()` replaces `_pending[broker_trade_id]` with WARNING if already present (defensive). |
| Race between `place()` and `_on_trade_closed` for an in-flight trade | Low | `_results` cache handles e:26-arrived-before-wait_result scenario. |
| `start_of_day_balance` not captured (e:55 push delayed >3s) | Low | `_start_of_day_balance = None` → scheduler falls back to M6 placeholder (treats `daily_drawdown_pct` as USD threshold). User sees WARNING in logs. |
| `BrokerAuthError` from `connect()` triggers infinite Railway restart loop | Medium | `__main__.py` returns exit code 2 (config error), Railway's `ON_FAILURE` policy does NOT restart on code 2. |
| `client.connection.is_connected` is unreliable (vendored library bug) | Low | M8 only uses it as a hint for `wait_result`'s disconnect detection. False positives just trigger an extra `on_olymp_disconnect` DM (no cascade abort — same as the timeout path). |
| Vendored library has a hidden connection-close signal we miss | Low | M9+ integration testing will surface this. M8 covers the documented paths. |
| `daily_drawdown_pct` semantics change breaks existing M6 tests | Low | M6 tests assert the placeholder behavior; M8 patch only fires when `broker.start_of_day_balance is not None`. Dry-run path is unchanged. |

## 13. Out of scope

- **Self-healing reconnect supervisor** (PRD S-5) — M10.
- **Circuit breaker for repeated connection failures** (PRD S-11) — M10+.
- **Token-refresh helper script** (PRD S-6) — manual `.env` update for v1.
- **Pre-flight broker validation** (PRD S-13) — deferred.
- **Modifying any file under `src/olymptrade_ws/`** — vendored third-party code; per R-15 / §12.6.
- **Editing the M3 `Broker` Protocol** — additive `BrokerAuthError` exception only; the four-method surface is unchanged.
- **Real-money integration** — FR-6.6 demo-only guardrail unchanged. M8 only enables demo trading end-to-end.
- **Multiple broker support** — non-goal (PRD §2.2).

---

*End of M8 design spec. Next step: user review, then writing-plans.*
