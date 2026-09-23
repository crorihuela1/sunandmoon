#!/usr/bin/env python3
"""
Referral OS — unified ingest across all 17 segments.

  Google Places  → 12 supply-side, 30A-hyperlocal segments
  Apollo         →  5 demand-side, drive-market + southeast segments

Pure standard library. Talks to Supabase via REST. No pip installs.

Run from the referral-os/ folder:
    python3 ingest_all.py                                    # all segments
    python3 ingest_all.py --only-segments wedding-planner-local golf-cart-rental
    python3 ingest_all.py --skip-segments restaurant-30a     # skip heavy segments
    python3 ingest_all.py --dry-run                          # don't write to DB

After running, query the categorized view in Supabase:
    SELECT * FROM v_partners_categorized;
    SELECT * FROM v_tier_summary;
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HTTP_TIMEOUT = 25

# =====================================================================
# Geographic constants
# =====================================================================
EMERALD_COAST_METROS = [
    "Santa Rosa Beach FL",
    "Seagrove Beach FL",
    "Seaside FL",
    "Watercolor FL",
    "Rosemary Beach FL",
    "Alys Beach FL",
    "Grayton Beach FL",
    "Blue Mountain Beach FL",
    "Miramar Beach FL",
    "Destin FL",
    "Inlet Beach FL",
    "Panama City Beach FL",
    "Fort Walton Beach FL",
]

DRIVE_MARKET_LOCATIONS = [
    "Birmingham, Alabama",
    "Atlanta, Georgia",
    "Nashville, Tennessee",
    "New Orleans, Louisiana",
    "Memphis, Tennessee",
    "Jackson, Mississippi",
    "Knoxville, Tennessee",
    "Chattanooga, Tennessee",
]

SOUTHEAST_LOCATIONS = [
    "Alabama, US",
    "Georgia, US",
    "Tennessee, US",
    "Louisiana, US",
    "Mississippi, US",
    "Florida, US",
    "Texas, US",
]

# =====================================================================
# Segment definitions
# =====================================================================
# Each segment lists:
#   slug         — matches DB row in segments table
#   source       — google_places | apollo
#   queries      — list of search strings (GP) or keyword tags (Apollo)
#   metros       — list of geographic anchors
#   apollo_extra — Apollo-specific filters (employee ranges, etc.)
# =====================================================================
SEGMENTS = [
    # ----- TIER 1 -----
    {
        "slug": "wedding-planner-local",
        "source": "google_places",
        "queries": ["wedding planner", "wedding coordinator", "destination wedding planner"],
        "metros": EMERALD_COAST_METROS,
    },
    {
        "slug": "wedding-planner-feeder",
        "source": "apollo",
        "queries": ["destination wedding", "wedding planner", "wedding planning"],
        "locations": DRIVE_MARKET_LOCATIONS,
        "apollo_extra": {"organization_num_employees_ranges": ["1,10", "11,50"]},
    },
    {
        "slug": "corp-retreat-planner",
        "source": "apollo",
        "queries": ["corporate retreat", "executive offsite", "team offsite", "leadership retreat"],
        "locations": DRIVE_MARKET_LOCATIONS,
        "apollo_extra": {"organization_num_employees_ranges": ["1,10", "11,50", "51,200"]},
    },

    # ----- TIER 2 -----
    {
        "slug": "travel-advisor",
        "source": "apollo",
        "queries": ["travel advisor", "travel agency", "family travel"],
        "locations": SOUTHEAST_LOCATIONS,
        "apollo_extra": {"organization_num_employees_ranges": ["1,10", "11,50"]},
    },
    {
        "slug": "family-reunion-planner",
        "source": "apollo",
        "queries": ["family reunion", "multi-generational travel", "group travel"],
        "locations": SOUTHEAST_LOCATIONS,
    },
    {
        "slug": "golf-cart-rental",
        "source": "google_places",
        "queries": ["golf cart rental", "LSV rental"],
        "metros": EMERALD_COAST_METROS,
    },
    {
        "slug": "bike-rental",
        "source": "google_places",
        "queries": ["bike rental", "beach service rental", "beach chair rental"],
        "metros": EMERALD_COAST_METROS,
    },

    # ----- TIER 3 -----
    {
        "slug": "photographer-family",
        "source": "google_places",
        "queries": ["family photographer", "vacation photographer", "beach photographer"],
        "metros": EMERALD_COAST_METROS,
    },
    {
        "slug": "wedding-photographer",
        "source": "google_places",
        "queries": ["wedding photographer", "engagement photographer"],
        "metros": EMERALD_COAST_METROS,
    },
    {
        "slug": "private-chef",
        "source": "google_places",
        "queries": ["private chef", "personal chef", "in-home catering"],
        "metros": EMERALD_COAST_METROS,
    },
    {
        "slug": "yoga-instructor",
        "source": "google_places",
        "queries": ["yoga instructor", "private yoga", "beach yoga"],
        "metros": EMERALD_COAST_METROS,
    },

    # ----- TIER 4 -----
    {
        "slug": "boutique-hotel-30a",
        "source": "google_places",
        "queries": ["boutique hotel", "inn", "bed and breakfast"],
        "metros": EMERALD_COAST_METROS,
    },
    {
        "slug": "concierge-service",
        "source": "google_places",
        "queries": ["concierge service", "vacation concierge", "lifestyle management"],
        "metros": EMERALD_COAST_METROS,
    },

    # ----- TIER 5 -----
    {
        "slug": "restaurant-30a",
        "source": "google_places",
        "queries": ["restaurant", "fine dining"],
        "metros": EMERALD_COAST_METROS,
    },
    {
        "slug": "realtor-30a",
        "source": "google_places",
        "queries": ["real estate agent", "realtor"],
        "metros": EMERALD_COAST_METROS,
    },
    {
        "slug": "event-venue",
        "source": "google_places",
        "queries": ["event venue", "wedding venue", "beach venue"],
        "metros": EMERALD_COAST_METROS,
    },
    {
        "slug": "florist-30a",
        "source": "google_places",
        "queries": ["florist", "wedding florist"],
        "metros": EMERALD_COAST_METROS,
    },
]

# =====================================================================
# .env loader
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
# Google Places client
# =====================================================================
def gp_search(api_key: str, query: str) -> list[dict]:
    body = json.dumps({"textQuery": query, "pageSize": 20}).encode()
    req = urllib.request.Request(
        "https://places.googleapis.com/v1/places:searchText",
        data=body, method="POST",
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
        return json.loads(r.read()).get("places", [])


def gp_to_company_row(p: dict, source_query: str) -> dict:
    comps = p.get("addressComponents", [])
    city = next((c["longText"] for c in comps if "locality" in c.get("types", [])), None)
    state = next((c["shortText"] for c in comps if "administrative_area_level_1" in c.get("types", [])), None)
    postal = next((c["longText"] for c in comps if "postal_code" in c.get("types", [])), None)
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
            "source_query": source_query,
        },
    }


# =====================================================================
# Apollo client
# =====================================================================
def apollo_org_search(api_key: str, keywords: list[str], locations: list[str], extra: dict) -> list[dict]:
    """Single Apollo Org Search call. Apollo accepts location array natively."""
    body: dict = {
        "q_organization_keyword_tags": keywords,
        "organization_locations": locations,
        "per_page": 50,
        "page": 1,
    }
    body.update(extra or {})
    req = urllib.request.Request(
        "https://api.apollo.io/v1/mixed_companies/search",
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "X-Api-Key": api_key,
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "User-Agent": "ReferralOS/1.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        data = json.loads(r.read())
    return data.get("organizations", []) or data.get("accounts", [])


def apollo_to_company_row(o: dict, source_query: str) -> dict:
    primary_phone = o.get("primary_phone") or {}
    return {
        "apollo_organization_id": o.get("id"),
        "name": o.get("name"),
        "domain": (o.get("primary_domain") or "").lower() or None,
        "website": o.get("website_url"),
        "description": o.get("short_description"),
        "industry": o.get("industry"),
        "employee_count": o.get("estimated_num_employees"),
        "founded_year": o.get("founded_year"),
        "city": o.get("city"),
        "state": o.get("state"),
        "country": o.get("country") or "US",
        "main_phone": o.get("phone") or primary_phone.get("number"),
        "linkedin_url": o.get("linkedin_url"),
        "facebook_url": o.get("facebook_url"),
        "data_sources": ["apollo"],
        "metadata": {
            "source_query": source_query,
            "apollo_industry": o.get("industry"),
            "apollo_keywords": o.get("keywords"),
        },
    }


# =====================================================================
# Supabase REST
# =====================================================================
def sb_headers(key: str) -> dict[str, str]:
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": "ReferralOS/1.0",
    }


def sb_get(base: str, key: str, path: str) -> list[dict]:
    req = urllib.request.Request(
        f"{base.rstrip('/')}/rest/v1/{path}",
        headers={**sb_headers(key), "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read())


def sb_upsert(base: str, key: str, table: str, rows: list[dict], on_conflict: str) -> list[dict]:
    if not rows:
        return []
    url = f"{base.rstrip('/')}/rest/v1/{table}?on_conflict={on_conflict}"
    req = urllib.request.Request(
        url,
        data=json.dumps(rows).encode(),
        method="POST",
        headers={
            **sb_headers(key),
            "Prefer": "return=representation,resolution=merge-duplicates",
        },
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read())


# =====================================================================
# Per-segment ingestors
# =====================================================================
def ingest_google_places_segment(
    seg: dict, segment_id: str, google_key: str,
    base: str, sb_key: str, dry_run: bool,
) -> tuple[int, int]:
    metros = seg["metros"]
    queries = seg["queries"]
    by_pid: dict[str, tuple[dict, str]] = {}  # place_id -> (place, source_query)

    print(f"  → searching {len(queries)} keyword(s) × {len(metros)} metros = {len(queries)*len(metros)} calls")
    for q in queries:
        for metro in metros:
            full_q = f"{q} in {metro}"
            try:
                places = gp_search(google_key, full_q)
            except urllib.error.HTTPError as e:
                print(f"    ✗ {full_q}: HTTP {e.code}")
                continue
            except Exception as e:  # noqa: BLE001
                print(f"    ✗ {full_q}: {e}")
                continue
            for p in places:
                if p["id"] not in by_pid:
                    by_pid[p["id"]] = (p, full_q)
            time.sleep(0.1)
    print(f"  → {len(by_pid)} unique places found")

    if not by_pid or dry_run:
        return len(by_pid), 0

    rows = [gp_to_company_row(p, sq) for p, sq in by_pid.values()]
    try:
        upserted = sb_upsert(base, sb_key, "companies", rows, on_conflict="google_place_id")
    except urllib.error.HTTPError as e:
        print(f"  ✗ DB upsert failed: HTTP {e.code} — {e.read().decode()[:300]}")
        return len(by_pid), 0

    links = [{
        "company_id": c["id"], "segment_id": segment_id,
        "relevance_score": 75, "status": "prospect",
    } for c in upserted]
    sb_upsert(base, sb_key, "company_segments", links, on_conflict="company_id,segment_id")
    return len(by_pid), len(upserted)


def ingest_apollo_segment(
    seg: dict, segment_id: str, apollo_key: str,
    base: str, sb_key: str, dry_run: bool,
) -> tuple[int, int]:
    by_id: dict[str, tuple[dict, str]] = {}

    print(f"  → 1 Apollo search across {len(seg['locations'])} locations")
    full_q = " | ".join(seg["queries"])
    try:
        orgs = apollo_org_search(
            apollo_key,
            keywords=seg["queries"],
            locations=seg["locations"],
            extra=seg.get("apollo_extra", {}),
        )
    except urllib.error.HTTPError as e:
        print(f"    ✗ Apollo HTTP {e.code}: {e.read().decode()[:300]}")
        return 0, 0
    except Exception as e:  # noqa: BLE001
        print(f"    ✗ Apollo error: {e}")
        return 0, 0

    for o in orgs:
        oid = o.get("id")
        if oid and oid not in by_id:
            by_id[oid] = (o, full_q)
    print(f"  → {len(by_id)} unique orgs returned")

    if not by_id or dry_run:
        return len(by_id), 0

    rows = [apollo_to_company_row(o, sq) for o, sq in by_id.values()]
    try:
        upserted = sb_upsert(base, sb_key, "companies", rows, on_conflict="apollo_organization_id")
    except urllib.error.HTTPError as e:
        print(f"  ✗ DB upsert failed: HTTP {e.code} — {e.read().decode()[:300]}")
        return len(by_id), 0

    links = [{
        "company_id": c["id"], "segment_id": segment_id,
        "relevance_score": 70, "status": "prospect",
    } for c in upserted]
    sb_upsert(base, sb_key, "company_segments", links, on_conflict="company_id,segment_id")
    return len(by_id), len(upserted)


# =====================================================================
# Main
# =====================================================================
def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--only-segments", nargs="+", default=None,
                   help="Process only these segment slugs.")
    p.add_argument("--skip-segments", nargs="+", default=None,
                   help="Skip these segment slugs.")
    p.add_argument("--dry-run", action="store_true",
                   help="Search but don't write to DB.")
    args = p.parse_args()

    env = load_env()
    google_key = env.get("GOOGLE_PLACES_API_KEY", "")
    apollo_key = env.get("APOLLO_API_KEY", "")
    base = env.get("SUPABASE_URL", "")
    sb_key = env.get("SUPABASE_SERVICE_ROLE_KEY", "")

    for label, val in [
        ("GOOGLE_PLACES_API_KEY", google_key),
        ("APOLLO_API_KEY", apollo_key),
        ("SUPABASE_URL", base),
        ("SUPABASE_SERVICE_ROLE_KEY", sb_key),
    ]:
        if not val:
            print(f"❌ {label} is empty in .env")
            return 1

    # Fetch all segment IDs upfront
    print("Loading segments from DB ...")
    db_segs = sb_get(base, sb_key, "segments?select=id,slug,name,priority_tier,target_count&order=priority_tier")
    segs_by_slug = {s["slug"]: s for s in db_segs}

    # Build work list
    work = []
    for seg in SEGMENTS:
        if args.only_segments and seg["slug"] not in args.only_segments:
            continue
        if args.skip_segments and seg["slug"] in args.skip_segments:
            continue
        db_seg = segs_by_slug.get(seg["slug"])
        if not db_seg:
            print(f"  ⚠ segment '{seg['slug']}' not in DB — skipping")
            continue
        work.append((seg, db_seg))

    print(f"\nWill process {len(work)} segments ({'DRY RUN' if args.dry_run else 'WRITING TO DB'})\n")

    # Run
    results = []
    for i, (seg, db_seg) in enumerate(work, 1):
        print(f"[{i}/{len(work)}] tier {db_seg['priority_tier']} · {seg['slug']} · via {seg['source']}")
        if seg["source"] == "google_places":
            found, written = ingest_google_places_segment(
                seg, db_seg["id"], google_key, base, sb_key, args.dry_run,
            )
        elif seg["source"] == "apollo":
            found, written = ingest_apollo_segment(
                seg, db_seg["id"], apollo_key, base, sb_key, args.dry_run,
            )
        else:
            print(f"  ✗ unknown source: {seg['source']}")
            continue
        results.append({
            "tier": db_seg["priority_tier"],
            "slug": seg["slug"],
            "name": db_seg["name"],
            "source": seg["source"],
            "target": db_seg["target_count"],
            "found": found,
            "written": written,
        })
        print()

    # Summary
    print("=" * 78)
    print("CATEGORIZED RESULTS")
    print("=" * 78)
    by_tier: dict[int, list[dict]] = {}
    for r in results:
        by_tier.setdefault(r["tier"], []).append(r)

    grand_total = 0
    for tier in sorted(by_tier):
        print(f"\n── TIER {tier} ──")
        print(f"{'segment':<28} {'src':<14} {'target':>6} {'found':>6} {'in DB':>6}")
        print("-" * 78)
        tier_total = 0
        for r in by_tier[tier]:
            print(f"{r['slug']:<28} {r['source']:<14} {r['target']:>6} {r['found']:>6} {r['written']:>6}")
            tier_total += r["written"]
        print(f"{'TIER SUBTOTAL':<28} {' ':<14} {' ':>6} {' ':>6} {tier_total:>6}")
        grand_total += tier_total

    print(f"\n{'=' * 78}")
    print(f"GRAND TOTAL: {grand_total} partner records in Postgres across {len(results)} segments")
    print(f"{'=' * 78}")
    print()
    print("Open Supabase → SQL Editor → run these to inspect:")
    print("    SELECT * FROM v_tier_summary;")
    print("    SELECT * FROM v_partners_categorized LIMIT 50;")
    return 0


if __name__ == "__main__":
    sys.exit(main())
