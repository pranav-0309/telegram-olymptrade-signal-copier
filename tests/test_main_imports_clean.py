"""Preflight cleanup guard: signal_copier.__main__ must import without OlympTrade deps.

This test fails immediately after Task 4 deletes broker/olymp.py and
broker/reconnect.py, because __main__'s top-level import is now broken.
Task 5's edit makes this test pass by removing the broken import.
"""

from __future__ import annotations

import importlib
import sys


def test_main_module_imports_clean() -> None:
    """After preflight cleanup, signal_copier.__main__ must import cleanly.

    Guards against accidental reintroduction of OlympTrade imports.
    """
    # Drop any cached module so the test reflects the current file on disk.
    sys.modules.pop("signal_copier.__main__", None)
    module = importlib.import_module("signal_copier.__main__")
    assert module is not None
    assert hasattr(module, "main")
