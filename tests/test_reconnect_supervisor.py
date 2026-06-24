"""Tests for M10's ReconnectingOlympTradeBroker wrapper.

Wraps OlympTradeBroker (broker/olymp.py). Adds a 1s polling watcher that
detects WS disconnects and a reconnect loop with exponential backoff.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from decimal import Decimal
from typing import Any, cast

import pytest

from olymptrade_ws import OlympTradeClient
from signal_copier.broker.base import Broker, BrokerAuthError
from signal_copier.broker.olymp import OlympTradeBroker
from signal_copier.broker.reconnect import (
    ReconnectingOlympTradeBroker,
    compute_backoff_seconds,
)
from signal_copier.domain.state import StageResult
from tests._broker_fixtures import (
    FakeClientFactory,
    FakeOlympTradeClient,
    make_signal,
)
from tests._scheduler_fixtures import RecordingNotifier


@pytest.fixture
def notifier() -> RecordingNotifier:
    return RecordingNotifier()


@pytest.fixture
def fake_client() -> FakeOlympTradeClient:
    return FakeOlympTradeClient()


@pytest.fixture
def factory(fake_client: FakeOlympTradeClient) -> Iterator[FakeClientFactory]:
    yield FakeClientFactory([fake_client])


def _make_wrapper(
    notifier: RecordingNotifier,
    factory: FakeClientFactory,
    *,
    reconnect_max_attempts: int = 3,
    watcher_poll_seconds: float = 0.05,
) -> ReconnectingOlympTradeBroker:
    return ReconnectingOlympTradeBroker(
        access_token="fake",
        account_id="12345",
        account_group="demo",
        notifier=notifier,
        _client_factory=cast(Callable[[], OlympTradeClient], factory),
        reconnect_max_attempts=reconnect_max_attempts,
        watcher_poll_seconds=watcher_poll_seconds,
    )


async def _async_noop() -> None:
    return None


def _bypass_inner_slow_parts(inner: OlympTradeBroker) -> OlympTradeBroker:
    """Replace inner's slow startup steps with no-ops so wrapper tests don't hang.

    `OlympTradeBroker._build_asset_map` waits up to 15s for an e:1068 push
    that `FakeOlympTradeClient` never delivers. Wrapper tests verify the
    WRAPPER's plumbing (factory call, _inner swap, watcher lifecycle), not
    the inner's asset-map logic — that's covered by `test_olymp_broker.py`.

    Also inject a minimal `_assets` entry so `place()` doesn't raise
    `UnsupportedPairError` before reaching the (potentially monkey-patched)
    `trade.place_order`.
    """
    inner._build_asset_map = _async_noop  # type: ignore[method-assign]
    inner._cache_start_of_day_balance = _async_noop  # type: ignore[method-assign]
    inner._assets = {"EUR/JPY": ("EURJPY", "forex")}
    return inner


def _patch_build_inner(wrapper: ReconnectingOlympTradeBroker) -> None:
    """Make wrapper._build_inner() return a bypassed inner.

    Affects both the wrapper's initial connect() and the reconnect loop's
    `self._build_inner()` call, since the loop calls `self._build_inner()`
    to mint a replacement inner after a disconnect.
    """
    orig = wrapper._build_inner
    wrapper._build_inner = lambda: _bypass_inner_slow_parts(orig())  # type: ignore[method-assign]


async def test_satisfies_broker_protocol(
    notifier: RecordingNotifier,
    factory: FakeClientFactory,
) -> None:
    """ReconnectingOlympTradeBroker satisfies the Broker Protocol."""
    wrapper = _make_wrapper(notifier, factory)
    assert isinstance(wrapper, Broker)


async def test_initial_connect_succeeds(
    notifier: RecordingNotifier, factory: FakeClientFactory, fake_client: FakeOlympTradeClient
) -> None:
    """connect() sets _inner, calls factory, fake.start/init_session == True."""
    wrapper = _make_wrapper(notifier, factory)
    assert wrapper._inner is None
    assert factory.call_count == 0

    _patch_build_inner(wrapper)

    await wrapper.connect()

    assert wrapper._inner is not None
    assert factory.call_count == 1
    assert fake_client.start_called is True
    assert fake_client.initialize_session_called is True


async def test_close_is_idempotent_and_cancels_watcher(
    notifier: RecordingNotifier, factory: FakeClientFactory, fake_client: FakeOlympTradeClient
) -> None:
    """close() cancels watcher task; second close() is no-op."""
    wrapper = _make_wrapper(notifier, factory)
    _patch_build_inner(wrapper)

    await wrapper.connect()
    assert wrapper._watcher is not None
    assert not wrapper._watcher.done()

    await wrapper.close()
    assert wrapper._watcher is None or wrapper._watcher.done()
    assert fake_client.stop_called is True

    # Second close must not raise
    await wrapper.close()


async def test_watcher_detects_disconnect_and_reconnects(
    notifier: RecordingNotifier,
) -> None:
    """Flipping inner.connection._connected=False triggers a watcher-driven reconnect."""
    fake0 = FakeOlympTradeClient()
    fake1 = FakeOlympTradeClient()
    factory = FakeClientFactory([fake0, fake1])
    wrapper = _make_wrapper(
        notifier,
        factory,
        reconnect_max_attempts=3,
        watcher_poll_seconds=0.02,
    )
    _patch_build_inner(wrapper)

    await wrapper.connect()
    inner0_id = id(wrapper._inner)

    # Simulate disconnect: flip fake0's underlying connection state.
    fake0.connection._connected = False

    # Wait up to ~2s for the watcher to detect (~0.02s) and the reconnect
    # loop to complete (1s backoff + small overhead). 100 * 0.02 = 2s.
    for _ in range(100):
        await asyncio.sleep(0.02)
        if id(wrapper._inner) != inner0_id:
            break

    assert id(wrapper._inner) != inner0_id, "watcher did not trigger reconnect"
    assert factory.call_count == 2
    assert fake1.start_called is True

    # Notifier saw the full lifecycle.
    methods = [m for m, _ in notifier.calls]
    assert "on_olymp_disconnect" in methods
    assert "on_olymp_reconnecting" in methods
    assert "on_olymp_reconnected" in methods


@pytest.mark.parametrize("trigger_method", ["place", "wait_result"])
async def test_event_driven_reconnect(
    notifier: RecordingNotifier,
    trigger_method: str,
) -> None:
    """ConnectionError from inner.place() or inner.wait_result() triggers a reconnect
    and is re-raised to the caller so M6's existing handler maps it to 'error'.
    """
    fake0 = FakeOlympTradeClient()
    fake1 = FakeOlympTradeClient()
    factory = FakeClientFactory([fake0, fake1])
    wrapper = _make_wrapper(
        notifier,
        factory,
        reconnect_max_attempts=3,
        watcher_poll_seconds=10.0,  # watcher disabled
    )
    _patch_build_inner(wrapper)

    await wrapper.connect()
    inner0_id = id(wrapper._inner)
    assert wrapper._inner is not None

    if trigger_method == "place":
        # Force inner.place_order() to raise ConnectionError on next call.
        assert wrapper._inner is not None
        assert wrapper._inner._client is not None
        cast(Any, wrapper._inner._client.trade).raise_on_call = ConnectionError("WS down on place")
        sig = make_signal()
        with pytest.raises(ConnectionError, match="WS down on place"):
            await wrapper.place(sig, stage="initial", amount=Decimal("2.00"))
    else:
        # Force inner.wait_result() to raise ConnectionError on next call.
        async def raise_conn_err(*args: object, **kwargs: object) -> StageResult:
            raise ConnectionError("WS down on wait_result")

        wrapper._inner.wait_result = raise_conn_err  # type: ignore[method-assign]
        with pytest.raises(ConnectionError, match="WS down on wait_result"):
            await wrapper.wait_result("fake-trade-id", timeout=1.0)

    # Reconnect should have swapped the inner broker (awaited inside place/wait_result).
    assert id(wrapper._inner) != inner0_id, "event-driven path did not trigger reconnect"
    assert factory.call_count == 2

    methods = [m for m, _ in notifier.calls]
    assert "on_olymp_disconnect" in methods
    assert "on_olymp_reconnected" in methods


def test_compute_backoff_seconds_caps_at_30() -> None:
    """Backoff doubles: 1, 2, 4, 8, 16, then 30-cap."""
    assert compute_backoff_seconds(0) == 1.0
    assert compute_backoff_seconds(1) == 2.0
    assert compute_backoff_seconds(2) == 4.0
    assert compute_backoff_seconds(3) == 8.0
    assert compute_backoff_seconds(4) == 16.0
    assert compute_backoff_seconds(5) == 30.0
    assert compute_backoff_seconds(10) == 30.0


@pytest.fixture
def fast_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reduce reconnect backoff to ~1ms so the exhaustion tests don't wait 31s."""
    monkeypatch.setattr("signal_copier.broker.reconnect._BACKOFF_BASE_SECONDS", 0.001)
    monkeypatch.setattr("signal_copier.broker.reconnect._BACKOFF_CAP_SECONDS", 0.001)


async def test_reconnect_exhausts_after_max_attempts(
    notifier: RecordingNotifier,
    fast_backoff: None,
) -> None:
    """5 consecutive failures → BrokerAuthError + on_olymp_reconnect_failed fired."""
    bad_fakes = [FakeOlympTradeClient() for _ in range(5)]

    async def bad_start() -> None:
        raise BrokerAuthError("token rejected")

    for f in bad_fakes:
        f.start = bad_start  # type: ignore[method-assign]

    factory = FakeClientFactory(bad_fakes)
    wrapper = _make_wrapper(
        notifier,
        factory,
        reconnect_max_attempts=5,
        watcher_poll_seconds=10.0,
    )
    _patch_build_inner(wrapper)

    # Pre-populate _inner with a real connected fake so close() inside the
    # reconnect loop succeeds (and so the wrapper has something to tear down).
    good_inner_factory = FakeClientFactory([FakeOlympTradeClient()])
    wrapper._client_factory = cast(Callable[[], OlympTradeClient], good_inner_factory)
    wrapper._inner = wrapper._build_inner()
    await wrapper._inner.connect()

    # Now swap to the bad factory and trigger the reconnect loop.
    wrapper._client_factory = cast(Callable[[], OlympTradeClient], factory)

    with pytest.raises(BrokerAuthError, match="reconnect exhausted"):
        await wrapper._trigger_reconnect()

    methods = [m for m, _ in notifier.calls]
    assert "on_olymp_disconnect" in methods
    assert methods.count("on_olymp_reconnecting") == 5
    assert "on_olymp_reconnect_failed" in methods


async def test_reconnect_resets_failure_counter_on_success(
    notifier: RecordingNotifier,
    fast_backoff: None,
) -> None:
    """After a successful reconnect, a NEW disconnect can run the full N attempts again.

    Cycle 1: bad_fake[0] raises → counter=1, then good_fake[1] succeeds → counter=0.
    Cycle 2: 3 bad fakes → counter climbs to 3 → BrokerAuthError (exhaustion).
    """
    bad_fake = FakeOlympTradeClient()

    async def bad_start() -> None:
        raise BrokerAuthError("transient")

    bad_fake.start = bad_start  # type: ignore[method-assign]

    good_fake = FakeOlympTradeClient()
    # Use a good-only factory for the initial connect so wrapper._build_inner()
    # returns a connected inner.
    wrapper = _make_wrapper(
        notifier,
        FakeClientFactory([good_fake]),
        reconnect_max_attempts=3,
        watcher_poll_seconds=10.0,
    )
    _patch_build_inner(wrapper)

    wrapper._inner = wrapper._build_inner()
    await wrapper._inner.connect()

    # First reconnect cycle: attempt 1 fails, attempt 2 succeeds.
    wrapper._client_factory = cast(
        Callable[[], OlympTradeClient],
        FakeClientFactory([bad_fake, good_fake]),
    )
    await wrapper._trigger_reconnect()
    assert wrapper._consecutive_failures == 0

    # Second disconnect: all 3 attempts fail → exhaustion → BrokerAuthError.
    bad_fake2 = FakeOlympTradeClient()

    async def bad_start2() -> None:
        raise BrokerAuthError("permanent")

    bad_fake2.start = bad_start2  # type: ignore[method-assign]
    wrapper._client_factory = cast(
        Callable[[], OlympTradeClient],
        FakeClientFactory([bad_fake2, bad_fake2, bad_fake2]),
    )

    with pytest.raises(BrokerAuthError, match="reconnect exhausted"):
        await wrapper._trigger_reconnect()


async def test_concurrent_detection_only_one_reconnect_loop(
    notifier: RecordingNotifier,
) -> None:
    """Watcher + in-flight place() detect disconnect simultaneously → only ONE
    reconnect loop runs (asyncio.Lock + state-check guard in `_trigger_reconnect`).

    Timing:
    - t=0: connect() returns. Watcher is in watcher_loop, sleeping 0.02s.
    - t=0: flip fake0.connection._connected = False.
    - t=0.02-0.04: watcher polls, sees disconnected, enters _trigger_reconnect
      (state → RECONNECTING, lock held). Starts 1s backoff sleep.
    - t=0.05: test wakes up. fake0.trade.raise_on_call set.
    - t=0.05: place() called → inner raises ConnectionError → _trigger_reconnect
      sees state=RECONNECTING, takes fast path (waits for lock).
    - t=~1.02: watcher's reconnect completes (fake1 succeeds). Lock released.
    - t=~1.02: place() returns (re-raises ConnectionError).

    Asserts exactly ONE on_olymp_disconnect notification.
    """
    fake0 = FakeOlympTradeClient()
    fake1 = FakeOlympTradeClient()
    factory = FakeClientFactory([fake0, fake1])
    wrapper = _make_wrapper(
        notifier,
        factory,
        reconnect_max_attempts=3,
        watcher_poll_seconds=0.02,
    )
    _patch_build_inner(wrapper)

    await wrapper.connect()

    # Trigger reconnect from two coroutines "simultaneously".
    fake0.connection._connected = False
    # Let the watcher start its reconnect (poll interval is 0.02s).
    await asyncio.sleep(0.05)

    # Now also trigger via place() while the watcher is still in flight.
    sig = make_signal()
    assert wrapper._inner is not None
    assert wrapper._inner._client is not None
    cast(Any, wrapper._inner._client.trade).raise_on_call = ConnectionError("WS down")
    with pytest.raises(ConnectionError):
        await wrapper.place(sig, stage="initial", amount=Decimal("2.00"))

    # Count on_olymp_disconnect calls — must be exactly 1.
    methods = [m for m, _ in notifier.calls]
    disconnect_count = methods.count("on_olymp_disconnect")
    assert disconnect_count == 1, (
        f"expected exactly 1 disconnect notification, got {disconnect_count}: {methods}"
    )


# --- State-check branch regression tests (Task 6 follow-up) ----------------


from signal_copier.broker.reconnect import _ConnectionState  # noqa: E402


async def test_place_during_reconnect_surfaces_clean_connection_error(
    notifier: RecordingNotifier,
) -> None:
    """When state=RECONNECTING, place() must surface the wrapper-owned
    ConnectionError("broker reconnecting") without re-entering inner.

    This locks in the fix from commit fcf40f2 (place/wait_result 3-way state
    check) against regression — without this test, a future refactor could
    drop the state check and leak BrokerAuthError from the closed inner.
    """
    from tests._broker_fixtures import FakeClientFactory, FakeOlympTradeClient, make_signal

    fake0, fake1 = FakeOlympTradeClient(), FakeOlympTradeClient()
    factory = FakeClientFactory([fake0, fake1])
    wrapper = _make_wrapper(
        notifier,
        factory,
        reconnect_max_attempts=3,
        watcher_poll_seconds=10.0,
    )
    _patch_build_inner(wrapper)
    await wrapper.connect()

    # Force RECONNECTING state without involving the watcher.
    wrapper._state = _ConnectionState.RECONNECTING

    # Hold the lock so the fast path blocks; release after a short delay.
    await wrapper._reconnect_lock.acquire()

    async def release_lock() -> None:
        await asyncio.sleep(0.05)
        wrapper._reconnect_lock.release()

    asyncio.create_task(release_lock())

    sig = make_signal()
    with pytest.raises(ConnectionError, match="broker reconnecting"):
        await wrapper.place(sig, stage="initial", amount=Decimal("2.00"))


async def test_place_when_disconnected_raises_connection_error(
    notifier: RecordingNotifier,
) -> None:
    """When state=DISCONNECTED (exhaustion), place() must raise ConnectionError
    directly without touching inner."""
    from tests._broker_fixtures import FakeClientFactory, FakeOlympTradeClient, make_signal

    fake = FakeOlympTradeClient()
    factory = FakeClientFactory([fake])
    wrapper = _make_wrapper(
        notifier,
        factory,
        reconnect_max_attempts=3,
        watcher_poll_seconds=10.0,
    )
    _patch_build_inner(wrapper)
    await wrapper.connect()

    wrapper._state = _ConnectionState.DISCONNECTED

    sig = make_signal()
    with pytest.raises(ConnectionError, match="broker disconnected"):
        await wrapper.place(sig, stage="initial", amount=Decimal("2.00"))


async def test_wait_result_during_reconnect_surfaces_clean_connection_error(
    notifier: RecordingNotifier,
) -> None:
    """Same regression test as place() but for wait_result()."""
    fake0, fake1 = FakeOlympTradeClient(), FakeOlympTradeClient()
    factory = FakeClientFactory([fake0, fake1])
    wrapper = _make_wrapper(
        notifier,
        factory,
        reconnect_max_attempts=3,
        watcher_poll_seconds=10.0,
    )
    _patch_build_inner(wrapper)
    await wrapper.connect()

    wrapper._state = _ConnectionState.RECONNECTING
    await wrapper._reconnect_lock.acquire()

    async def release_lock() -> None:
        await asyncio.sleep(0.05)
        wrapper._reconnect_lock.release()

    asyncio.create_task(release_lock())

    with pytest.raises(ConnectionError, match="broker reconnecting"):
        await wrapper.wait_result("fake-trade-id", timeout=1.0)
