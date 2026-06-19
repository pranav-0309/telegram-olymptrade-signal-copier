from __future__ import annotations

import pytest

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


# --- Whitespace tolerance -------------------------------------------------


def test_signal_line_with_trailing_whitespace_still_parses() -> None:
    msg = "💰5-minute expiration\nEUR/JPY;10:20;PUT🟥   \n"
    result = parse_signal(msg, allowed_expirations=ALLOWED)
    assert isinstance(result, ParsedSignal)
    assert result.pair == "EUR/JPY"


def test_message_with_leading_blank_lines_parses() -> None:
    msg = "\n\n💰5-minute expiration\nEUR/JPY;10:20;PUT🟥\n"
    result = parse_signal(msg, allowed_expirations=ALLOWED)
    assert isinstance(result, ParsedSignal)
    assert result.pair == "EUR/JPY"


def test_message_with_trailing_blank_lines_parses() -> None:
    msg = "💰5-minute expiration\nEUR/JPY;10:20;PUT🟥\n\n\n"
    result = parse_signal(msg, allowed_expirations=ALLOWED)
    assert isinstance(result, ParsedSignal)
    assert result.pair == "EUR/JPY"


def test_message_with_internal_blank_lines_parses() -> None:
    msg = "💰5-minute expiration\n" "\n" "EUR/JPY;10:20;PUT🟥\n" "\n" "🕛TIME UNTIL 10:25\n"
    result = parse_signal(msg, allowed_expirations=ALLOWED)
    assert isinstance(result, ParsedSignal)
    assert result.pair == "EUR/JPY"


# --- BOM tolerance --------------------------------------------------------


def test_leading_utf8_bom_is_stripped() -> None:
    msg = "\ufeff💰5-minute expiration\nEUR/JPY;10:20;PUT🟥\n"
    result = parse_signal(msg, allowed_expirations=ALLOWED)
    assert isinstance(result, ParsedSignal)
    assert result.pair == "EUR/JPY"


def test_trailing_utf8_bom_is_stripped() -> None:
    msg = "💰5-minute expiration\nEUR/JPY;10:20;PUT🟥\n\ufeff"
    result = parse_signal(msg, allowed_expirations=ALLOWED)
    assert isinstance(result, ParsedSignal)
    assert result.pair == "EUR/JPY"


# --- Gale arithmetic ------------------------------------------------------


@pytest.mark.parametrize(
    ("trigger", "gale1", "gale2"),
    [
        ("10:20", "10:25", "10:30"),  # normal
        ("00:00", "00:05", "00:10"),  # midnight start
        ("23:55", "00:00", "00:05"),  # wraps midnight
        ("23:58", "00:03", "00:08"),  # wraps with non-zero carry
    ],
)
def test_gale_times_are_arithmetic_with_midnight_wrap(
    trigger: str,
    gale1: str,
    gale2: str,
) -> None:
    msg = f"💰5-minute expiration\nEUR/JPY;{trigger};PUT🟥\n"
    result = parse_signal(msg, allowed_expirations=ALLOWED)
    assert isinstance(result, ParsedSignal)
    assert result.trigger_hhmm == trigger
    assert result.gale1_hhmm == gale1
    assert result.gale2_hhmm == gale2


def test_add_minutes_at_exactly_midnight_returns_zero_hour() -> None:
    # Edge case: trigger exactly at 00:00 → gales also at 00:05 / 00:10.
    msg = "💰5-minute expiration\nUSD/CAD;00:00;CALL🟩\n"
    result = parse_signal(msg, allowed_expirations=ALLOWED)
    assert isinstance(result, ParsedSignal)
    assert result.trigger_hhmm == "00:00"
    assert result.gale1_hhmm == "00:05"
    assert result.gale2_hhmm == "00:10"
