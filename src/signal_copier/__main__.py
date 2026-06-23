from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
from pathlib import Path

from pydantic import ValidationError

from signal_copier import recovery
from signal_copier.broker.base import Broker, BrokerAuthError
from signal_copier.broker.dry_run import DryRunBroker
from signal_copier.broker.olymp import OlympTradeBroker
from signal_copier.config import Config
from signal_copier.domain.signal import Signal
from signal_copier.infra.db import Database, DatabaseConnectionError
from signal_copier.infra.log import setup_logging, setup_parse_failures_log
from signal_copier.notify.protocol import NoOpNotifier, Notifier
from signal_copier.notify.telegram_dm import TelegramDMNotifier
from signal_copier.scheduler.trigger import Scheduler
from signal_copier.telegram.client import TelegramClient, TelegramConfigError
from signal_copier.telegram.listener import Listener

_log = logging.getLogger(__name__)

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
    replay_task: asyncio.Task[None] | None = None
    notifier: Notifier = NoOpNotifier()
    broker: Broker | None = None
    try:
        # M8: validate config-style broker requirements BEFORE any I/O so a
        # missing OLYMP_ACCESS_TOKEN doesn't burn through a DB/Telegram connect.
        if not config.dry_run and not config.olymp_access_token:
            sys.stderr.write(
                "❌ DRY_RUN=false but OLYMP_ACCESS_TOKEN is empty. "
                "Set OLYMP_ACCESS_TOKEN in .env or set DRY_RUN=true.\n"
            )
            return 2

        db = await Database.connect(config.database_url)
        tg = TelegramClient(
            api_id=config.telegram_api_id,
            api_hash=config.telegram_api_hash,
            phone=config.telegram_phone,
            session_string=config.telegram_session_string,
            target_chat=config.telegram_target_chat,
        )
        await tg.connect()

        if config.telegram_self_dm_notifications:
            notifier = TelegramDMNotifier(tg_client=tg, config=config)
            _log.info("Notifications: TelegramDMNotifier (self-DM enabled)")
        else:
            _log.info("Notifications: NoOpNotifier (self-DM disabled)")

        # M8: config-driven broker selection. DRY_RUN=true keeps the M6
        # behavior (DryRunBroker, no I/O). DRY_RUN=false uses OlympTradeBroker
        # wrapping the vendored olymptrade_ws client.
        if config.dry_run:
            broker = DryRunBroker()
            _log.info("Broker: DryRunBroker (DRY_RUN=true)")
            await broker.connect()
        else:
            broker = OlympTradeBroker(
                access_token=config.olymp_access_token,
                account_id=config.olymp_account_id,
                account_group=config.olymp_account_group,
                notifier=notifier,
            )
            _log.info(
                "Broker: OlympTradeBroker (live %s, account_id=%s)",
                config.olymp_account_group,
                config.olymp_account_id,
            )
            await broker.connect()

        signals_queue: asyncio.Queue[Signal] = asyncio.Queue(maxsize=_SIGNALS_QUEUE_MAXSIZE)
        parse_failures = setup_parse_failures_log(config.log_path.parent)

        listener = Listener(
            target_chat_id=tg.target_chat_id,
            state_store=db.state_store,
            queue=signals_queue,
            config=config,
            parse_failures_logger=parse_failures,
            notifier=notifier,
        )
        tg.add_message_handler(listener.on_new_message)
        tg.add_message_handler(listener.on_message_edited)

        # M9: opt-in fixture-driven signal injector for the soak. Gated by
        # SOAK_REPLAY env var; production never sets this.
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

        # M9: rehydrate in-progress cascades from DB before starting the
        # scheduler. Recovery runs ONCE at boot, before the listener starts.
        recovery_report = await recovery.recover_active_signals(
            state_store=db.state_store,
            broker=broker,
            scheduler=scheduler,
        )
        _log.info(
            "Recovery: rehydrated=%d timed_out=%d abandoned=%d",
            recovery_report.rehydrated,
            recovery_report.timed_out,
            recovery_report.abandoned,
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
        telegram_task = asyncio.create_task(tg.start(notifier=notifier), name="telegram")

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
        for bg_task in (scheduler_task, telegram_task, replay_task):
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
    except BrokerAuthError as exc:
        sys.stderr.write(f"❌ OlympTradeBroker failed to connect: {exc}\n")
        return 2
    except KeyboardInterrupt:
        print("\n🔴 signal_copier stopping (SIGINT)")
        return 0
    except Exception as exc:
        sys.stderr.write(f"❌ Unhandled error: {type(exc).__name__}: {exc}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
