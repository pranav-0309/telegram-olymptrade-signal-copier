from __future__ import annotations

from signal_copier.domain.signal import (
    ParsedSignal,
    parse_signal,
)

ALLOWED = frozenset({300})  # 5-minute only (v1 default per PRD §8)

VALID_MESSAGE = (
    "💰5-minute expiration\n"
    "EUR/JPY;10:20;PUT🟥\n"
    "🕛TIME UNTIL 10:25\n"
    "1st GALE -> TIME UNTIL 10:30\n"
    "2nd GALE - TIME UNTIL 10:35\n"
)

# --- Happy paths ----------------------------------------------------------


def test_happy_path_put_returns_parsed_signal() -> None:
    result = parse_signal(VALID_MESSAGE, allowed_expirations=ALLOWED)
    assert isinstance(result, ParsedSignal)
    assert result.pair == "EUR/JPY"
    assert result.direction == "down"
    assert result.trigger_hhmm == "10:20"
    assert result.expiration_seconds == 300
    assert result.gale1_hhmm == "10:25"
    assert result.gale2_hhmm == "10:30"


def test_happy_path_call_returns_parsed_signal_with_up_direction() -> None:
    msg = "💰5-minute expiration\n" "GBP/USD;14:30;CALL🟩\n" "🕛TIME UNTIL 14:35\n"
    result = parse_signal(msg, allowed_expirations=ALLOWED)
    assert isinstance(result, ParsedSignal)
    assert result.pair == "GBP/USD"
    assert result.direction == "up"
    assert result.trigger_hhmm == "14:30"
    assert result.expiration_seconds == 300
    assert result.gale1_hhmm == "14:35"
    assert result.gale2_hhmm == "14:40"
