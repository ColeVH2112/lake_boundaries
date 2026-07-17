"""Static map rendering of depth rasters and zone overlays."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

from .config import CRS_UTM, OUT_DIR

LAND = "#f4f3f1"
SHORE = "#52514e"
QUALIFYING = "#eda100"  # categorical slot 4 (validated pair with slot 8)
RUNS = "#e34948"        # categorical slot 8
TEXT = "#0b0b0b"


def render_lake(slug: str, title: str, criteria_label: str) -> Path | None:
    outdir = OUT_DIR / slug
    depth_tifs = sorted(outdir.glob("depth_ft_*.tif"))
    dist_tifs = sorted(outdir.glob("distance_m_*.tif"))
    has_depth = bool(depth_tifs)
    basemap_tifs = depth_tifs if has_depth else dist_tifs
    if not basemap_tifs:
        return None
    with rasterio.open(basemap_tifs[0]) as src:
        field = src.read(1)
        b = src.bounds

    fig_w = 9.0
    aspect = (b.top - b.bottom) / (b.right - b.left)
    fig, ax = plt.subplots(figsize=(fig_w, max(4.0, min(14.0, fig_w * aspect))), dpi=150)
    ax.set_facecolor(LAND)

    extent = (b.left, b.right, b.bottom, b.top)
    masked = np.ma.masked_invalid(field)
    # depth: shade by depth; geometry-only: shade by distance-from-shore (ft)
    if not has_depth:
        masked = masked / 0.3048  # metres → feet for a familiar scale
    cmap = "Blues" if has_depth else "GnBu"
    im = ax.imshow(masked, cmap=cmap, extent=extent, origin="upper",
                   vmin=0, vmax=max(10.0, float(masked.max())), zorder=1)
    ax.contour(np.isfinite(field).astype(float), levels=[0.5], colors=[SHORE],
               linewidths=0.6, extent=extent, origin="upper", zorder=2)

    qual_label = "qualifying (depth + shore distance)" if has_depth else "qualifying (shore distance)"
    handles = []
    for name, path, color, alpha, hatch in [
        (qual_label, outdir / "zones_qualifying.geojson", QUALIFYING, 0.45, None),
        ("supports straight run", outdir / "zones_runs.geojson", RUNS, 0.55, "//"),
    ]:
        if path.exists():
            gdf = gpd.read_file(path)
            if not gdf.empty:
                gdf = gdf.to_crs(CRS_UTM)
                gdf.plot(ax=ax, facecolor=color, alpha=alpha, edgecolor=color,
                         linewidth=1.0, hatch=hatch, zorder=3)
        handles.append(Patch(facecolor=color, alpha=0.6, hatch=hatch, label=name))

    # 1-mile scale bar, lower left
    mile = 1609.34
    x0 = b.left + 0.05 * (b.right - b.left)
    y0 = b.bottom + 0.04 * (b.top - b.bottom)
    ax.add_line(Line2D([x0, x0 + mile], [y0, y0], color=TEXT, linewidth=2))
    ax.annotate("1 mile", (x0 + mile / 2, y0), textcoords="offset points",
                xytext=(0, 5), ha="center", fontsize=8, color=TEXT)

    cbar = fig.colorbar(im, ax=ax, shrink=0.6, pad=0.02)
    cbar.set_label("depth (ft below 2,128 ft full pool)" if has_depth
                   else "distance from shore (ft)", fontsize=8)
    cbar.ax.tick_params(labelsize=7)

    ax.legend(handles=handles, loc="upper right", fontsize=8, framealpha=0.9)
    ax.set_title(f"{title}\n{criteria_label}", fontsize=11, color=TEXT)
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)

    out = outdir / "map.png"
    fig.savefig(out, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out
