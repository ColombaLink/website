"""Turn raw swisstopo + OpenStreetMap data into the Basel city data embedded in Secret Pigeon.

Usage: python3 -I secret-pigeon/build/build_city.py <raw-dir> <work-dir> <out-js>
  raw-dir   folder written by fetch_data.py (treated as untrusted input)
  work-dir  scratch folder for converted images and a cache of the parsed 3D buildings
  out-js    JS file that defines `const BASEL = {...}`; assemble.py injects it into the game

Buildings are swisstopo's swissBUILDINGS3D 3.0: real roof shapes and heights. OpenStreetMap supplies the landmarks,
the buildings that get facades in the game, trees, bridges, ferries, place names, the railway and tram tracks and the
tram and bus routes; Switzerland's timetable (GTFS) the trams' and buses' trips; the Copernicus DEM the hills and
mountains on the horizon (horizon.py).
Coordinates: swiss LV95 metres relative to the Mittlere Brücke. Game x = east / S, game z = -north / S,
heights are multiplied by VS (a mild vertical exaggeration so landmarks read from the air).
"""
import base64
import csv
import datetime
import gzip
import heapq
import json
import math
import os
import re
import struct
import subprocess
import sys
import zipfile
from array import array
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import horizon  # noqa: E402
import sb3d  # noqa: E402

S = 5.0             # metres per game unit on the ground
EH = 1.4            # vertical exaggeration
VS = EH / S         # game units per metre of height
AREA = (-1450.0, 1950.0, -1650.0, 1450.0)   # west, east, south, north in metres around the origin
ORIGIN_LATLON = (47.56015, 7.58972)          # Mittlere Brücke, replaced by the bridge outline's centre if found
RHINE = "Q584"      # the Rhine's Wikidata item, which names its waterway relation in OpenStreetMap
COURSE_R = 22000.0  # metres from the origin to which the Rhine's way beyond the map is kept (the game's land ends 15 km out)
LEVEL = 3.0         # metres per storey
MARKT = (-152.0, -228.0)                     # rough Marktplatz offset, refined from data

# OSM ids of landmarks: the Münster and Spalentor are hand-modelled (their outlines are dropped from the generic
# city); the Rathaus keeps its real outline but gets its red walls and a tower in the game
LANDMARKS = {"muenster": ("way", 137472252), "rathaus": ("relation", 7634307), "spalentor": ("relation", 3531078),
             "elisabethen": ("way", 105047858), "stalbantor": ("way", 100700024), "stjohannstor": ("way", 144223843),
             "kreuzgang": ("relation", 7744965), "barfuesser": ("way", 137472263), "martins": ("way", 137472347),
             "peters": ("way", 270939309), "leonhard": ("way", 137472332), "theodor": ("way", 317500703),
             "clara": ("way", 315883214), "prediger": ("way", 262233179), "stalbankirche": ("way", 147744346),
             "kartaeuser": ("way", 317492467)}
HAND_MODELLED = set(LANDMARKS) - {"rathaus"}
LANDMARK_TOWERS = {"elisabethen": ("way", 407463501)}      # the Elisabethenkirche's tower, as an OSM building part
# buildings that keep swisstopo's shape but get painted facades in the game; the list order is the style number
FACADES = [("rathaus", "relation", 7634307), ("messe1", "way", 315916587), ("kunstmuseum", "relation", 5279724),
           ("kunstneubau", "way", 295287955), ("sbb", "way", 100712891), ("badbf", "way", 100789428),
           ("tinguely", "way", 317500933), ("messe1", "relation", 18445990), ("moh", "way", 562039631),
           ("uni", "way", 270630550),
           ("troisrois", "way", 148034746), ("troisroisbank", "way", 148034745),   # Les Trois Rois, and its corner building
           ("highrise", None, None), ("sacral", None, None),     # every other high-rise and church
           ("shops1", None, None), ("shops2", None, None), ("shops3", None, None), ("shops4", None, None)]
UNI_ENTRANCE = (47.5578479, 7.5841403)   # the Kollegienhaus's main door, by Carl Gutknecht's relief (OSM)
SHOP_STYLES = ["shops1", "shops2", "shops3", "shops4"]   # houses with shop fronts along the streets below
# the shopping streets from Barfüsserplatz to Marktplatz (OSM names, half their width in metres) and the two squares
SHOP_STREETS = {"Gerbergasse": 4.5, "Falknerstrasse": 6.0, "Freie Strasse": 5.0, "Streitgasse": 4.0,
                "Barfüssergasse": 4.0, "Eisengasse": 5.0, "Marktgasse": 4.5}
SQUARES = {"Marktplatz": ("way", 193681195), "Barfüsserplatz": ("way", 193681190)}
FACADE_STYLES = sorted({f[0] for f in FACADES}, key=[f[0] for f in FACADES].index)
# landmarks that stood in 1615 too: build_1615.py leaves the plan's own shapes out inside their outlines
IN_1615 = ["muenster", "kreuzgang", "spalentor", "stalbantor", "stjohannstor", "rathaus", "barfuesser", "martins",
           "peters", "leonhard", "theodor", "clara", "prediger", "stalbankirche", "kartaeuser"]
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


def river_mask(cand, w, h, seeds, extra=()):
    """The river on a w x h grid: candidate cells connected to a seed, plus `extra` cells, with every enclosed hole
    (moored boats, bridge shadows, noise) filled in. Returns a bytearray of 0/1."""
    def spread(mask, ok, start):
        stack = [i for i in start if not mask[i] and ok(i)]
        for i in stack:
            mask[i] = 1
        while stack:
            i = stack.pop()
            x = i % w
            for j in (i - 1 if x > 0 else -1, i + 1 if x < w - 1 else -1, i - w, i + w):
                if 0 <= j < w * h and not mask[j] and ok(j):
                    mask[j] = 1
                    stack.append(j)
    river = bytearray(w * h)
    spread(river, lambda i: cand[i], [y * w + x for x, y in seeds if 0 <= x < w and 0 <= y < h])
    for i in extra:
        river[i] = 1
    river = close_mask(river, w, h, 4)       # seals bridge shadows and smooths the banks
    outside = bytearray(w * h)
    border = list(range(w)) + list(range((h - 1) * w, h * w)) + list(range(0, w * h, w)) + list(range(w - 1, w * h, w))
    spread(outside, lambda i: not river[i], border)
    holes = outside.translate(bytes([1, 0] + [0] * 254))
    n = w * h
    return bytearray((int.from_bytes(river, "big") | int.from_bytes(holes, "big")).to_bytes(n, "big"))


def close_mask(mask, w, h, r):
    """Morphological closing (dilate, then erode, by r cells) of a 0/1 byte grid, a row at a time as big integers.
    It can't see beyond the grid, so the cells within r of its edges are left as they are."""
    full = (1 << (8 * w)) - 1
    rows = [int.from_bytes(mask[y * w:(y + 1) * w], "big") for y in range(h)]

    def sweep(rows, grow):
        out = []
        for v in rows:                   # along each row
            acc = v
            for k in range(1, r + 1):
                a, b = (v << 8 * k) & full, v >> 8 * k
                acc = (acc | a | b) if grow else (acc & a & b)
            out.append(acc)
        res = []
        for y in range(h):               # across the rows
            acc = out[y]
            for k in range(1, r + 1):
                a, b = out[max(0, y - k)], out[min(h - 1, y + k)]
                acc = (acc | a | b) if grow else (acc & a & b)
            res.append(acc)
        return res
    rim = int.from_bytes(b"\x01" * r + b"\x00" * (w - 2 * r) + b"\x01" * r, "big")
    out = [v if r <= y < h - r else rows[y] for y, v in enumerate(sweep(sweep(rows, True), False))]
    out = [(v & ~rim) | (rows[y] & rim) for y, v in enumerate(out)]
    return bytearray(b"".join(v.to_bytes(w, "big") for v in out))


GROSSBASEL = (47.5550, 7.5880)   # the Barfüsserplatz: which of the Rhine's shores is Grossbasel's


def river_shores(els, geo):
    """The Rhine's two shores in OpenStreetMap (its outline, natural=water and water=river, as far as the data goes):
    {"g": Grossbasel's, "k": Kleinbasel's}, each a flat list x, z, x, z, ... in game units from upstream (the east) to
    downstream. The game sinks the river between them and builds the banks along them; {} without them."""
    runs = []
    for el in els:
        t = el.get("tags", {})
        if t.get("natural") != "water" or t.get("water") != "river":
            continue
        ways = [el.get("geometry") or []] if el["type"] == "way" else \
            [m.get("geometry") or [] for m in el.get("members", []) if m.get("role") == "outer"]
        for g in ways:
            cur = []
            for p in g + [None]:         # the outline leaves the area of the data: the shore stops there
                if p:
                    cur.append(geo.local(p["lat"], p["lon"]))
                elif len(cur) > 1:
                    runs.append(cur)
                    cur = []
                else:
                    cur = []
    joined = True
    while joined:                        # a multipolygon's outline comes in several ways, either way round
        joined = False
        for i, a in enumerate(runs):
            for j, b in enumerate(runs):
                if i == j:
                    continue
                if math.dist(a[-1], b[0]) < 0.5:
                    runs[i] = a + b[1:]
                elif math.dist(a[-1], b[-1]) < 0.5:
                    runs[i] = a + b[::-1][1:]
                elif math.dist(a[0], b[-1]) < 0.5:
                    runs[i] = b[:-1] + a
                elif math.dist(a[0], b[0]) < 0.5:
                    runs[i] = b[::-1][:-1] + a
                else:
                    continue
                del runs[j]
                joined = True
                break
            if joined:
                break
    runs = sorted(runs, key=lambda r: -sum(math.dist(p, q) for p, q in zip(r, r[1:])))[:2]
    if len(runs) < 2 or min(sum(math.dist(p, q) for p, q in zip(r, r[1:])) for r in runs) < 1000:
        return {}
    return shores_out(runs, geo.local(*GROSSBASEL))


def shores_out(runs, ref):
    """Two shores (local metres) as the game wants them: game units, from the east, Grossbasel's first."""
    out = []
    for pts in runs:
        line = [(round(x / S, 2), round(-y / S, 2)) for x, y in pts]
        if line[0][0] < line[-1][0]:
            line.reverse()
        out.append(line)
    out.sort(key=lambda line: min((x - ref[0] / S) ** 2 + (z + ref[1] / S) ** 2 for x, z in line))
    return {"g": [c for p in out[0] for c in p], "k": [c for p in out[1] for c in p]}


def rhine_course(raw, geo):
    """The Rhine's way beyond the map, for the game to run the river on round its real bends: OpenStreetMap's
    centreline of its main stream (upstream through the Birsfelden power station's weir towards Augst and Rheinfelden,
    downstream north past the Märkt weir down the Old Rhine, not the Grand Canal d'Alsace) within COURSE_R of the
    origin, as a flat list x, z, x, z, ... in game units from upstream to downstream. The farther from the map, the more
    its bends are eased (they are in the haze there), so that the shores the game lays beside it never fold over.
    [] without <raw>/osm_rhine.json."""
    src = os.path.join(raw, "osm_rhine.json")
    if not os.path.exists(src):
        print("no <raw-dir>/osm_rhine.json: run fetch_data.py <raw-dir> rhine for the Rhine's way beyond the map", flush=True)
        return []
    chains = [[geo.local(p["lat"], p["lon"]) for p in el.get("geometry") or [] if p]
              for el in json.load(open(src))["elements"] if el["type"] == "way"]
    chains = [c for c in chains if len(c) > 1]
    joined = True
    while joined:                        # the ways run with the river: each goes on where another one ends
        joined = False
        for i, a in enumerate(chains):
            j = next((j for j, b in enumerate(chains) if j != i and math.dist(a[-1], b[0]) < 1), None)
            if j is not None:
                chains[i] = a + chains[j][1:]
                del chains[j]
                joined = True
                break
    if not chains:
        return []
    line = min(chains, key=lambda c: min(math.hypot(*p) for p in c))     # the one through Basel
    mid = min(range(len(line)), key=lambda i: math.hypot(*line[i]))
    i0, i1 = mid, mid
    while i0 > 0 and math.hypot(*line[i0]) < COURSE_R:
        i0 -= 1
    while i1 < len(line) - 1 and math.hypot(*line[i1]) < COURSE_R:
        i1 += 1
    line = line[i0:i1 + 1]
    pts = [line[0]]                      # every 10 m along it
    for a, b in zip(line, line[1:]):
        n = max(1, round(math.dist(a, b) / 10))
        pts += [(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for k in range(1, n + 1)]
    W, E, Sth, N = AREA
    smooth = []
    for i, (x, y) in enumerate(pts):     # eased with a Gaussian that widens from 300 m off the map to 250 m 2 km out
        sigma = min(250.0, max(0.0, max(W - x, x - E, Sth - y, y - N) - 300.0) * 0.15) / 10
        if sigma < 0.5:
            smooth.append((x, y))
            continue
        r = int(3 * sigma)
        ks = range(max(0, i - r), min(len(pts), i + r + 1))
        ws = [math.exp(-((k - i) / sigma) ** 2 / 2) for k in ks]
        smooth.append(tuple(sum(w * pts[k][c] for w, k in zip(ws, ks)) / sum(ws) for c in (0, 1)))
    return [c for x, y in rdp_long(smooth, 1.0) for c in (round(x / S, 2), round(-y / S, 2))]


def river_banks(mask, w, h, west, north, ref, cell=2.0):
    """The two shores of the river in a mask (`cell`-metre cells from `west`, `north`; 1 = water), for a river that
    OpenStreetMap doesn't have (Basel in 1615): its edge, smoothed over the landing stages, as river_shores() gives
    them, each from the map's edge to the map's edge. `ref` (local metres) lies in Grossbasel. {} if the river doesn't
    cross the map."""
    flip = bytes([1, 0] + [0] * 254)
    m = close_mask(mask, w, h, 6)                                  # landing stages, boats: water
    m = close_mask(m.translate(flip), w, h, 2).translate(flip)     # slivers of water-coloured land: land
    water = lambda i, j: 0 <= i < w and 0 <= j < h and m[j * w + i] == 1
    # every edge between water and land (or the map's edge) as corner -> next corner, the water on its right on screen
    nxt = {}
    for j in range(h):
        i = m.find(1, j * w, (j + 1) * w)
        while i >= 0:
            i -= j * w
            for ok, a, b in ((water(i, j - 1), (i, j), (i + 1, j)), (water(i + 1, j), (i + 1, j), (i + 1, j + 1)),
                             (water(i, j + 1), (i + 1, j + 1), (i, j + 1)), (water(i - 1, j), (i, j + 1), (i, j))):
                if not ok:
                    nxt.setdefault(a, []).append(b)
            i = m.find(1, j * w + i + 1, (j + 1) * w)
    loops = []
    while nxt:
        a = next(iter(nxt))
        loop, d = [a], None
        while a in nxt:
            ends = nxt[a]       # where two water cells meet at a corner, keep to the same one: turn right
            b = max(ends, key=lambda e: d[0] * (e[1] - a[1]) - d[1] * (e[0] - a[0])) if d and len(ends) > 1 else ends[0]
            ends.remove(b)
            if not ends:
                del nxt[a]
            d, a = (b[0] - a[0], b[1] - a[1]), b
            if a == loop[0]:
                break
            loop.append(a)
        loops.append(loop)
    if not loops:
        return {}
    loop = max(loops, key=len)
    edge = [min(i, j, w - i, h - j) == 0 for i, j in loop]     # on the map's edge
    if all(edge) or not any(edge):
        return {}
    k0 = edge.index(True)               # start on the map's edge, so each shore is one unbroken run
    loop, edge = loop[k0:] + loop[:k0 + 1], edge[k0:] + edge[:k0 + 1]
    runs, cur = [], []
    for p, e in zip(loop, edge):
        q = (west + p[0] * cell, north - p[1] * cell)
        if e:                           # a shore runs from where it leaves the map's edge to where it meets it again
            if len(cur) > 21:
                runs.append(cur + [q])
            cur = [q]
        else:
            cur.append(q)
    if len(runs) != 2:
        return {}
    for k, pts in enumerate(runs):
        for _ in range(6):              # the cells' staircase smoothed into a shore line
            pts = pts[:2] + [tuple(sum(q[c] for q in pts[i - 2:i + 3]) / 5 for c in (0, 1)) for i in range(2, len(pts) - 2)] + pts[-2:]
        runs[k] = rdp_long(pts, 0.5)
    return shores_out(runs, ref)


def waterside_access(raw, geo, banks):
    """The canton's stairs and ladders down to the Rhine (data.bs.ch dataset 100285, Geodaten Kanton Basel-Stadt) within
    40 m of a shore in the map: [x, z, kind, ...] in game units, kind 0 a stair, 1 a stair that is also a way out for
    swimmers, 2 a ladder; [] without the data."""
    src = os.path.join(raw, "access_bs.json")
    if not os.path.exists(src) or not banks:
        return []
    shores = [list(zip(b[0::2], b[1::2])) for b in banks.values()]

    def dist(x, z):
        best = 1e9
        for line in shores:
            for (ax, az), (bx, bz) in zip(line, line[1:]):
                dx, dz = bx - ax, bz - az
                t = max(0.0, min(1.0, ((x - ax) * dx + (z - az) * dz) / (dx * dx + dz * dz or 1e-9)))
                best = min(best, math.hypot(ax + t * dx - x, az + t * dz - z))
        return best
    out = []
    for r in json.load(open(src)):
        p = r.get("geo_point_2d") or {}
        if p.get("lat") is None:
            continue
        x, y = geo.local(p["lat"], p["lon"])
        if dist(x / S, -y / S) * S > 40:
            continue
        kind = 2 if r.get("typ_bez") == "Ausstiegsleiter" else 1 if str(r.get("ausstieg_moeglich")).lower() == "true" else 0
        out += [round(x / S, 2), round(-y / S, 2), kind]
    return out


def rail_tracks(raw, geo):
    """Tracks for the game's trains, out of OpenStreetMap's: two from Basel SBB over the Rhine to the Badischer Bahnhof
    and on north towards Germany (DB line 4000), the second kept off the first one's rails so the trains pass side by
    side, and one from the French platforms towards France. Cheapest paths along running lines that never turn back on
    themselves, in game units; [] without the data."""
    src = os.path.join(raw, "osm_rail.json")
    if not os.path.exists(src):
        return []
    els = json.load(open(src))["elements"]
    stations = {e["tags"].get("name"): geo.local(e["lat"], e["lon"]) for e in els if e["type"] == "node" and e.get("tags")}
    sbb, bad = stations.get("Basel SBB"), stations.get("Basel Badischer Bahnhof")
    if not sbb or not bad:
        return []
    W, E, Sth, N = AREA
    pos, adj, wtags = {}, {}, {}

    def cost(t):     # running lines first; station, yard and siding tracks only where nothing else goes
        if t.get("usage") in ("main", "branch") and not t.get("service"):
            return 1.0
        return {"crossover": 1.3, "siding": 3.0, "yard": 6.0, "spur": 6.0}.get(t.get("service"), 2.0)

    for w in els:
        if w["type"] != "way":
            continue
        t = wtags[w["id"]] = w.get("tags", {})
        for nid, p in zip(w["nodes"], w.get("geometry") or []):
            if p:
                pos[nid] = geo.local(p["lat"], p["lon"])
        for a, b in zip(w["nodes"], w["nodes"][1:]):
            if a in pos and b in pos:
                c = math.dist(pos[a], pos[b]) * cost(t)
                adj.setdefault(a, []).append((b, c, w["id"]))
                adj.setdefault(b, []).append((a, c, w["id"]))

    def heading(a, b):
        return math.atan2(pos[b][1] - pos[a][1], pos[b][0] - pos[a][0])

    def bend(h1, h2):
        return abs(math.atan2(math.sin(h1 - h2), math.cos(h1 - h2)))

    def search(start, goal, avoid, back=None):
        """Cheapest path to a node where goal() holds, turning at most 34 degrees at each node (no reversing)."""
        best, prev, pq = {(start, None): 0.0}, {}, [(0.0, start, None)]
        while pq:
            d, u, pu = heapq.heappop(pq)
            if d > best[(u, pu)]:
                continue
            if goal(u):
                out, st = [u], (u, pu)
                while st in prev:
                    st = prev[st]
                    out.append(st[0])
                return out[::-1], d
            for v, c, _ in adj.get(u, ()):
                h = heading(u, v)
                if bend(heading(pu, u) if pu is not None else h if back is None else back, h) > 0.6:
                    continue
                c *= 30 if (min(u, v), max(u, v)) in avoid else 1
                if d + c < best.get((v, u), 1e18):
                    best[(v, u)], prev[(v, u)] = d + c, (u, pu)
                    heapq.heappush(pq, (d + c, v, u))
        return None

    def way_of(a, b):
        return next(w for v, _, w in adj[a] if v == b)

    def along(pts, q):   # distance along a polyline of its point nearest to q
        best, run = (1e18, 0.0), 0.0
        for a, b in zip(pts, pts[1:]):
            L = math.dist(a, b) or 1e-9
            t = max(0.0, min(1.0, ((q[0] - a[0]) * (b[0] - a[0]) + (q[1] - a[1]) * (b[1] - a[1])) / (L * L)))
            d = math.dist(q, (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
            best = min(best, (d, run + L * t))
            run += L
        return best[1]

    def onward(nodes, metres):   # carry on along the track past the last node, as straight as the rails go
        got = 0.0
        while got < metres:
            u, pu = nodes[-1], nodes[-2]
            nxt = sorted((bend(heading(pu, u), heading(u, v)), v) for v, _, _ in adj.get(u, ()) if v != pu)
            if not nxt or nxt[0][0] > 0.35:
                break
            got += math.dist(pos[u], pos[nxt[0][1]])
            nodes.append(nxt[0][1])
        return got

    inside = lambda q: W <= q[0] <= E and Sth <= q[1] <= N

    def export(nodes, to):   # a track in game units: where it stops, runs in tunnels and finally leaves the map
        nonlocal avoid, taken, used
        pairs = list(zip(nodes, nodes[1:]))
        avoid |= {(min(a, b), max(a, b)) for a, b in pairs}
        taken |= {way_of(a, b) for a, b in pairs}
        used |= set(nodes)
        pts = [pos[n] for n in nodes]
        (x0, y0), (x1, y1) = pts[-2], pts[-1]
        k = 280 / (math.dist(pts[-2], pts[-1]) or 1)     # and on beyond the map edge, out of sight
        pts.append((x1 + (x1 - x0) * k, y1 + (y1 - y0) * k))
        tunnels, run, edge = [], 0.0, None
        for i, (a, b) in enumerate(zip(pts, pts[1:])):
            L = math.dist(a, b)
            if i < len(pairs) and wtags[way_of(*pairs[i])].get("tunnel") == "yes":
                if tunnels and abs(tunnels[-1][1] - run) < 0.01:
                    tunnels[-1][1] = run + L
                else:
                    tunnels.append([run, run + L])
            if inside(a) and not inside(b):
                lo, hi = 0.0, 1.0           # where this segment crosses the edge of the map; the last crossing counts
                for _ in range(30):
                    m = (lo + hi) / 2
                    lo, hi = (m, hi) if inside((a[0] + (b[0] - a[0]) * m, a[1] + (b[1] - a[1]) * m)) else (lo, m)
                edge = run + L * lo
            run += L
        plat = next((wtags[w].get("railway:track_ref") for _, _, w in adj[nodes[0]] if wtags[w].get("railway:track_ref")), None)
        out = {"to": to, "p": [round(v / S * (1 if i % 2 == 0 else -1), 2) for x, y in rdp(pts, 0.25) for i, v in enumerate((x, y))],
               "sbb": 0.0 if to == "fr" else round(along(pts, sbb) / S, 2), "edge": round((edge or run) / S, 2),
               "tunnels": [[round(a / S, 2), round(b / S, 2)] for a, b in tunnels], "platform": plat, "len": round(run / S, 1)}
        if to == "de":
            out["bad"] = round(along(pts, bad) / S, 2)
        return out

    running = {n for n, es in adj.items() if any(cost(wtags[w]) == 1.0 for _, _, w in es)}
    tracks, avoid, taken, used, short = [], set(), set(), set(), set()
    for _ in range(2):
        def at_sbb(n):   # a platform track at Basel SBB that the other train does not use
            es = adj.get(n, ())
            return (math.dist(pos[n], sbb) < 260 and any(wtags[w].get("railway:track_ref") for _, _, w in es)
                    and not any(w in taken or w in short for _, _, w in es))
        near = sorted((n for n in running if math.dist(pos[n], bad) < 90 and n not in used), key=lambda n: math.dist(pos[n], bad))
        for _attempt in range(8):
            best = None
            for b0 in near[:12]:     # through the Badischer Bahnhof: north to the map edge, and south to Basel SBB
                north = search(b0, lambda n: pos[n][1] > N - 5, avoid)
                south = north and search(b0, at_sbb, avoid, back=heading(north[0][0], north[0][1]) + math.pi)
                if south and (best is None or north[1] + south[1] < best[0]):
                    best = (north[1] + south[1], south[0][::-1] + north[0][1:])
            if not best:
                break
            nodes = best[1][::-1]          # north end first: on past the platform, so the whole train stands at it
            if onward(nodes, 320) >= 200:
                break
            short |= {w for _, _, w in adj[best[1][0]] if wtags[w].get("railway:track_ref")}   # a short track: another one
        if not best:
            break
        nodes.reverse()
        tracks.append(export(nodes, "de"))

    # the TGV to France: from the French platforms at Basel SBB (a terminus, so the train leaves the way it came) west
    # through the Schützenmatt and Kannenfeld tunnels, past Basel St. Johann and north off the map towards Saint-Louis
    def at_french(n):
        return any(wtags[w].get("railway:track_ref") in ("30", "31", "32", "33", "34", "35") for _, _, w in adj.get(n, ()))

    best = None
    for n0 in (n for n in running if pos[n][1] > N - 5 and any(wtags[w].get("ref") == "510" for _, _, w in adj[n])):
        got = search(n0, at_french, avoid)
        if got and (best is None or got[1] < best[1]):
            best = got
    if best:
        nodes = best[0]
        onward(nodes, 450)                 # along the platform to the buffer stop
        tracks.append(export(nodes[::-1], "fr"))
    return tracks


def rail_lines(raw, geo):
    """Every railway track inside the map (main lines, stations, yards), for the game to lay: polylines in game units,
    cut where they leave the map or go into a tunnel."""
    src = os.path.join(raw, "osm_rail.json")
    if not os.path.exists(src):
        return []
    W, E, Sth, N = AREA
    runs = []
    for w in json.load(open(src))["elements"]:
        if w["type"] != "way" or w.get("tags", {}).get("tunnel") in ("yes", "building_passage"):
            continue
        cur = []
        for p in w.get("geometry") or []:
            q = geo.local(p["lat"], p["lon"]) if p else None
            if q and W <= q[0] <= E and Sth <= q[1] <= N:
                cur.append(q)
                continue
            if len(cur) > 1:
                runs.append(cur)
            cur = []
        if len(cur) > 1:
            runs.append(cur)
    return [[round(v / S * (1 if i % 2 == 0 else -1), 2) for x, y in rdp(r, 0.3) for i, v in enumerate((x, y))] for r in runs]


TT_DATE = "20261021"     # the timetable's day for the trams and buses: an ordinary Wednesday
TT_AGENCIES = ("Basler Verkehrsbetriebe", "Baselland Transport")
# the bus companies whose lines run in the map: the game's name for each, and how OpenStreetMap writes their operator
BUS_AGENCIES = {"Basler Verkehrsbetriebe": ("BVB", ("BVB", "Basler Verkehrs")),
                "Baselland Transport": ("BLT", ("BLT", "Baselland")),
                "Autobus AG Liestal": ("AAGL", ("AAGL", "Autobus AG Liestal")),
                "SWEG Südwestdeutsche Landesverkehrs-GmbH": ("SWEG", ("SWEG", "Südwestdeutsche"))}


def gtfs_timetable(raw, work):
    """Basel's tram and bus trips on TT_DATE from Switzerland's timetable (GTFS, opentransportdata.swiss): for each
    line, each trip's stops as (lat, lon, arrival, departure) in seconds after midnight, and for the buses the company
    that runs each line. The timetable is big (its stop times alone are 3.7 GB unpacked), so it is read as a stream
    once and the result kept in the work folder."""
    src = os.path.join(raw, "gtfs.zip")
    if not os.path.exists(src):
        return {"trams": {}, "buses": {}}
    key = [os.path.getsize(src), TT_DATE, list(TT_AGENCIES), sorted(BUS_AGENCIES)]
    cache = os.path.join(work, "timetable.json")
    if os.path.exists(cache):
        c = json.load(open(cache))
        if c.get("key") == key:
            return c["timetable"]
    z = zipfile.ZipFile(src)

    def table(name, keep_line=None):   # rows as dicts; keep_line skips most lines before they are parsed
        with z.open(name) as f:
            head = next(csv.reader([f.readline().decode("utf-8-sig")]))
            for raw_line in f:
                line = raw_line.decode("utf-8")
                if keep_line is None or keep_line(line):
                    yield dict(zip(head, next(csv.reader([line]))))

    hms = lambda t: sum(int(v) * m for v, m in zip(t.split(":"), (3600, 60, 1)))
    agency = {r["agency_id"]: r["agency_name"] for r in table("agency.txt") if r["agency_name"] in TT_AGENCIES or r["agency_name"] in BUS_AGENCIES}
    routes = {}                        # route -> (trams or buses, line, company)
    for r in table("routes.txt"):
        name = agency.get(r["agency_id"])
        rt = int(r["route_type"]) if r["route_type"].isdigit() else -1
        if name in TT_AGENCIES and rt in (0, 900):
            routes[r["route_id"]] = ("trams", r["route_short_name"], name)
        elif name in BUS_AGENCIES and (rt == 3 or 700 <= rt <= 799):
            routes[r["route_id"]] = ("buses", r["route_short_name"], name)
    first = lambda l: l.split(",", 1)[0].strip('"')      # every field is quoted
    trips = {r["trip_id"]: (routes[r["route_id"]], r["service_id"]) for r in table("trips.txt", lambda l: first(l) in routes)}
    services = {sid for _, sid in trips.values()}
    day = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")[datetime.date(int(TT_DATE[:4]), int(TT_DATE[4:6]), int(TT_DATE[6:])).weekday()]
    active = {r["service_id"] for r in table("calendar.txt") if r["service_id"] in services and r["start_date"] <= TT_DATE <= r["end_date"] and r[day] == "1"}
    for r in table("calendar_dates.txt", lambda l: TT_DATE in l):
        if r["service_id"] in services and r["date"] == TT_DATE:
            (active.add if r["exception_type"] == "1" else active.discard)(r["service_id"])
    keep = {t for t, (_, sid) in trips.items() if sid in active}
    times = {}
    for r in table("stop_times.txt", lambda l: first(l) in keep):
        times.setdefault(r["trip_id"], []).append((int(r["stop_sequence"]), r["stop_id"], hms(r["arrival_time"]), hms(r["departure_time"])))
    want = {sid for v in times.values() for _, sid, _, _ in v}
    stops = {r["stop_id"]: (float(r["stop_lat"]), float(r["stop_lon"])) for r in table("stops.txt") if r["stop_id"] in want}
    out = {"trams": {}, "buses": {}}         # trams by line; buses by company and line ("BVB 30")
    for tid, seq in times.items():
        seq.sort()
        (kind, line, name), _ = trips[tid]
        out[kind].setdefault(line if kind == "trams" else f"{BUS_AGENCIES[name][0]} {line}", []).append(
            [[*stops[sid], a, d] for _, sid, a, d in seq if sid in stops])
    with open(cache, "w") as f:
        json.dump({"key": key, "timetable": out}, f, separators=(",", ":"))
    return out


def along_line(pts, q):
    """How far q is from a polyline, and the distance along it of its nearest point."""
    best, run = (1e18, 0.0), 0.0
    for a, b in zip(pts, pts[1:]):
        L = math.dist(a, b) or 1e-9
        t = max(0.0, min(1.0, ((q[0] - a[0]) * (b[0] - a[0]) + (q[1] - a[1]) * (b[1] - a[1])) / (L * L)))
        best = min(best, (math.dist(q, (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)), run + L * t))
        run += L
    return best


def timetable_runs(trips, lines, geo, along, min_stops=3, end_reach=35):
    """Each timetabled trip laid on one of the line's routes (the one with most of its stops, at least min_stops):
    the trip's stops that lie on the route, in order (its first and last may be end_reach metres off it), with times
    smoothed (the timetable gives whole minutes, so stops that share a minute are spread by distance) and
    a short stop at each. A trip that carries on beyond the route's ends enters or leaves at the end, out of sight;
    one that starts or ends on the route stands at its terminus for a while. Returns the distinct patterns (stops
    as distance in game units, arrival and departure in seconds after the trip's start) and the trips that use
    them as (route, pattern, start in seconds after midnight)."""
    totals = [sum(math.dist(a, b) for a, b in zip(l, l[1:])) for l in lines]
    pats, index, runs = [], {}, []
    for trip in trips:
        best = None
        for di, line in enumerate(lines):
            kept = []
            for i, (lat, lon, a, d) in enumerate(trip):
                dist, al = along(line, geo.local(lat, lon))
                if dist < (end_reach if i in (0, len(trip) - 1) else 35) and (not kept or al > kept[-1][1] + 5):
                    kept.append((i, al, a, d))
            if len(kept) >= min_stops and (best is None or len(kept) > len(best[1])):
                best = (di, kept)
        if not best:
            continue
        di, kept = best
        sd, t = [k[1] for k in kept], [(k[2] + k[3]) / 2 for k in kept]
        v = max(3.0, (sd[-1] - sd[0]) / max(60.0, t[-1] - t[0]))      # the trip's own pace, metres a second
        firsts = [k for k in range(len(t)) if k == 0 or t[k] != t[k - 1]]
        sm = list(t)
        for g, k0 in enumerate(firsts):
            k1 = firsts[g + 1] if g + 1 < len(firsts) else None
            for k in range(k0, k1 if k1 is not None else len(t)):
                sm[k] = (t[k0] + (t[k1] - t[k0]) * (sd[k] - sd[k0]) / max(1e-6, sd[k1] - sd[k0]) if k1 is not None
                         else t[k0] + (sd[k] - sd[k0]) / v)
        dw = []                                     # a stop: up to 20 s, never more than a third of the runs either side
        for k in range(len(sm)):
            gaps = ([sm[k] - sm[k - 1]] if k > 0 else []) + ([sm[k + 1] - sm[k]] if k + 1 < len(sm) else [])
            dw.append(min(20.0, 0.35 * min(gaps or [60.0])))
        pts = [[sd[k], sm[k] - dw[k] / 2, sm[k] + dw[k] / 2] for k in range(len(sm))]
        if kept[0][0] > 0 and sd[0] < 800:          # it comes from further back: in at the route's start
            pts.insert(0, [0.0, pts[0][1] - sd[0] / v, pts[0][1] - sd[0] / v])
        else:                                       # it starts here: waiting at the terminus first
            pts[0][1] -= 60
        if kept[-1][0] < len(trip) - 1 and totals[di] - sd[-1] < 800:
            pts.append([totals[di], pts[-1][2] + (totals[di] - sd[-1]) / v, pts[-1][2] + (totals[di] - sd[-1]) / v])
        else:
            pts[-1][2] += 45
        t0 = pts[0][1]
        pat = tuple(v for q in pts for v in (round(q[0] / S, 2), round(q[1] - t0, 1), round(q[2] - t0, 1)))
        if pat not in index:
            index[pat] = len(pats)
            pats.append(list(pat))
        runs.append([di, index[pat], round(t0)])
    runs.sort(key=lambda r: r[2])
    return pats, runs


def tram_network(raw, work, geo):
    """Basel's trams, out of OpenStreetMap and the timetable: every tram track in the map, and for each line that
    runs today its colour on the network map, its operator, one route each way with the stops (the pair of
    variants that runs longest in the map, trimmed to the map plus a stretch either side) and every trip of an
    ordinary weekday on them. Game units; empty without the data."""
    src = os.path.join(raw, "osm_tram.json")
    if not os.path.exists(src):
        return [], []
    W, E, Sth, N = AREA
    inside = lambda q: W <= q[0] <= E and Sth <= q[1] <= N
    els = json.load(open(src))["elements"]
    tracks = []
    for w in els:
        if w["type"] != "way":
            continue
        cur = []
        for p in w.get("geometry") or []:
            q = geo.local(p["lat"], p["lon"]) if p else None
            if q and inside(q):
                cur.append(q)
                continue
            if len(cur) > 1:
                tracks.append(cur)
            cur = []
        if len(cur) > 1:
            tracks.append(cur)

    def chain(rel):   # the route's ways in order, joined end to end; its stops; None if the chain has a gap
        segs = [[geo.local(p["lat"], p["lon"]) for p in m["geometry"] if p] for m in rel["members"]
                if m["type"] == "way" and m.get("role", "") in ("", "forward", "backward") and m.get("geometry")]
        segs = [sg for sg in segs if len(sg) > 1]
        if not segs:
            return None
        line = list(segs[0])
        if len(segs) > 1 and min(math.dist(line[0], q) for q in (segs[1][0], segs[1][-1])) < min(math.dist(line[-1], q) for q in (segs[1][0], segs[1][-1])):
            line.reverse()
        for sg in segs[1:]:
            if math.dist(line[-1], sg[-1]) < math.dist(line[-1], sg[0]):
                sg = sg[::-1]
            if math.dist(line[-1], sg[0]) > 40:
                return None
            line += sg[1:]
        stops = [geo.local(m["lat"], m["lon"]) for m in rel["members"]
                 if m["type"] == "node" and m.get("role", "").startswith("stop") and "lat" in m]
        ins = [i for i, q in enumerate(line) if inside(q)]
        if not ins:
            return None
        run, d = [0.0], 0.0
        for a, b in zip(line, line[1:]):
            d += math.dist(a, b)
            run.append(d)
        a0, a1 = run[ins[0]] - 300, run[ins[-1]] + 300       # the map's stretch and 300 m either side
        cut = [q for q, r in zip(line, run) if a0 <= r <= a1]
        if len(cut) < 2:
            return None
        return cut, stops, run[ins[-1]] - run[ins[0]]

    along = along_line

    def ends(rel):
        name = rel.get("tags", {}).get("name", "")
        parts = name.split(":", 1)[-1].split("→")
        return (parts[0].strip(), parts[-1].strip()) if len(parts) == 2 else (name, name)

    timetable = gtfs_timetable(raw, work)["trams"]
    lines = {}
    for r in els:
        if r["type"] == "relation" and r.get("tags", {}).get("ref"):
            c = chain(r)
            if c:
                lines.setdefault(r["tags"]["ref"], []).append((r, c))
    out = []
    for ref, variants in sorted(lines.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 99):
        best = None
        for r1, c1 in variants:
            for r2, c2 in variants:
                if ends(r1) == ends(r2)[::-1] and ends(r1)[0] != ends(r1)[1]:
                    score = c1[2] + c2[2]
                    if best is None or score > best[0]:
                        best = (score, (r1, c1), (r2, c2))
        if best is None:      # no variant back the other way: drive the longest one in reverse
            r1, c1 = max(variants, key=lambda v: v[1][2])
            best = (0, (r1, c1), (r1, (c1[0][::-1], c1[1], c1[2])))
        dirs, routes = [], []
        for r, (line, stops, _) in best[1:]:
            line = rdp(line, 0.5)
            routes.append(line)
            ds = sorted({round(dd / S, 2) for dist, dd in (along(line, q) for q in stops) if dist < 25})
            merged = []
            for dd in ds:                # a stop's two positions (one per platform edge) count once
                if not merged or dd - merged[-1] > 6:
                    merged.append(dd)
            dirs.append({"p": [round(v / S * (1 if i % 2 == 0 else -1), 2) for x, y in line for i, v in enumerate((x, y))],
                         "stops": merged, "name": r["tags"].get("name", "")})
        t = best[1][0]["tags"]
        if not timetable.get(ref):
            continue                                # not in today's timetable
        pats, runs = timetable_runs(timetable[ref], routes, geo, along)
        out.append({"ref": ref, "colour": t.get("colour") or "#888888", "op": t.get("operator") or "", "dirs": dirs,
                    "pats": pats, "trips": [v for r in runs for v in r]})
    return [[round(v / S * (1 if i % 2 == 0 else -1), 2) for x, y in rdp(r, 0.3) for i, v in enumerate((x, y))] for r in tracks], out


LANE_W = 3.2        # metres: a bus keeps to the middle of the rightmost lane it may use
BUS_HALF = 1.45     # half a bus's width (2.55 m) and a little room: from the walls, and from a bus coming the other way
END_REACH = 80      # metres a bus terminus may be off its route: in a turning loop or a bus station of its own


def rdp_long(pts, eps):
    """rdp() for long polylines that may turn back on themselves: no deep recursion, distances to the chord itself."""
    keep, stack = {0, len(pts) - 1}, [(0, len(pts) - 1)]
    while stack:
        i0, i1 = stack.pop()
        a, b = pts[i0], pts[i1]
        L2 = (b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2 or 1e-9
        imax, dmax = 0, -1.0
        for i in range(i0 + 1, i1):
            q = pts[i]
            t = max(0.0, min(1.0, ((q[0] - a[0]) * (b[0] - a[0]) + (q[1] - a[1]) * (b[1] - a[1])) / L2))
            d = math.dist(q, (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
            if d > dmax:
                imax, dmax = i, d
        if dmax > eps:
            keep.add(imax)
            stack += [(i0, imax), (imax, i1)]
    return [pts[i] for i in sorted(keep)]


class Footprints:
    """Where the buildings stand (the outlines of their roofs, whose walls go down to the street), to keep buses off them."""

    def __init__(self, rings):
        self.grid = {}
        for r in rings:
            xs, ys = [p[0] for p in r], [p[1] for p in r]
            bb = (min(xs), max(xs), min(ys), max(ys))
            for gx in range(int(bb[0] // 20), int(bb[1] // 20) + 1):
                for gy in range(int(bb[2] // 20), int(bb[3] // 20) + 1):
                    self.grid.setdefault((gx, gy), []).append((r, bb))

    def hit(self, x, y):
        return any(bb[0] <= x <= bb[1] and bb[2] <= y <= bb[3] and inside((x, y), r)
                   for r, bb in self.grid.get((int(x // 20), int(y // 20)), ()))


def bus_network(raw, work, geo, footprints):
    """Basel's buses, out of OpenStreetMap and the timetable: for each line that runs in the map today, its company and
    its routes (OpenStreetMap's route variants joined way by way, or where a line has none, the roads between its
    stops), each laid in the middle of the rightmost lane and away from the walls, and every trip of an ordinary
    weekday on the route it takes. Game units; empty without the data. Also returns a report for the build log."""
    src = os.path.join(raw, "osm_bus.json")
    timetable = gtfs_timetable(raw, work)["buses"]
    if not os.path.exists(src) or not timetable:
        return [], {}
    W, E, Sth, N = AREA
    in_map = lambda q, m=0: W - m <= q[0] <= E + m and Sth - m <= q[1] <= N + m
    els = json.load(open(src))["elements"]
    ways = {e["id"]: e for e in els if e["type"] == "way"}
    pos = {}
    for w in ways.values():
        for nid, p in zip(w["nodes"], w.get("geometry") or []):
            if p:
                pos[nid] = geo.local(p["lat"], p["lon"])

    def oneway(t):
        return (t.get("oneway") in ("yes", "true", "1", "-1") or t.get("junction") in ("roundabout", "circular")
                or t.get("highway") in ("motorway", "motorway_link")) and "no" not in (t.get("oneway:bus"), t.get("oneway:psv"))

    def offset(t, forward):     # metres right of the way's middle: the middle of the rightmost lane a bus may use there
        ow = oneway(t)
        if t.get("oneway") == "-1":
            forward = not forward
        try:
            n = int(str(t.get("lanes", "")).split(";")[0])
        except ValueError:
            n = 1 if ow else 2
        if ow and not forward:      # against the one-way traffic: in a bus lane of its own beside it
            n += 1
        o = (n - 1) / 2 * LANE_W
        return min(6.0, o if ow and forward else max(o, BUS_HALF))

    def chain(rel):
        """The route's ways in driving order, joined end to end: points, each with the lane offset of its way; None if
        there is a gap. A roundabout that is a member as a whole is driven from where the route enters it to where
        it leaves."""
        mem = []
        for m in rel["members"]:
            if m["type"] == "way" and m.get("role", "") in ("", "forward", "backward") and m.get("geometry"):
                pts = [geo.local(p["lat"], p["lon"]) for p in m["geometry"] if p]
                if len(pts) > 1:
                    mem.append((pts, ways.get(m["ref"], {}).get("tags", {})))
        if not mem:
            return None
        ring = lambda pts: len(pts) > 3 and math.dist(pts[0], pts[-1]) < 0.5
        near = lambda q, pts: min(math.dist(q, p) for p in (pts if ring(pts) else (pts[0], pts[-1])))
        line, offs = [], []
        for k, (pts, t) in enumerate(mem):
            nxt = mem[k + 1][0] if k + 1 < len(mem) else None
            if ring(pts):
                loop = pts[:-1]
                a = min(range(len(loop)), key=lambda i: math.dist(loop[i], line[-1])) if line else 0
                b = min(range(len(loop)), key=lambda i: near(loop[i], nxt)) if nxt else (a - 1) % len(loop)
                fwd = (b - a) % len(loop)
                if not oneway(t) and fwd > len(loop) - fwd:      # not one way round: the shorter way
                    seg, forward = [loop[(a - j) % len(loop)] for j in range(len(loop) - fwd + 1)], False
                else:
                    seg, forward = [loop[(a + j) % len(loop)] for j in range(fwd + 1)], True
            elif line:
                forward = math.dist(line[-1], pts[0]) <= math.dist(line[-1], pts[-1])
                seg = pts if forward else pts[::-1]
            else:
                forward = not nxt or near(pts[-1], nxt) <= near(pts[0], nxt)
                seg = pts if forward else pts[::-1]
            if line:
                gap = math.dist(line[-1], seg[0])
                if gap > 40:
                    return None
                if gap < 0.5:
                    seg = seg[1:]
            o = offset(t, forward)
            line += seg
            offs += [o] * len(seg)
        return line, offs

    def cut(line, offs):            # the map's stretch of a route and 300 m either side, like the trams'
        ins = [i for i, q in enumerate(line) if in_map(q)]
        if not ins:
            return None
        run = [0.0]
        for a, b in zip(line, line[1:]):
            run.append(run[-1] + math.dist(a, b))
        a0, a1 = run[ins[0]] - 300, run[ins[-1]] + 300
        keep = [i for i, r in enumerate(run) if a0 <= r <= a1]
        return ([line[i] for i in keep], [offs[i] for i in keep]) if len(keep) > 1 else None

    def box(v, r):                  # mean over 2r+1 samples
        acc, out = [0.0], []
        for x in v:
            acc.append(acc[-1] + x)
        for k in range(len(v)):
            i0, i1 = max(0, k - r), min(len(v), k + r + 1)
            out.append((acc[i1] - acc[i0]) / (i1 - i0))
        return out

    def lay(line, offs):
        """The route as a bus drives it: a point every metre, moved right by its lane's offset (eased over 20 m where
        the lanes change) and back towards the middle of the road wherever a side of the bus would be in a building.
        Also the metres that stay in one (under a building, or nowhere to go)."""
        P, O = [line[0]], [offs[0]]
        for i in range(1, len(line)):
            a, b = line[i - 1], line[i]
            n = max(1, int(math.dist(a, b)))
            for k in range(1, n + 1):
                P.append((a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n))
                O.append(offs[i])
        n = len(P)
        T = []
        for k in range(n):
            a, b = P[max(0, k - 3)], P[min(n - 1, k + 3)]
            L = math.dist(a, b) or 1.0
            T.append(((b[0] - a[0]) / L, (b[1] - a[1]) / L))
        O = box(O, 10)
        lim, stuck = [], 0
        for k in range(n):
            (px, py), (tx, ty) = P[k], T[k]
            o, got = O[k], None
            while o > -0.01 and got is None:
                cx, cy = px + ty * o, py - tx * o
                if not any(footprints.hit(cx + ty * s, cy - tx * s) for s in (-BUS_HALF, 0.0, BUS_HALF)):
                    got = o
                o -= 0.25
            if got is None:
                stuck += 1
            lim.append(O[k] if got is None else got)
        ero = [min(lim[max(0, k - 6):k + 7]) for k in range(n)]
        O = box(ero, 6)                 # never above what is clear: a mean over a window no wider than the minimum's
        Q, last = [], None
        for k in range(n):
            (px, py), (tx, ty) = P[k], T[k]
            q = (px + ty * O[k], py - tx * O[k])
            if last is None or (q[0] - last[0]) * tx + (q[1] - last[1]) * ty > 0.05:   # no loops on a tight bend
                Q.append(q)
                last = q
        return rdp_long(Q, 0.2), stuck

    # the roads, for a line without a route of its own: directed edges between OSM nodes, on the ways buses may use
    adj, egrid = {}, {}
    for w in ways.values():
        t = w.get("tags", {})
        if any(t.get(k) in ("no", "private") for k in ("access", "motor_vehicle", "vehicle")) and not (
                {t.get("bus"), t.get("psv")} & {"yes", "designated", "permissive"}):
            continue
        ow, rev = oneway(t), t.get("oneway") == "-1"
        for a, b in zip(w["nodes"], w["nodes"][1:]):
            if a not in pos or b not in pos:
                continue
            L = math.dist(pos[a], pos[b])
            if not (ow and rev):
                adj.setdefault(a, []).append((b, L, t, True))
            if not ow or rev:
                adj.setdefault(b, []).append((a, L, t, False))
            for gx in range(int(min(pos[a][0], pos[b][0]) // 25), int(max(pos[a][0], pos[b][0]) // 25) + 1):
                for gy in range(int(min(pos[a][1], pos[b][1]) // 25), int(max(pos[a][1], pos[b][1]) // 25) + 1):
                    egrid.setdefault((gx, gy), []).append((a, b))

    def edge(u, v):                 # the drivable edge from u to v: (length, tags, along the way's direction) or None
        return next(((L, t, f) for x, L, t, f in adj.get(u, ()) if x == v), None)

    def snap(q):                    # the nearest road to a stop: its two nodes (in the way's order) and how far along
        best = None
        for gx in range(int(q[0] // 25) - 2, int(q[0] // 25) + 3):
            for gy in range(int(q[1] // 25) - 2, int(q[1] // 25) + 3):
                for a, b in egrid.get((gx, gy), ()):
                    (ax, ay), (bx, by) = pos[a], pos[b]
                    L2 = (bx - ax) ** 2 + (by - ay) ** 2 or 1e-9
                    u = max(0.0, min(1.0, ((q[0] - ax) * (bx - ax) + (q[1] - ay) * (by - ay)) / L2))
                    d = math.dist(q, (ax + (bx - ax) * u, ay + (by - ay) * u))
                    if d < 45 and (best is None or d < best[0]):
                        best = (d, a, b, u)
        return best and best[1:]

    GOAL = -1                       # (OSM node ids are positive)

    def route(qs):
        """The shortest drive along the roads through the stops qs in order: points with their lane offsets, or None."""
        snaps = [snap(q) for q in qs]
        if any(s is None for s in snaps):
            return None
        at = lambda s: (pos[s[0]][0] + (pos[s[1]][0] - pos[s[0]][0]) * s[2], pos[s[0]][1] + (pos[s[1]][1] - pos[s[0]][1]) * s[2])
        line, offs = [at(snaps[0])], [BUS_HALF]
        for (a0, b0, u0), (a1, b1, u1) in zip(snaps, snaps[1:]):
            q0, q1 = at((a0, b0, u0)), at((a1, b1, u1))
            if (a0, b0) == (a1, b1) and ((u1 > u0 and edge(a0, b0)) or (u1 < u0 and edge(b0, a0))):
                e = edge(a0, b0) if u1 > u0 else edge(b0, a0)
                line.append(q1)
                offs.append(offset(e[1], e[2]))
                continue
            best, prev, pq = {}, {}, []
            for u, v in ((a0, b0), (b0, a0)):      # off along the first stop's road, whichever way it may be driven
                e = edge(u, v)
                if e and math.dist(q0, pos[v]) < best.get(v, 1e18):
                    best[v], prev[v] = math.dist(q0, pos[v]), (None, e[1], e[2])
                    heapq.heappush(pq, (best[v], v))
            ends = {u: edge(u, v) for u, v in ((a1, b1), (b1, a1)) if edge(u, v)}   # and onto the next stop's road
            goal = None
            while pq:
                d, u = heapq.heappop(pq)
                if u == GOAL:
                    goal = True
                    break
                if d > best.get(u, 1e18):
                    continue
                if u in ends and d + math.dist(pos[u], q1) < best.get(GOAL, 1e18):
                    best[GOAL], prev[GOAL] = d + math.dist(pos[u], q1), (u, ends[u][1], ends[u][2])
                    heapq.heappush(pq, (best[GOAL], GOAL))
                for v, L, t, f in adj.get(u, ()):
                    if d + L < best.get(v, 1e18):
                        best[v], prev[v] = d + L, (u, t, f)
                        heapq.heappush(pq, (d + L, v))
            if not goal or best[GOAL] > 4 * math.dist(q0, q1) + 600:
                return None                 # no way there, or only a long way round: something is missing
            path, u = [], GOAL
            while u is not None:
                pu, t, f = prev[u]
                path.append((q1 if u == GOAL else pos[u], offset(t, f)))
                u = pu
            for q, o in path[::-1]:
                if math.dist(line[-1], q) > 0.3:
                    line.append(q)
                    offs.append(o)
        return line, offs

    rels = [e for e in els if e["type"] == "relation"]
    out, report = [], {}
    for key in sorted(timetable, key=lambda k: (k.split(" ")[0], k.split(" ", 1)[1].zfill(5))):
        trips = timetable[key]
        op, ref = key.split(" ", 1)
        names = next(v[1] for v in BUS_AGENCIES.values() if v[0] == op)
        memo = {}

        def along(line, q):
            k = (id(line), q)
            if k not in memo:
                memo[k] = along_line(line, q)
            return memo[k]

        def covered(qs, line):          # how many of a trip's stops in the map lie on the route, in order, as
            n, last = 0, -1e9           # timetable_runs keeps them (a terminus may be off it, in a loop or a bus station)
            for i, q in enumerate(qs):
                if in_map(q, 30):
                    d, al = along(line, q)
                    if d < (END_REACH if i in (0, len(qs) - 1) else 35) and al > last + 5:
                        n, last = n + 1, al
            return n

        trip_qs = [[geo.local(lat, lon) for lat, lon, _, _ in trip] for trip in trips]
        if not any(sum(1 for q in qs if in_map(q)) >= 2 for qs in trip_qs):
            continue                    # the line never comes into the map
        routes, kinds, stuck = [], [], []
        for r in rels:
            t = r.get("tags", {})
            if t.get("ref") == ref and any(nm in (t.get("operator") or "") for nm in names):
                c = chain(r)
                c = c and cut(*c)
                if c:
                    pts, st = lay(*c)
                    routes.append(pts)
                    kinds.append("osm")
                    stuck.append(st)
        # a trip that stops in the map where none of the line's routes go is driven along the roads between its stops,
        # from the one before it comes into the map to the one after it leaves
        need = Counter()
        for qs in trip_qs:
            ins = [i for i, q in enumerate(qs) if in_map(q)]
            if len(ins) < 2 or (routes and max(covered(qs, l) for l in routes) >= sum(1 for q in qs if in_map(q, 30))):
                continue
            need[tuple(qs[max(0, ins[0] - 1):ins[-1] + 2])] += 1
        for qs, _ in need.most_common():
            if any(covered(qs, l) >= sum(1 for q in qs if in_map(q, 30)) for l in routes):
                continue
            c = route(list(qs))
            c = c and cut(*c)
            if c:
                pts, st = lay(*c)
                routes.append(pts)
                kinds.append("road")
                stuck.append(st)
        if not routes:
            continue
        pats, runs = timetable_runs(trips, routes, geo, along, min_stops=2, end_reach=END_REACH)
        if not runs:
            continue
        used = sorted({r[0] for r in runs})
        renum = {di: k for k, di in enumerate(used)}
        out.append({"ref": ref, "op": op,
                    "routes": [{"p": [round(v / S * (1 if i % 2 == 0 else -1), 2) for x, y in routes[di] for i, v in enumerate((x, y))]} for di in used],
                    "pats": pats, "trips": [v for r in runs for v in (renum[r[0]], r[1], r[2])]})
        report[key] = {"trips": [len(runs), len(trips)], "patterns": len(pats),
                       "routes": [[kinds[di], len(routes[di]), stuck[di]] for di in used]}
    return out, report


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

    iconic = {"roche1": [], "roche2": None, "messe": None}
    skip_rings = [lm_rings[k] for k in HAND_MODELLED if k in lm_rings]
    bau1 = next((o for o in outlines if o["type"] == "way" and o["id"] == 680531949), None)   # Roche Tower, Bau 1
    if bau1:
        skip_rings.append(bau1["outer"][0])
        for prt in parts:
            if inside(centroid(prt["outer"][0]), bau1["outer"][0]):
                b, top, *_ = building_heights(prt["tags"], abs(area2(prt["outer"][0])))
                iconic["roche1"].append({"ring": game_ring(prt["outer"][0]), "base": round(b, 1), "top": round(top, 1)})
    for key, typ, oid in (("roche2", "way", 317511699), ("messe", "way", 315916699)):
        o = next((o for o in outlines if o["type"] == typ and o["id"] == oid), None)
        if o:
            outer = max(o["outer"], key=lambda r: abs(area2(r)))
            b, top, *_ = building_heights(o["tags"], abs(area2(outer)))
            iconic[key] = {"rings": [game_ring(outer)] + [game_ring(h) for h in o["inner"]], "top": round(top, 1)}
            skip_rings.append(outer)
    skip_bb = [bbox(r) for r in skip_rings]
    facade_rings = []
    for key, typ, oid in FACADES:
        o = next((o for o in outlines if o["type"] == typ and o["id"] == oid), None) if typ else None
        if o:
            for r in o["outer"]:
                facade_rings.append((r, bbox(r), FACADE_STYLES.index(key)))
    lm_bb = {k: bbox(r) for k, r in lm_rings.items()}
    lm_heights = {k: [] for k in lm_rings}       # roof heights of the 3D model inside each landmark, and its peak
    lm_peak = {}

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
    # covered footbridges (OSM building=bridge): swisstopo models them down to the ground, often as one building with
    # the houses they join, so the street under them would be walled off. Their roofs become buildings of their own,
    # open underneath
    spans = [(r, bbox(r)) for o in outlines if o["tags"].get("building") == "bridge" for r in o["outer"]]
    on_span = lambda q: any(bb[0] <= q[0] <= bb[1] and bb[2] <= q[1] <= bb[3] and inside(q, r) for r, bb in spans)
    split = []
    for b in blds:
        xs, ys = [p[0] for f in b["roof"] for p in f], [p[1] for f in b["roof"] for p in f]
        if not any(bb[0] <= max(xs) and min(xs) <= bb[1] and bb[2] <= max(ys) and min(ys) <= bb[3] for _, bb in spans):
            split.append(b)
            continue
        over = [f for f in b["roof"] if on_span((sum(p[0] for p in f) / len(f), sum(p[1] for p in f) / len(f)))]
        rest = [f for f in b["roof"] if f not in over]
        split += ([dict(b, roof=over, span=True)] if over else []) + ([dict(b, roof=rest)] if rest else [])
    blds = split
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

    # platform roofs and train sheds: swisstopo gives some of them walls down to the ground (Basel SBB's are one big
    # building with the Passerelle). A low roof with railway under a third of it is opened up: posts, no walls
    rail_cells = set()
    if os.path.exists(os.path.join(raw, "osm_rail.json")):
        for w in json.load(open(os.path.join(raw, "osm_rail.json")))["elements"]:
            pts = [geo.local(p["lat"], p["lon"]) for p in w.get("geometry") or [] if p] if w["type"] == "way" else []
            for a, b in zip(pts, pts[1:]):
                n = max(1, int(math.dist(a, b) / 2))
                for k in range(n):
                    rail_cells.add((int((a[0] + (b[0] - a[0]) * k / n) // 4), int((a[1] + (b[1] - a[1]) * k / n) // 4)))
    rail_near = {(x + dx, y + dy) for x, y in rail_cells for dx in (-3, 0, 3) for dy in (-3, 0, 3)}

    def over_tracks(roof, g):    # a low roof (not an office block beside the line) that is mostly railway beneath
        if min(p[2] for f in roof for p in f) - g > 15 or not any((int(p[0] // 4), int(p[1] // 4)) in rail_near for f in roof for p in f):
            return False
        cells = set()
        for f in roof:
            ring = [(p[0], p[1]) for p in f]
            xs, ys = [q[0] for q in ring], [q[1] for q in ring]
            for cx in range(int(min(xs) // 4), int(max(xs) // 4) + 1):
                for cy in range(int(min(ys) // 4), int(max(ys) // 4) + 1):
                    if (cx, cy) not in cells and inside((cx * 4 + 2, cy * 4 + 2), ring):
                        cells.add((cx, cy))
        hit = sum(1 for c in cells if c in rail_cells)
        return hit >= 12 and hit >= 0.35 * len(cells)

    # the shopping streets and squares, sampled every metre: a house with a corner within reach of one gets shop fronts
    shop_samples = {}

    def sample(pts, reach):
        for a, b in zip(pts, pts[1:]):
            n = max(1, int(math.dist(a, b)))
            for k in range(n + 1):
                q = (a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n)
                shop_samples.setdefault((int(q[0] // 10), int(q[1] // 10)), []).append((q, reach))

    streets_path = os.path.join(raw, "osm_streets.json")
    shop_lines = []
    if os.path.exists(streets_path):
        for w in json.load(open(streets_path))["elements"]:
            hw = SHOP_STREETS.get(w.get("tags", {}).get("name"))
            if w["type"] == "way" and hw:
                line = [geo.local(p["lat"], p["lon"]) for p in w.get("geometry") or [] if p]
                sample(line, hw + 3.5)
                shop_lines.append((line, hw))
    squares = {}
    for el in xjs:
        for name, (typ, oid) in SQUARES.items():
            if el["type"] == typ and el["id"] == oid:
                outer, _ = element_rings(el, geo)
                if outer:
                    squares[name] = max(outer, key=lambda r: abs(area2(r)))
                    sample(squares[name] + squares[name][:1], 3.5)
                    shop_lines.append((squares[name] + squares[name][:1], 0.0))

    def fronts_shops(pts):
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        if not any((i, j) in shop_samples for i in range(int(min(xs) // 10) - 1, int(max(xs) // 10) + 2)
                   for j in range(int(min(ys) // 10) - 1, int(max(ys) // 10) + 2)):
            return False
        for p in pts:
            cx, cy = int(p[0] // 10), int(p[1] // 10)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for q, reach in shop_samples.get((cx + dx, cy + dy), ()):
                        if math.dist(p[:2], q) < reach:
                            return True
        return False

    stats = Counter()
    recs = []
    for b in blds:
        roof = b["roof"]
        pts = [p for f in roof for p in f]
        c = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
        for k, bb in lm_bb.items():
            if bb[0] <= c[0] <= bb[1] and bb[2] <= c[1] <= bb[3] and inside(c, lm_rings[k]):
                g0 = b["ground"] if b["ground"] is not None else min(p[2] for p in pts) - 3.0
                lm_heights[k] += [p[2] - g0 for p in pts]
                top = max(pts, key=lambda p: p[2])
                if k not in lm_peak or top[2] - g0 > lm_peak[k][2]:
                    lm_peak[k] = (top[0], top[1], top[2] - g0)
        if any(bb[0] <= c[0] <= bb[1] and bb[2] <= c[1] <= bb[3] and inside(c, r) for r, bb in zip(skip_rings, skip_bb)):
            stats["landmark"] += 1
            continue
        style = None
        for q in (c, centroid(max(([(p[0], p[1]) for p in f] for f in roof), key=lambda r: abs(area2(r))))):
            style = next((st for r, bb, st in facade_rings if bb[0] <= q[0] <= bb[1] and bb[2] <= q[1] <= bb[3] and inside(q, r)), None)
            if style is not None:      # (a wedge round a courtyard has its middle outside it: then its biggest roof)
                break
        kind = KINDS.get(b["kind"], 0)
        g = b["ground"]
        if g is None:
            g = ground_at(*c)
            if g is None:
                g = b["wall_min"] if b["wall_min"] is not None else min(p[2] for p in pts) - 3.0
        top_rel = max(p[2] for p in pts) - g
        if style is None and kind == 2:
            style = FACADE_STYLES.index("sacral")
        elif style is None and (kind == 1 or (kind in (0, 10) and top_rel > 40)):
            hi = [p for p in pts if p[2] - g > top_rel - 15]   # a real tower is still wide near its top; a chimney is not
            if max(max(p[0] for p in hi) - min(p[0] for p in hi), max(p[1] for p in hi) - min(p[1] for p in hi)) >= 12:
                style = FACADE_STYLES.index("highrise")
        if style is None and kind in (0, 10) and top_rel < 40 and fronts_shops(pts):
            style = FACADE_STYLES.index(SHOP_STYLES[int(abs(c[0] * 7.31 + c[1] * 3.17)) % len(SHOP_STYLES)])
            stats["shops"] += 1
        zbot = 0.0
        lift = 0.0
        if style is None and kind in (0, 10) and over_tracks(roof, g):
            kind, zbot = 5, 2.5         # on posts; the game keeps only a fascia under each roof edge
            stats["shed"] += 1
        elif b.get("span"):
            kind, zbot = 9, max(4.5, top_rel - 4.0)     # a covered footbridge, about 4 m deep and high over the street
            stats["footbridge"] += 1
        elif kind in (4, 5, 9):         # canopies, open buildings and covered bridges stand on posts or span a street
            if b["wall_min"] is not None and b["wall_min"] - g > 1.5:
                zbot = b["wall_min"] - g
            elif kind in (4, 5):        # a roof without walls: a platform or shelter roof on posts, 2.5 to 5 m up
                under = min(p[2] for p in pts) - g
                lift = min(5.0, max(2.5, under)) - under
                zbot = under + lift - 0.5
        verts, loops = sb3d.simplify_roof([[(p[0], p[1], p[2] - g + lift) for p in f] for f in roof])
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
        recs.append({"c": c, "kind": kind if style is None else style, "roof": tile_tint(rc) if pitched else rc,
                     "wall": hexrgb(wall), "zbot": zbot, "verts": verts, "loops": loops, "g": g, "facade": style is not None})

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

    # pack: varints, buildings in rows of 100 m cells so neighbours follow each other, then gzip. Facade buildings
    # (their kind byte is the facade style) go in a pack of their own.
    def pack(rs):
        rs.sort(key=lambda r: (int(r["c"][1] // 100), int(r["c"][0] // 100), r["c"][0]))
        buf = bytearray()
        fx = fy = 0
        for r in rs:
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
        return buf, gzip.compress(bytes(buf), 9, mtime=0)

    frecs = [r for r in recs if r["facade"]]
    recs = [r for r in recs if not r["facade"]]

    # the Rathaus front onto the Marktplatz: the longest straight run of its walls facing the square, so the game
    # can put the arcade, battlements, clock and flèche exactly on it
    rathaus_front = rathaus_tower = None
    cands = []
    for r in frecs:
        if FACADE_STYLES[r["kind"]] != "rathaus":
            continue
        v, loops, flags = r["verts"], r["loops"], r["walls"]
        directed = {(l[k], l[(k + 1) % len(l)]) for l in loops for k in range(len(l))}
        bnd = [(l[k], l[(k + 1) % len(l)]) for l in loops for k in range(len(l)) if (l[(k + 1) % len(l)], l[k]) not in directed]
        for (a, b), f in zip(bnd, flags):
            pa, pb = v[a], v[b]
            dx, dy = pb[0] - pa[0], pb[1] - pa[1]
            L = math.hypot(dx, dy)
            if not f or L < 3:
                continue
            mx, my = (pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2
            tx, ty = MARKT[0] - mx, MARKT[1] - my
            tl = math.hypot(tx, ty) or 1
            if (dy * tx - dx * ty) / (L * tl) > 0.75:          # the outward normal (dy, -dx) points at the square
                cands.append((L, pa, pb))
    if cands:
        cands.sort(key=lambda c: -c[0])
        _, pa, pb = cands[0]
        ux, uy = (pb[0] - pa[0]) / cands[0][0], (pb[1] - pa[1]) / cands[0][0]
        ts, zs = [0.0, cands[0][0]], [pa[2], pb[2]]
        for L, qa, qb in cands[1:]:
            off = [abs((q[0] - pa[0]) * uy - (q[1] - pa[1]) * ux) for q in (qa, qb)]
            if max(off) < 1.2:                                  # on the same facade line
                ts += [(q[0] - pa[0]) * ux + (q[1] - pa[1]) * uy for q in (qa, qb)]
                zs += [qa[2], qb[2]]
        t0, t1 = min(ts), max(ts)
        rathaus_front = [round((pa[0] + ux * t0) / S, 2), round(-(pa[1] + uy * t0) / S, 2),
                         round((pa[0] + ux * t1) / S, 2), round(-(pa[1] + uy * t1) / S, 2), round(min(zs), 1)]
        # the roof height just behind the front, every 1/48 of it: the tall south wing, the old front building and the
        # tower each have their own height, so the game can tell them apart
        planes = []
        for r in frecs:
            if FACADE_STYLES[r["kind"]] == "rathaus":
                for l in r["loops"]:
                    pts = [r["verts"][i] for i in l]
                    nrm = sb3d.newell(pts)
                    if nrm and nrm[2] > 0.05:
                        planes.append(([p[:2] for p in pts], pts[0], nrm))
        nx, ny = uy, -ux                                     # the front's outward normal, onto the square
        if nx * (MARKT[0] - pa[0]) + ny * (MARKT[1] - pa[1]) < 0:
            nx, ny = -nx, -ny
        prof = []
        for k in range(49):
            t = t0 + (t1 - t0) * k / 48
            q = (pa[0] + ux * t - nx * 1.0, pa[1] + uy * t - ny * 1.0)
            prof.append(round(max([p0[2] - (n[0] * (q[0] - p0[0]) + n[1] * (q[1] - p0[1])) / n[2]
                                   for ring, p0, n in planes if inside(q, ring)] or [0.0]), 1))
        rathaus_front.append(prof)
        # the tower's shaft: the roof parts round the spire above 30 m, whose lowest corner is where its walls stop
        if "rathaus" in lm_peak:
            tx, ty, _ = lm_peak["rathaus"]
            tv = [p for r in frecs if FACADE_STYLES[r["kind"]] == "rathaus" for l in r["loops"]
                  if all(math.dist(r["verts"][i][:2], (tx, ty)) < 7 and r["verts"][i][2] > 30 for i in l) for p in (r["verts"][i] for i in l)]
            if tv:
                us = [(p[0] - tx) * ux + (p[1] - ty) * uy for p in tv]
                ns = [(p[0] - tx) * nx + (p[1] - ty) * ny for p in tv]
                cu, cn = (min(us) + max(us)) / 2, (min(ns) + max(ns)) / 2
                cx, cy = tx + ux * cu + nx * cn, ty + uy * cu + ny * cn
                rathaus_tower = [round(cx / S, 2), round(-cy / S, 2), round((max(us) - min(us)) / 2 / S, 2),
                                 round((max(ns) - min(ns)) / 2 / S, 2), round(min(p[2] for p in tv), 1)]
    # awnings over the shop windows: along every wall of a shop house that faces one of the streets or squares,
    # one every 6.5 m, as (x, z, the angle its front faces) in game units
    awnings = []

    def street_side(m, nrm):     # is this wall's midpoint at a street edge, looking onto it?
        for line, hw in shop_lines:
            for a, b in zip(line, line[1:]):
                L = math.dist(a, b) or 1e-9
                t = max(0.0, min(1.0, ((m[0] - a[0]) * (b[0] - a[0]) + (m[1] - a[1]) * (b[1] - a[1])) / (L * L)))
                q = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
                d = math.dist(m, q)
                if d < hw + 3.5 and d > 0.3 and ((q[0] - m[0]) * nrm[0] + (q[1] - m[1]) * nrm[1]) / d > 0.55:
                    return True
        return False

    for r in frecs:
        if FACADE_STYLES[r["kind"]] not in SHOP_STYLES:
            continue
        v, loops, flags = r["verts"], r["loops"], r["walls"]
        directed = {(l[k], l[(k + 1) % len(l)]) for l in loops for k in range(len(l))}
        bnd = [(l[k], l[(k + 1) % len(l)]) for l in loops for k in range(len(l)) if (l[(k + 1) % len(l)], l[k]) not in directed]
        for (a, b), f in zip(bnd, flags):
            pa, pb = v[a], v[b]
            L = math.dist(pa[:2], pb[:2])
            if not f or L < 3.5:
                continue
            nrm = ((pb[1] - pa[1]) / L, -(pb[0] - pa[0]) / L)      # outward, as for the Rathaus front
            if not street_side(((pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2), nrm):
                continue
            n = max(1, int(L / 6.5))
            for k in range(n):
                t = (k + 0.5) / n
                x, y = pa[0] + (pb[0] - pa[0]) * t + nrm[0] * 0.05, pa[1] + (pb[1] - pa[1]) * t + nrm[1] * 0.05
                awnings += [round(x / S, 2), round(-y / S, 2), round(math.atan2(nrm[0], -nrm[1]), 3), round(min(L / n, 6.0) / S, 2)]
    market = None
    if "Marktplatz" in squares:
        avoid = []
        for el in xjs:
            t = el.get("tags", {})
            if el["type"] == "way" and t.get("name") == "Marktplatz" and t.get("railway") == "platform" and t.get("area") == "yes":
                outer, _ = element_rings(el, geo)
                avoid += [[[round(x / S, 2), round(-y / S, 2)] for x, y in o] for o in outer]
        market = {"square": [[round(x / S, 2), round(-y / S, 2)] for x, y in squares["Marktplatz"]], "avoid": avoid}

    # Basel SBB's front onto the Centralbahnplatz: the ticket hall's glazed arch between two clock towers. The towers
    # are the two small clusters of the hall's highest roof points, the arch is the end of its barrel vault between
    # them; the outline of the arch is read from the roof so the game can glaze it and put domes on the towers
    # the Kollegienhaus: every eave (a wall's top edge, with its height and which way it faces) for the deep
    # overhanging roof, the wall with the main door for the entrance hall, and the height of its highest roof
    uni = None
    ue = geo.local(*UNI_ENTRANCE)
    edges = []
    for r in frecs:
        if FACADE_STYLES[r["kind"]] != "uni":
            continue
        v, loops, flags = r["verts"], r["loops"], r["walls"]
        directed = {(l[k], l[(k + 1) % len(l)]) for l in loops for k in range(len(l))}
        bnd = [(l[k], l[(k + 1) % len(l)]) for l in loops for k in range(len(l)) if (l[(k + 1) % len(l)], l[k]) not in directed]
        for (a, b), f in zip(bnd, flags):
            pa, pb = v[a], v[b]
            L = math.dist(pa[:2], pb[:2])
            if f and L > 1.0:
                edges.append((pa, pb, L, ((pb[1] - pa[1]) / L, -(pb[0] - pa[0]) / L)))
    if edges:
        def gap(e):
            (pa, pb, L, _) = e
            t = max(0.0, min(1.0, ((ue[0] - pa[0]) * (pb[0] - pa[0]) + (ue[1] - pa[1]) * (pb[1] - pa[1])) / (L * L)))
            return math.dist(ue, (pa[0] + (pb[0] - pa[0]) * t, pa[1] + (pb[1] - pa[1]) * t)), t
        door = min(edges, key=lambda e: gap(e)[0])
        (pa, pb, L, nrm), t = door, gap(door)[1]
        uni = {"eaves": [v for pa, pb, L, nrm in edges for v in (round(pa[0] / S, 2), round(-pa[1] / S, 2), round(pb[0] / S, 2),
                                                                   round(-pb[1] / S, 2), round(min(pa[2], pb[2]), 1), round(nrm[0], 3), round(-nrm[1], 3))],
               "door": [round((pa[0] + (pb[0] - pa[0]) * t) / S, 2), round(-(pa[1] + (pb[1] - pa[1]) * t) / S, 2),
                        round(math.atan2(nrm[0], -nrm[1]), 4), round(min(pa[2], pb[2]), 1), round(L / S, 2), round(t, 3)],
               "top": round(max(p[2] for r in frecs if FACADE_STYLES[r["kind"]] == "uni" for p in r["verts"]), 1)}
    # Les Trois Rois: Amadeus Merian's hotel (1842-44), joined in 2004 by the Kantonalbank's corner building of 1901-03
    # at the Schifflände. Every wall of the hotel (its top edge, height and which way it faces), so the game can lay
    # the terrace over the Rhine along the walls facing the water and the porch on the middle of the street front;
    # and the height of its flat roof, where the corner pavilions have their railed terraces and flagpoles
    tr_walls, tr_top = [], 0.0
    for r in frecs:
        if FACADE_STYLES[r["kind"]] != "troisrois":
            continue
        v, loops, flags = r["verts"], r["loops"], r["walls"]
        directed = {(l[k], l[(k + 1) % len(l)]) for l in loops for k in range(len(l))}
        bnd = [(l[k], l[(k + 1) % len(l)]) for l in loops for k in range(len(l)) if (l[(k + 1) % len(l)], l[k]) not in directed]
        for (a, b), f in zip(bnd, flags):
            pa, pb = v[a], v[b]
            L = math.dist(pa[:2], pb[:2])
            if f and L > 1.0:
                nx, ny = (pb[1] - pa[1]) / L, -(pb[0] - pa[0]) / L
                tr_walls += [round(pa[0] / S, 2), round(-pa[1] / S, 2), round(pb[0] / S, 2), round(-pb[1] / S, 2),
                             round(min(pa[2], pb[2]), 1), round(nx, 3), round(-ny, 3)]
        for l in loops:
            zs = [v[i][2] for i in l]
            if len(l) >= 3 and max(zs) - min(zs) < 0.4 and min(zs) > 18:
                tr_top = max(tr_top, round(max(zs), 1))
    if tr_walls:
        iconic["troisrois"] = {"walls": tr_walls, "top": tr_top}
    sbb_front = None
    node = geo.local(47.54762, 7.58964)
    halls = [r for r in frecs if FACADE_STYLES[r["kind"]] == "sbb" and math.dist(r["c"], node) < 60]
    if halls:
        hall = max(halls, key=lambda r: max(v[2] for v in r["verts"]))
        V = hall["verts"]
        top = max(v[2] for v in V)
        clusters = []
        for v in (v for v in V if v[2] > top - 4):
            near = [c for c in clusters if any(math.dist(v[:2], w[:2]) < 3 for w in c)]
            merged = [v] + [w for c in near for w in c]
            clusters = [c for c in clusters if c not in near] + [merged]
        span = lambda c: max(math.dist(a[:2], b[:2]) for a in c for b in c)
        small = sorted((c for c in clusters if len(c) >= 4 and 3 < span(c) < 12), key=len)[-2:]     # the domes
        ridge = [v for c in clusters if c not in small for v in c]                                   # the vault's crown
        if len(small) == 2 and ridge:
            T = [(sum(v[0] for v in c) / len(c), sum(v[1] for v in c) / len(c), max(v[2] for v in c)) for c in small]
            if T[0][0] > T[1][0]:
                T.reverse()                                  # west tower first
            gap = math.dist(T[0][:2], T[1][:2])
            ux, uy = (T[1][0] - T[0][0]) / gap, (T[1][1] - T[0][1]) / gap
            nx, ny = uy, -ux
            if nx * (T[0][0] - hall["c"][0]) + ny * (T[0][1] - hall["c"][1]) < 0:
                nx, ny = -nx, -ny                            # the normal looks out of the building, onto the square
            front = max((v[0] - T[0][0]) * nx + (v[1] - T[0][1]) * ny for v in ridge)
            planes = []
            for l in hall["loops"]:
                pts = [V[i] for i in l]
                nrm = sb3d.newell(pts)
                if nrm and nrm[2] > 0.05:
                    planes.append(([p[:2] for p in pts], pts[0], nrm))

            def roof_z(x, y):
                z = 0.0
                for ring, p0, n in planes:
                    if inside((x, y), ring):
                        z = max(z, p0[2] - (n[0] * (x - p0[0]) + n[1] * (y - p0[1])) / n[2])
                return z

            def tower_size(t):     # how far the tower's own high roof reaches along the front and out of it
                hi = [v for v in V if math.dist(v[:2], t[:2]) < 8 and v[2] > top - 9]
                a = [(v[0] - t[0]) * ux + (v[1] - t[1]) * uy for v in hi]
                b = [(v[0] - t[0]) * nx + (v[1] - t[1]) * ny for v in hi]
                return max(a) - min(a), max(b)

            sizes = [tower_size(t) for t in T]
            w = max(6.0, min(12.0, sum(sz[0] for sz in sizes) / 2))
            prof = []
            for k in range(25):                              # the arch: roof height just behind the front, tower to tower
                d = w / 2 + (gap - w) * k / 24
                prof.append(round(roof_z(T[0][0] + ux * d + nx * (front - 0.8), T[0][1] + uy * d + ny * (front - 0.8)), 1))
            sbb_front = {"towers": [[round(t[0] / S, 2), round(-t[1] / S, 2), round(t[2], 1)] for t in T],
                         "front": round(front / S, 2), "out": [round(nx, 4), round(-ny, 4)], "width": round(w / S, 2),
                         "tower_out": [round(sz[1] / S, 2) for sz in sizes], "arch": prof}
    n_verts = sum(len(r["verts"]) for r in recs)
    n_loops = sum(len(r["loops"]) for r in recs)
    n_idx = sum(len(l) for r in recs for l in r["loops"])
    buf, packed = pack(recs)
    _, fpacked = pack(frecs)

    # ---------- landmarks, places, trees, bridges, ferries ----------
    landmarks = {}
    for k, r in lm_rings.items():
        b = obb(r)
        landmarks[k] = {"x": b["cx"] / S, "z": -b["cy"] / S, "len": b["len"] / S, "wid": b["wid"] / S,
                        "ang": b["ang"], "colour": roof_colour([r])}
    for k, hs in lm_heights.items():
        if hs and k in landmarks:
            hs.sort()
            landmarks[k]["roofs"] = [round(hs[int(len(hs) * f)], 1) for f in (0.1, 0.5, 0.9)]
            px_, py_, ph = lm_peak[k]
            landmarks[k]["peak"] = [round(px_ / S, 2), round(-py_ / S, 2), round(ph, 1)]
    if rathaus_front and "rathaus" in landmarks:
        landmarks["rathaus"]["front"] = rathaus_front
    if rathaus_tower and "rathaus" in landmarks:
        landmarks["rathaus"]["tower"] = rathaus_tower
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
    # the Rhine for the game's animated water: aerial pixels of the river's colour connected to points beside the
    # bridges, the bridge decks over the water, and every enclosed hole filled (boats in the photo disappear)
    RW, RH = GW // 2, GH // 2
    cell = lambda x, y: (int((x - W) / 2), int((N - y) / 2))
    cand = bytearray(RW * RH)
    if ref:
        for j in range(RH):
            for i in range(RW):
                c = pixel(*map(int, px(W + (i + 0.5) * 2, N - (j + 0.5) * 2)))
                if (c[0] - ref[0]) ** 2 + (c[1] - ref[1]) ** 2 + (c[2] - ref[2]) ** 2 < 42 * 42:
                    cand[j * RW + i] = 1
    seeds, extra = [], []
    for br in bridges:
        if br["water"]:
            o = br["_obb"]
            for t in (0.3, 0.5, 0.7):
                for side in (-1, 1):
                    seeds.append(cell(*axis(o, br["water"][0] + (br["water"][1] - br["water"][0]) * t, side, o["wid"] / 2 + 20)))
            ring = br["_ring"]
            xs, ys = [p[0] for p in ring], [p[1] for p in ring]
            u = (math.cos(o["ang"]), math.sin(o["ang"]))
            for y in range(int(min(ys)) // 2 * 2, int(max(ys)) + 2, 2):
                for x in range(int(min(xs)) // 2 * 2, int(max(xs)) + 2, 2):
                    t = 0.5 + ((x - o["cx"]) * u[0] + (y - o["cy"]) * u[1]) / o["len"]
                    if br["water"][0] <= t <= br["water"][1] and inside((x, y), ring):
                        i, j = cell(x, y)
                        if 0 <= i < RW and 0 <= j < RH:
                            extra.append(j * RW + i)
    river = river_mask(cand, RW, RH, seeds, extra) if ref else bytearray(RW * RH)
    shores = river_shores(xjs, geo)
    course = rhine_course(raw, geo)
    access = waterside_access(raw, geo, shores)
    for br in bridges:
        del br["_ring"], br["id"], br["_obb"]
    for k, (lat, lon) in {"sbb": (47.54762, 7.58964), "tinguely": (47.55917, 7.61234), "moh": (47.54611, 7.58697),
                          "uni": (47.5582338, 7.5834589), "alte_uni": (47.5588605, 7.5893384)}.items():
        # station building, museum, the Meret Oppenheim Hochhaus, the Kollegienhaus and the Alte Universität on the
        # Rheinsprung, the university's home from 1460 until 1939 (OSM way 137472411)
        x, y = geo.local(lat, lon)
        places[k] = [round(x / S, 2), round(-y / S, 2)]
    for k, (lat, lon) in {"steiger": (47.56725, 7.58377), "bad_stjohann": (47.56509, 7.58403),
                          "bad_breite": (47.55639, 7.61095)}.items():
        # on the Rhine, read off the aerial photo: where the hotel ships moor at St. Johann (the passenger-boat
        # terminal's Steiger 1 and 2), and the Rhine baths at St. Johann and at the Breite
        x, y = geo.local(lat, lon)
        places[k] = [round(x / S, 2), round(-y / S, 2)]
    if "schuetzenmatt" not in places:
        x, y = geo.local(47.55257, 7.57448)
        places["schuetzenmatt"] = [round(x / S, 2), round(-y / S, 2)]
    if "solitude" not in places:
        x, y = geo.local(47.5583, 7.6092)
        places["solitude"] = [round(x / S, 2), round(-y / S, 2)]

    rail = rail_tracks(raw, geo)
    tram_tracks, trams = tram_network(raw, work, geo)
    # where the buildings stand, walls down to the street (a roof on posts high enough for a bus is left out), and
    # the hand-modelled ones: the buses keep off them
    footprints = Footprints([[(r["verts"][i][0], r["verts"][i][1]) for i in l] for r in recs + frecs if r["zbot"] < 3.8
                             for l in r["loops"]] + skip_rings)
    buses, bus_report = bus_network(raw, work, geo, footprints)
    # the Jura, the Black Forest and the Vosges on the horizon, over the city's ground: the game's y = 0, here the
    # median ground height of its buildings
    grounds = sorted(b["ground"] for b in blds if b["ground"] is not None)
    if os.path.isdir(os.path.join(raw, "dem")):
        sky, sky_report = horizon.build(raw, geo.e0, geo.n0, AREA, grounds[len(grounds) // 2])
    else:
        sky, sky_report = None, "no <raw-dir>/dem: run fetch_data.py <raw-dir> dem for the hills on the horizon"
        print(sky_report, flush=True)

    def b64(arr):
        return base64.b64encode(arr.tobytes()).decode()

    data = {
        "S": S, "VS": VS,
        "ground": {"x0": W / S, "x1": E / S, "z0": -N / S, "z1": -Sth / S, "avg": [round(c) for c in avg],
                   "src": "data:image/avif;base64," + ground_b64, "fallback": "data:image/jpeg;base64," + small_b64,
                   "banks": shores, "course": course},
        "lod2": {"count": len(recs), "gz": base64.b64encode(packed).decode()},
        "facades": {"count": len(frecs), "gz": base64.b64encode(fpacked).decode(), "styles": FACADE_STYLES},
        "trees": b64(trees), "treeInfo": b64(tree_info),
        "towers": towers, "landmarks": landmarks, "iconic": dict(iconic, sbb=sbb_front, uni=uni), "places": places, "bridges": bridges, "ferries": ferries,
        "rail": rail, "tracks": rail_lines(raw, geo), "awnings": awnings, "market": market,
        "tramTracks": tram_tracks, "trams": trams, "buses": buses, "access": access, "horizon": sky,
    }
    js = "const BASEL=" + json.dumps(data, separators=(",", ":"), ensure_ascii=False) + ";\n"
    with open(out_js, "w") as f:
        f.write(js)
    print(json.dumps({"buildings": len(recs), "roof_vertices": n_verts, "roof_polygons": n_loops, "loop_indices": n_idx,
                      "walls": [n_walls, n_edges], "facade_buildings": len(frecs),
                      "packed_kB": [len(buf) // 1000, len(packed) // 1000],
                      "trees": {"register": n_register, "osm": len(tree_info) - n_register}, "stats": stats,
                      "bridges": [[b["name"], b["structure"], b["axis"][2] * S, b["water"]] for b in bridges],
                      "ferries": [x["name"] for x in ferries],
                      "shores": {k: len(v) // 2 for k, v in shores.items()}, "course": len(course) // 2,
                      "access": len(access) // 3,
                      "rail": [{k: v for k, v in t.items() if k != "p"} for t in rail], "sbb_front": sbb_front,
                      "uni": uni and {"eaves": len(uni["eaves"]) // 7, "door": uni["door"], "top": uni["top"]},
                      "awnings": len(awnings) // 4, "market": market and len(market["square"]),
                      "trams": [[t["ref"], t["op"], t["colour"], len(t["pats"]), len(t["trips"]) // 3, [[d["name"], len(d["p"]) // 2, len(d["stops"])] for d in t["dirs"]]] for t in trams],
                      "tram_tracks": len(tram_tracks), "buses": bus_report, "horizon": sky_report,
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
