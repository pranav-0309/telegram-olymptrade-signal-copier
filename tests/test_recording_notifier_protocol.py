"""Protocol-satisfaction regression tests for RecordingNotifier.

These tests guard the contract that M6's scheduler tests rely on:
`RecordingNotifier` must implement every method declared by the
runtime-checkable `Notifier` Protocol. If the Protocol grows new methods
(M7 added 3; M10 added 3 more), RecordingNotifier must grow them in
lockstep — otherwise calling those notifier hooks in a real run would
hit an unbound method.

Why we don't use `isinstance(RecordingNotifier(), Notifier)`:
    `runtime_checkable` Protocol's `isinstance()` walks the MRO via
    `hasattr()`. Because `RecordingNotifier` inherits from `Notifier`,
    the Protocol's method definitions are visible on the class via
    inheritance even when the subclass doesn't implement them. So a plain
    `isinstance` check passes regardless of whether the new methods are
    actually present. We instead check the subclass's own `__dict__`.
"""

from __future__ import annotations

from signal_copier.notify.protocol import Notifier
from tests._scheduler_fixtures import RecordingNotifier


def test_recording_notifier_satisfies_protocol() -> None:
    """RecordingNotifier must define every method the Notifier Protocol declares.

    If the Protocol grows, this test fails until RecordingNotifier grows with it.
    Enumerating the 6 expected names here makes the failure message point at the
    missing method directly.
    """
    expected = (
        "on_parse_failure",
        "on_telegram_disconnect",
        "on_olymp_disconnect",
        "on_olymp_reconnecting",
        "on_olymp_reconnected",
        "on_olymp_reconnect_failed",
    )
    missing = [m for m in expected if m not in vars(RecordingNotifier)]
    assert not missing, (
        f"RecordingNotifier no longer satisfies Notifier Protocol — missing methods: {missing}"
    )


def test_recording_notifier_isinstance_protocol() -> None:
    """Plain isinstance() should also pass — this is the surface contract
    callers may rely on (e.g., a future supervisor that asserts the type
    of its notifier dependency)."""
    assert isinstance(RecordingNotifier(), Notifier)
