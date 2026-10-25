"""Median rent by ZIP code, every year available, from the U.S. Census Bureau.

Source: American Community Survey 5-year estimates, table B25064
(median gross rent, which includes utilities). One API call per year.

Run:  python scripts/fetch_census_rent.py
"""

import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(__file__))
from common import http, load_areas, today, write_json  # noqa: E402

VAR = "B25064_001E"
FIRST_YEAR = 2011  # first year ACS 5-year data exists for ZIP code areas


def fetch_year(year, zips):
    base = f"https://api.census.gov/data/{year}/acs/acs5"
    params = {"get": f"NAME,{VAR}", "for": "zip code tabulation area:" + ",".join(zips)}
    if os.getenv("CENSUS_API_KEY"):
        params["key"] = os.environ["CENSUS_API_KEY"]
    # 2020 and later: ZIP areas can't be nested in a state. 2019 and earlier: they must be.
    attempts = [params, {**params, "in": "state:12"}]
    if year <= 2019:
        attempts.reverse()
    for p in attempts:
        try:
            rows = json.loads(http(base, p, timeout=45, tries=2))
        except Exception as e:  # noqa: BLE001
            print(f"    {year}: {type(e).__name__}: {e}")
            continue
        header, body = rows[0], rows[1:]
        zcol = header.index("zip code tabulation area")
        vcol = header.index(VAR)
        out = {}
        for r in body:
            try:
                v = int(float(r[vcol]))
            except (TypeError, ValueError):
                continue
            if v > 0:  # the Census uses big negative numbers for "no data"
                out[r[zcol]] = v
        return out
    return None


def main():
    zips = sorted({z for a in load_areas() for z in a["zips"]})
    print(f"Census rent for ZIPs {', '.join(zips)}")
    by_zip = {z: {} for z in zips}
    latest = None
    for year in range(FIRST_YEAR, date.today().year):
        got = fetch_year(year, zips)
        if not got:
            print(f"  {year}: not published (skipped)")
            continue
        for z, v in got.items():
            by_zip[z][str(year)] = v
        latest = year
        print(f"  {year}: {len(got)} ZIPs")
    if latest is None:
        raise RuntimeError("Couldn't reach the Census API. Check your internet connection.")
    write_json("rent.json", {
        "source": "U.S. Census Bureau, American Community Survey 5-year estimates (median gross rent, table B25064)",
        "source_url": f"https://data.census.gov/table/ACSDT5Y{latest}.B25064",
        "note": "Each year is a 5-year average ending that year, so it trails today's asking rents.",
        "fetched": today(),
        "zips": by_zip,
    })


if __name__ == "__main__":
    main()
