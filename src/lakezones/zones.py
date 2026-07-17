"""Deterministic zone analysis on the depth raster.

- distance from shore: Euclidean distance transform of the lake mask
- depth/distance criteria: simple thresholding
- straight-run test: morphological opening with a rotated linear window —
  a pixel survives iff it lies on a fully-qualifying straight segment of the
  requested length at some heading.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

from .config import M_PER_FT


def distance_from_shore_m(mask: np.ndarray, cell: float) -> np.ndarray:
    return ndimage.distance_transform_edt(mask) * cell


def straight_run_mask(mask: np.ndarray, run_px: int, angle_step: float = 5.0) -> np.ndarray:
    """Pixels of mask lying on a straight all-True run of run_px pixels at any heading.

    Rotate-open-unrotate with nearest-neighbour rotation: exact along the
    tested headings, ~1 px positional error from resampling (documented
    screening tolerance). Headings cover [0, 180).
    """
    if run_px <= 1:
        return mask.copy()

    h, w = mask.shape
    diag = int(np.ceil(np.hypot(h, w))) + 2
    y0, x0 = (diag - h) // 2, (diag - w) // 2
    canvas = np.zeros((diag, diag), dtype=np.uint8)
    canvas[y0 : y0 + h, x0 : x0 + w] = mask

    result = np.zeros_like(canvas)
    for theta in np.arange(0.0, 180.0, angle_step):
        if theta == 0.0:
            rot = canvas
        else:
            rot = ndimage.rotate(canvas, theta, reshape=False, order=0, prefilter=False)
        eroded = ndimage.minimum_filter1d(rot, run_px, axis=1, mode="constant", cval=0)
        opened = ndimage.maximum_filter1d(eroded, run_px, axis=1, mode="constant", cval=0)
        if theta != 0.0:
            opened = ndimage.rotate(opened, -theta, reshape=False, order=0, prefilter=False)
        result |= opened

    return result[y0 : y0 + h, x0 : x0 + w].astype(bool) & mask


def compute_zones(
    depth_ft: np.ndarray,
    mask: np.ndarray,
    cell: float,
    min_depth_ft: float,
    min_shore_dist_ft: float,
    run_length_ft: float,
    angle_step: float = 5.0,
) -> dict:
    dist_m = distance_from_shore_m(mask, cell)
    with np.errstate(invalid="ignore"):
        qualifying = mask & (depth_ft >= min_depth_ft) & (dist_m >= min_shore_dist_ft * M_PER_FT)

    run_px = max(1, int(round(run_length_ft * M_PER_FT / cell)))
    runs = straight_run_mask(qualifying, run_px, angle_step)

    return {"qualifying": qualifying, "runs": runs, "distance_m": dist_m, "run_px": run_px}
