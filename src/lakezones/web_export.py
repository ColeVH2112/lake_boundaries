"""Export per-lake depth + distance rasters to compact JSON for the web app.

The browser does the cheap part live (threshold + straight-run) so the sliders
are interactive; Python does the expensive part once (interpolation, distance
transform). Rasters are downsampled to a web-friendly size and packed as
base64 Int16 buffers. Output goes to docs/ so GitHub Pages can serve it.

Also bakes a per-lake aerial-imagery basemap and ships lat/lon bounds, shoreline
length, and de-duped dock pixel coordinates so the frontend can show a real map,
inspect points, and draw docks.
"""

from __future__ import annotations

import base64
import bisect
import gzip
import json
import math
import time
import urllib.request

import geopandas as gpd
import numpy as np
import rasterio
from pyproj import Transformer

from .config import ACRES_PER_M2, CRS_UTM, DOCK_WIDTH_M, M_PER_FT, OUT_DIR, PROJECT_ROOT
from .lakes import DISSOLVE_TOUCHING

DOCS = PROJECT_ROOT / "docs"
WEB_DATA = DOCS / "data"
DOCKS_DIR = PROJECT_ROOT / "data" / "docks"
POLY_DIR = PROJECT_ROOT / "data" / "polygons"  # custom outlines (river reaches etc.)
ZONES_DIR = PROJECT_ROOT / "data" / "zones"    # rule overlays: <slug>_no_wake / <slug>_caution
SENTINEL = -32768
TARGET_MAX_PX = 600
# narrow rivers are all edge: downsampling inflates their acreage tallies, so
# keep them at native resolution (larger payload, honest numbers)
TARGET_MAX_PX_OVERRIDE = {
    "spokane_river_95_bridge_to_post_falls_dam": 1200,
    "spokane_river_post_falls_to_stateline": 1200,
}
# EDT measures to the nearest land-cell CENTER, overstating distance to the true
# shoreline by ~half a cell — negligible on lakes, material on a narrow river.
# Waters listed here get the half-cell correction so acreages reconcile with
# vector buffers (the HLWID ArcGIS model).
EDGE_CORRECTED = {
    "spokane_river_95_bridge_to_post_falls_dam",
    "spokane_river_post_falls_to_stateline",
}
BASEMAP_MAX_PX = 900

_TO_LONLAT = Transformer.from_crs(CRS_UTM, "EPSG:4326", always_xy=True)

IMAGERY_URL = (
    "https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/"
    "MapServer/export?bbox={w},{s},{e},{n}&bboxSR=26911&imageSR=26911"
    "&size={iw},{ih}&format=jpg&f=image"
)


def _dedupe_points(gdf: gpd.GeoDataFrame, radius_m: float = 12.0) -> gpd.GeoDataFrame:
    """Greedy spatial de-dup: collapse multiple detections of one physical dock.
    Points are swept in x order, so only kept points within radius_m in x can
    conflict — a bisect window keeps the greedy result identical but O(n log n)."""
    pts = [g for g in gdf.geometry if g.geom_type == "Point"]
    kept, kx = [], []
    for p in sorted(pts, key=lambda g: (g.x, g.y)):
        lo = bisect.bisect_left(kx, p.x - radius_m)
        if all(p.distance(k) > radius_m for k in kept[lo:]):
            kept.append(p)
            kx.append(p.x)
    return gpd.GeoDataFrame(geometry=kept, crs=gdf.crs)


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


def _int16_grid(arr: np.ndarray) -> np.ndarray:
    return np.where(np.isfinite(arr), np.round(arr), SENTINEL).astype("<i2")


def _b64(a: np.ndarray) -> str:
    return base64.b64encode(a.astype("<i2").tobytes()).decode("ascii")


def _rowdelta(a: np.ndarray) -> np.ndarray:
    """Per-row first differences with int16 wraparound — the JS decoder's
    Int16Array cumulative sum reconstructs this bit-exactly."""
    d = a.astype(np.int32).copy()
    d[:, 1:] = d[:, 1:] - d[:, :-1]
    return d.astype("<i2")


def _gz(s: str) -> int:
    return len(gzip.compress(s.encode("ascii"), 9))


def _pack_best(grid: np.ndarray):
    """Pack an Int16 grid as plain or row-delta, whichever gzips smaller (the
    wire format — GitHub Pages gzips JSON). Returns (b64, enc-or-None)."""
    plain = _b64(grid)
    rd = _b64(_rowdelta(grid))
    return (rd, "rowdelta") if _gz(rd) < _gz(plain) else (plain, None)


def _pack_bits(mask: np.ndarray) -> str:
    """0/1 masks ship 1-bit packed: 16x smaller cached/parsed than Int16."""
    return base64.b64encode(np.packbits(mask.flatten()).tobytes()).decode("ascii")


def _pack_int16(arr: np.ndarray) -> str:
    return _b64(_int16_grid(arr))


def _dock_file(slug: str):
    """Hand-edited dock sets (exported from the site's dock editor and committed
    as <slug>_manual.geojson) take precedence over the CV extraction."""
    manual = DOCKS_DIR / f"{slug}_manual.geojson"
    return manual if manual.exists() else DOCKS_DIR / f"{slug}_cv.geojson"


def _dock_field(poly, slug, native_cell, factor, hh, ww):
    """Dock-aware distance field aligned to the exported grid, plus the deduped
    dock points (UTM). Returns (packed Int16 base64, deduped dock GeoDataFrame)."""
    from .depth import build_mask_raster
    from .docks import burn_docks
    from .zones import distance_from_shore_m

    mask, tr = build_mask_raster(poly, native_cell)
    docks = _dedupe_points(gpd.read_file(_dock_file(slug)).to_crs(CRS_UTM))
    mask_d = burn_docks(mask, docks.geometry.values, tr, width_m=DOCK_WIDTH_M)
    d = distance_from_shore_m(mask_d, native_cell)
    if slug in EDGE_CORRECTED:
        d = np.maximum(d - native_cell / 2, 0.0)
    arr = np.where(mask, d, np.nan)
    ds = _downsample(arr, factor)[:hh, :ww]
    return _int16_grid(ds), docks


def _zone_layer(slug, kind, west, north, web_cell, hh, ww):
    """Rasterize a rule-overlay GeoJSON (designated no-wake zones, caution areas)
    onto the exported grid. Returns (packed Int16 0/1 base64, [zone names]) or None."""
    from rasterio import features
    from rasterio.transform import from_origin

    path = ZONES_DIR / f"{slug}_{kind}.geojson"
    if not path.exists():
        return None
    zones = gpd.read_file(path).to_crs(CRS_UTM)
    if not len(zones):
        return None
    tr = from_origin(west, north, web_cell, web_cell)
    m = features.geometry_mask(zones.geometry.values, (hh, ww), tr, invert=True)
    if not m.any():
        print(f"  ! {kind} zones for {slug} fall entirely outside the grid — skipped")
        return None
    raw = zones["name"] if "name" in zones.columns else [None] * len(zones)
    names = [str(n) if n and str(n) != "nan" else f"zone {i + 1}"
             for i, n in enumerate(raw)]
    return _pack_bits(m), names


def _fetch_basemap(slug, west, south, east, north, ww, hh, retries=5) -> bool:
    """Bake an ESRI World Imagery JPEG covering the exported grid bbox exactly, at
    the same aspect ratio so it aligns pixel-for-pixel. Idempotent; retries on the
    transient 504s the imagery server throws. Returns True if the file is present."""
    out = WEB_DATA / f"{slug}_imagery.jpg"
    if out.exists() and out.stat().st_size > 10000:
        return True
    scale = max(BASEMAP_MAX_PX, TARGET_MAX_PX_OVERRIDE.get(slug, 0)) / max(ww, hh)
    iw, ih = max(1, round(ww * scale)), max(1, round(hh * scale))
    url = IMAGERY_URL.format(w=west, s=south, e=east, n=north, iw=iw, ih=ih)
    out.parent.mkdir(parents=True, exist_ok=True)
    last = None
    for attempt in range(retries):
        try:
            urllib.request.urlretrieve(url, out)
            if out.stat().st_size > 10000:
                # the ArcGIS endpoint ignores compressionQuality; re-encode at
                # q85+optimize (-16% bytes, PSNR ~40 dB — imagery is 80% of wire)
                from PIL import Image
                Image.open(out).convert("RGB").save(out, "JPEG", quality=85, optimize=True)
                return True
        except Exception as e:  # noqa: BLE001 - transient imagery-server errors
            last = e
        if attempt < retries - 1:
            time.sleep(2 * (attempt + 1))
    print(f"  ! basemap fetch failed for {slug}: {last}")
    return False


def export_lake(slug: str, wb=None) -> dict | None:
    from .data import get_lake_polygon

    outdir = OUT_DIR / slug
    stats_path = outdir / "stats.json"
    dist_tifs = sorted(outdir.glob("distance_m_*.tif"))
    if not dist_tifs or not stats_path.exists():
        return None
    stats = json.loads(stats_path.read_text())
    name = stats["lake"]

    with rasterio.open(dist_tifs[0]) as src:
        dist = src.read(1)
        b = src.bounds
        native_cell = src.transform.a
    if slug in EDGE_CORRECTED:
        with np.errstate(invalid="ignore"):
            dist = np.maximum(dist - native_cell / 2, 0.0)

    h, w = dist.shape
    factor = max(1, math.ceil(max(h, w) / TARGET_MAX_PX_OVERRIDE.get(slug, TARGET_MAX_PX)))
    web_cell = native_cell * factor
    dist_ds = _downsample(dist, factor)
    hh, ww = dist_ds.shape

    # geographic extent of the exported grid (top-left origin, y decreasing south)
    west, north = b.left, b.top
    east, south = b.left + ww * web_cell, b.top - hh * web_cell
    corners_utm = {"nw": (west, north), "ne": (east, north),
                   "se": (east, south), "sw": (west, south)}
    bounds_lonlat = {k: [round(v, 6) for v in _TO_LONLAT.transform(x, y)]
                     for k, (x, y) in corners_utm.items()}

    # lake polygon (matches the run's resolution) for shoreline + dock burn;
    # custom outlines (e.g. clipped river reaches) take precedence over NHD lookup
    poly = None
    shoreline_ft = None
    custom = POLY_DIR / f"{slug}.geojson"
    if custom.exists():
        from .data import polygon_from_file

        poly = polygon_from_file(custom)
    elif wb is not None:
        try:
            poly = get_lake_polygon(wb, name, dissolve_touching=name in DISSOLVE_TOUCHING)
        except Exception as e:  # noqa: BLE001
            print(f"  ! could not resolve NHD polygon for {name!r}: {e} — "
                  "exporting without shoreline/docks")
            poly = None
    if poly is not None:
        shoreline_ft = round(poly.length / M_PER_FT, 0)

    depth_tifs = sorted(outdir.glob("depth_ft_*.tif"))
    has_depth = bool(depth_tifs)
    enc = {}   # per-key wire encodings the frontend must undo (absent = plain)
    dist_i16 = _int16_grid(dist_ds)
    dist_b64, e = _pack_best(dist_i16)
    if e:
        enc["dist_m"] = e
    depth_b64 = None
    if has_depth:
        with rasterio.open(depth_tifs[0]) as src:
            depth_ds = _downsample(src.read(1), factor)[:hh, :ww]
        depth_b64, e = _pack_best(_int16_grid(depth_ds))
        if e:
            enc["depth_ft"] = e

    payload = {
        "name": name,
        "slug": slug,
        "has_depth": has_depth,
        "width": ww,
        "height": hh,
        "cell_m": round(web_cell, 3),
        "bounds_utm": [west, south, east, north],
        "bounds_lonlat": bounds_lonlat,
        "epsg": 26911,
        "lake_area_acres": stats.get("lake_area_acres"),
        "shoreline_ft": shoreline_ft,
        # native max depth from stats (downsampling would lose the deepest cells)
        "max_depth_ft": stats.get("max_depth_ft"),
        "acre_per_cell": web_cell * web_cell * ACRES_PER_M2,
        "dist_m": dist_b64,
        "depth_ft": depth_b64,
    }

    payload["edge_corrected"] = slug in EDGE_CORRECTED

    has_docks = False
    n_docks = 0
    if poly is not None and _dock_file(slug).exists():
        docks_i16, docks = _dock_field(poly, slug, native_cell, factor, hh, ww)
        # dock field differs from dist_m on few cells -> ship the difference,
        # optionally row-delta'd on top, whichever gzips smallest
        delta = (docks_i16.astype(np.int32) - dist_i16.astype(np.int32)).astype("<i2")
        cands = {None: _b64(docks_i16), "delta": _b64(delta),
                 "delta+rowdelta": _b64(_rowdelta(delta))}
        key = min(cands, key=lambda k: _gz(cands[k]))
        payload["dist_m_docks"] = cands[key]
        if key:
            enc["dist_m_docks"] = key
        # cell index = floor, not round: round() put 3/4 of dock dots in the
        # neighboring cell (a systematic half-cell bias in the editor seed too)
        px = [[math.floor((p.x - west) / web_cell), math.floor((north - p.y) / web_cell)]
              for p in docks.geometry]
        payload["docks"] = [[c, r] for c, r in px if 0 <= c < ww and 0 <= r < hh]
        n_docks = len(docks)
        payload["has_docks"] = True
        payload["n_docks"] = n_docks
        has_docks = True

    n_zones = 0
    for kind, key in (("no_wake", "no_wake"), ("caution", "caution")):
        z = _zone_layer(slug, kind, west, north, web_cell, hh, ww)
        if z is not None:
            payload[key], payload[f"{key}_names"] = z
            enc[key] = "bits"
            n_zones += len(z[1])
    payload["enc"] = enc

    has_imagery = _fetch_basemap(slug, west, south, east, north, ww, hh)
    payload["imagery"] = f"{slug}_imagery.jpg" if has_imagery else None

    WEB_DATA.mkdir(parents=True, exist_ok=True)
    (WEB_DATA / f"{slug}.json").write_text(json.dumps(payload))
    kb = len((WEB_DATA / f"{slug}.json").read_text()) // 1024
    tag = ("depth+dist" if has_depth else "dist only") + (f" +{n_docks} docks" if has_docks else "") \
        + (f" +{n_zones} rule zones" if n_zones else "")
    print(f"[web] {name:<24} {ww}x{hh} @ {web_cell:.0f} m  {tag}"
          f"{'  +imagery' if has_imagery else ''}  ({kb} KB)")
    return {"slug": slug, "name": name, "has_depth": has_depth, "has_docks": has_docks,
            "lake_area_acres": stats.get("lake_area_acres"),
            "max_depth_ft": payload["max_depth_ft"]}


def export_all() -> None:
    from .data import load_waterbodies

    slugs = sorted(p.name for p in OUT_DIR.iterdir() if p.is_dir())
    print("loading NHD waterbodies ...", flush=True)
    wb = load_waterbodies()
    manifest = [m for s in slugs if (m := export_lake(s, wb))]
    # depth lakes first, then by area
    manifest.sort(key=lambda m: (not m["has_depth"], -(m["lake_area_acres"] or 0)))
    WEB_DATA.mkdir(parents=True, exist_ok=True)
    (WEB_DATA / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"\nwrote {WEB_DATA / 'manifest.json'} ({len(manifest)} lakes)")
