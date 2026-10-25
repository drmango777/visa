"""Bus and train stops, routes, and route lines near the three areas.

Source: Miami-Dade Transit's official GTFS feed (updated by the county).
If the download fails, save the zip yourself as data/cache/gtfs.zip and run again.

Run:  python scripts/fetch_transit.py
"""

import csv
import io
import os
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import date

sys.path.insert(0, os.path.dirname(__file__))
from common import CACHE, DATA, http, load_areas, miles_between, today, write_json  # noqa: E402

URL = "https://www.miamidade.gov/transit/googletransit/current/google_transit.zip"
KEEP_MILES = 4.5      # keep stops this close to an area center (lets an address ~2 mi away still see 2 mi)
MAX_SHAPE_POINTS = 220


def rows(zf, name):
    with zf.open(name) as raw:
        yield from csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))


def get_zip():
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / "gtfs.zip"
    try:
        print("  downloading the county's transit feed (about 8 MB)...")
        path.write_bytes(http(URL, timeout=120))
    except Exception as e:  # noqa: BLE001
        if not path.exists():
            raise RuntimeError(
                f"Couldn't download {URL} ({e}). Download it in your browser, "
                f"save it as {path}, and run this again.") from e
        print(f"  download failed ({e}); using the copy already at {path}")
    return zipfile.ZipFile(path)


def thin(points, limit=MAX_SHAPE_POINTS):
    step = max(1, len(points) // limit)
    out = points[::step]
    if out[-1] != points[-1]:
        out.append(points[-1])
    return out


def main():
    areas = load_areas()
    zf = get_zip()
    names = set(zf.namelist())

    # 1. stops near the areas
    stops = {}
    for r in rows(zf, "stops.txt"):
        try:
            lat, lon = float(r["stop_lat"]), float(r["stop_lon"])
        except (ValueError, KeyError):
            continue
        if r.get("location_type") not in ("", "0", None):
            continue
        if min(miles_between(lat, lon, a["lat"], a["lon"]) for a in areas) <= KEEP_MILES:
            stops[r["stop_id"]] = {"id": r["stop_id"], "name": r["stop_name"].title(),
                                   "lat": round(lat, 5), "lon": round(lon, 5), "routes": set()}
    print(f"  {len(stops)} stops near the three areas")

    # 2. which services run on weekdays today
    t = date.today().strftime("%Y%m%d")
    weekday, fallback = set(), set()
    for r in rows(zf, "calendar.txt"):
        if r["monday"] == "1" and r["tuesday"] == "1" and r["wednesday"] == "1":
            fallback.add(r["service_id"])
            if r["start_date"] <= t <= r["end_date"]:
                weekday.add(r["service_id"])
    weekday = weekday or fallback

    # 3. routes and weekday trips
    routes = {}
    for r in rows(zf, "routes.txt"):
        label = (r.get("route_short_name") or "").strip() or (r.get("route_long_name") or "").strip()
        routes[r["route_id"]] = {"label": label, "name": (r.get("route_long_name") or "").strip().title(),
                                 "type": int(r.get("route_type") or 3),
                                 "color": "#" + (r.get("route_color") or "0B6E99")}
    trip_route, trip_shape, trips_per_route, shapes_per_route = {}, {}, Counter(), defaultdict(Counter)
    for r in rows(zf, "trips.txt"):
        if r["service_id"] not in weekday:
            continue
        trip_route[r["trip_id"]] = r["route_id"]
        trips_per_route[r["route_id"]] += 1
        if r.get("shape_id"):
            shapes_per_route[r["route_id"]][r["shape_id"]] += 1

    # 4. which routes stop at the kept stops (stop_times.txt is the big file)
    print("  reading stop times (this takes a minute)...")
    touching = set()
    for r in rows(zf, "stop_times.txt"):
        s = stops.get(r["stop_id"])
        if s is None:
            continue
        rid = trip_route.get(r["trip_id"])
        if rid:
            s["routes"].add(routes[rid]["label"])
            touching.add(rid)

    # 5. route lines: the two most-used shapes per route
    want = {}
    for rid in touching:
        for sid, _ in shapes_per_route[rid].most_common(2):
            want[sid] = rid
    pts = defaultdict(list)
    if "shapes.txt" in names:
        for r in rows(zf, "shapes.txt"):
            if r["shape_id"] in want:
                pts[r["shape_id"]].append((int(r["shape_pt_sequence"]), float(r["shape_pt_lat"]), float(r["shape_pt_lon"])))

    out_routes = {}
    for rid in touching:
        info = routes[rid]
        entry = out_routes.setdefault(info["label"], {
            "name": info["name"], "type": info["type"], "color": info["color"],
            "trips_weekday": 0, "shapes": []})
        entry["trips_weekday"] += trips_per_route[rid]
    for sid, rid in want.items():
        if sid in pts:
            line = [[round(a, 5), round(b, 5)] for _, a, b in sorted(pts[sid])]
            out_routes[routes[rid]["label"]]["shapes"].append(thin(line))

    out_stops = [{**s, "routes": sorted(s["routes"])} for s in stops.values() if s["routes"]]
    print(f"  {len(out_stops)} stops with service, {len(out_routes)} routes")
    meta = {"source": "Miami-Dade Transit GTFS feed", "source_url": URL, "fetched": today()}
    write_json("transit_stops.json", {**meta, "stops": out_stops})
    write_json("transit_routes.json", {**meta, "routes": out_routes})


if __name__ == "__main__":
    main()
