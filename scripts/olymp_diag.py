"""Quick diagnostic: try placing trades with various pair formats against the
live OlympTrade demo API. Reads creds from .env (same as production).
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv  # type: ignore[import-untyped]

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from olymptrade_ws.core.client import OlympTradeClient  # noqa: E402

ACCESS_TOKEN = os.environ["OLYMP_ACCESS_TOKEN"]
ACCOUNT_ID = int(os.environ["OLYMP_ACCOUNT_ID"])
ACCOUNT_GROUP = os.environ.get("OLYMP_ACCOUNT_GROUP", "demo")

TEST_PAIRS = [
    "GBPUSD",
    "GBP/USD",
    "GBPUSD-otc",
    "EURUSD",
    "EUR/USD",
    "EURUSD-otc",
    "CADCHF",
    "CAD/CHF",
    "GBPNZD",
    "GBP/NZD",
    "USDJPY",
    "USD/JPY",
    "CRASH_1000_X",
    "BTCUSD",
    "POLUSD",
]


async def main() -> None:
    print(f"Connecting... account_id={ACCOUNT_ID} group={ACCOUNT_GROUP}", flush=True)
    client = OlympTradeClient(
        access_token=ACCESS_TOKEN,
        account_id=ACCOUNT_ID,
        account_group=ACCOUNT_GROUP,
        log_raw_messages=False,
    )
    await client.start()
    print(f"Connected. account_group={client.account_group}", flush=True)

    print("Calling initialize_session() to set account_id/group...", flush=True)
    await client.initialize_session()
    print(f"After init: account_id={client.account_id} account_group={client.account_group}", flush=True)

    # Apply same guardrail the broker code does:
    if client.account_group is None:
        client.account_group = ACCOUNT_GROUP
        print(f"  forced client.account_group={ACCOUNT_GROUP} (broker code fallback)", flush=True)
    # Force account_id too (broker code currently doesn't, but the e:1068 path didn't set it)
    if client.account_id is None:
        client.account_id = ACCOUNT_ID
        print(f"  forced client.account_id={ACCOUNT_ID}", flush=True)
    print(f"Final: account_id={client.account_id} account_group={client.account_group}", flush=True)

    print("Waiting 5s for asset map to settle...", flush=True)
    await asyncio.sleep(5)

    trade = client.trade

    for pair in TEST_PAIRS:
        print(f"\n=== Trying pair={pair!r} ===", flush=True)
        try:
            result = await trade.place_order(
                pair=pair,
                amount=1.0,
                direction="up",
                duration=60,
                account_id=ACCOUNT_ID,
                group=ACCOUNT_GROUP,
                category="digital",
            )
            if result is None:
                print(f"  FAILED: returned None", flush=True)
            else:
                print(f"  SUCCESS: id={result.get('id')} status={result.get('status')}", flush=True)
        except Exception as e:
            print(f"  EXCEPTION: {type(e).__name__}: {e}", flush=True)

    print("\nDone. Closing...", flush=True)
    await client.close()


if __name__ == "__main__":
    asyncio.run(main())