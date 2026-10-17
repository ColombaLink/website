"""Turn the canton's reconstructed 1615 city plan into Secret Pigeon's "Basel 1615" data.

Usage: python3 -I secret-pigeon/build/build_1615.py <raw-dir> <work-dir> <out-js> [<preview.bmp>]
  raw-dir   folder written by fetch_data.py and fetch_1615.py (treated as untrusted input)
  work-dir  scratch folder; reuses the aerial tiles that build_city.py converted there
  out-js    JS file that defines `const BASEL1615 = {...}`

The plan draws every house as its own grey shape, separated from its neighbours by thin black lines.
Each connected grey shape is traced into one 3D house at its exact position (0.5 m pixels, Swiss LV95).
Heights and roofs are estimates; the wall follows the canton's outline of the historic core.
Source: Geodaten Kanton Basel-Stadt (Übersichtsplan 1615, historic core), CC BY 4.0.
"""
import base64
import json
import math
import os
import random
import re
import subprocess
import sys
from array import array

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_city import (S, VS, AREA, Geo, ORIGIN_LATLON, read_bmp, write_bmp, area2, centroid,  # noqa: E402
                        inside, rdp, simplify_ring, obb, element_rings, star_shaped, convex, tree_byte, LANDMARKS, IN_1615,
                        river_mask, mask_png)
from fetch_1615 import TRACE, COLS, ROWS, MPP  # noqa: E402

LEVEL = 3.3
ROOFS = ["#9c4a33", "#8f4630", "#a8583c", "#7f4130", "#b0643f", "#94503a", "#7a5546", "#a14d38", "#6e5b52"]
WALLS = ["#efe6d6", "#e8dcc4", "#dccbb0", "#d8c3a4", "#e9e2d3", "#cdbb9f", "#e3d5bd", "#d9c7a8", "#c8b596", "#efe0c4"]
STONE, STONE_ROOF, TOWER_ROOF = "#bda98a", "#8a7a66", "#6f4a3b"


def hexrgb(h):
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def moore_trace(bm, w, h):
    """Outer boundary (pixel centres, clockwise on screen) of the shape in bitmap bm (1 = inside)."""
    start = None
    for i, v in enumerate(bm):
        if v:
            start = (i % w, i // w)
            break
    if start is None:
        return []
    nbr = [(-1, 0), (-1, -1), (0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1)]   # clockwise from west
    def on(x, y):
        return 0 <= x < w and 0 <= y < h and bm[y * w + x]
    pts = [start]
    c, back = start, 0                     # we "came from" the west neighbour
    first_back = None
    for _ in range(4 * (w + h) * 4 + 16):
        found = False
        for k in range(8):
            d = (back + k) % 8
            nx, ny = c[0] + nbr[d][0], c[1] + nbr[d][1]
            if on(nx, ny):
                prev = (back + k - 1) % 8      # last empty neighbour checked
                px, py = c[0] + nbr[prev][0], c[1] + nbr[prev][1]
                c = (nx, ny)
                back = [i for i, o in enumerate(nbr) if (c[0] + o[0], c[1] + o[1]) == (px, py)][0]
                found = True
                break
        if not found:                      # a single pixel
            return [start]
        if c == start:
            if first_back is None:
                first_back = back
            elif back == first_back:
                break
            if len(pts) > 2 and back == first_back:
                break
        pts.append(c)
        if c == start and len(pts) > 2:
            break
    return pts[:-1] if len(pts) > 1 and pts[-1] == pts[0] else pts


def offset_ring(r, d):
    """Grow a ring outward by d (mitred corners, clamped)."""
    if area2(r) < 0:
        r = r[::-1]
    n = len(r)
    out = []
    for i in range(n):
        p0, p1, p2 = r[i - 1], r[i], r[(i + 1) % n]
        e1 = (p1[0] - p0[0], p1[1] - p0[1])
        e2 = (p2[0] - p1[0], p2[1] - p1[1])
        l1, l2 = math.hypot(*e1) or 1e-9, math.hypot(*e2) or 1e-9
        n1 = (e1[1] / l1, -e1[0] / l1)       # outward normals for a CCW ring
        n2 = (e2[1] / l2, -e2[0] / l2)
        bx, by = n1[0] + n2[0], n1[1] + n2[1]
        bl = math.hypot(bx, by)
        if bl < 1e-6:
            out.append((p1[0] + n1[0] * d, p1[1] + n1[1] * d))
            continue
        bx, by = bx / bl, by / bl
        cos_half = bx * n1[0] + by * n1[1]
        m = d / max(cos_half, 0.5)
        out.append((p1[0] + bx * m, p1[1] + by * m))
    return out


def seg_intersections(a, b, ring):
    out = []
    n = len(ring)
    for i in range(n):
        p, q = ring[i], ring[(i + 1) % n]
        d = (b[0] - a[0]) * (q[1] - p[1]) - (b[1] - a[1]) * (q[0] - p[0])
        if abs(d) < 1e-9:
            continue
        t = ((p[0] - a[0]) * (q[1] - p[1]) - (p[1] - a[1]) * (q[0] - p[0])) / d
        u = ((p[0] - a[0]) * (b[1] - a[1]) - (p[1] - a[1]) * (b[0] - a[0])) / d
        if 0 <= t <= 1 and 0 <= u <= 1:
            out.append(((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])), i))
    return out


def main(raw, work, out_js, preview=None):
    rnd = random.Random(1615)
    xjs = json.load(open(os.path.join(raw, "osm_extras.json")))["elements"]
    bjs = json.load(open(os.path.join(raw, "osm_buildings.json")))["elements"]
    ojs = json.load(open(os.path.join(raw, "osm_1615.json")))["elements"]
    origin = ORIGIN_LATLON
    bridge_ring = None
    for el in xjs:
        if el["type"] == "way" and el["id"] == 653605685:
            g = [p for p in el.get("geometry") or [] if p]
            origin = (sum(p["lat"] for p in g) / len(g), sum(p["lon"] for p in g) / len(g))
    geo = Geo(origin)
    for el in xjs:
        if el["type"] == "way" and el["id"] == 653605685:
            bridge_ring = [geo.local(p["lat"], p["lon"]) for p in el["geometry"] if p]
    W_, E_, S_, N_ = AREA
    e0, n0, e1, n1 = TRACE

    # ---------- the plan mosaic ----------
    tiles = {}
    for f in os.listdir(os.path.join(raw, "plan1615")):
        m = re.match(r"tile_(\d+)_(\d+)\.png$", f)
        if m:
            tiles[(int(m.group(1)), int(m.group(2)))] = os.path.join(raw, "plan1615", f)
    tw = th = None
    imgs = {}
    for (c, r), path in tiles.items():
        bmp = os.path.join(work, f"plan_{c}_{r}.bmp")
        if not os.path.exists(bmp):
            subprocess.run(["sips", "-s", "format", "bmp", path, "--out", bmp], check=True, capture_output=True)
        w, h, rgb = read_bmp(bmp)
        tw, th = w, h
        imgs[(c, r)] = rgb
    PW, PH = COLS * tw, ROWS * th
    plan = bytearray(PW * PH * 3)
    for (c, r), rgb in imgs.items():
        for y in range(th):
            o = ((r * th + y) * PW + c * tw) * 3
            plan[o:o + tw * 3] = rgb[y * tw * 3:(y + 1) * tw * 3]
    del imgs
    mpp_x, mpp_y = (e1 - e0) / PW, (n1 - n0) / PH

    def to_local(px, py):            # plan pixel corner -> local metres (east, north of the origin)
        return e0 + px * mpp_x - geo.e0, n1 - py * mpp_y - geo.n0

    def to_px(x, y):
        return (x + geo.e0 - e0) / mpp_x, (n1 - (y + geo.n0)) / mpp_y

    R, G, B = bytes(plan[0::3]), bytes(plan[1::3]), bytes(plan[2::3])

    def band(ch, lo, hi):
        return ch.translate(bytes(1 if lo <= v <= hi else 0 for v in range(256)))

    def AND(a, b):
        return (int.from_bytes(a, "big") & int.from_bytes(b, "big")).to_bytes(len(a), "big")

    fill = AND(AND(band(R, 209, 219), band(G, 209, 219)), band(B, 209, 219))
    water = AND(band(R, 186, 204), band(B, 208, 226))
    dark = band(R, 0, 150)
    del R, G, B
    print("plan", PW, "x", PH, "px", flush=True)

    # ---------- houses: connected grey shapes ----------
    parent = array("i")
    ry, rx0, rx1 = array("i"), array("i"), array("i")

    def find(i):
        root = i
        while parent[root] != root:
            root = parent[root]
        while parent[i] != root:
            parent[i], i = root, parent[i]
        return root

    prev = []
    pat = re.compile(rb"\x01+")
    for y in range(PH):
        cur = []
        for m in pat.finditer(fill, y * PW, (y + 1) * PW):
            i = len(ry)
            ry.append(y)
            rx0.append(m.start() - y * PW)
            rx1.append(m.end() - y * PW)
            parent.append(i)
            cur.append(i)
        j = 0
        for i in cur:
            a0, a1 = rx0[i], rx1[i]
            while j < len(prev) and rx1[prev[j]] <= a0:
                j += 1
            k = j
            while k < len(prev) and rx0[prev[k]] < a1:
                ra, rb = find(i), find(prev[k])
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)
                k += 1
        prev = cur
    comps = {}
    for i in range(len(ry)):
        comps.setdefault(find(i), []).append(i)
    print("runs", len(ry), "shapes", len(comps), flush=True)

    # the game models the Münster, the Rathaus, the surviving gates and the medieval churches itself, so the plan's
    # own shapes inside their outlines are left out
    church_rings, landmark_rings = [], []
    wanted = {LANDMARKS[k] for k in IN_1615}
    for el in bjs:
        t = el.get("tags", {})
        kind = t.get("building")
        if kind in ("church", "cathedral", "chapel") or t.get("amenity") == "place_of_worship":
            outer, _ = element_rings(el, geo)
            church_rings += [r for r in outer if abs(area2(r)) > 150]
        if (el["type"], el["id"]) in wanted:
            outer, _ = element_rings(el, geo)
            landmark_rings += outer

    # ---------- the old city wall (canton's outline of the historic core) and its gates ----------
    ok = json.load(open(os.path.join(raw, "ortskern.json")))
    core = [(x - geo.e0, y - geo.n0) for x, y in ok["features"][0]["geometry"]["coordinates"][0]]
    if core[0] == core[-1]:
        core = core[:-1]
    if area2(core) < 0:
        core = core[::-1]

    def plan_water(x, y):
        px, py = to_px(x, y)
        ix, iy = int(px), int(py)
        if 0 <= ix < PW and 0 <= iy < PH:
            return water[iy * PW + ix] == 1
        return None

    def osm_way_points(name):
        pts = []
        for el in ojs:
            if el["type"] == "way" and el.get("tags", {}).get("name") == name and el.get("tags", {}).get("highway"):
                pts.append([geo.local(p["lat"], p["lon"]) for p in el["geometry"] if p])
        return pts

    markt = (-154.0, -218.0)
    gates = {}
    for el in ojs:
        t = el.get("tags", {})
        if t.get("historic") == "city_gate" and t.get("name") in ("St. Johanns-Tor", "St. Alban-Tor"):
            g = [geo.local(p["lat"], p["lon"]) for p in el.get("geometry") or [] if p]
            if g:
                gates[t["name"]] = centroid(g[:-1] if g[0] == g[-1] else g)
    for name, street in (("Aeschentor", "Aeschenvorstadt"), ("Steinentor", "Steinenvorstadt"),
                         ("Riehentor", "Riehentorstrasse"), ("Bläsitor", "Klybeckstrasse")):
        hits = []
        for line in osm_way_points(street):
            for a, b in zip(line, line[1:]):
                hits += [p for p, _ in seg_intersections(a, b, core)]
        if hits:
            gates[name] = max(hits, key=lambda p: math.hypot(p[0] - markt[0], p[1] - markt[1]))
        else:      # the street stops just short of the wall line: use the wall point nearest its outer end
            ends = [p for line in osm_way_points(street) for p in (line[0], line[-1])]
            if ends:
                far = max(ends, key=lambda p: math.hypot(p[0] - markt[0], p[1] - markt[1]))
                best, bd = None, 1e18
                for i in range(len(core)):
                    a, b = core[i], core[(i + 1) % len(core)]
                    dx, dy = b[0] - a[0], b[1] - a[1]
                    t = max(0, min(1, ((far[0] - a[0]) * dx + (far[1] - a[1]) * dy) / (dx * dx + dy * dy or 1e-9)))
                    q = (a[0] + t * dx, a[1] + t * dy)
                    d = math.hypot(q[0] - far[0], q[1] - far[1])
                    if d < bd:
                        best, bd = q, d
                if bd < 120:
                    gates[name] = best
    if bridge_ring:
        bo = obb(bridge_ring[:-1])
        ends = [(bo["cx"] + s * math.cos(bo["ang"]) * bo["len"] / 2, bo["cy"] + s * math.sin(bo["ang"]) * bo["len"] / 2) for s in (-1, 1)]
        gates["Rheintor"] = min(ends, key=lambda p: math.hypot(p[0] - markt[0], p[1] - markt[1]))

    def wall_dir(p):
        best, bd = 0.0, 1e18
        for i in range(len(core)):
            a, b = core[i], core[(i + 1) % len(core)]
            dx, dy = b[0] - a[0], b[1] - a[1]
            L2 = dx * dx + dy * dy or 1e-9
            t = max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
            d = math.hypot(a[0] + t * dx - p[0], a[1] + t * dy - p[1])
            if d < bd:
                bd, best = d, math.atan2(dy, dx)
        return best

    # ---------- buildings ----------
    recs = []   # (rings, base_m, top_m, roof_h_m, roof_type, roof_rgb, wall_rgb)
    stats = {"houses": 0, "churches": 0, "skipped": 0, "gable": 0, "hip": 0, "flat": 0}
    gate_pts = list(gates.values())
    for root, runs in comps.items():
        n_px = sum(rx1[i] - rx0[i] for i in runs)
        if n_px * mpp_x * mpp_y < 9:
            stats["skipped"] += 1
            continue
        y0 = min(ry[i] for i in runs)
        y1 = max(ry[i] for i in runs) + 1
        x0 = min(rx0[i] for i in runs)
        x1 = max(rx1[i] for i in runs)
        w, h = x1 - x0 + 2, y1 - y0 + 2
        bm = bytearray(w * h)
        for i in runs:
            o = (ry[i] - y0 + 1) * w + 1
            bm[o + rx0[i] - x0:o + rx1[i] - x0] = b"\x01" * (rx1[i] - rx0[i])
        pts = moore_trace(bm, w, h)
        if len(pts) < 3:
            stats["skipped"] += 1
            continue
        ring_px = [(x0 - 1 + px + 0.5, y0 - 1 + py + 0.5) for px, py in pts]
        ring = [to_local(px, py) for px, py in ring_px]
        ring = simplify_ring(ring + [ring[0]], 0.55)
        if len(ring) < 3 or abs(area2(ring)) < 6:
            stats["skipped"] += 1
            continue
        ring = offset_ring(ring, 0.5)          # out to the middle of the plan's dividing lines
        if len(ring) > 3:
            ring = simplify_ring(ring + [ring[0]], 0.3)
        c = centroid(ring)
        if any(inside(c, r) for r in landmark_rings) or any(math.hypot(c[0] - g[0], c[1] - g[1]) < 9 for g in gate_pts):
            stats["skipped"] += 1
            continue
        area_m2 = abs(area2(ring))
        in_walls = inside(c, core)
        d_markt = math.hypot(c[0] - markt[0], c[1] - markt[1])
        church = area_m2 > 150 and any(inside(c, r) for r in church_rings)
        bo = obb(ring)
        short = min(bo["len"], bo["wid"])
        if church:
            wall = 15 + rnd.random() * 4
            rh = min(16.0, max(6.0, 0.7 * short))
            stats["churches"] += 1
        else:
            if not in_walls:
                levels = 1 + (rnd.random() < 0.4)
            elif d_markt < 320:
                levels = 3 + (rnd.random() < 0.55)
            elif d_markt < 700:
                levels = 2 + (rnd.random() < 0.65)
            else:
                levels = 2 + (rnd.random() < 0.35)
            if area_m2 < 25:
                levels = 1
            wall = levels * LEVEL + 0.6
            rh = min(11.0, max(2.5, 0.62 * short))
            stats["houses"] += 1
        if len(ring) == 4 and convex(ring):
            rtype = 2 if (area_m2 > 700 and not church) else 1
            stats["gable" if rtype == 1 else "hip"] += 1
        elif star_shaped(ring):
            rtype = 4
            rh *= 0.75
            stats["hip"] += 1
        else:
            rtype = 0
            wall += rh * 0.3
            rh = 0.0
            stats["flat"] += 1
        hsh = (int(c[0] * 7) * 73856093 ^ int(c[1] * 7) * 19349663) & 0xFFFFFFFF
        wall_c = "#c9b49a" if church else WALLS[hsh % len(WALLS)]
        roof_c = "#7d4a3a" if church else ROOFS[(hsh >> 8) % len(ROOFS)]
        recs.append(([ring], 0.0, wall + rh, rh, rtype, hexrgb(roof_c), hexrgb(wall_c)))

    # wall segments and round towers along the outline, except where the outline runs over the Rhine
    simple = simplify_ring(core + [core[0]], 2.0)
    if area2(simple) < 0:
        simple = simple[::-1]
    keep = []
    for i in range(len(simple)):
        a, b = simple[i], simple[(i + 1) % len(simple)]
        wet = sum(1 for t in (0.2, 0.5, 0.8) if plan_water(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
        keep.append(wet < 2)
    towers = []
    for i in range(len(simple)):
        if not keep[i]:
            continue
        a, b = simple[i], simple[(i + 1) % len(simple)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy)
        if L < 1:
            continue
        nx, ny = dy / L * 1.3, -dx / L * 1.3      # thickness: 2.6 m centred on the line
        ring = [(a[0] - nx, a[1] - ny), (b[0] - nx, b[1] - ny), (b[0] + nx, b[1] + ny), (a[0] + nx, a[1] + ny)]
        recs.append(([ring], 0.0, 9.0, 0.0, 0, hexrgb(STONE_ROOF), hexrgb(STONE)))
        if keep[i - 1]:
            pa, pb = simple[i - 1], a
            turn = abs(math.atan2(dy, dx) - math.atan2(pb[1] - pa[1], pb[0] - pa[0]))
            turn = min(turn, 2 * math.pi - turn)
            if turn > 0.25:
                towers.append(a)
        k = 1
        while k * 85 < L - 30:
            towers.append((a[0] + dx * k * 85 / L, a[1] + dy * k * 85 / L))
            k += 1
    placed = []
    for t in towers:
        if any(math.hypot(t[0] - g[0], t[1] - g[1]) < 28 for g in gate_pts + placed):
            continue
        placed.append(t)
        ring = [(t[0] + 4.3 * math.cos(k * math.pi / 4 + math.pi / 8), t[1] + 4.3 * math.sin(k * math.pi / 4 + math.pi / 8)) for k in range(8)]
        recs.append(([ring], 0.0, 20.0, 6.0, 3, hexrgb(TOWER_ROOF), hexrgb(STONE)))

    # the Rhine bridge (the only one in 1615), its piers and trestles and the Käppeli chapel are built in the game

    # ---------- the ground: the plan, hand-tinted, with the Rhine from the aerial photo beyond its edges ----------
    GW, GH = int(round((E_ - W_) / 2)), int(round((N_ - S_) / 2))
    # where the plan has content
    ys = [y for y in range(0, PH, 8) if dark.find(b"\x01", y * PW, (y + 1) * PW) >= 0]
    cov_y0, cov_y1 = min(ys), max(ys)
    cov_x0 = min(dark.find(b"\x01", y * PW, (y + 1) * PW) - y * PW for y in range(cov_y0, cov_y1, 8) if dark.find(b"\x01", y * PW, (y + 1) * PW) >= 0)
    cov_x1 = max(dark.rfind(b"\x01", y * PW, (y + 1) * PW) - y * PW for y in range(cov_y0, cov_y1, 8) if dark.rfind(b"\x01", y * PW, (y + 1) * PW) >= 0)
    # inside-the-walls mask by scanline
    inside_rows = []
    for gy in range(GH):
        yy = N_ - (gy + 0.5) * 2
        xs = []
        for i in range(len(core)):
            a, b = core[i], core[(i + 1) % len(core)]
            if (a[1] > yy) != (b[1] > yy):
                xs.append(a[0] + (yy - a[1]) * (b[0] - a[0]) / (b[1] - a[1]))
        xs.sort()
        inside_rows.append([(int((xs[k] - W_) / 2), int((xs[k + 1] - W_) / 2)) for k in range(0, len(xs) - 1, 2)])
    # aerial mosaic for the river outside the plan
    aerial, AT = {}, None
    for f in os.listdir(work):
        m = re.match(r"ortho_(\d{4})-(\d{4})\.bmp$", f)
        if m:
            w, h, rgb = read_bmp(os.path.join(work, f))
            AT = w
            aerial[(int(m.group(1)), int(m.group(2)))] = rgb
    if not aerial:
        raise SystemExit("no aerial tiles in the work folder: run build_city.py first")

    def aerial_px(x, y):
        E, N = geo.e0 + x, geo.n0 + y
        te, tn = int(E // 1000), int(N // 1000)
        img = aerial.get((te, tn))
        if img is None:
            return None
        ix, iy = int((E - te * 1000) * AT / 1000), int(((tn + 1) * 1000 - N) * AT / 1000)
        i = (min(AT - 1, iy) * AT + min(AT - 1, ix)) * 3
        return img[i], img[i + 1], img[i + 2]

    cls = bytearray(GW * GH)      # 0 meadow/street, 1 line, 2 house, 3 water, 9 outside the plan
    outside = bytearray(GW * GH)  # 1 where the plan has no content
    for gy in range(GH):
        for gx in range(GW):
            x, y = W_ + (gx + 0.5) * 2, N_ - (gy + 0.5) * 2
            px, py = to_px(x, y)
            ix, iy = int(px) - 2, int(py) - 2
            if not (cov_x0 + 4 <= ix and ix + 4 <= cov_x1 - 4 and cov_y0 + 4 <= iy and iy + 4 <= cov_y1 - 4):
                cls[gy * GW + gx] = 9
                outside[gy * GW + gx] = 1
                continue
            nd = nf = nw = 0
            for yy in range(iy, iy + 4):
                o = yy * PW + ix
                nd += dark[o:o + 4].count(1)
                nf += fill[o:o + 4].count(1)
                nw += water[o:o + 4].count(1)
            cls[gy * GW + gx] = 1 if nd else 2 if nf >= 6 else 3 if nw >= 8 else 0
    # river colour statistics from plan water, then grow it outside the plan through similar aerial pixels
    samp = []
    for gy in range(0, GH, 3):
        for gx in range(0, GW, 3):
            if cls[gy * GW + gx] == 3:
                a = aerial_px(W_ + (gx + 0.5) * 2, N_ - (gy + 0.5) * 2)
                if a:
                    samp.append(a)
    mean = [sum(s[k] for s in samp) / len(samp) for k in range(3)]
    sd = [max(6, (sum((s[k] - mean[k]) ** 2 for s in samp) / len(samp)) ** 0.5) for k in range(3)]
    # continue the Rhine beyond the plan: follow its banks outward line by line through water-coloured
    # aerial pixels, keeping the band solid and letting each bank move only a little per step
    def smooth(x2, y2):
        vals = []
        for dx, dy in ((0, 0), (2, 0), (-2, 0), (0, 2), (0, -2)):
            a = aerial_px(W_ + (x2 + dx + 0.5) * 2, N_ - (y2 + dy + 0.5) * 2)
            if a is None:
                return False
            vals.append(sum(a))
        return max(vals) - min(vals) < 70

    def waterish(x2, y2):
        a = aerial_px(W_ + (x2 + 0.5) * 2, N_ - (y2 + 0.5) * 2)
        return bool(a) and all(abs(a[k] - mean[k]) < 2.2 * sd[k] for k in range(3)) and smooth(x2, y2)

    inside_cells = [i for i in range(GW * GH) if not outside[i]]
    gx_min, gx_max = min(i % GW for i in inside_cells), max(i % GW for i in inside_cells)
    gy_min, gy_max = min(i // GW for i in inside_cells), max(i // GW for i in inside_cells)
    grown = 0
    for side in "NSEW":
        horizontal = side in "NS"                    # the river crosses a top/bottom edge: walk rows
        edge_line = {"N": gy_min, "S": gy_max, "W": gx_min, "E": gx_max}[side]
        steps = {"N": range(gy_min - 1, -1, -1), "S": range(gy_max + 1, GH),
                 "W": range(gx_min - 1, -1, -1), "E": range(gx_max + 1, GW)}[side]
        extent = GW if horizontal else GH
        at = (lambda u, v: cls[v * GW + u]) if horizontal else (lambda u, v: cls[u * GW + v])
        runs, u = [], 0
        while u < extent:
            if at(u, edge_line) == 3:
                u0 = u
                while u < extent and at(u, edge_line) == 3:
                    u += 1
                if u - u0 >= 25:
                    runs.append((u0, u - 1))
            u += 1
        for a, b in runs:
            width = b - a
            for v in steps:
                lo, hi = max(0, a - 15), min(extent - 1, b + 15)
                flags = [waterish(uu, v) if horizontal else waterish(v, uu) for uu in range(lo, hi + 1)]
                best, cur = None, None
                gap = 0
                for k, f in enumerate(flags + [False] * 5):
                    if f:
                        cur = (cur[0], k) if cur else (k, k)
                        gap = 0
                    elif cur:
                        gap += 1
                        if gap > 4:
                            ov = min(cur[1] + lo, b) - max(cur[0] + lo, a)
                            if ov > 0 and (best is None or ov > best[2]):
                                best = (cur[0] + lo, cur[1] + lo, ov)
                            cur, gap = None, 0
                if best and best[1] - best[0] >= 0.5 * width:
                    a = max(a - 2, min(a + 2, best[0]))
                    b = max(b - 2, min(b + 2, best[1]))
                for uu in range(a, b + 1):
                    i = (v * GW + uu) if horizontal else (uu * GW + v)
                    if cls[i] == 9:
                        cls[i] = 3
                        grown += 1
    print("river pixels grown from the aerial photo:", grown, flush=True)

    # the Rhine for the game's animated water: the plan's water connected to points beside the bridge
    seeds = []
    if bridge_ring:
        bo = obb(bridge_ring[:-1])
        u, v = (math.cos(bo["ang"]), math.sin(bo["ang"])), (-math.sin(bo["ang"]), math.cos(bo["ang"]))
        for t in (-0.25, 0.0, 0.25):
            for side in (-1, 1):
                x = bo["cx"] + u[0] * t * bo["len"] + v[0] * side * (bo["wid"] / 2 + 25)
                y = bo["cy"] + u[1] * t * bo["len"] + v[1] * side * (bo["wid"] / 2 + 25)
                seeds.append((int((x - W_) / 2), int((N_ - y) / 2)))
    river = river_mask(cls.translate(bytes([1 if k == 3 else 0 for k in range(256)])), GW, GH, seeds)
    water_png = mask_png(river, GW, GH, work, "water-1615")

    # ---------- paint the ground: the plan's own pixels (0.5 m), recoloured by what they show, on a canvas over
    # the whole map with the Rhine beyond the plan from the classes above; sips then scales it to 1 m, which
    # smooths every edge ----------
    COL = {"street": hexrgb("#d6c8aa"), "meadow": hexrgb("#b3c08a"), "line_in": hexrgb("#5a4a3b"),
           "line_out": hexrgb("#86946a"), "house": hexrgb("#7a6a5a"), "water": hexrgb("#6e9db4"), "moat": hexrgb("#7f9663")}
    HM = mpp_x                                              # canvas metres per pixel: the plan's own
    CW, CH = int(round((E_ - W_) / HM)), int(round((N_ - S_) / HM))
    OX = int(round((W_ + geo.e0 - e0) / mpp_x))            # plan column of canvas column 0
    OY = int(round((n1 - (N_ + geo.n0)) / mpp_y))          # plan row of canvas row 0
    canvas = bytearray(bytes(COL["meadow"]) * (CW * CH))
    tables = []
    for ch in range(3):          # a pixel code is inside-walls + 2 house + 4 water + 8 line; lines win, then houses
        t = bytearray(256)
        for v in range(16):
            inn = v & 1
            key = ("line_in" if inn else "line_out") if v & 8 else "house" if v & 2 else "water" if v & 4 else \
                "street" if inn else "meadow"
            t[v] = COL[key][ch]
        tables.append(bytes(t))
    pc0, pc1 = max(cov_x0 + 4, OX), min(cov_x1 - 4, OX + CW)
    for prow in range(max(cov_y0 + 4, OY), min(cov_y1 - 4, OY + CH)):
        n = pc1 - pc0
        if n <= 0:
            break
        yy = n1 - (prow + 0.5) * mpp_y - geo.n0
        inn = bytearray(n)
        xs = sorted(a[0] + (yy - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
                    for a, b in zip(core, core[1:] + core[:1]) if (a[1] > yy) != (b[1] > yy))
        for k in range(0, len(xs) - 1, 2):
            c0 = max(0, int((xs[k] + geo.e0 - e0) / mpp_x) - pc0)
            c1 = min(n, int((xs[k + 1] + geo.e0 - e0) / mpp_x) - pc0)
            if c1 > c0:
                inn[c0:c1] = b"\x01" * (c1 - c0)
        o = prow * PW
        code = (int.from_bytes(inn, "big") + (int.from_bytes(fill[o + pc0:o + pc1], "big") << 1)
                + (int.from_bytes(water[o + pc0:o + pc1], "big") << 2)
                + (int.from_bytes(dark[o + pc0:o + pc1], "big") << 3)).to_bytes(n, "big")
        seg = bytearray(n * 3)
        for ch in range(3):
            seg[ch::3] = code.translate(tables[ch])
        q = ((prow - OY) * CW + (pc0 - OX)) * 3
        canvas[q:q + n * 3] = seg
    cell = int(round(2 / HM))     # the Rhine grown beyond the plan, in 2 m cells
    water_px = bytes(COL["water"]) * cell
    for gy in range(GH):
        for gx in range(GW):
            i = gy * GW + gx
            if outside[i] and cls[i] == 3:
                for r in range(cell):
                    q = ((gy * cell + r) * CW + gx * cell) * 3
                    if q + cell * 3 <= len(canvas):
                        canvas[q:q + cell * 3] = water_px
    for i in range(len(simple)):  # the moat: a band just outside the wall
        if not keep[i]:
            continue
        a, b = simple[i], simple[(i + 1) % len(simple)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy) or 1
        nx, ny = dy / L, -dx / L
        quad = [(a[0] + nx * 3, a[1] + ny * 3), (b[0] + nx * 3, b[1] + ny * 3), (b[0] + nx * 16, b[1] + ny * 16), (a[0] + nx * 16, a[1] + ny * 16)]
        cq = [((p[0] - W_) / HM, (N_ - p[1]) / HM) for p in quad]
        for cy in range(max(0, int(min(p[1] for p in cq))), min(CH, int(max(p[1] for p in cq)) + 1)):
            for cx in range(max(0, int(min(p[0] for p in cq))), min(CW, int(max(p[0] for p in cq)) + 1)):
                if inside((cx + 0.5, cy + 0.5), cq) and cls[min(GH - 1, int(cy * HM / 2)) * GW + min(GW - 1, int(cx * HM / 2))] != 3:
                    q = (cy * CW + cx) * 3
                    canvas[q:q + 3] = bytes(COL["moat"])
    cbmp, gbmp = os.path.join(work, "ground1615-canvas.bmp"), os.path.join(work, "ground1615.bmp")
    write_bmp(cbmp, CW, CH, canvas)
    del canvas
    IW, IH = int(round((E_ - W_) / 1.0)), int(round((N_ - S_) / 1.0))
    subprocess.run(["sips", "-z", str(IH), str(IW), cbmp, "--out", gbmp], check=True, capture_output=True)
    _, _, img = read_bmp(gbmp)
    avg = [sum(img[i::3][::97]) / len(img[i::3][::97]) for i in range(3)]
    F = 96
    for y in range(IH):
        for x in (list(range(min(F, IW))) + list(range(max(F, IW - F), IW))) if F <= y < IH - F else range(IW):
            d = min(x, y, IW - 1 - x, IH - 1 - y)
            if d >= F:
                continue
            t = d / F
            t = t * t * (3 - 2 * t)
            o = (y * IW + x) * 3
            for c in range(3):
                img[o + c] = int(avg[c] * (1 - t) + img[o + c] * t)
    write_bmp(gbmp, IW, IH, img)
    gavif, gsmall = os.path.join(work, "ground1615.avif"), os.path.join(work, "ground1615-small.jpg")
    subprocess.run(["sips", "-s", "format", "avif", "-s", "formatOptions", "50", gbmp, "--out", gavif], check=True, capture_output=True)
    subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "70", "-z", str(IH // 4), str(IW // 4), gbmp,
                    "--out", gsmall], check=True, capture_output=True)
    ground_b64 = base64.b64encode(open(gavif, "rb").read()).decode()
    small_b64 = base64.b64encode(open(gsmall, "rb").read()).decode()

    # ---------- trees: gardens inside the walls, orchards outside ----------
    # The plan draws the Rhine bridge in light grey, which reads as open ground, so keep its deck
    # (and a few metres around it) free of trees.
    def near_bridge(x, y, d=8.0):
        if not bridge_ring:
            return False
        if inside((x, y), bridge_ring):
            return True
        for i in range(len(bridge_ring) - 1):
            a, b = bridge_ring[i], bridge_ring[i + 1]
            dx, dy = b[0] - a[0], b[1] - a[1]
            t = max(0, min(1, ((x - a[0]) * dx + (y - a[1]) * dy) / (dx * dx + dy * dy or 1e-9)))
            if math.hypot(a[0] + t * dx - x, a[1] + t * dy - y) < d:
                return True
        return False

    trees, tree_info = array("h"), array("B")       # fruit and garden trees: round crowns
    for gy in range(2, GH - 2, 3):
        for gx in range(2, GW - 2, 3):
            if cls[gy * GW + gx] not in (0, 9):
                continue
            clear = all(cls[(gy + dy) * GW + gx + dx] in (0, 9) for dy in (-2, -1, 0, 1, 2) for dx in (-2, -1, 0, 1, 2))
            if not clear:
                continue
            inn = any(a <= gx < b for a, b in inside_rows[gy])
            p = 0.3 if inn else (0.045 if cls[gy * GW + gx] == 0 else 0.012)
            if rnd.random() < p:
                x = W_ + (gx + 0.5 + rnd.random() * 2 - 1) * 2
                y = N_ - (gy + 0.5 + rnd.random() * 2 - 1) * 2
                if near_bridge(x, y):
                    continue
                trees.append(int(round(x * 10)))
                trees.append(int(round(y * 10)))
                tree_info.append(tree_byte(0, 0.75 + rnd.random() * 0.6))

    # ---------- encode ----------
    rings_per, ring_len, verts = array("B"), array("H"), array("h")
    base_dm, top_dm, roof_dm, roof_type, roof_rgb, wall_rgb = array("H"), array("H"), array("H"), array("B"), array("B"), array("B")
    for rings, base, top, rh, rtype, rc, wc in recs:
        rings_per.append(len(rings))
        for r in rings:
            ring_len.append(len(r))
            for x, y in r:
                verts.append(int(round(x * 10)))
                verts.append(int(round(y * 10)))
        base_dm.append(int(round(base * 10)))
        top_dm.append(int(round(top * 10)))
        roof_dm.append(int(round(rh * 10)))
        roof_type.append(rtype)
        roof_rgb.extend(rc)
        wall_rgb.extend(wc)

    def b64(a):
        return base64.b64encode(a.tobytes()).decode()

    def g(p):
        return [round(p[0] / S, 2), round(-p[1] / S, 2)]

    gate_list = [{"name": k, "x": g(p)[0], "z": g(p)[1], "ang": wall_dir(p), "h": 30 if k == "St. Alban-Tor" else 26}
                 for k, p in gates.items()]
    places = {}
    for el in ojs:
        t = el.get("tags", {})
        if t.get("name") == "Basler Papiermühle Museum":
            pts = [geo.local(p["lat"], p["lon"]) for p in el.get("geometry") or [] if p]
            places["papermill"] = g(centroid(pts[:-1]))
    data = {"count": len(rings_per), "ringsPer": b64(rings_per), "ringLen": b64(ring_len), "verts": b64(verts),
            "base": b64(base_dm), "top": b64(top_dm), "roofH": b64(roof_dm), "roofType": b64(roof_type),
            "roofRGB": b64(roof_rgb), "wallRGB": b64(wall_rgb), "trees": b64(trees), "treeInfo": b64(tree_info), "gates": gate_list,
            "places": places, "ground": {"avg": [round(c) for c in avg], "water": water_png, "src": "data:image/avif;base64," + ground_b64,
                                         "fallback": "data:image/jpeg;base64," + small_b64}}
    js = "const BASEL1615=" + json.dumps(data, separators=(",", ":"), ensure_ascii=False) + ";\n"
    with open(out_js, "w") as f:
        f.write(js)
    print(json.dumps({"buildings": len(rings_per), "vertices": len(verts) // 2, "trees": len(trees) // 2, "stats": stats,
                      "towers": len(placed), "wall_segments": sum(keep), "gates": {k["name"]: (k["x"], k["z"]) for k in gate_list},
                      "places": places, "ground_kB": [len(ground_b64) * 3 // 4 // 1000, len(small_b64) * 3 // 4 // 1000],
                      "js_kB": len(js) // 1000},
                     ensure_ascii=False, indent=1))
    if preview:      # a quick check image: the ground with the traced outlines drawn over it
        pv = bytearray(img)
        for rings, *_ in recs:
            for x, y in rings[0]:
                ix, iy = int(x - W_), int(N_ - y)
                if 0 <= ix < IW and 0 <= iy < IH:
                    o = (iy * IW + ix) * 3
                    pv[o:o + 3] = b"\xff\x20\x20"
        write_bmp(preview, IW, IH, pv)


if __name__ == "__main__":
    if len(sys.argv) not in (4, 5):
        raise SystemExit(__doc__)
    main(*sys.argv[1:])
