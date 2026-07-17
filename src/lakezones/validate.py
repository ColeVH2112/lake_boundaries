"""Cross-check our interpolated depth surface against Idaho DEQ's published
20-ft depth contour for Lake Coeur d'Alene."""

from __future__ import annotations

import numpy as np
import geopandas as gpd
import shapely
from skimage import measure

from .config import CRS_UTM, DATA_RAW


def _our_20ft_contour(depth: np.ndarray, mask: np.ndarray, transform) -> list[shapely.LineString]:
    field = np.where(mask & np.isfinite(depth), depth, 0.0)
    lines = []
    for arr in measure.find_contours(field, 20.0):
        # (row, col) -> world; find_contours returns positions in array index space
        cols, rows = arr[:, 1], arr[:, 0]
        xs, ys = transform * (cols + 0.5, rows + 0.5)
        if len(xs) >= 2:
            lines.append(shapely.LineString(np.c_[xs, ys]))
    return lines


def validate_cda_20ft(cell: float = 10.0, densify: float | None = None) -> dict:
    from .data import get_lake_polygon, load_bathy_contours, load_waterbodies
    from .depth import build_depth_raster

    deq_path = DATA_RAW / "deq_depth20ft" / "cda_depth_20ft.geojson"
    deq = gpd.read_file(deq_path).to_crs(CRS_UTM)
    # the DEQ service holds contours at every 20-ft interval; keep only 20 ft
    deq = deq[deq["Depth"] == 20]

    wb, contours = load_waterbodies(), load_bathy_contours()
    poly = get_lake_polygon(wb, "Coeur d'Alene Lake", contours=contours)
    depth, mask, transform = build_depth_raster(poly, contours, cell=cell, densify=densify)

    # the DEQ layer spans the whole basin (chain lakes, rivers) in thousands of
    # small fragments — keep only fragments on the CdA lake polygon itself
    deq = deq.explode(index_parts=False)
    deq = deq.iloc[deq.sindex.query(poly, predicate="intersects")]

    ours = _our_20ft_contour(depth, mask, transform)
    if not ours:
        return {"error": "no 20 ft contour produced"}
    tree = shapely.STRtree(ours)

    # DEQ vertices are ~0.5 m apart; decimate to ~50 m spacing before sampling
    offsets = []
    for geom in deq.geometry:
        for part in getattr(geom, "geoms", [geom]):
            coords = shapely.get_coordinates(part)
            step = max(1, int(round(50.0 / max(part.length / max(len(coords) - 1, 1), 1e-9))))
            for x, y in coords[::step]:
                pt = shapely.Point(x, y)
                nearest = tree.nearest(pt)
                offsets.append(shapely.distance(pt, ours[nearest]))
    off = np.array(offsets)
    return {
        "deq_vertices_sampled": int(len(off)),
        "median_offset_m": round(float(np.median(off)), 1),
        "p90_offset_m": round(float(np.percentile(off, 90)), 1),
        "max_offset_m": round(float(off.max()), 1),
        "note": "offset from DEQ 20ft contour vertices to our interpolated 20ft contour",
    }
