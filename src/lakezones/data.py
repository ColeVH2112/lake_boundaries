"""Loaders for raw datasets: tribal bathymetry contours and NHD waterbody polygons."""

from __future__ import annotations

import numpy as np
import pandas as pd
import geopandas as gpd
import pyogrio
import shapely

from .config import CRS_UTM, DATA_RAW, FULL_POOL_ELEV_FT


def _find_col(df: pd.DataFrame, *names: str) -> str | None:
    lower = {c.lower(): c for c in df.columns}
    for n in names:
        if n.lower() in lower:
            return lower[n.lower()]
    return None


def load_bathy_contours() -> gpd.GeoDataFrame:
    """Load the CdA Tribe/Avista basin bathymetry contours with a clean depth_ft column.

    Depth is feet below the 2,128-ft summer full pool. Records with negative or
    missing depth (upstream river reaches above full pool) are dropped.
    """
    shps = sorted((DATA_RAW / "bathy_cdabasin").rglob("*.shp"))
    if not shps:
        raise FileNotFoundError(
            "Tribal bathymetry shapefile not found — run scripts/fetch_data.py first"
        )
    gdf = gpd.read_file(shps[0])
    gdf = gdf.to_crs(CRS_UTM)

    dcol = _find_col(gdf, "Depth")
    ccol = _find_col(gdf, "CONTOUR")
    depth = pd.to_numeric(gdf[dcol], errors="coerce") if dcol else pd.Series(np.nan, index=gdf.index)
    if ccol is not None:
        from_elev = FULL_POOL_ELEV_FT - pd.to_numeric(gdf[ccol], errors="coerce")
        depth = depth.where(depth.notna() & (depth > 0), from_elev)
    gdf["depth_ft"] = depth
    gdf = gdf[np.isfinite(gdf["depth_ft"]) & (gdf["depth_ft"] >= 0)]
    gdf["geometry"] = shapely.force_2d(gdf.geometry)
    return gdf[["depth_ft", "geometry"]].reset_index(drop=True)


def load_waterbodies(huc8s: list[str] | None = None) -> gpd.GeoDataFrame:
    """Load NHD HR lake/pond/reservoir polygons (FType 390/436) from the HUC8 GPKGs."""
    paths = sorted((DATA_RAW / "nhd").rglob("*.gpkg"))
    if huc8s:
        paths = [p for p in paths if any(h in p.name for h in huc8s)]
    if not paths:
        raise FileNotFoundError("No NHD GPKGs found — run scripts/fetch_data.py first")

    frames = []
    for p in paths:
        layers = [l[0] for l in pyogrio.list_layers(p)]
        lname = next((l for l in layers if l.lower() == "nhdwaterbody"), None)
        if lname is None:
            continue
        df = gpd.read_file(p, layer=lname)
        fcol = _find_col(df, "ftype")
        df = df[pd.to_numeric(df[fcol], errors="coerce").isin([390, 436])]
        ncol = _find_col(df, "gnis_name")
        df["name"] = df[ncol] if ncol else None
        frames.append(df[["name", "geometry"]])
    wb = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=frames[0].crs)
    wb = wb.to_crs(CRS_UTM)
    wb["geometry"] = shapely.force_2d(wb.geometry)
    # drop exact duplicates that appear in overlapping HUC8 deliveries
    wb = wb[~wb.geometry.duplicated()].reset_index(drop=True)
    return wb


def get_lake_polygon(
    wb: gpd.GeoDataFrame,
    name: str,
    contours: gpd.GeoDataFrame | None = None,
    dissolve_touching: bool = True,
) -> shapely.Geometry:
    """Resolve a lake name to a single (Multi)Polygon.

    Duplicate GNIS names (e.g. several 'Blue Lake's) are disambiguated by
    requiring intersection with the bathymetry contours when provided.
    NHD splits large lakes into several touching polygons (main pool, river
    arms, connected lakes); dissolve_touching merges everything connected.
    """
    cand = wb[wb["name"].str.strip().str.lower() == name.strip().lower()]
    if cand.empty:
        cand = wb[wb["name"].str.contains(name, case=False, na=False)]
    if cand.empty:
        raise KeyError(f"No NHD waterbody named {name!r}")

    if contours is not None and len(cand) > 1:
        tree = shapely.STRtree(contours.geometry.values)
        hits = cand[
            cand.geometry.apply(lambda g: len(tree.query(g, predicate="intersects")) > 0)
        ]
        if not hits.empty:
            cand = hits

    # keep the largest candidate plus any same-named piece near it
    cand = cand.assign(_a=cand.geometry.area).sort_values("_a", ascending=False)
    seed = cand.geometry.iloc[0]
    near = cand[cand.geometry.distance(seed) < 200.0]
    geom = shapely.unary_union(near.geometry.values)

    if dissolve_touching:
        included = set(near.index)
        sindex = wb.sindex
        while True:
            idx = sindex.query(geom, predicate="intersects")
            new = [i for i in wb.index[idx] if i not in included]
            if not new:
                break
            included.update(new)
            geom = shapely.unary_union([geom, *wb.geometry.loc[new].values])
    return geom
