#!/usr/bin/env python3
"""
INGEST — 30A / Emerald Coast restaurants via Google Places (New).

Same engine as ingest_atlanta_restaurants.py (we import its helpers so there's
ONE copy of the Places + Supabase logic), but 30A is a small, clustered coastal
strip — so instead of a city grid we search a handful of beach-town centers and
dedupe by place_id. A few dozen calls cover the whole corridor; well inside
Google's free Pro tier.

Loads into the `30a-restaurants` project (warm/local positioning).

Run it (locally — sandbox can't reach googleapis.com)
-----------------------------------------------------
    python3 ingest_30a_restaurants.py --dry-run     # show the search points
    python3 ingest_30a_restaurants.py               # full 30A pull
"""
from __future__ import annotations

import argparse
import sys
import time
import urllib.error
from collections import Counter

# Reuse the Atlanta module's helpers — single source of truth for Places + DB.
import ingest_atlanta_restaurants as base

PROJECT_SLUG = "30a-restaurants"

# 30A beach-town centers (lat, lng, radius_m). Towns are tiny and close, so a
# ~2.2km radius each with overlap + place_id dedupe covers the corridor.
TOWN_CENTERS = [
    ("Dune Allen Beach",    30.366, -86.297, 2200),
    ("Santa Rosa Beach",    30.379, -86.247, 2600),
    ("Blue Mountain Beach", 30.357, -86.262, 2000),
    ("Grayton Beach",       30.331, -86.160, 2000),
    ("WaterColor",          30.327, -86.155, 1600),
    ("Seaside",             30.321, -86.143, 1600),
    ("Seagrove Beach",      30.320, -86.117, 2000),
    ("WaterSound",          30.300, -86.075, 2000),
    ("Alys Beach",          30.296, -86.064, 1600),
    ("Rosemary Beach",      30.284, -86.011, 1800),
    ("Inlet Beach",         30.276, -86.001, 2200),
    ("Miramar Beach",       30.379, -86.354, 2800),   # adjacent west
    ("Destin (Hwy 98)",     30.393, -86.470, 3200),   # adjacent west
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the town search points, hit no APIs.")
    args = ap.parse_args()

    env = base.load_env()
    gkey = env.get("GOOGLE_PLACES_API_KEY", "")
    sbase = env.get("SUPABASE_URL", "")
    skey = env.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not gkey:
        print("❌ GOOGLE_PLACES_API_KEY empty in .env"); return 1

    print(f"30A corridor: {len(TOWN_CENTERS)} town searches "
          f"(≈{len(TOWN_CENTERS)} Places Pro calls — free tier)")
    if args.dry_run:
        for name, lat, lng, rad in TOWN_CENTERS:
            print(f"  {name:20s} ({lat},{lng})  r={rad}m")
        return 0
    if not sbase or not skey:
        print("❌ SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY empty in .env"); return 1

    segs = base.sb_get(sbase, skey,
        "segments?select=id,slug,project_id,projects!inner(slug)"
        f"&projects.slug=eq.{PROJECT_SLUG}")
    seg_id = {s["slug"]: s["id"] for s in segs}
    if not seg_id:
        print(f"❌ No segments for project '{PROJECT_SLUG}'. Run 008 seed."); return 1
    print(f"Segments: {', '.join(seg_id)}\n")

    by_pid: dict[str, dict] = {}
    for name, lat, lng, rad in TOWN_CENTERS:
        try:
            data = base.nearby_search(gkey, lat, lng, rad)
        except urllib.error.HTTPError as e:
            print(f"  {name:20s}: HTTP {e.code} {e.read().decode()[:100]}"); continue
        except Exception as e:  # noqa: BLE001
            print(f"  {name:20s}: ERROR {e}"); continue
        places = data.get("places", [])
        new_here = 0
        for p in places:
            if p["id"] not in by_pid:
                by_pid[p["id"]] = p; new_here += 1
        print(f"  {name:20s}: {len(places):2d} returned, {new_here:2d} new "
              f"[total {len(by_pid)}]")
        time.sleep(0.15)

    print(f"\nUnique 30A restaurants: {len(by_pid)}")
    if not by_pid:
        print("Nothing to upsert."); return 0

    rows = [base.place_to_company(p) for p in by_pid.values()]
    company_rows = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
    try:
        upserted = base.sb_upsert(sbase, skey, "companies", company_rows,
                                  on_conflict="google_place_id")
    except urllib.error.HTTPError as e:
        print(f"❌ HTTP {e.code}: {e.read().decode()[:500]}"); return 1
    pid_to_id = {c["google_place_id"]: c["id"] for c in upserted}
    print(f"  → {len(upserted)} companies in DB")

    links = []
    for r in rows:
        cid = pid_to_id.get(r["google_place_id"])
        if not cid:
            continue
        links.append({"company_id": cid, "segment_id": seg_id[r["_segment_slug"]],
                      "relevance_score": 55, "status": "prospect"})
        if r["website"] is None and "no-website-opportunity" in seg_id:
            links.append({"company_id": cid, "segment_id": seg_id["no-website-opportunity"],
                          "relevance_score": 70, "status": "prospect"})
    base.sb_upsert(sbase, skey, "company_segments", links,
                   on_conflict="company_id,segment_id")
    print(f"  → {len(links)} segment links")

    seg_counts = Counter(r["_segment_slug"] for r in rows)
    print("\n" + "=" * 60 + "\nRESULTS — 30A restaurants\n" + "=" * 60)
    for slug, n in seg_counts.most_common():
        print(f"  {slug:28s} {n}")
    print(f"\n  with website : {sum(1 for r in rows if r['website'])}/{len(rows)}")
    print(f"  with $-tier  : {sum(1 for r in rows if r['metadata'].get('price_level'))}/{len(rows)}")
    print("\nNext: python3 scrape_restaurant_menus.py  (point PROJECT_SLUG at 30a-restaurants)")
    print("Then: python3 outreach_restaurants.py --region 30a --from-db")
    return 0


if __name__ == "__main__":
    sys.exit(main())
