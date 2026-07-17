"""Export per-lake depth + distance rasters to compact JSON for the web app.

The browser does the cheap part live (threshold + straight-run) so the sliders
are interactive; Python does the expensive part once (interpolation, distance
transform). Rasters are downsampled to a web-friendly size and packed as
base64 Int16 buffers. Output goes to docs/ so GitHub Pages can serve it.
"""

from __future__ import annotations

import base64
import json
import math
from pathlib import Path

import numpy as np
import rasterio

from .config import ACRES_PER_M2, OUT_DIR, PROJECT_ROOT
from .lakes import slugify

DOCS = PROJECT_ROOT / "docs"
WEB_DATA = DOCS / "data"
SENTINEL = -32768
TARGET_MAX_PX = 600


def _downsample(arr: np.ndarray, factor: int) -> np.ndarray:
    if factor <= 1:
        return arr
    h, w = arr.shape
    ph, pw = (-h) % factor, (-w) % factor
    if ph or pw:
        arr = np.pad(arr, ((0, ph), (0, pw)), constant_values=np.nan)
    hh, ww = arr.shape
    blocks = arr.reshape(hh // factor, factor, ww // factor, factor)
    with np.errstate(invalid="ignore"):
        return np.nanmean(blocks, axis=(1, 3))


def _pack_int16(arr: np.ndarray) -> str:
    out = np.where(np.isfinite(arr), np.round(arr), SENTINEL)
    return base64.b64encode(out.astype("<i2").tobytes()).decode("ascii")


def export_lake(slug: str) -> dict | None:
    outdir = OUT_DIR / slug
    stats_path = outdir / "stats.json"
    dist_tifs = sorted(outdir.glob("distance_m_*.tif"))
    if not dist_tifs or not stats_path.exists():
        return None
    stats = json.loads(stats_path.read_text())

    with rasterio.open(dist_tifs[0]) as src:
        dist = src.read(1)
        b = src.bounds
        native_cell = src.transform.a

    h, w = dist.shape
    factor = max(1, math.ceil(max(h, w) / TARGET_MAX_PX))
    web_cell = native_cell * factor
    dist_ds = _downsample(dist, factor)
    hh, ww = dist_ds.shape

    depth_tifs = sorted(outdir.glob("depth_ft_*.tif"))
    has_depth = bool(depth_tifs)
    depth_b64 = None
    max_depth = None
    if has_depth:
        with rasterio.open(depth_tifs[0]) as src:
            depth_ds = _downsample(src.read(1), factor)
        # align shapes (padding can differ by rounding)
        depth_ds = depth_ds[:hh, :ww]
        depth_b64 = _pack_int16(depth_ds)
        finite = np.isfinite(depth_ds)
        max_depth = float(np.nanmax(depth_ds)) if finite.any() else None

    payload = {
        "name": stats["lake"],
        "slug": slug,
        "has_depth": has_depth,
        "width": ww,
        "height": hh,
        "cell_m": round(web_cell, 3),
        "bounds_utm": [b.left, b.bottom, b.left + ww * web_cell, b.top - hh * web_cell],
        "epsg": 26911,
        "lake_area_acres": stats.get("lake_area_acres"),
        "max_depth_ft": round(max_depth, 1) if max_depth is not None else None,
        "acre_per_cell": web_cell * web_cell * ACRES_PER_M2,
        "dist_m": _pack_int16(dist_ds),
        "depth_ft": depth_b64,
    }
    WEB_DATA.mkdir(parents=True, exist_ok=True)
    (WEB_DATA / f"{slug}.json").write_text(json.dumps(payload))
    kb = len((WEB_DATA / f"{slug}.json").read_text()) // 1024
    print(f"[web] {stats['lake']:<24} {ww}x{hh} @ {web_cell:.0f} m  "
          f"{'depth+dist' if has_depth else 'dist only'}  ({kb} KB)")
    return {"slug": slug, "name": stats["lake"], "has_depth": has_depth,
            "lake_area_acres": stats.get("lake_area_acres"),
            "max_depth_ft": payload["max_depth_ft"]}


def export_all() -> None:
    slugs = sorted(p.name for p in OUT_DIR.iterdir() if p.is_dir())
    manifest = [m for s in slugs if (m := export_lake(s))]
    # depth lakes first, then by area
    manifest.sort(key=lambda m: (not m["has_depth"], -(m["lake_area_acres"] or 0)))
    WEB_DATA.mkdir(parents=True, exist_ok=True)
    (WEB_DATA / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"\nwrote {WEB_DATA / 'manifest.json'} ({len(manifest)} lakes)")
