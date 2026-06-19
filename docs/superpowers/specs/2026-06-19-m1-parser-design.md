# M1 — Signal Parser & Domain Types Design

**Date:** 2026-06-19
**Status:** Approved — design sections (§1–§7) reviewed by user; §8–§11 (risks, out-of-scope, decisions, transition) are reference material derived from the design.
**PRD reference:** `docs/PRD.md` v0.7 (§4.2 FR-2.1–2.5, §15 M1 row, §4.7 FR-7.1 parse-failure row)
**Build plan reference:** PRD §15, M1 row

---

## 1. Purpose & Scope

M1 is the second milestone of the Telegram → OlympTrade Signal Copier (PRD v0.7). It ships the message-format parser and the in-memory domain types that every downstream milestone (M2 state machine, M4 DB, M5 listener, M6 scheduler, M8 broker) will import.

**M1 ships a pure, fully-tested parser plus the `Signal` / `ParsedSignal` value types — no I/O, no clock, no broker, no Telethon.**

**In scope for M1 (3 new files):**

1. `src/signal_copier/domain/signal.py` — `FailureReason` enum, `ParsedSignal` dataclass, `Signal` dataclass, `ParseFailure` dataclass, `ParseResult` type alias, `parse_signal()` function, `derive_signal_id()` helper, `_add_minutes()` private helper, two module-level compiled regexes
2. `src/signal_copier/domain/__init__.py` — re-export the public API
3. `tests/test_parser.py` — pytest suite of ~27 tests covering happy paths, gale arithmetic, whitespace/BOM tolerance, all 7 failure reasons, and `derive_signal_id()`

**Out of scope (deferred to later milestones):**

| Concern | Lands in |
|---|---|
| Reading `.env` / pydantic-settings config | M2 (config layer) or M5 (listener wires from config) |
| Timezone conversion (`trigger_hhmm` → epoch) | M5 (sets `received_at_unix`) + M6 (scheduler) |
| Pair-availability check on broker | M8 (auto-discover via e:1068 push) |
| Trigger-time window check (±1 min past / +30 min future) | M5 (listener has clock) |
| Constructing `Signal` instances (wrapping `ParsedSignal`) | M5 |
| Telegram event handling (edits, flood waits, reconnects) | M5 |
| Real `__main__.py` wiring | M6 |

**What M1's parser validates (per FR-2.2 + FR-2.3 structural subset):**

- Message structure (signal line matches anchored regex)
- Header line `💰N-minute expiration` exists, and `N * 60 ∈ allowed_expirations`
- HH:MM is well-formed and in range (hour 0–23, minute 0–59)
- Pair matches `[A-Z]{3}/[A-Z]{3}` (strict)
- Direction is exactly `PUT🟥` or `CALL🟩`
- Tolerates: trailing UTF-8 BOM (leading or trailing), varying whitespace, blank lines, trailing newlines

**What M1's parser does NOT validate (deferred):**

- Pair availability on broker (M8)
- Time relative to clock (past/future) (M5)

---

## 2. Module Structure & Dependencies

**Files to create (3 new):**

```
src/signal_copier/
├── domain/
│   ├── __init__.py             # NEW — re-exports public API
│   └── signal.py               # NEW — all signal-shaped types + parser
tests/
└── test_parser.py              # NEW — pytest suite for parse_signal + derive_signal_id
```

**No new dependencies.** Plain stdlib `@dataclass(frozen=True)` is sufficient — `Signal` and `ParsedSignal` are pure value types with no validation that needs a library. `pydantic-settings` (planned for M2's config layer) is for **config** loading, not signal data.

**Public API (`src/signal_copier/domain/__init__.py`):**

```python
from signal_copier.domain.signal import (
    FailureReason,
    ParseFailure,
    ParsedSignal,
    ParseResult,
    Signal,
    derive_signal_id,
    parse_signal,
)

__all__ = [
    "FailureReason",
    "ParseFailure",
    "ParsedSignal",
    "ParseResult",
    "Signal",
    "derive_signal_id",
    "parse_signal",
]
```

**Type/import conventions:**

- `from __future__ import annotations` at the top of `signal.py` (PEP 563; mypy `--strict` friendly)
- `from typing import Final, Literal` for the regex constants and direction enum
- No external deps imported in `signal.py` — pure stdlib

**Module-private helpers in `signal.py`:**

- `_SIGNAL_LINE_RE: Final[re.Pattern[str]]` — compiled once at module load
- `_HEADER_RE: Final[re.Pattern[str]]` — compiled once at module load
- `_BOM: Final[str] = "\ufeff"` — for stripping
- `_add_minutes(hhmm: str, minutes: int) -> str` — gale arithmetic with midnight wrap

**Visibility:** anything starting with `_` is module-private. The 7 exports above are the entire public surface.

---

## 3. Data Types

```python
# src/signal_copier/domain/signal.py

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Final, Literal


# --- Failure reason enum ----------------------------------------------------

class FailureReason(str, Enum):
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
    pair: str                       # "EUR/JPY"
    direction: Literal["up", "down"]
    trigger_hhmm: str               # "10:20"
    expiration_seconds: int         # 300 (i.e. 5 minutes)
    gale1_hhmm: str                 # "10:25" (trigger + 5 min, wraps midnight)
    gale2_hhmm: str                 # "10:30" (trigger + 10 min, wraps midnight)


# --- Full dataclass: PRD FR-2.5; constructed by M5 listener ---------------

@dataclass(frozen=True, slots=True)
class Signal:
    signal_id: str                  # sha1(pair|trigger_hhmm|direction|date)[:12]
    pair: str
    direction: Literal["up", "down"]
    trigger_hhmm: str
    expiration_seconds: int
    received_at_unix: float
    source_message_id: int
    source_chat_id: int
    raw_text: str


# --- Failure dataclass + tagged union -------------------------------------

@dataclass(frozen=True, slots=True)
class ParseFailure:
    reason: FailureReason
    raw_text: str                   # echo for FR-7.1 parse-failure DM (PRD §4.7)


ParseResult = ParsedSignal | ParseFailure
```

**Key choices:**

- `@dataclass(frozen=True, slots=True)` — immutable + memory-efficient. `ParsedSignal`/`Signal` are pure value types; mutability would be a bug surface.
- `Literal["up", "down"]` — matches PRD FR-2.5; rejects `Literal["down", "up"]`-shaped typos at type-check time.
- `FailureReason(str, Enum)` — string values are human-readable in logs/DMs and JSON-serializable. `str, Enum` makes `reason.value` work as a plain string.
- `ParseResult = ParsedSignal | ParseFailure` — discriminated via `isinstance(...)`. Python's pattern matching (`match result:`) handles discrimination cleanly. No wrapper class needed.
- `Signal` declared here but **never constructed by M1** — it's the M5 listener's job. Including the type now keeps the file complete (`signal.py` = everything about Signal-shaped data) and avoids M5 adding a class to an existing file.
- `ParseFailure.raw_text` is the original input, not normalized — preserves BOM/whitespace for accurate logging per FR-7.1.

---

## 4. Parser Algorithm

```python
# Module-level compiled regexes (defined once at import time)

_HEADER_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*\U0001f4b0(?P<N>\d+)-minute expiration\s*$",
    re.MULTILINE,
)

_SIGNAL_LINE_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<pair>[A-Z]{3}/[A-Z]{3});(?P<time>\d{2}:\d{2});(?P<dir>PUT\U0001f7e5|CALL\U0001f7e9)\s*$",
    re.MULTILINE,
)

_BOM: Final[str] = "\ufeff"


def _add_minutes(hhmm: str, minutes: int) -> str:
    """Add minutes to an HH:MM string, wrapping midnight. Returns HH:MM."""
    hour, mins = (int(x) for x in hhmm.split(":"))
    total = (hour * 60 + mins + minutes) % (24 * 60)
    new_hour, new_mins = divmod(total, 60)
    return f"{new_hour:02d}:{new_mins:02d}"


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

    # Map PUT/CALL to direction
    if direction_str == "PUT\U0001f7e5":
        direction: Literal["up", "down"] = "down"
    elif direction_str == "CALL\U0001f7e9":
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
```

**Algorithm notes:**

- **BOM:** `str.strip(_BOM)` strips from both ends. Tolerates leading BOM (common in pasted Telegram messages) and trailing BOM (rare). Spec deviation from PRD FR-2.2 "trailing UTF-8 BOM" — both-ends strip is a defensive superset.
- **Regex strictness:** PAIR is `[A-Z]{3}/[A-Z]{3}` — rejects lowercase `eur/jpy`, no-slash `EURJPY`, extras like `EUR/JPY/`. Direction is exactly `PUT🟥` or `CALL🟩` — no emoji variants.
- **Time range check:** regex accepts `\d{2}:\d{2}` but not `25:00` or `10:60` — we explicitly validate hour ∈ 0..23 and minute ∈ 0..59.
- **Multi-signal:** `finditer` enumerates all matches. Zero → `MISSING_SIGNAL_LINE`. One → proceed. >1 → `MULTIPLE_SIGNAL_LINES`.
- **Gale arithmetic:** wraps midnight. `_add_minutes("23:58", 5) == "00:03"`. M5/M6 can detect cross-midnight gales from full context if it matters.
- **Type-narrowing:** the `direction = "down"` / `direction = "up"` assignments give mypy `--strict` enough information. The defensive `else` branch is unreachable given the regex, marked `# pragma: no cover` to keep coverage at 100% on real code.
- **`derive_signal_id`:** pure helper, called by M5 listener (not by `parse_signal()`). SHA-1 truncated to 12 hex chars (48 bits) — collision probability is negligible for v1's signal volume (< 100/day).

---

## 5. Test Plan

**File:** `tests/test_parser.py` — pytest discovers by default (no `conftest.py` needed for M1).

**Coverage target:** 100% line + branch coverage on `parse_signal()`, `_add_minutes()`, and `derive_signal_id()` (per PRD §15 M1 row's "100% line coverage on parser" goal). The dataclass definitions and module-level regex compiles are imported as side effects and counted as covered. The `else` branch in direction mapping carries `# pragma: no cover` (see §4).

**Test inventory (~27 tests):**

```python
from signal_copier.domain.signal import (
    FailureReason, ParseFailure, ParsedSignal, derive_signal_id, parse_signal,
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

def test_happy_path_put_returns_parsed_signal()
def test_happy_path_call_returns_parsed_signal_with_up_direction()
def test_signal_line_with_trailing_whitespace_still_parses()

# --- Whitespace tolerance -------------------------------------------------

def test_message_with_leading_blank_lines_parses()
def test_message_with_trailing_blank_lines_parses()
def test_message_with_internal_blank_lines_parses()

# --- BOM tolerance --------------------------------------------------------

def test_leading_utf8_bom_is_stripped()
def test_trailing_utf8_bom_is_stripped()

# --- Gale arithmetic ------------------------------------------------------

@pytest.mark.parametrize(
    "trigger, gale1, gale2",
    [
        ("10:20", "10:25", "10:30"),   # normal
        ("00:00", "00:05", "00:10"),   # midnight start
        ("23:55", "00:00", "00:05"),   # wraps midnight
        ("23:58", "00:03", "00:08"),   # wraps with non-zero carry
    ],
)
def test_gale_times_are_arithmetic_with_midnight_wrap(trigger, gale1, gale2)
def test_add_minutes_at_exactly_midnight_returns_zero_hour()

# --- Missing / malformed fields ------------------------------------------

def test_missing_header_line_returns_missing_header_failure()
def test_missing_signal_line_returns_missing_signal_failure()
def test_multiple_signal_lines_returns_multiple_signal_lines_failure()
def test_message_with_no_semicolon_in_signal_returns_missing_signal_failure()
def test_message_with_wrong_emoji_direction_returns_missing_signal_failure()
def test_lowercase_pair_returns_missing_signal_failure()
def test_pair_without_slash_returns_missing_signal_failure()
def test_invalid_hour_25_returns_bad_time_failure()
def test_invalid_minute_60_returns_bad_time_failure()

# --- Expiration validation ------------------------------------------------

def test_header_with_disallowed_expiration_returns_expiration_not_allowed_failure()
def test_header_with_5_minute_expiration_is_accepted_with_allowed_300()
def test_header_with_5_minute_expiration_is_accepted_with_allowed_set_including_300()

# --- Ad-only / non-signal messages ---------------------------------------

def test_typical_ad_message_returns_missing_header_failure()
def test_message_with_only_gale_lines_returns_missing_header_failure()
def test_empty_message_returns_missing_header_failure()
def test_whitespace_only_message_returns_missing_header_failure()

# --- ParseFailure echo + signal_id derivation ----------------------------

def test_parse_failure_preserves_original_raw_text()
def test_derive_signal_id_is_deterministic_per_day()
def test_derive_signal_id_differs_across_days()
```

**Test fixture strategy:**

- One shared `VALID_MESSAGE` constant for happy-path tests (kept identical to the PRD §4.2 example so reviewers can compare side-by-side).
- Parametrized tests for gale arithmetic (4 cases).
- Failure-reason tests construct minimal invalid messages inline.
- `derive_signal_id` tests construct `ParsedSignal` directly (no parser call needed).

**Assertion style:**

- Use `isinstance(result, ParsedSignal)` and assert field-by-field — explicit, no mystery equality.
- For failures, use `isinstance(result, ParseFailure)` and assert `result.reason == FailureReason.X`.
- For `derive_signal_id`, assert exact 12-char string equality.

**Test running:**

```bash
uv run pytest tests/test_parser.py -v
uv run pytest tests/test_parser.py --cov=signal_copier.domain.signal --cov-report=term-missing
# Expect: 100% coverage on signal.py, all tests pass
```

---

## 6. Handoff to M2+

**M2 (state machine + gale math)** — PRD §15 M2 row: "`domain/state.py` + `domain/gale.py` + state machine tests".

- Consumes: `Signal` (declared in §3, constructed in M5).
- Provides: state transitions, gale amount computation (`$2 → $4 → $8`, stage amounts per R-2).
- Test surface: synthetic `Signal` instances built via direct dataclass construction.
- M1 helps M2 by: declaring `Signal` so M2 can import it without first depending on M5.

**M4 (DB + migrations)** — PRD §15 M4 row.

- Consumes: `Signal`, plus its own `StageRecord` / `DailySummary` types.
- Provides: `StateStore` with CRUD methods backed by asyncpg.
- Test surface: asyncpg pool against a test PostgreSQL (Docker).
- M1 helps M4 by: nothing direct — `Signal` shape is stable but M4 defines its own types.

**M5 (Telegram listener)** — PRD §15 M5 row.

- Consumes: Telegram events → `parse_signal(raw_text, allowed_expirations=...)` → wraps `ParsedSignal` → `Signal` (adding `signal_id` via `derive_signal_id`, `received_at_unix`, `source_message_id`, `source_chat_id`, `raw_text` echo).
- Provides: signals to scheduler queue, parse-failure DMs per FR-7.1.
- Test surface: Telethon mock + parser integration test.
- M1 helps M5 by: providing `parse_signal()`, `ParsedSignal`, `Signal`, `FailureReason`, `ParseFailure`, `derive_signal_id()`, all with 100% coverage.

**M6 (scheduler + `__main__.py` glue)** — PRD §15 M6 row.

- Consumes: `Signal` from queue.
- Provides: `asyncio.get_event_loop().call_at(target_ts, ...)` triggers at `trigger_hhmm` (in configured TZ).
- Test surface: synthetic `Signal` + mock clock.
- M1 helps M6 by: nothing direct — scheduler takes `Signal.trigger_hhmm` as-is.

**M8 (broker)** — PRD §15 M8 row.

- Consumes: `Signal` with `direction` ∈ `Literal["up", "down"]`.
- Provides: trade placement via vendored `olymptrade_ws.OlympTradeClient`; win/loss results via push event e:26.
- Test surface: vendored client mock + recorded session.
- M1 helps M8 by: nothing direct — broker receives `Signal` with canonical direction.

---

## 7. Verification Criteria

**M1 ships 3 new files, ~380 LOC total:**

| File | Approx LOC | Purpose |
|---|---|---|
| `src/signal_copier/domain/__init__.py` | 15 | Re-export public API (7 symbols) |
| `src/signal_copier/domain/signal.py` | 130 | Dataclasses, enum, regex, `parse_signal()`, `_add_minutes()`, `derive_signal_id()` |
| `tests/test_parser.py` | 250 | ~27 pytest cases |

**No `pyproject.toml` changes.** M1 adds zero runtime deps (stdlib only — `re`, `hashlib`, `dataclasses`, `enum`, `typing`, `datetime.date`).

**Verification commands (all must pass):**

| # | Command | Expected |
|---|---|---|
| V-1 | `uv run pytest tests/test_parser.py -v` | All ~27 tests pass |
| V-2 | `uv run pytest tests/test_parser.py --cov=signal_copier.domain.signal --cov-report=term-missing` | **100% line + branch coverage** on `signal.py` (per PRD §15 M1 row) |
| V-3 | `uv run ruff check src/signal_copier tests/test_parser.py` | "All checks passed!" |
| V-4 | `uv run ruff format --check src/signal_copier tests/test_parser.py` | No output (formatted) |
| V-5 | `uv run mypy src/signal_copier tests/test_parser.py` | "Success: no issues found in N source files" |
| V-6 | `uv run python -m signal_copier` | Still prints M0 stub message (M1 doesn't touch `__main__.py`) |
| V-7 | `uv run pytest tests/test_main.py` | M0's stub test still passes (M1 doesn't break M0) |

**Coverage specifics for V-2:**

- `parse_signal()` — every branch covered (each `FailureReason` path tested, plus happy-path PUT and CALL).
- `_add_minutes()` — covered by parametrized gale tests (4 cases) and the `00:00` edge case.
- `derive_signal_id()` — covered by the 2 added tests.
- Module-level regex compiles + dataclass definitions — counted as imported/covered.
- The defensive `else` branch in direction mapping — `# pragma: no cover` (see §4).

**Idempotency / determinism:**

- `parse_signal()` is a pure function. Same input → same `ParseResult`. Restart-safe by construction.
- `derive_signal_id()` is deterministic. Same `(parsed, date)` → same signal_id.
- Tests are independent (no shared state, no fixtures file I/O). Reorder-safe.

**Failure modes:**

| Failure | Surface |
|---|---|
| Regex doesn't match a message the analyst considers valid | Parser returns `ParseFailure`; M5 listener logs + DM-notifies. NOT a crash. |
| Test fixture has wrong emoji literal | Test fails with a clear diff. Emoji matching is the most likely copy-paste hazard. |
| `derive_signal_id` collides for different signals on different days | Hash space is 48 bits (12 hex chars) → 2^48 ≈ 2.8e14. Collision probability is negligible for v1's signal volume (< 100/day). |
| `parse_signal()` called with empty `allowed_expirations` | Every header match fails → `EXPIRATION_NOT_ALLOWED`. Correct behavior. |

**Demo (no real Telegram needed):**

```bash
uv run pytest tests/test_parser.py -v --tb=short
# Shows all tests passing, including the gale-wrap and BOM-stripping cases.

uv run python -c "
from signal_copier.domain.signal import parse_signal
msg = '💰5-minute expiration\nEUR/JPY;10:20;PUT🟥'
r = parse_signal(msg, allowed_expirations={300})
print(r)
# ParsedSignal(pair='EUR/JPY', direction='down', trigger_hhmm='10:20',
#              expiration_seconds=300, gale1_hhmm='10:25', gale2_hhmm='10:30')
"
```

**Definition of Done for M1:** all V-1 through V-7 pass. No scope creep into M2+ concerns. No changes to `__main__.py`, `pyproject.toml`, `Dockerfile`, `railway.toml`, or any M0 file.

---

## 8. Risks & Edge Cases

1. **Emoji literals in source files.** `🟥` (U+1F7E5), `🟩` (U+1F7E9), `💰` (U+1F4B0) are 4-byte UTF-8 sequences. Editors that don't preserve UTF-8 could corrupt them. Mitigation: source files are UTF-8 by Python 3 default; `pyproject.toml` declares encoding implicitly; review via `git diff` should flag any byte changes.

2. **BOM strip scope.** Spec implements `str.strip(_BOM)` (both ends), which is a superset of PRD FR-2.2's "trailing UTF-8 BOM" wording. If a future maintainer interprets FR-2.2 strictly (leading-only), the both-ends behavior is harmless — it's purely additive tolerance. No risk.

3. **`# pragma: no cover` on the direction `else` branch.** This branch is unreachable given the regex (`PUT🟥` and `CALL🟩` are the only `dir` group matches). Marking it `pragma: no cover` keeps coverage at 100% on real code and makes the intent explicit. If someone later changes the regex to allow more directions, the `pragma` should be revisited.

4. **Gale cross-midnight is silent.** A trigger at "23:58" produces gale1="00:03" with no flag. M5/M6 will need to detect this if it matters (e.g., to avoid scheduling gales on the wrong day). M1 doesn't surface a flag because the parser is pure string→string and has no concept of "day". Acceptable: cross-midnight is rare (5-minute signals at 23:55+), and the wrapper can detect it.

5. **Pair case sensitivity.** The regex is `[A-Z]{3}` — strictly uppercase. If the analyst ever posts `eur/jpy` (lowercase), the parser rejects it as `MISSING_SIGNAL_LINE`. The M5 listener will log + DM-notify per FR-7.1. This is correct behavior: the regex enforces a strict format by design (FR-2.2).

6. **`Signal` declared but not constructed by M1.** M2 and later milestones can import `Signal`, but M1's test suite does not exercise `Signal` (only `ParsedSignal`). This is intentional: `Signal` requires `received_at_unix` (a clock) and message IDs (Telethon), which M1 doesn't have. M1's "no coverage gap" applies only to the functions M1 defines.

7. **Test fixture divergence.** The `VALID_MESSAGE` constant mirrors the PRD §4.2 example exactly. If the PRD's example changes, the test fixture must be updated to match — otherwise tests pass but real-world signals fail. Mitigation: review fixture against PRD §4.2 on every release.

8. **`derive_signal_id` date semantics.** Caller (M5) must pass a `date` in the configured TZ (not UTC). If M5 accidentally passes UTC, two signals at the same HH:MM around midnight UTC-3 will collide or split incorrectly. M1's tests don't catch this (they pass synthetic dates). M5 must own this correctness — defer detail to M5 spec.

---

## 9. Out of Scope (deferred to future milestones)

- ❌ Reading `.env` / pydantic-settings for `allowed_expirations` and other config. **Defer to M2 (config layer) or M5 (listener wires from config).**
- ❌ Pair-availability check on broker. **Defer to M8 (auto-discover via e:1068 push).**
- ❌ Trigger-time window check (±1 min past / +30 min future). **Defer to M5 (listener has clock).**
- ❌ Cross-midnight gale detection / day-boundary handling. **Defer to M5/M6 (has TZ context).**
- ❌ Constructing `Signal` instances from `ParsedSignal`. **Defer to M5.**
- ❌ A `conftest.py` with shared fixtures. **Defer until M2+ when test surface grows.**
- ❌ Adding runtime deps to `pyproject.toml`. **M1 is stdlib-only.**
- ❌ Modifying `__main__.py`, `pyproject.toml`, `Dockerfile`, `railway.toml`, `.env.example`, `src/olymptrade_ws/`, or any other M0 file. **Surgical change: leave them alone.**
- ❌ CI / GitHub Actions. **Per M0 D-5; revisit in M2+ if test surface justifies it.**

---

## 10. Resolved Decisions (M1-specific)

The following decisions were confirmed during brainstorming on 2026-06-19. The PRD resolves all architectural questions (R-1 through R-15); these are M1-specific scoping calls.

| # | Decision | Rationale |
|---|---|---|
| D-1 | **Pure format parser in M1** | Time-window validation (past/future) and broker-pair validation deferred to M5/M8. Keeps M1's tests pure — no clock fixture, no broker mock — matching the PRD's "100% line coverage on parser" goal. |
| D-2 | **Two-stage: `ParsedSignal` + `Signal`** | M1 emits `ParsedSignal` (message-derived fields); M5 listener wraps into `Signal` (full PRD FR-2.5 dataclass with clock + message IDs + `signal_id`). Cleanest separation — M1 tests need no clock or Telethon fakes. |
| D-3 | **Tagged `ParseResult` union for failures** | `parse_signal()` returns `ParsedSignal \| ParseFailure`, where `ParseFailure` carries a `FailureReason` enum (7 values) and the original `raw_text`. Caller pattern-matches. Most informative for M5 logging + FR-7.1 parse-failure DM. |
| D-4 | **Module-level function, not `Parser` class** | `parse_signal(raw_text, *, allowed_expirations)` keeps the API simple; no class-instance boilerplate. Caller constructs `allowed_expirations` once at app boot from config and passes it on each call. |
| D-5 | **`derive_signal_id()` ships in M1** | Pure function, 5 lines, trivially testable. Keeps `signal.py` complete (all Signal-shaped helpers in one place). M5 imports instead of inlining the sha1. Small expansion beyond the strict PRD M1 row — accepted as low-risk completeness. |
| D-6 | **`Signal` dataclass declared in M1 (not constructed)** | M5 wraps `ParsedSignal` → `Signal`. Declaring `Signal` now avoids M5 adding a class to an existing file. M1's tests don't exercise `Signal` (only `ParsedSignal`) — no coverage gap. |
| D-7 | **BOM stripped from both ends** | `str.strip(_BOM)` is a defensive superset of PRD FR-2.2's "trailing UTF-8 BOM" wording. Handles leading BOM (common in pasted Telegram messages) and trailing BOM (rare). No risk — purely additive tolerance. |
| D-8 | **Multi-signal messages rejected with `MULTIPLE_SIGNAL_LINES`** | `finditer` enumerates all matches; >1 returns `ParseFailure(MULTIPLE_SIGNAL_LINES)`. Picking the first match is brittle — explicit rejection is more defensible. M5 DM-notifies the user per FR-7.1. |
| D-9 | **`frozenset[int]` for `allowed_expirations`** | Immutable + hashable; signals "closed set of allowed values". O(1) lookup vs. list. `int` because expiration is in seconds (300 = 5 min). |
| D-10 | **Keyword-only `allowed_expirations`** | Prevents positional confusion at call sites (`parse_signal("text", {300})` is ambiguous; `parse_signal("text", allowed_expirations={300})` is explicit). |
| D-11 | **`# pragma: no cover` on unreachable direction `else`** | Regex makes the `else` branch unreachable. Marking it `pragma: no cover` keeps coverage at 100% on real code and documents intent. Revisit if regex is changed to allow more directions. |

---

## 11. Transition to Implementation

After this spec is approved, the next step is to invoke the **writing-plans** skill to produce a detailed, step-by-step implementation plan. The plan will enumerate the file-creation order, exact commands to run, and per-step verification (V-1 through V-7).
