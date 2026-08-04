"""
Assign each Woodland Carbon Code project (from wcc_projects_all.csv) to its
HISTORIC county via point-in-polygon matching against a real boundary file,
rather than the modern administrative "Cumbria" label the registry uses.

This resolves the Cumberland-vs-Westmorland ambiguity discussed in the
paper's footnote: historic Cumberland borders Scotland; historic Westmorland
does not, and is excluded from the border-county sample.

DATA SOURCE (confirmed)
------------------------
ONS Open Geography Portal: "Counties (December 1921) Boundaries EW BGC" --
official ONS-published historic county boundaries as of Census Day 1921
(pre-1974 reorganisation), so Cumberland and Westmorland are still separate
polygons here, unlike modern "Cumbria".

Download directly via the ArcGIS REST API (GeoJSON, no login/ArcGIS Hub UI
needed):
    https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/CTY_DEC_1921_EW_BGC/FeatureServer/0/query?where=1=1&outFields=*&f=geojson

Save it as e.g. `data/boundaries/counties_1921.geojson`.

Confirmed field names (pulled directly from the ArcGIS layer metadata):
    CTY1921NM  -- county name (e.g. "Cumberland", "Westmorland")
    CTY1921CD  -- county code

IMPORTANT -- coordinate system: this layer is in British National Grid
(EPSG:27700), NOT WGS84 lat/long. geopandas will reproject it automatically
via `.to_crs(epsg=4326)` in `load_boundaries()` below, but if you swap in a
different source, double check its CRS.

WHY THIS RUNS LOCALLY, NOT IN THE SANDBOX
-------------------------------------------
This environment's network access is restricted to a small allowlist that
doesn't include ArcGIS/ONS hosts, and the full England & Wales file is also
too large for its fetch tool's response-size limit. Both issues are specific
to this sandbox -- the download and script will run normally on your own
machine.

STEP 1 -- Download the boundary file (see DATA SOURCE above) and save it to
    `data/boundaries/counties_1921.geojson` (or update BOUNDARY_FILE below).

STEP 2 -- Install dependencies:
    pip install geopandas shapely pandas --break-system-packages

STEP 3 -- Double check CUMBERLAND_LABEL below matches the actual value in
    the file (open it in a text editor, or run in Python:
    `geopandas.read_file(BOUNDARY_FILE)["CTY1921NM"].unique()`) --
    it should be "Cumberland" but worth confirming, e.g. no trailing spaces.

STEP 4 -- Run:
    python assign_historic_county.py

Produces `wcc_projects_historic_county.csv`: a copy of the input data with
a new `historic_county` column, plus a pre-filtered
`wcc_projects_border_counties_historic.csv` containing only Northumberland,
Dumfries and Galloway, Scottish Borders, and historic Cumberland.
"""

from pathlib import Path

import pandas as pd
import geopandas as gpd
from shapely.geometry import Point

# ---- Confirmed against the ONS ArcGIS layer metadata; double-check
#      CUMBERLAND_LABEL once you have the file (see STEP 3 above). ----
INPUT_CSV = Path("wcc_projects_all.csv")
BOUNDARY_FILE = Path("counties_1921.geojson")
COUNTY_NAME_FIELD = "CTY1921NM"
CUMBERLAND_LABEL = "Cumberland"
# ------------------------------------------------------------------------

# Counties already correctly labelled by the registry -- kept as-is.
DIRECT_COUNTIES = {"Northumberland", "Dumfries and Galloway", "Scottish Borders"}

# The registry's modern "Cumbria" label needs splitting via the boundary file.
CUMBRIA_LABEL_IN_REGISTRY = "Cumbria"


def load_boundaries() -> gpd.GeoDataFrame:
    if not BOUNDARY_FILE.exists():
        raise FileNotFoundError(
            f"Boundary file not found at {BOUNDARY_FILE}. See the module "
            f"docstring for where to download one."
        )
    gdf = gpd.read_file(BOUNDARY_FILE)
    if COUNTY_NAME_FIELD not in gdf.columns:
        raise KeyError(
            f"Column '{COUNTY_NAME_FIELD}' not found in boundary file. "
            f"Available columns: {list(gdf.columns)}. Update COUNTY_NAME_FIELD."
        )
    # Ensure lat/long compatibility (WGS84)
    if gdf.crs is not None and gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)
    return gdf


def assign_historic_county(df: pd.DataFrame, boundaries: gpd.GeoDataFrame) -> pd.DataFrame:
    """For rows currently labelled 'Cumbria', determine whether they fall
    inside the historic Cumberland polygon via point-in-polygon test."""
    cumberland_poly = boundaries[boundaries[COUNTY_NAME_FIELD].str.strip() == CUMBERLAND_LABEL]
    if cumberland_poly.empty:
        raise ValueError(
            f"No polygon found matching CUMBERLAND_LABEL='{CUMBERLAND_LABEL}'. "
            f"Available values: {sorted(boundaries[COUNTY_NAME_FIELD].unique())}"
        )
    cumberland_union = cumberland_poly.unary_union

    df = df.copy()
    df["historic_county"] = df["county"]  # default: keep registry label

    cumbria_mask = df["county"] == CUMBRIA_LABEL_IN_REGISTRY
    n_cumbria = cumbria_mask.sum()
    print(f"Checking {n_cumbria} 'Cumbria' projects against the historic "
          f"Cumberland boundary...")

    def in_cumberland(row) -> bool:
        if pd.isna(row["latitude"]) or pd.isna(row["longitude"]):
            return False
        pt = Point(float(row["longitude"]), float(row["latitude"]))
        return cumberland_union.contains(pt)

    is_cumberland = df.loc[cumbria_mask].apply(in_cumberland, axis=1)
    df.loc[cumbria_mask, "historic_county"] = is_cumberland.map(
        {True: "Cumberland", False: "Westmorland/Furness (excluded)"}
    )

    n_in = (df.loc[cumbria_mask, "historic_county"] == "Cumberland").sum()
    n_out = n_cumbria - n_in
    print(f"  -> {n_in} fall within historic Cumberland (retained)")
    print(f"  -> {n_out} fall outside historic Cumberland (excluded as "
          f"Westmorland/Furness)")

    return df


def main():
    df = pd.read_csv(INPUT_CSV)
    boundaries = load_boundaries()

    df = assign_historic_county(df, boundaries)
    df.to_csv("wcc_projects_historic_county.csv", index=False)
    print(f"\nWrote full dataset with historic_county column to "
          f"wcc_projects_historic_county.csv")

    keep = DIRECT_COUNTIES | {"Cumberland"}
    border_df = df[df["historic_county"].isin(keep)]
    border_df.to_csv("wcc_projects_border_counties_historic.csv", index=False)
    print(f"Wrote {len(border_df)} border-county records to "
          f"wcc_projects_border_counties_historic.csv")

    print("\nFinal county counts:")
    print(border_df["historic_county"].value_counts())


if __name__ == "__main__":
    main()
