"""Command-line pipeline driver.

Examples:
    python -m lakezones list-lakes
    python -m lakezones run --lake "Coeur d'Alene Lake"
    python -m lakezones run --all-covered --min-depth-ft 20 --min-shore-dist-ft 500 --run-length-ft 3000
    python -m lakezones validate-20ft
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import numpy as np

from .config import ACRES_PER_M2, CRS_UTM, OUT_DIR
from .lakes import COVERED_LAKES, DISSOLVE_TOUCHING, slugify


def _load_inputs():
    from .data import load_bathy_contours, load_waterbodies

    print("loading NHD waterbodies + bathymetry contours ...", flush=True)
    return load_waterbodies(), load_bathy_contours()


def cmd_list_lakes(_args) -> int:
    import shapely

    wb, contours = _load_inputs()
    tree = shapely.STRtree(contours.geometry.values)
    n_hits = wb.geometry.apply(lambda g: len(tree.query(g, predicate="intersects")))
    covered = wb[n_hits > 0]
    covered = covered.assign(acres=(covered.geometry.area * ACRES_PER_M2).round(0))
    named = covered.dropna(subset=["name"]).sort_values("acres", ascending=False)
    print(f"\n{len(covered)} NHD waterbodies intersect the bathymetry ({named['name'].nunique()} named):")
    for _, row in named.iterrows():
        print(f"  {row['name']:<28} {row['acres']:>9,.0f} ac")
    return 0


def _resolve_polygon(lake_name, wb, contours, args):
    from .data import get_lake_polygon, polygon_from_file

    if getattr(args, "polygon", None):
        return polygon_from_file(args.polygon)
    return get_lake_polygon(
        wb, lake_name, contours=contours,
        dissolve_touching=lake_name in DISSOLVE_TOUCHING,
    )


def _run_one(lake_name: str, wb, contours, args) -> dict:
    import rasterio

    from . import depth_sources
    from .vectorize import mask_to_gdf, save_geojson
    from .zones import compute_zones

    slug = slugify(lake_name)
    outdir = OUT_DIR / slug
    t0 = time.time()

    try:
        poly = _resolve_polygon(lake_name, wb, contours, args)
        depth, mask, transform = depth_sources.resolve(
            args.depth_source, poly, contours=contours, path=args.depth_file,
            depth_field=args.depth_field, cell=args.cell, densify=args.densify,
        )
    except (KeyError, ValueError) as e:
        print(f"[{lake_name}] SKIP: {e}")
        return {"lake": lake_name, "skipped": str(e)}

    # fold docks into the shoreline: dock cells become non-water, so distance is
    # measured from shore-or-dock (Idaho's setback rule counts docks & floats)
    n_docks = 0
    if getattr(args, "docks", None):
        from .docks import burn_docks, load_dock_file
        dk = load_dock_file(args.docks)
        n_docks = len(dk)
        mask = burn_docks(mask, dk.geometry.values, transform, width_m=args.dock_width_m)
        if depth is not None:
            depth = np.where(mask, depth, np.nan)

    outdir.mkdir(parents=True, exist_ok=True)
    has_depth = depth is not None
    z = compute_zones(
        depth, mask, args.cell,
        min_depth_ft=args.min_depth_ft,
        min_shore_dist_ft=args.min_shore_dist_ft,
        run_length_ft=args.run_length_ft,
        angle_step=args.angle_step,
    )

    def _save_tif(name, arr):
        with rasterio.open(
            outdir / name, "w", driver="GTiff",
            height=arr.shape[0], width=arr.shape[1], count=1, dtype="float32",
            crs=CRS_UTM, transform=transform, nodata=np.nan, compress="deflate",
        ) as dst:
            dst.write(arr.astype("float32"), 1)

    cellstr = f"{int(args.cell)}m"
    # distance-from-shore raster is always written (the primary geometry layer +
    # what the interactive site sliders read); depth only when a source gave it
    dist_out = np.where(mask, z["distance_m"], np.nan)
    _save_tif(f"distance_m_{cellstr}.tif", dist_out)
    if has_depth:
        _save_tif(f"depth_ft_{cellstr}.tif", depth)

    criteria = dict(
        min_depth_ft=args.min_depth_ft if has_depth else 0.0,
        min_shore_dist_ft=args.min_shore_dist_ft,
        run_length_ft=args.run_length_ft,
        angle_step_deg=args.angle_step,
        cell_m=args.cell,
        depth_source=args.depth_source if has_depth else "none",
        docks=n_docks,
    )
    gdf_q = mask_to_gdf(z["qualifying"], transform, lake=lake_name, layer="qualifying", **criteria)
    gdf_r = mask_to_gdf(z["runs"], transform, lake=lake_name, layer="runs", **criteria)
    save_geojson(gdf_q, outdir / "zones_qualifying.geojson")
    save_geojson(gdf_r, outdir / "zones_runs.geojson")

    cell_acres = args.cell * args.cell * ACRES_PER_M2
    stats = {
        "lake": lake_name,
        "criteria": criteria,
        "has_depth": has_depth,
        "lake_area_acres": round(float(mask.sum()) * cell_acres, 1),
        "max_depth_ft": round(float(np.nanmax(depth)), 1) if has_depth and mask.any() else None,
        "qualifying_acres": round(float(z["qualifying"].sum()) * cell_acres, 1),
        "runs_acres": round(float(z["runs"].sum()) * cell_acres, 1),
        "runs_polygons": int(len(gdf_r)),
        "seconds": round(time.time() - t0, 1),
    }
    (outdir / "stats.json").write_text(json.dumps(stats, indent=2))
    depth_str = f"max {stats['max_depth_ft']:.0f} ft" if has_depth else "geometry-only (no depth)"
    print(
        f"[{lake_name}] {stats['lake_area_acres']:,.0f} ac lake, {depth_str} | "
        f"qualifying {stats['qualifying_acres']:,.0f} ac | "
        f"with {args.run_length_ft:.0f} ft runs {stats['runs_acres']:,.0f} ac "
        f"({stats['runs_polygons']} zones) [{stats['seconds']}s]"
    )
    return stats


def cmd_run(args) -> int:
    needs_nhd = not getattr(args, "polygon", None)
    needs_contours = args.depth_source == "contours"
    wb, contours = _load_inputs() if (needs_nhd or needs_contours) else (None, None)
    names = COVERED_LAKES if args.all_covered else [args.lake]
    if not names or names == [None]:
        print("error: pass --lake NAME or --all-covered", file=sys.stderr)
        return 2
    all_stats = [_run_one(n, wb, contours, args) for n in names if n is not None]
    (OUT_DIR / "summary.json").write_text(json.dumps(all_stats, indent=2))
    ran = [s for s in all_stats if "skipped" not in s]
    print(f"\n{len(ran)}/{len(all_stats)} lakes analyzed → {OUT_DIR / 'summary.json'}")
    return 0


def _criteria_label(crit: dict) -> str:
    depth = crit.get("min_depth_ft", 0)
    depth_part = f"depth ≥ {depth:.0f} ft · " if depth and depth > 0 else ""
    return (
        f"{depth_part}≥ {crit.get('min_shore_dist_ft', 0):.0f} ft from shore · "
        f"straight run ≥ {crit.get('run_length_ft', 0):.0f} ft"
    )


def cmd_render(args) -> int:
    from .render import render_lake

    names = COVERED_LAKES if args.all_covered else [args.lake]
    for n in names:
        if n is None:
            continue
        slug = slugify(n)
        # label from the criteria actually recorded by `run`, not render's own
        # defaults — otherwise a custom run gets mislabeled maps
        stats_path = OUT_DIR / slug / "stats.json"
        if stats_path.exists():
            crit = json.loads(stats_path.read_text()).get("criteria", {})
        else:
            crit = dict(
                min_depth_ft=args.min_depth_ft,
                min_shore_dist_ft=args.min_shore_dist_ft,
                run_length_ft=args.run_length_ft,
            )
        out = render_lake(slug, n, _criteria_label(crit))
        print(f"[{n}] {'wrote ' + str(out) if out else 'no outputs to render'}")
    return 0


def cmd_web_export(_args) -> int:
    from .web_export import export_all

    export_all()
    return 0


def cmd_validate_20ft(args) -> int:
    from .validate import validate_cda_20ft

    report = validate_cda_20ft(cell=args.cell, densify=args.densify)
    print(json.dumps(report, indent=2))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="lakezones")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list-lakes", help="show NHD waterbodies covered by the bathymetry")

    pr = sub.add_parser("run", help="compute zones for one or all lakes")
    pr.add_argument("--lake", help="lake name (any NHD waterbody) or label for --polygon")
    pr.add_argument("--all-covered", action="store_true", help="all lakes with tribal bathymetry")
    pr.add_argument("--polygon", help="path to an outline file (any CRS) — for lakes outside the NHD tiles")
    pr.add_argument("--depth-source", choices=["contours", "contour-file", "raster", "none"],
                    default="contours", help="where depth comes from ('none' = geometry-only)")
    pr.add_argument("--depth-file", help="path for --depth-source contour-file/raster")
    pr.add_argument("--depth-field", default="depth_ft", help="depth attribute in a contour-file")
    pr.add_argument("--min-depth-ft", type=float, default=20.0)
    pr.add_argument("--min-shore-dist-ft", type=float, default=500.0)
    pr.add_argument("--run-length-ft", type=float, default=3000.0)
    pr.add_argument("--angle-step", type=float, default=5.0)
    pr.add_argument("--cell", type=float, default=10.0)
    pr.add_argument("--densify", type=float, default=None)
    pr.add_argument("--docks", help="dock geometry file (points/lines) to treat as shore")
    pr.add_argument("--dock-width-m", type=float, default=4.0, help="rasterized dock width")

    pm = sub.add_parser("render", help="render map PNGs from existing outputs")
    pm.add_argument("--lake")
    pm.add_argument("--all-covered", action="store_true")
    pm.add_argument("--min-depth-ft", type=float, default=20.0)
    pm.add_argument("--min-shore-dist-ft", type=float, default=500.0)
    pm.add_argument("--run-length-ft", type=float, default=3000.0)

    sub.add_parser("web-export", help="pack rasters into docs/data for the web app")

    pv = sub.add_parser("validate-20ft", help="compare our 20 ft contour to Idaho DEQ's")
    pv.add_argument("--cell", type=float, default=10.0)
    pv.add_argument("--densify", type=float, default=None)

    args = p.parse_args(argv)
    return {
        "list-lakes": cmd_list_lakes,
        "run": cmd_run,
        "render": cmd_render,
        "web-export": cmd_web_export,
        "validate-20ft": cmd_validate_20ft,
    }[args.cmd](args)
