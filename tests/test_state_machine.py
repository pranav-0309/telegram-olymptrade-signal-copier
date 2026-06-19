from __future__ import annotations

from decimal import Decimal

import pytest

from signal_copier.config import Config
from signal_copier.domain.signal import Signal
from signal_copier.domain.state import (
    FireEvent,
    ResultEvent,
    SignalState,
    transition,
)

# --- Helpers --------------------------------------------------------------

INITIAL_UNIX = 1_700_000_000.0  # 2023-11-14 22:13:20 UTC, an arbitrary Tuesday
GALE1_UNIX = INITIAL_UNIX + 300.0
GALE2_UNIX = INITIAL_UNIX + 600.0


def _config(**overrides) -> Config:
    return Config(
        _env_file=None,
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


# --- Placed_initial transitions ------------------------------------------


def _placed_initial_state() -> SignalState:
    state = _initial_state()
    r = transition(state, FireEvent(now_unix=INITIAL_UNIX), config=_config())
    assert r.success and r.new_state
    return r.new_state


def test_placed_initial_with_win_result_moves_to_done_win() -> None:
    state = _placed_initial_state()
    result = transition(
        state, ResultEvent(result="win", now_unix=INITIAL_UNIX + 60), config=_config()
    )
    assert result.success
    assert result.new_state.state == "done_win"
    assert result.new_state.stage is None
    assert result.new_state.result == "win"
    assert result.new_state.cumulative_pnl == Decimal("2.00") * Decimal("0.92")


def test_placed_initial_with_loss_result_moves_to_placed_gale1() -> None:
    state = _placed_initial_state()
    result = transition(
        state, ResultEvent(result="loss", now_unix=INITIAL_UNIX + 300), config=_config()
    )
    assert result.success
    assert result.new_state.state == "placed_gale1"
    assert result.new_state.stage == "gale1"
    assert result.new_state.amount == Decimal("4.00")
    assert result.new_state.trigger_unix == GALE1_UNIX
    assert result.new_state.cumulative_pnl == Decimal("-2.00")


def test_placed_initial_with_tie_result_moves_to_placed_gale1() -> None:
    """FR-5.3: tie at non-final stage is treated as loss for cascade purposes."""
    state = _placed_initial_state()
    result = transition(
        state, ResultEvent(result="tie", now_unix=INITIAL_UNIX + 300), config=_config()
    )
    assert result.success
    assert result.new_state.state == "placed_gale1"
    assert result.new_state.result == "tie"


def test_placed_initial_with_timeout_result_moves_to_placed_gale1() -> None:
    """FR-5.3: timeout at non-final stage is treated as loss for cascade purposes."""
    state = _placed_initial_state()
    result = transition(
        state, ResultEvent(result="timeout", now_unix=INITIAL_UNIX + 300), config=_config()
    )
    assert result.success
    assert result.new_state.state == "placed_gale1"
    assert result.new_state.result == "timeout"


def test_placed_initial_with_error_result_moves_to_error_state() -> None:
    state = _placed_initial_state()
    result = transition(
        state, ResultEvent(result="error", now_unix=INITIAL_UNIX + 60), config=_config()
    )
    assert result.success
    assert result.new_state.state == "error"
    assert result.new_state.error_reason == "broker_unavailable"


def test_placed_initial_with_fire_event_returns_invalid() -> None:
    state = _placed_initial_state()
    result = transition(state, FireEvent(now_unix=INITIAL_UNIX + 60), config=_config())
    assert result.success is False
    assert "invalid_event" in (result.reason or "")


# --- Placed_gale1 transitions --------------------------------------------


def _placed_gale1_state() -> SignalState:
    state = _placed_initial_state()
    r = transition(state, ResultEvent(result="loss", now_unix=INITIAL_UNIX + 300), config=_config())
    assert r.success and r.new_state
    return r.new_state


def test_placed_gale1_with_win_result_moves_to_done_win() -> None:
    state = _placed_gale1_state()
    result = transition(
        state, ResultEvent(result="win", now_unix=GALE1_UNIX + 60), config=_config()
    )
    assert result.success
    assert result.new_state.state == "done_win"
    assert result.new_state.cumulative_pnl == Decimal("1.68")


def test_placed_gale1_with_loss_result_moves_to_placed_gale2() -> None:
    state = _placed_gale1_state()
    result = transition(
        state, ResultEvent(result="loss", now_unix=GALE1_UNIX + 300), config=_config()
    )
    assert result.success
    assert result.new_state.state == "placed_gale2"
    assert result.new_state.stage == "gale2"
    assert result.new_state.amount == Decimal("8.00")
    assert result.new_state.trigger_unix == GALE2_UNIX
    assert result.new_state.cumulative_pnl == Decimal("-6.00")


def test_placed_gale1_with_tie_result_moves_to_placed_gale2() -> None:
    state = _placed_gale1_state()
    result = transition(
        state, ResultEvent(result="tie", now_unix=GALE1_UNIX + 60), config=_config()
    )
    assert result.success
    assert result.new_state.state == "placed_gale2"


def test_placed_gale1_with_fire_event_returns_invalid() -> None:
    state = _placed_gale1_state()
    result = transition(state, FireEvent(now_unix=GALE1_UNIX), config=_config())
    assert result.success is False


def test_placed_gale1_with_error_result_moves_to_error_state() -> None:
    state = _placed_gale1_state()
    result = transition(
        state, ResultEvent(result="error", now_unix=GALE1_UNIX + 60), config=_config()
    )
    assert result.success
    assert result.new_state.error_reason == "broker_unavailable"


# --- Placed_gale2 transitions --------------------------------------------


def _placed_gale2_state() -> SignalState:
    state = _placed_gale1_state()
    r = transition(state, ResultEvent(result="loss", now_unix=GALE1_UNIX + 300), config=_config())
    assert r.success and r.new_state
    return r.new_state


def test_placed_gale2_with_win_result_moves_to_done_win() -> None:
    state = _placed_gale2_state()
    result = transition(
        state, ResultEvent(result="win", now_unix=GALE2_UNIX + 60), config=_config()
    )
    assert result.success
    assert result.new_state.state == "done_win"
    assert result.new_state.cumulative_pnl == Decimal("1.36")


def test_placed_gale2_with_loss_result_moves_to_done_loss() -> None:
    state = _placed_gale2_state()
    result = transition(
        state, ResultEvent(result="loss", now_unix=GALE2_UNIX + 300), config=_config()
    )
    assert result.success
    assert result.new_state.state == "done_loss"
    assert result.new_state.cumulative_pnl == Decimal("-14.00")


def test_placed_gale2_with_tie_result_moves_to_done_loss() -> None:
    state = _placed_gale2_state()
    result = transition(
        state, ResultEvent(result="tie", now_unix=GALE2_UNIX + 60), config=_config()
    )
    assert result.success
    assert result.new_state.state == "done_loss"


def test_placed_gale2_with_timeout_result_moves_to_done_loss() -> None:
    state = _placed_gale2_state()
    result = transition(
        state, ResultEvent(result="timeout", now_unix=GALE2_UNIX + 330), config=_config()
    )
    assert result.success
    assert result.new_state.state == "done_loss"


def test_placed_gale2_with_fire_event_returns_invalid() -> None:
    state = _placed_gale2_state()
    result = transition(state, FireEvent(now_unix=GALE2_UNIX), config=_config())
    assert result.success is False


# --- Time-window enforcement on gale cascade -----------------------------


def test_placed_gale1_with_loss_result_past_gale2_window_ends_cascade() -> None:
    state = _placed_gale1_state()
    result = transition(
        state,
        ResultEvent(result="loss", now_unix=GALE2_UNIX + 100.0),
        config=_config(),
    )
    assert result.success
    assert result.new_state.state == "error"
    assert result.new_state.error_reason == "signal_expired"


def test_placed_initial_with_loss_result_past_gale1_window_ends_cascade() -> None:
    state = _placed_initial_state()
    result = transition(
        state,
        ResultEvent(result="loss", now_unix=GALE1_UNIX + 100.0),
        config=_config(),
    )
    assert result.success
    assert result.new_state.state == "error"
    assert result.new_state.error_reason == "signal_expired"


def test_placed_gale2_with_loss_result_at_gale2_window_boundary_succeeds() -> None:
    state = _placed_gale2_state()
    result = transition(
        state,
        ResultEvent(result="loss", now_unix=GALE2_UNIX + 100.0),
        config=_config(),
    )
    assert result.success
    assert result.new_state.state == "done_loss"


# --- Terminal states are absorbing ---------------------------------------


@pytest.mark.parametrize("terminal", ["done_win", "done_loss", "error"])
def test_terminal_states_reject_fire_event(terminal: str) -> None:
    """done_tie is excluded — reserved for v2 (D-7), unreachable in M2."""
    state = _initial_state()
    if terminal == "done_win":
        placed = transition(state, FireEvent(now_unix=INITIAL_UNIX), config=_config()).new_state
        r = transition(
            placed, ResultEvent(result="win", now_unix=INITIAL_UNIX + 60), config=_config()
        )
    elif terminal == "done_loss":
        s1 = transition(state, FireEvent(now_unix=INITIAL_UNIX), config=_config()).new_state
        s2 = transition(
            s1, ResultEvent(result="loss", now_unix=INITIAL_UNIX + 300), config=_config()
        ).new_state
        s3 = transition(
            s2, ResultEvent(result="loss", now_unix=GALE1_UNIX + 300), config=_config()
        ).new_state
        r = transition(s3, ResultEvent(result="loss", now_unix=GALE2_UNIX + 300), config=_config())
    else:  # error
        r = transition(state, FireEvent(now_unix=INITIAL_UNIX + 100), config=_config())
    final = r.new_state
    assert final is not None
    result = transition(final, FireEvent(now_unix=INITIAL_UNIX + 60), config=_config())
    assert result.success is False
    assert "invalid_event" in (result.reason or "")


# --- Full cascade tests (end-to-end through the state machine) ----------


def test_full_cascade_initial_win_path() -> None:
    s0 = _initial_state()
    s1 = transition(s0, FireEvent(now_unix=INITIAL_UNIX), config=_config()).new_state
    s2 = transition(
        s1, ResultEvent(result="win", now_unix=INITIAL_UNIX + 60), config=_config()
    ).new_state
    assert s2.state == "done_win"
    assert s2.cumulative_pnl == Decimal("1.84")


def test_full_cascade_gale1_win_path() -> None:
    s0 = _initial_state()
    s1 = transition(s0, FireEvent(now_unix=INITIAL_UNIX), config=_config()).new_state
    s2 = transition(
        s1, ResultEvent(result="loss", now_unix=INITIAL_UNIX + 300), config=_config()
    ).new_state
    s3 = transition(
        s2, ResultEvent(result="win", now_unix=GALE1_UNIX + 60), config=_config()
    ).new_state
    assert s3.state == "done_win"
    assert s3.cumulative_pnl == Decimal("1.68")


def test_full_cascade_gale2_win_path() -> None:
    s0 = _initial_state()
    s1 = transition(s0, FireEvent(now_unix=INITIAL_UNIX), config=_config()).new_state
    s2 = transition(
        s1, ResultEvent(result="loss", now_unix=INITIAL_UNIX + 300), config=_config()
    ).new_state
    s3 = transition(
        s2, ResultEvent(result="loss", now_unix=GALE1_UNIX + 300), config=_config()
    ).new_state
    s4 = transition(
        s3, ResultEvent(result="win", now_unix=GALE2_UNIX + 60), config=_config()
    ).new_state
    assert s4.state == "done_win"
    assert s4.cumulative_pnl == Decimal("1.36")


def test_full_cascade_full_loss_path() -> None:
    s0 = _initial_state()
    s1 = transition(s0, FireEvent(now_unix=INITIAL_UNIX), config=_config()).new_state
    s2 = transition(
        s1, ResultEvent(result="loss", now_unix=INITIAL_UNIX + 300), config=_config()
    ).new_state
    s3 = transition(
        s2, ResultEvent(result="loss", now_unix=GALE1_UNIX + 300), config=_config()
    ).new_state
    s4 = transition(
        s3, ResultEvent(result="loss", now_unix=GALE2_UNIX + 300), config=_config()
    ).new_state
    assert s4.state == "done_loss"
    assert s4.cumulative_pnl == Decimal("-14.00")


def test_full_cascade_signal_expired_at_initial() -> None:
    s0 = _initial_state()
    s1 = transition(s0, FireEvent(now_unix=INITIAL_UNIX + 100), config=_config()).new_state
    assert s1.state == "error"
    assert s1.error_reason == "signal_expired"


def test_full_cascade_signal_expired_at_gale1() -> None:
    s0 = _initial_state()
    s1 = transition(s0, FireEvent(now_unix=INITIAL_UNIX), config=_config()).new_state
    s2 = transition(
        s1,
        ResultEvent(result="loss", now_unix=GALE1_UNIX + 100),
        config=_config(),
    ).new_state
    assert s2.state == "error"
    assert s2.error_reason == "signal_expired"


def test_full_cascade_signal_expired_at_gale2() -> None:
    s0 = _initial_state()
    s1 = transition(s0, FireEvent(now_unix=INITIAL_UNIX), config=_config()).new_state
    s2 = transition(
        s1, ResultEvent(result="loss", now_unix=INITIAL_UNIX + 300), config=_config()
    ).new_state
    s3 = transition(
        s2,
        ResultEvent(result="loss", now_unix=GALE2_UNIX + 100),
        config=_config(),
    ).new_state
    assert s3.state == "error"
    assert s3.error_reason == "signal_expired"
