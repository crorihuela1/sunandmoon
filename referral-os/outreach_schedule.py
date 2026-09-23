#!/usr/bin/env python3
"""
SCHEDULE LOADER — Press one button to queue 3 weeks of outreach.

What it does
------------
For each (date, segment, count) in the SCHEDULE list below, this script:
  1. Pulls the top N "ready" contacts in that segment.
  2. Generates the personalized email via Claude.
  3. Inserts an `outreach` row with status='queued' and scheduled_for=<send time>.
  4. Pre-creates tracking_links and embeds the open pixel in the saved body.

Nothing is actually SENT here — only queued. The companion dispatcher
script (outreach_dispatch.py) wakes up via cron/launchd, finds queued
rows with scheduled_for <= now(), and sends them.

This separation lets you:
  - Generate everything in one focused session (~30 min, ~$2 in Claude API)
  - Review every email in the DB before any go out
  - Reschedule by editing `scheduled_for` if needed
  - Run dispatcher headlessly via cron

Run it
------
    cd "/Users/samantha/Documents/Claude/Projects/Sun & Moon at 30a/referral-os"

    # Dry-run preview: shows what would be queued, no DB writes
    python3 outreach_schedule.py --dry-run

    # Queue it all
    python3 outreach_schedule.py --queue
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone, time as dtime
from zoneinfo import ZoneInfo

# Re-use the engine functions
import os as _os
from outreach_email import (
    PROJECT_ID, TRACKING_DOMAIN, PROPERTY_URL, ANTHROPIC_MODEL,
    PROPERTY_PHOTO_PATH, PROPERTY_PHOTO_CID,
    load_env, load_voice_samples,
    sb_get, sb_post, sb_patch,
    generate_email, create_tracking_link,
    collect_tracked_links, html_from_text,
    get_or_create_campaign,
)

# ============================================================
# THE SCHEDULE — edit this to change timing
# ============================================================
# Each row: (date string YYYY-MM-DD, hour-of-day-CT, segment-slug, max contacts)
# Days are spaced Tue-Wed-Thu only; volumes ramp gently.
#
# Reset 2026-07-06 after bogus-contact cleanup (186 placeholders removed).
# 657 real outreach targets remaining. 7 send-days over 2.5 weeks.
# All emails use the post-Marisol prompt + embedded property photo.
SCHEDULE = [
    # Week 1 — warmup with locals (highest conversion segment)
    ("2026-07-07",  9, "wedding-planner-local",   30),  # part 1 of 65
    ("2026-07-07", 10, "photographer-family",     12),  # add photogs
    ("2026-07-08",  9, "wedding-planner-local",   35),  # finish locals

    # Week 2 — ramp into drive-market feeders (388 total)
    ("2026-07-14",  9, "wedding-planner-feeder",  80),
    ("2026-07-15",  9, "wedding-planner-feeder", 130),
    ("2026-07-16",  9, "wedding-planner-feeder", 178),  # finish feeders

    # Week 3 — bachelorette planners (192 total, cleaned segment)
    ("2026-07-21",  9, "bachelorette-planner",   100),
    ("2026-07-22",  9, "bachelorette-planner",    92),  # finish
]

CT = ZoneInfo("America/Chicago")  # 30A local time


def queue_one_email(
    base, key, contact, segment_slug, campaign_id,
    anthropic_key, cristian_name, property_name, voice_samples,
    send_at_iso,
):
    """Generate + queue (status='queued') a single email row."""
    subject, body = generate_email(
        anthropic_key, contact, cristian_name, property_name, voice_samples,
    )

    # Pre-create outreach row to get an id for the pixel
    pre = sb_post(base, key, "outreach", [{
        "project_id":     PROJECT_ID,
        "campaign_id":    campaign_id,
        "contact_id":     contact["contact_id"],
        "company_id":     contact["company_id"],
        "channel":        "email",
        "sequence_step":  1,
        "subject":        subject,
        "body":           body,
        "status":         "queued",
        "metadata": {
            "to_email":      contact["email"],
            "scheduled_for": send_at_iso,
            "model":         ANTHROPIC_MODEL,
        },
    }])
    outreach_id = pre[0]["id"]

    # Plain-text body keeps the original URL visible (clean).
    # HTML body shows the clean URL as link text but href to the tracker.
    text_body = body
    if "sunandmoon30a.com" not in text_body:
        text_body = text_body.rstrip() + f"\n\nMore on the houses: {PROPERTY_URL}"

    def create_link(orig):
        _, tracked = create_tracking_link(
            base, key,
            destination_url=orig,
            contact_id=contact["contact_id"],
            company_id=contact["company_id"],
            campaign_id=campaign_id,
            utm_campaign=segment_slug,
        )
        return tracked

    link_map  = collect_tracked_links(text_body, create_link)
    pixel_url = f"{TRACKING_DOMAIN}/o/{outreach_id}"
    photo_cid = PROPERTY_PHOTO_CID if _os.path.exists(PROPERTY_PHOTO_PATH) else None
    html_body = html_from_text(text_body, pixel_url, link_map, embed_photo_cid=photo_cid)

    # Persist body (originals intact) + rendered HTML + scheduled_for in metadata
    sb_patch(base, key, f"outreach?id=eq.{outreach_id}", {
        "body":     text_body,
        "metadata": {
            "to_email":      contact["email"],
            "scheduled_for": send_at_iso,
            "model":         ANTHROPIC_MODEL,
            "html_body":     html_body,
        },
    })
    return outreach_id


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Preview only, no DB writes")
    ap.add_argument("--queue",   action="store_true", help="Actually queue everything")
    args = ap.parse_args()

    if not args.dry_run and not args.queue:
        ap.error("Pass either --dry-run or --queue")

    env = load_env()
    base = env["SUPABASE_URL"]
    key  = env["SUPABASE_SERVICE_ROLE_KEY"]
    anth = env["ANTHROPIC_API_KEY"]
    cn   = env.get("CRISTIAN_NAME", "Cristian Orihuela")
    pn   = env.get("CRISTIAN_PROPERTY_NAME", "Sun & Moon at 30A")
    voice = load_voice_samples()

    print("Schedule loader — Sun & Moon at 30A outreach")
    print("=" * 70)
    print(f"{'Date (CT)':<14} {'Time':<7} {'Segment':<26} {'Target':>7}")
    print("-" * 70)
    grand_total = 0
    for d, h, seg, n in SCHEDULE:
        print(f"{d:<14} {h:02d}:00   {seg:<26} {n:>7}")
        grand_total += n
    print("-" * 70)
    print(f"{'TOTAL':<48} {grand_total:>7}")
    print()

    if args.dry_run:
        # Show 1 sample contact per scheduled day to confirm audience exists
        print("Sample contacts that would be queued (first per day):")
        for d, h, seg, n in SCHEDULE:
            rows = sb_get(base, key,
                f"v_outreach_ready?segment_slug=eq.{seg}&limit=1&order=tier,relevance.desc.nullslast,reviews.desc.nullslast")
            sample = (rows[0]["company_name"] if rows else "(none ready)")
            print(f"  {d} {h:02d}:00 CT  {seg:<26}  → {sample}")
        print("\n(dry-run — nothing queued)")
        return 0

    # Real queue mode
    for d, h, seg, target in SCHEDULE:
        send_at = datetime.fromisoformat(d).replace(hour=h, minute=0, tzinfo=CT)
        send_at_iso = send_at.astimezone(timezone.utc).isoformat()

        # Resolve campaign for this segment
        camp_slug = f"{seg}-email-{datetime.now().strftime('%Y%m')}"
        camp_name = f"{seg} email — {datetime.now().strftime('%b %Y')}"
        camp_id   = get_or_create_campaign(base, key, camp_slug, camp_name)

        # Pull the target audience
        contacts = sb_get(base, key,
            f"v_outreach_ready?segment_slug=eq.{seg}&limit={target}")
        if not contacts:
            print(f"⚠ {d} {seg}: 0 ready contacts. Skipping.")
            continue

        print(f"\n→ {d} {h:02d}:00 CT  {seg}  ({len(contacts)} contacts)")
        for i, c in enumerate(contacts, 1):
            try:
                oid = queue_one_email(
                    base, key, c, seg, camp_id,
                    anth, cn, pn, voice,
                    send_at_iso,
                )
                print(f"   [{i:>3}/{len(contacts)}] queued  {c['company_name'][:38]:38s}  → outreach {oid[:8]}")
            except Exception as ex:  # noqa: BLE001
                print(f"   [{i:>3}/{len(contacts)}] ❌ {c['company_name'][:38]:38s}: {ex}")

    print()
    print("=" * 70)
    print("Queue ready. Next: deploy the dispatcher (cron/launchd) to fire them.")
    print("Or run a one-shot dispatch:")
    print("  python3 outreach_dispatch.py --dispatch-due")
    return 0


if __name__ == "__main__":
    sys.exit(main())
