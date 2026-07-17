"""Build a per-lake depth raster from labeled bathymetric contours.

Method (screening-grade, matches the Minnesota DNR lake-DEM convention):
TIN linear interpolation over densified contour vertices plus the lake
shoreline (and island shores) burned in as depth 0.
"""

from __future__ import annotations

import numpy as np
import geopandas as gpd
import shapely
from rasterio import features
from rasterio.transform import from_origin
from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator


def build_mask_raster(lake_poly: shapely.Geometry, cell: float = 10.0, margin_cells: int = 4):
    """Rasterize a lake polygon to a boolean water mask + affine transform.

    Depth-free: this is all the geometry-only pipeline (distance from shore,
    straight runs) needs, so any lake with just an outline can be analyzed.
    """
    minx, miny, maxx, maxy = lake_poly.bounds
    minx = np.floor(minx / cell) * cell - margin_cells * cell
    miny = np.floor(miny / cell) * cell - margin_cells * cell
    maxx = np.ceil(maxx / cell) * cell + margin_cells * cell
    maxy = np.ceil(maxy / cell) * cell + margin_cells * cell
    width = int(round((maxx - minx) / cell))
    height = int(round((maxy - miny) / cell))
    transform = from_origin(minx, maxy, cell, cell)
    mask = features.geometry_mask(
        [lake_poly], out_shape=(height, width), transform=transform, invert=True
    )
    return mask, transform


def _ring_points(poly: shapely.Geometry, densify: float) -> np.ndarray:
    pts = []
    for part in getattr(poly, "geoms", [poly]):
        for ring in [part.exterior, *part.interiors]:
            seg = shapely.segmentize(shapely.LineString(ring), densify)
            pts.append(shapely.get_coordinates(seg))
    return np.vstack(pts)


def build_depth_raster(
    lake_poly: shapely.Geometry,
    contours: gpd.GeoDataFrame,
    cell: float = 10.0,
    densify: float | None = None,
    margin_cells: int = 4,
):
    """Return (depth_ft float32 array with NaN outside, lake mask, affine transform).

    contours must have a depth_ft column; only contours intersecting the lake
    are used, and their vertices are filtered to a small buffer around the lake
    so river contours from the basin-wide dataset don't leak in.
    """
    densify = densify or cell
    mask, transform = build_mask_raster(lake_poly, cell, margin_cells)
    minx, maxy = transform.c, transform.f

    near = contours.iloc[
        contours.sindex.query(lake_poly, predicate="intersects")
    ]
    if near.empty:
        raise ValueError("no bathymetry contours intersect this lake")

    pt_chunks, val_chunks = [], []
    for geom, d in zip(near.geometry, near["depth_ft"]):
        seg = shapely.segmentize(geom, densify)
        coords = shapely.get_coordinates(seg)
        pt_chunks.append(coords)
        val_chunks.append(np.full(len(coords), float(d)))

    shore = _ring_points(lake_poly, densify)
    pt_chunks.append(shore)
    val_chunks.append(np.zeros(len(shore)))

    pts = np.vstack(pt_chunks)
    vals = np.concatenate(val_chunks)

    # keep only points on/near the lake (basin dataset includes river contours)
    inside = shapely.contains_xy(lake_poly.buffer(2 * cell), pts[:, 0], pts[:, 1])
    # never drop the shoreline zeros
    inside[len(pts) - len(shore):] = True
    pts, vals = pts[inside], vals[inside]

    # dedupe to a 1 m grid — keeps Qhull stable and fast; on ties the shallowest
    # value wins so shoreline zeros beat contours that graze the waterline
    order = np.argsort(vals)
    key = np.round(pts[order]).astype(np.int64)
    _, idx = np.unique(key, axis=0, return_index=True)
    pts, vals = pts[order][idx], vals[order][idx]

    interp = LinearNDInterpolator(pts, vals)
    ys, xs = np.nonzero(mask)
    cx = minx + (xs + 0.5) * cell
    cy = maxy - (ys + 0.5) * cell

    depth = np.full(mask.shape, np.nan, dtype=np.float32)
    step = 500_000
    for i in range(0, len(xs), step):
        sl = slice(i, i + step)
        depth[ys[sl], xs[sl]] = interp(np.c_[cx[sl], cy[sl]])

    holes = mask & ~np.isfinite(depth)
    if holes.any():
        nn = NearestNDInterpolator(pts, vals)
        hy, hx = np.nonzero(holes)
        depth[hy, hx] = nn(np.c_[minx + (hx + 0.5) * cell, maxy - (hy + 0.5) * cell])

    np.clip(depth, 0.0, None, out=depth)
    depth[~mask] = np.nan
    return depth, mask, transform
