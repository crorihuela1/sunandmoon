#!/usr/bin/env python3
"""
REGEN WAVE — upgrade queued-but-never-sent outreach rows to the enhanced
experience, then schedule them at a steady daily pace.

For each targeted queued row:
  1. Provisions a LIVE partner record (rev_share_partners) with a slug from
     the company name → their private link book.sunandmoon30a.com/?partner=<slug>
     (auto-applies the guest discount + greets their clients by name).
  2. Regenerates subject/body with the upgraded prompt (live booking site,
     partner link CTA, revenue share, Sun & Moon Together preferred rate).
  3. Recreates tracked links + open pixel, re-renders HTML with the new
     professional photo, and reschedules to the next open send slot
     (PER_DAY per weekday, starting START_DATE, 09:00 CT, 4-min spacing).

Usage:
    python3 ops/outreach_regen_wave.py --dry-run          # plan only
    python3 ops/outreach_regen_wave.py --run --limit 2    # sample
    python3 ops/outreach_regen_wave.py --run              # full wave
"""
from __future__ import annotations

import argparse, re, sys, time, unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import os as _os
from outreach_email import (  # noqa: E402
    PROJECT_ID, TRACKING_DOMAIN, ANTHROPIC_MODEL,
    PROPERTY_PHOTO_PATH, PROPERTY_PHOTO_CID,
    load_env, load_voice_samples,
    sb_get, sb_post, sb_patch,
    generate_email, create_tracking_link,
    collect_tracked_links, html_from_text,
)

BOOKING_BASE   = "https://book.sunandmoon30a.com"
CT             = ZoneInfo("America/Chicago")
START_DATE     = "2026-08-04"     # first send day (weekdays only)
PER_DAY        = 25
SPACING_MIN    = 4                # minutes between sends within a day
GUEST_DISCOUNT = 5
REV_SHARE      = 10

# Wave targets: campaign slug → how many of its queued rows to take (oldest first)
TARGETS = [("bachelorette-planner-email-202607", 192),
           ("wedding-planner-feeder-email-202607", 58)]


def slugify(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:48] or "partner"


def send_slots():
    """Yield ISO-UTC datetimes: PER_DAY slots per weekday from START_DATE."""
    day = datetime.fromisoformat(START_DATE).date()
    while True:
        if day.weekday() < 5:                       # Mon-Fri
            base = datetime.combine(day, datetime.min.time(), tzinfo=CT).replace(hour=9)
            for i in range(PER_DAY):
                yield (base + timedelta(minutes=SPACING_MIN * i)).astimezone(timezone.utc).isoformat()
        day += timedelta(days=1)


def context_for(base, key, contact_id, company_id):
    """Rebuild the v_outreach_ready-shaped context dict for one contact."""
    k = sb_get(base, key, f"contacts?id=eq.{contact_id}&select=email,first_name,company_id&limit=1")[0]
    c = sb_get(base, key, f"companies?id=eq.{company_id}&select=name,city,state,website,metadata&limit=1")[0]
    seg = sb_get(base, key,
        f"company_segments?company_id=eq.{company_id}&select=segments(slug,name)&limit=1")
    md = c.get("metadata") or {}
    s = (seg[0].get("segments") if seg else {}) or {}
    return {
        "contact_id": contact_id, "company_id": company_id,
        "email": k["email"], "first_name": k.get("first_name"),
        "company_name": c["name"], "city": c.get("city"), "state": c.get("state"),
        "website": c.get("website"), "editorial_summary": md.get("editorial_summary"),
        "market_role": md.get("market_role"), "market_metro": md.get("market_metro"),
        "rating": md.get("rating"), "reviews": md.get("rating_count"),
        "segment_slug": s.get("slug"), "segment_name": s.get("name"),
    }


def ensure_partner(base, key, company_name, email, taken: set) -> str:
    """Upsert a rev_share_partners row; returns the slug."""
    slug = slugify(company_name)
    n = 2
    while slug in taken:
        slug = f"{slugify(company_name)[:44]}-{n}"; n += 1
    existing = sb_get(base, key, f"rev_share_partners?slug=eq.{slug}&select=id&limit=1")
    if not existing:
        sb_post(base, key, "rev_share_partners", [{
            "slug": slug, "name": company_name, "email": email,
            "guest_discount_pct": GUEST_DISCOUNT, "rev_share_pct": REV_SHARE,
            "landing_copy": "auto-provisioned for outreach wave 2026-08",
        }])
    taken.add(slug)
    return slug


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--limit", type=int, default=None, help="cap total rows (sampling)")
    ap.add_argument("--topup", type=int, default=None,
                    help="regen N ADDITIONAL unique-company rows from the parked pool")
    ap.add_argument("--reslot", action="store_true",
                    help="compact all ready wave rows onto the 25/weekday schedule")
    args = ap.parse_args()
    if not (args.dry_run or args.run or args.topup or args.reslot):
        ap.error("pass --dry-run, --run, --topup N, or --reslot")

    env = load_env()
    base, key = env["SUPABASE_URL"], env["SUPABASE_SERVICE_ROLE_KEY"]
    anth = env["ANTHROPIC_API_KEY"]
    cn = env.get("CRISTIAN_NAME", "Cristian Orihuela")
    pn = env.get("CRISTIAN_PROPERTY_NAME", "Sun & Moon at 30A")
    voice = load_voice_samples()

    WAVE = "enhanced-wave-2026-08"

    if args.reslot:
        ready = sb_get(base, key,
            f"outreach?status=eq.queued&metadata->>regen=eq.{WAVE}&metadata->>hold=is.null"
            "&select=id,metadata&order=metadata->>scheduled_for&limit=500")
        slots = send_slots()
        n = 0
        for r in ready:
            sched = next(slots)
            sb_patch(base, key, f"outreach?id=eq.{r['id']}",
                     {"metadata": {**(r.get("metadata") or {}), "scheduled_for": sched}})
            n += 1
        print(f"reslotted {n} rows onto {PER_DAY}/weekday from {START_DATE}")
        return 0

    if args.topup:
        # unique-company selection from BOTH campaigns' entire queued pool
        camp_ids = []
        for camp_slug, _ in TARGETS:
            c = sb_get(base, key, f"campaigns?slug=eq.{camp_slug}&select=id&limit=1")
            if c: camp_ids.append((camp_slug, c[0]["id"]))
        ready = sb_get(base, key,
            f"outreach?status=eq.queued&metadata->>regen=eq.{WAVE}&metadata->>hold=is.null"
            "&select=company_id,metadata&limit=500")
        used_companies = {r["company_id"] for r in ready}
        used_emails = {(r.get("metadata") or {}).get("to_email", "").lower() for r in ready}
        sent_recent = sb_get(base, key,
            "outreach?status=eq.sent&sent_at=gte." +
            (datetime.now(timezone.utc) - timedelta(days=60)).isoformat().replace("+00:00","Z") + "&select=company_id&limit=2000")
        used_companies |= {r["company_id"] for r in sent_recent}

        pool = []
        for camp_slug, cid in camp_ids:
            pool += [(camp_slug, r) for r in sb_get(base, key,
                f"outreach?campaign_id=eq.{cid}&status=eq.queued"
                f"&select=id,contact_id,company_id,campaign_id,metadata&order=created_at&limit=600")]
        BOGUS = ("@mysite.com", "@example.com", "@sentry.io", "@wixpress.com", "@domain.com")
        picked = []
        for camp_slug, r in pool:
            if len(picked) >= args.topup: break
            md = r.get("metadata") or {}
            if md.get("regen") == WAVE and not md.get("hold"): continue      # already in wave
            to = (md.get("to_email") or "").lower()
            if not to or any(to.endswith(b) for b in BOGUS): continue
            if r["company_id"] in used_companies or to in used_emails: continue
            used_companies.add(r["company_id"]); used_emails.add(to)
            picked.append((camp_slug, r))
        rows = picked
        print(f"topup: {len(rows)} unique-company rows selected")
    else:
        rows = []

    if not args.topup:
     for camp_slug, take in TARGETS:
        camp = sb_get(base, key, f"campaigns?slug=eq.{camp_slug}&select=id&limit=1")
        if not camp:
            print(f"⚠ campaign {camp_slug} not found"); continue
        batch = sb_get(base, key,
            f"outreach?campaign_id=eq.{camp[0]['id']}&status=eq.queued"
            f"&select=id,contact_id,company_id,campaign_id,metadata&order=created_at&limit={take}")
        rows += [(camp_slug, r) for r in batch]
    if args.limit:
        rows = rows[: args.limit]
    print(f"wave: {len(rows)} queued rows to regenerate")
    if args.dry_run:
        for camp_slug, r in rows[:10]:
            print(" ", camp_slug, r["id"][:8], (r.get("metadata") or {}).get("to_email"))
        print("(dry-run — nothing changed)")
        return 0

    taken = {p["slug"] for p in sb_get(base, key, "rev_share_partners?select=slug&limit=2000")}
    slots = send_slots()
    ok = fail = 0
    log_path = Path(__file__).parent / "regen_wave.log"
    with open(log_path, "a") as log:
        BOGUS = ("@mysite.com", "@example.com", "@sentry.io", "@wixpress.com", "@domain.com")
        for i, (camp_slug, r) in enumerate(rows, 1):
            try:
                to = ((r.get("metadata") or {}).get("to_email") or "").lower()
                if any(to.endswith(b) for b in BOGUS):
                    sb_patch(base, key, f"outreach?id=eq.{r['id']}",
                             {"status": "failed", "metadata": {**(r.get("metadata") or {}), "error": "bogus email domain"}})
                    line = f"[{i}/{len(rows)}] SKIP bogus {to}"
                    print(line); log.write(line + "\n"); continue
                ctx = context_for(base, key, r["contact_id"], r["company_id"])
                slug = ensure_partner(base, key, ctx["company_name"], ctx["email"], taken)
                ctx["partner_link"] = f"{BOOKING_BASE}/?partner={slug}"
                ctx["partner_discount"] = GUEST_DISCOUNT

                subject, body = generate_email(anth, ctx, cn, pn, voice)
                text_body = body
                if slug not in text_body:
                    text_body = text_body.rstrip() + f"\n\nYour clients' private link (already live): {ctx['partner_link']}"

                def mk(orig):
                    _, tracked = create_tracking_link(
                        base, key, destination_url=orig,
                        contact_id=r["contact_id"], company_id=r["company_id"],
                        campaign_id=r["campaign_id"], utm_campaign=camp_slug)
                    return tracked

                link_map = collect_tracked_links(text_body, mk)
                pixel = f"{TRACKING_DOMAIN}/o/{r['id']}"
                photo = PROPERTY_PHOTO_CID if _os.path.exists(PROPERTY_PHOTO_PATH) else None
                html = html_from_text(text_body, pixel, link_map, embed_photo_cid=photo)
                sched = next(slots)

                md_clean = dict(r.get("metadata") or {})
                md_clean.pop("hold", None)
                sb_patch(base, key, f"outreach?id=eq.{r['id']}", {
                    "subject": subject, "body": text_body,
                    "metadata": {**md_clean,
                        "scheduled_for": sched, "model": ANTHROPIC_MODEL,
                        "html_body": html, "partner_slug": slug,
                        "regen": "enhanced-wave-2026-08"},
                })
                ok += 1
                line = f"[{i}/{len(rows)}] ok {ctx['company_name'][:40]:40s} → {slug:32s} @ {sched}"
                print(line); log.write(line + "\n"); log.flush()
                time.sleep(0.4)
            except Exception as ex:  # noqa: BLE001
                fail += 1
                line = f"[{i}/{len(rows)}] FAIL outreach={r['id'][:8]} — {str(ex)[:160]}"
                print(line); log.write(line + "\n"); log.flush()
                time.sleep(1.5)
    print(f"\ndone: {ok} regenerated, {fail} failed (log: {log_path})")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
