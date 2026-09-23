#!/usr/bin/env python3
"""
INGEST — 30A private chefs / in-home catering.

Why this is the "message-specificity" channel
---------------------------------------------
Private chefs are the rare partner where a generic email gets deleted but
a 3-sentence pitch that names their specialty + your two-kitchen setup
lands every time:

  > Hey {chef} — saw your low-country menu on Instagram.
  > We have two adjacent houses on 30A with two full kitchens, two ovens,
  > and a shared yard. Family groups of 12-16 are a perfect fit for the
  > kind of yard-staged courses you do. Want to be on the chef sheet we
  > send every booking? — Cristian, Sun & Moon

The 30A-rental kitchen problem is real — most STRs have one cramped galley
kitchen. We have a TRUE chef setup. Chefs feel that instantly.

Extra metadata captured (beyond the standard ingest)
----------------------------------------------------
For each chef we pull TWO extra Google Places fields that nothing else
in the platform pulls:

  • editorialSummary   — Google's curated 1-2 sentence description.
                         Often names the cuisine or specialty directly.
                         This is the gold for outreach personalization.
  • primaryType        — Google's primary category (e.g. "personal_chef",
                         "caterer"). Lets us segment "true private chefs"
                         from "small catering shops" in SQL later.

These get stored in metadata.editorial_summary and metadata.primary_type
so the outreach script can plug them into the email template per-chef.

Three query angles per metro
----------------------------
  • "private chef in {metro}"
  • "personal chef in {metro}"
  • "in-home chef in {metro}"

Each surfaces a slightly different cluster.

Run it
------
    cd "/Users/samantha/Documents/Claude/Projects/Sun & Moon at 30a/referral-os"
    python3 ingest_private_chefs.py
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SEGMENT_SLUG = "private-chef"

METROS = [
    "Santa Rosa Beach FL",
    "Seagrove Beach FL",
    "Seaside FL",
    "Watercolor FL",
    "Rosemary Beach FL",
    "Alys Beach FL",
    "Grayton Beach FL",
    "Miramar Beach FL",
    "Destin FL",
    "Inlet Beach FL",
    "Watersound FL",
    "Blue Mountain Beach FL",
]

QUERY_TEMPLATES = [
    "private chef in {metro}",
    "personal chef in {metro}",
    "in-home chef in {metro}",
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
# Google Places — note expanded FieldMask for chef-specific signal
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
                "places.location,places.addressComponents,"
                # ↓↓↓ extra fields for message-specificity ↓↓↓
                "places.editorialSummary,"
                "places.primaryType,"
                "places.types"
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

    # editorialSummary is { text: "...", languageCode: "en" } when present
    edsum = p.get("editorialSummary") or {}
    editorial_text = edsum.get("text")

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
            "rating":            p.get("rating"),
            "rating_count":      p.get("userRatingCount"),
            "source_query":      p.get("_source_query"),
            "query_angle":       query_angle,
            "market_role":       "local",
            "market_metro":      "30a",
            # ↓↓↓ outreach personalization gold ↓↓↓
            "editorial_summary": editorial_text,
            "primary_type":      p.get("primaryType"),
            "all_types":         p.get("types"),
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

    print(f"Looking up segment '{SEGMENT_SLUG}' ...")
    segs = sb_get(base_url, service_key, f"segments?slug=eq.{SEGMENT_SLUG}&select=id,name")
    if not segs:
        print(f"❌ Segment '{SEGMENT_SLUG}' not found.")
        return 1
    segment_id = segs[0]["id"]
    print(f"  → {segs[0]['name']}  ({segment_id})")

    total_queries = len(METROS) * len(QUERY_TEMPLATES)
    print(f"\nSearching Google Places: {len(METROS)} metros × {len(QUERY_TEMPLATES)} angles = {total_queries} calls ...")

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
                print(f"  {metro:25s} [{angle:14s}]: HTTP {e.code}")
                continue
            except Exception as e:  # noqa: BLE001
                print(f"  {metro:25s} [{angle:14s}]: ERROR {e}")
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
            print(f"  {metro:25s} [{angle:14s}]: {len(places):2d} returned, {new_here:2d} new")
            time.sleep(0.2)
        print(f"  → {metro:25s} subtotal: {per_metro_added} new\n")

    print(f"Unique chefs found: {len(by_pid)}")
    if not by_pid:
        print("Nothing to upsert.")
        return 0

    rows = [place_to_company(p, angle_used[p["id"]]) for p in by_pid.values()]

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
    links = [
        {
            "company_id":      c["id"],
            "segment_id":      segment_id,
            "relevance_score": 78,  # high — message-fit makes these warmer than typical Tier 3
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

    # ============================================================
    # Summary — focused on outreach readiness
    # ============================================================
    print()
    print("=" * 70)
    print("RESULTS — 30A private chefs")
    print("=" * 70)
    print(f"Segment:           {SEGMENT_SLUG}")
    print(f"Unique found:      {len(by_pid)}")
    print(f"Companies in DB:   {len(upserted)}")
    print(f"Segment links:     {len(links)}")

    # How many have rich enough data for personalized outreach?
    has_summary = sum(1 for r in rows if r["metadata"].get("editorial_summary"))
    has_website = sum(1 for r in rows if r["website"])
    has_phone   = sum(1 for r in rows if r["main_phone"])
    print(f"\nOutreach-readiness:")
    print(f"  With Google editorial summary: {has_summary} / {len(rows)}  (best signal for personalization)")
    print(f"  With website:                  {has_website} / {len(rows)}  (manual personalization fallback)")
    print(f"  With phone:                    {has_phone} / {len(rows)}")

    print(f"\nBy query angle:")
    from collections import Counter
    angle_counts = Counter(angle_used.values())
    for angle, count in angle_counts.most_common():
        print(f"  {angle:14s}: {count}")

    print(f"\nTop 10 by review count (eyeball for real chefs vs catering shops):")
    sorted_rows = sorted(
        rows,
        key=lambda r: (r["metadata"].get("rating_count") or 0),
        reverse=True,
    )
    for r in sorted_rows[:10]:
        site    = (r["website"] or "—")[:45]
        rating  = r["metadata"].get("rating") or "—"
        count   = r["metadata"].get("rating_count") or 0
        ptype   = r["metadata"].get("primary_type") or "—"
        edsum   = (r["metadata"].get("editorial_summary") or "")[:60]
        print(f"  ★ {rating}  ({count:>3})  [{ptype:14s}]  {r['name'][:30]:30s}")
        if edsum:
            print(f"           summary: \"{edsum}{'...' if len(r['metadata'].get('editorial_summary') or '') > 60 else ''}\"")
        print(f"           {site}")

    print()
    print("👉 In Supabase, sample the rich metadata for outreach with:")
    print("""
    SELECT name, city,
           metadata->>'primary_type'      AS gtype,
           metadata->>'editorial_summary' AS summary,
           website,
           (metadata->>'rating_count')::int AS reviews
    FROM companies c
    JOIN company_segments cs ON cs.company_id = c.id
    JOIN segments        s  ON s.id = cs.segment_id
    WHERE s.slug = 'private-chef'
    ORDER BY (metadata->>'rating_count')::int DESC NULLS LAST
    LIMIT 30;
    """)
    return 0


if __name__ == "__main__":
    sys.exit(main())
