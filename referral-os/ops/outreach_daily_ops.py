#!/usr/bin/env python3
"""
DAILY OPS — the outreach autopilot's morning routine. Runs once a day (launchd).

  1. REPLY SCAN   — IMAP-scan the experience@ inbox; any message from a
                    contacted outreach address marks that row replied
                    (metadata.replied_at) and advances company_segments
                    to 'responded'. Replied contacts never get nurtured.
  2. NURTURE      — view-based follow-ups: contacts whose email was OPENED
                    or CLICKED ≥ NURTURE_AFTER_DAYS ago, no reply, still on
                    sequence_step 1 → queue a short step-2 follow-up
                    (capped at NURTURE_CAP/day; scheduled for ~1h later so
                    the dispatcher picks it up naturally).
  3. DIGEST       — email the owner(s) the last-24h picture: sends, opens,
                    clicks, replies, nurture queued, partner-attributed
                    bookings, and the most-engaged companies.

Central objective: revenue and relationships — replies surface immediately,
warm contacts get exactly one thoughtful nudge, and every partner booking is
attributed in the digest.
"""
from __future__ import annotations

import email as email_lib
import imaplib
import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import os as _os
from outreach_email import (  # noqa: E402
    PROJECT_ID, TRACKING_DOMAIN, ANTHROPIC_MODEL,
    load_env, sb_get, sb_post, sb_patch, send_via_gmail, html_from_text,
)

DIGEST_TO = ["corihuela@gmail.com", "experience@sunandmoon30a.com"]
NURTURE_AFTER_DAYS = 4
NURTURE_CAP = 15
BOOKING_BASE = "https://book.sunandmoon30a.com"

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


def now_utc():
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------
# 1. Reply scan
# ------------------------------------------------------------------
def scan_replies(env, base, key, days=7):
    """Match recent inbox senders against contacted outreach; mark replies."""
    sent_rows = sb_get(base, key,
        "outreach?status=eq.sent&channel=eq.email"
        "&select=id,contact_id,company_id,sent_at,metadata,contact:contacts(email)")
    by_email = {}
    for r in sent_rows:
        addr = ((r.get("contact") or {}).get("email") or "").lower()
        if addr:
            by_email.setdefault(addr, []).append(r)

    found = []
    try:
        M = imaplib.IMAP4_SSL("imap.gmail.com")
        M.login(env["GMAIL_USER"], env["GMAIL_APP_PASSWORD"])
        M.select("INBOX", readonly=True)
        since = (now_utc() - timedelta(days=days)).strftime("%d-%b-%Y")
        _, data = M.search(None, f'(SINCE "{since}")')
        ids = data[0].split()
        for mid in ids[-400:]:
            _, msg_data = M.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            if not msg_data or not msg_data[0]:
                continue
            hdr = email_lib.message_from_bytes(msg_data[0][1])
            sender = parseaddr(hdr.get("From", ""))[1].lower()
            subj = str(hdr.get("Subject", ""))
            # OOO/auto-responses are NOT replies: never mark replied, never
            # advance/stop a sequence (guardrail v2.1)
            AUTO = ("out of office", "email received:", "auto-reply", "autoreply", "automatic reply", "away from")
            if any(a in subj.lower() for a in AUTO):
                continue
            if sender in by_email:
                for row in by_email[sender]:
                    md = row.get("metadata") or {}
                    if not md.get("replied_at"):
                        sb_patch(base, key, f"outreach?id=eq.{row['id']}",
                                 {"metadata": {**md, "replied_at": now_utc().isoformat(),
                                               "reply_subject": subj[:200]}})
                        sb_patch(base, key, f"company_segments?company_id=eq.{row['company_id']}",
                                 {"status": "responded"})
                        found.append({"email": sender, "subject": str(hdr.get("Subject", ""))[:120]})
        M.logout()
    except Exception as ex:  # noqa: BLE001
        print(f"reply scan error (non-fatal): {ex}")
    return found


# ------------------------------------------------------------------
# 2. View-based nurture
# ------------------------------------------------------------------
FOLLOWUP_PROMPT = """You are writing a SHORT follow-up (step 2) for Cristian Orihuela, owner of Sun & Moon \
at 30A (two adjacent vacation rental cottages in Seagrove Beach, FL that sleep 16 combined).

Context: the recipient received a first email about a referral partnership and OPENED it (some clicked), \
but has not replied. They run: {company} ({segment}).
Their private booking link (live, applies their clients' 5% discount automatically): {link}

Write a 60-90 word follow-up. Rules: complete sentences; warm, unpushy, confident; reference that you \
wrote recently; add ONE new concrete detail (their clients see live availability and can book in about \
two minutes, or the preferred rate when booking both cottages together); restate the link; end with a \
one-line question that is easy to answer. Sign off "Best, Cristian". Output format EXACTLY:
SUBJECT: <subject, 5-9 words, sentence case, include "Sun & Moon">
---
<body>"""


def queue_nurture(env, base, key):
    """Opened/clicked ≥ N days ago, no reply, still step 1 → queue step 2."""
    cutoff = (now_utc() - timedelta(days=NURTURE_AFTER_DAYS)).isoformat().replace("+00:00","Z")
    sent_rows = sb_get(base, key,
        f"outreach?status=eq.sent&sequence_step=eq.1&sent_at=lte.{cutoff}"
        "&select=id,contact_id,company_id,campaign_id,subject,metadata,"
        "contact:contacts(email,first_name),company:companies(name)")
    queued = []
    for r in sent_rows:
        if len(queued) >= NURTURE_CAP:
            break
        md = r.get("metadata") or {}
        if md.get("replied_at") or md.get("nurture_queued"):
            continue
        # suppression is law: a listed email is never contacted again
        to_check = ((r.get("contact") or {}).get("email") or "").lower()
        if to_check and sb_get(base, key,
                f"outreach_suppression?email=eq.{to_check}&select=email&limit=1"):
            continue
        # already has a step-2 row?
        step2 = sb_get(base, key,
            f"outreach?contact_id=eq.{r['contact_id']}&sequence_step=eq.2&select=id&limit=1")
        if step2:
            continue
        # engagement: any open/click event for this contact
        ev = sb_get(base, key,
            f"tracking_events?contact_id=eq.{r['contact_id']}"
            "&event_type=in.(email_open,email_click,link_click)&select=id&limit=1")
        if not ev:
            continue

        company = (r.get("company") or {}).get("name") or "their business"
        seg_md = md.get("partner_slug")
        link = f"{BOOKING_BASE}/?partner={seg_md}" if seg_md else BOOKING_BASE
        prompt = FOLLOWUP_PROMPT.format(company=company, segment=md.get("utm_campaign") or "referral partner", link=link)
        body_req = {"model": ANTHROPIC_MODEL, "max_tokens": 400,
                    "messages": [{"role": "user", "content": prompt}]}
        req = urllib.request.Request(ANTHROPIC_URL, data=json.dumps(body_req).encode(), method="POST",
            headers={"x-api-key": env["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                out = json.loads(resp.read())
            text = "".join(b.get("text", "") for b in out.get("content", []) if b.get("type") == "text").strip()
            subject, _, body = text.partition("---")
            subject = subject.replace("SUBJECT:", "").strip() or f"Sun & Moon at 30A — quick follow-up"
            body = body.strip()
            if not body:
                continue
        except Exception as ex:  # noqa: BLE001
            print(f"nurture gen failed for {company}: {ex}")
            continue

        to_email = (r.get("contact") or {}).get("email")
        # send ~1h out, but never outside 8am-6pm CT — else next morning 9:30 CT
        from zoneinfo import ZoneInfo
        ct = ZoneInfo("America/Chicago")
        cand = now_utc() + timedelta(hours=1)
        local = cand.astimezone(ct)
        if local.hour < 8 or local.hour >= 18:
            nxt = local if local.hour < 8 else local + timedelta(days=1)
            local = nxt.replace(hour=9, minute=30, second=0, microsecond=0)
        sched = local.astimezone(timezone.utc).isoformat()
        pre = sb_post(base, key, "outreach", [{
            "project_id": PROJECT_ID, "campaign_id": r["campaign_id"],
            "contact_id": r["contact_id"], "company_id": r["company_id"],
            "channel": "email", "sequence_step": 2,
            "subject": subject, "body": body, "status": "queued",
            "metadata": {"to_email": to_email, "scheduled_for": sched,
                         "model": ANTHROPIC_MODEL, "nurture_reason": "opened_no_reply",
                         "parent_outreach": r["id"], "partner_slug": seg_md,
                         "html_body": html_from_text(body, f"{TRACKING_DOMAIN}/o/PENDING")},
        }])
        oid = pre[0]["id"]
        # fix pixel with real id
        sb_patch(base, key, f"outreach?id=eq.{oid}", {"metadata": {
            "to_email": to_email, "scheduled_for": sched, "model": ANTHROPIC_MODEL,
            "nurture_reason": "opened_no_reply", "parent_outreach": r["id"], "partner_slug": seg_md,
            "html_body": html_from_text(body, f"{TRACKING_DOMAIN}/o/{oid}")}})
        sb_patch(base, key, f"outreach?id=eq.{r['id']}", {"metadata": {**md, "nurture_queued": oid}})
        queued.append({"email": to_email, "company": company, "subject": subject})
    return queued


# ------------------------------------------------------------------
# 3. Digest
# ------------------------------------------------------------------
def build_digest(base, key, replies, nurtured):
    day_ago = (now_utc() - timedelta(hours=24)).isoformat().replace("+00:00","Z")
    sent = sb_get(base, key, f"outreach?status=eq.sent&sent_at=gte.{day_ago}&select=id,subject,contact:contacts(email),company:companies(name)")
    opens = sb_get(base, key, f"tracking_events?event_type=eq.email_open&occurred_at=gte.{day_ago}&select=company_id")
    clicks = sb_get(base, key, f"tracking_events?event_type=in.(email_click,link_click)&occurred_at=gte.{day_ago}&select=company_id")
    still_queued = sb_get(base, key, "outreach?status=eq.queued&select=id")
    partner_bookings = sb_get(base, key,
        f"reservations?partner_id=not.is.null&created_at=gte.{day_ago}"
        "&select=confirmation_code,partner_slug,total,status")

    # engaged companies (opens+clicks last 24h)
    from collections import Counter
    eng = Counter()
    for e in opens + clicks:
        if e.get("company_id"):
            eng[e["company_id"]] += 1
    top = []
    for cid, n in eng.most_common(5):
        c = sb_get(base, key, f"companies?id=eq.{cid}&select=name&limit=1")
        top.append(f"  • {c[0]['name'] if c else cid} — {n} event(s)")

    L = [f"SUN & MOON — OUTREACH DAILY DIGEST — {now_utc().astimezone().strftime('%a %b %d, %Y')}",
         "=" * 56, "",
         f"Sent (24h):            {len(sent)}",
         f"Opens (24h):           {len(opens)}",
         f"Clicks (24h):          {len(clicks)}",
         f"Replies detected:      {len(replies)}",
         f"Nurture queued today:  {len(nurtured)}",
         f"Still in send queue:   {len(still_queued)}",
         f"Partner bookings 24h:  {len(partner_bookings)}", ""]
    if replies:
        L.append("REPLIES — answer these today:")
        L += [f"  • {r['email']} — \"{r['subject']}\"" for r in replies]
        L.append("")
    if partner_bookings:
        L.append("PARTNER-ATTRIBUTED BOOKINGS 🎉")
        L += [f"  • {b['confirmation_code']} via {b['partner_slug']} — ${b['total']} ({b['status']})" for b in partner_bookings]
        L.append("")
    if top:
        L.append("MOST ENGAGED (24h):")
        L += top
        L.append("")
    if nurtured:
        L.append("FOLLOW-UPS QUEUED (view-based nurture):")
        L += [f"  • {n['company']} <{n['email']}> — \"{n['subject']}\"" for n in nurtured]
        L.append("")
    if sent:
        L.append("SENT (24h):")
        L += [f"  • {(s.get('company') or {}).get('name') or '?'} <{(s.get('contact') or {}).get('email')}>" for s in sent[:30]]
    L += ["", f"Dashboards: admin (bookings) · {BOOKING_BASE}", "— outreach autopilot"]
    return "\n".join(L)


def main():
    env = load_env()
    base, key = env["SUPABASE_URL"], env["SUPABASE_SERVICE_ROLE_KEY"]

    print(f"[{now_utc().isoformat(timespec='seconds')}] daily ops start")
    replies = scan_replies(env, base, key)
    print(f"  replies found: {len(replies)}")
    nurtured = queue_nurture(env, base, key)
    print(f"  nurture queued: {len(nurtured)}")
    digest = build_digest(base, key, replies, nurtured)
    esc = digest.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    digest_html = f"<pre style='font-family:ui-monospace,Menlo,monospace;font-size:13px;line-height:1.5'>{esc}</pre>"
    for to in DIGEST_TO:
        send_via_gmail(smtp_user=env["GMAIL_USER"], smtp_pass=env["GMAIL_APP_PASSWORD"],
                       from_display_name="Sun & Moon Outreach",
                       to_email=to, subject=f"Outreach digest — {now_utc().astimezone().strftime('%b %d')}",
                       text_body=digest, html_body=digest_html, reply_to=env["GMAIL_USER"])
        print(f"  digest sent → {to}")
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
