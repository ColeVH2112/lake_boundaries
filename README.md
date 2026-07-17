# lake_boundaries — Kootenai County lake depth-zone detector

**▶ Interactive explorer: https://colevh2112.github.io/lake_boundaries/** — pick a
lake and drag the distance-from-shore slider; recomputes live in the browser.


Deterministic geospatial pipeline that maps the lakes of Kootenai County, Idaho,
builds per-lake **depth rasters** from public bathymetry, and outlines every area
that satisfies configurable criteria such as:

> deeper than **20 ft** AND more than **500 ft from shore** AND lying on a
> straight run at least **3,000 ft** long.

No computer vision required for the core county lakes: authoritative lake
outlines come from USGS NHD polygons, and digital depth contours exist for
Lake Coeur d'Alene **and all ten chain lakes** (Coeur d'Alene Tribe / Avista
bathymetry, published via INSIDE Idaho). See
[docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) for the full data survey,
including which lakes have *no* open depth data (Hayden, Spirit, Twin, Hauser).

## Quickstart

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python scripts/fetch_data.py          # ~140 MB of public data
.venv/bin/python -m lakezones list-lakes        # what's analyzable
.venv/bin/python -m lakezones run --all-covered # default 20ft/500ft/3000ft
.venv/bin/python -m lakezones run --lake "Coeur d'Alene Lake" \
    --min-depth-ft 30 --min-shore-dist-ft 1000 --run-length-ft 5000
.venv/bin/python -m lakezones validate-20ft     # cross-check vs Idaho DEQ contour
.venv/bin/python -m lakezones web-export        # rebuild the interactive site data
```

### Works on any lake (portable)

Depth is optional — distance-from-shore and the straight-run test need only an
outline, so **any** lake can be analyzed:

```bash
# geometry only (no depth data needed) — e.g. Hayden Lake
python -m lakezones run --lake "Lake Hayden" --depth-source none

# a lake outside the bundled NHD tiles: supply any outline file (any CRS)
python -m lakezones run --lake "My Lake" --polygon my_lake.geojson --depth-source none

# bring your own depth: a contour file with a depth field, or a depth GeoTIFF
python -m lakezones run --lake "My Lake" --polygon my_lake.geojson \
    --depth-source contour-file --depth-file contours.geojson --depth-field DEPTH_FT
python -m lakezones run --lake "My Lake" --polygon my_lake.geojson \
    --depth-source raster --depth-file sonar_depth.tif
```

`--depth-source` is `contours` (bundled tribal data), `contour-file`, `raster`,
or `none`. When a depth source doesn't cover a lake it falls back to
geometry-only rather than failing.

### Interactive explorer

`docs/` is a static site (no build step, no server) that loads the exported
rasters and recomputes zones live as you drag the depth / distance / run-length
sliders. Enable **GitHub Pages → Deploy from branch → `main` / `docs`** and it
publishes itself. Run it locally with `python -m http.server -d docs`.

Outputs land in `out/<lake_slug>/`:

| file | contents |
|---|---|
| `depth_ft_10m.tif` | interpolated depth raster (ft, 10 m cells, EPSG:26911) |
| `zones_qualifying.geojson` | areas meeting the depth + shore-distance criteria |
| `zones_runs.geojson` | subset also lying on a qualifying straight run |
| `stats.json` | acreages, max depth, run count |

## Method

1. **Outline** — NHD HR waterbody polygons (FType 390/436), dissolving the
   touching pieces NHD splits big lakes into (main pool, river arms,
   Chatcolet narrows).
2. **Depth raster** — TIN (Delaunay linear) interpolation over densified
   bathymetric contour vertices, with the shoreline (and island shores)
   burned in as depth 0 — the same convention Minnesota DNR used for its
   statewide lake DEMs. 10 m cells by default.
3. **Distance from shore** — Euclidean distance transform of the lake mask
   (islands count as shore).
4. **Criteria mask** — `depth ≥ D` AND `distance ≥ S`, pure thresholding.
5. **Straight runs** — morphological opening with a rotated linear window:
   a cell survives iff a straight, fully-qualifying segment of length `L`
   passes through it at one of the tested headings (default every 5°).
   This is the right test for "can a 3,000 ft straight lane fit here".

Everything is deterministic; no ML, no manual digitizing for covered lakes.

### Digitizing a scanned / nautical map (`lakezones.digitize`)

For lakes whose only depth information is a paper or scanned map, `digitize.py`
georeferences the image (`affine_from_gcps` from a few control points) and turns
color-isolated contour lines into the same `depth_ft` contour layer the rest of
the pipeline consumes — so a digitized map and native bathymetry run identically.
`scripts/validate_digitize.py` proves the round-trip on real data: rendering a
lake's true contours into a synthetic scan and digitizing them back reproduces
the depth raster to **RMSE 0.23 ft / median 0.02 ft**, far below the 5-ft
contour interval. The error that matters is the source survey's, not the tracing.

## Accuracy

Screening-grade, not navigation-grade. The bathymetry derives from
late-1990s/2000s single-beam surveys (Post Falls Dam relicensing) and 1991–92
USGS work around the chain lakes; contours are 5–10 ft interval. Depths
reference the **2,128 ft summer full pool** — winter drawdown (~7 ft) shrinks
every zone. Chain-lake contours are coarse (3–15 lines per lake).

## Data credits & licensing

- Bathymetry: Coeur d'Alene Tribe & Avista Corp. (public, credit requested)
- Hydrography: USGS NHD/WBD (US public domain)
- Fernan Lake soundings: Univ. of Idaho, Wilhelm & LaCroix — **CC BY-NC-SA
  (non-commercial only)**, kept out of default outputs
- Validation contour: Idaho DEQ `depth_20ft` feature service
