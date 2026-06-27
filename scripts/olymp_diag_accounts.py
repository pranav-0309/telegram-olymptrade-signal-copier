"""Test crypto-only since weekend = no forex."""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from olymptrade_ws.core.client import OlympTradeClient

ACCESS_TOKEN = os.environ["OLYMP_ACCESS_TOKEN"]
ACCOUNT_ID = int(os.environ["OLYMP_ACCOUNT_ID"])
ACCOUNT_GROUP = os.environ.get("OLYMP_ACCOUNT_GROUP", "demo")

# Crypto pairs only — weekend = no forex
CRYPTO_PAIRS = [
    "BTCUSD", "ETHUSD", "XRPUSD", "LTCUSD", "BCHUSD",
    "BNBUSD", "ADAUSD", "DOGUSD", "SOLUSD", "TRUMPUSD",
    "POLUSD", "AVXUSD", "TONUSD", "DOTUSD",
]

# Try each account_id × each pair
ACCOUNTS = [
    (2892023356, "demo"),  # current (demo, $9696)
    (2892023357, "real"),  # alternative real, $0
    (2892023355, "real"),  # alternative real, $0
]


async def test_account(account_id: int, group: str) -> None:
    print(f"\n=== account_id={account_id} group={group!r} ===", flush=True)
    client = OlympTradeClient(
        access_token=ACCESS_TOKEN,
        account_id=account_id,
        account_group=group,
        log_raw_messages=False,
    )
    await client.start()
    client.account_id = account_id
    client.account_group = group
    await asyncio.sleep(5)
    trade = client.trade

    # Try just 2 crypto pairs to keep it short
    for pair in ["BTCUSD", "ETHUSD"]:
        try:
            r = await trade.place_order(
                pair=pair,
                amount=1.0,
                direction="up",
                duration=60,
                account_id=account_id,
                group=group,
                category="digital",
            )
            status = f"SUCCESS id={r.get('id')}" if r else "FAIL"
            print(f"  {pair!r}: {status}", flush=True)
        except Exception as e:
            print(f"  {pair!r}: EXC {type(e).__name__}: {e}", flush=True)

    await client.close()


async def main() -> None:
    for acct_id, grp in ACCOUNTS:
        await test_account(acct_id, grp)


if __name__ == "__main__":
    asyncio.run(main())