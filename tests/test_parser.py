from __future__ import annotations

from datetime import date

import pytest

from signal_copier.domain.signal import (
    FailureReason,
    ParsedSignal,
    ParseFailure,
    derive_signal_id,
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
    msg = "💰5-minute expiration\nGBP/USD;14:30;CALL🟩\n🕛TIME UNTIL 14:35\n"
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


# --- Direction/emoji whitespace tolerance (new) ------------------------


def test_signal_with_space_between_put_and_emoji_parses() -> None:
    """The actual missed signal from 2026-06-26 19:33:29 UTC.
    Format: 'USD/IDR; 16:35; PUT 🟥' — has SPACE between PUT and 🟥.
    This was the production failure: header matched, signal line did not.
    """
    msg = (
        "💰5-minute expiration\n"
        "USD/IDR; 16:35; PUT 🟥\n"
        "🕐 TIME TO 16:40\n"
        "1st GALE —> TIME TO\n"
    )
    result = parse_signal(msg, allowed_expirations=ALLOWED)
    assert isinstance(result, ParsedSignal)
    assert result.pair == "USD/IDR"
    assert result.direction == "down"
    assert result.trigger_hhmm == "16:35"
    assert result.expiration_seconds == 300
    assert result.gale1_hhmm == "16:40"
    assert result.gale2_hhmm == "16:45"


def test_signal_with_space_between_call_and_emoji_parses() -> None:
    msg = "💰5-minute expiration\nGBP/USD; 14:30; CALL 🟩\n"
    result = parse_signal(msg, allowed_expirations=ALLOWED)
    assert isinstance(result, ParsedSignal)
    assert result.direction == "up"
    assert result.trigger_hhmm == "14:30"


def test_signal_with_multiple_spaces_between_direction_and_emoji_parses() -> None:
    msg = "💰5-minute expiration\nEUR/JPY;10:20;PUT  🟥\n"
    result = parse_signal(msg, allowed_expirations=ALLOWED)
    assert isinstance(result, ParsedSignal)
    assert result.direction == "down"


# --- Whitespace around semicolons (new) ---------------------------------


def test_signal_with_whitespace_around_semicolons_parses() -> None:
    """Whitespace BEFORE/AFTER the ; separators (analyst does this)."""
    msg = "💰5-minute expiration\nEUR/JPY ; 10:20 ; PUT🟥\n"
    result = parse_signal(msg, allowed_expirations=ALLOWED)
    assert isinstance(result, ParsedSignal)
    assert result.pair == "EUR/JPY"
    assert result.trigger_hhmm == "10:20"


def test_signal_with_combined_whitespace_parses() -> None:
    """Whitespace everywhere: separators AND direction/emoji."""
    msg = "💰5-minute expiration\nUSD/IDR ; 16:35 ; PUT 🟥\n"
    result = parse_signal(msg, allowed_expirations=ALLOWED)
    assert isinstance(result, ParsedSignal)
    assert result.pair == "USD/IDR"
    assert result.trigger_hhmm == "16:35"
    assert result.direction == "down"


# --- Alternative emoji variants (new) -----------------------------------


def test_signal_with_red_circle_emoji_parses() -> None:
    """🔴 (U+1F534) LARGE RED CIRCLE — analyst uses this sometimes."""
    msg = "💰5-minute expiration\nCAD/CHF;12:05;PUT🔴\n"
    result = parse_signal(msg, allowed_expirations=ALLOWED)
    assert isinstance(result, ParsedSignal)
    assert result.direction == "down"


def test_signal_with_green_circle_emoji_parses() -> None:
    """🟢 (U+1F7E6) LARGE GREEN CIRCLE."""
    msg = "💰5-minute expiration\nCAD/CHF;12:05;CALL🟢\n"
    result = parse_signal(msg, allowed_expirations=ALLOWED)
    assert isinstance(result, ParsedSignal)
    assert result.direction == "up"


def test_signal_with_red_triangle_emoji_parses() -> None:
    """🔻 (U+1F53B) DOWN-POINTING RED TRIANGLE."""
    msg = "💰5-minute expiration\nEUR/JPY;10:20;PUT🔻\n"
    result = parse_signal(msg, allowed_expirations=ALLOWED)
    assert isinstance(result, ParsedSignal)
    assert result.direction == "down"


def test_signal_with_green_triangle_emoji_parses() -> None:
    """🔺 (U+1F53C) UP-POINTING RED TRIANGLE — used for CALL direction."""
    msg = "💰5-minute expiration\nEUR/JPY;10:20;CALL🔺\n"
    result = parse_signal(msg, allowed_expirations=ALLOWED)
    assert isinstance(result, ParsedSignal)
    assert result.direction == "up"


def test_message_with_leading_blank_lines_parses() -> None:
    msg = "\n\n💰5-minute expiration\nEUR/JPY;10:20;PUT🟥\n"
    result = parse_signal(msg, allowed_expirations=ALLOWED)
    assert isinstance(result, ParsedSignal)
    assert result.pair == "EUR/JPY"


# --- Invisible-character defense (new) ----------------------------------
# Some Telegram clients / signal-generation tools inject invisible Unicode
# characters after emojis (ZWJ U+200D, VS16 U+FE0F, ZWSP U+200B, word
# joiner U+2060). These are invisible in render but break our ^...$
# anchored regex because the extra character makes the line not end with
# the expected character class.
#
# Production reference: 2026-06-26 20:38:29 UTC failure where the
# exact same signal text was rejected as "missing_signal_line".


def test_signal_with_zwj_after_emoji_parses() -> None:
    """U+200D (ZWJ) after the direction emoji — invisible in render,
    breaks the $ anchor."""
    msg = "💰5-minute expiration\nUSD/IDR; 17:40; PUT 🟥\u200d\n"
    result = parse_signal(msg, allowed_expirations=ALLOWED)
    assert isinstance(result, ParsedSignal)
    assert result.pair == "USD/IDR"
    assert result.direction == "down"


def test_signal_with_vs16_after_emoji_parses() -> None:
    """U+FE0F (Variation Selector-16) after the emoji — added by some
    clients to request emoji presentation. Highest-likelihood production
    culprit per investigation 2026-06-27."""
    msg = "💰5-minute expiration\nUSD/IDR; 17:40; PUT 🟥\ufe0f\n"
    result = parse_signal(msg, allowed_expirations=ALLOWED)
    assert isinstance(result, ParsedSignal)
    assert result.pair == "USD/IDR"
    assert result.direction == "down"


def test_signal_with_zwsp_after_emoji_parses() -> None:
    """U+200B (ZWSP) after the emoji — invisible in render."""
    msg = "💰5-minute expiration\nUSD/IDR; 17:40; PUT 🟥\u200b\n"
    result = parse_signal(msg, allowed_expirations=ALLOWED)
    assert isinstance(result, ParsedSignal)
    assert result.pair == "USD/IDR"


def test_signal_with_word_joiner_after_emoji_parses() -> None:
    """U+2060 (Word Joiner) after the emoji — invisible in render."""
    msg = "💰5-minute expiration\nUSD/IDR; 17:40; PUT 🟥\u2060\n"
    result = parse_signal(msg, allowed_expirations=ALLOWED)
    assert isinstance(result, ParsedSignal)
    assert result.pair == "USD/IDR"


def test_message_with_trailing_blank_lines_parses() -> None:
    msg = "💰5-minute expiration\nEUR/JPY;10:20;PUT🟥\n\n\n"
    result = parse_signal(msg, allowed_expirations=ALLOWED)
    assert isinstance(result, ParsedSignal)
    assert result.pair == "EUR/JPY"


def test_message_with_internal_blank_lines_parses() -> None:
    msg = "💰5-minute expiration\n\nEUR/JPY;10:20;PUT🟥\n\n🕛TIME UNTIL 10:25\n"
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


# --- Missing / malformed fields ------------------------------------------


def test_missing_header_line_returns_missing_header_failure() -> None:
    msg = "EUR/JPY;10:20;PUT🟥\n"  # no 💰 header
    result = parse_signal(msg, allowed_expirations=ALLOWED)
    assert isinstance(result, ParseFailure)
    assert result.reason == FailureReason.MISSING_HEADER_LINE


def test_missing_signal_line_returns_missing_signal_failure() -> None:
    msg = "💰5-minute expiration\n🕛TIME UNTIL 10:25\n"  # header but no signal
    result = parse_signal(msg, allowed_expirations=ALLOWED)
    assert isinstance(result, ParseFailure)
    assert result.reason == FailureReason.MISSING_SIGNAL_LINE


def test_multiple_signal_lines_returns_multiple_signal_lines_failure() -> None:
    msg = "💰5-minute expiration\nEUR/JPY;10:20;PUT🟥\nGBP/USD;11:30;CALL🟩\n"
    result = parse_signal(msg, allowed_expirations=ALLOWED)
    assert isinstance(result, ParseFailure)
    assert result.reason == FailureReason.MULTIPLE_SIGNAL_LINES


def test_message_with_no_semicolon_in_signal_returns_missing_signal_failure() -> None:
    msg = "💰5-minute expiration\nEUR/JPY 10:20 PUT🟥\n"
    result = parse_signal(msg, allowed_expirations=ALLOWED)
    assert isinstance(result, ParseFailure)
    assert result.reason == FailureReason.MISSING_SIGNAL_LINE


def test_message_with_wrong_emoji_direction_returns_missing_signal_failure() -> None:
    # 🚀 (U+1F680) — definitely not a direction marker
    msg = "💰5-minute expiration\nEUR/JPY;10:20;PUT🚀\n"
    result = parse_signal(msg, allowed_expirations=ALLOWED)
    assert isinstance(result, ParseFailure)
    assert result.reason == FailureReason.MISSING_SIGNAL_LINE


def test_lowercase_pair_returns_missing_signal_failure() -> None:
    msg = "💰5-minute expiration\n eur/jpy ;10:20;PUT🟥\n"
    result = parse_signal(msg, allowed_expirations=ALLOWED)
    assert isinstance(result, ParseFailure)
    assert result.reason == FailureReason.MISSING_SIGNAL_LINE


def test_pair_without_slash_returns_missing_signal_failure() -> None:
    msg = "💰5-minute expiration\nEURJPY;10:20;PUT🟥\n"
    result = parse_signal(msg, allowed_expirations=ALLOWED)
    assert isinstance(result, ParseFailure)
    assert result.reason == FailureReason.MISSING_SIGNAL_LINE


def test_invalid_hour_25_returns_bad_time_failure() -> None:
    msg = "💰5-minute expiration\nEUR/JPY;25:00;PUT🟥\n"
    result = parse_signal(msg, allowed_expirations=ALLOWED)
    assert isinstance(result, ParseFailure)
    assert result.reason == FailureReason.BAD_TIME_FORMAT


def test_invalid_minute_60_returns_bad_time_failure() -> None:
    msg = "💰5-minute expiration\nEUR/JPY;10:60;PUT🟥\n"
    result = parse_signal(msg, allowed_expirations=ALLOWED)
    assert isinstance(result, ParseFailure)
    assert result.reason == FailureReason.BAD_TIME_FORMAT


# --- Expiration validation ------------------------------------------------


def test_header_with_disallowed_expiration_returns_expiration_not_allowed_failure() -> None:
    # 3-minute expiration not in {300}
    msg = "💰3-minute expiration\nEUR/JPY;10:20;PUT🟥\n"
    result = parse_signal(msg, allowed_expirations=ALLOWED)
    assert isinstance(result, ParseFailure)
    assert result.reason == FailureReason.EXPIRATION_NOT_ALLOWED


def test_header_with_5_minute_expiration_is_accepted_with_allowed_300() -> None:
    msg = "💰5-minute expiration\nEUR/JPY;10:20;PUT🟥\n"
    result = parse_signal(msg, allowed_expirations=frozenset({300}))
    assert isinstance(result, ParsedSignal)
    assert result.expiration_seconds == 300


def test_header_with_5_minute_expiration_is_accepted_with_allowed_set_including_300() -> None:
    msg = "💰5-minute expiration\nEUR/JPY;10:20;PUT🟥\n"
    # Multiple allowed expirations; 5-minute is one of them.
    result = parse_signal(msg, allowed_expirations=frozenset({60, 300, 600}))
    assert isinstance(result, ParsedSignal)
    assert result.expiration_seconds == 300


# --- Ad-only / non-signal messages ---------------------------------------


def test_typical_ad_message_returns_missing_header_failure() -> None:
    msg = (
        "🔥 HOT SIGNAL 🔥\nJoin our VIP channel for exclusive trades!\n💎 Limited spots available\n"
    )
    result = parse_signal(msg, allowed_expirations=ALLOWED)
    assert isinstance(result, ParseFailure)
    assert result.reason == FailureReason.MISSING_HEADER_LINE


def test_message_with_only_gale_lines_returns_missing_header_failure() -> None:
    msg = "🕛TIME UNTIL 10:25\n1st GALE -> TIME UNTIL 10:30\n2nd GALE - TIME UNTIL 10:35\n"
    result = parse_signal(msg, allowed_expirations=ALLOWED)
    assert isinstance(result, ParseFailure)
    assert result.reason == FailureReason.MISSING_HEADER_LINE


def test_empty_message_returns_missing_header_failure() -> None:
    result = parse_signal("", allowed_expirations=ALLOWED)
    assert isinstance(result, ParseFailure)
    assert result.reason == FailureReason.MISSING_HEADER_LINE


def test_whitespace_only_message_returns_missing_header_failure() -> None:
    result = parse_signal("   \n\n\t\n", allowed_expirations=ALLOWED)
    assert isinstance(result, ParseFailure)
    assert result.reason == FailureReason.MISSING_HEADER_LINE


# --- ParseFailure echo + signal_id derivation ----------------------------


def test_parse_failure_preserves_original_raw_text() -> None:
    msg = "💰3-minute expiration\nEUR/JPY;10:20;PUT🟥\n"  # 3-min = disallowed
    result = parse_signal(msg, allowed_expirations=ALLOWED)
    assert isinstance(result, ParseFailure)
    assert result.raw_text == msg


def test_derive_signal_id_is_deterministic_per_day() -> None:
    parsed = ParsedSignal(
        pair="EUR/JPY",
        direction="down",
        trigger_hhmm="10:20",
        expiration_seconds=300,
        gale1_hhmm="10:25",
        gale2_hhmm="10:30",
    )
    sig_id_1 = derive_signal_id(parsed, signal_date=date(2026, 6, 19))
    sig_id_2 = derive_signal_id(parsed, signal_date=date(2026, 6, 19))
    assert sig_id_1 == sig_id_2
    assert len(sig_id_1) == 12  # SHA-1 truncated to 12 hex chars


def test_derive_signal_id_differs_across_days() -> None:
    parsed = ParsedSignal(
        pair="EUR/JPY",
        direction="down",
        trigger_hhmm="10:20",
        expiration_seconds=300,
        gale1_hhmm="10:25",
        gale2_hhmm="10:30",
    )
    sig_id_day1 = derive_signal_id(parsed, signal_date=date(2026, 6, 19))
    sig_id_day2 = derive_signal_id(parsed, signal_date=date(2026, 6, 20))
    assert sig_id_day1 != sig_id_day2
