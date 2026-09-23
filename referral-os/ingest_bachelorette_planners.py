#!/usr/bin/env python3
"""
INGEST — bachelorette party planners.

Two-mode pull
-------------
Unlike family photographers, bachelorette planners come in BOTH local
and drive-market flavors, so this script searches both:

  • LOCAL (30A):       on-the-ground concierges who curate the weekend
                       once the group arrives — beach setups, pole-dancing
                       parties, brunch reservations, photographer pairing.
                       Tag: market_role="local", market_metro="30a".

  • FEEDER (drive markets):
                       trip-planners who organize the whole weekend FROM
                       the bride's hometown — Atlanta, Birmingham,
                       Nashville, New Orleans. They pick the destination,
                       book the house, coordinate vendors.
                       Tag: market_role="feeder", market_metro=<city>.

Both feed the same `bachelorette-planner` segment (Tier 1, target 150).

Run it
------
    cd "/Users/samantha/Documents/Claude/Projects/Sun & Moon at 30a/referral-os"
    python3 ingest_bachelorette_planners.py

    # Only local 30A
    python3 ingest_bachelorette_planners.py --only local

    # Only drive markets
    python3 ingest_bachelorette_planners.py --only feeder
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SEGMENT_SLUG = "bachelorette-planner"

# ============================================================
# Local 30A — concierges + on-the-ground curators
# ============================================================
LOCAL_METROS = [
    "Santa Rosa Beach FL",
    "Seagrove Beach FL",
    "Seaside FL",
    "Rosemary Beach FL",
    "Miramar Beach FL",
    "Destin FL",
    "Inlet Beach FL",
]

LOCAL_QUERIES = [
    "bachelorette party planner in {metro}",
    "bachelorette concierge in {metro}",
    "girls weekend planner in {metro}",
    "party planner in {metro}",
]

# ============================================================
# Drive markets — trip planners organizing FROM the bride's hometown
# ============================================================
DRIVE_MARKETS: dict[str, list[str]] = {
    "atlanta":     ["Atlanta GA", "Buckhead Atlanta GA", "Marietta GA"],
    "birmingham":  ["Birmingham AL", "Mountain Brook AL"],
    "nashville":   ["Nashville TN", "Franklin TN"],
    "new_orleans": ["New Orleans LA", "Uptown New Orleans LA", "Metairie LA"],
    "dallas":      ["Dallas TX", "Highland Park Dallas TX"],   # adding Dallas — huge bach feeder for 30A
    "houston":     ["Houston TX", "River Oaks Houston TX"],
}

FEEDER_QUERIES = [
    "bachelorette party planner in {metro}",
    "bachelorette trip planner in {metro}",
    "girls trip planner in {metro}",
]

HTTP_TIMEOUT = 20


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


def place_to_company(p: dict, market_role: str, market_metro: str) -> dict:
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
            "market_role":   market_role,
            "market_metro":  market_metro,
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


# ============================================================
# CLI
# ============================================================
def parse_only(argv: list[str]) -> str | None:
    for i, a in enumerate(argv):
        if a == "--only" and i + 1 < len(argv):
            val = argv[i + 1].lower()
            if val not in {"local", "feeder"}:
                print("❌ --only must be 'local' or 'feeder'")
                sys.exit(1)
            return val
    return None


# ============================================================
# Search runners
# ============================================================
def run_local(google_key: str) -> list[dict]:
    """Returns list of place dicts with market metadata stamped in."""
    print(f"\n{'─' * 70}")
    print(f"  LOCAL 30A  —  {len(LOCAL_METROS)} metros × {len(LOCAL_QUERIES)} angles = {len(LOCAL_METROS) * len(LOCAL_QUERIES)} calls")
    print(f"{'─' * 70}")
    seen: dict[str, dict] = {}
    for metro in LOCAL_METROS:
        for template in LOCAL_QUERIES:
            q = template.format(metro=metro)
            angle = template.split(" in ")[0]
            try:
                data = google_places_search(google_key, q)
            except urllib.error.HTTPError as e:
                print(f"  {metro:22s} [{angle:30s}]: HTTP {e.code}")
                continue
            except Exception as e:  # noqa: BLE001
                print(f"  {metro:22s} [{angle:30s}]: ERROR {e}")
                continue
            places = data.get("places", [])
            new_here = 0
            for p in places:
                if p["id"] not in seen:
                    p["_source_query"]    = q
                    p["_market_role"]     = "local"
                    p["_market_metro"]    = "30a"
                    seen[p["id"]] = p
                    new_here += 1
            print(f"  {metro:22s} [{angle:30s}]: {len(places):2d} returned, {new_here:2d} new")
            time.sleep(0.2)
    print(f"\n  Local unique: {len(seen)}")
    return list(seen.values())


def run_feeders(google_key: str) -> list[dict]:
    print(f"\n{'─' * 70}")
    total = sum(len(qs) * len(FEEDER_QUERIES) for qs in DRIVE_MARKETS.values())
    print(f"  DRIVE MARKETS  —  {len(DRIVE_MARKETS)} markets, {total} calls")
    print(f"{'─' * 70}")
    seen: dict[str, dict] = {}
    for market, queries in DRIVE_MARKETS.items():
        per_market = 0
        for metro in queries:
            for template in FEEDER_QUERIES:
                q = template.format(metro=metro)
                angle = template.split(" in ")[0]
                try:
                    data = google_places_search(google_key, q)
                except urllib.error.HTTPError as e:
                    print(f"  {market:12s} {metro:30s} [{angle:25s}]: HTTP {e.code}")
                    continue
                except Exception as e:  # noqa: BLE001
                    print(f"  {market:12s} {metro:30s} [{angle:25s}]: ERROR {e}")
                    continue
                places = data.get("places", [])
                new_here = 0
                for p in places:
                    if p["id"] not in seen:
                        p["_source_query"] = q
                        p["_market_role"]  = "feeder"
                        p["_market_metro"] = market
                        seen[p["id"]] = p
                        new_here += 1
                        per_market += 1
                print(f"  {market:12s} {metro:30s} [{angle:25s}]: {len(places):2d}/{new_here:2d}")
                time.sleep(0.2)
        print(f"  → {market:12s} subtotal: {per_market} new\n")
    print(f"  Feeder unique: {len(seen)}")
    return list(seen.values())


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

    only_mode = parse_only(sys.argv)

    # Resolve segment id
    print(f"Looking up segment '{SEGMENT_SLUG}' ...")
    segs = sb_get(base_url, service_key, f"segments?slug=eq.{SEGMENT_SLUG}&select=id,name")
    if not segs:
        print(f"❌ Segment '{SEGMENT_SLUG}' not found. Did you create it?")
        return 1
    segment_id = segs[0]["id"]
    print(f"  → {segs[0]['name']}  ({segment_id})")

    # Run requested phase(s)
    all_places: list[dict] = []
    if only_mode != "feeder":
        all_places += run_local(google_key)
    if only_mode != "local":
        all_places += run_feeders(google_key)

    if not all_places:
        print("\nNothing found.")
        return 0

    # Dedup across local + feeder (a Destin planner could surface in both buckets)
    by_pid: dict[str, dict] = {}
    for p in all_places:
        if p["id"] not in by_pid:
            by_pid[p["id"]] = p
    print(f"\nGrand total unique (after cross-bucket dedup): {len(by_pid)}")

    rows = [
        place_to_company(p, p["_market_role"], p["_market_metro"])
        for p in by_pid.values()
    ]

    print("\nUpserting into companies ...")
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
    # Local gets higher relevance — they're closer to the booking
    links = []
    pid_to_role = {p["id"]: p["_market_role"] for p in by_pid.values()}
    pid_to_real = {c["google_place_id"]: c["id"] for c in upserted}
    for pid, real_id in pid_to_real.items():
        relevance = 80 if pid_to_role.get(pid) == "local" else 70
        links.append({
            "company_id":      real_id,
            "segment_id":      segment_id,
            "relevance_score": relevance,
            "status":          "prospect",
        })
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
    print("RESULTS — bachelorette party planners")
    print("=" * 70)
    from collections import Counter
    role_counts = Counter(r["metadata"]["market_role"] for r in rows)
    print(f"Segment:           {SEGMENT_SLUG}")
    print(f"Total unique:      {len(by_pid)}")
    print(f"  Local 30A:       {role_counts.get('local', 0)}")
    print(f"  Drive-market:    {role_counts.get('feeder', 0)}")

    print(f"\nDrive-market breakdown:")
    metro_counts = Counter(r["metadata"]["market_metro"] for r in rows if r["metadata"]["market_role"] == "feeder")
    for metro, count in metro_counts.most_common():
        print(f"  {metro:15s}: {count}")

    print(f"\nTop 10 by Google review count:")
    sorted_rows = sorted(
        rows,
        key=lambda r: (r["metadata"].get("rating_count") or 0),
        reverse=True,
    )
    for r in sorted_rows[:10]:
        site   = (r["website"] or "—")[:45]
        rating = r["metadata"].get("rating") or "—"
        count  = r["metadata"].get("rating_count") or 0
        role   = r["metadata"]["market_role"]
        metro  = r["metadata"]["market_metro"]
        print(f"  ★ {rating}  ({count:>4})  [{role:6s} {metro:12s}]  {r['name'][:32]:32s}  {site}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
