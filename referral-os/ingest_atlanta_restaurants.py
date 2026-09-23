#!/usr/bin/env python3
"""
INGEST — Atlanta, GA restaurants (ALL of them) via Google Places API (New).

Goal
----
Load *every* public restaurant in Atlanta into the Referral OS database under
the `atlanta-restaurants-b2b` project, with the contact + pricing-signal fields
needed to drive B2B outreach: phone, website, $-tier (price_level), rating,
review count, primary type, and Google's editorial summary.

Why grid tiling
---------------
Google Places caps any single search at 60 results (20/page × 3 pages). "All
restaurants in Atlanta" is thousands, so one query can't return them. We tile
the city into a grid of circular searches (searchNearby), dedupe by place_id,
and keep going. Tile radius is sized so dense areas rarely hit the 60 cap; the
--audit flag reports any tile that maxed out so you can subdivide it.

This mirrors ingest_private_chefs.py (same .env loader, same Supabase REST
upsert, same company schema) — just swapped to searchNearby + a grid, and
pointed at the restaurants project's segments.

Run it (locally — the sandbox can't reach googleapis.com)
---------------------------------------------------------
    cd "/Users/samantha/Documents/Claude/Projects/Sun & Moon at 30a/referral-os"
    python3 ingest_atlanta_restaurants.py --sample          # ~9 tiles, smoke test
    python3 ingest_atlanta_restaurants.py                    # full Atlanta grid
    python3 ingest_atlanta_restaurants.py --step-miles 1.0   # finer grid (more $)

Cost: see COST_ATLANTA_RESTAURANTS.md. Each tile-page is one Nearby Search (Pro)
call. --sample is ~9-27 calls (free tier). Full city is a few hundred calls.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

PROJECT_SLUG = "atlanta-restaurants-b2b"
HTTP_TIMEOUT = 30

# ------------------------------------------------------------------
# Atlanta bounding box (city proper + close-in metro).
# SW corner -> NE corner. Widen for full metro; tighten for city only.
# ------------------------------------------------------------------
ATL_SW = (33.647, -84.551)   # ~ Hartsfield / SW
ATL_NE = (33.887, -84.289)   # ~ Brookhaven / NE
DEFAULT_STEP_MILES = 1.25    # grid spacing; tile radius derived from this
SAMPLE_BBOX = (33.770, -84.400, 33.800, -84.360)  # Midtown/Old Fourth Ward-ish

# Place types we treat as "restaurant universe" for Nearby Search.
INCLUDED_TYPES = [
    "restaurant", "cafe", "bakery", "bar", "meal_takeaway",
    "fast_food_restaurant", "brunch_restaurant", "breakfast_restaurant",
    "coffee_shop", "pub", "wine_bar", "fine_dining_restaurant",
    "sandwich_shop", "pizza_restaurant", "steak_house",
]

# Map a Google primary_type / type set -> our project segment slug.
SEGMENT_RULES = [
    ("fine_dining_restaurant",  "independent-fine-dining"),
    ("steak_house",             "independent-fine-dining"),
    ("bar",                     "bar-brewery-nightlife"),
    ("pub",                     "bar-brewery-nightlife"),
    ("wine_bar",                "bar-brewery-nightlife"),
    ("brewpub",                 "bar-brewery-nightlife"),
    ("cafe",                    "cafe-bakery-brunch"),
    ("coffee_shop",             "cafe-bakery-brunch"),
    ("bakery",                  "cafe-bakery-brunch"),
    ("brunch_restaurant",       "cafe-bakery-brunch"),
    ("breakfast_restaurant",    "cafe-bakery-brunch"),
    ("fast_food_restaurant",    "quick-service-counter"),
    ("meal_takeaway",           "quick-service-counter"),
    ("sandwich_shop",           "quick-service-counter"),
]


# ============================================================
# .env  (identical loader to the other ingest scripts)
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
# Grid math
# ============================================================
def build_grid(sw, ne, step_miles: float):
    """Return a list of (lat, lng, radius_m) tile centers covering the bbox."""
    lat0, lng0 = sw
    lat1, lng1 = ne
    dlat = step_miles / 69.0                                   # ~69 mi per deg lat
    mid_lat = (lat0 + lat1) / 2
    dlng = step_miles / (69.0 * max(0.1, math.cos(math.radians(mid_lat))))
    # radius covers the tile corner-to-center so circles overlap slightly
    radius_m = (step_miles * 1609.34) * 0.75
    tiles = []
    lat = lat0
    while lat <= lat1 + 1e-9:
        lng = lng0
        while lng <= lng1 + 1e-9:
            tiles.append((round(lat, 6), round(lng, 6), round(radius_m)))
            lng += dlng
        lat += dlat
    return tiles


# ============================================================
# Google Places (New) — searchNearby
# ============================================================
def nearby_search(api_key: str, lat: float, lng: float, radius_m: float) -> dict:
    body = json.dumps({
        "includedTypes": INCLUDED_TYPES,
        "maxResultCount": 20,
        "rankPreference": "POPULARITY",
        "locationRestriction": {
            "circle": {"center": {"latitude": lat, "longitude": lng},
                       "radius": float(radius_m)}
        },
    }).encode()
    req = urllib.request.Request(
        "https://places.googleapis.com/v1/places:searchNearby",
        data=body, method="POST",
        headers={
            "X-Goog-Api-Key": api_key,
            # Field mask drives which SKU you're billed at. These fields keep us
            # in the "Pro" tier (price_level + rating). Adding reviews would
            # bump every call to Enterprise+Atmosphere — we deliberately don't.
            "X-Goog-FieldMask": (
                "places.id,places.displayName,places.formattedAddress,"
                "places.websiteUri,places.nationalPhoneNumber,"
                "places.rating,places.userRatingCount,"
                "places.priceLevel,places.businessStatus,"
                "places.location,places.addressComponents,"
                "places.primaryType,places.types,places.editorialSummary"
            ),
            "Content-Type": "application/json",
            "User-Agent": "ReferralOS/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read())


def pick_segment(primary_type: str | None, types: list[str] | None) -> str:
    pool = [primary_type] + (types or [])
    for gtype, slug in SEGMENT_RULES:
        if gtype in pool:
            return slug
    return "independent-full-service"   # default: the core sit-down segment


def place_to_company(p: dict) -> dict:
    comps = p.get("addressComponents", [])
    def comp(kind, key="longText"):
        return next((c[key] for c in comps if kind in c.get("types", [])), None)
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
    edsum = (p.get("editorialSummary") or {}).get("text")
    return {
        "google_place_id": p["id"],
        "name":          p["displayName"]["text"],
        "domain":        domain,
        "city":          comp("locality"),
        "state":         comp("administrative_area_level_1", "shortText"),
        "postal_code":   comp("postal_code"),
        "country":       "US",
        "latitude":      loc.get("latitude"),
        "longitude":     loc.get("longitude"),
        "main_phone":    p.get("nationalPhoneNumber"),
        "website":       website,
        "address_line1": p.get("formattedAddress"),
        "data_sources":  ["google_places"],
        "metadata": {
            "price_level":       p.get("priceLevel"),       # PRICE_LEVEL_*
            "rating":            p.get("rating"),
            "rating_count":      p.get("userRatingCount"),
            "business_status":   p.get("businessStatus"),
            "primary_type":      p.get("primaryType"),
            "all_types":         p.get("types"),
            "editorial_summary": edsum,
            "market_metro":      "atlanta",
        },
        "_segment_slug": pick_segment(p.get("primaryType"), p.get("types")),
    }


# ============================================================
# Supabase REST  (identical helpers to the other ingest scripts)
# ============================================================
def sb_headers(k):
    return {"apikey": k, "Authorization": f"Bearer {k}",
            "Content-Type": "application/json", "User-Agent": "ReferralOS/1.0"}


def sb_get(base, k, path):
    req = urllib.request.Request(f"{base.rstrip('/')}/rest/v1/{path}",
                                 headers={**sb_headers(k), "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read())


def sb_upsert(base, k, table, rows, on_conflict):
    if not rows:
        return []
    url = f"{base.rstrip('/')}/rest/v1/{table}?on_conflict={on_conflict}"
    req = urllib.request.Request(url, data=json.dumps(rows).encode(), method="POST",
        headers={**sb_headers(k),
                 "Prefer": "return=representation,resolution=merge-duplicates"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read())


# ============================================================
# Main
# ============================================================
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true",
                    help="Small bbox (Midtown-ish), ~9 tiles — smoke test.")
    ap.add_argument("--step-miles", type=float, default=DEFAULT_STEP_MILES,
                    help="Grid spacing in miles. Smaller = more tiles = more $.")
    ap.add_argument("--max-tiles", type=int, default=0,
                    help="Hard cap on tiles (cost guardrail). 0 = no cap.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the grid + estimated calls, hit no APIs.")
    args = ap.parse_args()

    env = load_env()
    gkey  = env.get("GOOGLE_PLACES_API_KEY", "")
    base  = env.get("SUPABASE_URL", "")
    skey  = env.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not gkey:
        print("❌ GOOGLE_PLACES_API_KEY empty in .env"); return 1

    if args.sample:
        sw = (SAMPLE_BBOX[0], SAMPLE_BBOX[1]); ne = (SAMPLE_BBOX[2], SAMPLE_BBOX[3])
    else:
        sw, ne = ATL_SW, ATL_NE
    tiles = build_grid(sw, ne, args.step_miles)
    if args.max_tiles:
        tiles = tiles[:args.max_tiles]

    print(f"Grid: {len(tiles)} tiles  (step={args.step_miles} mi, "
          f"bbox {sw}→{ne})")
    print(f"Worst-case calls (3 pages/tile): {len(tiles)*3}")
    if args.dry_run:
        for t in tiles[:12]:
            print(f"  tile {t}")
        if len(tiles) > 12:
            print(f"  ... +{len(tiles)-12} more")
        return 0

    if not base or not skey:
        print("❌ SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY empty in .env"); return 1

    # Map segment slug -> id for this project
    segs = sb_get(base, skey,
        f"segments?select=id,slug,project_id,projects!inner(slug)"
        f"&projects.slug=eq.{PROJECT_SLUG}")
    seg_id = {s["slug"]: s["id"] for s in segs}
    if not seg_id:
        print(f"❌ No segments for project '{PROJECT_SLUG}'. Run 006 seed."); return 1
    print(f"Segments loaded: {', '.join(seg_id)}")

    by_pid: dict[str, dict] = {}
    maxed_tiles = 0
    for i, (lat, lng, radius) in enumerate(tiles, 1):
        page, token = 0, None
        while True:
            try:
                # NOTE: searchNearby (New) does not page via token in all setups;
                # POPULARITY rank returns the top 20 per tile. We rely on the grid
                # for coverage. (Kept single-page; subdivide hot tiles via --step-miles.)
                data = nearby_search(gkey, lat, lng, radius)
            except urllib.error.HTTPError as e:
                print(f"  tile {i:>3}/{len(tiles)}: HTTP {e.code} {e.read().decode()[:120]}")
                break
            except Exception as e:  # noqa: BLE001
                print(f"  tile {i:>3}/{len(tiles)}: ERROR {e}")
                break
            places = data.get("places", [])
            new_here = 0
            for p in places:
                if p["id"] not in by_pid:
                    by_pid[p["id"]] = p
                    new_here += 1
            if len(places) >= 20:
                maxed_tiles += 1
            print(f"  tile {i:>3}/{len(tiles)} ({lat:.3f},{lng:.3f}): "
                  f"{len(places):2d} returned, {new_here:2d} new  "
                  f"[running total {len(by_pid)}]")
            break
        time.sleep(0.15)

    print(f"\nUnique restaurants: {len(by_pid)}")
    if maxed_tiles:
        print(f"⚠️  {maxed_tiles} tiles returned the full 20 — likely undercounting "
              f"those dense areas. Re-run with a smaller --step-miles to subdivide.")
    if not by_pid:
        print("Nothing to upsert."); return 0

    rows = [place_to_company(p) for p in by_pid.values()]
    company_rows = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]

    print("Upserting companies ...")
    try:
        upserted = sb_upsert(base, skey, "companies", company_rows,
                             on_conflict="google_place_id")
    except urllib.error.HTTPError as e:
        print(f"❌ HTTP {e.code}: {e.read().decode()[:600]}"); return 1
    pid_to_id = {c["google_place_id"]: c["id"] for c in upserted}
    print(f"  → {len(upserted)} companies in DB")

    links = []
    for r in rows:
        cid = pid_to_id.get(r["google_place_id"])
        if not cid:
            continue
        slug = r["_segment_slug"]
        links.append({"company_id": cid, "segment_id": seg_id[slug],
                      "relevance_score": 50, "status": "prospect"})
        # Cross-link the no-website opportunity segment too
        if r["website"] is None and "no-website-opportunity" in seg_id:
            links.append({"company_id": cid,
                          "segment_id": seg_id["no-website-opportunity"],
                          "relevance_score": 65, "status": "prospect"})
    try:
        sb_upsert(base, skey, "company_segments", links,
                  on_conflict="company_id,segment_id")
    except urllib.error.HTTPError as e:
        print(f"❌ segment link HTTP {e.code}: {e.read().decode()[:400]}"); return 1
    print(f"  → {len(links)} segment links")

    # ---- summary ----
    print("\n" + "=" * 66)
    print("RESULTS — Atlanta restaurants")
    print("=" * 66)
    seg_counts = Counter(r["_segment_slug"] for r in rows)
    for slug, n in seg_counts.most_common():
        print(f"  {slug:28s} {n}")
    print(f"\n  with website : {sum(1 for r in rows if r['website'])}/{len(rows)}")
    print(f"  with phone   : {sum(1 for r in rows if r['main_phone'])}/{len(rows)}")
    print(f"  with $-tier  : {sum(1 for r in rows if r['metadata'].get('price_level'))}/{len(rows)}")
    print("\nNext: python3 scrape_restaurant_menus.py --limit 25")
    print("Then open v_atlanta_restaurant_targets in Supabase, or the dashboard.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
