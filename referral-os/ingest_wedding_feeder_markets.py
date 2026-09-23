#!/usr/bin/env python3
"""
DRIVE-MARKET INGEST — wedding planners across ALL 4 feeder markets.

Markets and why they matter for 30A
-----------------------------------
  • Atlanta GA      ~5 hours    largest SE metro; massive wedding economy
  • Birmingham AL   ~5-6 hours  smaller but wealthy enclaves (Mountain Brook)
  • Nashville TN    ~7 hours    Franklin TN is one of the country's top wedding markets
  • New Orleans LA  ~4-5 hours  closest big city; strong destination-wedding tradition

How the search works
--------------------
Google Places text search returns at most 20 results per query and weights
proximity heavily. So we query 6-8 high-income / wedding-dense
neighborhoods per market and dedup by google_place_id.

Each row is tagged with metadata.market_metro = "atlanta" | "birmingham" | ...
so you can later filter / segment outreach per city.

Run it
------
    # All four markets (Atlanta will be a no-op since it's already loaded)
    python3 ingest_wedding_feeder_markets.py

    # Skip Atlanta (since you already ran it)
    python3 ingest_wedding_feeder_markets.py --markets birmingham,nashville,new_orleans

    # Just one
    python3 ingest_wedding_feeder_markets.py --markets nashville

Cost
----
~7 queries × 4 markets = ~28 Google Places Text Search calls.
Google Places API (New) gives $200/mo free credit → cost ≈ $1-2. Free.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ============================================================
# Config — the 4 drive markets and their high-value zones
# ============================================================
SEGMENT_SLUG = "wedding-planner-feeder"

MARKETS: dict[str, list[str]] = {
    # ───────── Original 4 (already run) ─────────
    "atlanta": [
        "Atlanta GA",
        "Buckhead Atlanta GA",
        "Marietta GA",
        "Alpharetta GA",
        "Decatur GA",
        "Sandy Springs GA",
        "Roswell GA",
    ],
    "birmingham": [
        "Birmingham AL",
        "Mountain Brook AL",       # old-money wedding hub
        "Homewood AL",
        "Vestavia Hills AL",
        "Hoover AL",
        "Cahaba Heights AL",
    ],
    "nashville": [
        "Nashville TN",
        "Franklin TN",             # one of the country's top wedding markets
        "Brentwood TN",
        "Belle Meade Nashville TN",
        "Germantown Nashville TN",
        "East Nashville TN",
        "Murfreesboro TN",
    ],
    "new_orleans": [
        "New Orleans LA",
        "Uptown New Orleans LA",
        "Garden District New Orleans LA",
        "Metairie LA",
        "Mandeville LA",
        "Lakeview New Orleans LA",
        "Covington LA",
    ],

    # ───────── NEXT-TIER feeders (added 2026-05-29) ─────────
    # Each metro: 3-6 high-income / wedding-dense neighborhoods.
    "dallas": [
        "Dallas TX",
        "Highland Park Dallas TX",
        "University Park Dallas TX",
        "Preston Hollow Dallas TX",
        "Plano TX",
        "Frisco TX",
    ],
    "houston": [
        "Houston TX",
        "River Oaks Houston TX",
        "Memorial Houston TX",
        "Sugar Land TX",
        "The Woodlands TX",
    ],
    "memphis": [
        "Memphis TN",
        "Germantown TN",
        "Collierville TN",
        "East Memphis TN",
    ],
    "louisville": [
        "Louisville KY",
        "St. Matthews Louisville KY",
        "Crescent Hill Louisville KY",
        "Prospect KY",                 # wealthy suburb
    ],
    "chattanooga": [
        "Chattanooga TN",
        "Lookout Mountain TN",
        "Signal Mountain TN",
    ],
    "knoxville": [
        "Knoxville TN",
        "Sequoyah Hills Knoxville TN",
        "Farragut TN",
        "Sevierville TN",
    ],
    "charlotte": [
        "Charlotte NC",
        "Myers Park Charlotte NC",
        "SouthPark Charlotte NC",
        "Ballantyne Charlotte NC",
        "Davidson NC",                 # affluent Lake Norman ring
    ],
    "raleigh_durham": [
        "Raleigh NC",
        "Durham NC",
        "Chapel Hill NC",
        "Cary NC",
    ],
    "greenville_sc": [
        "Greenville SC",
        "Greer SC",
        "Simpsonville SC",
    ],
    "mobile": [
        "Mobile AL",
        "Fairhope AL",                 # affluent Eastern Shore
        "Spring Hill Mobile AL",
        "Daphne AL",
    ],
    "alabama_college_towns": [
        "Tuscaloosa AL",
        "Auburn AL",
        "Birmingham Hoover AL",        # extra hits on outer Birmingham
    ],
    "jackson_ms": [
        "Jackson MS",
        "Madison MS",                  # wealthy suburb
        "Ridgeland MS",
    ],
    "florida_drive": [
        "Tallahassee FL",              # state capital, 2hr drive
        "Jacksonville FL",
        "St. Augustine FL",            # wedding destination overlap
    ],
    "tampa_orlando": [
        "Tampa FL",
        "Saint Petersburg FL",
        "Sarasota FL",                 # wealthy Gulf coast
        "Orlando FL",
        "Winter Park FL",              # affluent Orlando enclave
    ],
}

QUERY_TEMPLATE = "wedding planner in {metro}"
HTTP_TIMEOUT   = 20


# ============================================================
# .env
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
# Google Places
# ============================================================
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


def place_to_company(p: dict, market: str) -> dict:
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
            "rating":       p.get("rating"),
            "rating_count": p.get("userRatingCount"),
            "source_query": p.get("_source_query"),
            "market_role":  "feeder",
            "market_metro": market,
        },
    }


# ============================================================
# Supabase REST
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
# CLI
# ============================================================
def parse_markets_arg(argv: list[str]) -> list[str]:
    """--markets atlanta,birmingham,nashville,new_orleans (default: all)"""
    for i, a in enumerate(argv):
        if a == "--markets" and i + 1 < len(argv):
            requested = [m.strip().lower() for m in argv[i + 1].split(",")]
            unknown = [m for m in requested if m not in MARKETS]
            if unknown:
                print(f"❌ Unknown markets: {unknown}. Valid: {list(MARKETS.keys())}")
                sys.exit(1)
            return requested
    return list(MARKETS.keys())


# ============================================================
# Per-market processor
# ============================================================
def run_market(
    market: str,
    google_key: str,
    base_url: str,
    service_key: str,
    segment_id: str,
) -> tuple[int, int, list[dict]]:
    """Returns (unique_found, db_count, rows_for_summary)."""
    queries = MARKETS[market]
    print(f"\n{'─' * 70}")
    print(f"  {market.upper().replace('_', ' ')}  —  {len(queries)} neighborhoods")
    print(f"{'─' * 70}")

    by_pid: dict[str, dict] = {}
    for metro in queries:
        q = QUERY_TEMPLATE.format(metro=metro)
        try:
            data = google_places_search(google_key, q)
        except urllib.error.HTTPError as e:
            print(f"  {metro:35s}: HTTP {e.code}")
            continue
        except Exception as e:  # noqa: BLE001
            print(f"  {metro:35s}: ERROR {e}")
            continue
        places = data.get("places", [])
        added = 0
        for p in places:
            if p["id"] not in by_pid:
                p["_source_query"] = q
                by_pid[p["id"]] = p
                added += 1
        print(f"  {metro:35s}: {len(places):3d} returned, {added:3d} new")
        time.sleep(0.2)

    if not by_pid:
        return (0, 0, [])

    rows = [place_to_company(p, market) for p in by_pid.values()]

    # upsert companies
    try:
        upserted = sb_upsert(
            base_url, service_key,
            table="companies",
            rows=rows,
            on_conflict="google_place_id",
        )
    except urllib.error.HTTPError as e:
        print(f"  ❌ upsert HTTP {e.code}: {e.read().decode()[:300]}")
        return (len(by_pid), 0, rows)

    # link to segment
    links = [
        {
            "company_id":      c["id"],
            "segment_id":      segment_id,
            "relevance_score": 70,
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
        print(f"  ❌ segment link HTTP {e.code}: {e.read().decode()[:300]}")

    return (len(by_pid), len(upserted), rows)


# ============================================================
# Main
# ============================================================
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

    markets_to_run = parse_markets_arg(sys.argv)

    # Look up segment id once
    print(f"Looking up segment '{SEGMENT_SLUG}' ...")
    segs = sb_get(base_url, service_key, f"segments?slug=eq.{SEGMENT_SLUG}&select=id,name")
    if not segs:
        print(f"❌ Segment '{SEGMENT_SLUG}' not found.")
        return 1
    segment_id = segs[0]["id"]
    print(f"  → {segs[0]['name']}  ({segment_id})")
    print(f"\nRunning markets: {', '.join(markets_to_run)}")

    market_results: dict[str, tuple[int, int, list[dict]]] = {}
    for m in markets_to_run:
        market_results[m] = run_market(m, google_key, base_url, service_key, segment_id)

    # ============================================================
    # Final summary — totals + per-market top 10
    # ============================================================
    print()
    print("=" * 70)
    print("RESULTS — drive-market wedding-planner feeders")
    print("=" * 70)
    print(f"{'Market':15s}  {'Unique':>8s}  {'In DB':>8s}")
    print(f"{'-' * 15}  {'-' * 8}  {'-' * 8}")
    grand_unique = 0
    grand_db     = 0
    for m, (uniq, db, _rows) in market_results.items():
        print(f"{m:15s}  {uniq:>8d}  {db:>8d}")
        grand_unique += uniq
        grand_db     += db
    print(f"{'-' * 15}  {'-' * 8}  {'-' * 8}")
    print(f"{'TOTAL':15s}  {grand_unique:>8d}  {grand_db:>8d}")

    for m, (_uniq, _db, rows) in market_results.items():
        if not rows:
            continue
        print()
        print(f"────────  TOP 10 — {m.upper().replace('_', ' ')}  ────────")
        sorted_rows = sorted(
            rows,
            key=lambda r: (r["metadata"].get("rating_count") or 0),
            reverse=True,
        )
        for r in sorted_rows[:10]:
            site   = (r["website"] or "—")[:50]
            rating = r["metadata"].get("rating") or "—"
            count  = r["metadata"].get("rating_count") or 0
            print(f"  ★ {rating}  ({count:>4} reviews)  {r['name'][:42]:42s}  {site}")

    print()
    print("👉 In Supabase SQL Editor, see the top 40 per market:")
    print("""
    SELECT name, metadata->>'market_metro' AS market,
           metadata->>'rating' AS rating,
           (metadata->>'rating_count')::int AS reviews,
           website
    FROM companies
    WHERE metadata->>'market_role' = 'feeder'
    ORDER BY metadata->>'market_metro',
             (metadata->>'rating_count')::int DESC NULLS LAST
    LIMIT 200;
    """)
    return 0


if __name__ == "__main__":
    sys.exit(main())
