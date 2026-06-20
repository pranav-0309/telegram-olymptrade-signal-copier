from __future__ import annotations

from signal_copier.broker import Broker, UnsupportedPairError


def test_unsupported_pair_error_is_exception() -> None:
    assert issubclass(UnsupportedPairError, Exception)


def test_unsupported_pair_error_has_meaningful_message() -> None:
    err = UnsupportedPairError("USD/EGP not available")
    assert "USD/EGP" in str(err)


def test_broker_protocol_is_importable() -> None:
    # Protocol type exists and is a Protocol.
    assert Broker is not None
