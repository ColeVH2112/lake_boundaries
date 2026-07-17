#!/usr/bin/env python3
"""Extract candidate dock points from aerial imagery for a lake (screening-grade).

Fetches ESRI World Imagery tiles over the lake, runs the CV extractor inside the
NHD water mask, and writes candidate dock points to GeoJSON. Output is meant to
be REVIEWED (delete false positives from boats/beaches, add missed docks) before
feeding it to `lakezones run --docks`. Also merges any OpenStreetMap piers.

Usage:
    python scripts/extract_docks.py "Lake Hayden" --huc 17010305 17010303 \
        --out data/docks/hayden_docks.geojson
"""

from __future__ import annotations

import argparse
import math
import urllib.request
from pathlib import Path

import geopandas as gpd
import matplotlib.image as mpimg
import numpy as np
import pandas as pd
import shapely
from rasterio import features
from rasterio.transform import from_bounds

from lakezones.config import CRS_UTM
from lakezones.data import get_lake_polygon, load_waterbodies
from lakezones.docks import docks_from_osm, extract_docks_from_imagery

EXPORT = (
    "https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/"
    "MapServer/export?bbox={0},{1},{2},{3}&bboxSR=26911&imageSR=26911"
    "&size={4},{5}&format=jpg&f=image"
)
TILE_M = 1600      # tile span in metres
TILE_PX = 2048     # ~0.8 m/px


def fetch_tile(bbox, cache: Path):
    if not (cache.exists() and cache.stat().st_size > 10000):
        cache.parent.mkdir(parents=True, exist_ok=True)
        h = int(round(TILE_PX * (bbox[3] - bbox[1]) / (bbox[2] - bbox[0])))
        urllib.request.urlretrieve(EXPORT.format(*bbox, TILE_PX, h), cache)
    return mpimg.imread(cache)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("lake")
    ap.add_argument("--huc", nargs="+", default=["17010305", "17010303"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-osm", action="store_true")
    args = ap.parse_args()

    wb = load_waterbodies(args.huc)
    poly = get_lake_polygon(wb, args.lake)
    minx, miny, maxx, maxy = poly.bounds

    tmp = Path(args.out).parent / "_tiles"
    all_pts = []
    nx = math.ceil((maxx - minx) / TILE_M)
    ny = math.ceil((maxy - miny) / TILE_M)
    print(f"{args.lake}: {nx}x{ny} tiles @ ~{TILE_M/TILE_PX:.2f} m/px")
    for j in range(ny):
        for i in range(nx):
            bbox = (minx + i * TILE_M, miny + j * TILE_M,
                    min(maxx, minx + (i + 1) * TILE_M), min(maxy, miny + (j + 1) * TILE_M))
            clip = poly.intersection(shapely.box(*bbox))
            if clip.is_empty:
                continue
            img = fetch_tile(bbox, tmp / f"t_{i}_{j}.jpg")
            th, tw = img.shape[:2]
            tf = from_bounds(*bbox, tw, th)
            wmask = features.geometry_mask([poly], (th, tw), tf, invert=True)
            if wmask.sum() < 50:
                continue
            pts = extract_docks_from_imagery(img, tf, wmask, cell_m=(bbox[2] - bbox[0]) / tw)
            if len(pts):
                all_pts.append(pts)
    cv = gpd.GeoDataFrame(pd.concat(all_pts, ignore_index=True), crs=CRS_UTM) if all_pts \
        else gpd.GeoDataFrame(geometry=[], crs=CRS_UTM)
    cv["source"] = "cv"
    print(f"  CV candidate docks: {len(cv)}")

    parts = [cv]
    if not args.no_osm:
        try:
            tf = __import__("pyproj").Transformer.from_crs(CRS_UTM, "EPSG:4326", always_xy=True)
            lo0, la0 = tf.transform(minx, miny); lo1, la1 = tf.transform(maxx, maxy)
            osm = docks_from_osm((lo0, la0, lo1, la1))
            osm["source"] = "osm"
            osm = osm[osm.intersects(poly.buffer(50))]
            print(f"  OSM docks: {len(osm)}")
            parts.append(osm[["source", "geometry"]])
        except Exception as e:  # noqa: BLE001
            print(f"  OSM fetch skipped: {e}")

    out = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=CRS_UTM)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    out.to_crs("EPSG:4326").to_file(args.out, driver="GeoJSON")
    print(f"wrote {len(out)} dock features -> {args.out}  (REVIEW before use)")


if __name__ == "__main__":
    main()
