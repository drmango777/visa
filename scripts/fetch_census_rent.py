"""Median rent by ZIP code, every year available, from the U.S. Census Bureau.

Source: American Community Survey 5-year estimates, table B25064 (median gross rent).
Asks for every ZIP area in Florida once per year, then keeps ours. That way one
ZIP the Census doesn't track can't break the whole request.

Run:  python scripts/fetch_census_rent.py
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date

sys.path.insert(0, os.path.dirname(__file__))
from common import load_areas, today, write_json  # noqa: E402

VAR = "B25064_001E"
FIRST_YEAR = 2011


def get(url):
    """(status, text). Never raises on HTTP errors, so we can show what the Census said."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Sail-by hackathon project)"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


def fetch_year(year, wanted):
    base = f"https://api.census.gov/data/{year}/acs/acs5?get=NAME,{VAR}&for=zip%20code%20tabulation%20area:*"
    suffix = f"&key={os.environ['CENSUS_API_KEY']}" if os.getenv("CENSUS_API_KEY") else ""
    urls = [base + suffix, base + "&in=state:12" + suffix]   # 2019 and earlier need the state
    if year <= 2019:
        urls.reverse()
    why = ""
    for url in urls:
        status, text = get(url)
        try:
            rows = json.loads(text)
            header = rows[0]
            zcol, vcol = header.index("zip code tabulation area"), header.index(VAR)
        except (ValueError, IndexError):
            why = f"HTTP {status}: {text[:160]!r}"
            continue
        out = {}
        for r in rows[1:]:
            if r[zcol] in wanted:
                try:
                    v = int(float(r[vcol]))
                except (TypeError, ValueError):
                    continue
                if v > 0:   # the Census uses big negative numbers for "no data"
                    out[r[zcol]] = v
        return out, ""
    return None, why


def main():
    zips = sorted({z for a in load_areas() for z in a["zips"]})
    print(f"Census rent for ZIPs {', '.join(zips)}")
    by_zip = {z: {} for z in zips}
    latest, shown = None, False
    for year in range(FIRST_YEAR, date.today().year):
        got, why = fetch_year(year, set(zips))
        if got is None:
            print(f"  {year}: failed" + ("" if shown else f"  ({why})"))
            shown = True
            continue
        for z, v in got.items():
            by_zip[z][str(year)] = v
        latest = year
        print(f"  {year}: {len(got)} of {len(zips)} ZIPs")
    if latest is None:
        raise RuntimeError("The Census didn't give us any data. Copy the line above that starts with 'failed' and send it to Claude.")
    write_json("rent.json", {
        "source": "U.S. Census Bureau, American Community Survey 5-year estimates (median gross rent, table B25064)",
        "source_url": f"https://data.census.gov/table/ACSDT5Y{latest}.B25064",
        "note": "Each year is a 5-year average ending that year, so it trails today's asking rents.",
        "fetched": today(),
        "zips": by_zip,
    })


if __name__ == "__main__":
    main()
