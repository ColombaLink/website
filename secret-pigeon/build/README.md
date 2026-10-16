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
(`osm`, `ortho`, `sb3d`, `trees`) to fetch only some of them.

| File | Does |
|---|---|
| `fetch_data.py` | Downloads the swisstopo aerial photo at 1 m per pixel (WMS), swissBUILDINGS3D 3.0 (CityGML), Basel's tree register (data.bs.ch) and the OpenStreetMap landmarks, bridges, ferries and place names (Swiss Overpass mirror). |
| `fetch_1615.py` | Downloads the canton's "Übersichtsplan 1615" (WMS, 0.5 m pixels), the outline of the historic core inside the old wall (WFS), and OSM streets used to place the demolished gates. |
| `sb3d.py` | Streams the CityGML, merges each building's roof triangles back into roof planes, drops vertices on straight edges and closes seams between planes. |
| `build_city.py` | Today's city: the 3D roofs in compact gzip form (the game raises the walls from the roof edges, skipping the ones another roof hides), roof colours from the aerial photo, the 1 m ground as AVIF with a small JPEG fallback, trees by species and age, and each bridge's axis and stretch over the Rhine. |
| `build_1615.py` | Basel in 1615: traces every house on the plan into its own outline, builds the wall, towers and gates, plants garden and orchard trees, and paints the 1 m ground from the plan's own pixels (the Rhine beyond the plan follows the aerial photo). |
| `assemble.py` | Injects both cities into the template; with a sixth argument it also writes the Artifact copy without the page wrapper. |

Credits shown in the game: 3D buildings (swissBUILDINGS3D) and aerial imagery (SWISSIMAGE) © swisstopo; landmarks,
bridges and places © OpenStreetMap contributors (ODbL); tree register (Baumkataster) and the 1615 plan and wall line:
Quelle: Geodaten Kanton Basel-Stadt (CC BY 4.0).
