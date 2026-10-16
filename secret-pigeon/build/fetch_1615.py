"""Download the sources for Basel in 1615.

Usage: python3 -I secret-pigeon/build/fetch_1615.py <output-dir>

Writes into <output-dir>:
  plan1615/tile_<col>_<row>.png  the canton's "Übersichtsplan 1615" (WMS, 0.5 m per pixel, Swiss LV95)
  ortskern.json                  outline of the historic core inside the old city wall (WFS, GeoJSON)
  osm_1615.json                  OSM features used to place the old gates and a few 1615 sites
Source: Geodaten Kanton Basel-Stadt (CC BY 4.0); OpenStreetMap contributors (ODbL).
"""
import os
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_data import BBOX, get, overpass  # noqa: E402

WMS = "https://wms.geo.bs.ch/historisch"
WFS = "https://wfs.geo.bs.ch/"
TRACE = (2610400, 1266070, 2612700, 1268760)   # LV95 box (E0, N0, E1, N1) holding the walled city of 1615
COLS, ROWS, MPP = 4, 4, 0.5                     # tiles across, tiles down, metres per pixel


def main(out):
    os.makedirs(os.path.join(out, "plan1615"), exist_ok=True)
    e0, n0, e1, n1 = TRACE
    tw, th = (e1 - e0) / COLS, (n1 - n0) / ROWS
    for c in range(COLS):
        for r in range(ROWS):
            path = os.path.join(out, "plan1615", f"tile_{c}_{r}.png")
            if os.path.exists(path):
                continue
            bbox = f"{e0 + c * tw},{n1 - (r + 1) * th},{e0 + (c + 1) * tw},{n1 - r * th}"
            q = urllib.parse.urlencode({"SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetMap",
                                        "LAYERS": "HP_Uebersichtsplan_Basel_1615", "STYLES": "", "CRS": "EPSG:2056",
                                        "BBOX": bbox, "WIDTH": round(tw / MPP), "HEIGHT": round(th / MPP),
                                        "FORMAT": "image/png"})
            data = get(f"{WMS}?{q}")
            if not data.startswith(b"\x89PNG"):
                raise SystemExit(f"WMS did not return a PNG for tile {c},{r}: {data[:200]!r}")
            with open(path, "wb") as f:
                f.write(data)
            print(f"plan tile {c},{r}: {len(data) / 1e6:.1f} MB", flush=True)
            time.sleep(1)

    q = urllib.parse.urlencode({"SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature",
                                "TYPENAMES": "ms:HD_HistorischerOrtskern", "OUTPUTFORMAT": "geojson",
                                "BBOX": f"{e0 - 500},{n0 - 500},{e1 + 500},{n1 + 500},urn:ogc:def:crs:EPSG::2056"})
    with open(os.path.join(out, "ortskern.json"), "wb") as f:
        f.write(get(f"{WFS}?{q}"))
    print("wrote ortskern.json", flush=True)

    s, w, n, e = BBOX
    overpass(f"""[out:json][timeout:180][bbox:{s},{w},{n},{e}];
(nwr["historic"~"city_gate|citywalls"];nwr["barrier"="city_wall"];
 way["highway"]["name"~"^(Aeschenvorstadt|Steinenvorstadt|Klybeckstrasse|Klingentalstrasse|Riehentorstrasse|St. Alban-Tal)$"];
 nwr["name"~"Papiermühle|Schützenhaus|Theodorskirche|Martinskirche"];);
out body geom;""", os.path.join(out, "osm_1615.json"))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
