"""The shape of Miami (land, Biscayne Bay, the Miami River, the causeways) from OpenStreetMap.

    python scripts/fetch_coast.py

Used only for the drawing behind the landing page headline. Writes data/coast.json.

OpenStreetMap stores the coast as lines with the land on the left. To get land
shapes we can fill, we cut those lines at the edge of our box and join the
pieces back together by walking around the box edge (the usual way to turn
coastlines into polygons).
"""

import json
import urllib.parse

from common import http, today, write_json

BOX = (25.73, -80.26, 25.83, -80.11)       # south, west, north, east: what the drawing shows
PAD = 0.06                                  # fetch a bit more so the coast lines run past the edges
OVERPASS = "https://overpass-api.de/api/interpreter"


def overpass(q):
    return json.loads(http(OVERPASS, data="data=" + urllib.parse.quote(q), timeout=120))


def geom(el):
    return [(p["lon"], p["lat"]) for p in el.get("geometry", [])]   # x, y


def stitch(ways):
    """Join coastline ways that share end points into longer chains."""
    chains = [list(w) for w in ways if len(w) > 1]
    joined = True
    while joined:
        joined = False
        for i in range(len(chains)):
            for j in range(len(chains)):
                if i != j and chains[i] and chains[j] and chains[i][-1] == chains[j][0]:
                    chains[i] += chains[j][1:]
                    chains[j] = []
                    joined = True
        chains = [c for c in chains if c]
    return chains


# ---- cutting lines at the box edge ----

W, S, E, N = BOX[1], BOX[0], BOX[3], BOX[2]
BW, BH = E - W, N - S
PERIM = 2 * (BW + BH)
CORNERS = [(0.0, (W, S)), (BW, (E, S)), (BW + BH, (E, N)), (2 * BW + BH, (W, N))]   # counterclockwise


def inside(p):
    return W <= p[0] <= E and S <= p[1] <= N


def perim_t(p):
    """Position of a point on the box edge, measured counterclockwise from the bottom-left corner."""
    x, y = p
    d = [abs(y - S), abs(x - E), abs(y - N), abs(x - W)]
    side = d.index(min(d))
    if side == 0:
        return x - W
    if side == 1:
        return BW + (y - S)
    if side == 2:
        return BW + BH + (E - x)
    return 2 * BW + BH + (N - y)


def clip(p, q):
    """Liang-Barsky: the part of segment p-q inside the box, as (t0, t1), or None."""
    dx, dy = q[0] - p[0], q[1] - p[1]
    t0, t1 = 0.0, 1.0
    for pp, qq in ((-dx, p[0] - W), (dx, E - p[0]), (-dy, p[1] - S), (dy, N - p[1])):
        if pp == 0:
            if qq < 0:
                return None
            continue
        r = qq / pp
        if pp < 0:
            t0 = max(t0, r)
        else:
            t1 = min(t1, r)
        if t0 > t1:
            return None
    return t0, t1


def at(p, q, t):
    return (p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t)


def pieces_of(chain):
    """Split a chain into pieces that run from one box edge to another."""
    out, cur = [], None
    if chain[0] == chain[-1] and all(inside(p) for p in chain):
        return [], [chain]            # a whole island inside the box
    if chain[0] == chain[-1]:         # a ring that crosses the edge: start it outside
        k = next(i for i, p in enumerate(chain) if not inside(p))
        chain = chain[k:-1] + chain[:k + 1]
    for p, q in zip(chain, chain[1:]):
        c = clip(p, q)
        if c is None:
            continue
        t0, t1 = c
        if t0 > 0 or cur is None:
            cur = [at(p, q, t0)]
        if t1 < 1:
            cur.append(at(p, q, t1))
            out.append(cur)
            cur = None
        else:
            cur.append(q)
    return out, []


def assemble(pieces):
    """Join edge-to-edge pieces into closed land shapes by walking the box edge counterclockwise."""
    info = [{"pts": pc, "tin": perim_t(pc[0]), "tout": perim_t(pc[-1]), "used": False} for pc in pieces]
    polys = []
    for start in info:
        if start["used"]:
            continue
        start["used"] = True
        poly, cur = list(start["pts"]), start
        for _ in range(len(info) + 1):
            nxt = min(info, key=lambda o: (o["tin"] - cur["tout"]) % PERIM if (o is start or not o["used"]) else 1e9)
            gap = (nxt["tin"] - cur["tout"]) % PERIM
            for ct, xy in sorted(CORNERS, key=lambda k: (k[0] - cur["tout"]) % PERIM):
                if (ct - cur["tout"]) % PERIM < gap:
                    poly.append(xy)
            if nxt is start:
                break
            nxt["used"] = True
            poly += nxt["pts"]
            cur = nxt
        polys.append(poly)
    return polys


def rnd(pts):
    return [[round(y, 5), round(x, 5)] for x, y in pts]     # back to lat, lon


def main():
    s, w, n, e = BOX[0] - PAD, BOX[1] - PAD, BOX[2] + PAD, BOX[3] + PAD
    raw = overpass(f"""[out:json][timeout:90];
(way["natural"="coastline"]({s},{w},{n},{e}););out geom;""")
    chains = stitch([geom(el) for el in raw["elements"]])
    pieces, islands = [], []
    for c in chains:
        p, i = pieces_of(c)
        pieces += p
        islands += i
    land = assemble(pieces) + islands
    print(f"  coastline: {len(chains)} chains -> {len(land)} land shapes")

    bs, bw, bn, be = BOX
    raw = overpass(f"""[out:json][timeout:60];
(way["waterway"="river"]["name"="Miami River"]({bs},{bw},{bn},{be});
 way["highway"]["name"~"Causeway"]({bs},{bw},{bn},{be}););out geom;""")
    river, roads = [], []
    for el in raw["elements"]:
        pts = rnd(geom(el))
        (river if el["tags"].get("waterway") == "river" else roads).append(pts)
    print(f"  Miami River: {len(river)} pieces, causeways: {len(roads)} pieces")

    write_json("coast.json", {"source": "OpenStreetMap contributors", "fetched": today(), "bbox": list(BOX),
                              "land": [rnd(p) for p in land if len(p) > 3], "river": river, "causeways": roads})


if __name__ == "__main__":
    main()
