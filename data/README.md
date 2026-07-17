# data/

Raw downloads are **not** committed (see .gitignore). Recreate with:

```bash
.venv/bin/python scripts/fetch_data.py
```

- `raw/bathy_cdabasin/` — CdA Tribe/Avista bathymetric contours (credit the
  Coeur d'Alene Tribe & Avista Corp.)
- `raw/nhd/` — USGS NHD HR per-HUC8 geopackages (public domain)
- `raw/deq_depth20ft/` — Idaho DEQ 20-ft contour for Lake CdA (validation)
- `raw/fernan/` — Univ. of Idaho Fernan soundings (**CC BY-NC-SA, non-commercial**)
- `raw/county/` — Kootenai County boundary (Census TIGERweb)

Sources, licenses, verified URLs: ../docs/DATA_SOURCES.md
