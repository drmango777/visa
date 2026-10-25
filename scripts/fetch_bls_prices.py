"""Everyday prices over time, from the U.S. Bureau of Labor Statistics.

- Miami-area Consumer Price Index (how much prices rose overall in Miami)
- Average U.S. prices for a few everyday items (gas, eggs, milk, ...)

Works without a key (smaller limits). Add BLS_API_KEY to .env for more.
Run:  python scripts/fetch_bls_prices.py
"""

import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(__file__))
from common import http, today, write_json  # noqa: E402

FIRST_YEAR = 2006

SERIES = {
    "cpi_miami": ("CUURS35BSA0", "All prices in the Miami area", "index", "Miami area"),
    "gas": ("APU000074714", "Gallon of regular gas", "$", "U.S. average"),
    "eggs": ("APU0000708111", "Dozen eggs", "$", "U.S. average"),
    "milk": ("APU0000709112", "Gallon of whole milk", "$", "U.S. average"),
    "bread": ("APU0000702111", "Pound of white bread", "$", "U.S. average"),
    "beef": ("APU0000703112", "Pound of ground beef", "$", "U.S. average"),
    "coffee": ("APU0000717311", "Pound of ground coffee", "$", "U.S. average"),
    "chicken": ("APU0000FF1101", "Pound of chicken breast", "$", "U.S. average"),
    "electricity": ("APU000072610", "Electricity, per kWh", "$", "U.S. average"),
}


def fetch(ids, start, end, key):
    url = f"https://api.bls.gov/publicAPI/{'v2' if key else 'v1'}/timeseries/data/"
    body = {"seriesid": ids, "startyear": str(start), "endyear": str(end)}
    if key:
        body["registrationkey"] = key
    raw = json.loads(http(url, data=json.dumps(body), headers={"Content-Type": "application/json"}))
    if raw.get("status") != "REQUEST_SUCCEEDED":
        raise RuntimeError(f"BLS said: {raw.get('message')}")
    return raw["Results"]["series"]


def main():
    key = os.getenv("BLS_API_KEY")
    step = 20 if key else 10  # years per request
    ids = [s[0] for s in SERIES.values()]
    by_id = {i: {} for i in ids}
    print(f"BLS prices ({'with' if key else 'without'} API key)")
    year = FIRST_YEAR
    end_year = date.today().year
    while year <= end_year:
        end = min(year + step - 1, end_year)
        for s in fetch(ids, year, end, key):
            for row in s.get("data", []):
                period = row["period"]
                if not period.startswith("M") or period == "M13":
                    continue
                try:
                    by_id[s["seriesID"]][f"{row['year']}-{period[1:]}"] = float(row["value"])
                except ValueError:
                    pass  # "-" means no value that month
        print(f"  {year}-{end}: ok")
        year = end + 1

    series = {}
    for name, (sid, label, unit, area) in SERIES.items():
        monthly = dict(sorted(by_id[sid].items()))
        if not monthly:
            print(f"  {name}: no data (skipped)")
            continue
        series[name] = {"id": sid, "label": label, "unit": unit, "area": area, "monthly": monthly}
        print(f"  {name}: {len(monthly)} months, latest {list(monthly)[-1]}")
    if not series:
        raise RuntimeError("Couldn't get any BLS data.")
    write_json("prices.json", {
        "source": "U.S. Bureau of Labor Statistics (Consumer Price Index and average prices)",
        "source_url": "https://www.bls.gov/cpi/data.htm",
        "fetched": today(),
        "series": series,
    })


if __name__ == "__main__":
    main()
