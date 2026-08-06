"""
Enrich the existing wcc_projects_border_counties_historic.csv with the
per-project detail fields (category, location, RAG rating, woodland benefit
scores, etc.) that only live on the individual project page -- not in the
list-search endpoint the original scrape used.

Self-contained: does not import from registry_scrape.py, so there's no risk
of it silently running against a stale copy of that file.

This does NOT redo the full 2,477-project scrape. It reads your existing
325-row border-counties file and does one detail-fetch per row, using the
`project_id` column already present.

SETUP
-----
    pip install requests --break-system-packages

USAGE
-----
    python enrich_border_counties.py

Produces `wcc_projects_border_counties_enriched.csv` alongside the input
file, with the new detail columns appended.
"""

import csv
import sys
import time
from pathlib import Path

import requests

INPUT_CSV = Path("wcc_projects_border_counties_historic.csv")
OUTPUT_CSV = Path("wcc_projects_border_counties_enriched.csv")

REGISTRY_HOME = "https://registry.spglobal.com/uklandcarbonregistry/public/wcc"

# Detail endpoint (found via DevTools full-text search on the individual
# project page). GET, with project_id + application name baked into the
# path -- unlike the list-search endpoint, which is a POST.
#     GET .../public-report-manager/getProjectById/{project_id}/{application}
# NOTE: uses project_id (104xxx...), not entity_id (103xxx...).
PROJECT_DETAIL_URL_TMPL = (
    "https://prod-us.api.platts.com/ci-raas-prod/br-reg/rest/"
    "public-report-manager/getProjectById/{project_id}/{application}"
)

REQUEST_DELAY_SEC = 0.5
MANUAL_XSRF_TOKEN = None  # paste a fresh token here if auto-bootstrap fails

BASE_HEADERS = {
    "accept": "application/json",
    "content-type": "application/json",
    "appkey": "wOKHFGuxKApQaujPSKgF",
    "application": "Markit",
    "registry": "UKLR",
    "standardacronym": "WCC",
    "standardid": "100000000000042",
    "language": "en",
    "origin": "https://registry.spglobal.com",
    "referer": "https://registry.spglobal.com/",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}


def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(BASE_HEADERS)

    try:
        session.get(REGISTRY_HOME, timeout=15)
    except requests.RequestException as e:
        print(f"Warning: could not pre-visit registry home page ({e}); "
              f"continuing without cookie bootstrap.", file=sys.stderr)

    token = MANUAL_XSRF_TOKEN or session.cookies.get("XSRF-TOKEN")
    if token:
        session.headers["x-xsrf-token"] = token
        print(f"Using XSRF token: {token[:12]}...")
    else:
        print("No XSRF-TOKEN cookie found -- proceeding with appkey-only auth.")

    return session


def fetch_project_detail(session: requests.Session, project_id) -> dict:
    application = BASE_HEADERS.get("application", "Markit")
    url = PROJECT_DETAIL_URL_TMPL.format(project_id=project_id, application=application)
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    return resp.json()


def flatten_project_detail(detail: dict) -> dict:
    """Confirmed against a full response capture (Annandale and Lochwood,
    project_id 104000000026263): the top-level object is a shared/generic
    schema and is mostly null. The populated values live one level down,
    inside `mixedUnitList[0]`. RAG rating and documentList are genuine
    top-level fields.
    """
    mixed_units = detail.get("mixedUnitList") or []
    unit = mixed_units[0] if mixed_units else {}

    benefit_fields = {
        f.get("name"): f.get("value")
        for f in (unit.get("additionalInfoFieldList") or [])
        if f.get("name")
    }

    return {
        "category_name": unit.get("category_name"),
        "project_type_name": unit.get("project_type_name"),
        "standard_project_type_name": unit.get("standard_project_type_name"),
        "standard_name": unit.get("standard_name") or detail.get("standard_name"),
        "province": unit.get("province"),
        "zipcode": unit.get("zipcode"),
        "unit_latitude": unit.get("latitude"),
        "unit_longitude": unit.get("longitude"),
        "unit_address_line_1": unit.get("address_line_1"),
        "unit_city": unit.get("city"),
        "address": unit.get("address"),
        "public_view_address": unit.get("publicViewAddress"),
        "unit_country_name": unit.get("country_name"),
        "unit_validator_name": unit.get("validator_name"),
        "unit_grid_reference": unit.get("grid_reference"),
        "rag_color": detail.get("ragColor"),
        "rag_color_text": detail.get("ragColorText"),
        "project_duration": detail.get("project_duration"),
        "state_name": detail.get("state_name"),
        "project_developer_name": detail.get("project_developer_name"),
        "registration_date": detail.get("registration_date"),
        "document_count": len(detail.get("documentList") or []),
        "detail_woodland_benefits_score": benefit_fields.get("woodland_benefits_score"),
        "detail_biodiversity_score": benefit_fields.get("biodiversity"),
        "detail_community_score": benefit_fields.get("community"),
        "detail_economy_score": benefit_fields.get("economy"),
        "detail_water_score": benefit_fields.get("water"),
    }


def load_existing_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main():
    if not INPUT_CSV.exists():
        print(f"Couldn't find {INPUT_CSV.resolve()} -- place it alongside "
              f"this script, or edit INPUT_CSV above.", file=sys.stderr)
        sys.exit(1)

    rows = load_existing_rows(INPUT_CSV)
    print(f"Loaded {len(rows)} existing rows from {INPUT_CSV}")

    session = build_session()

    enriched_rows = []
    failures = []

    for i, row in enumerate(rows, start=1):
        project_id = row.get("project_id")
        project_name = row.get("project_name", "")
        print(f"[{i}/{len(rows)}] Fetching detail for project_id={project_id} "
              f"({project_name})...")

        try:
            detail = fetch_project_detail(session, project_id)
            detail_fields = flatten_project_detail(detail)
        except Exception as e:
            print(f"  Failed: {e}", file=sys.stderr)
            failures.append(project_id)
            detail_fields = {}

        merged = {**row, **detail_fields}
        enriched_rows.append(merged)
        time.sleep(REQUEST_DELAY_SEC)

    if not enriched_rows:
        print("No rows to write -- something went wrong upstream.")
        return

    fieldnames = list(enriched_rows[0].keys())
    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(enriched_rows)

    print(f"\nWrote {len(enriched_rows)} enriched rows to {OUTPUT_CSV.resolve()}")
    if failures:
        print(f"\n{len(failures)} project(s) failed detail-fetch and have "
              f"blank detail columns -- project_ids: {failures}")


if __name__ == "__main__":
    main()
