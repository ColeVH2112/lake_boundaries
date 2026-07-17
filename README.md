# lake_boundaries — Kootenai County lake depth-zone detector

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
```

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
