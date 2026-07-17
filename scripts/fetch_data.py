#!/usr/bin/env python3
"""Download all raw datasets for the Kootenai County lake zones pipeline.

Idempotent: skips files that already exist with a plausible size.
Everything lands in data/raw/. Sources and licensing: docs/DATA_SOURCES.md.
"""

from __future__ import annotations

import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"

# HUC8 subbasins intersecting Kootenai County (verified against TIGERweb county polygon)
HUC8S = [
    "17010214",  # Pend Oreille Lake (Spirit, Twin Lakes)
    "17010301",  # Upper Coeur d'Alene
    "17010302",  # South Fork Coeur d'Alene
    "17010303",  # Coeur d'Alene Lake (CdA, chain lakes, Fernan)
    "17010304",  # St. Joe
    "17010305",  # Upper Spokane (Hauser, Hayden side)
    "17010306",  # Hangman
]

NHD_URL = (
    "https://prd-tnm.s3.amazonaws.com/StagedProducts/Hydrography/NHD/HU8/GPKG/"
    "NHD_H_{huc8}_HU8_GPKG.zip"
)

DOWNLOADS = [
    # (relative path, url, min plausible bytes)
    (
        "bathy_cdabasin/bathymetry_cdabasin_cdatribe.zip",
        "https://insideidaho.org/data/ago/cdatribe/bathymetry_cdabasin_cdatribe.zip",
        50_000_000,
    ),
    (
        "deq_depth20ft/cda_depth_20ft.geojson",
        "https://services1.arcgis.com/Kr5oFycwwDsdTyVH/arcgis/rest/services/"
        "depth_20ft/FeatureServer/0/query?where=1%3D1&outFields=*&f=geojson",
        10_000,
    ),
    (
        "county/kootenai_county.geojson",
        "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/"
        "MapServer/13/query?where=GEOID%3D%2716055%27&outFields=GEOID,NAME"
        "&outSR=4326&f=geojson",
        10_000,
    ),
    # Fernan Lake soundings (Univ. of Idaho, CC BY-NC-SA — non-commercial use only)
    (
        "fernan/fernan_bathymetry_uidaho.zip",
        "https://www.northwestknowledge.net/data/download.php?uuid=4dc776f4-5ee1-4425-86ad-267b06abb7a6",
        1_000_000,
    ),
] + [
    (f"nhd/NHD_H_{h}_HU8_GPKG.zip", NHD_URL.format(huc8=h), 1_000_000) for h in HUC8S
]


def fetch(rel: str, url: str, min_bytes: int) -> bool:
    dest = RAW / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size >= min_bytes:
        print(f"[skip] {rel} ({dest.stat().st_size:,} bytes)")
        return True
    print(f"[get ] {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "lake-outline-detector/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=300) as resp, open(dest, "wb") as f:
            while chunk := resp.read(1 << 20):
                f.write(chunk)
    except Exception as e:  # noqa: BLE001 - report and continue with other files
        print(f"[FAIL] {rel}: {e}", file=sys.stderr)
        if dest.exists():
            dest.unlink()
        return False
    size = dest.stat().st_size
    ok = size >= min_bytes
    print(f"[{'ok  ' if ok else 'WARN'}] {rel} ({size:,} bytes)")
    return ok


def unzip_all() -> None:
    for z in RAW.rglob("*.zip"):
        mark = z.with_suffix(".unzipped")
        if mark.exists():
            continue
        print(f"[unzip] {z.relative_to(RAW)}")
        try:
            with zipfile.ZipFile(z) as zf:
                zf.extractall(z.parent)
        except zipfile.BadZipFile:
            # a truncated download or an HTML error page saved as .zip — drop it
            # and keep going so one bad archive can't abort the rest
            print(f"[BAD ] {z.relative_to(RAW)} is not a valid zip; deleting", file=sys.stderr)
            z.unlink()
            continue
        mark.touch()


def main() -> int:
    results = [fetch(rel, url, mb) for rel, url, mb in DOWNLOADS]
    unzip_all()
    failed = results.count(False)
    print(f"\n{len(results) - failed}/{len(results)} downloads ok")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
