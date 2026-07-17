"""Convert zone masks to vector polygons and GeoJSON."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import shapely.geometry
from rasterio import features

from .config import ACRES_PER_M2, CRS_UTM


def mask_to_gdf(
    mask: np.ndarray,
    transform,
    min_area_acres: float = 0.5,
    simplify_m: float | None = 5.0,
    **props,
) -> gpd.GeoDataFrame:
    shapes = features.shapes(mask.astype(np.uint8), mask=mask, transform=transform)
    geoms = [shapely.geometry.shape(g) for g, v in shapes if v == 1]
    gdf = gpd.GeoDataFrame(geometry=geoms, crs=CRS_UTM)
    if gdf.empty:
        return gdf
    gdf["area_acres"] = gdf.area * ACRES_PER_M2
    gdf = gdf[gdf["area_acres"] >= min_area_acres]
    if simplify_m:
        gdf["geometry"] = gdf.simplify(simplify_m)
    for k, v in props.items():
        gdf[k] = v
    return gdf.reset_index(drop=True)


def save_geojson(gdf: gpd.GeoDataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if gdf.empty:
        path.write_text('{"type": "FeatureCollection", "features": []}\n')
        return
    gdf.to_crs("EPSG:4326").to_file(path, driver="GeoJSON")
