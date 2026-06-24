"""Tests for signal_copier.infra.log — loguru-based logging + parse-failures sink."""

from __future__ import annotations

import logging
from collections.abc import Generator
from pathlib import Path

import pytest

from signal_copier.infra.log import setup_logging, setup_parse_failures_log


@pytest.fixture(autouse=True)
def _reset_loguru() -> Generator[None]:
    """Each test starts with a clean loguru state."""
    from loguru import logger as _loguru_logger

    _loguru_logger.remove()
    yield
    _loguru_logger.remove()


def test_setup_logging_creates_log_file(tmp_path: Path) -> None:
    log_file = tmp_path / "signal_copier.log"
    setup_logging(log_file)
    from loguru import logger

    logger.info("hello world")
    import time

    time.sleep(0.1)
    content = log_file.read_text(encoding="utf-8")
    assert "hello world" in content


def test_setup_logging_is_idempotent(tmp_path: Path) -> None:
    log_file = tmp_path / "signal_copier.log"
    setup_logging(log_file)
    setup_logging(log_file)  # second call must not crash
    from loguru import logger

    logger.info("after-second-setup")
    import time

    time.sleep(0.1)
    content = log_file.read_text(encoding="utf-8")
    assert "after-second-setup" in content


def test_intercept_handler_forwards_stdlib_log(tmp_path: Path) -> None:
    log_file = tmp_path / "signal_copier.log"
    setup_logging(log_file)
    stdlib_logger = logging.getLogger("signal_copier.test_module")
    stdlib_logger.info("via stdlib")
    import time

    time.sleep(0.1)
    content = log_file.read_text(encoding="utf-8")
    assert "via stdlib" in content
    assert "signal_copier.test_module" in content


def test_setup_parse_failures_log_writes_to_separate_file(tmp_path: Path) -> None:
    log_file = tmp_path / "signal_copier.log"
    setup_logging(log_file)
    pf_logger = setup_parse_failures_log(tmp_path)
    pf_logger.warning("malformed signal: %s", "preview-here")
    for h in pf_logger.handlers:
        h.close()
    import time

    time.sleep(0.1)
    pf_content = (tmp_path / "parse_failures.log").read_text(encoding="utf-8")
    main_content = log_file.read_text(encoding="utf-8")
    assert "malformed signal: preview-here" in pf_content
    assert "malformed signal: preview-here" not in main_content


def test_setup_parse_failures_log_idempotent(tmp_path: Path) -> None:
    """Repeated setup calls must NOT accumulate loguru sinks — a single
    warning should appear in the file exactly once."""
    pf_logger1 = setup_parse_failures_log(tmp_path)
    pf_logger2 = setup_parse_failures_log(tmp_path)
    pf_logger3 = setup_parse_failures_log(tmp_path)
    assert pf_logger1 is pf_logger2 is pf_logger3

    pf_logger3.warning("idempotency_test_message")
    for h in pf_logger3.handlers:
        h.close()
    import time

    time.sleep(0.1)

    content = (tmp_path / "parse_failures.log").read_text(encoding="utf-8")
    assert (
        content.count("idempotency_test_message") == 1
    ), f"Expected exactly one write, got {content.count('idempotency_test_message')}"
