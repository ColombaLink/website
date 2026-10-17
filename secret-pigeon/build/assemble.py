"""Assemble the single-file game from the editable template and the generated city data.

Usage: python3 -I secret-pigeon/build/assemble.py <game.template.html> <basel-data.js> <basel-1615.js> <out.html> [<artifact-out.html>]
<out.html> is the colomba.link copy: the site promises no third-party scripts or fonts, so it loads three.js and the
fonts from the game's own vendor/ and fonts/ folders, and it gets the parts in site-parts.html (share previews, a
byline). The optional artifact copy keeps the CDN links its host allows, leaves those parts out and drops the page
wrapper (doctype, html, head, body), which the Artifact host adds itself.
"""
import os
import re
import sys

WRAPPER = {"<!doctype html>", '<html lang="en">', "<head>", '<meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">',
           "</head>", "<body>", "</body>", "</html>"}

# what the website copy swaps, each found exactly once in the template
SITE_SWAPS = [
    ("<title>Secret Pigeon</title>", "<title>Secret Pigeon – a LoRaWAN flight game over Basel</title>"),
    ('<meta name="claude:viewport-fit-sides" content="cover">\n', ""),
    ('<link rel="preconnect" href="https://fonts.googleapis.com">\n', ""),
    ('<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n', ""),
    ('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible:wght@400;700'
     '&family=Bungee&family=DM+Mono:wght@400;500&display=swap">\n', ""),
    ("https://cdnjs.cloudflare.com/ajax/libs/three.js/0.183.2/three.module.min.js", "/secret-pigeon/vendor/three.module.min.js"),
    (": the game loads three.js from cdnjs.", "."),
]


def site_parts(path):
    text = open(path, encoding="utf-8").read()
    names = re.findall(r"^<!--#(\w+)-->$", text, flags=re.M)
    bodies = re.split(r"^<!--#\w+-->\n", text, flags=re.M)[1:]
    return {name: body.rstrip("\n") for name, body in zip(names, bodies)}


def main(template, today, y1615, out, artifact=None):
    page = open(template, encoding="utf-8").read()
    for marker, path in (("/*@BASEL_DATA@*/", today), ("/*@BASEL1615_DATA@*/", y1615)):
        if page.count(marker) != 1:
            raise SystemExit(f"template must contain exactly one {marker} marker")
        page = page.replace(marker, open(path, encoding="utf-8").read().strip())
    parts = site_parts(os.path.join(os.path.dirname(os.path.abspath(__file__)), "site-parts.html"))
    site = page
    for old, new in SITE_SWAPS:
        if site.count(old) != 1:
            raise SystemExit(f"the website swap expects exactly one {old[:60]!r} in the template")
        site = site.replace(old, new)
    for name, body in parts.items():
        marker = f"<!--@{name}@-->"
        if page.count(marker) != 1:
            raise SystemExit(f"template must contain exactly one {marker} marker")
        indent = re.search(r"^([ \t]*)" + re.escape(marker), page, flags=re.M).group(1)
        site = site.replace(marker, body.replace("\n", "\n" + indent))
        page = page.replace(indent + marker + "\n", "")
    with open(out, "w", encoding="utf-8") as f:
        f.write(site)
    if artifact:
        with open(artifact, "w", encoding="utf-8") as f:
            f.write("\n".join(line for line in page.split("\n") if line.strip() not in WRAPPER))
    print(f"wrote {out} ({len(site.encode()) / 1e6:.2f} MB)")


if __name__ == "__main__":
    if len(sys.argv) not in (5, 6):
        raise SystemExit(__doc__)
    main(*sys.argv[1:])
