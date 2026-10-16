"""Assemble the single-file game from the editable template and the generated city data.

Usage: python3 -I secret-pigeon/build/assemble.py <game.template.html> <basel-data.js> <basel-1615.js> <out.html> [<artifact-out.html>]
The optional artifact copy drops the page wrapper (doctype, html, head, body), which the Artifact host adds itself.
"""
import sys

WRAPPER = {"<!doctype html>", '<html lang="en">', "<head>", '<meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">',
           "</head>", "<body>", "</body>", "</html>"}


def main(template, today, y1615, out, artifact=None):
    page = open(template, encoding="utf-8").read()
    for marker, path in (("/*@BASEL_DATA@*/", today), ("/*@BASEL1615_DATA@*/", y1615)):
        if page.count(marker) != 1:
            raise SystemExit(f"template must contain exactly one {marker} marker")
        page = page.replace(marker, open(path, encoding="utf-8").read().strip())
    with open(out, "w", encoding="utf-8") as f:
        f.write(page)
    if artifact:
        with open(artifact, "w", encoding="utf-8") as f:
            f.write("\n".join(line for line in page.split("\n") if line.strip() not in WRAPPER))
    print(f"wrote {out} ({len(page.encode()) / 1e6:.2f} MB)")


if __name__ == "__main__":
    if len(sys.argv) not in (5, 6):
        raise SystemExit(__doc__)
    main(*sys.argv[1:])
