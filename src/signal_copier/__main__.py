from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from dataclasses import asdict

from pydantic import ValidationError

from signal_copier.config import Config
from signal_copier.infra.db import Database, DatabaseConnectionError
from signal_copier.infra.log import setup_logging, setup_parse_failures_log
from signal_copier.telegram.client import TelegramClient, TelegramConfigError
from signal_copier.telegram.listener import Listener

# Bounded as a safety net. M5's dump_consumer drains instantly; M6's
# scheduler drains at ~1 signal/min so the cap is never hit.
_SIGNALS_QUEUE_MAXSIZE: int = 1000


def _build_dump_consumer(
    queue: asyncio.Queue,
) -> asyncio.Task[None]:
    """Return an asyncio Task that drains `queue` and pretty-prints each Signal.

    D-17: lives in __main__ as a local helper. M6 will replace this
    body with the scheduler (or delete it entirely when M6 owns the
    consumer).
    """

    async def _consume() -> None:
        while True:
            signal = await queue.get()
            try:
                print(json.dumps(asdict(signal), indent=2, default=str))
            finally:
                queue.task_done()

    return asyncio.create_task(_consume(), name="dump_consumer")


async def _run(config: Config) -> int:
    """Async main: wire up the pipeline and run until cancelled or fatal error."""
    db: Database | None = None
    tg: TelegramClient | None = None
    dump_task: asyncio.Task[None] | None = None
    try:
        db = await Database.connect(config.database_url)
        tg = TelegramClient(
            api_id=config.telegram_api_id,
            api_hash=config.telegram_api_hash,
            phone=config.telegram_phone,
            session_string=config.telegram_session_string,
            target_chat=config.telegram_target_chat,
        )
        await tg.connect()

        signals_queue: asyncio.Queue = asyncio.Queue(maxsize=_SIGNALS_QUEUE_MAXSIZE)
        parse_failures = setup_parse_failures_log(config.log_path.parent)

        listener = Listener(
            target_chat_id=tg.target_chat_id,
            state_store=db.state_store,
            queue=signals_queue,
            config=config,
            parse_failures_logger=parse_failures,
        )
        tg.add_message_handler(listener.on_new_message)
        tg.add_message_handler(listener.on_message_edited)

        dump_task = _build_dump_consumer(signals_queue)

        print(
            f"🟢 signal_copier M5 started\n"
            f"   Mode: {'dry_run' if config.dry_run else 'live demo'}\n"
            f"   Timezone: {config.timezone}\n"
            f"   Target chat: {config.telegram_target_chat} (chat_id={tg.target_chat_id})\n"
            f"   Watching for new messages and edits...\n"
        )

        await tg.start()  # blocks until disconnect or re-raise
        return 0
    finally:
        if dump_task is not None:
            dump_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await dump_task
        if tg is not None:
            await tg.close()
        if db is not None:
            await db.close()


def main() -> int:
    try:
        config = Config()
    except ValidationError as exc:
        sys.stderr.write(f"❌ Config validation failed:\n{exc}\n")
        return 2

    setup_logging(config.log_path)

    try:
        return asyncio.run(_run(config))
    except DatabaseConnectionError as exc:
        sys.stderr.write(f"❌ {exc}\n")
        return 1
    except TelegramConfigError as exc:
        sys.stderr.write(f"❌ {exc}\n")
        return 2
    except KeyboardInterrupt:
        print("\n🔴 signal_copier stopping (SIGINT)")
        return 0
    except Exception as exc:
        sys.stderr.write(f"❌ Unhandled error: {type(exc).__name__}: {exc}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
