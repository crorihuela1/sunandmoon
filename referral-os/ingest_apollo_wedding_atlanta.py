#!/usr/bin/env python3
"""
Apollo SMOKE INGEST — wedding planners in Atlanta.

Goal: prove the Apollo plumbing end-to-end with a small, focused pull
before running the unified ingest across all 5 Apollo segments.

What it does
------------
1. Loads keys from .env (no pip installs — pure stdlib).
2. Calls Apollo's /v1/mixed_companies/search for "wedding planner" in Atlanta.
3. Prints the first ~20 results so you can eyeball quality BEFORE writing.
4. Only writes to Postgres if you pass `--write`.
   When writing, upserts into `companies` and links to the
   `wedding-planner-feeder` segment via `company_segments`.

Run it
------
    cd "/Users/samantha/Documents/Claude/Projects/Sun & Moon at 30a/referral-os"

    # Step 1 — DRY-RUN (safe, no DB writes, costs ~1 Apollo credit per result returned)
    python3 ingest_apollo_wedding_atlanta.py

    # Step 2 — if results look good, actually write them
    python3 ingest_apollo_wedding_atlanta.py --write

Why dry-run first
-----------------
Apollo's "wedding planner" tag bucket is noisy — corporate event firms,
huge venues, and out-of-state false positives sometimes leak in. Eyeballing
the first page lets us tune `q_organization_keyword_tags` and the location
filter before we burn credits on Birmingham/Nashville/New Orleans too.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request

# ============================================================
# Config — tweak here, not by editing function bodies.
# ============================================================
SEGMENT_SLUG       = "wedding-planner-feeder"
APOLLO_SEARCH_URL  = "https://api.apollo.io/v1/mixed_companies/search"
TARGET_LOCATION    = "Atlanta, Georgia"
KEYWORD_TAGS       = ["wedding planner", "wedding planning", "event planner"]
PER_PAGE           = 20          # keep small for smoke test
PAGE               = 1
# Employee range: most legit wedding planners are 1-50. Filters out
# huge corporate event/AV firms that match the keyword but aren't us.
EMPLOYEE_RANGES    = ["1,10", "11,20", "21,50"]
HTTP_TIMEOUT       = 30

# ============================================================
# .env loader (same shape as the Google Places script)
# ============================================================
def load_env(path: str = ".env") -> dict[str, str]:
    env: dict[str, str] = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


# ============================================================
# Apollo
# ============================================================
def apollo_search(api_key: str) -> dict:
    body = {
        "q_organization_keyword_tags":   KEYWORD_TAGS,
        "organization_locations":        [TARGET_LOCATION],
        "organization_num_employees_ranges": EMPLOYEE_RANGES,
        "per_page": PER_PAGE,
        "page":     PAGE,
    }
    req = urllib.request.Request(
        APOLLO_SEARCH_URL,
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "X-Api-Key":      api_key,
            "Content-Type":   "application/json",
            "Accept":         "application/json",
            "Cache-Control":  "no-cache",
            # ↓↓↓ Cloudflare WAF blocks Python's default UA with err 1010.
            "User-Agent":     "ReferralOS/1.0 (https://github.com/cristian/referral-os)",
        },
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read())


def org_to_company(o: dict) -> dict:
    """Translate an Apollo organization into a row for our companies table."""
    website = o.get("website_url")
    domain  = o.get("primary_domain")
    if not domain and website:
        try:
            host = urllib.parse.urlparse(website).hostname
            if host:
                domain = host.lower().removeprefix("www.")
        except Exception:  # noqa: BLE001
            pass

    return {
        "apollo_organization_id": o.get("id"),
        "name":                   o.get("name"),
        "domain":                 domain,
        "website":                website,
        "industry":               o.get("industry"),
        "city":                   o.get("city"),
        "state":                  o.get("state"),
        "country":                o.get("country") or "US",
        "linkedin_url":           o.get("linkedin_url"),
        "facebook_url":           o.get("facebook_url"),
        "main_phone":             o.get("phone"),
        "address_line1":          o.get("street_address"),
        "postal_code":            o.get("postal_code"),
        "data_sources":           ["apollo"],
        "metadata": {
            "apollo_estimated_employees": o.get("estimated_num_employees"),
            "apollo_founded_year":        o.get("founded_year"),
            "apollo_industry":            o.get("industry"),
            "apollo_keywords":            o.get("keywords"),
            "source_query": {
                "keyword_tags": KEYWORD_TAGS,
                "location":     TARGET_LOCATION,
            },
        },
    }


# ============================================================
# Supabase REST (same helpers as ingest_wedding_planners.py)
# ============================================================
def sb_headers(service_key: str) -> dict[str, str]:
    return {
        "apikey":        service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type":  "application/json",
        "User-Agent":    "ReferralOS/1.0",
    }


def sb_get(base_url: str, service_key: str, path: str) -> list[dict]:
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/rest/v1/{path}",
        headers={**sb_headers(service_key), "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read())


def sb_upsert(
    base_url: str,
    service_key: str,
    table: str,
    rows: list[dict],
    on_conflict: str,
) -> list[dict]:
    if not rows:
        return []
    url = f"{base_url.rstrip('/')}/rest/v1/{table}?on_conflict={on_conflict}"
    req = urllib.request.Request(
        url,
        data=json.dumps(rows).encode(),
        method="POST",
        headers={
            **sb_headers(service_key),
            "Prefer": "return=representation,resolution=merge-duplicates",
        },
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read())


# ============================================================
# Pretty print
# ============================================================
def print_preview(orgs: list[dict]) -> None:
    print(f"\n{'=' * 70}")
    print(f"PREVIEW — first {len(orgs)} organizations from Apollo")
    print(f"{'=' * 70}")
    for i, o in enumerate(orgs, 1):
        name = (o.get("name") or "—")[:50]
        loc  = ", ".join(filter(None, [o.get("city"), o.get("state")])) or "—"
        emp  = o.get("estimated_num_employees")
        emp_s = f"{emp} emp" if emp else "—"
        dom  = o.get("primary_domain") or "—"
        ind  = (o.get("industry") or "—")[:25]
        print(f"  {i:2}. {name:50s}  {loc:22s}  {emp_s:8s}  {ind:25s}  {dom}")
    print()


# ============================================================
# Main
# ============================================================
def main() -> int:
    write_mode = "--write" in sys.argv

    env = load_env()
    apollo_key  = env.get("APOLLO_API_KEY", "")
    base_url    = env.get("SUPABASE_URL", "")
    service_key = env.get("SUPABASE_SERVICE_ROLE_KEY", "")

    for label, val in [
        ("APOLLO_API_KEY",            apollo_key),
        ("SUPABASE_URL",              base_url),
        ("SUPABASE_SERVICE_ROLE_KEY", service_key),
    ]:
        if not val:
            print(f"❌ {label} is empty in .env")
            return 1

    # --- 1. Apollo search ---
    print(f"Searching Apollo for '{', '.join(KEYWORD_TAGS)}' in {TARGET_LOCATION} ...")
    try:
        data = apollo_search(apollo_key)
    except urllib.error.HTTPError as e:
        print(f"❌ Apollo HTTP {e.code}: {e.read().decode()[:400]}")
        return 1

    orgs = data.get("organizations") or data.get("accounts") or []
    pag  = data.get("pagination", {})
    print(f"  → returned {len(orgs)} on page {pag.get('page', '?')} of {pag.get('total_pages', '?')}")
    print(f"  → total matching entries in Apollo: {pag.get('total_entries', '?')}")

    if not orgs:
        print("\nNothing returned. Try widening KEYWORD_TAGS or EMPLOYEE_RANGES.")
        return 0

    print_preview(orgs)

    # --- 2. Dry-run stops here ---
    if not write_mode:
        print("DRY-RUN — nothing written to Postgres.")
        print("If results look good, rerun with:  python3 ingest_apollo_wedding_atlanta.py --write")
        return 0

    # --- 3. Fetch segment id ---
    print(f"\nLooking up segment '{SEGMENT_SLUG}' ...")
    segs = sb_get(base_url, service_key, f"segments?slug=eq.{SEGMENT_SLUG}&select=id,name")
    if not segs:
        print(f"❌ Segment '{SEGMENT_SLUG}' not found. Did you run schema/002_seed.sql?")
        return 1
    segment_id = segs[0]["id"]
    print(f"  → {segs[0]['name']}  ({segment_id})")

    # --- 4. Upsert companies ---
    rows = [org_to_company(o) for o in orgs if o.get("id")]
    print(f"\nUpserting {len(rows)} companies (on_conflict=apollo_organization_id) ...")
    try:
        upserted = sb_upsert(
            base_url, service_key,
            table="companies",
            rows=rows,
            on_conflict="apollo_organization_id",
        )
    except urllib.error.HTTPError as e:
        print(f"❌ HTTP {e.code} during company upsert:")
        print(e.read().decode()[:600])
        return 1
    print(f"  → {len(upserted)} rows back from Postgres")

    # --- 5. Link to segment ---
    print(f"\nLinking companies to segment '{SEGMENT_SLUG}' ...")
    links = [
        {
            "company_id":      c["id"],
            "segment_id":      segment_id,
            "relevance_score": 70,  # slightly lower than local (75) — feeders are warmer, but less validated
            "status":          "prospect",
        }
        for c in upserted
    ]
    try:
        sb_upsert(
            base_url, service_key,
            table="company_segments",
            rows=links,
            on_conflict="company_id,segment_id",
        )
    except urllib.error.HTTPError as e:
        print(f"❌ HTTP {e.code} during segment link:")
        print(e.read().decode()[:600])
        return 1
    print(f"  → {len(links)} segment links upserted")

    # --- 6. Summary ---
    print()
    print("=" * 70)
    print("RESULTS")
    print("=" * 70)
    print(f"Segment:          {SEGMENT_SLUG}")
    print(f"Source:           Apollo /v1/mixed_companies/search")
    print(f"Location:         {TARGET_LOCATION}")
    print(f"Returned:         {len(orgs)}")
    print(f"Companies in DB:  {len(upserted)}")
    print(f"Segment links:    {len(links)}")
    print()
    print("👉 Open Supabase → Table Editor → companies and filter:")
    print("   data_sources @> '[\"apollo\"]'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
