from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, cast

from signal_copier.config import Config
from signal_copier.domain.gale import Stage, amount_for_stage
from signal_copier.domain.signal import Signal

# --- Top-level signal state machine states (PRD FR-5.1) ------------------

# Pre-terminal: the cascade is in flight.
State = Literal[
    "pending",  # signal received, not yet fired
    "placed_initial",  # initial trade placed, awaiting result
    "placed_gale1",  # gale1 trade placed, awaiting result
    "placed_gale2",  # gale2 trade placed, awaiting result
]

# Terminal: cascade complete.
# Per D-7: done_tie is reserved for v2 (unreachable in M2's transitions).
# done_win / done_loss are the only signal-level terminals in v1.
# error carries error_reason for signal_expired / broker_unavailable / unknown.
TerminalState = Literal[
    "done_win",
    "done_loss",
    "done_tie",  # reserved for v2; M2 transitions never reach this
    "error",
]
AllStates = State | TerminalState


# Per-stage result (recorded in SignalState.result, not the top-level state).
# Tie and timeout at a non-final stage are treated as LOSS for cascade purposes (FR-5.3);
# the original outcome is still recorded here for observability.
StageResult = Literal["win", "loss", "tie", "timeout", "error"]


# --- Error reason enum (subset of PRD §9.1 + FR-5.1) ---------------------

ErrorReason = Literal[
    "signal_expired",  # FR-3.3 / FR-3.5 / FR-3.6 / FR-5.9: time window passed
    "broker_unavailable",  # FR-4.4: broker dropped / token expired
    "daily_limit_hit",  # M6 D-2: DAILY_LOSS_LIMIT/TRADE_LIMIT/DRAWDOWN_PCT tripped
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


Event = FireEvent | ResultEvent


# --- The state value (frozen, replaceable) --------------------------------


@dataclass(frozen=True, slots=True)
class SignalState:
    signal_id: str
    pair: str
    direction: Literal["up", "down"]
    state: AllStates  # one of the State / TerminalState values
    stage: Stage | None  # current stage; None at terminal states
    amount: Decimal  # bet amount for the current stage
    trigger_unix: float  # trigger time for the current stage
    expires_at_unix: float  # trigger_unix + expiration_seconds
    result: StageResult | None  # last stage result (None until first result)
    cumulative_pnl: Decimal  # sum of stage PnLs (signed; losses negative)
    error_reason: ErrorReason | None  # populated only when state == "error"

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


# --- Transition result ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class TransitionResult:
    """Outcome of a transition attempt.

    Always returns a value — never raises. Invalid events return
    success=False with a reason. The caller (M6) decides what to do.
    """

    success: bool
    new_state: SignalState | None  # set on success; None on failure
    reason: str | None  # set on failure; human-readable


# --- Pure transition function (D-1) --------------------------------------


def _check_time_window(
    state: SignalState,
    now_unix: float,
    tolerance: float,
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
    state: SignalState,
    next_stage: Stage,
    result: StageResult,
    cumulative: Decimal,
    config: Config,
) -> SignalState:
    """Move from a placed_X state to the next placed_X state after a loss.

    Each gale stage fires 5 minutes after the previous stage's trigger.
    gale1 fires 5min after initial; gale2 fires 5min after gale1.
    This couples M2 to the 5-minute expiration — see Risk #10.
    """
    trigger_unix = state.trigger_unix + 5 * 60  # always 5min after current stage
    next_state: AllStates = cast(AllStates, f"placed_{next_stage}")
    return SignalState(
        signal_id=state.signal_id,
        pair=state.pair,
        direction=state.direction,
        state=next_state,
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
    return state.trigger_unix  # gale2 has no next stage  # pragma: no cover


def _to_terminal(
    state: SignalState,
    terminal: TerminalState,
    result: StageResult,
    cumulative: Decimal,
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
    state: SignalState,
    reason: ErrorReason,
    result: StageResult | None,
    cumulative: Decimal,
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
    state: SignalState,
    result: StageResult,
    now_unix: float,
    config: Config,
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
    state: SignalState,
    event: Event,
    *,
    config: Config,
) -> TransitionResult:
    """Apply an event to the current state. Returns the new state (or failure).

    This is a pure function. Same (state, event, config) → same result.
    """
    # Terminal states are absorbing — no further events accepted.
    if state.state in {"done_win", "done_loss", "done_tie", "error"}:
        return TransitionResult(
            success=False,
            new_state=None,
            reason=f"invalid_event: state is terminal ({state.state}); event {event} ignored",
        )

    # FireEvent: try to fire the current stage's trade.
    if isinstance(event, FireEvent):
        # placed_* states cannot be re-fired — they're awaiting result.
        if state.state != "pending":
            return TransitionResult(
                success=False,
                new_state=None,
                reason=(
                    f"invalid_event: FireEvent on placed state ({state.state}); record_result first"
                ),
            )
        # Pre-fire guard (FR-3.5 / FR-3.6 / FR-5.9): every stage's window matters.
        if _check_time_window(state, event.now_unix, config.trigger_skew_tolerance_seconds):
            new_state = _to_error(state, "signal_expired", None, state.cumulative_pnl)
            return TransitionResult(success=True, new_state=new_state, reason=None)
        if state.stage == "initial":
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
        # Catch-all: pending but not initial — shouldn't happen in v1.
        return TransitionResult(  # pragma: no cover
            success=False,
            new_state=None,
            reason=f"invalid_event: FireEvent on pending state with stage {state.stage}",
        )

    # ResultEvent: apply result to the current placed stage.
    if isinstance(event, ResultEvent):
        if state.state not in {"placed_initial", "placed_gale1", "placed_gale2"}:
            return TransitionResult(
                success=False,
                new_state=None,
                reason=f"invalid_event: ResultEvent on non-placed state ({state.state})",
            )
        new_state = _advance_after_result(state, event.result, event.now_unix, config)
        return TransitionResult(success=True, new_state=new_state, reason=None)

    return TransitionResult(
        success=False, new_state=None, reason="unknown event type"
    )  # pragma: no cover
