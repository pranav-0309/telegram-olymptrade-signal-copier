"""Loguru-based logging infrastructure for signal_copier.

Sinks (configured by setup_logging + setup_parse_failures_log):
  1. stderr — colored, INFO+ (Railway live tail)
  2. logs/signal_copier.log — rotating, 10 MB × 5, ZIP, INFO+

Plus a stdlib-to-loguru bridge (_InterceptHandler) so existing
``logging.getLogger(__name__).info(...)`` call sites flow through
without any code changes.

Plus setup_parse_failures_log which returns a stdlib logger that
writes WARNING+ to logs/parse_failures.log (separate file, no rotation).
"""

from __future__ import annotations

import contextlib
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from loguru import logger as _loguru_logger

if TYPE_CHECKING:
    from loguru import Record

# Module-level state to track the parse-failures loguru sink id,
# so repeated setup_parse_failures_log() calls don't accumulate sinks.
_parse_failures_sink_id: int | None = None


def setup_logging(log_path: Path) -> None:
    """Configure loguru for the whole app.

    Idempotent: removes the default loguru sink first. Existing stdlib
    logging handlers are replaced with the InterceptHandler below so
    every ``logging.getLogger(name).info(...)`` call flows through loguru.
    """

    def _not_parse_failure(record: Record) -> bool:
        return not record["extra"].get("parse_failure", False)

    log_path.parent.mkdir(parents=True, exist_ok=True)
    _loguru_logger.remove()

    # Sink 1: stderr — colored.
    _loguru_logger.add(
        sys.stderr,
        level="INFO",
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> "
            "<level>{level: <8}</level> "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> "
            "- <level>{message}</level>"
        ),
        colorize=True,
        filter=_not_parse_failure,
    )

    # Sink 2: rotating file — no colors.
    _loguru_logger.add(
        str(log_path),
        level="INFO",
        format=("{time:YYYY-MM-DD HH:mm:ss.SSS} {level: <8} {name}:{function}:{line} - {message}"),
        rotation="10 MB",
        retention=5,
        compression="zip",
        encoding="utf-8",
        enqueue=True,
        filter=_not_parse_failure,
    )

    # Bridge stdlib logging → loguru.
    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)


def setup_parse_failures_log(log_dir: Path) -> logging.Logger:
    """Return a stdlib logger that writes WARNING+ to
    ``<log_dir>/parse_failures.log`` via a dedicated loguru sink.

    Idempotent: removes any previously-installed parse-failures sink
    before adding a new one, so calling this function N times does
    not result in N writes per warning.
    """
    global _parse_failures_sink_id
    log_dir.mkdir(parents=True, exist_ok=True)
    parse_path = log_dir / "parse_failures.log"

    # Remove the previous parse-failures sink (if any) before adding a new one.
    if _parse_failures_sink_id is not None:
        with contextlib.suppress(ValueError):  # sink already removed (e.g., loguru was reset)
            _loguru_logger.remove(_parse_failures_sink_id)
        _parse_failures_sink_id = None

    _parse_failures_sink_id = _loguru_logger.add(
        str(parse_path),
        level="WARNING",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} {level: <8} {message}",
        encoding="utf-8",
    )

    pf_logger = logging.getLogger("signal_copier.parse_failures")
    pf_logger.handlers.clear()
    pf_logger.addHandler(_ParseFailuresHandler())
    pf_logger.propagate = False
    pf_logger.setLevel(logging.WARNING)
    return pf_logger


class _InterceptHandler(logging.Handler):
    """Forward stdlib ``logging`` records to loguru.

    Standard pattern from https://loguru.readthedocs.io/en/stable/usage.html
    extended to preserve the stdlib logger name in the formatted output
    (loguru would otherwise substitute the caller's module name).
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = _loguru_logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        # TypedDict's update() rejects arbitrary kwargs at type-check time, but
        # loguru intentionally lets patchers mutate the record dict in place
        # (see loguru docs: "lambda r: r.update(function=func.__name__)"). Cast
        # to a plain dict so the call type-checks.
        _loguru_logger.patch(lambda r: cast("dict[str, Any]", r).update(name=record.name)).opt(
            depth=6, exception=record.exc_info
        ).log(level, record.getMessage())


class _ParseFailuresHandler(logging.Handler):
    """Forward parse-failure warnings into loguru at WARNING level.

    The ``parse_failure`` extra flag is consumed by ``_not_parse_failure``
    in ``setup_logging`` so these records only land in ``parse_failures.log``.
    """

    def emit(self, record: logging.LogRecord) -> None:
        _loguru_logger.bind(parse_failure=True).opt(depth=6, exception=record.exc_info).log(
            "WARNING", "[{}] {}", record.name, record.getMessage()
        )
