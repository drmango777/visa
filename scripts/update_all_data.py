"""Pull fresh data from the civic websites. Run on a computer with internet.

    python scripts/update_all_data.py

Each step is independent: if one source is down, the others still update.
Commit the new files in /data so the deployed site uses them too.
"""

import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(__file__))

import fetch_bls_prices  # noqa: E402
import fetch_census_rent  # noqa: E402
import fetch_transit  # noqa: E402

STEPS = [
    ("Rent by ZIP (Census)", fetch_census_rent.main),
    ("Everyday prices (BLS)", fetch_bls_prices.main),
    ("Bus stops and routes (Miami-Dade Transit)", fetch_transit.main),
]

failed = []
for name, fn in STEPS:
    print(f"\n== {name}")
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        failed.append(name)
        print(f"  FAILED: {e}")
        if os.getenv("DEBUG"):
            traceback.print_exc()

print("\nDone." if not failed else f"\nDone, but these failed: {', '.join(failed)}")
sys.exit(1 if failed else 0)
