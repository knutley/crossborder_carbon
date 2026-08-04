"""
Scraper for the UK Land Carbon Registry (Woodland Carbon Code) public project
data, hitting the underlying JSON API directly -- no browser automation needed.

Endpoint (discovered via browser DevTools Network tab):
    POST https://prod-us.api.platts.com/ci-raas-prod/raas-report-api/es/public/project/publicReportPageSearch

Confirmed response shape: paginated JSON, 50 records/page, 2477 total projects
across 50 pages (as of the data pulled during development of this script --
these numbers will grow over time).

SETUP
-----
    pip install requests pandas --break-system-packages

USAGE
-----
    python scrape_land_carbon_registry.py

Produces `wcc_projects_all.csv` (every public project) and
`wcc_projects_border_counties.csv` (just the four counties used in the paper).

A NOTE ON AUTH
---------------
The request headers include an `x-xsrf-token`. This is the standard
Angular/Spring "double submit cookie" CSRF pattern: the server sets an
`XSRF-TOKEN` cookie, and the client is expected to echo its value back as the
`x-xsrf-token` header on write-ish requests. Even though this is a read-only
public search, the API may still expect a matching pair. This script:
    1. Visits the registry site once with a `requests.Session()` to pick up
       whatever cookies get set,
    2. Reads the `XSRF-TOKEN` cookie value (if present) and uses it as the
       header,
    3. Falls back to `appkey`-only auth (the other required headers) if no
       such cookie shows up -- some public-read endpoints only check `appkey`.

If you get 401/403 responses, that's the signal the token pairing actually
matters -- open the DevTools cURL copy again, check the *current* XSRF-TOKEN
cookie value straight from the browser, and either paste it into
MANUAL_XSRF_TOKEN below, or let me know and I'll adjust the session bootstrap.
"""

import csv
import time
import sys
from pathlib import Path

import requests

API_URL = "https://prod-us.api.platts.com/ci-raas-prod/raas-report-api/es/public/project/publicReportPageSearch"
REGISTRY_HOME = "https://registry.spglobal.com/uklandcarbonregistry/public/wcc"

PAGE_SIZE = 50          # confirmed from the captured payload/response
REQUEST_DELAY_SEC = 0.5  # be polite; ratelimit-limit header showed 100 req/sec allowed

# If you have a fresh token from DevTools and the auto-bootstrap below doesn't
# work, paste it here as a string and it'll be used verbatim.
MANUAL_XSRF_TOKEN = None

# Counties of interest for the England/Scotland border comparison.
# NOTE: county lives in the `stateProvince` field in the API response.
COUNTY_FILTER = {
    "Cumbria", # Intially used Cumberland here, but it seems that has changed? 
    "Northumberland",
    "Dumfries and Galloway",
    "Scottish Borders",
}

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

    # Bootstrap: visit the registry so any XSRF-TOKEN cookie gets set.
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
        print("No XSRF-TOKEN cookie found -- proceeding with appkey-only "
              "auth. If requests start failing with 401/403, that's why.")

    return session


def fetch_page(session: requests.Session, start: int, limit: int = PAGE_SIZE) -> dict:
    payload = {
        "searchFilter": {
            "filterModel": {},
            "pagination": {
                "start": start,
                "limit": limit,
                "sortOptions": [{"sort": "accountName.keyword", "dir": "ASC"}],
            },
        }
    }
    resp = session.post(API_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def flatten_record(entity: dict) -> dict:
    aux = entity.get("auxiliaries") or {}
    return {
        "entity_id": entity.get("entityId"),
        "project_id": entity.get("projectId"),
        "account_name": entity.get("accountName"),
        "project_name": entity.get("projectName"),
        "project_description": entity.get("projectDescription"),
        "status": entity.get("status"),
        "state_code": entity.get("stateCode"),
        "country": entity.get("countryName"),
        "county": entity.get("stateProvince"),
        "city": entity.get("city"),
        "grid_reference": entity.get("gridReference"),
        "latitude": entity.get("latitude"),
        "longitude": entity.get("longitude"),
        "project_type": entity.get("projectType"),
        "master_project_name": entity.get("masterProjectName"),
        "validator_name": entity.get("validatorName"),
        "project_start_date": entity.get("projectStartDate"),
        "project_end_date": entity.get("projectEndDate"),
        "project_submitted_date": entity.get("projectSubmittedDate"),
        "project_registration_date": entity.get("projectRegistrationDate"),
        "project_activation_date": entity.get("projectActivationDate"),
        "credit_period_start": entity.get("creditPeriodStartDate"),
        "credit_period_end": entity.get("creditPeriodEndDate"),
        "pius_listed": entity.get("piusListed"),
        "units_issued_flag": entity.get("units"),
        # From `auxiliaries`:
        "area_ha": aux.get("area"),
        "ha_broadleaf": aux.get("ha_broadleaf"),
        "ha_conifer": aux.get("ha_conifer"),
        "ha_mixed_broadleaf": aux.get("ha_mixed_broadleaf"),
        "ha_mixed_conifer": aux.get("ha_mixed_conifer"),
        "biodiversity_score": aux.get("biodiversity"),
        "economy_score": aux.get("economy"),
        "community_score": aux.get("community"),
        "water_score": aux.get("water"),
        "project_duration_years": aux.get("project_duration"),
        "total_carbon_sequestration_tco2e": aux.get("Totalcarbonsequestration"),
        "predicted_claimable_carbon_tco2e": aux.get("Predictedclaimablecarbon"),
        "predicted_buffer_contribution_tco2e": aux.get("Predictedcontributiontobuffer"),
    }


def fetch_all_projects(session: requests.Session) -> list[dict]:
    all_records = []
    start = 0

    first_page = fetch_page(session, start=0)
    total_entities = first_page.get("totalEntities", 0)
    total_pages = first_page.get("totalPages", 1)
    print(f"Total projects: {total_entities} across {total_pages} pages")

    all_records.extend(flatten_record(e) for e in first_page.get("entities", []))
    start += PAGE_SIZE

    while start < total_entities:
        page_num = start // PAGE_SIZE + 1
        print(f"Fetching page {page_num}/{total_pages} (start={start})...")
        try:
            page = fetch_page(session, start=start)
        except requests.HTTPError as e:
            print(f"  Error on page {page_num}: {e}. Retrying once after a pause...",
                  file=sys.stderr)
            time.sleep(3)
            page = fetch_page(session, start=start)

        entities = page.get("entities", [])
        if not entities:
            print("  No entities returned -- stopping early.")
            break

        all_records.extend(flatten_record(e) for e in entities)
        start += PAGE_SIZE
        time.sleep(REQUEST_DELAY_SEC)

    return all_records


def write_csv(records: list[dict], path: Path):
    if not records:
        print(f"No records to write for {path}")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(records[0].keys()))
        writer.writeheader()
        writer.writerows(records)
    print(f"Wrote {len(records)} records to {path.resolve()}")


def main():
    session = build_session()
    records = fetch_all_projects(session)

    write_csv(records, Path("wcc_projects_all.csv"))

    border_records = [r for r in records if r["county"] in COUNTY_FILTER]
    write_csv(border_records, Path("wcc_projects_border_counties.csv"))

    if border_records:
        from collections import Counter
        counts = Counter(r["county"] for r in border_records)
        print("\nBorder-county project counts:")
        for county, n in counts.items():
            print(f"  {county}: {n}")
    else:
        print("\nNo projects matched the border-county filter -- check that "
              "`stateProvince` values in the data match COUNTY_FILTER exactly "
              "(e.g. capitalisation, 'Scottish Borders' vs 'The Scottish Borders').")


if __name__ == "__main__":
    main()
