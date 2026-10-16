"""Download the raw map data for Secret Pigeon's Basel.

Usage: python3 -I secret-pigeon/build/fetch_data.py <output-dir> [osm] [ortho] [sb3d] [trees]
  (no part names: all of them)

Writes into <output-dir>:
  osm_buildings.json   buildings and building parts (OpenStreetMap, ODbL) via the Swiss Overpass mirror; used for
                       the landmarks and the facade buildings
  osm_extras.json      trees, bridges, ferries, water, parks and a few named places
  ortho/E-N.jpg        swisstopo SWISSIMAGE at 1 m per pixel, one file per LV95 kilometre (WMS)
  sb3d/*.citygml.zip   swisstopo swissBUILDINGS3D 3.0 (CityGML, LOD2 roofs), newest release of each map sheet
  trees_bs.json        Basel's public tree register (species and age), Geodaten Kanton Basel-Stadt
"""
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_city import lv95  # noqa: E402

BBOX = (47.5440, 7.5690, 47.5735, 7.6170)  # south, west, north, east
UA = "SecretPigeonGame/0.1 (educational game; https://colomba.link, info@colomba.link)"
OVERPASS = "https://overpass.osm.ch/api/interpreter"
STAC = "https://data.geo.admin.ch/api/stac/v0.9/collections"
WMS = "https://wms.geo.admin.ch/"
ORTHO_PX = 1000            # pixels per kilometre tile: 1 m


def get(url, data=None, tries=5):
    wait = 4
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=600) as r:
                return r.read()
        except Exception as e:  # network hiccup or server busy: back off and retry
            print(f"  retry {attempt + 1} after error: {e}", flush=True)
            time.sleep(wait)
            wait *= 2
    raise SystemExit(f"giving up on {url}")


def overpass(query, path):
    body = get(OVERPASS, urllib.parse.urlencode({"data": query}).encode())
    if not body.startswith(b"{"):
        raise SystemExit(f"Overpass did not return JSON for {path}: {body[:200]!r}")
    with open(path, "wb") as f:
        f.write(body)
    print(f"wrote {path} ({len(body) / 1e6:.1f} MB)", flush=True)


def fetch_osm(out):
    s, w, n, e = BBOX
    bb = f"{s},{w},{n},{e}"
    overpass(f"""[out:json][timeout:300][bbox:{bb}];
(way["building"];relation["building"]["type"="multipolygon"];
 way["building:part"];relation["building:part"]["type"="multipolygon"];);
out body geom;""", os.path.join(out, "osm_buildings.json"))

    overpass(f"""[out:json][timeout:300][bbox:{bb}];
(node["natural"="tree"];
 way["man_made"="bridge"];
 way["route"="ferry"];
 nwr["amenity"="townhall"];
 nwr["name"~"Solitude|Barfüsserplatz|Claraplatz|Theaterplatz|Kaserne|Marktplatz|Münsterplatz|Petersplatz|Tinguely|Messeplatz"];
 way["natural"="water"];relation["natural"="water"];
 way["leisure"="park"];relation["leisure"="park"];);
out body geom({bb});""", os.path.join(out, "osm_extras.json"))


def fetch_ortho(out):
    """The aerial photo: every LV95 kilometre the area touches, at 1 m per pixel."""
    os.makedirs(os.path.join(out, "ortho"), exist_ok=True)
    s, w, n, e = BBOX
    corners = [lv95(la, lo) for la in (s, n) for lo in (w, e)]
    for te in range(math.floor(min(c[0] for c in corners) / 1000), math.floor(max(c[0] for c in corners) / 1000) + 1):
        for tn in range(math.floor(min(c[1] for c in corners) / 1000), math.floor(max(c[1] for c in corners) / 1000) + 1):
            path = os.path.join(out, "ortho", f"{te}-{tn}.jpg")
            if not os.path.exists(path):
                q = urllib.parse.urlencode({"SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetMap",
                                            "LAYERS": "ch.swisstopo.swissimage", "STYLES": "", "CRS": "EPSG:2056",
                                            "BBOX": f"{te * 1000},{tn * 1000},{te * 1000 + 1000},{tn * 1000 + 1000}",
                                            "WIDTH": ORTHO_PX, "HEIGHT": ORTHO_PX, "FORMAT": "image/jpeg"})
                body = get(f"{WMS}?{q}")
                if not body.startswith(b"\xff\xd8"):
                    raise SystemExit(f"WMS did not return a JPEG for tile {te}-{tn}: {body[:200]!r}")
                with open(path, "wb") as f:
                    f.write(body)
                time.sleep(0.3)
            print(f"aerial {te}-{tn}: {os.path.getsize(path) / 1e3:.0f} kB", flush=True)


def fetch_buildings(out):
    """3D buildings: the newest CityGML release of each map sheet that covers a real part of the area."""
    os.makedirs(os.path.join(out, "sb3d"), exist_ok=True)
    s, w, n, e = BBOX
    items = json.loads(get(f"{STAC}/ch.swisstopo.swissbuildings3d_3_0/items?bbox={w},{s},{e},{n}&limit=100"))["features"]
    newest = {}
    for it in items:        # ids look like swissbuildings3d_3_0_2024_1047-34; whole-country releases have no sheet
        parts = it["id"].split("_")
        if len(parts) < 5:
            continue
        year, sheet = parts[3], parts[4]
        x0, y0, x1, y1 = it["bbox"]
        if min(x1, e) - max(x0, w) < 0.001 or min(y1, n) - max(y0, s) < 0.001:
            continue        # only grazes the area
        href = next((a["href"] for k, a in it["assets"].items() if k.endswith(".citygml.zip")), None)
        if href and (sheet not in newest or year > newest[sheet][0]):
            newest[sheet] = (year, href)
    for sheet, (year, href) in sorted(newest.items()):
        path = os.path.join(out, "sb3d", os.path.basename(href))
        if not os.path.exists(path):
            print(f"downloading 3D buildings {sheet} ({year}) ...", flush=True)
            with open(path, "wb") as f:
                f.write(get(href))
        print(f"3D buildings {sheet} {year}: {os.path.getsize(path) / 1e6:.0f} MB", flush=True)


def fetch_trees(out):
    """Basel's tree register (Stadtgärtnerei, data.bs.ch dataset 100052): position, species and age of each public tree."""
    s, w, n, e = BBOX
    q = urllib.parse.urlencode({"select": "geo_point_2d,baumart_lateinisch,ba_baumalter",
                                "where": f"in_bbox(geo_point_2d,{s},{w},{n},{e})"})
    body = get(f"https://data.bs.ch/api/explore/v2.1/catalog/datasets/100052/exports/json?{q}")
    if not body.startswith(b"["):
        raise SystemExit(f"the tree register did not return a JSON list: {body[:200]!r}")
    path = os.path.join(out, "trees_bs.json")
    with open(path, "wb") as f:
        f.write(body)
    print(f"wrote {path} ({len(json.loads(body))} trees)", flush=True)


PARTS = {"osm": fetch_osm, "ortho": fetch_ortho, "sb3d": fetch_buildings, "trees": fetch_trees}

if __name__ == "__main__":
    if len(sys.argv) < 2 or any(a not in PARTS for a in sys.argv[2:]):
        raise SystemExit(__doc__)
    for part in sys.argv[2:] or PARTS:
        PARTS[part](sys.argv[1])
