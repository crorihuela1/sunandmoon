#!/usr/bin/env python3
"""
INGEST — expanded 30A + Panhandle wedding planner coverage.

What this adds on top of ingest_wedding_planners.py (the original 41-row run)
-----------------------------------------------------------------------------
  • 14 metros instead of 8 (adds Watersound, Blue Mountain Beach, Grayton,
    Alys Beach, Panama City Beach, Mexico Beach, Pensacola, Gulf Breeze,
    Fort Walton Beach, Niceville, Freeport)
  • 5 query angles per metro instead of 1:
      - "wedding planner"
      - "wedding coordinator"     ← surfaces a different cluster
      - "wedding designer"         ← higher-end / styling shops
      - "bridal coordinator"       ← smaller but distinct
      - "destination wedding planner"

Why 5 angles
------------
Google Places weighs query terms heavily. Each phrasing surfaces a slightly
different ranked list — total uniques after dedup is often 2-3x what one
query alone returns. This is the same trick that got the photographer
ingest from ~15 to 43.

Idempotent: existing rows from the original ingest are matched by
google_place_id and merged, not duplicated.

Run it
------
    cd "/Users/samantha/Documents/Claude/Projects/Sun & Moon at 30a/referral-os"
    python3 ingest_wedding_local_expansion.py
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SEGMENT_SLUG = "wedding-planner-local"

METROS = [
    # Original 8
    "Santa Rosa Beach FL",
    "Seagrove Beach FL",
    "Seaside FL",
    "Watercolor FL",
    "Rosemary Beach FL",
    "Miramar Beach FL",
    "Destin FL",
    "Inlet Beach FL",
    # Added in this expansion — rest of 30A
    "Alys Beach FL",
    "Grayton Beach FL",
    "Blue Mountain Beach FL",
    "Watersound FL",
    # Panhandle adjacent — same wedding catchment
    "Panama City Beach FL",
    "Mexico Beach FL",
    "Pensacola FL",
    "Gulf Breeze FL",
    "Navarre Beach FL",
    "Fort Walton Beach FL",
    "Niceville FL",
    "Freeport FL",
]

QUERY_TEMPLATES = [
    "wedding planner in {metro}",
    "wedding coordinator in {metro}",
    "wedding designer in {metro}",
    "bridal coordinator in {metro}",
    "destination wedding planner in {metro}",
]

HTTP_TIMEOUT = 20


def load_env(path: str = ".env") -> dict[str, str]:
    env: dict[str, str] = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


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


def place_to_company(p: dict, query_angle: str) -> dict:
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
        "name":            p["displayName"]["text"],
        "domain":          domain,
        "city":            city,
        "state":           state,
        "postal_code":     postal,
        "country":         "US",
        "latitude":        loc.get("latitude"),
        "longitude":       loc.get("longitude"),
        "main_phone":      p.get("nationalPhoneNumber"),
        "website":         website,
        "address_line1":   p.get("formattedAddress"),
        "data_sources":    ["google_places"],
        "metadata": {
            "rating":        p.get("rating"),
            "rating_count":  p.get("userRatingCount"),
            "source_query":  p.get("_source_query"),
            "query_angle":   query_angle,
            "market_role":   "local",
            "market_metro":  "30a",
        },
    }


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


def sb_upsert(base_url, service_key, table, rows, on_conflict):
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


def main() -> int:
    env = load_env()
    google_key  = env.get("GOOGLE_PLACES_API_KEY", "")
    base_url    = env.get("SUPABASE_URL", "")
    service_key = env.get("SUPABASE_SERVICE_ROLE_KEY", "")

    for label, val in [
        ("GOOGLE_PLACES_API_KEY",     google_key),
        ("SUPABASE_URL",              base_url),
        ("SUPABASE_SERVICE_ROLE_KEY", service_key),
    ]:
        if not val:
            print(f"❌ {label} is empty in .env")
            return 1

    print(f"Looking up segment '{SEGMENT_SLUG}' ...")
    segs = sb_get(base_url, service_key, f"segments?slug=eq.{SEGMENT_SLUG}&select=id,name")
    if not segs:
        print(f"❌ Segment '{SEGMENT_SLUG}' not found.")
        return 1
    segment_id = segs[0]["id"]
    print(f"  → {segs[0]['name']}  ({segment_id})")

    total_queries = len(METROS) * len(QUERY_TEMPLATES)
    print(f"\nSearching: {len(METROS)} metros × {len(QUERY_TEMPLATES)} angles = {total_queries} calls ...")

    by_pid: dict[str, dict] = {}
    angle_used: dict[str, str] = {}
    for metro in METROS:
        per_metro_added = 0
        for template in QUERY_TEMPLATES:
            q = template.format(metro=metro)
            angle = template.split(" in ")[0]
            try:
                data = google_places_search(google_key, q)
            except urllib.error.HTTPError as e:
                print(f"  {metro:25s} [{angle:28s}]: HTTP {e.code}")
                continue
            except Exception as e:  # noqa: BLE001
                print(f"  {metro:25s} [{angle:28s}]: ERROR {e}")
                continue
            places = data.get("places", [])
            new_here = 0
            for p in places:
                if p["id"] not in by_pid:
                    p["_source_query"] = q
                    by_pid[p["id"]] = p
                    angle_used[p["id"]] = angle
                    new_here += 1
            per_metro_added += new_here
            print(f"  {metro:25s} [{angle:28s}]: {len(places):2d} returned, {new_here:2d} new")
            time.sleep(0.2)
        print(f"  → {metro:25s} subtotal: {per_metro_added} new\n")

    print(f"Unique planners found this run: {len(by_pid)}")
    if not by_pid:
        print("Nothing to upsert.")
        return 0

    rows = [place_to_company(p, angle_used[p["id"]]) for p in by_pid.values()]

    print("\nUpserting into companies (existing rows merge by google_place_id) ...")
    try:
        upserted = sb_upsert(
            base_url, service_key,
            table="companies", rows=rows,
            on_conflict="google_place_id",
        )
    except urllib.error.HTTPError as e:
        print(f"❌ HTTP {e.code}: {e.read().decode()[:600]}")
        return 1
    print(f"  → {len(upserted)} rows back from Postgres")

    print(f"\nLinking to segment '{SEGMENT_SLUG}' ...")
    links = [
        {
            "company_id":      c["id"],
            "segment_id":      segment_id,
            "relevance_score": 80,  # local 30A = 80 (per convention)
            "status":          "prospect",
        }
        for c in upserted
    ]
    try:
        sb_upsert(
            base_url, service_key,
            table="company_segments", rows=links,
            on_conflict="company_id,segment_id",
        )
    except urllib.error.HTTPError as e:
        print(f"❌ segment link HTTP {e.code}: {e.read().decode()[:600]}")
        return 1
    print(f"  → {len(links)} segment links upserted")

    # Summary
    print()
    print("=" * 70)
    print("RESULTS — local 30A + Panhandle wedding-planner expansion")
    print("=" * 70)
    print(f"Segment:           {SEGMENT_SLUG}")
    print(f"Metros searched:   {len(METROS)}")
    print(f"Query angles:      {len(QUERY_TEMPLATES)}")
    print(f"Unique this run:   {len(by_pid)}")
    print(f"Companies in DB:   {len(upserted)}")
    print(f"Segment links:     {len(links)}")

    from collections import Counter
    angle_counts = Counter(angle_used.values())
    print(f"\nBy query angle (which phrase surfaced new rows first):")
    for angle, count in angle_counts.most_common():
        print(f"  {angle:28s}: {count}")

    print(f"\nTop 10 new (by review count):")
    sorted_rows = sorted(
        rows,
        key=lambda r: (r["metadata"].get("rating_count") or 0),
        reverse=True,
    )
    for r in sorted_rows[:10]:
        site   = (r["website"] or "—")[:50]
        rating = r["metadata"].get("rating") or "—"
        count  = r["metadata"].get("rating_count") or 0
        print(f"  ★ {rating}  ({count:>4})  {r['name'][:42]:42s}  {site}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
