from __future__ import annotations

import asyncio
import contextlib
import sys

from pydantic import ValidationError

from signal_copier.broker.base import Broker
from signal_copier.broker.dry_run import DryRunBroker
from signal_copier.config import Config
from signal_copier.domain.signal import Signal
from signal_copier.infra.db import Database, DatabaseConnectionError
from signal_copier.infra.log import setup_logging, setup_parse_failures_log
from signal_copier.notify.protocol import NoOpNotifier
from signal_copier.scheduler.trigger import Scheduler
from signal_copier.telegram.client import TelegramClient, TelegramConfigError
from signal_copier.telegram.listener import Listener

# Bounded as a safety net (M5 D-1). M6's Scheduler drains immediately;
# the cap is never hit at the analyst's typical 1 signal/5min cadence.
_SIGNALS_QUEUE_MAXSIZE: int = 1000


async def _run(config: Config) -> int:
    """Async main: wire up the pipeline and run until cancelled or fatal error."""
    db: Database | None = None
    tg: TelegramClient | None = None
    scheduler: Scheduler | None = None
    scheduler_task: asyncio.Task[None] | None = None
    telegram_task: asyncio.Task[None] | None = None
    notifier = NoOpNotifier()
    broker: Broker | None = None
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

        # M6: build the broker. M6 uses DryRunBroker unconditionally;
        # M8 will add the DRY_RUN=false → OlympTradeBroker branch.
        broker = DryRunBroker()
        await broker.connect()

        signals_queue: asyncio.Queue[Signal] = asyncio.Queue(maxsize=_SIGNALS_QUEUE_MAXSIZE)
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

        # M6: Scheduler replaces the M5 dump_consumer. The Scheduler pulls
        # signals from the same queue and spawns a SignalSupervisor per
        # signal. The DryRunBroker (M3) handles place/wait_result calls.
        scheduler = Scheduler(
            queue=signals_queue,
            broker=broker,
            state_store=db.state_store,
            notifier=notifier,
            config=config,
        )

        await notifier.on_bot_started(
            mode="dry_run" if config.dry_run else "live demo",
            watching=config.telegram_target_chat,
            timezone=config.timezone,
        )

        print(
            f"🟢 signal_copier M6 started\n"
            f"   Mode: {'dry_run' if config.dry_run else 'live demo'}\n"
            f"   Timezone: {config.timezone}\n"
            f"   Target chat: {config.telegram_target_chat} (chat_id={tg.target_chat_id})\n"
            f"   Watching for new messages and edits...\n"
        )

        # Both run forever; either cancelling will trigger cleanup.
        scheduler_task = asyncio.create_task(scheduler.run(), name="scheduler")
        telegram_task = asyncio.create_task(tg.start(), name="telegram")

        # Wait for either to finish (clean exit) or raise (error).
        done, pending = await asyncio.wait(
            {scheduler_task, telegram_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in done:
            if exc := task.exception():
                raise exc
        return 0
    finally:
        for bg_task in (scheduler_task, telegram_task):
            if bg_task is not None:
                if not bg_task.done():
                    bg_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await bg_task
        if scheduler is not None:
            await notifier.on_bot_stopping(
                open_cascades=scheduler.active_task_count,
            )
        if broker is not None:
            await broker.close()
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
