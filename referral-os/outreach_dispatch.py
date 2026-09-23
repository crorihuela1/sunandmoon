#!/usr/bin/env python3
"""
DISPATCHER — finds queued outreach rows whose scheduled_for is past, sends them.

Designed to run from cron/launchd every 5-15 min. Idempotent and safe:
  - Picks up rows with status='queued' AND metadata.scheduled_for <= now()
  - Sends each via Gmail SMTP with pacing
  - Updates status to 'sent' on success, 'failed' on error
  - Advances company_segments.status: prospect → contacted

Run modes
---------
    # One-shot: process whatever's currently due, then exit
    python3 outreach_dispatch.py --dispatch-due

    # Daemon: stay running, check every 5 min
    python3 outreach_dispatch.py --daemon --interval 300

    # Dry-run: show what would be sent, don't actually send
    python3 outreach_dispatch.py --dispatch-due --dry-run

    # Cap: only send N rows per invocation (Gmail-friendly)
    python3 outreach_dispatch.py --dispatch-due --limit 50

Per-run pacing
--------------
    --pace 30      seconds between sends inside this run (default 30)
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone

import os as _os
from outreach_email import (
    PROJECT_ID, ANTHROPIC_MODEL,
    PROPERTY_PHOTO_PATH, PROPERTY_PHOTO_CID,
    load_env,
    sb_get, sb_patch, send_via_gmail,
    html_from_text,
)


def fetch_due(base: str, key: str, limit: int) -> list[dict]:
    """
    Pull queued outreach rows whose scheduled_for has passed.
    Joins company_segments via the segments table to know the slug
    (for the status update).
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    # PostgREST: filter on metadata->>scheduled_for
    path = (
        "outreach?status=eq.queued"
        f"&metadata->>scheduled_for=lte.{now_iso}"
        f"&select=id,subject,body,contact_id,company_id,campaign_id,sequence_step,metadata,"
        "contact:contacts(email,first_name),"
        "campaign:campaigns(slug)"
        f"&order=metadata->>scheduled_for&limit={limit}"
    )
    return sb_get(base, key, path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dispatch-due", action="store_true", help="Send all due rows then exit")
    ap.add_argument("--daemon",       action="store_true", help="Loop forever, check every --interval seconds")
    ap.add_argument("--interval",     type=int, default=300, help="Daemon poll interval (sec)")
    ap.add_argument("--limit",        type=int, default=50,  help="Max sends per dispatch wave")
    ap.add_argument("--pace",         type=int, default=30,  help="Sleep between sends (sec)")
    ap.add_argument("--dry-run",      action="store_true",   help="Don't send, just show what would")
    args = ap.parse_args()

    if not (args.dispatch_due or args.daemon):
        ap.error("Pass --dispatch-due or --daemon")

    env = load_env()
    base       = env["SUPABASE_URL"]
    key        = env["SUPABASE_SERVICE_ROLE_KEY"]
    gmail_user = env["GMAIL_USER"]
    gmail_pass = env["GMAIL_APP_PASSWORD"]
    cn         = env.get("CRISTIAN_NAME", "Cristian Orihuela")

    def one_dispatch_wave() -> int:
        rows = fetch_due(base, key, args.limit)
        if not rows:
            print(f"[{datetime.now().isoformat(timespec='seconds')}] nothing due")
            return 0

        print(f"\n[{datetime.now().isoformat(timespec='seconds')}] {len(rows)} due")
        sent = 0
        for i, r in enumerate(rows, 1):
            email = (r.get("contact") or {}).get("email")
            md    = r.get("metadata") or {}
            scheduled = md.get("scheduled_for")
            html_body = md.get("html_body") or html_from_text(r["body"], "about:blank")
            label = f"{email} ({(r.get('contact') or {}).get('first_name') or '?'})"

            if not email:
                print(f"  [{i}/{len(rows)}] ⚠ {label}: missing email, marking failed")
                if not args.dry_run:
                    sb_patch(base, key, f"outreach?id=eq.{r['id']}",
                             {"status":"failed","metadata":{**md,"error":"missing email"}})
                continue

            # suppression is law: never send to a suppressed address
            if sb_get(base, key, f"outreach_suppression?email=eq.{email.lower()}&select=email&limit=1"):
                print(f"  [{i}/{len(rows)}] ⛔ {label}: suppressed, skipping permanently")
                if not args.dry_run:
                    sb_patch(base, key, f"outreach?id=eq.{r['id']}",
                             {"status":"failed","metadata":{**md,"error":"suppressed"}})
                continue

            print(f"  [{i}/{len(rows)}] → {label}  (scheduled {scheduled})")
            print(f"      \"{r['subject']}\"")

            if args.dry_run:
                continue

            photo_cid = PROPERTY_PHOTO_CID if _os.path.exists(PROPERTY_PHOTO_PATH) else None
            try:
                msg_id = send_via_gmail(
                    smtp_user=gmail_user, smtp_pass=gmail_pass,
                    from_display_name=cn,
                    to_email=email,
                    subject=r["subject"],
                    text_body=r["body"],
                    html_body=html_body,
                    reply_to=gmail_user,
                    inline_image_path=PROPERTY_PHOTO_PATH if photo_cid else None,
                    inline_image_cid=photo_cid,
                )
            except Exception as ex:  # noqa: BLE001
                print(f"      ❌ SMTP failed: {ex}")
                sb_patch(base, key, f"outreach?id=eq.{r['id']}",
                         {"status":"failed","metadata":{**md,"error":str(ex)[:300]}})
                continue

            sb_patch(base, key, f"outreach?id=eq.{r['id']}", {
                "status":              "sent",
                "sent_at":             datetime.now(timezone.utc).isoformat(),
                "provider_message_id": msg_id,
            })

            # Advance company_segments status (best-effort)
            try:
                sb_patch(
                    base, key,
                    f"company_segments?company_id=eq.{r['company_id']}",
                    {"status": "contacted", "first_contacted_at": datetime.now(timezone.utc).isoformat()},
                )
            except Exception:  # noqa: BLE001
                pass

            sent += 1
            print(f"      ✓ sent  (msg-id: {msg_id})")
            if i < len(rows):
                time.sleep(args.pace)
        return sent

    if args.dispatch_due:
        total = one_dispatch_wave()
        print(f"\nDispatched {total} email(s).")
        return 0

    # Daemon mode
    print(f"Daemon started. Polling every {args.interval}s. Ctrl-C to stop.")
    try:
        while True:
            one_dispatch_wave()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopping.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
