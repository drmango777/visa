"""Tools the Reentry Compass agent can call.

Every function here is plain Python with no AI in it, so results are
predictable and testable. The agent decides which tools to call and
explains the results; these functions do the actual checking.

Demo data lives in /data. It is SAMPLE data for a few Miami-Dade areas.
Replace it with real data (see scripts/fetch_census_rent.py) before real use.
"""

import json
import math
from datetime import datetime, timezone
from pathlib import Path

DATA = Path(__file__).parent / "data"
LOG_PATH = DATA / "conflict_log.jsonl"

# How close to curfew counts as "tight" (minutes).
TIGHT_MARGIN = 15


def _load(name):
    with open(DATA / name, encoding="utf-8") as f:
        return json.load(f)


def _load_preferring_real(real_name, sample_name):
    """Use real data from the scripts/ folder if it has been built."""
    if (DATA / real_name).exists():
        return _load(real_name), True
    return _load(sample_name), False


AREAS = _load("areas.json")
SITES = _load("checkin_sites.json")
TRANSIT, TRANSIT_IS_REAL = _load_preferring_real("transit_real.json", "transit.json")
HELP, HELP_IS_REAL = _load_preferring_real("help_real.json", "help_nearby.json")
ALERTS = _load("civic_alerts.json")
SOURCES = _load("sources.json") if (DATA / "sources.json").exists() else {}

AREA_BY_ID = {a["id"]: a for a in AREAS}
SITE_BY_ID = {s["id"]: s for s in SITES}


# ---------- time helpers ----------

def parse_time(value):
    """'19:30' -> minutes after midnight. Raises ValueError if invalid."""
    try:
        hours, minutes = value.strip().split(":")
        hours, minutes = int(hours), int(minutes)
    except Exception:
        raise ValueError(f"Time must look like 19:30, got {value!r}")
    if not (0 <= hours < 24 and 0 <= minutes < 60):
        raise ValueError(f"Time out of range: {value!r}")
    return hours * 60 + minutes


def fmt_time(total_minutes):
    """Minutes after midnight -> '8:15 p.m.'"""
    total_minutes %= 24 * 60
    hours, minutes = divmod(total_minutes, 60)
    suffix = "a.m." if hours < 12 else "p.m."
    return f"{hours % 12 or 12}:{minutes:02d} {suffix}"


def miles_between(lat1, lon1, lat2, lon2):
    """Straight-line distance in miles (haversine)."""
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ---------- tool 1: transit vs curfew ----------

def check_trip(site_id, area_id, obligation_end, curfew):
    """Can the person get from their check-in site home before curfew?

    Assumes the obligation ends and curfew falls on the same evening.
    Wait time = half the bus/train headway (a standard planning estimate).
    """
    trip = TRANSIT.get(site_id, {}).get(area_id)
    if trip is None and TRANSIT_IS_REAL:
        return {"status": "no_service", "mode": "Transit", "ride_minutes": None,
                "message": "No bus or train route gets there from this check-in within 3 hours."}
    if trip is None:
        return {"status": "no_data", "message": "No transit data for this trip yet."}

    end = parse_time(obligation_end)
    cur = parse_time(curfew)
    if "schedule" in trip:
        return _check_scheduled_trip(trip["schedule"], end, cur)
    last = parse_time(trip["last_departure"])
    wait = trip["headway_minutes"] // 2
    leave = end + wait

    base = {
        "mode": trip["mode"],
        "ride_minutes": trip["minutes"],
        "last_departure": fmt_time(last),
        "obligation_end": fmt_time(end),
        "curfew": fmt_time(cur),
    }

    if leave > last:
        return {**base, "status": "no_service", "leave": None, "arrive": None,
                "margin_minutes": None,
                "message": f"No ride home after your {fmt_time(end)} check-in. The last trip leaves at {fmt_time(last)}"}

    arrive = leave + trip["minutes"]
    margin = cur - arrive
    if margin < 0:
        status = "conflict"
        message = f"You'd get home at {fmt_time(arrive)}, {-margin} min after your {fmt_time(cur)} curfew."
    elif margin <= TIGHT_MARGIN:
        status = "tight"
        message = f"You'd get home at {fmt_time(arrive)}, only {margin} min before curfew. One late bus breaks it."
    else:
        status = "ok"
        message = f"You'd get home at {fmt_time(arrive)}, {margin} min before curfew."

    return {**base, "status": status, "leave": fmt_time(leave), "arrive": fmt_time(arrive),
            "leave_minutes": leave, "arrive_minutes": arrive, "obligation_end_minutes": end,
            "curfew_minutes": cur, "margin_minutes": margin, "message": message}


def _status_for(arrive, cur):
    margin = cur - arrive
    if margin < 0:
        return "conflict", margin, f"You'd get home at {fmt_time(arrive)}, {-margin} min after your {fmt_time(cur)} curfew."
    if margin <= TIGHT_MARGIN:
        return "tight", margin, f"You'd get home at {fmt_time(arrive)}, only {margin} min before curfew. One late bus breaks it."
    return "ok", margin, f"You'd get home at {fmt_time(arrive)}, {margin} min before curfew."


def _check_scheduled_trip(schedule, end, cur):
    """Real GTFS data: schedule rows are [leave_minute, arrive_minute, modes].

    Each arrival already includes walking, waiting, and transfers, computed
    from Miami-Dade Transit's published weekday schedule.
    """
    base = {"obligation_end": fmt_time(end), "curfew": fmt_time(cur),
            "last_departure": fmt_time(schedule[-1][0]) if schedule else None}
    row = next((r for r in schedule if r[0] >= end), None)
    if row is None:
        last = fmt_time(schedule[-1][0]) if schedule else "earlier in the day"
        return {**base, "status": "no_service", "mode": "Transit", "ride_minutes": None,
                "leave": None, "arrive": None, "margin_minutes": None,
                "message": f"No ride home after your {fmt_time(end)} check-in. The last trip that gets there starts around {last}"}
    leave, arrive, modes = row
    status, margin, message = _status_for(arrive, cur)
    return {**base, "status": status, "mode": modes, "ride_minutes": arrive - leave,
            "leave": fmt_time(leave), "arrive": fmt_time(arrive),
            "leave_minutes": leave, "arrive_minutes": arrive, "obligation_end_minutes": end,
            "curfew_minutes": cur, "margin_minutes": margin, "message": message}


# ---------- tool 2: rank areas ----------

STATUS_RANK = {"ok": 0, "tight": 1, "conflict": 2, "no_service": 3, "no_data": 4}
RENT_RANK = {"fits": 0, "stretch": 1, "over": 2}


def rent_fit(rent, budget):
    if rent <= budget:
        return "fits"
    if rent <= budget * 1.1:
        return "stretch"
    return "over"


def find_areas(checkin_site, radius_miles, budget, curfew, obligation_end):
    """Rank every area within the radius by curfew safety, budget, and help nearby."""
    site = SITE_BY_ID.get(checkin_site)
    if site is None:
        raise ValueError(f"Unknown check-in site: {checkin_site}")

    results, outside = [], []
    for area in AREAS:
        dist = miles_between(site["lat"], site["lon"], area["lat"], area["lon"])
        if dist > radius_miles:
            outside.append(area["name"])
            continue
        trip = check_trip(checkin_site, area["id"], obligation_end, curfew)
        help_count = sum(1 for h in HELP if h["area_id"] == area["id"])
        results.append({
            "area_id": area["id"],
            "name": area["name"],
            "zip": area["zip"],
            "distance_miles": round(dist, 1),
            "room_rent": area["room_rent"],
            "apt_rent": area["apt_rent"],
            "rent_change_pct_2yr": area["rent_change_pct_2yr"],
            "room_fit": rent_fit(area["room_rent"], budget),
            "apt_fit": rent_fit(area["apt_rent"], budget),
            "trip": trip,
            "help_count": help_count,
            "rising_fast": area["rent_change_pct_2yr"] >= 12,
        })

    for r in results:
        r["recommended"] = r["trip"]["status"] in ("ok", "tight") and r["room_fit"] != "over"

    results.sort(key=lambda r: (
        0 if r["recommended"] else 1,
        STATUS_RANK[r["trip"]["status"]],
        RENT_RANK[r["room_fit"]],
        -r["help_count"],
        r["distance_miles"],
    ))
    for i, r in enumerate(results, start=1):
        r["rank"] = i

    return {
        "checkin_site": site["name"],
        "radius_miles": radius_miles,
        "budget": budget,
        "areas": results,
        "outside_radius": outside,
        "data_note": data_note(),
    }


def data_note():
    rent = SOURCES.get("rent")
    transit = SOURCES.get("transit") if TRANSIT_IS_REAL else None
    parts = [
        f"Rent: {rent}." if rent else "Rent: sample data.",
        f"Transit: {transit}." if transit else "Transit times: sample data.",
    ]
    return " ".join(parts)


# ---------- tool 3: help nearby ----------

def get_help_nearby(area_id, needs=None):
    items = [h for h in HELP if h["area_id"] == area_id]
    if needs:
        wanted = set(needs)
        if "shelter" in wanted:
            wanted.add("housing")
        items = [h for h in items if h["type"] in wanted] or items
    note = SOURCES.get("places", "OpenStreetMap") if HELP_IS_REAL else "Sample listings for the demo."
    return {"area_id": area_id, "places": items, "data_note": note}


# ---------- tool 4: civic alerts ----------

def get_civic_alerts(area_ids):
    names = {a: AREA_BY_ID[a]["name"] for a in area_ids if a in AREA_BY_ID}
    alerts = [{**a, "area_name": names[a["area_id"]]} for a in ALERTS if a["area_id"] in names]
    return {"alerts": alerts, "data_note": "Sample alerts. Check real county agendas before sharing."}


# ---------- civic evidence: anonymous conflict log ----------

def log_conflicts(site_id, ranked):
    """Save conflict PATTERNS only. No names, no personal details, ever."""
    entries = []
    for r in ranked["areas"]:
        status = r["trip"]["status"]
        if status in ("conflict", "no_service"):
            entries.append({
                "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "site": site_id,
                "area": r["area_id"],
                "type": "curfew" if status == "conflict" else "no_service",
                "detail": "Arrives after curfew" if status == "conflict"
                          else "Last trip home leaves before obligation ends",
                "sample": False,
            })
    if entries:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
    return len(entries)


def evidence_summary():
    """Group logged conflicts into patterns a decision-maker could act on."""
    groups = {}
    if LOG_PATH.exists():
        with open(LOG_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                e = json.loads(line)
                key = (e["site"], e["area"], e["type"])
                g = groups.setdefault(key, {"count": 0, "sample": 0, "detail": e["detail"]})
                g["count"] += 1
                g["sample"] += 1 if e.get("sample") else 0

    patterns = []
    for (site, area, kind), g in groups.items():
        site_name = SITE_BY_ID.get(site, {}).get("name", site)
        area_name = AREA_BY_ID.get(area, {}).get("name", area)
        if kind == "curfew":
            text = f"People living in {area_name} with a check-in at the {site_name} can't get home before curfew by transit."
        else:
            text = f"No transit runs from the {site_name} to {area_name} after evening obligations end."
        patterns.append({"site": site_name, "area": area_name, "type": kind,
                         "count": g["count"], "sample_count": g["sample"], "text": text})
    patterns.sort(key=lambda p: -p["count"])
    return {"patterns": patterns, "total": sum(p["count"] for p in patterns)}
