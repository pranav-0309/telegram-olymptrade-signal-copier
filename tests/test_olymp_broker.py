from __future__ import annotations

import pytest

from signal_copier.broker.olymp import OlympTradeBroker, _map_status, _normalize_key
from tests._broker_fixtures import FakeOlympTradeClient
from tests._scheduler_fixtures import RecordingNotifier


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


@pytest.fixture
def notifier() -> RecordingNotifier:
    return RecordingNotifier()


def test_constructor_rejects_empty_access_token() -> None:
    from signal_copier.notify.protocol import NoOpNotifier

    with pytest.raises(ValueError, match="access_token"):
        OlympTradeBroker(
            access_token="",
            account_id="12345",
            account_group="demo",
            notifier=NoOpNotifier(),
        )


def test_constructor_initializes_state(notifier: RecordingNotifier) -> None:
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


def test_constructor_stores_config(notifier: RecordingNotifier) -> None:
    broker = OlympTradeBroker(
        access_token="fake",
        account_id="99999",
        account_group="real",
        notifier=notifier,
    )
    assert broker._access_token == "fake"
    assert broker._account_id == "99999"
    assert broker._account_group == "real"


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
        _client_factory=lambda: fake_client,  # type: ignore[arg-type, return-value]
    )


async def _async_noop() -> None:
    return None


async def test_connect_is_idempotent(notifier: RecordingNotifier) -> None:
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
