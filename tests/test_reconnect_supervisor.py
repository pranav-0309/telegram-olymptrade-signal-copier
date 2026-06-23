"""Tests for M10's ReconnectingOlympTradeBroker wrapper.

Wraps OlympTradeBroker (broker/olymp.py). Adds a 1s polling watcher that
detects WS disconnects and a reconnect loop with exponential backoff.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from decimal import Decimal

import pytest

from signal_copier.broker.base import Broker
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
        _client_factory=factory,
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
        wrapper._inner._client.trade.raise_on_call = ConnectionError("WS down on place")
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
