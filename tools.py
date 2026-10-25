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


# ---------- can you make it home before curfew? ----------
# An ESTIMATE from stop locations and each stop's first/last/number of trips.
# It can't see transfers or which direction a bus is heading, and it says so.

WALK_MPH, BUS_MPH, CIRCUITY, ACCESS_MILES = 3.0, 11.0, 1.3, 0.45


def parse_time(value):
    """'19:30' -> minutes after midnight."""
    try:
        h, m = str(value).strip().split(":")[:2]
        h, m = int(h), int(m)
    except Exception:
        raise ValueError("Pick a time from the clock.")
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError("That time doesn't look right.")
    return h * 60 + m


def fmt_time(t):
    t = int(round(t)) % 1440
    h, m = divmod(t, 60)
    return f"{h % 12 or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


def _stops_within(lat, lon, miles):
    out = []
    for s in (STOPS or {}).get("stops", []):
        d = miles_between(lat, lon, s["lat"], s["lon"])
        if d <= miles:
            out.append((d, s))
    return sorted(out, key=lambda x: x[0])


def curfew_check(lat1, lon1, lat2, lon2, end_min, curfew_min):
    if curfew_min <= end_min:
        raise ValueError("Your curfew is before the time you finish. Check the two times.")
    straight = miles_between(lat1, lon1, lat2, lon2)
    walk_min = straight * CIRCUITY / WALK_MPH * 60
    walk_arrive = end_min + walk_min
    walk_ok = straight <= 4 and walk_arrive <= curfew_min
    if straight <= 4:
        walk_line = (f"Walking the whole way is about {round(walk_min)} minutes. You'd get there around "
                     f"{fmt_time(walk_arrive)}, {'before' if walk_ok else 'after'} your curfew.")
    else:
        walk_line = f"These two places are {straight:.1f} miles apart, which is too far to walk."
    out = {"status": "no_data", "headline": "", "plan": [], "last_bus_line": "", "walk_line": walk_line,
           "walk_ok": walk_ok, "miles_apart": round(straight, 1), "end": fmt_time(end_min), "curfew": fmt_time(curfew_min),
           "advice": "If it's tight or late, talk to your officer or case manager about changing the time. Do it before that day, not after.",
           "notes": ["This is an estimate, not a promise. Buses run late. Before you count on it, check the GO Miami-Dade Transit app or call 311.",
                     "We can't see transfers or which way each bus is heading, so make sure you're on the right side of the street.",
                     "Not legal advice. Your officer or case manager decides what counts as late."]}
    if STOPS is None:
        out["headline"] = "Bus data isn't loaded yet, so we can't check the bus."
        return out
    near_a, near_b = _stops_within(lat1, lon1, ACCESS_MILES), _stops_within(lat2, lon2, ACCESS_MILES)
    if not near_a or not near_b:
        out["headline"] = "We don't have bus stops close to one of these places. Check the GO Miami-Dade Transit app."
        return out
    a_by, b_by = {}, {}
    for d, s in near_a:
        for r in s["routes"]:
            a_by.setdefault(r, (d, s))
    for d, s in near_b:
        for r in s["routes"]:
            b_by.setdefault(r, (d, s))
    best, missed = None, None
    for r in a_by.keys() & b_by.keys():
        da, sa = a_by[r]
        db, sb = b_by[r]
        ride_miles = miles_between(sa["lat"], sa["lon"], sb["lat"], sb["lon"])
        if sa["id"] == sb["id"] or ride_miles < 0.15:
            continue
        svc = (sa.get("svc") or {}).get(r)
        headway = max(5.0, min(40.0, (svc[1] - svc[0]) / (svc[2] - 1))) if svc and svc[2] > 1 else 20.0
        reach = end_min + da * CIRCUITY / WALK_MPH * 60
        leave = reach + headway / 2
        last = svc[1] if svc else None
        cand = {"route": r, "name": (ROUTES or {}).get("routes", {}).get(r, {}).get("name", ""),
                "board": sa["name"], "off": sb["name"], "walk_a": da * CIRCUITY / WALK_MPH * 60, "wait": headway / 2,
                "ride": ride_miles * CIRCUITY / BUS_MPH * 60 + 3, "walk_b": db * CIRCUITY / WALK_MPH * 60,
                "headway": headway, "known": svc is not None, "last": last}
        if last is not None and reach > last:
            if missed is None or last > missed["last"]:
                missed = cand
            continue
        if last is not None:
            leave = min(leave, last)
        cand["arrive"] = leave + cand["ride"] + cand["walk_b"]
        if best is None or cand["arrive"] < best["arrive"]:
            best = cand
    if best is None and missed is not None:
        out["status"] = "no_bus"
        out["headline"] = (f"No. The last bus on route {missed['route']} near you leaves around {fmt_time(missed['last'])}. "
                           f"You'd get to the stop after that.")
        out["last_bus_line"] = f"Last bus on route {missed['route']} from {missed['board']}: about {fmt_time(missed['last'])}."
        return out
    if best is None:
        out["status"] = "walk" if walk_ok else "no_direct"
        out["headline"] = ("We couldn't find one bus between these two places, but you could walk it."
                           if walk_ok else "We couldn't find one bus between these two places. You may need to transfer, and we can't check transfers yet.")
        return out
    margin = curfew_min - best["arrive"]
    arrive = fmt_time(best["arrive"])
    if margin >= 20:
        out["status"], out["headline"] = "ok", f"Yes. You'd get home around {arrive}. That's {round(margin)} minutes before your curfew."
    elif margin >= 0:
        out["status"], out["headline"] = "tight", f"Maybe. You'd get home around {arrive}, only {round(margin)} minutes before your curfew. One late bus could break it."
    else:
        out["status"], out["headline"] = "late", f"No. You'd get home around {arrive}, {round(-margin)} minutes after your curfew."
    nm = best["name"]
    mins = lambda x: f"{max(1, round(x))} minute{'' if max(1, round(x)) == 1 else 's'}"
    out["plan"] = [f"Walk about {mins(best['walk_a'])} to the stop at {best['board']}.",
                   f"Take route {best['route']}{' (' + nm + ')' if nm else ''}." + (f" A bus comes about every {round(best['headway'])} minutes, so plan to wait about {round(best['wait'])}." if best["known"] else " We don't know how often it comes, so we guessed."),
                   f"Ride about {mins(best['ride'])}. Get off near {best['off']}.",
                   f"Walk about {mins(best['walk_b'])} to where you're staying."]
    if best["last"] is not None:
        out["last_bus_line"] = f"The last bus on route {best['route']} from that stop leaves around {fmt_time(best['last'])}."
    else:
        out["last_bus_line"] = "We don't have the last-bus time for this route. Check the app."
    return out
