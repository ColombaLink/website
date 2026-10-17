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

`fetch_data.py` downloads about 330 MB: the four swissBUILDINGS3D map sheets are most of it. Add part names
(`osm`, `rail`, `tram`, `timetable`, `streets`, `ortho`, `sb3d`, `trees`) to fetch only some of them. The
timetable is Switzerland's whole public transport timetable (GTFS, about 300 MB); the build keeps Basel's trams from it.

| File | Does |
|---|---|
| `fetch_data.py` | Downloads the swisstopo aerial photo at 1 m per pixel (WMS), swissBUILDINGS3D 3.0 (CityGML), Basel's tree register (data.bs.ch) and the OpenStreetMap landmarks, bridges, ferries, railway tracks, tram tracks and lines, shopping streets and place names (Swiss Overpass mirror), and Switzerland's public transport timetable (GTFS, opentransportdata.swiss). |
| `fetch_1615.py` | Downloads the canton's "Übersichtsplan 1615" (WMS, 0.5 m pixels), the outline of the historic core inside the old wall (WFS), and OSM streets used to place the demolished gates. |
| `sb3d.py` | Streams the CityGML, merges each building's roof triangles back into roof planes, drops vertices on straight edges and closes seams between planes. |
| `build_city.py` | Today's city: the 3D roofs in compact gzip form (the game raises the walls from the roof edges, skipping the ones another roof hides; platform roofs and train sheds over the tracks stand open on posts), roof colours from the aerial photo, the 1 m ground as AVIF with a small JPEG fallback, the Rhine's outline for the animated water, trees by species and age, each bridge's axis and stretch over the Rhine, the measurements of the landmarks, of Basel SBB's front and of the Kollegienhaus's eaves and entrance, the shop houses and their awnings, every railway and tram track, each tram line's route both ways with its stops, colour and operator and every trip of an ordinary weekday on it (from the timetable, with the stops laid on the route), two train tracks from Basel SBB over the Rhine to the Badischer Bahnhof and north off the map, and one from its French platforms towards France (shortest paths along OSM's running lines that never turn back). |
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
time it is in Basel, every trip stopping at each of its stations; a tram that would run into another waits behind it.

"Tour the city" on the title screen has no level and no clock: the pigeon hovers on the spot with its wings beating and
moves only when steered, with the levels' keys: W / S climb and dive, A / D turn, and Space held flies forward (on touch
screens the stick, with Flap held). Its "Fly to…" list glides over the rooftops to a landmark of the chosen era. Esc goes
back to the title.

Next to the game, `vendor/` holds three.js 0.183.2 (`three.module.min.js` imports `three.core.min.js`; both match
cdnjs's SHA-512 hashes; MIT licence in `three-LICENSE.txt`) and `fonts/` the Latin subsets of Atkinson Hyperlegible,
Bungee and DM Mono as WOFF2 with `fonts.css` and their SIL Open Font Licence texts. To move to another three.js
version, replace both files and the version in the template's import and in `assemble.py` together. `og.jpg`
(1200 × 630, the share preview) and `poster-960.webp` / `poster-1440.webp` (the picture on the home page) are renders
from the game's own canvas: the pigeon beside the Münster in tour mode, drawn at twice the size and scaled down.

Credits shown in the game: 3D buildings (swissBUILDINGS3D) and aerial imagery (SWISSIMAGE) © swisstopo; landmarks,
bridges, railways, tram lines and places © OpenStreetMap contributors (ODbL); tram timetable: opentransportdata.swiss; tree register (Baumkataster) and the 1615 plan and
wall line: Quelle: Geodaten Kanton Basel-Stadt (CC BY 4.0).
