"""Docks as obstacles for the distance-from-shore computation.

Idaho's shoreline rules (and common sense on the water) measure distance from
docks and floats, not just the bank — and docks reach out into the lake, so
they shrink the "open water" zone, especially in dock-lined bays. This module
supplies dock geometry from three sources and folds it into the water mask so
the existing distance transform measures distance to *shore-or-dock*:

    water_mask &= ~dock_mask   →   distance_from_shore_m now respects docks

Sources
-------
- OpenStreetMap  man_made=pier/dock  (free, partial — misses many private docks)
- a user file    (points or lines, any CRS) — hand-digitized or reviewed CV output
- imagery CV     extract_docks_from_imagery() — screening-grade auto-extraction
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

import geopandas as gpd
import numpy as np
import shapely
from rasterio import features

from .config import CRS_UTM


def burn_docks(mask: np.ndarray, dock_geoms, transform, width_m: float = 4.0) -> np.ndarray:
    """Rasterize dock geometries and remove them from the water mask.

    Returns a new mask where dock footprints are no longer water, so the
    distance transform treats a dock as shore. Points/lines are buffered to
    width_m so a thin dock still occupies at least one cell.
    """
    geoms = [g.buffer(width_m / 2) for g in dock_geoms if g is not None and not g.is_empty]
    if not geoms:
        return mask
    dock_mask = features.geometry_mask(geoms, mask.shape, transform, invert=True)
    return mask & ~dock_mask


def docks_from_osm(bbox_lonlat, timeout: int = 30) -> gpd.GeoDataFrame:
    """Fetch man_made=pier/dock ways from OpenStreetMap for a lon/lat bbox
    (min_lon, min_lat, max_lon, max_lat). Free but partial coverage."""
    s, w, n, e = bbox_lonlat[1], bbox_lonlat[0], bbox_lonlat[3], bbox_lonlat[2]
    q = (
        f'[out:json][timeout:{timeout}];'
        f'(way["man_made"~"pier|dock"]({s},{w},{n},{e}););out geom;'
    )
    url = "https://overpass-api.de/api/interpreter"
    data = urllib.parse.urlencode({"data": q}).encode()
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "lakezones/0.1"})
    with urllib.request.urlopen(req, timeout=timeout + 10) as r:
        payload = json.load(r)
    geoms = []
    for el in payload.get("elements", []):
        g = el.get("geometry")
        if g and len(g) >= 2:
            geoms.append(shapely.LineString([(p["lon"], p["lat"]) for p in g]))
    gdf = gpd.GeoDataFrame(geometry=geoms, crs="EPSG:4326")
    return gdf.to_crs(CRS_UTM)


def load_dock_file(path: str) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(path).to_crs(CRS_UTM)
    gdf["geometry"] = shapely.force_2d(gdf.geometry)
    return gdf


def extract_docks_from_imagery(
    img: np.ndarray,
    transform,
    water_mask: np.ndarray,
    *,
    shore_reach_m: float = 70.0,
    bright_pct: float = 92.0,
    min_area_px: int = 8,
    max_area_px: int = 1500,
    min_reach_m: float = 8.0,
    min_elongation: float = 1.8,
    cell_m: float = 1.0,
) -> gpd.GeoDataFrame:
    """Screening-grade dock extraction from an aerial/satellite tile.

    Docks read as bright, small, elongated structures sitting on dark water
    within a short reach of shore. We look for bright pixels inside the water,
    within `shore_reach_m` of the bank, cluster them, size-filter, and take each
    cluster's waterward-most pixel as the dock point. Expect some false
    positives (moored boats, swim floats, wakes) — review before production use.
    """
    from scipy import ndimage
    from skimage import measure

    gray = img[..., :3].mean(axis=2) if img.ndim == 3 else img.astype(float)

    # distance from shore (in metres) to keep only near-shore structures
    dist_px = ndimage.distance_transform_edt(water_mask)
    near_shore = water_mask & (dist_px * cell_m <= shore_reach_m)

    water_vals = gray[water_mask]
    thresh = np.percentile(water_vals, bright_pct)
    bright = (gray >= thresh) & near_shore

    lbl = measure.label(bright)
    rows, cols, tips = [], [], []
    for reg in measure.regionprops(lbl):
        if not (min_area_px <= reg.area <= max_area_px):
            continue
        # a dock reaches OUT into the water; a bright beach fringe sits AT the
        # bank. Require the cluster's farthest pixel to be well offshore.
        coords = reg.coords
        d = dist_px[coords[:, 0], coords[:, 1]]
        reach = float(d.max() * cell_m)
        if reach < min_reach_m:
            continue
        # a dock is elongated (long axis >> short axis); reject blobby beach/sand
        if reg.minor_axis_length > 0 and (reg.major_axis_length / reg.minor_axis_length) < min_elongation:
            continue
        tip = coords[np.argmax(d)]
        rows.append(tip[0])
        cols.append(tip[1])
        tips.append(reach)
    xs, ys = transform * (np.array(cols) + 0.5, np.array(rows) + 0.5)
    gdf = gpd.GeoDataFrame(
        {"reach_m": tips}, geometry=shapely.points(np.asarray(xs), np.asarray(ys)), crs=CRS_UTM
    )
    return gdf
