"""Turn a scanned depth/nautical map into depth contours the pipeline can use.

The rest of lakezones consumes *contours with a depth_ft column*. Digitizing a
scanned map just has to produce that same shape, after which depth rasters,
distance, and straight-run zones all work identically to a lake with native
digital bathymetry. This is the answer to "can it work from just a paper map".

Pipeline
--------
1. georeference — map image pixels to world coordinates. If the scan has no
   coordinate grid (typical for fish-and-game lake maps), pick a handful of
   control points where the drawn shoreline meets identifiable real features
   and pass them to `affine_from_gcps`.
2. isolate each depth contour — by color/threshold — into a boolean line mask.
   (Left to the caller / a notebook, since map styling varies; scikit-image
   HSV thresholding is the usual tool.)
3. `contours_from_labeled` — skeletonize each line to 1 px, convert to world
   coordinates, and emit a GeoDataFrame of depth_ft points ready for
   `depth_sources.from_contours` / `build_depth_raster`.

Accuracy is screening-grade and dominated by the source survey, not the tracing
(see docs/DATA_SOURCES.md). Depth labels on such maps are sparse (~5-25 per
lake), so assigning each isolated line its depth by hand is fast and reliable.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import shapely
from skimage.morphology import skeletonize

from .config import CRS_UTM


def affine_from_gcps(src_px, dst_xy) -> np.ndarray:
    """Least-squares affine mapping (col, row) pixels -> (x, y) world coords.

    Needs >= 3 non-collinear control points. Returns a 2x3 matrix M such that
    [x, y] = M @ [col, row, 1].
    """
    src = np.asarray(src_px, float)
    dst = np.asarray(dst_xy, float)
    if len(src) < 3:
        raise ValueError("need at least 3 ground control points")
    A = np.column_stack([src[:, 0], src[:, 1], np.ones(len(src))])
    mx, *_ = np.linalg.lstsq(A, dst[:, 0], rcond=None)
    my, *_ = np.linalg.lstsq(A, dst[:, 1], rcond=None)
    return np.vstack([mx, my])


def world_from_affine(affine: np.ndarray, cols, rows):
    cols = np.asarray(cols, float)
    rows = np.asarray(rows, float)
    x = affine[0, 0] * cols + affine[0, 1] * rows + affine[0, 2]
    y = affine[1, 0] * cols + affine[1, 1] * rows + affine[1, 2]
    return x, y


def world_from_transform(transform, cols, rows):
    """Pixel-center world coords for an already-georeferenced rasterio transform."""
    cols = np.asarray(cols, float)
    rows = np.asarray(rows, float)
    xs, ys = transform * (cols + 0.5, rows + 0.5)
    return np.asarray(xs), np.asarray(ys)


def _line_points(line_mask: np.ndarray, to_world, depth_ft: float):
    sk = skeletonize(line_mask.astype(bool))
    rows, cols = np.nonzero(sk)
    if len(rows) == 0:
        return None
    x, y = to_world(cols, rows)
    return x, y, np.full(len(x), float(depth_ft))


def contours_from_labeled(labeled: np.ndarray, band_to_depth: dict, to_world) -> gpd.GeoDataFrame:
    """Digitize a labeled contour image into depth_ft points.

    labeled       : 2-D int array; each nonzero value is a contour-line code.
    band_to_depth : {code: depth_ft}.
    to_world      : callable(cols, rows) -> (x, y) world coords, e.g. a partial
                    of world_from_affine or world_from_transform.
    """
    xs, ys, ds = [], [], []
    for code, depth in band_to_depth.items():
        got = _line_points(labeled == code, to_world, depth)
        if got is None:
            continue
        x, y, d = got
        xs.append(x)
        ys.append(y)
        ds.append(d)
    if not xs:
        raise ValueError("no contour pixels found for any labeled band")
    x = np.concatenate(xs)
    y = np.concatenate(ys)
    d = np.concatenate(ds)
    gdf = gpd.GeoDataFrame({"depth_ft": d}, geometry=shapely.points(x, y), crs=CRS_UTM)
    return gdf
