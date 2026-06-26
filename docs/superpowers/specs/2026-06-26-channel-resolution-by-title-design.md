# Design — Channel Resolution by Title Pattern

**Status:** Approved (brainstorming session 2026-06-26)
**Author:** Generated via brainstorming skill
**PRD reference:** §4.1 (FR-1.3 amendment), §4.7 (FR-7.1 startup notification)
**Related:** replaces pre-resolution by chat identifier; affects `TelegramClient`, `Listener`, `__main__.py`

---

## 1. Summary

Replace the existing "resolve a specific chat_id once at startup, then filter every event by that chat_id" model with a "scan all dialogs for a title-matching pattern, capture the chat_id, then defensively re-verify the title on every incoming event" model. The change is encapsulated in a new `ChannelResolver` component so that `TelegramClient` keeps its single responsibility (connection lifecycle) and `Listener` keeps its single responsibility (parsing + persistence).

---

## 2. Goals & Non-Goals

### 2.1 Goals

- **Resilience to chat-identifier resolution failures.** Today, `TelegramClient.connect()` calls `get_entity(target_chat)` and the whole process exits if Telethon cannot resolve the reference. This has proven flaky on Railway against a specific Telegram account (chat ID `-1001940077808` does not resolve there, despite resolving locally).
- **Single source of truth for "which channel is the signal channel":** a human-readable title pattern, not a brittle numeric ID.
- **Defense-in-depth:** fast-path by `chat_id` (one integer compare) plus defensive title re-verification (catches mid-session renames).
- **Fail-fast on ambiguity:** the process refuses to start when zero or more than one dialog matches the configured pattern.
- **Trivially unit-testable:** the new logic takes a minimal Telethon-shaped fake (a stub that returns a list of dialog objects), no live Telegram connection required.

### 2.2 Non-Goals (v1)

- Listening to multiple channels in parallel.
- Sender-allowlist filtering (FR-1.4 unchanged — parser regex is the sole defense).
- Auto-recovery on channel rename mid-session (re-scanning dialogs on every reconnect). The user restarts the bot to recover.
- Backwards-compatible behavior for the old `@username` / numeric `chat_id` form of `TELEGRAM_TARGET_CHAT`. The variable is repurposed.
- Auto-detection of whether `TELEGRAM_TARGET_CHAT` is an identifier or a pattern (fragile and surprising — explicit > implicit).

---

## 3. Architecture

### 3.1 Component map (existing + new)

```
src/signal_copier/
├── __main__.py                  [EDIT]  wire ChannelResolver into boot sequence
├── config.py                    [EDIT]  TELEGRAM_TARGET_CHAT docstring + validator update
├── telegram/
│   ├── client.py                [EDIT]  remove get_entity(); expose raw_client;
│   │                                      add set_resolved_chat_id()
│   ├── channel_resolver.py      [NEW]   ChannelResolver class + error classes
│   └── listener.py              [EDIT]  takes ChannelResolver; chat filter delegated
├── domain/signal.py             [unchanged]
├── broker/                      [unchanged]
├── scheduler/                   [unchanged]
├── notify/                      [unchanged]
└── infra/                       [unchanged]
```

### 3.2 Three-layer responsibility split

| Layer | Responsibility | Knows about |
|---|---|---|
| `TelegramClient` | Owns the StringSession, the MTProto connection, reconnect/FloodWait logic | API credentials; nothing about channels |
| `ChannelResolver` (NEW) | Scans dialog list at startup, captures `chat_id` from title match, verifies title on each event | Title pattern; nothing about signals, parsing, or scheduling |
| `Listener` | Receives Telethon events, asks `ChannelResolver.matches(event)`, then parses + enqueues signals | Parsing, signal domain; nothing about Telegram resolution |

### 3.3 Migration impact

- **PRD FR-1.3** gets amended (see §6).
- **Config example** in `.env.example` and `README.md` gets updated.
- **No DB migration needed** — `signals.source_chat_id` is already captured per-signal.
- **No backwards-compat shim** — the only consumer of the old behavior is `__main__.py`, which we update in the same change.

---

## 4. Components & Interfaces

### 4.1 `ChannelResolver` (NEW) — `src/signal_copier/telegram/channel_resolver.py`

```python
class ChannelNotFoundError(RuntimeError):
    """Raised when zero dialogs match the configured title pattern."""

class ChannelAmbiguousError(RuntimeError):
    """Raised when more than one dialog matches the configured title pattern."""

class TelegramChannelResolveError(RuntimeError):
    """Wraps ChannelNotFoundError / ChannelAmbiguousError with the configured
    pattern in the message for actionable diagnostics. Raised by __main__."""

class ChannelResolver:
    def __init__(self, *, pattern: str) -> None:
        """pattern is the raw config string (e.g. 'Magic Trader Signals').
        Stored normalized: lowercase + whitespace-collapsed."""

    @property
    def resolved_chat_id(self) -> int:
        """Raises RuntimeError if resolve() has not been called yet."""

    @property
    def captured_title(self) -> str:
        """The exact title captured at startup. Used for diagnostics."""

    async def resolve(self, client: _TelethonClient) -> int:
        """Calls client.get_dialogs(), filters by title, fails fast on
        0 or >1 matches, caches chat_id + title. Returns the resolved chat_id."""

    def matches(self, event) -> bool:
        """Per-event filter. Returns True iff:
          (a) event.chat_id == self._resolved_chat_id, AND
          (b) event.chat is not None, AND
          (c) self._normalized_pattern is a substring of event.chat.title.lower()
              (after whitespace normalization).

        Used by Listener on every NewMessage/MessageEdited event.
        Cheap: one int compare + one lowercase string contains."""

    def _normalize(self, s: str) -> str:
        """Lowercase + collapse whitespace + strip. Used both at startup
        and per-event so the comparison is symmetric."""
```

**Internal state:**

- `self._pattern: str` — raw pattern (kept for error messages)
- `self._normalized_pattern: str` — pattern normalized
- `self._resolved_chat_id: int | None` — set by `resolve()`
- `self._captured_title: str | None` — set by `resolve()`

### 4.2 `TelegramClient` (EDIT) — `src/signal_copier/telegram/client.py`

Three surgical changes:

1. **Remove** the `get_entity(target_chat)` call from `connect()`. `connect()` only authenticates and runs `await self._client.connect()`. No more `self._target_chat_id` resolution here. `connect()` should log: `"TelegramClient connected (target_chat_pattern=<pattern>)"` using `self._target_chat`.
2. **Rename the variable semantics** of `target_chat` — keep the parameter name but document it as the title pattern. **Add** an explicit non-empty check (existing constructor validates `api_id`, `api_hash`, `phone`, `session_string` but not `target_chat`): `if not target_chat: raise TelegramConfigError("TELEGRAM_TARGET_CHAT is empty; set it in .env to the channel title pattern (e.g. 'Magic Trader Signals')")`.
3. **Add** a property `raw_client: _TelethonClient` so `ChannelResolver.resolve()` can call `raw_client.get_dialogs()`. Wrapping it in a property documents intent and prevents callers from poking at internals directly.

```python
class TelegramClient:
    @property
    def raw_client(self) -> _TelethonClient:
        """The underlying Telethon client. Escape hatch for ChannelResolver.
        All other components should use TelegramClient's own API."""

    def set_resolved_chat_id(self, chat_id: int) -> None:
        """Externally inject the resolved chat_id (typically from
        ChannelResolver.resolve()). Required by __main__.py and replay.py
        which read self.target_chat_id."""
```

The `target_chat_id` property **stays** (still required by `__main__.py` and `replay.py`), but is now set externally via `set_resolved_chat_id()` rather than internally by `connect()`.

### 4.3 `Listener` (EDIT) — `src/signal_copier/telegram/listener.py`

Replace the `target_chat_id: int` constructor parameter with `channel_resolver: ChannelResolver`. Replace the chat filter:

```python
# OLD:
if event.chat_id != self._target_chat_id:
    return

# NEW:
if not self._channel_resolver.matches(event):
    return
```

The defensive title check now lives in `ChannelResolver.matches()`, not in `Listener` — keeping `Listener` focused on parsing + persistence.

### 4.4 `__main__.py` (EDIT) — wiring

Replace the old chat-resolution sequence:

```python
tg = TelegramClient(..., target_chat=config.telegram_target_chat)
await tg.connect()  # used to fail here if get_entity() couldn't resolve
# ... build listener with tg.target_chat_id ...
```

with:

```python
tg = TelegramClient(..., target_chat=config.telegram_target_chat)
await tg.connect()  # never fails on chat resolution anymore

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
except Exception as exc:  # Telethon errors from get_dialogs() (network/auth/etc.)
    raise TelegramConfigError(
        f"Failed to scan Telegram dialogs: "
        f"{type(exc).__name__}: {exc}"
    ) from exc
tg.set_resolved_chat_id(resolver.resolved_chat_id)

listener = Listener(channel_resolver=resolver, ...)
```

---

## 5. Data Flow

### 5.1 Startup sequence

```
__main__.py
   │
   ├─► Config validation (config.py)
   │     └─ TELEGRAM_TARGET_CHAT must be non-empty
   │
   ├─► TelegramClient(api_id, hash, phone, session, target_chat=cfg.telegram_target_chat)
   │     └─► string validation (api_id, hash, phone, session all non-empty)
   │
   ├─► await tg.connect()
   │     ├─► construct _TelethonClient(StringSession, api_id, api_hash)
   │     ├─► await _client.connect()
   │     └─► log "TelegramClient connected (target_chat_pattern=<pattern>)"
   │
   ├─► ChannelResolver(pattern=cfg.telegram_target_chat)
   │     ├─► normalize pattern → self._normalized_pattern
   │     └─► self._resolved_chat_id = None, self._captured_title = None
   │
   ├─► await resolver.resolve(tg.raw_client)
   │     ├─► dialogs = await tg.raw_client.get_dialogs()           # one-time scan
   │     ├─► matches = [d for d in dialogs if d.title and
   │     │              self._normalized_pattern in self._normalize(d.title)]
   │     ├─► if len(matches) == 0:
   │     │       raise ChannelNotFoundError(
   │     │           f"No Telegram dialog matches pattern {pattern!r}. "
   │     │           f"Scanned {len(dialogs)} dialogs. "
   │     │           f"Check TELEGRAM_TARGET_CHAT in .env.")
   │     ├─► if len(matches) > 1:
   │     │       raise ChannelAmbiguousError(
   │     │           f"{len(matches)} dialogs match pattern {pattern!r}: "
   │     │           f"{[m.title for m in matches]}. "
   │     │           f"Make the pattern more specific.")
   │     ├─► self._resolved_chat_id = matches[0].id
   │     ├─► self._captured_title = matches[0].title
   │     └─► log INFO: "ChannelResolver resolved pattern=… → chat_id=… (title=…)"
   │
   ├─► tg.set_resolved_chat_id(resolver.resolved_chat_id)
   │
   ├─► Listener(channel_resolver=resolver, state_store=..., queue=..., ...)
   │
   ├─► tg.add_message_handler(listener.on_new_message)
   ├─► tg.add_message_handler(listener.on_message_edited)
   │     └─► registers Telethon NewMessage() / MessageEdited() with NO chats= filter
   │         → Telethon delivers EVERY new/edited message on the account
   │
   ├─► recovery.recover_active_signals(...)              [unchanged]
   ├─► notifier.on_bot_started(mode=..., watching=cfg.telegram_target_chat, ...)
   │     └─► "Watching: <pattern>" (now reflects the pattern, not a chat_id)
   └─► start scheduler_task + telegram_task
```

### 5.2 Runtime message flow (per `NewMessage` / `MessageEdited` event)

```
Telethon event fires (any chat, any sender)
   │
   ▼
Listener.on_new_message(event)
   │
   ├─► if event.message.out: return                [unchanged — skip our own DMs]
   │
   ├─► if not self._channel_resolver.matches(event): return
   │     │
   │     │   ChannelResolver.matches(event):
   │     │     ├─► fast-path: if event.chat_id != self._resolved_chat_id:
   │     │     │       return False                 (1 integer compare)
   │     │     ├─► if event.chat is None:
   │     │     │       log WARNING "chat_id matched but event.chat unavailable;
   │     │     │                  | accepting on chat_id alone"; return True
   │     │     │     [edge case: Telethon occasionally delivers events where
   │     │     │      the chat object isn't populated for very new chats]
   │     │     ├─► title = self._normalize(event.chat.title or "")
   │     │     └─► return self._normalized_pattern in title
   │     │
   │     └─► end matches()
   │
   ├─► text = event.text or ""                     [unchanged]
   ├─► if not text.strip(): return                 [unchanged]
   │
   ├─► result = parse_signal(text, ...)            [unchanged — strict regex]
   ├─► if ParseFailure: log + DM notify + return  [unchanged]
   ├─► if not is_within_window(...): log + return  [unchanged]
   ├─► signal = Signal(...); upsert_signal()       [unchanged]
   ├─► if duplicate: log + return                 [unchanged]
   ├─► queue.put(signal) + pretty-print            [unchanged]
   │
   ▼
Same downstream flow as today (Scheduler → Broker → Result Monitor)
```

### 5.3 Why two checks (chat_id + title) on each event?

| Scenario | chat_id check | title check | Result |
|---|---|---|---|
| Message from "Magic Trader Signals" (happy path) | passes | passes | process |
| Message from any other channel | fails fast (1 int compare) | (skipped) | drop |
| Channel was renamed mid-session (rare but possible) | still passes | fails | drop + warn |
| Channel was deleted + recreated with new chat_id | fails | (skipped) | drop (until next restart re-scans dialogs) |
| Stale event from before restart | passes | passes | process (correct: it's a valid signal) |

The two checks cost ~one int compare + one short string `in` per event — negligible overhead vs the regex parser that runs anyway.

### 5.4 Reconnect behavior (unchanged)

If Telegram WS drops, `TelegramClient.start()` reconnects with exponential backoff (1s → 30s cap, max 10 attempts). On reconnect, `ChannelResolver` does **NOT** re-scan — the cached `chat_id` is reused. If the channel was renamed during the disconnect window, the per-event title check catches it and drops the message with a warning. The user can restart the bot to re-scan.

Re-scanning on every reconnect is intentionally out of scope for v1 — adds complexity and the use case is rare.

---

## 6. Error Handling

### 6.1 Failure modes & responses

| # | Failure | Where caught | Behavior | User-visible |
|---|---|---|---|---|
| 1 | `TELEGRAM_TARGET_CHAT` empty in `.env` | `TelegramClient.__init__` | `TelegramConfigError` raised | Exit code 2 with actionable message |
| 2 | Dialog scan: **zero** matches for pattern | `ChannelResolver.resolve` → `ChannelNotFoundError` → `__main__` catches as `TelegramConfigError` | Process refuses to start; Railway auto-restarts (will hit same error repeatedly — user must fix config) | Exit code 2 with message: `"No Telegram dialog matches pattern 'Magic Trader Signals'. Scanned N dialogs. Check TELEGRAM_TARGET_CHAT in .env."` |
| 3 | Dialog scan: **multiple** matches | `ChannelResolver.resolve` → `ChannelAmbiguousError` → `__main__` catches as `TelegramConfigError` | Process refuses to start | Exit code 2 with list of all matching titles: `"3 dialogs match 'Magic': ['Magic Trader Signals', 'Magic Patterns Chat', 'Magic Hour']. Make the pattern more specific."` |
| 4 | Telethon `get_dialogs()` raises (network/auth) | `__main__.py`'s broad `except Exception` clause wraps it as `TelegramConfigError` | Process refuses to start | Exit code 2 with Telethon error class + message (e.g. `"Failed to scan Telegram dialogs: ConnectionError: …"`) |
| 5 | Per-event: `event.chat` is None (rare Telethon edge case) | `ChannelResolver.matches` accepts on chat_id alone, logs WARNING | Signal processed (defensible — chat_id matched) | Log line: `"chat_id=X matched but event.chat unavailable; accepting on chat_id alone"` |
| 6 | Per-event: title drifts (channel renamed mid-run) | `ChannelResolver.matches` returns False; `Listener` drops silently | Message ignored | Log line: `"channel title drift detected: was='…' now='…'; ignoring message_id=N"` (first occurrence per drift, then rate-limited to 1/min/chat_id) |
| 7 | Per-event: chat_id matches but `event.chat.title` is None | `ChannelResolver.matches` returns False (defensive) | Message ignored | Log line: `"chat_id=X matched but chat has no title; ignoring message_id=N"` |
| 8 | Telegram WS disconnects mid-run | `TelegramClient.start` (existing reconnect supervisor, unchanged) | Exponential backoff reconnect | DM: `"🔌 Telegram disconnected. Reconnecting…"` (existing) |
| 9 | Session string revoked / `AuthKeyError` | Telethon raises; `__main__` re-raises | Process exits non-zero; Railway restarts (will fail again until session regenerated) | Exit code 1; user must regenerate `TELEGRAM_SESSION_STRING` |
| 10 | `FloodWaitError > 60s` (existing FR-1.7) | `TelegramClient.start` (existing) | Re-raise → process exits | DM: `"⚠️ Telegram FloodWait: NNs"` (existing) |

### 6.2 Logging discipline

- **Startup**: one INFO line per major step (`TelegramClient connected`, `ChannelResolver resolved pattern=… → chat_id=…, title=…`).
- **Per-event drops**: WARNING level, rate-limited to **1 per minute per chat_id** (avoid log floods if a renamed channel suddenly starts producing events).
- **Errors**: ERROR level with full context (pattern, scanned count, exception class).
- **No PII in logs**: chat IDs are fine (already logged today); no message content beyond what the parser already logs (`preview: <first 80 chars>`).

### 6.3 Railway-specific behavior

- Railway auto-restarts on non-zero exit. Fail-fast startup errors (#2, #3, #4) will cause **rapid restart loops**. Mitigation: rely on Railway's built-in exponential backoff for crash loops (which it does). Add an explicit 30s pre-exit sleep only if Railway logs show crash loops in practice. Document as a v1.1 follow-up if needed.

### 6.4 What we do not handle (out of scope, with reasoning)

- **Channel renaming mid-run while disconnected**: cached `chat_id` may point to a renamed/deleted channel. Detected by title-drift check at reconnect. Recovery requires process restart. Acceptable: the rename scenario is rare and the bot is designed to run uninterrupted.
- **Race condition where channel is renamed between two events**: extremely unlikely (humans rename channels; signals fire every 5 min). Both events would log the drift and be ignored — user would notice and restart.
- **Malicious or compromised session string**: out of scope (security of `TELEGRAM_SESSION_STRING` is operational, not code-level).

---

## 7. Testing Strategy

### 7.1 New tests — `tests/test_channel_resolver.py`

| # | Test | Asserts |
|---|---|---|
| 1 | `test_init_normalizes_pattern` | Pattern is lowercased + whitespace-collapsed in `_normalized_pattern`; raw pattern preserved |
| 2 | `test_resolve_returns_chat_id_when_one_match` | Mock `get_dialogs()` returns 5 dialogs incl. one matching → returns its ID, stores title |
| 3 | `test_resolve_raises_ChannelNotFoundError_on_zero_matches` | Empty dialog list → `ChannelNotFoundError` mentioning pattern + scanned count |
| 4 | `test_resolve_raises_ChannelAmbiguousError_on_multiple_matches` | 2 dialogs match → `ChannelAmbiguousError` listing both titles |
| 5 | `test_resolve_is_case_insensitive` | Dialog title `MAGIC TRADER SIGNALS` matches pattern `magic trader` |
| 6 | `test_resolve_ignores_titles_with_none` | Dialog with `title=None` doesn't crash; just excluded |
| 7 | `test_matches_chat_id_fast_path` | `event.chat_id == resolved` + title matches → True |
| 8 | `test_matches_rejects_wrong_chat_id` | `event.chat_id != resolved` → False (fast-path, no title check needed) |
| 9 | `test_matches_rejects_chat_id_match_but_title_drift` | `event.chat_id == resolved` but title doesn't contain pattern → False |
| 10 | `test_matches_accepts_when_chat_object_unavailable` | `event.chat_id == resolved` but `event.chat is None` → True + WARNING logged |
| 11 | `test_matches_rejects_when_title_is_none` | `event.chat_id == resolved` but `event.chat.title is None` → False |
| 12 | `test_matches_uses_normalized_comparison` | Pattern `"  Magic  Trader  "` matches title `"magic trader signals"` (whitespace normalization works) |
| 13 | `test_resolved_chat_id_property_raises_before_resolve` | Accessing `resolver.resolved_chat_id` before `resolve()` → `RuntimeError` |
| 14 | `test_resolve_propagates_telethon_exceptions` | Mock `get_dialogs()` raises `ConnectionError` → propagates unchanged (wrapped by `__main__`) |

All tests use a minimal stub object (no real Telethon mock library needed):

```python
class _FakeDialog:
    def __init__(self, *, id: int, title: str | None = None):
        self.id = id
        self.title = title

class _FakeTelethonClient:
    def __init__(self, dialogs: list[_FakeDialog]):
        self._dialogs = dialogs
    async def get_dialogs(self):
        return self._dialogs
```

### 7.2 Modified tests — `tests/test_telegram_client.py`

| # | Change |
|---|---|
| 1 | Remove any assertion that `TelegramClient.connect()` calls `get_entity` (it no longer does) |
| 2 | Add test: `test_connect_does_not_resolve_chat` — fake client with broken `get_entity`; `connect()` should still succeed |
| 3 | Add test: `test_raw_client_property_returns_underlying` — verify the escape-hatch property |
| 4 | Add test: `test_set_resolved_chat_id_makes_target_chat_id_accessible` — verifies the new external injection path |
| 5 | Existing `target_chat_id raises before connect` test stays valid; add a counterpart that after `set_resolved_chat_id(N)`, the property returns N |

### 7.3 Modified tests — `tests/test_telegram_listener.py`

| # | Change |
|---|---|
| 1 | Replace `target_chat_id` constructor arg with `channel_resolver` (use a fake that implements `matches()`) |
| 2 | Test: `test_listener_invokes_resolver_matches` — fake resolver; call `_process_message`; assert `matches` was called with the event |
| 3 | Test: `test_listener_drops_event_when_resolver_says_no` — resolver returns False → no parsing happens |
| 4 | Test: `test_listener_processes_event_when_resolver_says_yes` — resolver returns True → parsing runs as before |
| 5 | Test: `test_listener_does_not_call_chat_id_directly` — confirms the chat_id check has moved out of `Listener` |

### 7.4 Modified tests — `tests/test_main.py`

| # | Change |
|---|---|
| 1 | The 6 existing `fake_tg.target_chat_id = -100` setups need to also mock `tg.raw_client.get_dialogs()` and provide a fake `ChannelResolver` injected into `Listener` |
| 2 | New test: `test_main_exits_2_when_no_channel_matches` — dialog scan returns empty → `TelegramConfigError` → exit code 2 |
| 3 | New test: `test_main_exits_2_when_multiple_channels_match` — dialog scan returns 2 matches → exit code 2 with ambiguous error |
| 4 | New test: `test_main_resolves_channel_then_proceeds` — happy path: 1 match → bot starts, listener registered |
| 5 | New test: `test_main_bot_started_dm_includes_pattern` — assert `notifier.on_bot_started` is called with `watching=<pattern>`, not `<chat_id>` |

### 7.5 Coverage targets

- `channel_resolver.py`: **100% line + branch coverage** — small surface area, easy to hit every branch.
- `client.py` (modified surface): ≥95% — the change is small, all branches covered.
- `listener.py` (modified surface): ≥95% — only the filter line changes; all other paths unchanged.
- `__main__.py` (modified wiring): ≥90% — the new error paths need their own tests; happy path already covered.

### 7.6 Manual verification checklist (post-implementation)

- [ ] Unit tests pass (`ruff check`, `mypy --strict`, `pytest` with full coverage)
- [ ] Local end-to-end: set `.env` `TELEGRAM_TARGET_CHAT=Magic Trader Signals`, run `python -m signal_copier` → resolves to the right chat_id, processes a test message
- [ ] Negative: set `.env` `TELEGRAM_TARGET_CHAT=Nonexistent` → exits 2 with clear error
- [ ] Negative: temporarily have two channels named "Magic X" (test fixture) → exits 2 with ambiguous error
- [ ] Railway deploy: `TELEGRAM_TARGET_CHAT=Magic Trader Signals` → boots, log shows `"ChannelResolver resolved pattern='Magic Trader Signals' → chat_id=-1001940077808 (title='Magic Trader Signals')"`

---

## 8. PRD Amendment

### 8.1 The amendment

**Current text — FR-1.3 (PRD line 96):**

> **FR-1.3** Watch exactly one channel/group (configured by `@username` or numeric `chat_id`).

**Amended text:**

> **FR-1.3** Watch exactly one channel/group whose **title** matches the configured pattern (case-insensitive substring after whitespace normalization). The pattern is set via the `TELEGRAM_TARGET_CHAT` env var (the variable name is preserved for compatibility; its semantics change from "chat reference" to "title pattern"). At startup, the user's dialog list is scanned and the bot refuses to start unless **exactly one** dialog matches (zero → `ChannelNotFoundError`; more than one → `ChannelAmbiguousError`). At runtime, every incoming event is double-filtered: fast-path by the resolved `chat_id`, then defensively re-verified by title to detect channel renames mid-session. Renames cause messages to be silently dropped with a WARNING log; the user must restart the bot to re-scan dialogs.

### 8.2 Related PRD touchpoints

| Location | Current | Amendment |
|---|---|---|
| PRD §4.1 (line 96, FR-1.3) | "Watch exactly one channel/group (configured by `@username` or numeric `chat_id`)." | See §8.1 above |
| PRD §17 / `.env.example` | `TELEGRAM_TARGET_CHAT=@analyst_channel` | `TELEGRAM_TARGET_CHAT=Magic Trader Signals` (commented: title pattern, case-insensitive substring) |
| PRD §4.7 (FR-7.1, Bot startup row, line 248) | `Watching: @channel` | `Watching: Magic Trader Signals` (or whatever the pattern is) |
| PRD §7 (architecture tree, lines 332–335) | Lists `telegram/client.py`, `telegram/listener.py` | Add `telegram/channel_resolver.py` |
| PRD §15 (Build Plan, M5 row, line 720) | "Connects to Telegram, parses real channel messages, dumps to stdout (no sender-allowlist, R-14)" | "Connects to Telegram via `ChannelResolver` (title-pattern matching), parses real channel messages, dumps to stdout (no sender-allowlist, R-14)" |

### 8.3 Decisions NOT changed

- **FR-1.4** (no sender-allowlist check) — stays. The channel is admin-only; the parser regex is the sole defense.
- **FR-1.5** (listen to `NewMessage` + `MessageEdited`) — stays. `add_message_handler` registers both.
- **FR-1.6** (structured `Signal` into `asyncio.Queue`) — stays. Unaffected by channel resolution.
- **FR-1.7** (FloodWait handling) — stays. Handled in `TelegramClient`.
- **FR-1.8** (reconnect with exponential backoff) — stays. Handled in `TelegramClient`.
- **R-14** (no sender allowlist) — stays. Channel-title filter is the only access gate, as before.
- **FR-6.6** (demo-only guardrail) — stays. Unrelated.
- **PRD §17.3** (Railway deploy shape) — stays. No infrastructure change.

### 8.4 Risk assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| User mistypes the title pattern | Medium | Bot won't start | Fail-fast at startup with pattern + scanned count in error |
| Analyst renames channel mid-week | Low | Signals silently dropped, no DM notification | Title-drift WARNING in logs; user must restart |
| Two channels happen to share a substring | Very low | Bot won't start | Ambiguous error lists all matches — user narrows pattern |
| Telethon's `get_dialogs()` returns different result on Railway vs local | Low | Works locally, fails on Railway | Both use the same session string → same dialog list |
| Existing tests break due to signature change in `Listener.__init__` | High (expected) | CI fails until updated | Covered in §7.3 — fixture-level updates |

### 8.5 Rollout plan

1. Implement `ChannelResolver` + new tests (greenfield — no existing code depends on it).
2. Update `TelegramClient.connect()` + tests.
3. Update `Listener.__init__` + tests.
4. Update `__main__.py` wiring + tests.
5. Run full local test suite — must pass with `mypy --strict` + ruff + pytest at current coverage target.
6. Update `.env` on Railway: change `TELEGRAM_TARGET_CHAT=@start_magictradersignalsbot` → `TELEGRAM_TARGET_CHAT=Magic Trader Signals`.
7. Commit, push → Railway auto-deploys.
8. Verify on Railway logs: `"ChannelResolver resolved pattern='Magic Trader Signals' → chat_id=-1001940077808 (title='Magic Trader Signals')"`.
9. Update PRD as a separate commit on the same branch (docs change).

---

## 9. Open Questions / Follow-ups

None blocking. Two noted for v1.1:

- **Railway crash-loop backoff**: empirically verify whether Railway's built-in restart backoff is sufficient when `ChannelResolver` errors at boot. If logs show tight crash loops, add an explicit 30s sleep before exit code 2.
- **Multi-channel support**: if user ever wants to listen to a second channel (e.g., one for forex, one for crypto), `ChannelResolver` already returns `resolved_chat_id` as a single int; extending to a list is straightforward. Defer until user requests.