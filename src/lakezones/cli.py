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


def _run_one(lake_name: str, wb, contours, args) -> dict | None:
    import rasterio

    from .data import get_lake_polygon
    from .depth import build_depth_raster
    from .vectorize import mask_to_gdf, save_geojson
    from .zones import compute_zones

    slug = slugify(lake_name)
    outdir = OUT_DIR / slug
    outdir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    try:
        poly = get_lake_polygon(
            wb, lake_name, contours=contours,
            dissolve_touching=lake_name in DISSOLVE_TOUCHING,
        )
        depth, mask, transform = build_depth_raster(
            poly, contours, cell=args.cell, densify=args.densify
        )
    except (KeyError, ValueError) as e:
        print(f"[{lake_name}] SKIP: {e}")
        return None
    z = compute_zones(
        depth, mask, args.cell,
        min_depth_ft=args.min_depth_ft,
        min_shore_dist_ft=args.min_shore_dist_ft,
        run_length_ft=args.run_length_ft,
        angle_step=args.angle_step,
    )

    with rasterio.open(
        outdir / f"depth_ft_{int(args.cell)}m.tif", "w",
        driver="GTiff", height=depth.shape[0], width=depth.shape[1],
        count=1, dtype="float32", crs=CRS_UTM, transform=transform,
        nodata=np.nan, compress="deflate",
    ) as dst:
        dst.write(depth, 1)

    criteria = dict(
        min_depth_ft=args.min_depth_ft,
        min_shore_dist_ft=args.min_shore_dist_ft,
        run_length_ft=args.run_length_ft,
        angle_step_deg=args.angle_step,
        cell_m=args.cell,
    )
    gdf_q = mask_to_gdf(z["qualifying"], transform, lake=lake_name, layer="qualifying", **criteria)
    gdf_r = mask_to_gdf(z["runs"], transform, lake=lake_name, layer="runs", **criteria)
    save_geojson(gdf_q, outdir / "zones_qualifying.geojson")
    save_geojson(gdf_r, outdir / "zones_runs.geojson")

    cell_acres = args.cell * args.cell * ACRES_PER_M2
    stats = {
        "lake": lake_name,
        "criteria": criteria,
        "lake_area_acres": round(float(mask.sum()) * cell_acres, 1),
        "max_depth_ft": round(float(np.nanmax(depth)), 1) if mask.any() else 0.0,
        "qualifying_acres": round(float(z["qualifying"].sum()) * cell_acres, 1),
        "runs_acres": round(float(z["runs"].sum()) * cell_acres, 1),
        "runs_polygons": int(len(gdf_r)),
        "seconds": round(time.time() - t0, 1),
    }
    (outdir / "stats.json").write_text(json.dumps(stats, indent=2))
    print(
        f"[{lake_name}] {stats['lake_area_acres']:,.0f} ac lake, "
        f"max {stats['max_depth_ft']:.0f} ft | qualifying {stats['qualifying_acres']:,.0f} ac | "
        f"with {args.run_length_ft:.0f} ft runs {stats['runs_acres']:,.0f} ac "
        f"({stats['runs_polygons']} zones) [{stats['seconds']}s]"
    )
    return stats


def cmd_run(args) -> int:
    wb, contours = _load_inputs()
    names = COVERED_LAKES if args.all_covered else [args.lake]
    if not names or names == [None]:
        print("error: pass --lake NAME or --all-covered", file=sys.stderr)
        return 2
    all_stats = [s for n in names if (s := _run_one(n, wb, contours, args))]
    (OUT_DIR / "summary.json").write_text(json.dumps(all_stats, indent=2))
    print(f"\nwrote {OUT_DIR / 'summary.json'}")
    return 0


def cmd_render(args) -> int:
    from .render import render_lake

    names = COVERED_LAKES if args.all_covered else [args.lake]
    label = (
        f"depth ≥ {args.min_depth_ft:.0f} ft · ≥ {args.min_shore_dist_ft:.0f} ft from shore · "
        f"straight run ≥ {args.run_length_ft:.0f} ft"
    )
    for n in names:
        if n is None:
            continue
        out = render_lake(slugify(n), n, label)
        print(f"[{n}] {'wrote ' + str(out) if out else 'no outputs to render'}")
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

    pr = sub.add_parser("run", help="compute zones for one or all covered lakes")
    pr.add_argument("--lake")
    pr.add_argument("--all-covered", action="store_true")
    pr.add_argument("--min-depth-ft", type=float, default=20.0)
    pr.add_argument("--min-shore-dist-ft", type=float, default=500.0)
    pr.add_argument("--run-length-ft", type=float, default=3000.0)
    pr.add_argument("--angle-step", type=float, default=5.0)
    pr.add_argument("--cell", type=float, default=10.0)
    pr.add_argument("--densify", type=float, default=None)

    pm = sub.add_parser("render", help="render map PNGs from existing outputs")
    pm.add_argument("--lake")
    pm.add_argument("--all-covered", action="store_true")
    pm.add_argument("--min-depth-ft", type=float, default=20.0)
    pm.add_argument("--min-shore-dist-ft", type=float, default=500.0)
    pm.add_argument("--run-length-ft", type=float, default=3000.0)

    pv = sub.add_parser("validate-20ft", help="compare our 20 ft contour to Idaho DEQ's")
    pv.add_argument("--cell", type=float, default=10.0)
    pv.add_argument("--densify", type=float, default=None)

    args = p.parse_args(argv)
    return {
        "list-lakes": cmd_list_lakes,
        "run": cmd_run,
        "render": cmd_render,
        "validate-20ft": cmd_validate_20ft,
    }[args.cmd](args)
