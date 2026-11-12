"""Pack country flags and outlines into the app's assets. A developer tool, run by hand.

    uv run python tools/geo_import.py FLAG_ICONS_PACKAGE NATURAL_EARTH_GEOJSON

- Flags: flag-icons (github.com/lipis/flag-icons, MIT, (c) Panayiotis Lipiridis), the
  4x3 SVGs, written to `clipper/assets/flags.json.gz` with the licence beside it.
- Country outlines: Natural Earth 1:110m admin 0 countries (naturalearthdata.com,
  public domain), rounded to a hundredth of a degree, written to
  `clipper/assets/countries.json.gz`.
"""

import gzip
import json
import re
import shutil
import sys
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[1] / "clipper" / "assets"


def flags(package: Path) -> None:
    folder = package / "flags" / "4x3"
    out = {}
    for path in sorted(folder.glob("*.svg")):
        svg = re.sub(r">\s+<", "><", path.read_text().strip())
        if re.search(r"<\s*(script|image|foreignObject)|href\s*=\s*\"http", svg, re.I):
            print("skipped", path.name)
            continue
        out[path.stem] = svg
    (ASSETS / "flags.json.gz").write_bytes(gzip.compress(json.dumps(out, separators=(",", ":")).encode()))
    shutil.copy(package / "LICENSE", ASSETS / "flags-LICENSE.txt")
    print(f"{len(out)} flags")


def countries(geojson: Path) -> None:
    data = json.loads(geojson.read_text())
    out = {}
    for feature in data["features"]:
        p = feature["properties"]
        code = (p.get("ISO_A2_EH") or p.get("ISO_A2") or "").lower()
        if not re.fullmatch(r"[a-z]{2}", code):
            continue
        geometry = feature["geometry"]
        polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
        rings = []
        for polygon in polygons:
            ring = polygon[0]            # the outline; holes are too small to matter at this scale
            rings.append([[round(x, 2), round(y, 2)] for x, y in ring])
        names = {p.get(k, "") for k in ("NAME", "NAME_LONG", "ADMIN", "NAME_EN", "FORMAL_EN", "NAME_SORT")}
        out[code] = {"name": p["NAME"], "names": sorted(n.lower() for n in names if n),
                     "continent": p.get("CONTINENT", ""), "rings": rings}
    (ASSETS / "countries.json.gz").write_bytes(gzip.compress(json.dumps(out, separators=(",", ":")).encode()))
    print(f"{len(out)} countries")


if __name__ == "__main__":
    flags(Path(sys.argv[1]))
    countries(Path(sys.argv[2]))
