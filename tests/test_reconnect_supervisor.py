"""Tests for M10's ReconnectingOlympTradeBroker wrapper.

Wraps OlympTradeBroker (broker/olymp.py). Adds a 1s polling watcher that
detects WS disconnects and a reconnect loop with exponential backoff.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from signal_copier.broker.base import Broker
from signal_copier.broker.olymp import OlympTradeBroker
from signal_copier.broker.reconnect import ReconnectingOlympTradeBroker
from tests._broker_fixtures import FakeClientFactory, FakeOlympTradeClient
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
    """
    inner._build_asset_map = _async_noop  # type: ignore[method-assign]
    inner._cache_start_of_day_balance = _async_noop  # type: ignore[method-assign]
    return inner


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

    orig_build_inner = wrapper._build_inner
    wrapper._build_inner = lambda: _bypass_inner_slow_parts(orig_build_inner())  # type: ignore[method-assign]

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
    orig_build_inner = wrapper._build_inner
    wrapper._build_inner = lambda: _bypass_inner_slow_parts(orig_build_inner())  # type: ignore[method-assign]

    await wrapper.connect()
    assert wrapper._watcher is not None
    assert not wrapper._watcher.done()

    await wrapper.close()
    assert wrapper._watcher is None or wrapper._watcher.done()
    assert fake_client.stop_called is True

    # Second close must not raise
    await wrapper.close()
