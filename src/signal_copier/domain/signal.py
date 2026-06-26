from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Final, Literal

# --- Failure reason enum ----------------------------------------------------


class FailureReason(StrEnum):
    MISSING_HEADER_LINE = "missing_header_line"
    MISSING_SIGNAL_LINE = "missing_signal_line"
    MULTIPLE_SIGNAL_LINES = "multiple_signal_lines"
    BAD_PAIR_FORMAT = "bad_pair_format"
    BAD_TIME_FORMAT = "bad_time_format"
    BAD_DIRECTION = "bad_direction"
    EXPIRATION_NOT_ALLOWED = "expiration_not_allowed"


# --- Success dataclass: what parse_signal returns when it matches ----------


@dataclass(frozen=True, slots=True)
class ParsedSignal:
    pair: str  # "EUR/JPY"
    direction: Literal["up", "down"]
    trigger_hhmm: str  # "10:20"
    expiration_seconds: int  # 300 (i.e. 5 minutes)
    gale1_hhmm: str  # "10:25" (trigger + 5 min, wraps midnight)
    gale2_hhmm: str  # "10:30" (trigger + 10 min, wraps midnight)


# --- Full dataclass: PRD FR-2.5; constructed by M5 listener ---------------


@dataclass(frozen=True, slots=True)
class Signal:
    signal_id: str  # sha1(pair|trigger_hhmm|direction|date)[:12]
    pair: str
    direction: Literal["up", "down"]
    trigger_hhmm: str
    expiration_seconds: int
    received_at_unix: float
    source_message_id: int
    source_chat_id: int
    raw_text: str
    # --- Added in M2 (D-5) ---
    trigger_unix_initial: float  # epoch for trigger_hhmm on the signal's date in config TZ
    trigger_unix_gale1: float  # trigger_unix_initial + expiration_seconds
    trigger_unix_gale2: float  # trigger_unix_initial + 2 * expiration_seconds


# --- Failure dataclass + tagged union -------------------------------------


@dataclass(frozen=True, slots=True)
class ParseFailure:
    reason: FailureReason
    raw_text: str  # echo for FR-7.1 parse-failure DM (PRD §4.7)


ParseResult = ParsedSignal | ParseFailure


# --- Module-level compiled regexes (defined once at import time) -----------

_HEADER_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*\U0001f4b0(?P<N>\d+)-minute expiration\s*$",
    re.MULTILINE,
)

# Signal line pattern. Tolerances documented in the planner notes:
#   - whitespace around the `;` separators (analyst uses both `;` and ` ; `)
#   - whitespace between the direction word and the direction emoji
#     (analyst uses both `PUT🟥` and `PUT 🟥`; the latter was the
#     production failure that motivated this fix)
#   - alternative direction emojis (circle, triangle) in addition to
#     the documented square. Grouped in a character class so each
#     direction accepts any of the three acceptable markers.
_SIGNAL_LINE_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<pair>[A-Z]{3}/[A-Z]{3})\s*;\s*"
    r"(?P<time>\d{2}:\d{2})\s*;\s*"
    r"(?P<dir>"
    r"PUT\s*[\U0001f534\U0001f7e5\U0001f53b]|"
    r"CALL\s*[\U0001f7e9\U0001f7e2\U0001f53a]"
    r")\s*$",
    re.MULTILINE,
)

_BOM: Final[str] = "\ufeff"


# --- Helpers ---------------------------------------------------------------


def _add_minutes(hhmm: str, minutes: int) -> str:
    """Add minutes to an HH:MM string, wrapping midnight. Returns HH:MM."""
    hour, mins = (int(x) for x in hhmm.split(":"))
    total = (hour * 60 + mins + minutes) % (24 * 60)
    new_hour, new_mins = divmod(total, 60)
    return f"{new_hour:02d}:{new_mins:02d}"


# --- Public API ------------------------------------------------------------


def parse_signal(
    raw_text: str,
    *,
    allowed_expirations: frozenset[int],
) -> ParseResult:
    text = raw_text.strip(_BOM)  # tolerate leading or trailing UTF-8 BOM

    # 1. Find and validate the expiration header line
    header_match = _HEADER_RE.search(text)
    if header_match is None:
        return ParseFailure(FailureReason.MISSING_HEADER_LINE, raw_text)
    expiration_seconds = int(header_match.group("N")) * 60
    if expiration_seconds not in allowed_expirations:
        return ParseFailure(FailureReason.EXPIRATION_NOT_ALLOWED, raw_text)

    # 2. Find the signal line (exactly one expected)
    signal_matches = list(_SIGNAL_LINE_RE.finditer(text))
    if len(signal_matches) == 0:
        return ParseFailure(FailureReason.MISSING_SIGNAL_LINE, raw_text)
    if len(signal_matches) > 1:
        return ParseFailure(FailureReason.MULTIPLE_SIGNAL_LINES, raw_text)
    sig = signal_matches[0]

    # 3. Extract fields. The regex enforces shape; we still validate ranges.
    pair: str = sig.group("pair")
    trigger_hhmm: str = sig.group("time")
    direction_str: str = sig.group("dir")

    # Range-check the time (regex only enforces \d{2}:\d{2})
    hour, minute = (int(x) for x in trigger_hhmm.split(":"))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return ParseFailure(FailureReason.BAD_TIME_FORMAT, raw_text)

    # Map PUT/CALL to direction. The direction_str starts with the
    # word and is followed by one of the accepted direction emojis
    # (see _SIGNAL_LINE_RE above). We check the leading word, not an
    # exact string match, so all three PUT-emoji variants and all
    # three CALL-emoji variants route correctly.
    if direction_str.startswith("PUT"):
        direction: Literal["up", "down"] = "down"
    elif direction_str.startswith("CALL"):
        direction = "up"
    else:  # pragma: no cover — regex makes this unreachable
        return ParseFailure(FailureReason.BAD_DIRECTION, raw_text)

    return ParsedSignal(
        pair=pair,
        direction=direction,
        trigger_hhmm=trigger_hhmm,
        expiration_seconds=expiration_seconds,
        gale1_hhmm=_add_minutes(trigger_hhmm, 5),
        gale2_hhmm=_add_minutes(trigger_hhmm, 10),
    )


def derive_signal_id(
    parsed: ParsedSignal,
    *,
    signal_date: date,  # date in the configured TZ, not UTC
) -> str:
    """Deterministic ID per (pair, trigger_hhmm, direction, date).

    Identical signals arriving twice in the same day collapse to the same
    signal_id, which the M4 StateStore uses as the signals.signal_id PK
    with ON CONFLICT DO NOTHING for idempotency.
    """
    payload = f"{parsed.pair}|{parsed.trigger_hhmm}|{parsed.direction}|{signal_date.isoformat()}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
