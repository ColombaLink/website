# Rebuilding Secret Pigeon's Basel (today and 1615)

`secret-pigeon/index.html` is generated. Edit `game.template.html` (the game, including the hand-modelled landmarks
and bridges) or the scripts below (the city data), then rebuild. Only the Python standard library and macOS `sips`
are needed. Run the commands from the repository root, and run `build_city.py` before `build_1615.py` (the 1615 step
reuses the aerial tiles it converts). The first `build_city.py` run parses about 2.8 GB of CityGML and takes about a
minute; later runs use a cache in the work folder.

```bash
python3 -I secret-pigeon/build/fetch_data.py /tmp/basel-raw
python3 -I secret-pigeon/build/fetch_1615.py /tmp/basel-raw
python3 -I secret-pigeon/build/build_city.py /tmp/basel-raw /tmp/basel-work /tmp/basel-work/basel-data.js
python3 -I secret-pigeon/build/build_1615.py /tmp/basel-raw /tmp/basel-work /tmp/basel-work/basel-1615.js
python3 -I secret-pigeon/build/assemble.py secret-pigeon/build/game.template.html /tmp/basel-work/basel-data.js /tmp/basel-work/basel-1615.js secret-pigeon/index.html
```

`fetch_data.py` downloads about 360 MB, most of it the four swissBUILDINGS3D map sheets, and the timetable on top:
Switzerland's whole public transport timetable (GTFS, about 290 MB), from which the build keeps Basel's trams and buses.
Add part names (`osm`, `rail`, `tram`, `bus`, `timetable`, `streets`, `ortho`, `sb3d`, `trees`) to fetch only some of
them.

| File | Does |
|---|---|
| `fetch_data.py` | Downloads the swisstopo aerial photo at 1 m per pixel (WMS), swissBUILDINGS3D 3.0 (CityGML), Basel's tree register (data.bs.ch) and the OpenStreetMap landmarks, bridges, ferries, railway tracks, tram tracks and lines, bus lines and the roads around them, shopping streets and place names (Swiss Overpass mirror), and Switzerland's public transport timetable (GTFS, opentransportdata.swiss). |
| `fetch_1615.py` | Downloads the canton's "Übersichtsplan 1615" (WMS, 0.5 m pixels), the outline of the historic core inside the old wall (WFS), and OSM streets used to place the demolished gates. |
| `sb3d.py` | Streams the CityGML, merges each building's roof triangles back into roof planes, drops vertices on straight edges and closes seams between planes. |
| `build_city.py` | Today's city: the 3D roofs in compact gzip form (the game raises the walls from the roof edges, skipping the ones another roof hides; platform roofs and train sheds over the tracks stand open on posts), roof colours from the aerial photo, the 1 m ground as AVIF with a small JPEG fallback, the Rhine's outline for the animated water, trees by species and age, each bridge's axis and stretch over the Rhine, the measurements of the landmarks, of Basel SBB's front and of the Kollegienhaus's eaves and entrance, the shop houses and their awnings, every railway and tram track, each tram line's route both ways with its stops, colour and operator and every trip of an ordinary weekday on it (from the timetable, with the stops laid on the route), the same for every bus line of BVB, BLT, AAGL and SWEG that comes into the map (OSM's route variants joined way by way, a roundabout driven only from where the route enters it to where it leaves; for a line without one, such as the EV8 replacement bus, the shortest drive along the roads between its stops), each laid in the middle of the rightmost lane its way's tags allow and kept clear of the walls, covered footbridges (OSM `building=bridge`, which swisstopo models down to the street) open underneath, two train tracks from Basel SBB over the Rhine to the Badischer Bahnhof and north off the map, and one from its French platforms towards France (shortest paths along OSM's running lines that never turn back). |
| `build_1615.py` | Basel in 1615: traces every house on the plan into its own outline, builds the wall, towers and gates, plants garden and orchard trees, and paints the 1 m ground from the plan's own pixels (the Rhine beyond the plan follows the aerial photo); the river's outline from the plan gives the 1615 water. |
| `assemble.py` | Injects both cities into the template and writes the colomba.link copy: it loads three.js and the fonts from the game's own `vendor/` and `fonts/` folders (the site promises no third-party scripts or fonts) and adds the parts in `site-parts.html` (share previews, a byline, a line on the final screen). With a sixth argument it also writes the Artifact copy, which keeps the cdnjs and Google Fonts links, leaves those parts out and drops the page wrapper. |
| `site-parts.html` | What only the colomba.link copy gets: its description, canonical address, Open Graph and Twitter previews (`og.jpg`), structured data and the links back to the site. |

The game itself (`game.template.html`) models the landmarks by hand on top of these measurements: the Münster and its
cloister, the Rathaus (its old front building, north wing and tower told apart by the roof heights behind the front),
the three city gates, the medieval churches, the Elisabethenkirche, Basel SBB's front, the University's Kollegienhaus
at Petersplatz (Roland Rohn, 1937–39: deep wooden eaves, and the bare stone block over its entrance standing on pillars
in front of the mosaic), the bridges and the Rhine ferries. In 1615 it marks the university's first home instead, the
college on the Rheinsprung, used from 1460 until the Kollegienhaus opened. It also paints the facades of the Messe, the Kunstmuseum, the two stations, Museum Tinguely, the Meret
Oppenheim Hochhaus and the shop houses from Barfüsserplatz to Marktplatz (with awnings, and market stalls on the
Marktplatz by day), lays every railway and tram track in the map, runs a DB ICE and an SBB EuroCity towards Germany and a TGV
Lyria towards France, and runs Basel's twelve tram lines to their real weekday timetable, three times faster than life and from the
time it is in Basel, every trip stopping at each of its stations. A tram waits behind one ahead of it on its track, and
where two tram tracks cross, join, or run closer than two trams can pass (lines 10 and 17 run both ways on one track
over a crossing), one tram goes through at a time, or several on the same route. A tram starting its trip at a
terminus in the map stands on the track behind the stop, and one running late still reaches its terminus before it goes.
The 15 bus lines that come into the map run to the same timetable: BVB's green battery buses (Mercedes eCitaro G
articulated buses, and HESS lighTram 25 double-articulated ones on line 50 to the airport, after the photos and
specification sheets BVB publishes), BLT's and AAGL's yellow ones and SWEG's white ones. Every bus stands at each stop
of its trip (a late one briefly) and moves only where its body and the road just ahead of it are clear of every other
bus and tram; the trams keep clear of the buses the same way. Where a bus route crosses another or a tram track, joins
one, or would meet a vehicle coming the other way on a bend or a narrow street, the game makes a zone (found when it
loads, from the routes), and the same for the tram tracks; a bus or tram goes in only with all the zones it will be in
until its tail is out, and room to stop beyond them, so none of them can lock another out of a junction. A full
weekday replayed at 3× has no tram or bus overlapping another and no stop missed.

"Tour the city" on the title screen has no level and no clock: the pigeon hovers on the spot with its wings beating and
moves only when steered (W / S forward and back, A / D turn, Space up, Shift down; on touch screens the stick, with Flap
and Sink held). Its "Fly to…" list glides over the rooftops to a landmark of the chosen era. Esc goes back to the title.

Next to the game, `vendor/` holds three.js 0.183.2 (`three.module.min.js` imports `three.core.min.js`; both match
cdnjs's SHA-512 hashes; MIT licence in `three-LICENSE.txt`) and `fonts/` the Latin subsets of Atkinson Hyperlegible,
Bungee and DM Mono as WOFF2 with `fonts.css` and their SIL Open Font Licence texts. To move to another three.js
version, replace both files and the version in the template's import and in `assemble.py` together. `og.jpg`
(1200 × 630, the share preview) and `poster-960.webp` / `poster-1440.webp` (the picture on the home page) are renders
from the game's own canvas: the pigeon beside the Münster in tour mode, drawn at twice the size and scaled down.

Credits shown in the game: 3D buildings (swissBUILDINGS3D) and aerial imagery (SWISSIMAGE) © swisstopo; landmarks,
bridges, railways, roads, tram and bus lines and places © OpenStreetMap contributors (ODbL); tram and bus timetable: opentransportdata.swiss; tree register (Baumkataster) and the 1615 plan and
wall line: Quelle: Geodaten Kanton Basel-Stadt (CC BY 4.0).
