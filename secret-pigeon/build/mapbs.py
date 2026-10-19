"""Read the buildings that mapbs-updates.json takes from Basel-Stadt's own 3D city model (MapBS 3D-Stadtmodell).

The canton delivers the model by email from its Geodaten-Shop (shop.geo.bs.ch, Wavefront OBJ with LV95
georeferencing), so fetch_data.py can't download it: put the delivered GeodatenBS_*.zip archives (or the OBJ files in
them) in <raw-dir>/mapbs. Each building is one object there, named by its material; the same building in several
overlapping deliveries is the same geometry under the same name. Only the buildings the update list names are kept,
in the form sb3d.read_buildings gives swissBUILDINGS3D's: roof polygons in LV95 metres with heights above sea level, the
mean height of the ground outline, the lowest wall point and a swisstopo OBJEKTART.
"""
import hashlib
import json
import math
import os
import zipfile

KIND = "Gebaeude Einzelhaus"    # MapBS doesn't classify its buildings; the build tells high-rises by their height


def _archives(folder):
    zips, objs = [], []
    for d, _, files in os.walk(folder, followlinks=True):
        for f in sorted(files):
            if f.lower().endswith(".zip"):
                zips.append(os.path.join(d, f))
            elif f.lower().endswith(".obj"):
                objs.append(os.path.join(d, f))
    return sorted(zips), sorted(objs)


def _sources(zips, objs):
    """Yield each OBJ as an iterator of text lines: the archives' members, or else the loose files."""
    if zips:
        for zp in zips:
            with zipfile.ZipFile(zp) as z:
                for name in sorted(n for n in z.namelist() if n.lower().endswith(".obj")):
                    with z.open(name) as f:
                        yield (line.decode("utf-8", "replace") for line in f)
    else:
        for p in objs:
            with open(p, encoding="utf-8", errors="replace") as f:
                yield f


def _normal(p):
    ux, uy, uz = (p[1][k] - p[0][k] for k in range(3))
    vx, vy, vz = (p[2][k] - p[0][k] for k in range(3))
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    l = math.sqrt(nx * nx + ny * ny + nz * nz)
    return nz / l if l > 1e-9 else None


def _building(name, faces):
    zmin = min(p[2] for f in faces for p in f)
    roof, ground, walls = [], [], []
    for f in faces:
        nz = _normal(f)
        if nz is None:
            continue
        if nz > 0.08:
            roof.append(f)
        elif nz < -0.9 and sum(p[2] for p in f) / len(f) < zmin + 2.0:     # the floor slab, not an overhang's underside
            ground += [p[2] for p in f]
        elif abs(nz) < 0.08:
            walls += [p[2] for p in f]
    return {"id": "mapbs:" + name, "kind": KIND, "roof": roof,
            "ground": sum(ground) / len(ground) if ground else zmin,
            "wall_min": min(walls) if walls else None}


def read_updates(folder, work, updates_path):
    """The update list's sites as [{"remove": set of swissBUILDINGS3D gml ids, "add": [building, ...]}], in LV95.
    A site whose MapBS buildings aren't all in the archives is left out, so that it keeps swissBUILDINGS3D whole.
    Returns [] without <raw-dir>/mapbs. The buildings read are cached in the work folder."""
    sites = json.load(open(updates_path))["sites"]
    zips, objs = _archives(folder) if os.path.isdir(folder) else ([], [])
    if not zips and not objs:
        print("no MapBS 3D city model in <raw-dir>/mapbs: the buildings stay swissBUILDINGS3D everywhere "
              "(see the README for the free order)", flush=True)
        return []
    want = {n for s in sites for n in s["add"]}
    key = [[os.path.basename(p), os.path.getsize(p)] for p in zips or objs] + \
          [hashlib.sha256(json.dumps(sorted(want)).encode()).hexdigest()]
    cache = os.path.join(work, "mapbs-cache.json")
    found = None
    if os.path.exists(cache):
        c = json.load(open(cache))
        if c.get("key") == key:
            found = c["buildings"]
    if found is None:
        found = {}
        for lines in _sources(zips, objs):
            verts, faces, cur = [None], {}, None
            for line in lines:
                if line.startswith("v "):
                    v = line.split()
                    verts.append((float(v[1]), float(v[2]), float(v[3])))
                elif line.startswith("usemtl"):
                    m = line.split()[1]
                    m = m[:-8] if m.endswith(".Mat_000") else m
                    cur = m if m in want and m not in found else None
                elif cur is not None and line.startswith("f "):
                    faces.setdefault(cur, []).append([verts[int(t.split("/")[0])] for t in line.split()[1:]])
            for name, fs in faces.items():
                found[name] = _building(name, fs)
            if len(found) == len(want):
                break
        with open(cache, "w") as f:
            json.dump({"key": key, "buildings": found}, f, separators=(",", ":"))
    out = []
    for s in sites:
        missing = [n for n in s["add"] if n not in found]
        if missing:
            print(f"MapBS: {len(missing)} of the buildings for the site at {s['at_lv95']} aren't in <raw-dir>/mapbs; "
                  "it keeps swissBUILDINGS3D", flush=True)
            continue
        out.append({"remove": set(s["remove"]), "add": [found[n] for n in s["add"]], "at": s["at_lv95"]})
    return out
