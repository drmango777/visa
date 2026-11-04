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
PLACES = _load("places.json")
TRANSLATIONS = {k: v for k, v in (_load("translations.json") or {}).items() if not k.startswith("_")}


def data_status():
    """Which real data files are loaded. The site shows a notice for anything missing."""
    return {
        "rent": RENT is not None,
        "prices": PRICES is not None,
        "buses": STOPS is not None and ROUTES is not None,
        "places": PLACES is not None,
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


COAST = _load("coast.json")


def _thin(pts, step):
    return pts[::step] + ([pts[-1]] if len(pts) > 1 and (len(pts) - 1) % step else [])


def route_lines(step=4, limit=55):
    """The landing page drawing: land and water, the Miami River, causeways, and bus routes."""
    bbox = tuple(COAST["bbox"]) if COAST else (25.73, -80.26, 25.83, -80.11)
    out = {"bbox": bbox, "lines": [], "land": [], "river": [], "causeways": []}
    if COAST:
        s, w, n, e = bbox

        def keep(i, p, last):   # every other point, but never the ones on the box edge (the land shape's corners)
            return i % 2 == 0 or i == last or min(abs(p[0] - s), abs(p[0] - n), abs(p[1] - w), abs(p[1] - e)) < 1e-6

        out["land"] = [[p for i, p in enumerate(poly) if keep(i, p, len(poly) - 1)] for poly in COAST["land"]]
        out["river"] = COAST["river"]
        out["causeways"] = [_thin(c, 2) for c in COAST["causeways"] if len(c) > 2]
    if ROUTES is None:
        return out
    s, w, n, e = bbox
    lines = []
    for r in ROUTES["routes"].values():
        for shape in r.get("shapes", []):
            pts = [p for p in shape if s <= p[0] <= n and w <= p[1] <= e]
            if len(pts) < 8:
                continue
            thin = pts[::step] + [pts[-1]]
            lines.append([[round(a, 4), round(b, 4)] for a, b in thin])
    lines.sort(key=len, reverse=True)
    out["lines"] = lines[:limit]
    return out


def routes_gone(area_id):
    return CHANGES.get("gone", {}).get(area_id, [])


# ---------- useful places (hospitals, libraries, ...) ----------

PLACE_KINDS = (PLACES or {}).get("kinds", {})
WALK_EASY_MILES = 0.6   # closer than this, we just say "walk"


def _stops_near_fast(lat, lon, miles):
    """Like _stops_within, but skips far stops cheaply first."""
    box = miles / 69.0 * 1.2
    out = []
    for s in (STOPS or {}).get("stops", []):
        if abs(s["lat"] - lat) > box or abs(s["lon"] - lon) > box * 1.15:
            continue
        d = miles_between(lat, lon, s["lat"], s["lon"])
        if d <= miles:
            out.append((d, s))
    return sorted(out, key=lambda x: x[0])


def _bus_between(home_stops, lat, lon):
    """The one bus route that stops near both home and the place, with the least walking."""
    place_stops = _stops_near_fast(lat, lon, ACCESS_MILES)
    if not home_stops or not place_stops:
        return None
    a_by, b_by = {}, {}
    for d, s in home_stops:
        for r in s["routes"]:
            a_by.setdefault(r, (d, s))
    for d, s in place_stops:
        for r in s["routes"]:
            b_by.setdefault(r, (d, s))
    best = None
    for r in a_by.keys() & b_by.keys():
        (da, sa), (db, sb) = a_by[r], b_by[r]
        if sa["id"] == sb["id"]:
            continue
        if best is None or da + db < best[0]:
            best = (da + db, r, sa, sb)
    if best is None:
        return None
    _, r, sa, sb = best
    return {"route": r, "name": (ROUTES or {}).get("routes", {}).get(r, {}).get("name", ""),
            "board": sa["name"], "off": sb["name"]}


def places_near(lat, lon, kinds=None, per_kind=5):
    """The closest few places of each kind, and how to get there (walk, or which bus)."""
    if PLACES is None:
        return {"available": False}
    want = [k for k in (kinds or PLACE_KINDS) if k in PLACE_KINDS]
    home_stops = _stops_near_fast(lat, lon, ACCESS_MILES) if STOPS else []
    out = {}
    for k in want:
        rows = sorted(((miles_between(lat, lon, p["lat"], p["lon"]), p) for p in PLACES["places"] if p["kind"] == k),
                      key=lambda x: x[0])[:per_kind]
        items = []
        for i, (d, p) in enumerate(rows):
            item = {**p, "miles": round(d, 2), "walk_minutes": round(d * CIRCUITY / WALK_MPH * 60)}
            if i == 0 and d > WALK_EASY_MILES:   # only work out the bus for the closest one
                item["bus"] = _bus_between(home_stops, p["lat"], p["lon"])
            items.append(item)
        out[k] = {"label": PLACE_KINDS[k], "items": items}
    return {"available": True, "kinds": out, "source": PLACES["source"], "source_url": PLACES["source_url"]}


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
        "places": places_near(lat, lon),
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


# Curfew answers in the three languages the site supports.
# Spanish and Haitian Creole should be checked by a native speaker.
CURFEW_TEXT = {
    "en": {
        "too_early": "Your curfew is before the time you finish. Check the two times.",
        "walk_line": "Walking the whole way is about {m} minutes. You'd get there around {t}, {when} your curfew.",
        "before": "before", "after": "after",
        "too_far": "These two places are {mi} miles apart, which is too far to walk.",
        "advice": "If it's tight or late, talk to your officer or case manager about changing the time. Do it before that day, not after.",
        "notes": ["This is an estimate, not a promise. Buses run late. Before you count on it, check the GO Miami-Dade Transit app or call 311.",
                  "We can't see transfers or which way each bus is heading, so make sure you're on the right side of the street.",
                  "Not legal advice. Your officer or case manager decides what counts as late."],
        "no_data": "Bus data isn't loaded yet, so we can't check the bus.",
        "no_stops": "We don't have bus stops close to one of these places. Check the GO Miami-Dade Transit app.",
        "no_bus": "No. The last bus on route {r} near you leaves around {t}. You'd get to the stop after that.",
        "missed_line": "Last bus on route {r} from {stop}: about {t}.",
        "walk": "We couldn't find one bus between these two places, but you could walk it.",
        "no_direct": "We couldn't find one bus between these two places. You may need to transfer, and we can't check transfers yet.",
        "ok": "Yes. You'd get home around {t}. That's {m} minutes before your curfew.",
        "tight": "Maybe. You'd get home around {t}, only {m} minutes before your curfew. One late bus could break it.",
        "late": "No. You'd get home around {t}, {m} minutes after your curfew.",
        "step_walk": "Walk about {m} to the stop at {stop}.",
        "step_take": "Take route {r}{name}.",
        "step_wait": " A bus comes about every {h} minutes, so plan to wait about {w}.",
        "step_guess": " We don't know how often it comes, so we guessed.",
        "step_ride": "Ride about {m}. Get off near {stop}.",
        "step_home": "Walk about {m} to where you're staying.",
        "last_line": "The last bus on route {r} from that stop leaves around {t}.",
        "no_last": "We don't have the last-bus time for this route. Check the app.",
        "min1": "1 minute", "mins": "{n} minutes",
    },
    "es": {
        "too_early": "Tu toque de queda es antes de la hora en que terminas. Revisa las dos horas.",
        "walk_line": "Caminando todo el trayecto son unos {m} minutos. Llegarías alrededor de las {t}, {when} de tu toque de queda.",
        "before": "antes", "after": "después",
        "too_far": "Estos dos lugares están a {mi} millas de distancia. Es demasiado lejos para caminar.",
        "advice": "Si es justo o tarde, habla con tu oficial o tu trabajador de casos para cambiar la hora. Hazlo antes de ese día, no después.",
        "notes": ["Esto es un cálculo, no una promesa. Los buses se atrasan. Antes de contar con esto, revisa la aplicación GO Miami-Dade Transit o llama al 311.",
                  "No podemos ver trasbordos ni hacia dónde va cada bus, así que asegúrate de estar en el lado correcto de la calle.",
                  "Esto no es consejo legal. Tu oficial o tu trabajador de casos decide qué cuenta como tarde."],
        "no_data": "Los datos de los buses todavía no están cargados, así que no podemos revisar el bus.",
        "no_stops": "No tenemos paradas de bus cerca de uno de estos lugares. Revisa la aplicación GO Miami-Dade Transit.",
        "no_bus": "No. El último bus de la ruta {r} cerca de ti sale alrededor de las {t}. Llegarías a la parada después.",
        "missed_line": "Último bus de la ruta {r} desde {stop}: alrededor de las {t}.",
        "walk": "No encontramos un solo bus entre estos dos lugares, pero podrías ir caminando.",
        "no_direct": "No encontramos un solo bus entre estos dos lugares. Puede que tengas que hacer trasbordo, y todavía no podemos revisar trasbordos.",
        "ok": "Sí. Llegarías a casa alrededor de las {t}. Son {m} minutos antes de tu toque de queda.",
        "tight": "Tal vez. Llegarías a casa alrededor de las {t}, solo {m} minutos antes de tu toque de queda. Un bus atrasado podría arruinarlo.",
        "late": "No. Llegarías a casa alrededor de las {t}, {m} minutos después de tu toque de queda.",
        "step_walk": "Camina unos {m} hasta la parada en {stop}.",
        "step_take": "Toma la ruta {r}{name}.",
        "step_wait": " Pasa un bus más o menos cada {h} minutos, así que cuenta con esperar unos {w}.",
        "step_guess": " No sabemos cada cuánto pasa, así que lo calculamos.",
        "step_ride": "Viaja unos {m}. Bájate cerca de {stop}.",
        "step_home": "Camina unos {m} hasta donde te quedas.",
        "last_line": "El último bus de la ruta {r} desde esa parada sale alrededor de las {t}.",
        "no_last": "No tenemos la hora del último bus de esta ruta. Revisa la aplicación.",
        "min1": "1 minuto", "mins": "{n} minutos",
    },
    "ht": {
        "too_early": "Kouvrefe ou a pi bonè pase lè ou fini. Tcheke de lè yo.",
        "walk_line": "Si ou mache tout wout la, li pran anviwon {m} minit. Ou ta rive vè {t}, {when} kouvrefe ou.",
        "before": "anvan", "after": "apre",
        "too_far": "De kote sa yo a {mi} mil youn ak lòt. Li twò lwen pou mache.",
        "advice": "Si li sere oswa an reta, pale ak ofisye ou oswa jesyonè dosye ou pou chanje lè a. Fè sa anvan jou sa a, pa apre.",
        "notes": ["Sa a se yon estimasyon, se pa yon pwomès. Bis yo konn an reta. Anvan ou konte sou sa, tcheke aplikasyon GO Miami-Dade Transit la oswa rele 311.",
                  "Nou pa ka wè koneksyon ant bis yo ni ki direksyon chak bis ap pran, kidonk asire ou ou sou bon bò lari a.",
                  "Sa a se pa konsèy legal. Se ofisye ou oswa jesyonè dosye ou ki deside sa ki konte kòm an reta."],
        "no_data": "Done bis yo poko chaje, kidonk nou pa ka tcheke bis la.",
        "no_stops": "Nou pa gen arè bis toupre youn nan kote sa yo. Tcheke aplikasyon GO Miami-Dade Transit la.",
        "no_bus": "Non. Dènye bis wout {r} toupre ou a pati vè {t}. Ou ta rive nan arè a apre sa.",
        "missed_line": "Dènye bis wout {r} soti {stop}: vè {t}.",
        "walk": "Nou pa jwenn yon sèl bis ant de kote sa yo, men ou ta ka mache.",
        "no_direct": "Nou pa jwenn yon sèl bis ant de kote sa yo. Ou ka bezwen chanje bis, epi nou poko ka tcheke sa.",
        "ok": "Wi. Ou ta rive lakay vè {t}. Sa fè {m} minit anvan kouvrefe ou.",
        "tight": "Petèt. Ou ta rive lakay vè {t}, sèlman {m} minit anvan kouvrefe ou. Yon bis an reta ta ka gate sa.",
        "late": "Non. Ou ta rive lakay vè {t}, {m} minit apre kouvrefe ou.",
        "step_walk": "Mache anviwon {m} pou rive nan arè a nan {stop}.",
        "step_take": "Pran wout {r}{name}.",
        "step_wait": " Yon bis pase apeprè chak {h} minit, kidonk prevwa tann anviwon {w}.",
        "step_guess": " Nou pa konnen konbyen fwa li pase, kidonk nou devine.",
        "step_ride": "Rete nan bis la anviwon {m}. Desann toupre {stop}.",
        "step_home": "Mache anviwon {m} pou rive kote ou rete a.",
        "last_line": "Dènye bis wout {r} soti nan arè sa a pati vè {t}.",
        "no_last": "Nou pa gen lè dènye bis pou wout sa a. Tcheke aplikasyon an.",
        "min1": "1 minit", "mins": "{n} minit",
    },
}


def curfew_check(lat1, lon1, lat2, lon2, end_min, curfew_min, lang="en"):
    T = CURFEW_TEXT.get(lang, CURFEW_TEXT["en"])
    if curfew_min <= end_min:
        raise ValueError(T["too_early"])
    straight = miles_between(lat1, lon1, lat2, lon2)
    walk_min = straight * CIRCUITY / WALK_MPH * 60
    walk_arrive = end_min + walk_min
    walk_ok = straight <= 4 and walk_arrive <= curfew_min
    if straight <= 4:
        walk_line = T["walk_line"].format(m=round(walk_min), t=fmt_time(walk_arrive), when=T["before"] if walk_ok else T["after"])
    else:
        walk_line = T["too_far"].format(mi=f"{straight:.1f}")
    out = {"status": "no_data", "headline": "", "plan": [], "last_bus_line": "", "walk_line": walk_line,
           "walk_ok": walk_ok, "miles_apart": round(straight, 1), "end": fmt_time(end_min), "curfew": fmt_time(curfew_min),
           "advice": T["advice"], "notes": list(T["notes"])}
    if STOPS is None:
        out["headline"] = T["no_data"]
        return out
    near_a, near_b = _stops_within(lat1, lon1, ACCESS_MILES), _stops_within(lat2, lon2, ACCESS_MILES)
    if not near_a or not near_b:
        out["headline"] = T["no_stops"]
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
        out["headline"] = T["no_bus"].format(r=missed["route"], t=fmt_time(missed["last"]))
        out["last_bus_line"] = T["missed_line"].format(r=missed["route"], stop=missed["board"], t=fmt_time(missed["last"]))
        return out
    if best is None:
        out["status"] = "walk" if walk_ok else "no_direct"
        out["headline"] = T[out["status"]]
        return out
    margin = curfew_min - best["arrive"]
    arrive = fmt_time(best["arrive"])
    if margin >= 20:
        out["status"] = "ok"
    elif margin >= 0:
        out["status"] = "tight"
    else:
        out["status"] = "late"
    out["headline"] = T[out["status"]].format(t=arrive, m=round(abs(margin)))
    nm = best["name"]

    def mins(x):
        n = max(1, round(x))
        return T["min1"] if n == 1 else T["mins"].format(n=n)

    take = T["step_take"].format(r=best["route"], name=f" ({nm})" if nm else "")
    take += T["step_wait"].format(h=round(best["headway"]), w=round(best["wait"])) if best["known"] else T["step_guess"]
    out["plan"] = [T["step_walk"].format(m=mins(best["walk_a"]), stop=best["board"]), take,
                   T["step_ride"].format(m=mins(best["ride"]), stop=best["off"]),
                   T["step_home"].format(m=mins(best["walk_b"]))]
    if best["last"] is not None:
        out["last_bus_line"] = T["last_line"].format(r=best["route"], t=fmt_time(best["last"]))
    else:
        out["last_bus_line"] = T["no_last"]
    return out
