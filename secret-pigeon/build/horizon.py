"""The hills and mountains on Secret Pigeon's horizon: the Jura, the Black Forest and the Vosges, from the Copernicus DEM.

Used by build_city.py (build()) and fetch_data.py (tiles()). Standard library only.

The terrain comes from Copernicus GLO-90 (WorldDEM-90: 3 arc-second cells, about 90 by 60 m here, heights in metres over
the EGM2008 geoid), the tiles fetch_data.py saves in <raw>/dem. It is a surface model: woods and towns stand on the
ground in it, which is what a skyline seen from afar is made of, but it is not bare earth. Around the game's origin (the
Mittlere Brücke) the terrain out to R_OUT is laid on a polar grid in LV95 metres (bearings from grid north, which here
is 0.1 degrees off true north): N_AZ bearings, by rings whose spacing grows with the distance, so every cell spans
about the same angle seen from Basel. Each grid point takes the highest of the DEM cells nearest to it and of the height
interpolated at the point, so ridges keep their crests.

Heights are given relative to the city's ground (the game's y = 0, the median ground height of its buildings) and
lowered by the Earth's curvature as seen from the bridge, with standard refraction (k = 0.13): a ridge 50 km away
drops by 170 m. The flat game world then shows each ridge at the angle it stands over Basel's horizon, and the game
multiplies the heights by VS like the buildings' (1.4 times the horizontal scale). Over the first FADE metres beyond
the map's edge the terrain rises out of the map's flat ground; inside the map it stays under it.
"""
import array
import base64
import gzip
import itertools
import math
import os
import struct
import sys
import zlib

R_IN = 1200.0        # metres from the origin: the first ring, inside the map everywhere
R_OUT = 75000.0      # the last ring: past the Vosges' crest (the Ballon d'Alsace is 63 km away) and the Chasseral
N_AZ = 720           # bearings: every half degree
Q = 0.035            # each ring this much farther out than the one before
FADE = 2000.0        # metres beyond the map's edge over which the terrain rises from the map's flat ground
EARTH_R = 6371000.0
REFRACTION = 0.13    # the usual coefficient of terrestrial refraction
GLO90 = "https://copernicus-dem-90m.s3.eu-central-1.amazonaws.com/{0}/{0}.tif"


def tile_name(lat, lon):
    """Copernicus' name for the 1-degree tile whose south-west corner is (lat, lon)."""
    return (f"Copernicus_DSM_COG_30_{'N' if lat >= 0 else 'S'}{abs(lat):02d}_00_"
            f"{'E' if lon >= 0 else 'W'}{abs(lon):03d}_00_DEM")


def tiles(lat, lon, radius=R_OUT):
    """The names of the 1-degree tiles within `radius` metres of (lat, lon)."""
    dlat = radius / 111000.0 + 0.01
    dlon = radius / (111000.0 * math.cos(math.radians(lat))) + 0.01
    return [tile_name(a, o) for a in range(math.floor(lat - dlat), math.floor(lat + dlat) + 1)
            for o in range(math.floor(lon - dlon), math.floor(lon + dlon) + 1)]


def read_geotiff(path):
    """A single-band float32 GeoTIFF on a geographic grid, as (lon0, lat0, step_lon, step_lat, width, height, values):
    values[j * width + i] is the height at (lon0 + i * step_lon, lat0 - j * step_lat). Tiled or striped, uncompressed or
    DEFLATE, with or without the floating-point predictor (which Copernicus' COGs use)."""
    b = open(path, "rb").read()
    bo = {b"II": "<", b"MM": ">"}.get(b[:2])
    if bo is None or struct.unpack_from(bo + "H", b, 2)[0] != 42:
        raise SystemExit(f"{path}: not a classic TIFF")
    off = struct.unpack_from(bo + "I", b, 4)[0]
    sizes = {1: 1, 2: 1, 3: 2, 4: 4, 11: 4, 12: 8, 16: 8}
    fmts = {1: "B", 2: "s", 3: "H", 4: "I", 11: "f", 12: "d", 16: "Q"}
    tags = {}
    for k in range(struct.unpack_from(bo + "H", b, off)[0]):      # the first IFD: the full-resolution image
        tag, typ, cnt, val = struct.unpack_from(bo + "HHI4s", b, off + 2 + 12 * k)
        if typ not in fmts:
            continue
        at = off + 2 + 12 * k + 8 if sizes[typ] * cnt <= 4 else struct.unpack(bo + "I", val)[0]
        tags[tag] = b[at:at + cnt] if typ == 2 else struct.unpack_from(bo + fmts[typ] * cnt, b, at)
    w, h = tags[256][0], tags[257][0]
    if tags.get(258, (32,))[0] != 32 or tags.get(339, (1,))[0] != 3 or tags.get(277, (1,))[0] != 1:
        raise SystemExit(f"{path}: expected one band of 32-bit floats")
    comp, pred = tags.get(259, (1,))[0], tags.get(317, (1,))[0]
    if comp not in (1, 8, 32946) or pred not in (1, 3):
        raise SystemExit(f"{path}: compression {comp} / predictor {pred} not supported")
    if 322 in tags:
        tw, th, offs, cnts = tags[322][0], tags[323][0], tags[324], tags[325]
    else:
        tw, th, offs, cnts = w, tags.get(278, (h,))[0], tags[273], tags[279]
    across = (w + tw - 1) // tw
    out = array.array("f", bytes(4 * w * h))
    little = sys.byteorder == "little"
    acc_type = next(t for t in "IL" if array.array(t).itemsize == 4)
    lo = 0 if little else 3                                         # where an int's lowest byte sits
    for t, (o, n) in enumerate(zip(offs, cnts)):
        x0, y0 = (t % across) * tw, (t // across) * th
        if y0 >= h:
            continue
        raw = b[o:o + n]
        data = zlib.decompress(raw) if comp != 1 else raw
        rows = min(th, h - y0)
        cols = min(tw, w - x0)
        for r in range(rows):
            row = data[r * tw * 4:(r + 1) * tw * 4]
            if pred == 3:   # undo the byte-wise differencing, then put each value's four bytes (big-endian) together
                row = array.array(acc_type, itertools.accumulate(row)).tobytes()[lo::4]
                be = bytearray(tw * 4)
                for k in range(4):
                    be[k::4] = row[k * tw:(k + 1) * tw]
                vals = array.array("f", bytes(be[:cols * 4]))
                if little:
                    vals.byteswap()
            else:
                vals = array.array("f", row[:cols * 4])
                if (bo == "<") != little:
                    vals.byteswap()
            out[(y0 + r) * w + x0:(y0 + r) * w + x0 + cols] = vals
    sx, sy = tags[33550][0], tags[33550][1]
    tie = tags[33922]
    keys = tags.get(34735, ())
    point = any(keys[i] == 1025 and keys[i + 3] == 2 for i in range(4, len(keys), 4))   # PixelIsPoint
    lon0, lat0 = tie[3] - tie[0] * sx, tie[4] + tie[1] * sy
    if not point:           # PixelIsArea: the tie point is a cell's corner; heights belong to its middle
        lon0, lat0 = lon0 + sx / 2, lat0 - sy / 2
    return lon0, lat0, sx, sy, w, h, out


def lv95(lat, lon):
    """WGS84 -> LV95 (swisstopo's approximate formulas, as in build_city.py)."""
    p = (lat * 3600 - 169028.66) / 10000
    l = (lon * 3600 - 26782.5) / 10000
    e = 2600072.37 + 211455.93 * l - 10938.51 * l * p - 0.36 * l * p * p - 44.54 * l ** 3
    n = 1200147.07 + 308807.95 * p + 3745.25 * l * l + 76.63 * p * p - 194.56 * l * l * p + 119.79 * p ** 3
    return e, n


def wgs84(e, n):
    """LV95 -> WGS84 (swisstopo's approximate inverse formulas)."""
    y = (e - 2600000) / 1e6
    x = (n - 1200000) / 1e6
    lon = 2.6779094 + 4.728982 * y + 0.791484 * y * x + 0.1306 * y * x * x - 0.0436 * y ** 3
    lat = 16.9023892 + 3.238272 * x - 0.270978 * y * y - 0.002528 * x * x - 0.0447 * y * y * x - 0.0140 * x ** 3
    return lat * 100 / 36, lon * 100 / 36


def rings():
    """The rings' distances from the origin in metres: R_IN, R_IN * (1 + Q), ... up to R_OUT."""
    n = int(math.ceil(math.log(R_OUT / R_IN) / math.log(1 + Q))) + 1
    return [R_IN * (1 + Q) ** j for j in range(n)]


class Dem:
    """The DEM tiles in <raw>/dem, sampled by latitude and longitude."""

    def __init__(self, folder):
        self.tiles = []
        for f in sorted(os.listdir(folder)):
            if f.endswith(".tif"):
                self.tiles.append(read_geotiff(os.path.join(folder, f)))
        if not self.tiles:
            raise SystemExit(f"no DEM tiles in {folder}: run fetch_data.py <raw-dir> dem first")

    def at(self, lat, lon):
        """The bilinearly interpolated height, or None outside the tiles. A tile's last cell is one short of the next
        tile's first: between them, the nearer edge's height."""
        for lon0, lat0, sx, sy, w, h, v in self.tiles:
            fx, fy = (lon - lon0) / sx, (lat0 - lat) / sy
            if -1 < fx < w and -1 < fy < h:
                fx, fy = min(max(fx, 0.0), w - 1.0), min(max(fy, 0.0), h - 1.0)
                i, j = min(int(fx), w - 2), min(int(fy), h - 2)
                tx, ty = fx - i, fy - j
                k = j * w + i
                return ((v[k] * (1 - tx) + v[k + 1] * tx) * (1 - ty) + (v[k + w] * (1 - tx) + v[k + w + 1] * tx) * ty)
        return None


def drop(d):
    """How far the ground d metres away sinks below a flat horizon: the Earth's curvature less refraction."""
    return d * d * (1 - REFRACTION) / (2 * EARTH_R)


def grid(dem, e0, n0):
    """The polar grid's heights in metres over the EGM2008 geoid: ring by ring, N_AZ bearings each, clockwise from
    grid north. Each point takes the highest DEM cell whose middle is nearer to it than to any other grid point, or
    the height interpolated at the point, whichever is higher."""
    rs = rings()
    nr = len(rs)
    da = 2 * math.pi / N_AZ
    lq = math.log(1 + Q)
    top = [-1e9] * (nr * N_AZ)
    reach = rs[-1] * (1 + Q / 2)
    r2min, r2max = (R_IN * 0.98) ** 2, reach * reach
    for lon0, lat0, sx, sy, w, h, v in dem.tiles:
        for j in range(h):
            p = ((lat0 - j * sy) * 3600 - 169028.66) / 10000
            ce = 211455.93 - 10938.51 * p - 0.36 * p * p           # lv95() with the row's latitude worked in
            cn = 1200147.07 + 308807.95 * p + 76.63 * p * p + 119.79 * p ** 3 - n0
            cl = 3745.25 - 194.56 * p
            if abs(cn) > reach + 100:
                continue
            half = math.sqrt(max(0.0, reach * reach - cn * cn)) + 2000   # the columns across the circle, with room
            l_lo, l_hi = (e0 - half - 2600072.37) / ce, (e0 + half - 2600072.37) / ce
            i_lo = max(0, int(((l_lo * 10000 + 26782.5) / 3600 - lon0) / sx) - 2)
            i_hi = min(w, int(((l_hi * 10000 + 26782.5) / 3600 - lon0) / sx) + 3)
            row = j * w
            for i in range(i_lo, i_hi):
                l = ((lon0 + i * sx) * 3600 - 26782.5) / 10000
                x = 2600072.37 + ce * l - 44.54 * l * l * l - e0
                y = cn + cl * l * l
                r2 = x * x + y * y
                if r2 < r2min or r2 > r2max:
                    continue
                k = (int(math.log(math.sqrt(r2) / R_IN) / lq + 0.5) * N_AZ
                     + math.floor(math.atan2(x, y) / da + 0.5) % N_AZ)
                if k < len(top) and v[row + i] > top[k]:
                    top[k] = v[row + i]
    out = array.array("f")
    for jr, r in enumerate(rs):
        for ia in range(N_AZ):
            a = ia * da
            lat, lon = wgs84(e0 + r * math.sin(a), n0 + r * math.cos(a))
            z = dem.at(lat, lon)
            if z is None:
                raise SystemExit(f"the DEM tiles do not cover {lat:.3f} N {lon:.3f} E: fetch the dem part again")
            out.append(max(z, top[jr * N_AZ + ia]))
    return out


def build(raw, e0, n0, area, h0):
    """BASEL.horizon: the heights on the polar grid around (e0, n0) (LV95) in whole metres, relative to the city's
    ground h0 (m over the geoid), lowered for the Earth's curvature and faded in beyond the map `area` (west, east,
    south, north in metres around the origin); packed per ring as the first height and then each one's difference
    from the one before it, as zigzag varints, gzipped. Returns (data, report)."""
    dem = Dem(os.path.join(raw, "dem"))
    rs = rings()
    z = grid(dem, e0, n0)
    W, E, S, N = area
    da = 2 * math.pi / N_AZ
    rel = array.array("h")
    for jr, r in enumerate(rs):
        below = h0 + drop(r)
        for ia in range(N_AZ):
            x, y = r * math.sin(ia * da), r * math.cos(ia * da)
            f = min(1.0, math.hypot(max(W - x, 0.0, x - E), max(S - y, 0.0, y - N)) / FADE)
            rel.append(int(round((z[jr * N_AZ + ia] - below) * f * f * (3 - 2 * f))))
    buf = bytearray()
    first = 0
    for jr in range(len(rs)):
        prev = first
        for ia in range(N_AZ):
            v = rel[jr * N_AZ + ia]
            d = v - prev
            prev = v
            if ia == 0:
                first = v
            u = d * 2 if d >= 0 else -d * 2 - 1
            while u >= 0x80:
                buf.append(u & 0x7F | 0x80)
                u >>= 7
            buf.append(u)
    packed = gzip.compress(bytes(buf), 9, mtime=0)
    data = {"az": N_AZ, "r0": R_IN, "q": Q, "rings": len(rs), "h0": round(h0, 1),
            "gz": base64.b64encode(packed).decode()}
    peak = max(range(len(rel)), key=rel.__getitem__)
    report = {"rings": len(rs), "points": len(rel), "highest_m": [rel[peak], round(rs[peak // N_AZ]),
              round(peak % N_AZ * 360 / N_AZ, 1)], "packed_kB": len(packed) // 1000}
    return data, report
