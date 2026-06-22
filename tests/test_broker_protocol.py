from __future__ import annotations

import inspect
import typing
from decimal import Decimal
from inspect import Parameter

from signal_copier.broker import Broker, UnsupportedPairError
from signal_copier.broker.base import Broker as BrokerCanonical
from signal_copier.broker.dry_run import DryRunBroker


def test_unsupported_pair_error_is_exception() -> None:
    assert issubclass(UnsupportedPairError, Exception)


def test_unsupported_pair_error_has_meaningful_message() -> None:
    err = UnsupportedPairError("USD/EGP not available")
    assert "USD/EGP" in str(err)


def test_broker_protocol_is_importable() -> None:
    # Protocol type exists and is a Protocol.
    assert Broker is not None


def test_dry_run_broker_satisfies_protocol() -> None:
    assert isinstance(DryRunBroker(), Broker)


def test_dry_run_broker_satisfies_canonical_protocol_path() -> None:
    # Both import paths resolve to the same Protocol object.
    assert Broker is BrokerCanonical


def test_place_signature_accepts_decimal_amount() -> None:
    # typing.get_type_hints resolves PEP 563 string annotations back to actual
    # types (works correctly with `from __future__ import annotations`).
    hints = typing.get_type_hints(DryRunBroker.place)
    assert hints["amount"] is Decimal


def test_place_signature_keyword_only_stage_and_amount() -> None:
    sig = inspect.signature(DryRunBroker.place)
    assert sig.parameters["stage"].kind == Parameter.KEYWORD_ONLY
    assert sig.parameters["amount"].kind == Parameter.KEYWORD_ONLY


def test_broker_importable_from_top_level() -> None:
    from signal_copier import Broker as TopLevelBroker

    assert TopLevelBroker is Broker


def test_unsupported_pair_error_importable_from_top_level() -> None:
    from signal_copier import UnsupportedPairError as TopLevel

    assert TopLevel is UnsupportedPairError


def test_broker_auth_error_importable() -> None:
    from signal_copier.broker.base import BrokerAuthError

    assert issubclass(BrokerAuthError, Exception)


def test_broker_auth_error_has_meaningful_message() -> None:
    from signal_copier.broker.base import BrokerAuthError

    err = BrokerAuthError("token rejected")
    assert "token rejected" in str(err)


def test_broker_auth_error_importable_from_top_level() -> None:
    from signal_copier import BrokerAuthError as TopLevel

    assert TopLevel is not None
