"""Download the raw map data for Secret Pigeon's Basel.

Usage: python3 -I secret-pigeon/build/fetch_data.py <output-dir> [osm] [rhine] [rail] [tram] [bus] [timetable] [streets]
       [ortho] [sb3d] [trees] [access] [fountains] [art] [dem]
  (no part names: all of them)

Writes into <output-dir>:
  osm_buildings.json   buildings and building parts (OpenStreetMap, ODbL) via the Swiss Overpass mirror; used for
                       the landmarks and the facade buildings
  osm_extras.json      trees, bridges, ferries, water, parks and a few named places
  osm_rhine.json       the Rhine's centreline (the main stream of its waterway relation) for 22 km round the city, for
                       its way on beyond the map; from the main Overpass instance, as it runs on into Germany and France
  osm_rail.json        railway tracks and stations for the trains to Germany and France (a little wider than the
                       map: the lines loop out of it and back in)
  osm_streets.json     the shopping streets between Barfüsserplatz and Marktplatz, for the shop fronts
  osm_tram.json        the tram tracks and the tram lines (route relations: colours, operators, stops)
  osm_bus.json         the bus lines (route relations, without the long-distance coaches) and the roads around them
  gtfs.zip             Switzerland's public transport timetable (GTFS, opentransportdata.swiss), for the trams' and
                       buses' times
  ortho/E-N.jpg        swisstopo SWISSIMAGE at 1 m per pixel, one file per LV95 kilometre (WMS)
  sb3d/*.citygml.zip   swisstopo swissBUILDINGS3D 3.0 (CityGML, LOD2 roofs), newest release of each map sheet
  trees_bs.json        Basel's public tree register (species and age), Geodaten Kanton Basel-Stadt
  access_bs.json       the stairs and ladders down to the water along the rivers, Geodaten Kanton Basel-Stadt
  fountains_bs.json    Basel's public fountains (IWB's inventory): names and positions
  art_bs.json          the canton's public art collection, for the statues the game models
  dem/*.tif            the Copernicus DEM GLO-90 (WorldDEM-90, 3 arc-seconds) around Basel out to 75 km, nine
                       1-degree tiles, for the hills and mountains on the horizon (about 47 MB)
"""
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import horizon  # noqa: E402
from build_city import COURSE_R, ORIGIN_LATLON, RHINE, lv95  # noqa: E402

BBOX = (47.5440, 7.5690, 47.5735, 7.6170)  # south, west, north, east
UA = "SecretPigeonGame/0.1 (educational game; https://colomba.link, info@colomba.link)"
OVERPASS = "https://overpass.osm.ch/api/interpreter"
OVERPASS_WORLD = "https://overpass-api.de/api/interpreter"   # the Swiss mirror's data stops at the border
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


def overpass(query, path, server=OVERPASS):
    body = get(server, urllib.parse.urlencode({"data": query}).encode())
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


def fetch_rhine(out):
    """The Rhine's course beyond the map: the ways of its waterway relation (Wikidata Q584) that are its main stream,
    within COURSE_R of the Mittlere Brücke, in the direction it flows. The Swiss mirror has nothing past the border,
    so this one small query goes to the main Overpass instance."""
    la, lo = ORIGIN_LATLON
    dla = COURSE_R / 111000.0 + 0.02
    dlo = COURSE_R / (111000.0 * math.cos(math.radians(la))) + 0.02
    overpass(f"""[out:json][timeout:300];
relation["type"="waterway"]["wikidata"="{RHINE}"];
way(r:"main_stream")({la - dla:.3f},{lo - dlo:.3f},{la + dla:.3f},{lo + dlo:.3f});
out tags geom;""", os.path.join(out, "osm_rhine.json"), OVERPASS_WORLD)


def fetch_rail(out):
    s, w, n, e = BBOX
    bb = f"{s - 0.006},{w - 0.02},{n + 0.004},{e + 0.006}"     # wider: the lines loop out of the map and back in
    overpass(f"""[out:json][timeout:300][bbox:{bb}];
(way["railway"="rail"];node["railway"="station"];);
out body geom;""", os.path.join(out, "osm_rail.json"))


def fetch_tram(out):
    s, w, n, e = BBOX
    bb = f"{s - 0.006},{w - 0.02},{n + 0.004},{e + 0.006}"     # the lines run on out of the map: their route
    overpass(f"""[out:json][timeout:300][bbox:{bb}];
(way["railway"="tram"];relation["route"="tram"];);
out body geom;""", os.path.join(out, "osm_tram.json"))


def fetch_bus(out):
    """The bus lines (route relations: ways in driving order, stops; not the long-distance coaches) and the roads
    around them: their tags tell which side a bus keeps to, and a line without a route is driven along them."""
    s, w, n, e = BBOX
    bb = f"{s - 0.006},{w - 0.02},{n + 0.004},{e + 0.006}"     # like the trams: the lines run on out of the map
    overpass(f"""[out:json][timeout:300][bbox:{bb}];
relation["route"="bus"]["network"!~"flix",i]["operator"!~"flix",i];
out body geom;
way["highway"~"^(motorway|trunk|primary|secondary|tertiary|unclassified|residential|living_street|service|road|busway|bus_guideway)(_link)?$"]["area"!="yes"]["service"!~"^(parking_aisle|driveway|drive-through)$"];
out body geom;""", os.path.join(out, "osm_bus.json"))


GTFS_PACKAGE = "https://ckan.opendata.swiss/api/3/action/package_show?id=fahrplan-2026-gtfs2020"


def fetch_timetable(out):
    """Switzerland's official public transport timetable (GTFS, opentransportdata.swiss): the newest daily file,
    about 300 MB. The build keeps only Basel's trams and buses from it."""
    res = json.loads(get(GTFS_PACKAGE))["result"]["resources"]
    zips = [r for r in res if (r.get("format") or "").upper() == "ZIP" and "gtfs" in (r.get("url") or "").lower()]
    newest = max(zips, key=lambda r: r.get("created") or "")
    path = os.path.join(out, "gtfs.zip")
    req = urllib.request.Request(newest["url"], headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=600) as r, open(path + ".part", "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    os.replace(path + ".part", path)
    print(f"wrote {path} ({os.path.getsize(path) / 1e6:.0f} MB, {newest['url'].rsplit('/', 1)[-1]})", flush=True)


SHOPPING = ["Gerbergasse", "Falknerstrasse", "Freie Strasse", "Streitgasse", "Barfüssergasse", "Eisengasse", "Marktgasse"]


def fetch_streets(out):
    s, w, n, e = BBOX
    names = "|".join(SHOPPING)
    overpass(f"""[out:json][timeout:300][bbox:{s},{w},{n},{e}];
way["highway"]["name"~"^({names})$"];
out body geom;""", os.path.join(out, "osm_streets.json"))


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


def fetch_access(out):
    """The stairs and ladders down to the water along Basel's rivers (data.bs.ch dataset 100285): position and kind."""
    s, w, n, e = BBOX
    q = urllib.parse.urlencode({"select": "geo_point_2d,typ_bez,ausstieg_moeglich",
                                "where": f"in_bbox(geo_point_2d,{s},{w},{n},{e})"})
    body = get(f"https://data.bs.ch/api/explore/v2.1/catalog/datasets/100285/exports/json?{q}")
    if not body.startswith(b"["):
        raise SystemExit(f"the waterside access points did not come as a JSON list: {body[:200]!r}")
    path = os.path.join(out, "access_bs.json")
    with open(path, "wb") as f:
        f.write(body)
    print(f"wrote {path} ({len(json.loads(body))} stairs and ladders)", flush=True)


def fetch_fountains(out):
    """Basel's public fountains (IWB, data.bs.ch dataset 100008): name, place and position. Credit "Quelle: IWB /
    Geodaten Kanton Basel-Stadt"; IWB asks to be asked before any commercial use."""
    s, w, n, e = BBOX
    q = urllib.parse.urlencode({"select": "name,desc,geo_point_2d", "where": f"in_bbox(geo_point_2d,{s},{w},{n},{e})"})
    body = get(f"https://data.bs.ch/api/explore/v2.1/catalog/datasets/100008/exports/json?{q}")
    if not body.startswith(b"["):
        raise SystemExit(f"the fountain inventory did not come as a JSON list: {body[:200]!r}")
    path = os.path.join(out, "fountains_bs.json")
    with open(path, "wb") as f:
        f.write(body)
    print(f"wrote {path} ({len(json.loads(body))} fountains)", flush=True)


def fetch_art(out):
    """The canton's public art collection (data.bs.ch dataset 100214, CC BY 4.0): inventory number, artist, title,
    place and position, for the statues the game models (build_city.STATUES)."""
    s, w, n, e = BBOX
    q = urllib.parse.urlencode({"select": "id_invnr,ku_name,werktitel,datierung,standort,rueckbau,geo_point_2d",
                                "where": f"in_bbox(geo_point_2d,{s},{w},{n},{e})"})
    body = get(f"https://data.bs.ch/api/explore/v2.1/catalog/datasets/100214/exports/json?{q}")
    if not body.startswith(b"["):
        raise SystemExit(f"the public art collection did not come as a JSON list: {body[:200]!r}")
    path = os.path.join(out, "art_bs.json")
    with open(path, "wb") as f:
        f.write(body)
    print(f"wrote {path} ({len(json.loads(body))} works)", flush=True)


def fetch_dem(out):
    """The terrain for the horizon: the Copernicus DEM GLO-90 tiles (Cloud Optimized GeoTIFFs from the AWS open data
    registry, no sign-in) within horizon.R_OUT of the Mittlere Brücke. Licence: free and open, with the notice in
    the game's credits (COP-DEM-GLO-90-F, https://dataspace.copernicus.eu/explore-data/data-collections/
    copernicus-contributing-missions/collections-description/COP-DEM)."""
    os.makedirs(os.path.join(out, "dem"), exist_ok=True)
    for name in horizon.tiles(*ORIGIN_LATLON):
        path = os.path.join(out, "dem", name + ".tif")
        if not os.path.exists(path):
            body = get(horizon.GLO90.format(name))
            if body[:4] not in (b"II*\0", b"MM\0*"):
                raise SystemExit(f"the DEM tile {name} is not a TIFF: {body[:200]!r}")
            with open(path + ".part", "wb") as f:
                f.write(body)
            os.replace(path + ".part", path)
        print(f"DEM {name}: {os.path.getsize(path) / 1e6:.1f} MB", flush=True)


PARTS = {"osm": fetch_osm, "rhine": fetch_rhine, "rail": fetch_rail, "tram": fetch_tram, "bus": fetch_bus, "timetable": fetch_timetable,
         "streets": fetch_streets, "ortho": fetch_ortho, "sb3d": fetch_buildings, "trees": fetch_trees, "access": fetch_access,
         "fountains": fetch_fountains, "art": fetch_art, "dem": fetch_dem}

if __name__ == "__main__":
    if len(sys.argv) < 2 or any(a not in PARTS for a in sys.argv[2:]):
        raise SystemExit(__doc__)
    for part in sys.argv[2:] or PARTS:
        PARTS[part](sys.argv[1])
