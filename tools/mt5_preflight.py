"""M13.2 mt5_preflight — sanity check before live deploy.

Runs through:
  1. Read MT5_* env vars (after load_dotenv; no pydantic Config here)
  2. MetaTrader5() + mt5_instance.initialize() → connect
  3. mt5_instance.login_info() + mt5_instance.account_info() → snapshot
  4. mt5_instance.symbols_get(group="*STD*") → asset-map probe
  5. mt5_instance.shutdown()

Prints PASS/FAIL summary. Exits 0 on success, 1 on any MT5 error.

Run:    uv run python -m tools.mt5_preflight
"""

from __future__ import annotations

import contextlib
import os
import sys

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None  # type: ignore[assignment]

from mt5linux import MetaTrader5

SYMBOL_SUFFIX = "-STD"  # duplicated from broker/mt5.py — local config match


def _read_env(key: str) -> str:
    """Return the env var or empty string; truthiness checked by caller."""
    return os.environ.get(key, "")


def run_preflight() -> int:
    """Execute the preflight checks; return 0 (PASS) or 1 (FAIL)."""
    if load_dotenv is not None:
        load_dotenv()
    login = _read_env("MT5_LOGIN")
    password = _read_env("MT5_PASSWORD")
    server = _read_env("MT5_SERVER")
    if not login or not password or not server:
        print("[FAIL] Missing MT5_* env vars. Set MT5_LOGIN, MT5_PASSWORD, MT5_SERVER in .env.")
        return 1

    mt5_instance = MetaTrader5()
    try:
        ok = mt5_instance.initialize(
            path=os.environ.get("MT5_TERMINAL_PATH"),
            server=server,
            login=int(login),
            password=password,
        )
        if not ok:
            err = mt5_instance.last_error()
            print(f"[FAIL] mt5.initialize → login error: {err}")
            print("       Hint: Is the MT5 terminal running with the configured server?")
            return 1
        print("[OK] mt5.initialize      → MT5 terminal reachable")

        login_info = mt5_instance.login_info()
        print(f"[OK] mt5.login_info      → user={login_info[0]} server={login_info[1]}")

        acct = mt5_instance.account_info()
        if acct is None:
            print("[FAIL] mt5.account_info → returned None")
            return 1
        balance = getattr(acct, "balance", None)
        leverage = getattr(acct, "leverage", None)
        currency = getattr(acct, "currency", "?")
        if balance is None:
            print("[WARN] mt5.account_info.balance is None; balance printed as ?")
            balance_str = "?"
        else:
            balance_str = f"{balance:.2f}"
        print(
            f"[OK] mt5.account_info    → balance={balance_str} "
            f"leverage=1:{leverage} currency={currency}"
        )

        symbols = mt5_instance.symbols_get(f"*{SYMBOL_SUFFIX}*")
        n = len(symbols) if symbols else 0
        print(f"[OK] mt5.symbols_get     → {n} tradeable symbols (STD-named)")

        print("PASS — preflight OK; safe to start the live bot.")
        return 0
    except Exception as exc:
        print(f"[FAIL] Unexpected error: {type(exc).__name__}: {exc}")
        return 1
    finally:
        with contextlib.suppress(Exception):
            mt5_instance.shutdown()


if __name__ == "__main__":
    sys.exit(run_preflight())
