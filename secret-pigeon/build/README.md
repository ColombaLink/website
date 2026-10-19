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

`fetch_data.py` downloads about 410 MB, most of it the four swissBUILDINGS3D map sheets and nine tiles of the Copernicus
elevation model (47 MB, for the hills on the horizon), and the timetable on top: Switzerland's whole public transport
timetable (GTFS, about 290 MB), from which the build keeps Basel's trams and buses. Add part names (`osm`, `rhine`,
`rail`, `tram`, `bus`, `timetable`, `streets`, `ortho`, `sb3d`, `trees`, `access`, `dem`) to fetch only some of them.

One input can't be fetched: Basel-Stadt's own 3D city model (MapBS "3D-Stadtmodell"), which is newer than
swissBUILDINGS3D in places. The canton's Geodaten-Shop (shop.geo.bs.ch) sends it free by email: order "Wavefront OBJ"
with georeferencing (LV95) for areas of up to 5 km² covering the map, and put the GeodatenBS_*.zip archives it sends
(or the OBJ files in them) in `<raw-dir>/mapbs`. Without them the build keeps swissBUILDINGS3D everywhere and says so.

| File | Does |
|---|---|
| `fetch_data.py` | Downloads the swisstopo aerial photo at 1 m per pixel (WMS), swissBUILDINGS3D 3.0 (CityGML), Basel's tree register and its register of the stairs and ladders down to the rivers (data.bs.ch) and the OpenStreetMap landmarks, bridges, ferries, railway tracks, tram tracks and lines, bus lines and the roads around them, shopping streets and place names (Swiss Overpass mirror), the Rhine's centreline for 22 km round the city (the ways of its OpenStreetMap waterway relation that are its main stream; one small query to the main Overpass instance, since the Swiss mirror's data stops at the border), Switzerland's public transport timetable (GTFS, opentransportdata.swiss), and the Copernicus DEM GLO-90 (a 3 arc-second surface model, heights over the EGM2008 geoid) within 75 km of the Mittlere Brücke: nine 1-degree tiles from the AWS open data registry, no sign-in. |
| `fetch_1615.py` | Downloads the canton's "Übersichtsplan 1615" (WMS, 0.5 m pixels), the outline of the historic core inside the old wall (WFS), and OSM streets used to place the demolished gates. |
| `sb3d.py` | Streams the CityGML, merges each building's roof triangles back into roof planes, drops vertices on straight edges and closes seams between planes. |
| `mapbs.py`, `mapbs-updates.json` | The places where the canton's 3D city model is newer than swissBUILDINGS3D: 22 sites, each checked against the aerial photo the game lays on the ground and the shadows in it (buildings finished since swisstopo's survey, often marked "Im Bau" there, demolished ones, a missing house, the rebuilt Messe block). The list names the swisstopo buildings to drop and the canton's to put in their place, so that a site is swapped whole, without holes or doubled buildings; `mapbs.py` reads those buildings out of the OBJ archives (roof faces, the floor slab's height as the ground, the lowest wall point). Everywhere else the two models agree to within about a metre and the game keeps swissBUILDINGS3D, which also tells canopies, towers and churches apart. |
| `horizon.py` | Reads the Copernicus DEM tiles (GeoTIFF, standard library only) and lays the terrain within 75 km of the Mittlere Brücke on a polar grid for the horizon: 720 bearings (every half degree) by 122 rings, each 3.5 % farther out than the last, every point the highest ground nearest to it so that ridges keep their crests. Heights are over the city's ground (the median ground height of its buildings) and lowered for the Earth's curvature with standard refraction, so each ridge stands as high over Basel's horizon as it does; the terrain rises from the map's flat ground over the first 2 km beyond its edge. `build_city.py` calls it and `fetch_data.py` takes the tile names from it. |
| `build_city.py` | Today's city: the 3D roofs in compact gzip form (the game raises the walls from the roof edges, skipping the ones another roof hides; platform roofs and train sheds over the tracks stand open on posts), roof colours from the aerial photo, the 1 m ground as AVIF with a small JPEG fallback, the hills and mountains on the horizon (`horizon.py`, 53 kB gzipped), the Rhine's two shores (OpenStreetMap's outline of the river) and its course on beyond the map (OpenStreetMap's centreline of its main stream, 7 kB, its bends eased more the farther they are from the map), the canton's stairs and ladders down to it, where the hotel ships moor at St. Johann and where the two Rhine baths stand (read off the aerial photo), trees by species and age, each bridge's axis and stretch over the Rhine, the measurements of the landmarks, of Basel SBB's front, of the Kollegienhaus's eaves and entrance and of the walls and flat roof of Les Trois Rois, the shop houses and their awnings, every railway and tram track, each tram line's route both ways with its stops, colour and operator and every trip of an ordinary weekday on it (from the timetable, with the stops laid on the route), the same for every bus line of BVB, BLT, AAGL and SWEG that comes into the map (OSM's route variants joined way by way, a roundabout driven only from where the route enters it to where it leaves; for a line without one, such as the EV8 replacement bus, the shortest drive along the roads between its stops), each laid in the middle of the rightmost lane its way's tags allow and kept clear of the walls, covered footbridges (OSM `building=bridge`, which swisstopo models down to the street) open underneath, two train tracks from Basel SBB over the Rhine to the Badischer Bahnhof and north off the map, and one from its French platforms towards France (shortest paths along OSM's running lines that never turn back). |
| `build_1615.py` | Basel in 1615: traces every house on the plan into its own outline, builds the wall, towers and gates, plants garden and orchard trees, and paints the 1 m ground from the plan's own pixels (beyond the plan the Rhine runs as it does today, within OpenStreetMap's outline of it, easing over from the plan's river in the first 200 m; without that outline it follows the aerial photo); the river's outline gives the 1615 shores, each from the map's edge to the map's edge. |
| `assemble.py` | Injects both cities into the template and writes the colomba.link copy: it loads three.js and the fonts from the game's own `vendor/` and `fonts/` folders (the site promises no third-party scripts or fonts) and adds the parts in `site-parts.html` (share previews, a byline, a line on the final screen). With a sixth argument it also writes the Artifact copy, which keeps the cdnjs and Google Fonts links, leaves those parts out and drops the page wrapper. |
| `site-parts.html` | What only the colomba.link copy gets: its description, canonical address, Open Graph and Twitter previews (`og.jpg`), structured data and the links back to the site. |

The game itself (`game.template.html`) models the landmarks by hand on top of these measurements: the Münster and its
cloister, the Rathaus (its old front building, north wing and tower told apart by the roof heights behind the front),
the three city gates, the medieval churches, the Elisabethenkirche, Basel SBB's front, the University's Kollegienhaus
at Petersplatz (Roland Rohn, 1937–39: deep wooden eaves, and the bare stone block over its entrance standing on pillars
in front of the mosaic), Les Trois Rois (Amadeus Merian's hotel of 1842–44: yellow plaster and grey pilasters, the
terrace over the quay with its iron railing, the winter garden and the gold letters between the two corner pavilions,
Basel's flag on the south one's roof and the Swiss flag on the north one's, the porch with the Three Kings on the
Blumenrain, and beside it the sandstone corner building put up for the Kantonalbank in 1901–03), the bridges and the
Rhine ferries. The Rhine runs in its bed, about 6.5 m below the promenades, between banks built after photos of
each stretch of them: Kleinbasel's sloped stone Rheinweg with a lower path at the water and broad seating steps from
the Leu ferry past the Kaserne to the Johanniterbrücke, grassy slopes at Solitude and below the Dreirosenbrücke, the
Pfalz's and the Rheinsprung's tall walls over a narrow path, the quay at the Schifflände and Les Trois Rois, and the
St. Alban- and St. Johanns-Rheinweg along the water under a railed wall; in 1615 town walls stand in the water on
both sides. The bridges stand on the water with their decks as high over it as before, the ferries land at small
stages with gangways and hang from masts on the promenades, the canton's stairs and ladders lead down to the water
where its register puts them, and past the edge of the map the river runs on along its real course into the haze:
upstream round the bend at the Birsfelden power station (through its weir, OpenStreetMap's main stream) towards Augst
and Rheinfelden, downstream north along the Upper Rhine (past the Märkt weir down the Old Rhine, not the Grand Canal
d'Alsace), in 1615 as today. Each shore keeps the distance from the other at which it leaves the map, while the river's
middle eases over onto the centreline in the first 750 m. BPG's MS Rhystärn lies
at the Schifflände landing stage below the Mittlere Brücke, MS Christoph Merian at the next one down, leaving now and
then for a trip up the Rhine towards Birsfelden through the bridge's middle arch, a Viking longship at the hotel
ships' landing stage in St. Johann, and the Rhine baths of St. Johann and the Breite stand on their stilts (after the
operators' figures and photos). In the tour the pigeon can dive into the river's bed and fly along the water. In
1615 it marks the university's first home instead, the
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

On the horizon stand the Jura, the Black Forest and the Vosges, from the Copernicus elevation model: every ridge at its
real bearing and distance, lowered for the Earth's curvature so that it stands as high over Basel's horizon as it does,
then stretched 1.4 times like the buildings (the game's one vertical exaggeration). The city's fog ends at 5 km, so the
hills have a haze of their own that pales each ridge with its distance, while up to about 430 m over the city the fog
lies on the hillsides as it does on the plain; the camera sees 80 km. Seen from the river at the Mittlere Brücke, from
above the Münster and from high over the Messe, the drawn skyline is within 0.15° of the elevation model's own
(stretched the same way) at nine bearings out of ten. Along the Rhine's course beyond the map its channel is kept open
through the low ground for 6 km, as far as the water shows through the fog, and the hills stay whole: upstream the
river bends round the foot of the Grenzacher Horn as the real one does.

"Tour the city" on the title screen has no level and no clock: the pigeon hovers on the spot with its wings beating and
moves only when steered, with the levels' keys: W / S climb and dive, A / D turn, and Space held flies forward (on touch
screens the stick, with Flap held). Its "Fly to…" list glides over the rooftops to a landmark of the chosen era and
hovers beside it, turned a little away so the camera sees it past the pigeon (Les Trois Rois from over the Rhine). Esc
goes back to the title.

Next to the game, `vendor/` holds three.js 0.183.2 (`three.module.min.js` imports `three.core.min.js`; both match
cdnjs's SHA-512 hashes; MIT licence in `three-LICENSE.txt`) and `fonts/` the Latin subsets of Atkinson Hyperlegible,
Bungee and DM Mono as WOFF2 with `fonts.css` and their SIL Open Font Licence texts. To move to another three.js
version, replace both files and the version in the template's import and in `assemble.py` together. `og.jpg`
(1200 × 630, the share preview) and `poster-960.webp` / `poster-1440.webp` (the picture on the home page) are renders
from the game's own canvas: the pigeon beside the Münster in tour mode, drawn at twice the size and scaled down.

Credits shown in the game: 3D buildings (swissBUILDINGS3D) and aerial imagery (SWISSIMAGE) © swisstopo; landmarks,
bridges, the Rhine's banks and course, railways, roads, tram and bus lines and places © OpenStreetMap contributors (ODbL); tram and bus timetable: opentransportdata.swiss; the newer 3D buildings in
places (3D-Stadtmodell), tree register (Baumkataster), the
stairs and ladders down to the Rhine, and the 1615 plan and wall line: Quelle: Geodaten Kanton Basel-Stadt (CC BY 4.0);
the hills and mountains on the horizon: produced using Copernicus WorldDEM™-90 © DLR e.V. 2010-2014 and © Airbus Defence
and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved. As its licence
(COP-DEM-GLO-90-F) asks, the credits add: "The organisations in charge of the Copernicus programme by law or by
delegation do not incur any liability for any use of the Copernicus WorldDEM™-90".
