"""Useful places (hospitals, libraries, food help, consulates, ...) from OpenStreetMap.

    python scripts/fetch_places.py

Pulls everything in a box around the three covered areas (with room to spare,
so "nearest hospital" still works near the edges) and writes data/places.json.
"""

import json
import re
import urllib.parse

from common import http, today, write_json

# south, west, north, east: central Miami, from Coral Way up to Little Haiti
BBOX = (25.70, -80.33, 25.86, -80.15)
OVERPASS = "https://overpass-api.de/api/interpreter"

# kind id -> (label, list of OSM tag filters)
KINDS = {
    "hospital": ("Hospitals", ['["amenity"="hospital"]']),
    "clinic": ("Clinics", ['["amenity"="clinic"]', '["amenity"="doctors"]', '["healthcare"="clinic"]']),
    "pharmacy": ("Pharmacies", ['["amenity"="pharmacy"]']),
    "library": ("Libraries", ['["amenity"="library"]']),
    "food": ("Food help and shelters", ['["amenity"="food_bank"]', '["social_facility"="food_bank"]',
                                        '["social_facility"="shelter"]', '["social_facility"="soup_kitchen"]',
                                        '["amenity"="shelter"]["shelter_type"!="public_transport"]']),
    "government": ("Government offices", ['["office"="government"]', '["amenity"="townhall"]']),
    "court": ("Courthouses", ['["amenity"="courthouse"]']),
    "police": ("Police", ['["amenity"="police"]']),
    "post": ("Post offices", ['["amenity"="post_office"]']),
    "consulate": ("Consulates and embassies", ['["amenity"="embassy"]', '["office"="diplomatic"]', '["diplomatic"]']),
}


# Cosmetic and specialty offices aren't what someone looking for a clinic needs.
SKIP_CLINICS = re.compile(r"plastic|cosmetic|aesthetic|med ?spa|laser|botox|vein|hair|weight loss|wellness spa|beauty|hgh|testosterone|anti.?aging|iv therapy|dermatolog", re.I)


def query():
    s, w, n, e = BBOX
    parts = []
    for _, filters in KINDS.values():
        for f in filters:
            parts.append(f"nwr{f}({s},{w},{n},{e});")
    return f"[out:json][timeout:90];({''.join(parts)});out center tags;"


def kind_of(tags):
    a, o, sf = tags.get("amenity"), tags.get("office"), tags.get("social_facility")
    if a == "hospital":
        return "hospital"
    if a in ("clinic", "doctors") or tags.get("healthcare") == "clinic":
        return "clinic"
    if a == "pharmacy":
        return "pharmacy"
    if a == "library":
        return "library"
    if a == "food_bank" or sf in ("food_bank", "shelter", "soup_kitchen") or (a == "shelter" and tags.get("shelter_type") != "public_transport"):
        return "food"
    if a == "embassy" or o == "diplomatic" or "diplomatic" in tags:
        return "consulate"
    if a == "courthouse":
        return "court"
    if a == "police":
        return "police"
    if a == "post_office":
        return "post"
    if o == "government" or a == "townhall":
        return "government"
    return None


def address(tags):
    num, street = tags.get("addr:housenumber", ""), tags.get("addr:street", "")
    return f"{num} {street}".strip()


def main():
    raw = json.loads(http(OVERPASS, data="data=" + urllib.parse.quote(query()), timeout=120))
    places, seen = [], set()
    for el in raw.get("elements", []):
        tags = el.get("tags", {})
        name = tags.get("name") or tags.get("official_name")
        kind = kind_of(tags)
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if not name or not kind or lat is None:
            continue
        if kind == "clinic" and SKIP_CLINICS.search(name):
            continue
        key = (kind, name.lower(), round(lat, 3), round(lon, 3))
        if key in seen:
            continue
        seen.add(key)
        places.append({"kind": kind, "name": name, "lat": round(lat, 6), "lon": round(lon, 6),
                       "address": address(tags), "phone": tags.get("phone") or tags.get("contact:phone") or ""})
    counts = {k: sum(1 for p in places if p["kind"] == k) for k in KINDS}
    print("  " + ", ".join(f"{k}: {n}" for k, n in counts.items()))
    write_json("places.json", {
        "source": "OpenStreetMap contributors", "source_url": "https://www.openstreetmap.org/copyright",
        "fetched": today(), "kinds": {k: v[0] for k, v in KINDS.items()}, "places": places,
    })


if __name__ == "__main__":
    main()
