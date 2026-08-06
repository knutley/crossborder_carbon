"""
Retry the detail-fetch for rows that failed in the first pass of
registry_proj_page_scrape.py, using the partially-filled enriched CSV as
input. Only re-fetches rows with a blank category_name (our proxy for "the
detail fetch failed or was never attempted"); leaves successful rows alone.

Logs the actual exception for each retry failure to a text file, since the
first run's failures scrolled past in the terminal -- this run will tell us
definitively whether it's a timeout, a 429/500, or something else.

USAGE
-----
    python retry_failed_details.py

Reads:  wcc_projects_border_counties_enriched.csv
Writes: wcc_projects_border_counties_enriched.csv  (updated in place)
        retry_failures.log  (only if any retries still fail)
"""

import csv
import sys
import time
from pathlib import Path

import requests

ENRICHED_CSV = Path("wcc_projects_border_counties_enriched.csv")
LOG_PATH = Path("retry_failures.log")

REGISTRY_HOME = "https://registry.spglobal.com/uklandcarbonregistry/public/wcc"
PROJECT_DETAIL_URL_TMPL = (
    "https://prod-us.api.platts.com/ci-raas-prod/br-reg/rest/"
    "public-report-manager/getProjectById/{project_id}/{application}"
)

# Longer pause than the first pass, in case the server was rate-limiting
# sustained traffic.
REQUEST_DELAY_SEC = 2.0
MANUAL_XSRF_TOKEN = None

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
        print(f"Warning: could not pre-visit registry home page ({e})", file=sys.stderr)
    token = MANUAL_XSRF_TOKEN or session.cookies.get("XSRF-TOKEN")
    if token:
        session.headers["x-xsrf-token"] = token
    return session


def fetch_project_detail(session: requests.Session, project_id) -> dict:
    application = BASE_HEADERS.get("application", "Markit")
    url = PROJECT_DETAIL_URL_TMPL.format(project_id=project_id, application=application)
    resp = session.get(url, timeout=90)  # generous timeout for a retry pass
    resp.raise_for_status()
    return resp.json()


def flatten_project_detail(detail: dict) -> dict:
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


def main():
    if not ENRICHED_CSV.exists():
        print(f"Couldn't find {ENRICHED_CSV.resolve()}", file=sys.stderr)
        sys.exit(1)

    with ENRICHED_CSV.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    # A row "failed" if category_name is blank -- it's null in the source
    # for zero legitimate projects (confirmed absent in the schema unless
    # populated), so an empty string here means the detail-fetch never
    # populated it.
    to_retry = [r for r in rows if not r.get("category_name")]
    print(f"{len(rows)} total rows, {len(to_retry)} need retrying.")

    if not to_retry:
        print("Nothing to retry.")
        return

    session = build_session()
    log_lines = []
    still_failed = 0

    for i, row in enumerate(to_retry, start=1):
        project_id = row.get("project_id")
        project_name = row.get("project_name", "")
        print(f"[{i}/{len(to_retry)}] Retrying project_id={project_id} ({project_name})...")

        try:
            detail = fetch_project_detail(session, project_id)
            detail_fields = flatten_project_detail(detail)
            row.update(detail_fields)
            print("  OK")
        except Exception as e:
            still_failed += 1
            msg = f"project_id={project_id} ({project_name}): {type(e).__name__}: {e}"
            print(f"  Still failed: {msg}", file=sys.stderr)
            log_lines.append(msg)

        time.sleep(REQUEST_DELAY_SEC)

    # rows list contains the same dict objects as to_retry (mutated in
    # place via row.update above), so writing `rows` back out reflects
    # both the untouched successes and the newly-filled retries.
    fieldnames = list(rows[0].keys())
    with ENRICHED_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nUpdated {ENRICHED_CSV.resolve()} in place.")
    print(f"{len(to_retry) - still_failed}/{len(to_retry)} retries succeeded.")

    if log_lines:
        LOG_PATH.write_text("\n".join(log_lines), encoding="utf-8")
        print(f"{still_failed} still failed -- reasons logged to {LOG_PATH.resolve()}")


if __name__ == "__main__":
    main()
