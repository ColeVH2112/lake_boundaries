"""Registry of Kootenai County lakes covered by the tribal bathymetry dataset."""

# GNIS names as they appear in NHD; all are covered by the CdA Tribe/Avista
# basin bathymetry (Lake CdA at 5/10-ft contours to 210 ft, chain lakes at
# coarse 5-ft contours). Lakes NOT here (Hayden, Spirit, Twin, Fernan, Hauser)
# have no open digital bathymetry — see docs/DATA_SOURCES.md.
# Only Lake CdA needs the touching-polygon dissolve (NHD splits it into main
# pool + Spokane River Arm + Chatcolet narrows). Chain lakes must NOT dissolve:
# some pairs (Medicine/Cave) touch via connecting channels and would merge.
DISSOLVE_TOUCHING = {"Coeur d'Alene Lake"}

COVERED_LAKES = [
    "Coeur d'Alene Lake",
    "Rose Lake",
    "Killarney Lake",
    "Medicine Lake",
    "Cave Lake",
    "Black Lake",
    "Blue Lake",
    "Anderson Lake",
    "Thompson Lake",
    "Swan Lake",
    "Bull Run Lake",
]


def slugify(name: str) -> str:
    return (
        name.lower()
        .replace("'", "")
        .replace(" ", "_")
    )
