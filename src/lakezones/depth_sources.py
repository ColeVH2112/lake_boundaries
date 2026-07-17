"""Pluggable depth sources.

Every source resolves to `(depth_ft_array_or_None, mask, transform)` for a given
lake polygon, so the rest of the pipeline is source-agnostic. This is what makes
the tool portable: a lake needs only an outline (geometry-only) and *optionally*
one of these depth sources.

Sources
-------
none      : geometry-only — distance-from-shore + straight-run, no depth criterion
contours  : any line layer with a depth field (the CdA Tribe data is one instance;
            digitized nautical maps produce the same shape — see digitize.py)
raster    : a prebuilt depth GeoTIFF (ft, positive down), e.g. from a sonar survey
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import shapely

from .config import CRS_UTM
from .depth import build_depth_raster, build_mask_raster


def from_none(lake_poly, cell=10.0, **_):
    mask, transform = build_mask_raster(lake_poly, cell)
    return None, mask, transform


def from_contours(lake_poly, contours: gpd.GeoDataFrame, cell=10.0, densify=None, **_):
    """contours: GeoDataFrame with a `depth_ft` column (ft below full pool / surface)."""
    if "depth_ft" not in contours.columns:
        raise ValueError("contour layer needs a 'depth_ft' column")
    hits = contours.sindex.query(lake_poly, predicate="intersects")
    if len(hits) == 0:
        # no depth here — fall back to geometry-only rather than failing the lake
        return from_none(lake_poly, cell=cell)
    depth, mask, transform = build_depth_raster(lake_poly, contours, cell=cell, densify=densify)
    return depth, mask, transform


def from_contour_file(lake_poly, path, depth_field="depth_ft", cell=10.0, densify=None, **_):
    gdf = gpd.read_file(path).to_crs(CRS_UTM)
    gdf["geometry"] = shapely.force_2d(gdf.geometry)
    if depth_field != "depth_ft":
        gdf = gdf.rename(columns={depth_field: "depth_ft"})
    gdf["depth_ft"] = gdf["depth_ft"].astype(float)
    return from_contours(lake_poly, gdf, cell=cell, densify=densify)


def from_raster(lake_poly, path, cell=10.0, **_):
    """Sample an existing depth GeoTIFF onto the lake grid (nearest, reprojected)."""
    import rasterio
    from rasterio.warp import Resampling, reproject

    mask, transform = build_mask_raster(lake_poly, cell)
    dst = np.full(mask.shape, np.nan, dtype="float32")
    with rasterio.open(path) as src:
        reproject(
            source=rasterio.band(src, 1),
            destination=dst,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=transform,
            dst_crs=CRS_UTM,
            resampling=Resampling.bilinear,
        )
    dst[~mask] = np.nan
    return dst, mask, transform


def resolve(kind: str, lake_poly, *, contours=None, path=None, depth_field="depth_ft",
            cell=10.0, densify=None):
    if kind == "none":
        return from_none(lake_poly, cell=cell)
    if kind == "contours":
        if contours is None:
            raise ValueError("depth-source 'contours' needs the tribal contour layer")
        return from_contours(lake_poly, contours, cell=cell, densify=densify)
    if kind == "contour-file":
        return from_contour_file(lake_poly, path, depth_field=depth_field, cell=cell, densify=densify)
    if kind == "raster":
        return from_raster(lake_poly, Path(path), cell=cell)
    raise ValueError(f"unknown depth source {kind!r}")
