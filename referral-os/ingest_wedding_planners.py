#!/usr/bin/env python3
"""
Smoke ingest — pull wedding planners from 30A via Google Places API
and land them in the Postgres companies table + link to the segment.

Pure standard library (no pip installs). Talks to Supabase via REST.

Run from the referral-os/ folder:
    python3 ingest_wedding_planners.py
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# =====================================================================
# Config
# =====================================================================
SEGMENT_SLUG = "wedding-planner-local"
METROS = [
    "Santa Rosa Beach FL",
    "Seagrove Beach FL",
    "Seaside FL",
    "Watercolor FL",
    "Rosemary Beach FL",
    "Miramar Beach FL",
    "Destin FL",
    "Inlet Beach FL",
]
QUERY_TEMPLATE = "wedding planner in {metro}"
HTTP_TIMEOUT = 20

# =====================================================================
# .env
# =====================================================================
def load_env(path: str = ".env") -> dict[str, str]:
    env: dict[str, str] = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


# =====================================================================
# Google Places
# =====================================================================
def google_places_search(api_key: str, query: str) -> dict:
    body = json.dumps({"textQuery": query, "pageSize": 20}).encode()
    req = urllib.request.Request(
        "https://places.googleapis.com/v1/places:searchText",
        data=body,
        method="POST",
        headers={
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": (
                "places.id,places.displayName,places.formattedAddress,"
                "places.websiteUri,places.nationalPhoneNumber,"
                "places.rating,places.userRatingCount,"
                "places.location,places.addressComponents"
            ),
            "Content-Type": "application/json",
            "User-Agent": "ReferralOS/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read())


def place_to_company(p: dict) -> dict:
    """Translate a Google Places result into a row for our companies table."""
    comps = p.get("addressComponents", [])
    city = next(
        (c["longText"] for c in comps if "locality" in c.get("types", [])),
        None,
    )
    state = next(
        (c["shortText"] for c in comps if "administrative_area_level_1" in c.get("types", [])),
        None,
    )
    postal = next(
        (c["longText"] for c in comps if "postal_code" in c.get("types", [])),
        None,
    )
    loc = p.get("location", {}) or {}
    website = p.get("websiteUri")
    domain = None
    if website:
        try:
            host = urllib.parse.urlparse(website).hostname
            if host:
                domain = host.lower().removeprefix("www.")
        except Exception:  # noqa: BLE001
            pass

    return {
        "google_place_id": p["id"],
        "name": p["displayName"]["text"],
        "domain": domain,
        "city": city,
        "state": state,
        "postal_code": postal,
        "country": "US",
        "latitude": loc.get("latitude"),
        "longitude": loc.get("longitude"),
        "main_phone": p.get("nationalPhoneNumber"),
        "website": website,
        "address_line1": p.get("formattedAddress"),
        "data_sources": ["google_places"],
        "metadata": {
            "rating": p.get("rating"),
            "rating_count": p.get("userRatingCount"),
            "source_query": p.get("_source_query"),  # we'll add this below
        },
    }


# =====================================================================
# Supabase REST
# =====================================================================
def sb_headers(service_key: str) -> dict[str, str]:
    return {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
        "User-Agent": "ReferralOS/1.0",
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


# =====================================================================
# Main
# =====================================================================
def main() -> int:
    env = load_env()
    google_key = env.get("GOOGLE_PLACES_API_KEY", "")
    base_url = env.get("SUPABASE_URL", "")
    service_key = env.get("SUPABASE_SERVICE_ROLE_KEY", "")

    for label, val in [
        ("GOOGLE_PLACES_API_KEY", google_key),
        ("SUPABASE_URL", base_url),
        ("SUPABASE_SERVICE_ROLE_KEY", service_key),
    ]:
        if not val:
            print(f"❌ {label} is empty in .env")
            return 1

    # --- 1. fetch segment id ---
    print(f"Looking up segment '{SEGMENT_SLUG}' ...")
    segs = sb_get(base_url, service_key, f"segments?slug=eq.{SEGMENT_SLUG}&select=id,name")
    if not segs:
        print(f"❌ Segment '{SEGMENT_SLUG}' not found. Did you run schema/002_seed.sql?")
        return 1
    segment_id = segs[0]["id"]
    print(f"  → {segs[0]['name']}  ({segment_id})")

    # --- 2. search google places ---
    print(f"\nSearching Google Places across {len(METROS)} 30A metros ...")
    by_pid: dict[str, dict] = {}
    for metro in METROS:
        q = QUERY_TEMPLATE.format(metro=metro)
        try:
            data = google_places_search(google_key, q)
        except urllib.error.HTTPError as e:
            print(f"  {metro}: HTTP {e.code} — {e.read().decode()[:200]}")
            continue
        except Exception as e:  # noqa: BLE001
            print(f"  {metro}: ERROR {e}")
            continue
        places = data.get("places", [])
        added = 0
        for p in places:
            if p["id"] not in by_pid:
                p["_source_query"] = q
                by_pid[p["id"]] = p
                added += 1
        print(f"  {metro}: {len(places)} returned, {added} new")
        time.sleep(0.2)

    print(f"\nUnique businesses found: {len(by_pid)}")
    if not by_pid:
        print("Nothing to upsert. Bailing.")
        return 0

    # --- 3. upsert companies ---
    print("\nUpserting into companies ...")
    rows = [place_to_company(p) for p in by_pid.values()]
    try:
        upserted = sb_upsert(
            base_url, service_key,
            table="companies",
            rows=rows,
            on_conflict="google_place_id",
        )
    except urllib.error.HTTPError as e:
        print(f"❌ HTTP {e.code} during company upsert:")
        print(e.read().decode()[:600])
        return 1
    print(f"  → {len(upserted)} rows back from Postgres")

    # --- 4. link to segment ---
    print(f"\nLinking companies to segment '{SEGMENT_SLUG}' ...")
    links = [
        {
            "company_id": c["id"],
            "segment_id": segment_id,
            "relevance_score": 75,  # baseline; refine later
            "status": "prospect",
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

    # --- 5. summary ---
    print()
    print("=" * 62)
    print("RESULTS")
    print("=" * 62)
    print(f"Segment:           {SEGMENT_SLUG}")
    print(f"Places searched:   {len(METROS)}")
    print(f"Unique businesses: {len(by_pid)}")
    print(f"Companies in DB:   {len(upserted)}")
    print(f"Segment links:     {len(links)}")
    print()
    print("Sample (first 5):")
    for r in rows[:5]:
        site = r["website"] or "—"
        print(f"  • {r['name']:40s} {r['city']}, {r['state']:2}  →  {site}")
    print()
    print("👉 Now open Supabase → Table Editor → companies to see them all.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
