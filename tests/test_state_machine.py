from __future__ import annotations

from decimal import Decimal

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
