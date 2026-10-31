"""Old-town facade types for Secret Pigeon: which game building gets which facade (30 October 2026).

  python3 -I -B oldtown.py export LIBRARY_FACADES_DIR FACADE_CACHE ROOF_CLASSES OUT.json
      writes oldtown-facades.json from the reference library's facades/resolved.json, records.json and
      areas-lv95.json, the reference world's cache/facade-types.json (wall colours, shared with the Blender world)
      and cache/roof-classes.json (roof classes and tints per LoD2 face). Re-run it whenever those are regenerated;
      nothing here names a building or a side.
  split(recs, frecs, styles, table, origin) -> (keep_l, keep_f, olds, meta, moved, stats)
      used by build_city.py (a full build) and by patch_oldtown.py (a built basel-data.js): the same code path, on
      vertices rounded to 0.1 m as the packs store them. assign() does the matching.

The game build reads only the export (kept in the repository beside this file), never the gitignored library.
"""
import hashlib
import json
import math
import os
import sys

TYPES = ["A1", "A2", "A3", "A4", "H1", "H2", "M1", "M2", "M3", "M4"]      # type code = index + 1; 0: plain wall
GROUND = ["plain", "shopfront", "arcade", "garage"]
SKIP_TYPES = {"church", "stone", "utility"}       # these stay as they are (their own styles / plain blocks)
SHOP_STYLES = {"shops1", "shops2", "shops3", "shops4"}
# shutter colour names (the labellers' words) -> sRGB; an unknown name falls back on its last word, then grey
SHUTTER = {"green": (74, 112, 76), "dark green": (47, 79, 56), "grey-green": (112, 128, 112), "pale green": (140, 168, 138),
           "olive green": (100, 108, 66), "teal": (52, 108, 104), "turquoise": (64, 132, 128), "blue-green": (58, 104, 100),
           "grey": (128, 132, 134), "light grey": (176, 178, 176), "dark grey": (76, 80, 84), "dark": (54, 56, 58),
           "grey-blue": (92, 108, 128), "blue-grey": (98, 110, 126), "dark grey-blue": (62, 72, 88), "light blue": (138, 166, 190),
           "red": (150, 52, 44), "dark red": (110, 40, 36), "red-brown": (122, 64, 48), "dark red-brown": (92, 48, 40),
           "brown-red": (118, 58, 44), "brown": (104, 74, 54), "dark brown": (72, 52, 40), "beige": (200, 184, 152),
           "pale": (214, 208, 194), "white": (232, 230, 224)}
SURROUND = {"sandstone red": (172, 94, 76), "sandstone grey": (186, 180, 168), "painted": (238, 234, 224)}


# the Blender reference world's numbers (build_world.py, facade_colours): linear RGB
TIMBER_LIN = (0.07, 0.045, 0.03)
SURROUND_DEFAULT_LIN = {"A2": (0.40, 0.17, 0.12), "A3": (0.50, 0.48, 0.44)}
SHUTTER_TYPES = {"A1", "A3", "H2"}                # the types that draw shutters where the resolver found them
CLAY = {1, 2}                                     # roof classes (cache/roof-classes.json): clay tile, new or bright tile


def lin(c):
    """sRGB 0-255 -> linear 0-1"""
    return tuple((v / 255) / 12.92 if v / 255 <= 0.04045 else ((v / 255 + 0.055) / 1.055) ** 2.4 for v in c)


def srgb(c):
    """linear 0-1 -> sRGB 0-255 (ints)"""
    return tuple(int(round(255 * min(1.0, max(0.0, v * 12.92 if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055))))
                 for v in c)


def tile_tint(c):
    """Pitched roofs in Basel are clay tiles: warm the reddish ones, and pull grey or tree-covered ones towards terracotta.
    (build_city.py uses this one for every pitched roof of the city.)"""
    r, g, b = c
    if g >= r or max(c) - min(c) < 14:
        r, g, b = r * 0.4 + 156 * 0.6, g * 0.4 + 92 * 0.6, b * 0.4 + 70 * 0.6
    else:
        lum = (r + g + b) / 3
        r, g, b = (lum + (v - lum) * 1.4 for v in (r, g, b))
        r, g, b = r + 10, g - 2, b - 10
    return tuple(int(min(255, max(0, v))) for v in (r, g, b))


def roof_of(faces):
    """(sRGB, m2) of one LoD2 building's classified roof faces, or None: faces with class != 0 and confidence over 0.6
    (labels count as 1.0); the area-weighted mean of tint_srgb in linear light, the clay part (classes 1-2) through
    tile_tint first, the other classes (grey 4, copper 6, flat 7 and 10, planted 8, glass 9) with their own tints"""
    acc, area, clay, clay_a = [0.0, 0.0, 0.0], 0.0, [0.0, 0.0, 0.0], 0.0
    for f in faces:
        conf = 1.0 if f.get("from") == "label" else (f.get("confidence") or 0.0)
        if not f.get("class") or conf <= 0.6 or not f.get("tint_srgb") or not f.get("area"):
            continue
        a, c = float(f["area"]), lin(f["tint_srgb"])
        if f["class"] in CLAY:
            clay = [s + a * v for s, v in zip(clay, c)]
            clay_a += a
        else:
            acc = [s + a * v for s, v in zip(acc, c)]
            area += a
    if clay_a > 0:
        c = lin(tile_tint(srgb([v / clay_a for v in clay])))
        acc = [s + clay_a * v for s, v in zip(acc, c)]
        area += clay_a
    return (srgb([v / area for v in acc]), round(area)) if area > 0 else None


M_TOP = 0.5                                       # the cornice band under the eave line that rows hang from (m)
ST_BAND = (2.6, 4.5)                              # a drawn storey height must lie in this band (m)
EPS = 1e-6                                        # ties in the row rule (rows_of)
ST_GAP = 5.2                                      # A in (4.5, 5.2): no count fits the band; st_draw is clamped (m)
G_LOW = 2.6                                       # the lowest ground floor the row rule leaves (gf_m where it is lower)
BAND_EPS = 1e-6                                   # st_draw_of's band edges (m), facade_types' ROW_EPS


def st_draw_of(cb, storeys, storey_m, gf_m, m_top=M_TOP):
    """the storey height to draw (the shared row rule, the same in the Blender world: rows hang from the eave line).
    On the cache's front wall A = (top - m_top) - base - gf_m. With 2 or more storeys and A / (storeys - 1) in
    [2.6, 4.5] that height (the count fills the front exactly); else the count n >= 1 nearest to A / storey_m whose
    A / n lies in the band; else, where A lies in the gap between one and two storeys (4.5 < A < 5.2 m), clamped:
    2.6 (two upper storeys) where storeys >= 3 and the ground floor gf_m - (5.2 - A) stays at least the house's own
    floor, rows_of's g_min (min(gf_m, 2.6); on an arcade ground floor, the cache's gf 'arcade',
    min(gf_m, max(2.6, gf_m - 1.0))), otherwise 4.5 (one upper storey, the ground floor gf_m + (A - 4.5)); else
    storey_m (facade_types.storey_draw). The band tests allow BAND_EPS, as storey_draw's do, so that an A of 5.2 m
    at float precision is two storeys of 2.6. Each wall's rows then follow rows_of (row rule version 3)"""
    if cb and cb.get("st_draw") is not None:
        return float(cb["st_draw"])
    walls = (cb or {}).get("walls") or []
    fi = (cb or {}).get("front")
    if fi is None or not (0 <= fi < len(walls)):
        return storey_m
    w = walls[fi]
    A = (w["top"] - m_top) - w["base"] - gf_m
    lo, hi = ST_BAND
    band = lambda s: lo - BAND_EPS <= s <= hi + BAND_EPS
    if storeys and storeys >= 2 and band(A / (storeys - 1)):
        return A / (storeys - 1)
    ok = [n for n in range(1, int(A / lo) + 2) if band(A / n)]
    if not ok:
        if hi < A < ST_GAP:
            g_min = min(gf_m, max(G_LOW, gf_m - 1.0)) if cb.get("gf") == "arcade" else min(gf_m, G_LOW)
            two = storeys and storeys >= 3 and gf_m - (ST_GAP - A) >= g_min - EPS
            return lo if two else hi
        return storey_m
    want = A / max(storey_m, 0.5)
    return A / min(ok, key=lambda n: (abs(n - want), n))


def rows_of(H, gf_m, st, arcade=False):
    """(rows, gf_h) of one wall under the shared row rule, version 3 (the same in game.template.html and the Blender
    world's facade_types.rows_hang): H = the wall's own eave line less m_top, over its base. rows 0 where H < gf_m;
    else n_up storeys of st over the ground floor, n_up = H - gf_m over st rounded (halves up), lowered by one while
    the ground floor H - n_up * st would be under g_min = min(gf_m, 2.6): the ground floor is the one nearest gf_m
    that is at least 2.6 m (gf_m where gf_m is lower). On a house whose ground floor is an arcade the arches need
    the height and g_min stays min(gf_m, max(2.6, gf_m - 1.0)) (version 2). gf_h = H - n_up * st. EPS (1e-6 m)
    settles exact ties the same way in Python and in the game (halves round up; a ground floor exactly at the
    bound, or H exactly gf_m, stands)"""
    if H < gf_m - EPS:
        return 0, None
    g_min = min(gf_m, max(G_LOW, gf_m - 1.0)) if arcade else min(gf_m, G_LOW)
    n = int(math.floor((H - gf_m) / st + 0.5 + EPS))
    while n > 0 and H - n * st < g_min - EPS:
        n -= 1
    return 1 + n, H - n * st


def colours_of(r, cb):
    """(wall, shutter, surround) sRGB of one building: the wall from the facade cache's wall_srgb (one colour per drawn
    building, haze-corrected once, shared with the Blender world), else its wall_lin, else the resolved colour;
    shutters and surrounds as the Blender world draws them (facade_colours)"""
    t = r["type"]
    if cb and cb.get("wall_srgb"):
        wall = tuple(cb["wall_srgb"])
    elif cb and cb.get("wall_lin"):
        wall = srgb(cb["wall_lin"])
    else:
        wall = tuple(r["wall_colour"]["rgb"]) if r.get("wall_colour", {}).get("rgb") else None
    wl = lin(wall) if wall else lin((220, 214, 200))
    lighter = tuple(min(1.0, c * 1.15 + 0.03) for c in wl)
    sur = cb.get("surround_lin") if cb else None
    if sur is None and not cb and r.get("surrounds") in SURROUND:
        sur = lin(SURROUND[r["surrounds"]])
    if t == "A4":
        sur = TIMBER_LIN
    elif sur is None:
        sur = SURROUND_DEFAULT_LIN.get(t, lighter)
    elif t == "H2" and (cb or {}).get("surround_name", r.get("surrounds")) == "painted":
        sur = lighter
    shut = None
    if t in SHUTTER_TYPES:
        if cb and cb.get("shutter_lin"):
            shut = srgb(cb["shutter_lin"])
        elif not cb and r["shutters"].get("present") == "yes":
            shut = shutter_rgb(r["shutters"].get("colour"))
    return wall, shut, srgb(sur)


def _sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _hex(c):
    return None if c is None else "%02x%02x%02x" % tuple(int(round(v)) for v in c)


def shutter_rgb(name):
    if not name:
        return SHUTTER["grey"]
    if name in SHUTTER:
        return SHUTTER[name]
    last = name.replace("-", " ").split()[-1]
    return SHUTTER.get(last, SHUTTER["grey"])


def export(lib, cache_path, roof_path, out):
    res = json.load(open(os.path.join(lib, "resolved.json")))
    recs = json.load(open(os.path.join(lib, "records.json")))
    areas = json.load(open(os.path.join(lib, "areas-lv95.json")))
    cache = json.load(open(cache_path))
    roofs = json.load(open(roof_path))
    by_rec = {b["bid_records"]: (name, b) for name, b in cache["buildings"].items()}
    faces = {}
    for f in roofs["faces"]:
        faces.setdefault(f["object"], []).append(f)
    rb = recs["buildings"]
    rb = list(rb.values()) if isinstance(rb, dict) else rb
    rows = []
    for b in sorted(rb, key=lambda b: b["bid"]):
        r = res["buildings"].get(str(b["bid"]))
        if r is None:
            continue
        ce, cn = b["centre_lv95"]
        sides = []
        for s in b["sides"]:
            if s["length_m"] < 3.0:
                continue
            e0, n0, e1, n1 = s["plane_lv95"][:4]
            sides += [round((e0 - ce) * 10), round((n0 - cn) * 10), round((e1 - ce) * 10), round((n1 - cn) * 10)]
        name, cb = by_rec.get(b["bid"], (None, None))
        wall, shut, sur = colours_of(r, cb)
        roof = roof_of(faces.get(name, [])) if name else None
        # storey and ground-floor heights as the Blender world draws them: the cache's gf_m and storey_m where it has
        # the building, the drawn storey height st_draw (rows hang from the eave line, m_top under it)
        gf_m = float(cb["gf_m"]) if cb and cb.get("gf_m") is not None else float(r["gf_m"])
        storey_m = float(cb["storey_m"]) if cb and cb.get("storey_m") is not None else float(r["storey_m"])
        m_top = float(cb.get("m_top", M_TOP)) if cb else M_TOP
        st = st_draw_of(cb, (cb or {}).get("storeys", r.get("storeys")), storey_m, gf_m, m_top)
        rows.append([b["bid"], round(ce, 1), round(cn, 1), r["type"], r["ground_floor"], round(storey_m, 2),
                     round(gf_m, 3), round(st, 3), round(m_top, 2), round(r["bay_m"], 2), round(r.get("eave_m") or 0, 1), _hex(wall),
                     _hex(shut), _hex(sur), round(r.get("confidence") or 0, 2),
                     _hex(roof[0]) if roof else None, roof[1] if roof else 0, sides])
    doc = {"about": "Old-town facade types for Secret Pigeon, exported by oldtown.py from the Basel reference library's "
                    "facades/resolved.json (one decision per building), records.json (LoD2 centres and sides), the "
                    "reference world's cache/facade-types.json (wall colours, shared with the Blender world) and "
                    "cache/roof-classes.json (roof colour per LoD2 building from its classified faces). Colours sRGB "
                    "hex; roof_m2: the classified roof area behind the roof colour; sides: LoD2 wall sides 3 m or "
                    "longer, in decimetres from the centre (e0, n0, e1, n1). Rows hang from the eave line: st_draw is the storey "
                    "height to draw (the cache's front wall filled exactly, see st_draw_of), m_top the band under the "
                    "eave; each game wall's rows and ground floor follow the row rule version 3 (rows_of, "
                    "game.template.html): the ground floor nearest gf_m that is at least min(gf_m, 2.6), on an arcade "
                    "ground floor at least min(gf_m, max(2.6, gf_m - 1.0)).",
           "tool": "secret-pigeon/build/oldtown.py export",
           "inputs": {"resolved.json": res.get("generated_at"), "resolved_sha256": _sha(os.path.join(lib, "resolved.json")),
                      "records_sha256": _sha(os.path.join(lib, "records.json")),
                      "facade_types_sha256": _sha(cache_path), "roof_classes_sha256": _sha(roof_path)},
           "fields": ["bid", "e", "n", "type", "ground", "storey_m", "gf_m", "st_draw", "m_top", "bay_m", "eave_m", "wall", "shutter",
                      "surround", "conf", "roof", "roof_m2", "sides"],
           "areas": {k: [[[round(p[0], 1), round(p[1], 1)] for p in ring] for ring in v["rings"]] for k, v in areas["areas"].items()},
           "buildings": rows}
    with open(out, "w") as f:
        f.write(json.dumps(doc, separators=(",", ":")).replace('],[', '],\n[') + "\n")
    return doc


def load(path):
    doc = json.load(open(path))
    F = doc["fields"]
    doc["by_bid"] = {r[0]: dict(zip(F, r)) for r in doc["buildings"]}
    return doc


def _inside(pt, ring):
    x, y = pt
    c = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            c = not c
        j = i
    return c


def _rgb(h):
    return None if h is None else (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def front_of(b):
    """the game's front record for one building of the export (colours as exported, no game-only correction)"""
    t = b["type"]
    return {"bid": b["bid"], "type": TYPES.index(t) + 1 if t in TYPES else 0,
            "gf": GROUND.index(b["ground"]) if b["ground"] in GROUND else 0,
            "storey": b["st_draw"], "gfh": b["gf_m"], "mtop": b["m_top"], "bay": b["bay_m"], "wall": _rgb(b["wall"]) or (220, 214, 200),
            "shutter": _rgb(b["shutter"]), "surround": _rgb(b["surround"]), "conf": b["conf"]}


def roof_mix(blds):
    """one game roof over several LoD2 buildings: their roof colours, area-weighted in linear light; None if none"""
    got = [(lin(_rgb(b["roof"])), b.get("roof_m2") or 1) for b in blds if b.get("roof")]
    if not got:
        return None
    a = sum(w for _, w in got)
    return srgb([sum(c[i] * w for c, w in got) / a for i in range(3)])


def walled_edges(verts, loops, flags):
    """the record's walls in the order the game draws them: outer edges (in loop order) whose flag is set"""
    directed = {(l[k], l[(k + 1) % len(l)]) for l in loops for k in range(len(l))}
    bnd = [(l[k], l[(k + 1) % len(l)]) for l in loops for k in range(len(l)) if (l[(k + 1) % len(l)], l[k]) not in directed]
    return [e for e, f in zip(bnd, flags) if f]


def assign(recs, table, origin):
    """recs: [{"src": "lod2"|"facades", "kind": int, "style": name|None, "verts": [(x, y, z) m], "loops", "flags"}];
    returns {index: {"fronts": [front], "edge": [front index per walled edge] or None, "roof": sRGB or None}} for the
    records that move into the old-town pack, and stats"""
    e0, n0 = origin
    blds = [b for b in table["by_bid"].values()]
    areas = [ring for rings in table["areas"].values() for ring in rings]
    cell = {}
    for b in blds:
        cell.setdefault((int((b["e"] - e0) // 25), int((b["n"] - n0) // 25)), []).append(b)

    def near(x, y, r):
        out = []
        for i in range(int((x - r) // 25), int((x + r) // 25) + 1):
            for j in range(int((y - r) // 25), int((y + r) // 25) + 1):
                out += cell.get((i, j), [])
        return out

    out, stats = {}, {"in_areas": 0, "one": 0, "none_near": 0, "several": 0, "unmapped": 0, "skipped_type": 0,
                      "walls": 0, "walls_by_side": 0}
    for ri, r in enumerate(recs):
        if not ((r["src"] == "lod2" and r["kind"] in (0, 10)) or (r["src"] == "facades" and r["style"] in SHOP_STYLES)):
            continue
        V = r["verts"]
        cx, cy = sum(p[0] for p in V) / len(V), sum(p[1] for p in V) / len(V)
        if sum(_inside((cx + e0, cy + n0), ring) for ring in areas) % 2 == 0:
            continue
        stats["in_areas"] += 1
        rings = [[(V[i][0], V[i][1]) for i in l] for l in r["loops"]]
        xs, ys = [p[0] for p in V], [p[1] for p in V]
        cand = near(cx, cy, max(max(xs) - min(xs), max(ys) - min(ys)) / 2 + 15)
        inside = [b for b in cand if any(_inside((b["e"] - e0, b["n"] - n0), ring) for ring in rings)]
        if not inside:
            bb = (min(xs) - 3, max(xs) + 3, min(ys) - 3, max(ys) + 3)
            best = [(math.hypot(b["e"] - e0 - cx, b["n"] - n0 - cy), b["bid"], b) for b in cand
                    if bb[0] <= b["e"] - e0 <= bb[1] and bb[2] <= b["n"] - n0 <= bb[3]]
            best = [t for t in best if t[0] <= 12]
            if not best:
                stats["unmapped"] += 1
                continue
            inside = [min(best)[2]]
            stats["none_near"] += 1
        else:
            stats["one" if len(inside) == 1 else "several"] += 1
        inside.sort(key=lambda b: b["bid"])
        walls = walled_edges(V, r["loops"], r["flags"])
        stats["walls"] += len(walls)
        if len(inside) == 1:
            if inside[0]["type"] in SKIP_TYPES:
                stats["skipped_type"] += 1
                continue
            out[ri] = {"fronts": [front_of(inside[0])], "edge": None, "roof": roof_mix(inside)}
            continue
        # several houses under one game roof: each wall takes the house whose LoD2 side it lies on, else the nearest
        sides = []
        for b in inside:
            s = b["sides"]
            for k in range(0, len(s), 4):
                sides.append((b, b["e"] - e0 + s[k] / 10, b["n"] - n0 + s[k + 1] / 10, b["e"] - e0 + s[k + 2] / 10, b["n"] - n0 + s[k + 3] / 10))
        pick = []
        for a, c in walls:
            ax, ay, cx2, cy2 = V[a][0], V[a][1], V[c][0], V[c][1]
            L = math.hypot(cx2 - ax, cy2 - ay)
            got = None
            if L > 0.5:
                ux, uy = (cx2 - ax) / L, (cy2 - ay) / L
                best = None
                for b, sx0, sy0, sx1, sy1 in sides:
                    sl = math.hypot(sx1 - sx0, sy1 - sy0)
                    if sl < 0.5:
                        continue
                    vx, vy = (sx1 - sx0) / sl, (sy1 - sy0) / sl
                    if abs(ux * vx + uy * vy) < math.cos(math.radians(11)):
                        continue
                    d = max(abs((px - sx0) * vy - (py - sy0) * vx) for px, py in ((ax, ay), (cx2, cy2)))
                    if d > 1.5:
                        continue
                    t0, t1 = sorted(((sx0 - ax) * ux + (sy0 - ay) * uy, (sx1 - ax) * ux + (sy1 - ay) * uy))
                    ov = max(0.0, min(L, t1) - max(0.0, t0)) / L
                    if ov >= 0.3 and (best is None or (ov, -d) > best[0]):
                        best = ((ov, -d), b)
                if best:
                    got = best[1]
                    stats["walls_by_side"] += 1
            if got is None:
                mx, my = (ax + cx2) / 2, (ay + cy2) / 2
                got = min(inside, key=lambda b: (math.hypot(b["e"] - e0 - mx, b["n"] - n0 - my), b["bid"]))
            pick.append(got)
        used = sorted({b["bid"] for b in pick if b["type"] not in SKIP_TYPES})
        if not used:
            stats["skipped_type"] += 1
            continue
        by = {b["bid"]: b for b in inside}
        fronts = [front_of(by[bid]) for bid in used]
        plain = None
        idx = []
        for b in pick:
            if b["bid"] in used:
                idx.append(used.index(b["bid"]))
            else:                                   # a church, utility or stone part of a block: a plain wall in its colour
                if plain is None:
                    f = front_of(b)
                    f["type"], f["shutter"], f["surround"] = 0, None, None
                    fronts.append(f)
                    plain = len(fronts) - 1
                idx.append(plain)
        out[ri] = {"fronts": fronts, "edge": idx if len(fronts) > 1 else None, "roof": roof_mix(inside)}
    return out, stats


def meta_bytes(front_list, edge, src, at):
    """one record's meta for the game: src (0 lod2, 1 facades), at (varint: the lod2 records before it), fronts, edges"""
    def varint(buf, v):
        while v >= 128:
            buf.append(v & 127 | 128)
            v >>= 7
        buf.append(v)
    buf = bytearray([src])
    varint(buf, at)
    buf.append(len(front_list))
    dm = lambda v, k=10: max(0, min(255, int(round(v * k))))
    mm = lambda v: max(0, min(65535, int(round(v * 1000)))).to_bytes(2, "little")
    for f in front_list:                            # 19 bytes: the drawn storey and gf_m in mm (two bytes each), m_top in cm
        buf += bytes([f["type"], f["gf"]]) + mm(f["storey"]) + mm(f["gfh"]) + bytes([dm(f["bay"])])
        buf += bytes(f["wall"])
        buf += bytes(f["shutter"] or (0, 0, 0)) + bytes([1 if f["shutter"] else 0])
        buf += bytes(f["surround"] or (0, 0, 0)) + bytes([1 if f["surround"] else 0])
        buf += bytes([dm(f["mtop"], 100)])
    if len(front_list) > 1:
        buf += bytes(edge)
    return buf


def split(recs, frecs, styles, table, origin):
    """the old-town houses out of the game's two packs. recs, frecs: the lod2 and facades records in pack order
    ({"kind", "verts" (m), "loops", "walls" (flags per outer edge)}), styles: the facades pack's style names. Matching
    runs on vertices rounded to 0.1 m, as the packs store them, so a full build and the patcher of a built
    basel-data.js move the same houses. Returns keep_l, keep_f, olds [(record, roof sRGB or None)], meta, moved, stats."""
    dm = lambda V: [(int(round(x * 10)) / 10, int(round(y * 10)) / 10, int(round(z * 10)) / 10) for x, y, z in V]
    cand = [{"src": "lod2", "kind": r["kind"], "style": None, "verts": dm(r["verts"]), "loops": r["loops"],
             "flags": r["walls"]} for r in recs]
    cand += [{"src": "facades", "kind": r["kind"], "style": styles[r["kind"]], "verts": dm(r["verts"]),
              "loops": r["loops"], "flags": r["walls"]} for r in frecs]
    moved, stats = assign(cand, table, origin)
    keep_l, keep_f, olds, meta = [], [], [], bytearray()
    for i, r in enumerate(recs):
        if i in moved:
            olds.append((r, moved[i]["roof"]))
            meta += meta_bytes(moved[i]["fronts"], moved[i]["edge"], 0, len(keep_l))
        else:
            keep_l.append(r)
    for j, r in enumerate(frecs):
        i = len(recs) + j
        if i in moved:
            olds.append((r, moved[i]["roof"]))
            meta += meta_bytes(moved[i]["fronts"], moved[i]["edge"], 1, 0)
        else:
            keep_f.append(r)
    stats["roofs_classified"] = sum(1 for _, c in olds if c)
    return keep_l, keep_f, olds, meta, moved, stats


if __name__ == "__main__":
    if len(sys.argv) == 6 and sys.argv[1] == "export":
        d = export(*sys.argv[2:6])
        print("exported", len(d["buildings"]), "buildings to", sys.argv[5], os.path.getsize(sys.argv[5]), "bytes")
    else:
        raise SystemExit(__doc__)
