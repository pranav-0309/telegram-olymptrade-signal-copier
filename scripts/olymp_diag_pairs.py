"""Find which crypto pairs are tradeable."""
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


async def main() -> None:
    client = OlympTradeClient(
        access_token=ACCESS_TOKEN,
        account_id=ACCOUNT_ID,
        account_group=ACCOUNT_GROUP,
        log_raw_messages=False,
    )
    await client.start()
    client.account_id = ACCOUNT_ID
    client.account_group = ACCOUNT_GROUP
    await asyncio.sleep(5)

    trade = client.trade

    # Test each crypto pair separately so we can attribute failures
    pairs = [
        "BTCUSD", "ETHUSD", "XRPUSD", "LTCUSD", "BCHUSD",
        "BNBUSD", "ADAUSD", "DOGUSD", "SOLUSD", "TRUMPUSD",
        "POLUSD", "AVXUSD", "TONUSD", "DOTUSD",
        "BOOM_300_X", "BOOM_150_X", "CRASH_150_X", "CRASH_300_X",
    ]

    results = {}
    for pair in pairs:
        try:
            r = await trade.place_order(
                pair=pair,
                amount=1.0,
                direction="up",
                duration=60,
                account_id=ACCOUNT_ID,
                group=ACCOUNT_GROUP,
                category="digital",
            )
            if r:
                results[pair] = f"OK id={r.get('id')} status={r.get('status')}"
            else:
                results[pair] = "FAIL"
        except Exception as e:
            results[pair] = f"EXC {type(e).__name__}"

    print("\n=== RESULTS ===", flush=True)
    for pair, status in results.items():
        marker = "✓" if status.startswith("OK") else "✗"
        print(f"  {marker} {pair:15s} {status}", flush=True)

    await client.stop()


if __name__ == "__main__":
    asyncio.run(main())