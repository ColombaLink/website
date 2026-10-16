"""Turn raw swisstopo + OpenStreetMap data into the Basel city data embedded in Secret Pigeon.

Usage: python3 -I secret-pigeon/build/build_city.py <raw-dir> <work-dir> <out-js>
  raw-dir   folder written by fetch_data.py (treated as untrusted input)
  work-dir  scratch folder for converted images and a cache of the parsed 3D buildings
  out-js    JS file that defines `const BASEL = {...}`; assemble.py injects it into the game

Buildings are swisstopo's swissBUILDINGS3D 3.0: real roof shapes and heights. OpenStreetMap supplies the landmarks,
the buildings that get facades in the game, trees, bridges, ferries and place names.
Coordinates: swiss LV95 metres relative to the Mittlere Brücke. Game x = east / S, game z = -north / S,
heights are multiplied by VS (a mild vertical exaggeration so landmarks read from the air).
"""
import base64
import gzip
import json
import math
import os
import re
import struct
import subprocess
import sys
from array import array
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sb3d  # noqa: E402

S = 5.0             # metres per game unit on the ground
EH = 1.4            # vertical exaggeration
VS = EH / S         # game units per metre of height
AREA = (-1450.0, 1950.0, -1650.0, 1450.0)   # west, east, south, north in metres around the origin
ORIGIN_LATLON = (47.56015, 7.58972)          # Mittlere Brücke, replaced by the bridge outline's centre if found
LEVEL = 3.0         # metres per storey
MARKT = (-152.0, -228.0)                     # rough Marktplatz offset, refined from data

# OSM ids of landmarks: the Münster and Spalentor are hand-modelled (their outlines are dropped from the generic
# city); the Rathaus keeps its real outline but gets its red walls and a tower in the game
LANDMARKS = {"muenster": ("way", 137472252), "rathaus": ("relation", 7634307), "spalentor": ("relation", 3531078),
             "elisabethen": ("way", 105047858)}
HAND_MODELLED = {"muenster", "spalentor", "elisabethen"}
LANDMARK_TOWERS = {"elisabethen": ("way", 407463501)}      # the Elisabethenkirche's tower, as an OSM building part
RATHAUS_ID = 7634307
TOWERS = {"roche2": ("way", 317511699), "messe": ("way", 315916699)}
# swisstopo's OBJEKTART -> a small code the game uses (windows go on 0, 1 and 10)
KINDS = {"Gebaeude Einzelhaus": 0, "Hochhaus": 1, "Sakrales Gebaeude": 2, "Kapelle": 2, "Sakraler Turm": 2,
         "Lagertank": 3, "Flugdach": 4, "Offenes Gebaeude": 5, "Treibhaus": 6, "Mauer gross": 7,
         "Mauer gross gedeckt": 7, "Turm": 8, "Hochkamin": 8, "Verbindungsbruecke": 9, "Im Bau": 10}
AVIF_Q = 50         # quality of the ground photo (AVIF via sips)
# tree shapes in the game: 0 round crown, 1 conifer, 2 columnar
CONIFERS = {"Pinus", "Picea", "Abies", "Taxus", "Thuja", "Cedrus", "Larix", "Pseudotsuga", "Sequoiadendron", "Sequoia",
            "Metasequoia", "Taxodium", "Juniperus", "Chamaecyparis", "Cupressus", "Cryptomeria", "Tsuga", "Calocedrus",
            "Platycladus", "Cupressocyparis"}
COLUMNAR = ("Fastigiata", "fastigiata", "Italica", "italica", "Columnar", "columnar", "Pyramidalis", "pyramidalis")


def tree_byte(shape, size):
    """Pack a tree's shape (top two bits) and size (crown scale 0.5 to 2.0, low six bits) into one byte."""
    return shape << 6 | max(0, min(63, int(round((size - 0.5) * 40))))


def lv95(lat, lon):
    """WGS84 -> LV95 (swisstopo approximate formulas, about 1 m accuracy)."""
    p = (lat * 3600 - 169028.66) / 10000
    l = (lon * 3600 - 26782.5) / 10000
    e = 2600072.37 + 211455.93 * l - 10938.51 * l * p - 0.36 * l * p * p - 44.54 * l ** 3
    n = 1200147.07 + 308807.95 * p + 3745.25 * l * l + 76.63 * p * p - 194.56 * l * l * p + 119.79 * p ** 3
    return e, n


def num(v):
    if v is None:
        return None
    try:
        return float(str(v).lower().replace("m", "").replace(",", ".").split(";")[0].strip())
    except ValueError:
        return None


def area2(r):
    return sum(r[i][0] * r[(i + 1) % len(r)][1] - r[(i + 1) % len(r)][0] * r[i][1] for i in range(len(r))) / 2


def centroid(r):
    a = area2(r)
    if abs(a) < 1e-6:
        return sum(p[0] for p in r) / len(r), sum(p[1] for p in r) / len(r)
    cx = cy = 0.0
    for i in range(len(r)):
        x0, y0 = r[i]
        x1, y1 = r[(i + 1) % len(r)]
        f = x0 * y1 - x1 * y0
        cx += (x0 + x1) * f
        cy += (y0 + y1) * f
    return cx / (6 * a), cy / (6 * a)


def inside(pt, r):
    x, y = pt
    c = False
    j = len(r) - 1
    for i in range(len(r)):
        xi, yi = r[i]
        xj, yj = r[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi:
            c = not c
        j = i
    return c


def rdp(pts, eps):
    if len(pts) < 3:
        return pts
    (x0, y0), (x1, y1) = pts[0], pts[-1]
    dx, dy = x1 - x0, y1 - y0
    L = math.hypot(dx, dy) or 1e-9
    imax, dmax = 0, -1.0
    for i in range(1, len(pts) - 1):
        d = abs(dy * (pts[i][0] - x0) - dx * (pts[i][1] - y0)) / L
        if d > dmax:
            imax, dmax = i, d
    if dmax > eps:
        return rdp(pts[: imax + 1], eps)[:-1] + rdp(pts[imax:], eps)
    return [pts[0], pts[-1]]


def simplify_ring(r, eps=0.25):
    if len(r) > 1 and r[0] == r[-1]:
        r = r[:-1]
    if len(r) < 3:
        return r
    # split at the vertex farthest from the first so RDP has two open chains
    far = max(range(len(r)), key=lambda i: (r[i][0] - r[0][0]) ** 2 + (r[i][1] - r[0][1]) ** 2)
    a = rdp(r[: far + 1], eps)
    b = rdp(r[far:] + [r[0]], eps)
    out = a[:-1] + b[:-1]
    return out if len(out) >= 3 else r


def convex(r):
    sign = 0
    n = len(r)
    for i in range(n):
        (x0, y0), (x1, y1), (x2, y2) = r[i], r[(i + 1) % n], r[(i + 2) % n]
        cr = (x1 - x0) * (y2 - y1) - (y1 - y0) * (x2 - x1)
        if abs(cr) < 1e-9:
            continue
        s = 1 if cr > 0 else -1
        if sign and s != sign:
            return False
        sign = s
    return True


def star_shaped(r):
    """True when every edge of the CCW ring faces the centroid, so the roof can slope towards it."""
    cx, cy = centroid(r)
    for i in range(len(r)):
        (x0, y0), (x1, y1) = r[i], r[(i + 1) % len(r)]
        if (x1 - x0) * (cy - y0) - (y1 - y0) * (cx - x0) <= 0.05:
            return False
    return True


def tile_tint(c):
    """Pitched roofs in Basel are clay tiles: warm the reddish ones, and pull grey or tree-covered ones towards terracotta."""
    r, g, b = c
    if g >= r or max(c) - min(c) < 14:
        r, g, b = r * 0.4 + 156 * 0.6, g * 0.4 + 92 * 0.6, b * 0.4 + 70 * 0.6
    else:
        lum = (r + g + b) / 3
        r, g, b = (lum + (v - lum) * 1.4 for v in (r, g, b))
        r, g, b = r + 10, g - 2, b - 10
    return tuple(int(min(255, max(0, v))) for v in (r, g, b))


def obb(r):
    cx, cy = centroid(r)
    sxx = sum((p[0] - cx) ** 2 for p in r)
    syy = sum((p[1] - cy) ** 2 for p in r)
    sxy = sum((p[0] - cx) * (p[1] - cy) for p in r)
    ang = 0.5 * math.atan2(2 * sxy, sxx - syy)
    ux, uy = math.cos(ang), math.sin(ang)
    us = [(p[0] - cx) * ux + (p[1] - cy) * uy for p in r]
    vs = [-(p[0] - cx) * uy + (p[1] - cy) * ux for p in r]
    mu, mv = (max(us) + min(us)) / 2, (max(vs) + min(vs)) / 2
    return {"cx": cx + mu * ux - mv * uy, "cy": cy + mu * uy + mv * ux, "len": max(us) - min(us),
            "wid": max(vs) - min(vs), "ang": ang}


def join_rings(segments):
    """Join multipolygon member ways into closed rings."""
    segs = [s[:] for s in segments if len(s) >= 2]
    rings = []
    while segs:
        cur = segs.pop(0)
        changed = True
        while cur[0] != cur[-1] and changed:
            changed = False
            for i, s in enumerate(segs):
                if s[0] == cur[-1]:
                    cur += s[1:]
                elif s[-1] == cur[-1]:
                    cur += s[::-1][1:]
                elif s[-1] == cur[0]:
                    cur = s[:-1] + cur
                elif s[0] == cur[0]:
                    cur = s[::-1][:-1] + cur
                else:
                    continue
                segs.pop(i)
                changed = True
                break
        if cur[0] == cur[-1] and len(cur) >= 4:
            rings.append(cur)
    return rings


class Geo:
    def __init__(self, origin):
        self.e0, self.n0 = lv95(*origin)

    def local(self, lat, lon):
        e, n = lv95(lat, lon)
        return round(e - self.e0, 2), round(n - self.n0, 2)


def element_rings(el, geo):
    """Outer and inner rings (local metres) of a way or multipolygon relation."""
    if el["type"] == "way":
        g = el.get("geometry") or []
        pts = [geo.local(p["lat"], p["lon"]) for p in g if p]
        if len(pts) >= 4 and pts[0] == pts[-1]:
            return [pts], []
        return [], []
    outer, inner = [], []
    for m in el.get("members", []):
        if m.get("type") != "way" or not m.get("geometry"):
            continue
        pts = [geo.local(p["lat"], p["lon"]) for p in m["geometry"] if p]
        (inner if m.get("role") == "inner" else outer).append(pts)
    return join_rings(outer), join_rings(inner)


DEFAULT_LEVELS = {"garage": 1, "garages": 1, "shed": 1, "carport": 1, "hut": 1, "kiosk": 1, "service": 1,
                  "transformer_tower": 1, "toilets": 1, "house": 2, "detached": 2, "semidetached_house": 2,
                  "bungalow": 1, "terrace": 3, "retail": 3, "industrial": 3, "warehouse": 3, "school": 3,
                  "kindergarten": 2, "church": 5, "chapel": 2, "apartments": 5, "office": 5, "hotel": 5}


def building_heights(t, area_m2):
    """(base, top, roof height) in metres, plus whether the roof is pitched."""
    kind = t.get("building") or t.get("building:part") or "yes"
    h, mh = num(t.get("height")), num(t.get("min_height"))
    lv, rlv, minlv = num(t.get("building:levels")), num(t.get("roof:levels")), num(t.get("building:min_level"))
    shape = t.get("roof:shape")
    pitched = (shape is not None and shape not in ("flat", "skillion")) or (shape is None and bool(rlv))
    rh = num(t.get("roof:height"))
    if rh is None:
        rh = rlv * 2.8 if rlv else 0.0
    base = mh if mh is not None else (minlv * LEVEL if minlv else 0.0)
    if h is None:
        if lv is None:
            lv = DEFAULT_LEVELS.get(kind)
            if lv is None:
                lv = 1 if area_m2 < 40 else 3 if area_m2 < 140 else 4
        h = lv * LEVEL + (0 if minlv else 1.0) + rh
    if kind == "roof":            # canopies: a thin slab on posts
        h = max(h, 5.0)
        base = max(base, h - 1.0)
        pitched, rh = False, 0.0
    if not pitched:
        rh = 0.0
    h = max(h, base + 1.0)
    return base, h, min(rh, max(0.0, h - base - 1.0)), pitched, kind


def varint(out, v):
    while v >= 0x80:
        out.append((v & 0x7F) | 0x80)
        v >>= 7
    out.append(v)


def zigzag(v):
    return v * 2 if v >= 0 else -v * 2 - 1


def scan_fill(poly):
    """Pixel centres (ix, iy) inside a polygon given in pixel coordinates."""
    ys = [p[1] for p in poly]
    n = len(poly)
    for iy in range(int(math.floor(min(ys))), int(math.ceil(max(ys))) + 1):
        y = iy + 0.5
        xs = []
        for i in range(n):
            (x0, y0), (x1, y1) = poly[i], poly[(i + 1) % n]
            if (y0 > y) != (y1 > y):
                xs.append(x0 + (y - y0) * (x1 - x0) / (y1 - y0))
        xs.sort()
        for k in range(0, len(xs) - 1, 2):
            for ix in range(int(math.ceil(xs[k] - 0.5)), int(math.floor(xs[k + 1] - 0.5)) + 1):
                yield ix, iy


def load_3d(raw, work, geo, keep_local):
    """swissBUILDINGS3D buildings in local metres (heights still above sea level), cached in the work folder
    because parsing the CityGML takes about a minute."""
    folder = os.path.join(raw, "sb3d")
    zips = sorted(os.path.join(folder, f) for f in os.listdir(folder) if f.endswith(".citygml.zip"))
    if not zips:
        raise SystemExit("no swissBUILDINGS3D tiles in <raw-dir>/sb3d: run fetch_data.py first")
    key = [[os.path.basename(z), os.path.getsize(z)] for z in zips] + [[round(geo.e0, 2), round(geo.n0, 2)], list(AREA)]
    cache = os.path.join(work, "sb3d-cache.json")
    if os.path.exists(cache):
        c = json.load(open(cache))
        if c.get("key") == key:
            return c["buildings"]
    out = []
    for b in sb3d.read_buildings(zips, lambda e, n: keep_local(e - geo.e0, n - geo.n0)):
        out.append({"kind": b["kind"], "ground": b["ground"], "wall_min": b["wall_min"],
                    "roof": [[[round(p[0] - geo.e0, 2), round(p[1] - geo.n0, 2), round(p[2], 2)] for p in f]
                             for f in b["roof"]]})
    with open(cache, "w") as f:
        json.dump({"key": key, "buildings": out}, f, separators=(",", ":"))
    return out


def main(raw, work, out_js):
    os.makedirs(work, exist_ok=True)
    bjs = json.load(open(os.path.join(raw, "osm_buildings.json")))["elements"]
    xjs = json.load(open(os.path.join(raw, "osm_extras.json")))["elements"]

    origin = ORIGIN_LATLON
    for el in xjs:
        if el["type"] == "way" and el.get("tags", {}).get("man_made") == "bridge" and el["id"] == 653605685:
            g = [p for p in el.get("geometry") or [] if p]
            origin = (sum(p["lat"] for p in g) / len(g), sum(p["lon"] for p in g) / len(g))
    geo = Geo(origin)
    W, E, Sth, N = AREA

    def in_area(x, y, m=0):
        return W - m <= x <= E + m and Sth - m <= y <= N + m

    # ---------- aerial mosaic (swisstopo SWISSIMAGE, one JPEG per kilometre) ----------
    tiles = {}
    for f in os.listdir(os.path.join(raw, "ortho")):
        m = re.match(r"(\d{4})-(\d{4})\.jpg$", f)
        if m:
            tiles[(int(m.group(1)), int(m.group(2)))] = os.path.join(raw, "ortho", f)
    te0, te1 = min(k[0] for k in tiles), max(k[0] for k in tiles)
    tn0, tn1 = min(k[1] for k in tiles), max(k[1] for k in tiles)
    T = None
    mosaic = None
    for (te, tn), path in sorted(tiles.items()):
        bmp = os.path.join(work, f"ortho_{te}-{tn}.bmp")
        if not os.path.exists(bmp):
            subprocess.run(["sips", "-s", "format", "bmp", path, "--out", bmp], check=True, capture_output=True)
        w, h, rgb = read_bmp(bmp)
        if T is None:
            T = w
            MW, MH = (te1 - te0 + 1) * T, (tn1 - tn0 + 1) * T
            mosaic = bytearray(MW * MH * 3)
        assert (w, h) == (T, T), (path, w, h)
        ox, oy = (te - te0) * T, (tn1 - tn) * T
        for y in range(T):
            mosaic[((oy + y) * MW + ox) * 3:((oy + y) * MW + ox + T) * 3] = rgb[y * T * 3:(y + 1) * T * 3]
    MPP = 1000.0 / T                                   # metres per pixel
    me0, mn0 = te0 * 1000.0, (tn1 + 1) * 1000.0       # LV95 of the mosaic's top-left corner

    def px(x, y):  # local metres -> mosaic pixel
        return (geo.e0 + x - me0) / MPP, (mn0 - (geo.n0 + y)) / MPP

    def pixel(ix, iy):
        ix = min(MW - 1, max(0, ix))
        iy = min(MH - 1, max(0, iy))
        i = (iy * MW + ix) * 3
        return mosaic[i], mosaic[i + 1], mosaic[i + 2]

    def colour_of(got):
        got.sort(key=lambda c: c[0] * 0.3 + c[1] * 0.59 + c[2] * 0.11)
        k = len(got)
        keep = got[k // 4: max(k // 4 + 1, k - k // 10)]       # drop shadows and glints
        r = sum(c[0] for c in keep) / len(keep)
        g = sum(c[1] for c in keep) / len(keep)
        b = sum(c[2] for c in keep) / len(keep)
        lum = (r + g + b) / 3                                   # more saturation and light for the game look
        r, g, b = (lum + (c - lum) * 1.6 for c in (r, g, b))
        return tuple(int(min(255, max(0, c * 1.1 + 8))) for c in (r, g, b))

    def roof_colour(rings, shrink=1.0):
        got = []
        for ring in rings:
            cx, cy = centroid(ring)
            poly = [px(cx + (p[0] - cx) * shrink, cy + (p[1] - cy) * shrink) for p in ring]
            got += [pixel(ix, iy) for ix, iy in scan_fill(poly)]
        if len(got) < 3:
            cx, cy = px(*centroid(rings[0]))
            got = [pixel(int(cx + dx), int(cy + dy)) for dx in (-1, 0, 1) for dy in (-1, 0, 1)]
        return colour_of(got)

    # ---------- ground image: crop, feather the border, encode (AVIF, plus a small JPEG for old browsers) ----------
    gx0, gy0 = px(W, N)
    GW, GH = int(round((E - W) / MPP)), int(round((N - Sth) / MPP))
    crop = bytearray(GW * GH * 3)
    for y in range(GH):
        sy = int(gy0) + y
        crop[y * GW * 3:(y + 1) * GW * 3] = mosaic[(sy * MW + int(gx0)) * 3:(sy * MW + int(gx0) + GW) * 3]
    acc = [sum(crop[c::3 * 97]) for c in range(3)]
    avg = [a / len(crop[0::3 * 97]) for a in acc]
    F = int(round(96 / MPP))
    for y in range(GH):
        for x in list(range(min(F, GW))) + list(range(max(F, GW - F), GW)) if F <= y < GH - F else range(GW):
            d = min(x, y, GW - 1 - x, GH - 1 - y)
            if d >= F:
                continue
            t = d / F
            t = t * t * (3 - 2 * t)
            i = (y * GW + x) * 3
            for c in range(3):
                crop[i + c] = int(avg[c] * (1 - t) + crop[i + c] * t)
    gbmp, gavif, gsmall = (os.path.join(work, n) for n in ("ground.bmp", "ground.avif", "ground-small.jpg"))
    write_bmp(gbmp, GW, GH, crop)
    subprocess.run(["sips", "-s", "format", "avif", "-s", "formatOptions", str(AVIF_Q), gbmp, "--out", gavif],
                   check=True, capture_output=True)
    subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "70", "-z", str(GH // 4), str(GW // 4),
                    gbmp, "--out", gsmall], check=True, capture_output=True)
    ground_b64 = base64.b64encode(open(gavif, "rb").read()).decode()
    small_b64 = base64.b64encode(open(gsmall, "rb").read()).decode()

    # ---------- OpenStreetMap buildings: landmarks, towers and the buildings that get facades ----------
    parts, outlines = [], []
    lm_rings = {}
    for el in bjs:
        t = el.get("tags", {})
        if t.get("location") == "underground" or (num(t.get("layer")) or 0) < 0 or t.get("building") == "no":
            continue
        outer, inner = element_rings(el, geo)
        if not outer:
            continue
        for name, (typ, oid) in LANDMARKS.items():
            if el["type"] == typ and el["id"] == oid:
                lm_rings[name] = max(outer, key=lambda r: abs(area2(r)))
        rec = {"id": el["id"], "type": el["type"], "tags": t, "outer": outer, "inner": inner}
        (parts if "building:part" in t and "building" not in t else outlines).append(rec)

    def bbox(r):
        return min(p[0] for p in r), max(p[0] for p in r), min(p[1] for p in r), max(p[1] for p in r)

    towers = {}
    for name, (typ, oid) in TOWERS.items():
        for o in outlines:
            if o["type"] == typ and o["id"] == oid:
                cx, cy = centroid(o["outer"][0])
                towers[name] = {"x": cx / S, "z": -cy / S, "top": building_heights(o["tags"], 0)[1] * VS}
    roche1 = [p for p in parts if num(p["tags"].get("height")) and num(p["tags"]["height"]) >= 170]
    if roche1:
        top = max(roche1, key=lambda p: num(p["tags"]["height"]))
        cx, cy = centroid(top["outer"][0])
        towers["roche1"] = {"x": cx / S, "z": -cy / S, "top": num(top["tags"]["height"]) * VS}

    # iconic buildings get hand-made facades in the game: export their real OSM outlines and keep the
    # 3D model's version of them out of the city mesh, together with the hand-modelled Münster and Spalentor
    def game_ring(r):
        r = simplify_ring(r, 0.25)
        return [[round(x / S, 2), round(-y / S, 2)] for x, y in r]

    iconic = {"roche1": [], "roche2": None, "messe": None, "rathaus": None}
    skip_rings = [lm_rings[k] for k in HAND_MODELLED if k in lm_rings]
    bau1 = next((o for o in outlines if o["type"] == "way" and o["id"] == 680531949), None)   # Roche Tower, Bau 1
    if bau1:
        skip_rings.append(bau1["outer"][0])
        for prt in parts:
            if inside(centroid(prt["outer"][0]), bau1["outer"][0]):
                b, top, *_ = building_heights(prt["tags"], abs(area2(prt["outer"][0])))
                iconic["roche1"].append({"ring": game_ring(prt["outer"][0]), "base": round(b, 1), "top": round(top, 1)})
    for key, typ, oid in (("roche2", "way", 317511699), ("messe", "way", 315916699), ("rathaus", "relation", RATHAUS_ID)):
        o = next((o for o in outlines if o["type"] == typ and o["id"] == oid), None)
        if o:
            outer = max(o["outer"], key=lambda r: abs(area2(r)))
            b, top, *_ = building_heights(o["tags"], abs(area2(outer)))
            iconic[key] = {"rings": [game_ring(outer)] + [game_ring(h) for h in o["inner"]], "top": round(top, 1)}
            skip_rings.append(outer)
    skip_bb = [bbox(r) for r in skip_rings]

    # OSM's building type (industrial, office, ...) at a point, for wall colours
    osm_grid = {}
    for o in outlines:
        for r in o["outer"]:
            x0, x1, y0, y1 = bbox(r)
            for gx in range(int(x0 // 50), int(x1 // 50) + 1):
                for gy in range(int(y0 // 50), int(y1 // 50) + 1):
                    osm_grid.setdefault((gx, gy), []).append((r, o["tags"]))

    def osm_kind(pt):
        for r, t in osm_grid.get((int(pt[0] // 50), int(pt[1] // 50)), []):
            if inside(pt, r):
                return t.get("building")
        return None

    def old_town(x, y):
        return math.hypot(x - MARKT[0], y - MARKT[1]) < 650 or math.hypot(x - 330, y - 230) < 300

    WALLS_OLD = ["#ecdcc0", "#f1e8d6", "#e6c999", "#dcb9a2", "#cdbfaa", "#e2e3d4", "#e9d1b6", "#d9ab8c", "#d1d8c6", "#f0e3c8"]
    WALLS = ["#e8e4dc", "#ddd9d1", "#d3d5d7", "#ebe8e1", "#d1ccc2", "#c4c9cd", "#e2ddd2"]
    WALLS_WORK = ["#c9ced3", "#b9bfc6", "#aeb6bf", "#d6d8da", "#c2c0bb"]
    WALL_KIND = {1: "#d6dadd", 2: "#c48e76", 3: "#e4e7ea", 6: "#d4e4dd", 7: "#bdb2a1", 8: "#c4b49c",
                 4: "#a9afb5", 5: "#a9afb5", 9: "#b8bec4"}

    def hexrgb(h):
        return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)

    # ---------- buildings: swissBUILDINGS3D roofs ----------
    blds = load_3d(raw, work, geo, lambda x, y: in_area(x, y, m=-15))
    terrain = {}            # ground-floor heights on a 50 m grid, for the few models without a ground outline
    for b in blds:
        if b["ground"] is not None:
            p = b["roof"][0][0]
            terrain.setdefault((int(p[0] // 50), int(p[1] // 50)), []).append(b["ground"])

    def ground_at(x, y):
        cx, cy = int(x // 50), int(y // 50)
        for r in range(6):
            vals = sorted(z for dx in range(-r, r + 1) for dy in range(-r, r + 1) if max(abs(dx), abs(dy)) == r
                          for z in terrain.get((cx + dx, cy + dy), []))
            if vals:
                return vals[len(vals) // 2]
        return None

    stats = Counter()
    recs = []
    for b in blds:
        roof = b["roof"]
        pts = [p for f in roof for p in f]
        c = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
        if any(bb[0] <= c[0] <= bb[1] and bb[2] <= c[1] <= bb[3] and inside(c, r) for r, bb in zip(skip_rings, skip_bb)):
            stats["landmark"] += 1
            continue
        kind = KINDS.get(b["kind"], 0)
        g = b["ground"]
        if g is None:
            g = ground_at(*c)
            if g is None:
                g = b["wall_min"] if b["wall_min"] is not None else min(p[2] for p in pts) - 3.0
        zbot = 0.0
        if kind in (4, 5, 9):           # canopies, open buildings and covered bridges stand on posts or span a street
            if b["wall_min"] is not None and b["wall_min"] - g > 1.5:
                zbot = b["wall_min"] - g
            elif kind == 4:
                zbot = max(0.0, min(p[2] for p in pts) - g - 0.6)
        verts, loops = sb3d.simplify_roof([[(p[0], p[1], p[2] - g) for p in f] for f in roof])
        if not loops:
            stats["empty"] += 1
            continue
        zmin = zbot + (0.4 if kind in (4, 5, 9) else 1.5)
        verts = [(x, y, max(z, zmin)) for x, y, z in verts]
        rings2d, area, flat = [], 0.0, 0.0
        for l in loops:
            r = [(verts[i][0], verts[i][1]) for i in l]
            a = abs(area2(r))
            nrm = sb3d.newell([verts[i] for i in l])
            area += a
            flat += a * (abs(nrm[2]) if nrm else 1.0)
            rings2d.append(r)
        if area < 6:
            stats["tiny"] += 1
            continue
        pitched = flat / area < 0.97
        rc = roof_colour(rings2d, 0.75 if area > 30 else 1.0)
        top = max(v[2] for v in verts)
        h = (int(c[0] * 10) * 73856093 ^ int(c[1] * 10) * 19349663) & 0xFFFFFFFF
        okind = osm_kind(c)
        if kind in WALL_KIND:
            wall = WALL_KIND[kind]
        elif top >= 45:
            wall = WALL_KIND[1]
        elif okind in ("industrial", "warehouse", "office", "commercial", "retail", "hospital", "university"):
            wall = WALLS_WORK[h % len(WALLS_WORK)]
        elif old_town(*c) and (pitched or top < 22):
            wall = WALLS_OLD[h % len(WALLS_OLD)]
        else:
            wall = WALLS[h % len(WALLS)]
        stats["pitched" if pitched else "flat"] += 1
        recs.append({"c": c, "kind": kind, "roof": tile_tint(rc) if pitched else rc, "wall": hexrgb(wall),
                     "zbot": zbot, "verts": verts, "loops": loops, "g": g})

    # walls: the game drops each roof's outer edges to the ground, except where another roof (this building's or
    # a neighbour's) carries on at about the same height just outside the edge, so that wall could never be seen.
    # Canopies, open buildings and covered bridges hide nothing: walls under them stay.
    CELL = 4.0
    pgrid, planes = {}, []
    for r in recs:
        if r["kind"] in (4, 5, 9):
            continue
        for l in r["loops"]:
            pts = [r["verts"][i] for i in l]
            nrm = sb3d.newell(pts)
            if not nrm or nrm[2] < 0.05:
                continue
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            cen = (sum(xs) / len(xs), sum(ys) / len(ys), r["g"] + sum(p[2] for p in pts) / len(pts))
            bb = (min(xs), max(xs), min(ys), max(ys))
            for gx in range(int(bb[0] // CELL), int(bb[1] // CELL) + 1):
                for gy in range(int(bb[2] // CELL), int(bb[3] // CELL) + 1):
                    pgrid.setdefault((gx, gy), []).append(len(planes))
            planes.append(([(p[0], p[1]) for p in pts], nrm, cen, bb))

    def roof_height(x, y):
        best = None
        for pi in pgrid.get((int(x // CELL), int(y // CELL)), ()):
            ring, nrm, (cx, cy, cz), bb = planes[pi]
            if bb[0] <= x <= bb[1] and bb[2] <= y <= bb[3] and inside((x, y), ring):
                h = cz - (nrm[0] * (x - cx) + nrm[1] * (y - cy)) / nrm[2]
                if best is None or h > best:
                    best = h
        return best

    n_edges = n_walls = 0
    for r in recs:
        v, g = r["verts"], r["g"]
        directed = {(l[k], l[(k + 1) % len(l)]) for l in r["loops"] for k in range(len(l))}
        flags = []
        for l in r["loops"]:
            for k in range(len(l)):
                a, b = l[k], l[(k + 1) % len(l)]
                if (b, a) in directed:
                    continue                    # a seam between two roof planes
                pa, pb = v[a], v[b]
                dx, dy = pb[0] - pa[0], pb[1] - pa[1]
                L = math.hypot(dx, dy)
                need = 0
                if L > 1e-6:
                    ox, oy = dy / L * 0.25, -dx / L * 0.25      # outside: right of a counter-clockwise edge
                    for t in (0.1, 0.5, 0.9):
                        h = roof_height(pa[0] + dx * t + ox, pa[1] + dy * t + oy)
                        if h is None or g + pa[2] + (pb[2] - pa[2]) * t - h > 0.8:
                            need = 1
                            break
                flags.append(need)
        r["walls"] = flags
        n_edges += len(flags)
        n_walls += sum(flags)

    # pack: varints, buildings in rows of 100 m cells so neighbours follow each other, then gzip
    recs.sort(key=lambda r: (int(r["c"][1] // 100), int(r["c"][0] // 100), r["c"][0]))
    buf = bytearray()
    fx = fy = 0
    n_verts = n_idx = n_loops = 0
    for r in recs:
        buf.append(r["kind"])
        buf.extend(r["roof"])
        buf.extend(r["wall"])
        varint(buf, int(round(r["zbot"] * 10)))
        V = [(int(round(x * 10)), int(round(y * 10)), int(round(z * 10))) for x, y, z in r["verts"]]
        varint(buf, len(V))
        lx, ly, lz = fx, fy, 0
        for x, y, z in V:
            varint(buf, zigzag(x - lx))
            varint(buf, zigzag(y - ly))
            varint(buf, zigzag(z - lz))
            lx, ly, lz = x, y, z
        fx, fy = V[0][0], V[0][1]
        varint(buf, len(r["loops"]))
        for l in r["loops"]:
            varint(buf, len(l))
            for i in l:
                varint(buf, i)
        flags = r["walls"]              # one bit per outer roof edge, in loop order: does it get a wall?
        for i in range(0, len(flags), 8):
            byte = 0
            for j, f in enumerate(flags[i:i + 8]):
                byte |= f << j
            buf.append(byte)
        n_verts += len(V)
        n_loops += len(r["loops"])
        n_idx += sum(len(l) for l in r["loops"])
    packed = gzip.compress(bytes(buf), 9, mtime=0)

    # ---------- landmarks, places, trees, bridges, ferries ----------
    landmarks = {}
    for k, r in lm_rings.items():
        b = obb(r)
        landmarks[k] = {"x": b["cx"] / S, "z": -b["cy"] / S, "len": b["len"] / S, "wid": b["wid"] / S,
                        "ang": b["ang"], "colour": roof_colour([r])}
    for k, (typ, oid) in LANDMARK_TOWERS.items():
        for p in parts + outlines:
            if p["type"] == typ and p["id"] == oid and k in landmarks:
                cx, cy = centroid(p["outer"][0])
                landmarks[k]["tower"] = [round(cx / S, 2), round(-cy / S, 2)]

    places = {}
    wanted = {"Barfüsserplatz": "barfi", "Claraplatz": "clara", "Marktplatz": "markt", "Kaserne": "kaserne",
              "Messeplatz": "messeplatz", "Petersplatz": "peters", "Theaterplatz": "theater"}
    acc = {}
    trees, tree_info = array("h"), array("B")
    tree_cells = set()

    def add_tree(x, y, shape, size):
        trees.append(int(round(x * 10)))
        trees.append(int(round(y * 10)))
        tree_info.append(tree_byte(shape, size))
        tree_cells.add((int(x // 3), int(y // 3)))

    # Basel's tree register first (species and age), then OpenStreetMap's trees where the register has none
    reg = os.path.join(raw, "trees_bs.json")
    for t in json.load(open(reg)) if os.path.exists(reg) else []:
        g = t.get("geo_point_2d") or {}
        if "lat" not in g:
            continue
        x, y = geo.local(g["lat"], g["lon"])
        if not in_area(x, y, m=-10):
            continue
        latin = t.get("baumart_lateinisch") or ""
        age = t.get("ba_baumalter")
        shape = 1 if latin.split(" ")[0] in CONIFERS else 2 if any(w in latin for w in COLUMNAR) else 0
        add_tree(x, y, shape, 0.95 if age is None else min(1.6, max(0.55, 0.55 + age * 0.012)))
    n_register = len(tree_info)
    bridges, ferries = [], []
    for el in xjs:
        t = el.get("tags", {})
        if el["type"] == "node":
            x, y = geo.local(el["lat"], el["lon"])
            if t.get("natural") == "tree":
                if in_area(x, y, m=-10) and not any((int(x // 3) + dx, int(y // 3) + dy) in tree_cells
                                                    for dx in (-1, 0, 1) for dy in (-1, 0, 1)):
                    conifer = t.get("leaf_type") == "needleleaved" or (t.get("genus") or "") in CONIFERS
                    add_tree(x, y, 1 if conifer else 0, 0.9)
                continue
            key = wanted.get(t.get("name"))
            if key:
                acc.setdefault(key, []).append((x, y))
            if t.get("amenity") == "ferry_terminal" and "Tinguely" in (t.get("name") or ""):
                acc.setdefault("tinguely", []).append((x, y))
        elif el["type"] == "way":
            g = [geo.local(p["lat"], p["lon"]) for p in el.get("geometry") or [] if p]
            if not g:
                continue
            if t.get("man_made") == "bridge" and len(g) >= 4 and g[0] == g[-1]:
                c = centroid(g)
                if in_area(*c):
                    r = simplify_ring(g, 0.5)
                    if area2(r) < 0:
                        r = r[::-1]
                    bridges.append({"name": t.get("name") or "", "ring": [[round(x / S, 2), round(-y / S, 2)] for x, y in r],
                                    "structure": t.get("bridge:structure") or "beam", "id": el["id"], "_ring": g[:-1]})
            elif t.get("route") == "ferry" and len(g) >= 2:
                a, b = g[0], g[-1]
                if in_area(*a) and in_area(*b) and math.hypot(a[0] - b[0], a[1] - b[1]) < 400:
                    ferries.append({"name": t.get("name") or "", "a": [a[0] / S, -a[1] / S], "b": [b[0] / S, -b[1] / S]})
            elif t.get("leisure") == "park" and t.get("name") and g[0] == g[-1]:
                c = centroid(g)
                key = {"Schützenmattpark": "schuetzenmatt", "Solitude": "solitude"}.get(t["name"])
                if "Solitude" in t["name"]:
                    key = "solitude"
                if key and in_area(*c):
                    acc.setdefault(key, []).append(c)
        elif el["type"] == "relation" and t.get("leisure") == "park" and "Schützenmatt" in (t.get("name") or ""):
            outer, _ = element_rings(el, geo)
            if outer:
                acc.setdefault("schuetzenmatt", []).append(centroid(max(outer, key=lambda r: abs(area2(r)))))
    for k, pts in acc.items():
        places[k] = [round(sum(p[0] for p in pts) / len(pts) / S, 2), round(-sum(p[1] for p in pts) / len(pts) / S, 2)]

    # bridges: their axis and where along it the Rhine is, read from the aerial photo beside the deck (the deck
    # itself hides the water), so the game can stand piers in the river and ramp the decks down on land
    def px_median(x, y):
        cx, cy = px(x, y)
        got = sorted((pixel(int(cx) + dx, int(cy) + dy) for dx in (-2, 0, 2) for dy in (-2, 0, 2)), key=sum)
        return got[len(got) // 2]

    def axis(o, t, side, off):
        u, v = (math.cos(o["ang"]), math.sin(o["ang"])), (-math.sin(o["ang"]), math.cos(o["ang"]))
        a = (t - 0.5) * o["len"]
        return o["cx"] + u[0] * a + v[0] * side * off, o["cy"] + u[1] * a + v[1] * side * off

    ref = None
    for br in bridges:
        if br["id"] == 653605685:      # the Mittlere Brücke's middle is mid-river: its sides give the Rhine's colour
            o = obb(br["_ring"])
            samples = [px_median(*axis(o, t, side, o["wid"] / 2 + 25)) for t in (0.35, 0.45, 0.55, 0.65) for side in (-1, 1)]
            ref = [sorted(c[k] for c in samples)[len(samples) // 2] for k in range(3)]
    for br in bridges:
        o = obb(br["_ring"])
        br["axis"] = [round(o["cx"] / S, 2), round(-o["cy"] / S, 2), round(o["len"] / S, 2), round(o["wid"] / S, 2), round(o["ang"], 4)]
        br["water"] = None
        if ref and o["len"] > 60:
            n = 60
            wet = []
            for k in range(n + 1):
                t = k / n
                wet.append(any(math.dist(px_median(*axis(o, t, side, o["wid"] / 2 + 12)), ref) < 42 for side in (-1, 1)))
            best, cur = None, None
            for k, w in enumerate(wet + [False]):
                if w:
                    cur = (cur[0], k) if cur else (k, k)
                elif cur:
                    if best is None or cur[1] - cur[0] > best[1] - best[0]:
                        best = cur
                    cur = None
            if best and (best[1] - best[0]) / n * o["len"] > 50:
                br["water"] = [round(best[0] / n, 3), round(best[1] / n, 3)]
        br["_obb"] = o
    for br in bridges:      # a bridge squeezed between two others sees only decks: borrow a parallel neighbour's river
        o = br["_obb"]
        if br["water"] is None and o["len"] > 60:
            for other in bridges:
                q = other["_obb"]
                turn = abs(math.atan2(math.sin(o["ang"] - q["ang"]), math.cos(o["ang"] - q["ang"])))
                if other["water"] and math.dist((o["cx"], o["cy"]), (q["cx"], q["cy"])) < 60 and min(turn, math.pi - turn) < 0.2:
                    ends = [axis(q, t, 0, 0) for t in other["water"]]
                    ts = sorted(0.5 + ((e[0] - o["cx"]) * math.cos(o["ang"]) + (e[1] - o["cy"]) * math.sin(o["ang"])) / o["len"] for e in ends)
                    br["water"] = [round(max(0.0, ts[0]), 3), round(min(1.0, ts[1]), 3)]
                    break
    for br in bridges:
        del br["_ring"], br["id"], br["_obb"]
    for k, (lat, lon) in {"sbb": (47.54762, 7.58964), "tinguely": (47.55917, 7.61234)}.items():
        x, y = geo.local(lat, lon)     # station building and museum, from their OSM nodes
        places[k] = [round(x / S, 2), round(-y / S, 2)]
    if "schuetzenmatt" not in places:
        x, y = geo.local(47.55257, 7.57448)
        places["schuetzenmatt"] = [round(x / S, 2), round(-y / S, 2)]
    if "solitude" not in places:
        x, y = geo.local(47.5583, 7.6092)
        places["solitude"] = [round(x / S, 2), round(-y / S, 2)]

    def b64(arr):
        return base64.b64encode(arr.tobytes()).decode()

    data = {
        "S": S, "VS": VS,
        "ground": {"x0": W / S, "x1": E / S, "z0": -N / S, "z1": -Sth / S, "avg": [round(c) for c in avg],
                   "src": "data:image/avif;base64," + ground_b64, "fallback": "data:image/jpeg;base64," + small_b64},
        "lod2": {"count": len(recs), "gz": base64.b64encode(packed).decode()},
        "trees": b64(trees), "treeInfo": b64(tree_info),
        "towers": towers, "landmarks": landmarks, "iconic": iconic, "places": places, "bridges": bridges, "ferries": ferries,
    }
    js = "const BASEL=" + json.dumps(data, separators=(",", ":"), ensure_ascii=False) + ";\n"
    with open(out_js, "w") as f:
        f.write(js)
    print(json.dumps({"buildings": len(recs), "roof_vertices": n_verts, "roof_polygons": n_loops, "loop_indices": n_idx,
                      "walls": [n_walls, n_edges],
                      "packed_kB": [len(buf) // 1000, len(packed) // 1000],
                      "trees": {"register": n_register, "osm": len(tree_info) - n_register}, "stats": stats,
                      "bridges": [[b["name"], b["structure"], b["axis"][2] * S, b["water"]] for b in bridges],
                      "ferries": [x["name"] for x in ferries],
                      "towers": towers, "landmarks": {k: {kk: (round(v, 2) if isinstance(v, float) else v) for kk, v in d.items()} for k, d in landmarks.items()},
                      "places": places, "ground_px": [GW, GH], "ground_kB": [len(ground_b64) * 3 // 4 // 1000, len(small_b64) * 3 // 4 // 1000],
                      "js_kB": len(js) // 1000}, ensure_ascii=False, indent=1))


def read_bmp(path):
    b = open(path, "rb").read()
    off = struct.unpack_from("<I", b, 10)[0]
    w, h = struct.unpack_from("<ii", b, 18)
    bpp = struct.unpack_from("<H", b, 28)[0]
    if bpp not in (24, 32):
        raise SystemExit(f"unsupported BMP depth {bpp} in {path}")
    n = bpp // 8
    stride = (w * n + 3) & ~3
    top_down = h < 0
    h = abs(h)
    rgb = bytearray(w * h * 3)
    for y in range(h):
        sy = y if top_down else h - 1 - y
        row = b[off + sy * stride: off + sy * stride + w * n]
        o = y * w * 3
        rgb[o:o + w * 3:3] = row[2::n]
        rgb[o + 1:o + w * 3:3] = row[1::n]
        rgb[o + 2:o + w * 3:3] = row[0::n]
    return w, h, rgb


def write_bmp(path, w, h, rgb):
    stride = (w * 3 + 3) & ~3
    pad = b"\0" * (stride - w * 3)
    with open(path, "wb") as f:
        f.write(b"BM" + struct.pack("<IHHI", 54 + stride * h, 0, 0, 54))
        f.write(struct.pack("<IiiHHIIiiII", 40, w, h, 1, 24, 0, stride * h, 2835, 2835, 0, 0))
        for y in range(h - 1, -1, -1):
            row = rgb[y * w * 3:(y + 1) * w * 3]
            bgr = bytearray(len(row))
            bgr[0::3], bgr[1::3], bgr[2::3] = row[2::3], row[1::3], row[0::3]
            f.write(bgr + pad)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    main(*sys.argv[1:])
