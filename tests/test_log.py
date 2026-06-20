from __future__ import annotations

import logging
from pathlib import Path

from signal_copier.infra.log import setup_parse_failures_log


def test_creates_log_dir_if_missing(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    assert not log_dir.exists()
    setup_parse_failures_log(log_dir)
    assert log_dir.is_dir()


def test_creates_parse_failures_log_file(tmp_path: Path) -> None:
    setup_parse_failures_log(tmp_path)
    expected = tmp_path / "parse_failures.log"
    assert expected.is_file()


def test_logger_writes_warning_to_file(tmp_path: Path) -> None:
    logger = setup_parse_failures_log(tmp_path)
    logger.warning("test message: %s", "hello")
    # Close the handler so the file is flushed.
    for handler in logger.handlers:
        handler.close()
    content = (tmp_path / "parse_failures.log").read_text(encoding="utf-8")
    assert "test message: hello" in content
    assert "WARNING" in content


def test_logger_does_not_propagate_to_root(tmp_path: Path) -> None:
    logger = setup_parse_failures_log(tmp_path)
    assert logger.propagate is False
    # Logger name should be namespaced so it doesn't pollute root.
    assert logger.name == "signal_copier.parse_failures"


def test_setup_is_idempotent(tmp_path: Path) -> None:
    # Calling setup_parse_failures_log twice should not stack handlers.
    logger1 = setup_parse_failures_log(tmp_path)
    logger2 = setup_parse_failures_log(tmp_path)
    assert logger1 is logger2  # same Logger instance (cached by name)
    file_handlers = [h for h in logger1.handlers if isinstance(h, logging.FileHandler)]
    assert len(file_handlers) == 1
