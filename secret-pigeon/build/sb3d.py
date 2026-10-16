"""Read swisstopo's swissBUILDINGS3D 3.0 CityGML tiles and boil each building down to its roof.

The roofs come as triangles. Coplanar neighbours are merged back into one polygon per roof plane and vertices on
straight edges are dropped, so a gable house is a handful of polygons. Each building keeps its roof polygons
(vertex loops, counter-clockwise seen from above, heights relative to the building's ground), the height its walls
start at (0 for houses, higher for canopies and covered bridges) and its kind (swisstopo's OBJEKTART).
The game rebuilds the walls by dropping the roof's outer edges to the ground.
"""
import math
import zipfile
import xml.etree.ElementTree as ET

B = "{http://www.opengis.net/citygml/building/2.0}"
G = "{http://www.opengis.net/gml}"
GEN = "{http://www.opengis.net/citygml/generics/2.0}"

PLANE_COS = math.cos(math.radians(4.0))   # neighbouring roof triangles this close in angle are one plane
STRAIGHT = 0.25                           # metres: a vertex this close to the line through its neighbours goes


def _points(text):
    v = text.split()
    pts = [(float(v[i]), float(v[i + 1]), float(v[i + 2])) for i in range(0, len(v) - 2, 3)]
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts.pop()
    return pts


def read_buildings(zip_paths, keep):
    """Yield dicts {id, kind, roof: [[(e, n, z), ...], ...], ground, wall_min} for each building whose roof centre
    passes keep(e, n). Coordinates are LV95 metres, heights metres above sea level; ground is the mean height of
    the ground floor outline (None when the model has none, as for canopies), wall_min the lowest wall point."""
    seen = set()
    for zp in zip_paths:
        with zipfile.ZipFile(zp) as z:
            name = next(n for n in z.namelist() if n.endswith(".gml"))
            with z.open(name) as f:
                root = None
                for ev, el in ET.iterparse(f, events=("start", "end")):
                    if ev == "start":
                        if root is None:
                            root = el
                        continue
                    if el.tag != B + "Building":
                        continue
                    bid = el.get(G + "id")
                    roof = []
                    for s in el.iter(B + "RoofSurface"):
                        for pl in s.iter(G + "posList"):
                            pts = _points(pl.text)
                            if len(pts) >= 3:
                                roof.append(pts)
                    if roof and bid not in seen:
                        e = sum(p[0] for r in roof for p in r) / sum(len(r) for r in roof)
                        n = sum(p[1] for r in roof for p in r) / sum(len(r) for r in roof)
                        if keep(e, n):
                            seen.add(bid)
                            kind = None
                            for a in el.iter(GEN + "stringAttribute"):
                                if a.get("name") == "OBJEKTART":
                                    kind = a.findtext(GEN + "value")
                                    break
                            ground = [p[2] for s in el.iter(B + "GroundSurface") for pl in s.iter(G + "posList") for p in _points(pl.text)]
                            walls = [p[2] for s in el.iter(B + "WallSurface") for pl in s.iter(G + "posList") for p in _points(pl.text)]
                            yield {"id": bid, "kind": kind, "roof": roof,
                                   "ground": sum(ground) / len(ground) if ground else None,
                                   "wall_min": min(walls) if walls else None}
                    el.clear()
                    root.clear()


def newell(pts):
    nx = ny = nz = 0.0
    for i in range(len(pts)):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        nx += (a[1] - b[1]) * (a[2] + b[2])
        ny += (a[2] - b[2]) * (a[0] + b[0])
        nz += (a[0] - b[0]) * (a[1] + b[1])
    l = math.sqrt(nx * nx + ny * ny + nz * nz)
    return (nx / l, ny / l, nz / l) if l > 1e-9 else None


def _seg_dist(p, a, b):
    d = [b[k] - a[k] for k in range(3)]
    L2 = sum(c * c for c in d)
    if L2 < 1e-12:
        return math.dist(p, a), 0.0
    t = sum((p[k] - a[k]) * d[k] for k in range(3)) / L2
    q = [a[k] + t * d[k] for k in range(3)]
    return math.dist(p, q), t


def simplify_roof(faces):
    """faces: roof polygons as lists of (x, y, z) metres. Returns (verts, loops): verts as (x, y, z) tuples
    snapped to 0.1 m, loops as lists of indices into verts, one per roof plane, counter-clockwise from above."""
    index, verts = {}, []

    def vid(p):
        k = (round(p[0] * 10), round(p[1] * 10), round(p[2] * 10))
        i = index.get(k)
        if i is None:
            i = index[k] = len(verts)
            verts.append((k[0] / 10, k[1] / 10, k[2] / 10))
        return i

    polys = []
    for f in faces:
        loop = []
        for p in f:
            i = vid(p)
            if not loop or loop[-1] != i:
                loop.append(i)
        while len(loop) > 1 and loop[0] == loop[-1]:
            loop.pop()
        if len(loop) < 3 or len(set(loop)) != len(loop):
            continue
        nrm = newell([verts[i] for i in loop])
        if nrm is None or abs(nrm[2]) < 0.08:          # degenerate or a vertical sliver
            continue
        if nrm[2] < 0:
            loop.reverse()
            nrm = (-nrm[0], -nrm[1], -nrm[2])
        polys.append((loop, nrm))

    # merge coplanar neighbours into planes
    edge_faces = {}
    for fi, (loop, _) in enumerate(polys):
        for k in range(len(loop)):
            a, b = loop[k], loop[(k + 1) % len(loop)]
            edge_faces.setdefault((min(a, b), max(a, b)), []).append(fi)
    region = [-1] * len(polys)
    regions = []
    for fi in range(len(polys)):
        if region[fi] >= 0:
            continue
        rid = len(regions)
        region[fi] = rid
        members, stack = [fi], [fi]
        n0 = polys[fi][1]
        while stack:
            cur = stack.pop()
            loop = polys[cur][0]
            for k in range(len(loop)):
                a, b = loop[k], loop[(k + 1) % len(loop)]
                for nb in edge_faces[(min(a, b), max(a, b))]:
                    if region[nb] < 0:
                        n1 = polys[nb][1]
                        if n0[0] * n1[0] + n0[1] * n1[1] + n0[2] * n1[2] >= PLANE_COS:
                            region[nb] = rid
                            members.append(nb)
                            stack.append(nb)
        regions.append(members)

    loops = []
    for members in regions:
        if len(members) == 1:
            loops.append(polys[members[0]][0])
            continue
        directed = set()
        for fi in members:
            loop = polys[fi][0]
            for k in range(len(loop)):
                directed.add((loop[k], loop[(k + 1) % len(loop)]))
        nxt = {}
        ok = True
        for a, b in directed:
            if (b, a) in directed:
                continue
            if a in nxt:             # a pinch point: keep the triangles as they are
                ok = False
                break
            nxt[a] = b
        chains = []
        if ok:
            left = dict(nxt)
            while left:
                start = next(iter(left))
                chain, cur = [start], left.pop(start)
                while cur != start and cur in left:
                    chain.append(cur)
                    cur = left.pop(cur)
                if cur != start:
                    ok = False
                    break
                chains.append(chain)
        if ok and len(chains) == 1:
            loops.append(chains[0])
        else:                        # holes or odd shapes: the original triangles
            loops += [polys[fi][0] for fi in members]

    # drop vertices that sit on a straight line between their only two neighbours (no cracks: they go everywhere)
    for _ in range(4):
        nbrs = {}
        for loop in loops:
            for k in range(len(loop)):
                a, b = loop[k], loop[(k + 1) % len(loop)]
                nbrs.setdefault(a, set()).add(b)
                nbrs.setdefault(b, set()).add(a)
        drop = set()
        for v, ns in nbrs.items():
            if len(ns) == 2:
                u, w = ns
                if u in drop or w in drop:
                    continue
                d, t = _seg_dist(verts[v], verts[u], verts[w])
                if d < STRAIGHT and 0 < t < 1:
                    drop.add(v)
        if not drop:
            break
        loops = [[i for i in loop if i not in drop] for loop in loops]
        loops = [l for l in loops if len(l) >= 3]

    loops = _close_seams(verts, loops)
    remap = {}                       # number the vertices in the order the loops visit them (small deltas)
    for loop in loops:
        for i in loop:
            if i not in remap:
                remap[i] = len(remap)
    out = [None] * len(remap)
    for old, new in remap.items():
        out[new] = verts[old]
    return out, [[remap[i] for i in l] for l in loops]


def _close_seams(verts, loops, tol=0.06):
    """Where one roof plane's corner sits in the middle of a neighbouring plane's edge, add that corner to the edge
    too, so both planes share the seam exactly and the game knows no wall belongs there."""
    used = sorted({i for l in loops for i in l})
    cell = {}
    for i in used:
        x, y, _ = verts[i]
        cell.setdefault((int(math.floor(x)), int(math.floor(y))), []).append(i)
    out = []
    for loop in loops:
        new = []
        m = len(loop)
        for k in range(m):
            a, b = loop[k], loop[(k + 1) % m]
            new.append(a)
            pa, pb = verts[a], verts[b]
            x0, x1 = sorted((pa[0], pb[0]))
            y0, y1 = sorted((pa[1], pb[1]))
            hits = []
            for cx in range(int(math.floor(x0 - tol)), int(math.floor(x1 + tol)) + 1):
                for cy in range(int(math.floor(y0 - tol)), int(math.floor(y1 + tol)) + 1):
                    for v in cell.get((cx, cy), ()):
                        if v == a or v == b:
                            continue
                        d, t = _seg_dist(verts[v], pa, pb)
                        if d < tol and 0.001 < t < 0.999:
                            hits.append((t, v))
            hits.sort()
            new += [v for _, v in hits if v not in new]
        out.append(new)
    return out
