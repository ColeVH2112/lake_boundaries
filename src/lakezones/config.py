"""Project-wide constants and paths."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_RAW = PROJECT_ROOT / "data" / "raw"
DATA_PROCESSED = PROJECT_ROOT / "data" / "processed"
OUT_DIR = PROJECT_ROOT / "out"

# Native CRS of the tribal bathymetry; a metric CRS suitable for all analysis.
CRS_UTM = "EPSG:26911"  # NAD83 / UTM zone 11N

# Lake Coeur d'Alene summer full-pool elevation (Post Falls Dam operation), ft ASL.
# The tribal bathymetry references depths to this pool elevation.
FULL_POOL_ELEV_FT = 2128.0

M_PER_FT = 0.3048
ACRES_PER_M2 = 1.0 / 4046.8564224

# HUC8 subbasins intersecting Kootenai County (verified via TIGERweb county polygon).
HUC8S = [
    "17010214",  # Pend Oreille Lake (Spirit, Twin Lakes)
    "17010301",  # Upper Coeur d'Alene
    "17010302",  # South Fork Coeur d'Alene
    "17010303",  # Coeur d'Alene Lake (CdA, chain lakes, Fernan)
    "17010304",  # St. Joe
    "17010305",  # Upper Spokane (Hauser, Hayden)
    "17010306",  # Hangman
]
