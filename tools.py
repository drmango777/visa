"""Tools for Sail-by. Plain Python, no AI, so every number is checkable.

The agent (agent.py) can call these, and the website calls them directly.
Data lives in /data. Run `python scripts/update_all_data.py` to refresh it.
"""

import json
import urllib.parse
from datetime import date, datetime
from pathlib import Path

from scripts.common import http, miles_between

DATA = Path(__file__).parent / "data"
COVERED_MILES = 2.0     # an address this close to an area center counts as that area


def _load(name):
    path = DATA / name
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


AREAS = _load("areas.json") or []
AREA_BY_ID = {a["id"]: a for a in AREAS}
TIMELINE = _load("timeline.json") or {"events": [], "min_wage": {"steps": []}}
CHANGES = _load("route_changes.json") or {"routes": {}, "gone": {}}
RENT = _load("rent.json")
PRICES = _load("prices.json")
STOPS = _load("transit_stops.json")
ROUTES = _load("transit_routes.json")


def data_status():
    """Which real data files are loaded. The site shows a notice for anything missing."""
    return {
        "rent": RENT is not None,
        "prices": PRICES is not None,
        "buses": STOPS is not None and ROUTES is not None,
    }


# ---------- finding the address ----------

def geocode(query):
    """Address text -> lat, lon, ZIP. Uses the free U.S. Census geocoder, then OpenStreetMap."""
    q = query.strip()
    if not q:
        raise ValueError("Type an address first.")
    if "fl" not in q.lower().replace(",", " ").split() and "florida" not in q.lower():
        q += ", Miami, FL"
    try:
        raw = json.loads(http("https://geocoding.geo.census.gov/geocoder/locations/onelineaddress",
                              {"address": q, "benchmark": "Public_AR_Current", "format": "json"},
                              timeout=20, tries=2))
        m = raw["result"]["addressMatches"]
        if m:
            c = m[0]
            return {"lat": c["coordinates"]["y"], "lon": c["coordinates"]["x"],
                    "zip": c["addressComponents"].get("zip", ""), "matched": c["matchedAddress"].title()}
    except Exception:  # noqa: BLE001
        pass
    raw = json.loads(http("https://nominatim.openstreetmap.org/search",
                          {"q": q, "format": "json", "addressdetails": 1, "limit": 1, "countrycodes": "us"},
                          timeout=20, tries=2))
    if not raw:
        raise ValueError("We couldn't find that address. Check the spelling, or pick an area below.")
    r = raw[0]
    return {"lat": float(r["lat"]), "lon": float(r["lon"]),
            "zip": (r.get("address", {}).get("postcode") or "")[:5], "matched": r["display_name"]}


def nearest_area(lat, lon):
    """The covered area this point belongs to, or None if it is outside all three."""
    best = min(AREAS, key=lambda a: miles_between(lat, lon, a["lat"], a["lon"]), default=None)
    if best and miles_between(lat, lon, best["lat"], best["lon"]) <= COVERED_MILES:
        return best
    return None


# ---------- rent ----------

def rent_story(zip_code, area_id=None, year_then=None):
    """Median rent now vs. an earlier year, from Census data."""
    if RENT is None:
        return {"available": False}
    zips = RENT["zips"]
    z = zip_code if zip_code in zips and zips[zip_code] else None
    if z is None and area_id in AREA_BY_ID:  # fall back to the area's own ZIPs
        z = next((x for x in AREA_BY_ID[area_id]["zips"] if zips.get(x)), None)
    if z is None:
        return {"available": False, "reason": "No rent numbers for this ZIP code."}
    hist = {int(y): v for y, v in zips[z].items()}
    years = sorted(hist)
    now_year = years[-1]
    want = year_then if year_then else now_year - 5
    clamped = want < years[0]
    then_year = max(years[0], min(want, now_year))
    out = {"available": True, "zip": z, "now": hist[now_year], "now_year": now_year,
           "history": [[y, hist[y]] for y in years], "first_year": years[0],
           "source": RENT["source"], "source_url": RENT["source_url"], "note": RENT.get("note", "")}
    if then_year >= now_year:
        return {**out, "then": None}
    then = hist[then_year]
    return {**out, "then": then, "then_year": then_year, "clamped": clamped,
            "diff": hist[now_year] - then, "pct": round((hist[now_year] / then - 1) * 100)}


# ---------- everyday prices ----------

def _at_or_before(monthly, ym):
    keys = [k for k in monthly if k <= ym]
    return (keys[-1], True) if keys else (next(iter(monthly)), False)


def prices_story(since):
    """How much everyday prices rose between `since` (a date) and the latest month."""
    if PRICES is None:
        return {"available": False}
    ym = f"{since.year}-{since.month:02d}"
    rows, clamped = [], False
    for name, s in PRICES["series"].items():
        m = s["monthly"]
        latest = list(m)[-1]
        then_key, exact = _at_or_before(m, ym)
        clamped = clamped or not exact
        if then_key >= latest:
            continue
        rows.append({"id": name, "label": s["label"], "area": s["area"], "unit": s["unit"],
                     "then": m[then_key], "now": m[latest], "then_month": then_key, "now_month": latest,
                     "pct": round((m[latest] / m[then_key] - 1) * 100)})
    return {"available": True, "clamped": clamped, "items": rows,
            "source": PRICES["source"], "source_url": PRICES["source_url"]}


# ---------- buses ----------

def buses_near(lat, lon, radius_miles):
    """Bus and train routes with a stop inside the circle, plus what changed on each."""
    if STOPS is None or ROUTES is None:
        return {"available": False}
    near = []
    for s in STOPS["stops"]:
        d = miles_between(lat, lon, s["lat"], s["lon"])
        if d <= radius_miles:
            near.append({**s, "miles": round(d, 2)})
    near.sort(key=lambda s: s["miles"])
    by_route = {}
    for s in near:
        for r in s["routes"]:
            e = by_route.setdefault(r, {"route": r, "stops": 0, "closest_miles": s["miles"]})
            e["stops"] += 1
    routes = []
    for r, e in by_route.items():
        info = ROUTES["routes"].get(r, {})
        routes.append({**e, "name": info.get("name", ""), "color": info.get("color", "#0B6E99"),
                       "type": info.get("type", 3), "trips_weekday": info.get("trips_weekday"),
                       "change": CHANGES["routes"].get(r), "shapes": info.get("shapes", [])})
    routes.sort(key=lambda r: (r["closest_miles"], r["route"]))
    return {"available": True, "routes": routes, "stops": near[:120],
            "source": ROUTES["source"], "source_url": ROUTES["source_url"],
            "changes_source": CHANGES.get("source"), "changes_url": CHANGES.get("source_url")}


def routes_gone(area_id):
    return CHANGES.get("gone", {}).get(area_id, [])


def nearby(lat, lon, zip_code, radius_miles):
    """Everything for the 'My area' tab."""
    area = nearest_area(lat, lon)
    area_id = area["id"] if area else None
    since = date(date.today().year - 5, date.today().month, 1)
    return {
        "area": {"id": area_id, "name": area["name"]} if area else None,
        "radius_miles": radius_miles,
        "rent": rent_story(zip_code, area_id),
        "prices": prices_story(since),
        "prices_since": since.isoformat(),
        "buses": buses_near(lat, lon, radius_miles),
        "gone": routes_gone(area_id) if area_id else [],
    }


# ---------- what changed since you went in ----------

def min_wage_on(day):
    steps = [(date.fromisoformat(d), v) for d, v in TIMELINE["min_wage"]["steps"]]
    before = [v for d, v in steps if d <= day]
    return before[-1] if before else None


def changes_since(since_text, area_id, zip_code):
    """Everything for the 'Since I went in' tab."""
    try:
        since = datetime.strptime(since_text, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError("That date doesn't look right. Pick it from the calendar.")
    today = date.today()
    if since > today:
        raise ValueError("That date is in the future.")
    wage_now = min_wage_on(today)
    wage_then = min_wage_on(since)
    events = []
    for e in TIMELINE["events"]:
        if "all" not in e["areas"] and area_id not in e["areas"]:
            continue
        d = date.fromisoformat(e["date"])
        if d > since:
            events.append({**e, "still_true": False})
        elif e.get("always"):
            events.append({**e, "still_true": True})
    events.sort(key=lambda e: (e["still_true"], e["date"]))
    return {
        "since": since.isoformat(),
        "years_away": round((today - since).days / 365.25, 1),
        "area": {"id": area_id, "name": AREA_BY_ID[area_id]["name"]} if area_id in AREA_BY_ID else None,
        "min_wage": {"then": wage_then, "now": wage_now, "source": TIMELINE["min_wage"]["source"],
                     "source_url": TIMELINE["min_wage"]["source_url"]},
        "rent": rent_story(zip_code, area_id, since.year),
        "prices": prices_story(since),
        "events": events,
        "gone": routes_gone(area_id) if area_id else [],
    }
