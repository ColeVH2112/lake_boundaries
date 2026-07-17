# Data sources for Kootenai County lake bathymetry & outlines

Findings from a systematic survey (July 2026) of public depth/outline data for
every significant lake in Kootenai County, Idaho. All download URLs verified
live at survey time.

## TL;DR

| Lake | Acres | Max depth | Open digital depth data |
|---|---:|---:|---|
| Coeur d'Alene Lake | 27,919 | ~210–220 ft | ✅ Tribal bathymetry, 5/10-ft contours to 210 ft |
| Chain lakes (Rose, Killarney, Medicine, Cave, Black, Blue, Anderson, Thompson, Swan, Bull Run) | 100–750 ea. | 10–35 ft | ✅ Tribal bathymetry, coarse 5-ft contours (3–15 lines/lake) |
| Fernan Lake | 423 | 27 ft | ⚠️ UI sounding points (22,666 pts, **CC BY-NC-SA — non-commercial**) |
| Hayden Lake | 3,798 | ~178–185 ft | ❌ view-only (2022 crowdsourced C-MAP Genesis map) |
| Spirit Lake | 1,535 | ~100 ft | ❌ none |
| Upper / Lower Twin | 526 / 390 | ~20 / ~60 ft | ❌ none |
| Hauser Lake | 539 | 40 ft | ❌ none |

## Primary datasets (used by the pipeline)

### 1. Bathymetry — Coeur d'Alene Tribe / Avista (2004, via INSIDE Idaho)
- **Download**: <https://insideidaho.org/data/ago/cdatribe/bathymetry_cdabasin_cdatribe.zip> (55.8 MB)
- Esri shapefile, 17,610 contour polylines, NAD83/UTM 11N (EPSG:26911)
- Attributes: `CONTOUR` (elevation ft ASL), `Depth` (ft below the 2,128-ft
  summer full pool), `Interval_` (5 or 10), `GeoLocatio`
- Covers Lake CdA (contours to 210 ft, from Parametrix/Golder Post Falls
  relicensing surveys) + all 10 chain lakes (5-ft contours from USGS 1991–92
  work) + CdA/St. Joe/Spokane/St. Maries rivers
- Access constraints "None"; **credit Coeur d'Alene Tribe & Avista Corp.**
- Caveat: contour lines only (the source DEM was never published); survey
  vintage ≈ 1990s–2000s single-beam

### 2. Lake outlines — USGS NHD High Resolution (per-HUC8 GPKGs)
- Pattern: `https://prd-tnm.s3.amazonaws.com/StagedProducts/Hydrography/NHD/HU8/GPKG/NHD_H_<HUC8>_HU8_GPKG.zip`
- Layer `NHDWaterbody`, filter `FType IN (390, 436)`; NAD83 (EPSG:4269); public domain
- **NHD is a frozen snapshot** (retired 2023-10-01). Its 3DHP successor for
  this area is still converted legacy NHD, so nothing is lost by using NHD HR.
- NHD splits Lake CdA into several touching polygons (main lake, Spokane River
  Arm, Chatcolet, …) — the pipeline dissolves connected polygons.
- HUC8s intersecting Kootenai County (verified spatially): 17010214 Pend
  Oreille Lk, 17010301 Upper CdA, 17010302 South Fork CdA, 17010303 CdA Lake,
  17010304 St. Joe, 17010305 Upper Spokane, 17010306 Hangman.

### 3. Watershed boundaries — USGS WBD
- Region-wide: `…/StagedProducts/Hydrography/WBD/HU2/GDB/WBD_17_HU2_GDB.zip` (356 MB; no per-HUC8 product)
- A WBD feature dataset is also embedded in each NHD HU4/HU8 GDB
- Live REST alternative: USGS WBD MapServer

### 4. Validation — Idaho DEQ `depth_20ft` feature service
- 20-ft depth contour of Lake CdA (10 polylines):
  `https://services1.arcgis.com/Kr5oFycwwDsdTyVH/arcgis/rest/services/depth_20ft/FeatureServer/0/query?where=1%3D1&outFields=*&f=geojson`
- No metadata/license published — cross-check only, likely derived from #1

### 5. Fernan Lake — Univ. of Idaho soundings (2014–15)
- <https://www.northwestknowledge.net/data/4dc776f4-5ee1-4425-86ad-267b06abb7a6/>
- 22,666 sonar/pole soundings (CSV + shapefiles + TIN), UTM NAD83
- **CC BY-NC-SA — non-commercial use only**; keep out of any commercial product

### 6. County boundary — Census TIGERweb REST (GEOID 16055)

## Confirmed absences (so nobody wastes time re-searching)

- **USGS never released digital bathymetry for Lake CdA** — the 1994
  bathymetric map (WRI 94-4119) exists only as a scanned plate PDF
  (<https://pubs.usgs.gov/wri/1994/4119/plate-1.pdf>); its data survives only
  in the tribal compilation.
- **IDFG publishes no depth-contour lake maps today** — Fishing Planner pages
  offer only a basemap printer + shoreline KMZ; a full enumeration of IDFG's
  GIS portal found no bathymetry layer. Old hand-drawn maps exist only inside
  scanned fishery reports (idahodocs.contentdm.oclc.org). **IDFG site content
  is copyrighted** — personal, non-commercial viewing only; get written
  permission before deriving data.
- **No topobathymetric lidar** anywhere in the basin (3DEP query: 0 products);
  the 1-m lidar DEMs are topo-only with flattened water.
- **No crowdsourced-sonar shortcut**: NOAA/IHO DCDB has zero tracks in the
  county bbox; OpenSeaMap is Europe-centric.
- **Commercial charts are closed**: Navionics/Garmin (API is partner-gated,
  display-only), C-MAP Genesis (free Social tier still live in 2026 but ToU
  forbids reuse/derivation; downloads are chartplotter-only AT5), Humminbird
  LakeMaster (no API), onX Fish (no Idaho coverage).

## Paths for the missing lakes (Hayden, Spirit, Twin, Hauser)

1. **Hayden**: the Hayden Lake Watershed Improvement District commissioned the
   2022 crowdsourced BioBase survey — ask them (haydenlakewid.com) for a GIS
   export; the contributing account can export shapefiles from BioBase.
2. **DIY sonar**: a sonar logger + the BioBase/ReefMaster toolchain, or raw
   NMEA logging — a weekend on the water per small lake.
3. **Digitize old scans**: 2012 IDEQ Fernan survey figure (TMDL addendum),
   old IDFG fishery-report maps (copyright — permission needed), or the USGS
   1994 CdA plate (public domain). Pipeline: georeference (GDAL GCP + TPS
   against the NHD shoreline), color-segment + skeletonize contour lines
   (OpenCV/scikit-image + sknw), label depths manually (~5–25 contours/lake),
   then the same TIN interpolation this repo already uses. No maintained
   end-to-end open tool exists; Minnesota DNR proved the workflow at scale.

## Context: the example criteria vs. seaplane guidance

The old FAA seaplane-base circular (AC 150/5395-1, 1994) recommended a
2,500 × 200 ft water operating area, +7 % length per 1,000 ft of elevation —
≈ 2,870 ft at Lake CdA's 2,125 ft, so a 3,000-ft straight-run filter matches
elevation-adjusted classic guidance. The current AC 150/5395-1B (2018) sets
minimum water depth 4 ft (6 ft recommended) and sizes length by design
aircraft; no FAA document requires 20 ft depth or a 500 ft shore setback —
those are conservative margins on top of the standards.
