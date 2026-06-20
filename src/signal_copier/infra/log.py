from __future__ import annotations

import logging
from pathlib import Path


def setup_logging(log_path: Path) -> None:
    """Configure the root logger with a stderr handler at INFO level.

    M5 keeps this minimal. M7 replaces it with a loguru setup that
    adds rotation, file sinks, and the FR-7.1 DM-mirroring handler.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    _ = log_path  # unused until M7


def setup_parse_failures_log(log_dir: Path) -> logging.Logger:
    """Configure a dedicated logger for parse failures.

    Writes WARNING+ records to `<log_dir>/parse_failures.log`. The
    returned logger is passed to the Listener constructor; tests
    inject a NullLogger from `tests/_telegram_fixtures.py`.

    M5 uses a plain FileHandler (no rotation) because parse failures
    are rare. M7's loguru setup will add rotation along with the
    main log file.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "parse_failures.log"

    logger = logging.getLogger("signal_copier.parse_failures")
    logger.setLevel(logging.WARNING)
    # Idempotent: don't double-add the same FileHandler on re-call.
    if not any(
        isinstance(h, logging.FileHandler) and h.baseFilename == str(log_path)
        for h in logger.handlers
    ):
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(message)s"))
        logger.addHandler(handler)
    logger.propagate = False
    return logger
