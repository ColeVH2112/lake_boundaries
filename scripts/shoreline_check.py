#!/usr/bin/env python3
"""Ground-truth a lake's shoreline and distance zones against aerial imagery.

Fetches ESRI World Imagery for the lake's bounding box and overlays the NHD
shoreline plus one or more "distance from shore" rings, so you can visually
confirm (a) the outline matches the real waterline and (b) which water clears
each distance threshold. Useful because the distance test is only as good as
the shoreline it measures from.

Usage:
    python scripts/shoreline_check.py "Lake Hayden" --huc 17010305 17010303 \
        --dist-ft 300 500 --out out/lake_hayden/imagery_300_500.png
"""

from __future__ import annotations

import argparse
import urllib.request
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.image as mpimg
import matplotlib.pyplot as plt

from lakezones.config import M_PER_FT, ACRES_PER_M2
from lakezones.data import get_lake_polygon, load_waterbodies

IMAGERY = (
    "https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/"
    "MapServer/export?bbox={minx},{miny},{maxx},{maxy}"
    "&bboxSR=26911&imageSR=26911&size={w},{h}&format=jpg&f=image"
)
RING_COLORS = ["#3bd16f", "#e5484d", "#ffa94d", "#845ef7"]


def fetch_imagery(bbox, w, h, cache: Path) -> Path:
    if cache.exists() and cache.stat().st_size > 10000:
        return cache
    cache.parent.mkdir(parents=True, exist_ok=True)
    url = IMAGERY.format(minx=bbox[0], miny=bbox[1], maxx=bbox[2], maxy=bbox[3], w=w, h=h)
    urllib.request.urlretrieve(url, cache)
    return cache


def draw_rings(ax, geom, color, label):
    first = True
    for part in getattr(geom, "geoms", [geom]):
        if part.is_empty:
            continue
        xs, ys = part.exterior.xy
        ax.plot(xs, ys, color=color, lw=2.0, label=label if first else None)
        first = False
        for r in part.interiors:
            ix, iy = r.xy
            ax.plot(ix, iy, color=color, lw=2.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("lake")
    ap.add_argument("--huc", nargs="+", default=["17010305", "17010303"])
    ap.add_argument("--dist-ft", nargs="+", type=float, default=[300, 500])
    ap.add_argument("--out", required=True)
    ap.add_argument("--width-px", type=int, default=1800)
    args = ap.parse_args()

    wb = load_waterbodies(args.huc)
    poly = get_lake_polygon(wb, args.lake)
    minx, miny, maxx, maxy = poly.bounds
    mx, my = 0.03 * (maxx - minx), 0.03 * (maxy - miny)
    bbox = (minx - mx, miny - my, maxx + mx, maxy + my)
    w = args.width_px
    h = int(round(w * (bbox[3] - bbox[1]) / (bbox[2] - bbox[0])))

    out = Path(args.out)
    img_path = out.with_name(out.stem + "_basemap.jpg")
    img = mpimg.imread(fetch_imagery(bbox, w, h, img_path))

    fig, ax = plt.subplots(figsize=(11, 11 * h / w + 1), dpi=140)
    ax.imshow(img, extent=(bbox[0], bbox[2], bbox[1], bbox[3]), origin="upper")
    xs, ys = poly.exterior.xy
    ax.plot(xs, ys, color="#ffd166", lw=1.8, label="NHD shoreline")
    for r in poly.interiors:
        ix, iy = r.xy
        ax.plot(ix, iy, color="#ffd166", lw=1.5)

    print(f"{args.lake}: {poly.area * ACRES_PER_M2:,.0f} ac")
    for i, ft in enumerate(sorted(args.dist_ft)):
        ring = poly.buffer(-ft * M_PER_FT)
        area = ring.area * ACRES_PER_M2 if not ring.is_empty else 0.0
        draw_rings(ax, ring, RING_COLORS[i % len(RING_COLORS)], f"{ft:.0f} ft from shore")
        print(f"  >= {ft:.0f} ft from shore: {area:,.0f} ac ({100 * area / (poly.area * ACRES_PER_M2):.0f}%)")

    mile = 1609.34
    ax.plot([bbox[0] + 300, bbox[0] + 300 + mile], [bbox[1] + 300, bbox[1] + 300], "w-", lw=3)
    ax.text(bbox[0] + 300 + mile / 2, bbox[1] + 450, "1 mile", color="w", ha="center", weight="bold")
    ax.legend(loc="upper left", fontsize=10, framealpha=0.9)
    ax.set_title(f"{args.lake} — NHD shoreline vs distance-from-shore, on aerial imagery")
    ax.axis("off")
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, bbox_inches="tight", facecolor="white")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
