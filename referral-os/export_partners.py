#!/usr/bin/env python3
"""
Dump every partner from Postgres → CSV (opens in Excel natively).

Pure stdlib. No pip installs. Two files written:
  • partners.csv         — every row, all useful fields
  • partners_summary.csv — segment counts vs targets

Run it
------
    cd "/Users/samantha/Documents/Claude/Projects/Sun & Moon at 30a/referral-os"
    python3 export_partners.py

Then in Finder → double-click partners.csv (opens in Numbers/Excel).
"""
from __future__ import annotations

import csv
import json
import sys
import urllib.request

HTTP_TIMEOUT = 30
PAGE_SIZE    = 1000


def load_env(path: str = ".env") -> dict[str, str]:
    env = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


def sb_get(base: str, key: str, path: str) -> list:
    req = urllib.request.Request(
        f"{base.rstrip('/')}/rest/v1/{path}",
        headers={
            "apikey":        key,
            "Authorization": f"Bearer {key}",
            "Accept":        "application/json",
            "User-Agent":    "ReferralOS/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read())


def main() -> int:
    env = load_env()
    base = env["SUPABASE_URL"]
    key  = env["SUPABASE_SERVICE_ROLE_KEY"]

    # ---- 1. segments (for summary) ----
    print("Fetching segments ...")
    segs = sb_get(base, key, "segments?select=id,slug,name,priority_tier,target_count&order=priority_tier,slug")
    seg_by_id = {s["id"]: s for s in segs}

    # ---- 2. paged company_segments + nested company + segment ----
    print("Fetching every partner row (paged) ...")
    rows: list[dict] = []
    offset = 0
    while True:
        chunk = sb_get(
            base, key,
            "company_segments?select="
            "relevance_score,status,added_at,segment_id,"
            "company:companies(id,name,city,state,website,main_phone,address_line1,metadata,data_sources)"
            f"&limit={PAGE_SIZE}&offset={offset}&order=segment_id"
        )
        if not chunk:
            break
        rows.extend(chunk)
        print(f"  pulled {len(rows)} so far")
        if len(chunk) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    # ---- 3. write the main partners.csv ----
    cols = [
        "tier", "segment", "segment_name", "market_role", "market_metro",
        "name", "city", "state",
        "rating", "reviews",
        "relevance_score", "status",
        "website", "phone", "address",
        "editorial_summary", "google_primary_type", "query_angle",
        "company_id",
    ]

    # sort: tier, segment, then reviews desc
    def revcount(r):
        md = (r["company"].get("metadata") or {})
        rc = md.get("rating_count")
        try:
            return -int(rc)
        except (ValueError, TypeError):
            return 0

    def tier(r):
        return seg_by_id[r["segment_id"]]["priority_tier"]

    def slug(r):
        return seg_by_id[r["segment_id"]]["slug"]

    rows.sort(key=lambda r: (tier(r), slug(r), revcount(r)))

    with open("partners.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            seg = seg_by_id[r["segment_id"]]
            co  = r["company"]
            md  = co.get("metadata") or {}
            w.writerow([
                seg["priority_tier"],
                seg["slug"],
                seg["name"],
                md.get("market_role"),
                md.get("market_metro"),
                co.get("name"),
                co.get("city"),
                co.get("state"),
                md.get("rating"),
                md.get("rating_count"),
                r.get("relevance_score"),
                r.get("status"),
                co.get("website"),
                co.get("main_phone"),
                co.get("address_line1"),
                md.get("editorial_summary"),
                md.get("primary_type"),
                md.get("query_angle"),
                co.get("id"),
            ])

    # ---- 4. write summary CSV ----
    counts: dict[str, int] = {}
    for r in rows:
        sid = r["segment_id"]
        counts[sid] = counts.get(sid, 0) + 1

    with open("partners_summary.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["tier", "segment", "segment_name", "target", "current", "pct_of_target"])
        for s in segs:
            cur = counts.get(s["id"], 0)
            tgt = s["target_count"] or 0
            pct = round(100.0 * cur / tgt, 1) if tgt else 0
            w.writerow([s["priority_tier"], s["slug"], s["name"], tgt, cur, pct])

    print()
    print("=" * 60)
    print(f"Wrote {len(rows)} partners → partners.csv")
    print(f"Wrote {len(segs)} segments → partners_summary.csv")
    print("=" * 60)
    print()
    print("Open them with:")
    print(f"  open partners.csv")
    print(f"  open partners_summary.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
