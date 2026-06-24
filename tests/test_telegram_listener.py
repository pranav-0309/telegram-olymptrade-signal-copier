from __future__ import annotations

import asyncio
import logging
from typing import cast
from zoneinfo import ZoneInfo

import pytest

from signal_copier.config import Config
from signal_copier.domain.signal import FailureReason, Signal
from signal_copier.infra.clock import now_unix
from signal_copier.infra.state_store import StateStore
from signal_copier.notify.protocol import NoOpNotifier
from signal_copier.telegram.listener import Listener
from tests._scheduler_fixtures import RecordingNotifier
from tests._telegram_fixtures import (
    FakeStateStore,
    NullLogger,
    make_event,
)

# --- Test fixtures --------------------------------------------------------


def _config() -> Config:
    """Build a Config suitable for listener tests.

    The only fields Listener reads are: expiration_seconds and timezone.
    We use defaults for everything else and don't set TELEGRAM_* env vars.
    """
    return Config(
        expiration_seconds=300,
        timezone="America/Sao_Paulo",
    )


def _listener(
    *,
    state_store: FakeStateStore,
    queue: asyncio.Queue[Signal],
    config: Config | None = None,
    target_chat_id: int = 42,
    parse_failures_logger: logging.Logger | None = None,
    notifier: NoOpNotifier | RecordingNotifier | None = None,
) -> Listener:
    return Listener(
        target_chat_id=target_chat_id,
        state_store=state_store,  # type: ignore[arg-type]  # FakeStateStore is duck-typed
        queue=queue,
        config=config or _config(),
        parse_failures_logger=parse_failures_logger or NullLogger(),
        notifier=notifier or NoOpNotifier(),
    )


VALID_SIGNAL_TEXT = (
    "💰5-minute expiration\n"
    "EUR/JPY;10:20;PUT🟥\n"
    "🕛TIME UNTIL 10:25\n"
    "1st GALE -> TIME UNTIL 10:30\n"
    "2nd GALE - TIME UNTIL 10:35\n"
)


def _within_window_signal_text(
    *,
    seconds_from_now: int = 60,
    pair: str = "EUR/JPY",
    direction_marker: str = "PUT🟥",
) -> str:
    """Build a valid signal text whose trigger_hhmm is `seconds_from_now`
    seconds from the current wall clock (positive = future). Stays within
    the 60s past / 1800s future time window so the listener accepts it.
    """
    import datetime as _dt

    now = now_unix()
    target_unix = now + seconds_from_now
    tz = ZoneInfo("America/Sao_Paulo")
    hhmm = _dt.datetime.fromtimestamp(target_unix, tz=tz).strftime("%H:%M")
    return f"💰5-minute expiration\n{pair};{hhmm};{direction_marker}\n"


# --- Happy path ----------------------------------------------------------


async def test_happy_path_valid_signal_enqueued_and_upserted(
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = FakeStateStore()
    queue: asyncio.Queue[Signal] = asyncio.Queue()
    listener = _listener(state_store=state, queue=queue)

    text = _within_window_signal_text(seconds_from_now=60)
    event = make_event(
        text=text,
        chat_id=42,
        message_id=7,
    )
    await listener.on_new_message(event)

    assert len(state.upserted) == 1
    assert queue.qsize() == 1
    enqueued = queue.get_nowait()
    assert enqueued.pair == "EUR/JPY"
    assert enqueued.direction == "down"
    assert enqueued.source_message_id == 7

    out = capsys.readouterr().out
    assert '"pair": "EUR/JPY"' in out
    assert '"signal_id"' in out


async def test_duplicate_signal_logged_not_re_enqueued(
    caplog: pytest.LogCaptureFixture,
) -> None:
    state = FakeStateStore(next_insert_returns=False)
    queue: asyncio.Queue[Signal] = asyncio.Queue()
    listener = _listener(state_store=state, queue=queue)

    text = _within_window_signal_text(seconds_from_now=60)
    event = make_event(text=text, chat_id=42, message_id=1)
    with caplog.at_level(logging.INFO, logger="signal_copier.telegram.listener"):
        await listener.on_new_message(event)

    assert len(state.upserted) == 1
    assert queue.empty()
    assert any("duplicate signal" in rec.message for rec in caplog.records)


# --- Parse failure -------------------------------------------------------


@pytest.mark.parametrize(
    "bad_text, expected_reason",
    [
        ("random ad text with no signal line", FailureReason.MISSING_HEADER_LINE),
        ("💰5-minute expiration\n", FailureReason.MISSING_SIGNAL_LINE),
        # "EURJPY" (no slash) doesn't match the signal-line regex, so the
        # parser reports MISSING_SIGNAL_LINE. The regex requires `XXX/XXX`.
        (
            "💰5-minute expiration\nEURJPY;10:20;PUT🟥\n",
            FailureReason.MISSING_SIGNAL_LINE,
        ),
        (
            "💰5-minute expiration\nEUR/JPY;25:99;PUT🟥\n",
            FailureReason.BAD_TIME_FORMAT,
        ),
        (
            "💰3-minute expiration\nEUR/JPY;10:20;PUT🟥\n",
            FailureReason.EXPIRATION_NOT_ALLOWED,
        ),
    ],
)
async def test_parse_failure_routed_to_logger(
    bad_text: str,
    expected_reason: FailureReason,
) -> None:
    parse_logger = logging.getLogger(f"test.parse_failures.{expected_reason.value}")
    records: list[logging.LogRecord] = []

    class _ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    parse_logger.addHandler(_ListHandler())
    parse_logger.setLevel(logging.WARNING)
    parse_logger.propagate = False

    state = FakeStateStore()
    queue: asyncio.Queue[Signal] = asyncio.Queue()
    listener = _listener(
        state_store=state,
        queue=queue,
        parse_failures_logger=parse_logger,
    )

    event = make_event(text=bad_text, chat_id=42, message_id=1)
    await listener.on_new_message(event)

    assert state.upserted == []
    assert queue.empty()
    assert len(records) == 1
    assert expected_reason.value in records[0].getMessage()


# --- Time-window rejection -----------------------------------------------


async def test_out_of_window_past_rejected() -> None:
    parse_logger = logging.getLogger("test.out_of_window_past")
    records: list[logging.LogRecord] = []

    class _ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    parse_logger.addHandler(_ListHandler())
    parse_logger.setLevel(logging.WARNING)
    parse_logger.propagate = False

    state = FakeStateStore()
    queue: asyncio.Queue[Signal] = asyncio.Queue()
    listener = _listener(
        state_store=state,
        queue=queue,
        parse_failures_logger=parse_logger,
    )

    # 5 minutes in the past is well outside the 60s past tolerance.
    now = now_unix()
    five_min_ago = now - 300
    import datetime as _dt

    tz = ZoneInfo("America/Sao_Paulo")
    past_hhmm = _dt.datetime.fromtimestamp(five_min_ago, tz=tz).strftime("%H:%M")
    text = f"💰5-minute expiration\nEUR/JPY;{past_hhmm};PUT🟥\n"

    event = make_event(text=text, chat_id=42, message_id=1)
    await listener.on_new_message(event)

    assert state.upserted == []
    assert queue.empty()
    assert len(records) == 1
    assert "out_of_window" in records[0].getMessage()


async def test_out_of_window_within_tolerance_accepted() -> None:
    state = FakeStateStore()
    queue: asyncio.Queue[Signal] = asyncio.Queue()
    listener = _listener(state_store=state, queue=queue)

    # Use the current minute (HH:MM) as the trigger. Reconstructed as
    # HH:MM:00, this is 0-59 seconds in the past — always within the
    # 60s past tolerance. A truly "30s in the past" value can't be
    # expressed with HH:MM precision, so we use the start of the
    # current minute as a deterministic within-tolerance case.
    text = _within_window_signal_text(seconds_from_now=0)

    event = make_event(text=text, chat_id=42, message_id=1)
    await listener.on_new_message(event)

    # Within tolerance, so it should be enqueued (or at least upserted).
    # Note: signal_id includes the date; if the date shifted, the
    # signal is still valid for the new date — we accept it.
    assert len(state.upserted) == 1


# --- Chat filter / outgoing filter ---------------------------------------


async def test_wrong_chat_filtered_silently() -> None:
    state = FakeStateStore()
    queue: asyncio.Queue[Signal] = asyncio.Queue()
    listener = _listener(state_store=state, queue=queue, target_chat_id=42)

    text = _within_window_signal_text(seconds_from_now=60)
    event = make_event(text=text, chat_id=999, message_id=1)
    await listener.on_new_message(event)

    assert state.upserted == []
    assert queue.empty()


async def test_outgoing_message_ignored() -> None:
    state = FakeStateStore()
    queue: asyncio.Queue[Signal] = asyncio.Queue()
    listener = _listener(state_store=state, queue=queue)

    text = _within_window_signal_text(seconds_from_now=60)
    event = make_event(
        text=text,
        chat_id=42,
        message_id=1,
        outgoing=True,
    )
    await listener.on_new_message(event)

    assert state.upserted == []
    assert queue.empty()


# --- NewMessage and MessageEdited parity ----------------------------------


async def test_new_message_and_edited_produce_identical_output() -> None:
    state1 = FakeStateStore()
    q1: asyncio.Queue[Signal] = asyncio.Queue()
    l1 = _listener(state_store=state1, queue=q1)
    text = _within_window_signal_text(seconds_from_now=60)
    await l1.on_new_message(make_event(text=text, chat_id=42, message_id=1))

    state2 = FakeStateStore()
    q2: asyncio.Queue[Signal] = asyncio.Queue()
    l2 = _listener(state_store=state2, queue=q2)
    await l2.on_message_edited(make_event(text=text, chat_id=42, message_id=1))

    # Same signal_id, same content; the only difference is the underlying
    # event class. Both should produce the same Signal in the queue.
    assert len(state1.upserted) == 1
    assert len(state2.upserted) == 1
    assert state1.upserted[0].signal_id == state2.upserted[0].signal_id
    assert state1.upserted[0].pair == state2.upserted[0].pair
    assert state1.upserted[0].direction == state2.upserted[0].direction
    assert q1.get_nowait().signal_id == q2.get_nowait().signal_id


# --- Edge cases ---------------------------------------------------------


async def test_empty_message_handled() -> None:
    state = FakeStateStore()
    queue: asyncio.Queue[Signal] = asyncio.Queue()
    listener = _listener(state_store=state, queue=queue)

    event = make_event(text="", chat_id=42, message_id=1)
    await listener.on_new_message(event)

    assert state.upserted == []
    assert queue.empty()


async def test_bom_message_handled() -> None:
    state = FakeStateStore()
    queue: asyncio.Queue[Signal] = asyncio.Queue()
    listener = _listener(state_store=state, queue=queue)

    text = _within_window_signal_text(seconds_from_now=60)
    text_with_bom = "\ufeff" + text
    event = make_event(text=text_with_bom, chat_id=42, message_id=1)
    await listener.on_new_message(event)

    assert len(state.upserted) == 1


async def test_handler_survives_parse_failure() -> None:
    state = FakeStateStore()
    queue: asyncio.Queue[Signal] = asyncio.Queue()
    listener = _listener(state_store=state, queue=queue)

    # First: a bad message (parse failure)
    bad_event = make_event(text="random ad text", chat_id=42, message_id=1)
    await listener.on_new_message(bad_event)

    # Then: a good message
    good_text = _within_window_signal_text(seconds_from_now=60)
    good_event = make_event(text=good_text, chat_id=42, message_id=2)
    await listener.on_new_message(good_event)

    # The good message was processed normally.
    assert len(state.upserted) == 1
    assert queue.qsize() == 1
    assert state.upserted[0].source_message_id == 2


# --- Notifier wiring (Task 14 / M7) --------------------------------------


async def test_listener_emits_on_parse_failure_on_invalid_message() -> None:
    """When parse_signal returns ParseFailure, the listener must call
    notifier.on_parse_failure with the raw text and FailureReason."""
    notifier = RecordingNotifier()
    config = Config(timezone="America/Sao_Paulo")
    listener = Listener(
        target_chat_id=-100,
        state_store=cast(StateStore, FakeStateStore()),
        queue=asyncio.Queue(),
        config=config,
        parse_failures_logger=NullLogger(),
        notifier=notifier,
    )
    bad_event = make_event(text="not a signal", chat_id=-100, message_id=1)
    await listener.on_new_message(bad_event)

    assert any(call[0] == "on_parse_failure" for call in notifier.calls)


async def test_listener_does_not_emit_on_parse_failure_for_valid_signal() -> None:
    """When parse_signal succeeds, no on_parse_failure call is made."""
    notifier = RecordingNotifier()
    config = Config(timezone="America/Sao_Paulo")
    listener = Listener(
        target_chat_id=-100,
        state_store=cast(StateStore, FakeStateStore()),
        queue=asyncio.Queue(),
        config=config,
        parse_failures_logger=NullLogger(),
        notifier=notifier,
    )
    good_event = make_event(text=VALID_SIGNAL_TEXT, chat_id=-100, message_id=1)
    await listener.on_new_message(good_event)

    assert not any(call[0] == "on_parse_failure" for call in notifier.calls)
