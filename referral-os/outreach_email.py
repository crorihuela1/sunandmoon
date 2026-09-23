#!/usr/bin/env python3
"""
EMAIL OUTREACH ENGINE — Gmail SMTP + Claude personalization.

What it does
------------
1. Pulls "ready to email" contacts from Postgres via v_outreach_ready
   (filters: has email, status=prospect, not contacted in last 60 days).
2. For each contact, generates a personalized email via Claude API using
   your voice samples + the recipient's context.
3. Sends via Gmail SMTP (uses GMAIL_USER + GMAIL_APP_PASSWORD from .env).
4. Logs every send to the `outreach` table with sent_at / subject / body /
   Message-ID.
5. Flips company_segments.status from 'prospect' to 'contacted' on success.

Three safety layers
-------------------
  • DEFAULT IS DRY-RUN. You must pass `--send` to actually send.
  • `--test-self` redirects every email to GMAIL_USER (yourself) so you
    can review what your partners would receive without touching them.
  • `--limit N` caps the batch. Always start small (5-10).

Run it
------
    cd "/Users/samantha/Documents/Claude/Projects/Sun & Moon at 30a/referral-os"

    # Apply the schema views first (once)
    # → paste schema/005_outreach_views.sql into Supabase SQL Editor

    # Dry-run preview — what would be sent? No DB writes, no emails.
    python3 outreach_email.py --segment private-chef --limit 5

    # Test-self — generate + actually send 5 emails, but TO YOURSELF.
    # Real DB writes. Real personalization. You see them in your inbox.
    python3 outreach_email.py --segment private-chef --limit 5 --send --test-self

    # Real send — first 10 real chefs get a personalized email.
    python3 outreach_email.py --segment private-chef --limit 10 --send

    # Wider segments
    python3 outreach_email.py --segment wedding-planner-local --limit 20 --send

Configuration (in .env)
-----------------------
    GMAIL_USER=cristian@yourdomain.com
    GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx        # 16-char app password
    ANTHROPIC_API_KEY=sk-ant-...                  # for personalization
    CRISTIAN_NAME=Cristian Orihuela
    CRISTIAN_PROPERTY_NAME=Sun & Moon at 30A
    CRISTIAN_PROPERTY_URL=https://...             # whatever your booking page is
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import smtplib
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, make_msgid
from pathlib import Path as _Path

# ============================================================
# Config
# ============================================================
PROJECT_ID         = "310c4ae6-eec9-47b7-9e22-b95a6dc69365"  # Sun & Moon project
ANTHROPIC_MODEL    = "claude-haiku-4-5-20251001"             # Cheap + fast for per-email personalization
ANTHROPIC_URL      = "https://api.anthropic.com/v1/messages"
GMAIL_SMTP_HOST    = "smtp.gmail.com"
GMAIL_SMTP_PORT    = 587
HTTP_TIMEOUT       = 30
DELAY_BETWEEN_SENDS = 2.0       # seconds — be a polite SMTP citizen
DAILY_SAFETY_CAP   = 250        # cap per script run (Gmail Workspace limit is 2000/day, personal ~500)
VOICE_SAMPLE_PATH  = "outreach_voice_samples.md"
TRACKING_DOMAIN    = "https://track.sunandmoon30a.com"
PROPERTY_URL       = "https://sunandmoon30a.com"  # default destination for tracked links
PROPERTY_PHOTO_PATH = str(_Path(__file__).parent / "assets" / "sun_moon_houses.jpg")
PROPERTY_PHOTO_CID  = "sun_moon_houses"  # referenced as <img src="cid:sun_moon_houses">
PROPERTY_PHOTO_CAPTION = "Golden Sun &amp; Blue Moon — two adjacent cottages, Crystal Court, Seagrove Beach"


# ============================================================
# .env loader
# ============================================================
def load_env(path: str = ".env") -> dict[str, str]:
    env = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


# ============================================================
# Supabase REST
# ============================================================
def sb_headers(key: str) -> dict[str, str]:
    return {
        "apikey":        key,
        "Authorization": f"Bearer {key}",
        "Content-Type":  "application/json",
        "User-Agent":    "ReferralOS/1.0",
    }


def sb_get(base: str, key: str, path: str) -> list:
    req = urllib.request.Request(
        f"{base.rstrip('/')}/rest/v1/{path}",
        headers={**sb_headers(key), "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read())


def sb_post(base: str, key: str, table: str, rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    req = urllib.request.Request(
        f"{base.rstrip('/')}/rest/v1/{table}",
        data=json.dumps(rows).encode(),
        method="POST",
        headers={**sb_headers(key), "Prefer": "return=representation"},
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read())


def sb_patch(base: str, key: str, path: str, body: dict) -> list[dict]:
    req = urllib.request.Request(
        f"{base.rstrip('/')}/rest/v1/{path}",
        data=json.dumps(body).encode(),
        method="PATCH",
        headers={**sb_headers(key), "Prefer": "return=representation"},
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read())


# ============================================================
# Claude personalization
# ============================================================
def load_voice_samples() -> str:
    if not os.path.exists(VOICE_SAMPLE_PATH):
        return ""
    with open(VOICE_SAMPLE_PATH) as f:
        return f.read()


def generate_email(
    anthropic_key: str,
    contact: dict,
    cristian_name: str,
    property_name: str,
    voice_samples: str,
) -> tuple[str, str]:
    """
    Calls Claude API to generate (subject, body) tailored to this contact.
    Returns ('Subject line', 'Email body text').
    """
    # Build a tight context block from what we have
    ctx_lines = [
        f"- First name: {contact.get('first_name') or '(unknown)'}",
        f"- Company: {contact.get('company_name')}",
        f"- Segment: {contact.get('segment_name')} (slug: {contact.get('segment_slug')})",
        f"- Location: {contact.get('city') or '?'}, {contact.get('state') or '?'}",
        f"- Market role: {contact.get('market_role') or '?'} ({contact.get('market_metro') or '?'})",
    ]
    if contact.get("partner_link"):
        ctx_lines.append(f"- Their PRIVATE partner booking link (already live): {contact['partner_link']}")
        ctx_lines.append(f"- Guest discount their clients get through that link: {contact.get('partner_discount', 5)}%")
    if contact.get("editorial_summary"):
        ctx_lines.append(f"- Google's description of them: \"{contact['editorial_summary']}\"")
    if contact.get("rating") and contact.get("reviews"):
        ctx_lines.append(f"- Google rating: {contact['rating']} ({contact['reviews']} reviews)")
    if contact.get("website"):
        ctx_lines.append(f"- Website: {contact['website']}")

    context = "\n".join(ctx_lines)

    system_prompt = f"""You are writing a personalized cold outreach email for {cristian_name}, owner of {property_name} \
— two adjacent vacation rental houses on 30A (Florida panhandle: Blue Moon at 65 Crystal Ct and Golden Sun \
at 53 Crystal Ct, in Seagrove Beach). Goal: get the recipient interested in being a referral partner \
(they recommend the houses to their clients; both sides win).

Cristian's two houses are unique because they are ADJACENT — they sleep 16 combined, have two full kitchens, \
and have two yards that connect into one shared outdoor space. This makes them ideal for groups (wedding \
parties, multi-generational families, bachelorette weekends, corporate retreats) that want both privacy \
AND togetherness in a single booking and address.

WHAT IS NEW (use this — it is the strongest material in the email):
- Sun & Moon now has a live direct-booking site: book.sunandmoon30a.com. Guests see live availability and \
  real prices and can book with a card in about two minutes — no listing-site runaround.
- If the recipient context includes a PRIVATE partner booking link, that link is ALREADY LIVE for them: \
  it greets their clients by the business's name and automatically applies their guest discount. Include the \
  link in the email and present it as something already set up for them — "I've already created your link; \
  try it" is far stronger than "we could set something up."
- Referral partners earn a revenue share on completed stays booked through their link — this is tracked \
  automatically by the link, so there is no paperwork and nothing for them to log. Frame it plainly: their \
  clients get a better rate than the listing sites, and the partner gets paid for the referral.
- Booking both houses together ("Sun & Moon Together") carries a preferred rate that beats booking the \
  two cottages separately — worth mentioning for group-oriented recipients (planners, bachelorette).

VOICE GUIDE — match the tone exemplified here:
{voice_samples or '(no voice samples provided — default to warm, professional small-business voice — never casual texting, never marketing-speak)'}

SUBJECT LINE RULES:
- 5 to 9 words. Sentence case (not all lowercase).
- MUST signal the email's purpose so the recipient knows why they should open it. Always include "Sun & Moon at 30A" \
  in the subject, paired with a phrase like "possible partnership", "vacation rental partnership idea", \
  "chef partnership idea", "referral partnership", or similar.
- BAD examples: "two kitchens, one yard" (cryptic — recipient cannot tell what this is about)
- GOOD examples:
  - "Sun & Moon at 30A — possible chef partnership"
  - "Sun & Moon at 30A — vacation rental partnership idea"
  - "Sun & Moon at 30A — partnership idea for Atlanta destination weddings"

OPENING PARAGRAPH RULES — the FIRST paragraph MUST do BOTH of these:
1. Introduce who the sender is and what they run. Example template: "My name is Cristian Orihuela. I own \
   Sun & Moon at 30A — two adjacent vacation rental houses on Crystal Court in Seagrove Beach."
2. State the purpose of the email clearly. Example template: "I'm reaching out because I'd love to explore \
   a referral partnership with <recipient business name>."
If the first paragraph does not do both of these, the email fails. Do not rely on the body to clarify purpose.

WRITING RULES:
- COMPLETE SENTENCES ONLY. No sentence fragments. A fragment is a clause without a subject and verb.
  - BAD: "Way easier than coordinating four different Airbnbs."
  - GOOD: "It is a simpler logistical setup than coordinating four different Airbnbs."
- Warm and conversational, but professional. Sound like a thoughtful small-business owner writing to another \
  small-business owner. Do NOT sound like casual texting. Do NOT sound like marketing copy.
- 130 to 180 words total in the body.
- Reference ONE specific thing about the recipient (their specialty, location, or what their business does).
- Tailor the two-adjacent-houses pitch to THEIR world specifically — chefs hear about the two kitchens; \
  wedding planners hear about splitting bride/groom families; photographers hear about location variety.
- The ask is a referral partnership: ask to be on their housing-options list, AND offer reciprocity \
  (any matching guest who reaches out to us would hear about their business first).
- When a private partner link exists in the context, the CTA must center on it: invite them to click their \
  link and see the experience their clients would get, and to reply so Cristian can walk them through the \
  revenue share. The link makes the offer concrete — lead with action, not abstraction.
- 140 to 200 words total in the body (the partner-link paragraph earns the extra room).
- Sign off "Best, Cristian" or "Thanks, Cristian" (full lines, never just "— Cristian").
- Include the email address experience@sunandmoon30a.com as a reply option.

STRUCTURE (recommended — adapt slightly per recipient but keep all four elements):
- Para 1: Introduction (who I am, what I run) + purpose (exploring a referral partnership with <them>).
- Para 2: Why our setup is relevant to their business — one specific reference to them, then the tailored \
  two-houses pitch.
- Para 3: The ask (be on your list) + the reciprocity (we send referrals back) + reply path.
- Closing line ("Thanks for considering it." or "Thanks for the time.") followed by sign-off.

OUTPUT FORMAT — return EXACTLY this, nothing else:
SUBJECT: <the subject line>
---
<the email body>"""

    user_prompt = f"""Generate a personalized first-touch outreach email for this recipient:

{context}

Generate the email now."""

    body = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": 600,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
    }
    req = urllib.request.Request(
        ANTHROPIC_URL,
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "x-api-key":         anthropic_key,
            "anthropic-version": "2023-06-01",
            "Content-Type":      "application/json",
            "User-Agent":        "ReferralOS/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        resp = json.loads(r.read())

    # Claude API response shape: {content: [{type: 'text', text: '...'}], ...}
    text = "".join(
        block.get("text", "")
        for block in resp.get("content", [])
        if block.get("type") == "text"
    ).strip()

    # Parse SUBJECT: ... \n---\n body
    if "SUBJECT:" in text and "---" in text:
        head, _, rest = text.partition("---")
        subject = head.replace("SUBJECT:", "", 1).strip()
        body_text = rest.strip()
    else:
        # Fallback if model didn't follow the format
        lines = text.splitlines()
        subject = lines[0].lstrip("Subject:").strip() if lines else "quick note"
        body_text = "\n".join(lines[1:]).strip() if len(lines) > 1 else text

    return subject, body_text


# ============================================================
# Tracking helpers
# ============================================================
def make_short_code() -> str:
    """URL-safe random 9-char token used as tracking_links.short_code."""
    return secrets.token_urlsafe(7)[:9]


def create_tracking_link(
    base: str, key: str,
    *, destination_url: str, contact_id: str, company_id: str,
    campaign_id: str | None, utm_campaign: str | None,
) -> tuple[str, str]:
    """
    Insert one tracking_links row. Returns (short_code, full_track_url).
    Adds UTM params to the destination so analytics tools downstream
    (e.g. Plausible, GA) also see the partner.
    """
    short = make_short_code()
    # append utm params to destination so they survive the redirect
    sep = "&" if "?" in destination_url else "?"
    dest_with_utm = (
        f"{destination_url}{sep}"
        f"utm_source=referral-os&utm_medium=email"
        + (f"&utm_campaign={urllib.parse.quote(utm_campaign)}" if utm_campaign else "")
        + f"&utm_content={short}"
    )
    sb_post(base, key, "tracking_links", [{
        "project_id":      PROJECT_ID,
        "short_code":      short,
        "destination_url": dest_with_utm,
        "contact_id":      contact_id,
        "company_id":      company_id,
        "campaign_id":     campaign_id,
        "utm_source":      "referral-os",
        "utm_medium":      "email",
        "utm_campaign":    utm_campaign,
        "utm_content":     short,
    }])
    return short, f"{TRACKING_DOMAIN}/c/{short}"


# Trailing punctuation we shouldn't include in a matched URL
URL_RE       = re.compile(r"https?://[^\s<>\)\]\"\']+")
URL_TRAILING = re.compile(r"[\.,;:!?]+$")


def _clean_url_match(raw: str) -> str:
    """Strip trailing sentence punctuation that a URL regex would over-match."""
    return URL_TRAILING.sub("", raw)


def collect_tracked_links(
    body_text: str,
    create_link_fn,
) -> dict[str, str]:
    """
    Find every http(s):// URL in the body, create a tracking_links row for
    each unique URL, and return a mapping {original_url -> tracked_url}.

    Does NOT modify the body — the original URL stays visible in both the
    plain-text version and as the displayed link text in HTML. Only the
    HTML <a href="..."> attribute points to the tracker.

    This is the "show sunandmoon30a.com, secretly track it" behavior.
    """
    mapping: dict[str, str] = {}
    for m in URL_RE.finditer(body_text):
        url = _clean_url_match(m.group(0))
        if url and url not in mapping:
            mapping[url] = create_link_fn(url)
    return mapping


def _display_url(raw_url: str) -> str:
    """Strip scheme + trailing slash for human-friendly link text."""
    return (raw_url.replace("https://", "")
                   .replace("http://", "")
                   .rstrip("/"))


def html_from_text(
    plain_body: str,
    open_pixel_url: str,
    link_map: dict[str, str] | None = None,
    embed_photo_cid: str | None = None,
) -> str:
    """
    Render the plain-text body as a minimal HTML email with:
      • invisible open pixel
      • URLs displayed as the original (clean) URL but href'd to the tracker
      • optional inline property photo placed AFTER the first paragraph
        (so it visually backs up the "two adjacent houses" claim made in the intro)

    `link_map`: optional dict of {original_url -> tracked_url}. When provided,
    each matched URL renders as <a href="{tracked}">{clean original}</a>.

    `embed_photo_cid`: when provided, an <img src="cid:{cid}"> tag is inserted
    right after the first non-empty paragraph block. The actual image must be
    attached to the MIMEMultipart('related') by send_via_gmail.
    """
    link_map = link_map or {}

    def linkify(text: str) -> str:
        # 1. Escape HTML special chars FIRST
        text = (text.replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;"))
        # 2. Replace URLs with <a> tags. Display = clean original; href = tracker (if mapped)
        def repl(m):
            raw_url = _clean_url_match(m.group(0))
            tracked = link_map.get(raw_url, raw_url)
            display = _display_url(raw_url)
            tail = m.group(0)[len(raw_url):]
            return f'<a href="{tracked}" style="color:#2563eb;text-decoration:underline">{display}</a>{tail}'
        return URL_RE.sub(repl, text)

    # Build paragraph blocks; insert the photo immediately after the FIRST non-empty paragraph
    photo_html = ""
    if embed_photo_cid:
        photo_html = (
            f"<div style='margin:18px 0 18px 0'>"
            f"<img src='cid:{embed_photo_cid}' "
            f"alt='Sun &amp; Moon at 30A — two adjacent vacation rental houses on Crystal Court' "
            f"style='display:block;width:100%;max-width:560px;height:auto;border-radius:6px' />"
            f"<div style='font-size:11.5px;color:#64748b;margin-top:6px;font-style:italic'>"
            f"{PROPERTY_PHOTO_CAPTION}"
            f"</div>"
            f"</div>"
        )

    rendered = []
    first_real_para_inserted_after = False
    for line in plain_body.split("\n"):
        if line.strip():
            rendered.append(f"<p style='margin:0 0 12px 0'>{linkify(line)}</p>")
            if not first_real_para_inserted_after and photo_html:
                rendered.append(photo_html)
                first_real_para_inserted_after = True
        else:
            rendered.append("<p style='margin:0 0 12px 0'>&nbsp;</p>")
    paragraphs = "\n".join(rendered)

    return f"""<!doctype html>
<html><body style="font-family:-apple-system,Helvetica,Arial,sans-serif;font-size:15px;line-height:1.5;color:#222;max-width:620px">
{paragraphs}
<img src="{open_pixel_url}" width="1" height="1" alt="" style="display:none" />
</body></html>"""


# ============================================================
# Gmail SMTP send
# ============================================================
def send_via_gmail(
    smtp_user: str,
    smtp_pass: str,
    from_display_name: str,
    to_email: str,
    subject: str,
    text_body: str,
    html_body: str,
    reply_to: str | None = None,
    inline_image_path: str | None = None,
    inline_image_cid: str | None = None,
) -> str:
    """
    Sends multipart text+html via Gmail SMTP. Returns Message-ID.

    If inline_image_path + inline_image_cid are provided, wraps everything
    in multipart/related so the image can be referenced from the HTML via
    <img src="cid:{cid}"> and renders inline in the recipient's inbox.

    Final structure when image is included:
        multipart/related
          ├── multipart/alternative
          │   ├── text/plain   (text_body)
          │   └── text/html    (html_body — references cid:{cid})
          └── image/jpeg       (Content-ID: <{cid}>, disposition inline)
    """
    has_image = bool(inline_image_path and inline_image_cid and os.path.exists(inline_image_path))

    if has_image:
        msg = MIMEMultipart("related")
        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText(text_body, "plain", "utf-8"))
        alt.attach(MIMEText(html_body, "html",  "utf-8"))
        msg.attach(alt)

        with open(inline_image_path, "rb") as f:
            img = MIMEImage(f.read())  # auto-detects mimetype
        img.add_header("Content-ID", f"<{inline_image_cid}>")
        img.add_header("Content-Disposition", "inline", filename=os.path.basename(inline_image_path))
        msg.attach(img)
    else:
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html",  "utf-8"))

    msg["From"]    = formataddr((from_display_name, smtp_user))
    msg["To"]      = to_email
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to
    msg_id = make_msgid(domain=smtp_user.split("@", 1)[1])
    msg["Message-ID"] = msg_id

    ctx = ssl.create_default_context()
    with smtplib.SMTP(GMAIL_SMTP_HOST, GMAIL_SMTP_PORT, timeout=30) as s:
        s.starttls(context=ctx)
        s.login(smtp_user, smtp_pass)
        s.sendmail(smtp_user, [to_email], msg.as_string())
    return msg_id


# ============================================================
# Campaign helpers
# ============================================================
def get_or_create_campaign(
    base: str, key: str, slug: str, name: str, channel: str = "email"
) -> str:
    existing = sb_get(base, key, f"campaigns?slug=eq.{slug}&select=id&limit=1")
    if existing:
        return existing[0]["id"]
    created = sb_post(
        base, key, "campaigns",
        [{
            "project_id": PROJECT_ID,
            "slug":       slug,
            "name":       name,
            "channel":    channel,
            "status":     "active",
            "metadata":   {"created_by": "outreach_email.py"},
        }],
    )
    return created[0]["id"]


# ============================================================
# Main
# ============================================================
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--segment",       required=True,
                    help="segment slug to send to (e.g. private-chef)")
    ap.add_argument("--campaign-name",
                    help="Friendly campaign name. Auto-derived if omitted.")
    ap.add_argument("--limit",         type=int, default=10,
                    help="Max contacts to process this run (default 10)")
    ap.add_argument("--send",          action="store_true",
                    help="Actually send emails. WITHOUT this, dry-run only.")
    ap.add_argument("--test-self",     action="store_true",
                    help="Redirect all emails to your own GMAIL_USER. Useful for QA.")
    ap.add_argument("--skip-no-name",  action="store_true",
                    help="Skip contacts where first_name is null")
    ap.add_argument("--preview-to-file",
                    help="Generate all emails to this markdown file for review (no sends, no DB writes)")
    ap.add_argument("--pace",          type=int, default=30,
                    help="Seconds to sleep between sends (default 30 — gentle pacing for Gmail)")
    ap.add_argument("--no-tracking",   action="store_true",
                    help="Skip tracking pixel + link rewriting (use for warmup or plain test sends)")
    args = ap.parse_args()

    env = load_env()
    base        = env["SUPABASE_URL"]
    key         = env["SUPABASE_SERVICE_ROLE_KEY"]
    anthropic_k = env.get("ANTHROPIC_API_KEY", "")
    gmail_user  = env.get("GMAIL_USER", "")
    gmail_pass  = env.get("GMAIL_APP_PASSWORD", "")
    cristian_nm = env.get("CRISTIAN_NAME", "Cristian Orihuela")
    property_nm = env.get("CRISTIAN_PROPERTY_NAME", "Sun & Moon at 30A")

    if args.send and not (gmail_user and gmail_pass):
        print("❌ GMAIL_USER and GMAIL_APP_PASSWORD must be set in .env to --send")
        return 1
    if not anthropic_k:
        print("❌ ANTHROPIC_API_KEY must be set in .env (used to personalize each email)")
        return 1

    voice_samples = load_voice_samples()
    if voice_samples.count("(PLACEHOLDER") > 0:
        print("⚠ outreach_voice_samples.md still has placeholders. Generation will fall back to default tone.")

    # 1. Fetch audience
    print(f"\nFetching ready-to-contact contacts in segment '{args.segment}' ...")
    contacts = sb_get(
        base, key,
        f"v_outreach_ready?segment_slug=eq.{args.segment}&limit={args.limit}"
    )
    if not contacts:
        print(f"  → 0 contacts ready in {args.segment}.")
        print(f"    (Make sure schema/005_outreach_views.sql is applied,")
        print(f"     and that contacts in this segment have emails.)")
        return 0
    print(f"  → {len(contacts)} contacts queued")

    if not args.send:
        print("\n*** DRY-RUN MODE *** — no emails sent, no DB writes.")
    elif args.test_self:
        print(f"\n*** TEST-SELF MODE *** — every email will be sent TO {gmail_user}.")
    else:
        print(f"\n*** LIVE SEND MODE *** — emails go to REAL recipients.")
        confirm = input(f"  Type the segment slug ('{args.segment}') to confirm: ").strip()
        if confirm != args.segment:
            print("  Confirmation didn't match. Aborting.")
            return 1

    if args.send and len(contacts) > DAILY_SAFETY_CAP:
        print(f"❌ batch size {len(contacts)} exceeds safety cap of {DAILY_SAFETY_CAP}. Lower --limit.")
        return 1

    # 2. Resolve campaign
    campaign_slug = f"{args.segment}-email-{datetime.now().strftime('%Y%m')}"
    campaign_name = args.campaign_name or f"{args.segment} email — {datetime.now().strftime('%b %Y')}"
    campaign_id   = None
    if args.send:
        campaign_id = get_or_create_campaign(base, key, campaign_slug, campaign_name)
        print(f"  Campaign: {campaign_name} ({campaign_id})")

    # 3. Iterate
    stats = {"generated": 0, "sent": 0, "failed": 0, "skipped": 0}
    preview_rows: list[dict] = []   # for --preview-to-file mode

    for i, contact in enumerate(contacts, 1):
        first = contact.get("first_name")
        if args.skip_no_name and not first:
            print(f"  [{i}/{len(contacts)}] SKIP (no first_name): {contact['company_name']}")
            stats["skipped"] += 1
            continue

        # 3a. Generate personalized email
        try:
            subject, body = generate_email(
                anthropic_k, contact, cristian_nm, property_nm, voice_samples,
            )
            stats["generated"] += 1
        except Exception as ex:  # noqa: BLE001
            print(f"  [{i}/{len(contacts)}] ❌ generate failed for {contact['company_name']}: {ex}")
            stats["failed"] += 1
            continue

        # Accumulate for preview-to-file
        if args.preview_to_file:
            preview_rows.append({
                "i":       i,
                "contact": contact,
                "subject": subject,
                "body":    body,
            })
            # also print a one-liner for live feedback
            print(f"  [{i}/{len(contacts)}] {contact['company_name'][:35]:35s} → \"{subject[:50]}\"")
            continue

        # 3b. Preview
        recipient_label = (
            f"{contact['email']} ({first or 'no-name'} @ {contact['company_name']})"
        )
        if args.test_self:
            actual_to = gmail_user
            label_extra = f"  [TEST → {gmail_user}]"
        else:
            actual_to = contact["email"]
            label_extra = ""
        print(f"\n  [{i}/{len(contacts)}] → {recipient_label}{label_extra}")
        print(f"      Subject: {subject}")
        for line in body.splitlines()[:3]:
            print(f"      | {line}")
        if len(body.splitlines()) > 3:
            print(f"      | ... ({len(body.splitlines())} lines total)")

        # 3c. Send + log
        if not args.send:
            continue

        # 3c.i. Pre-insert outreach row to get an id we can use in the pixel
        try:
            pre = sb_post(base, key, "outreach", [{
                "project_id":          PROJECT_ID,
                "campaign_id":         campaign_id,
                "contact_id":          contact["contact_id"],
                "company_id":          contact["company_id"],
                "channel":             "email",
                "sequence_step":       1,
                "subject":             subject,
                "body":                body,                  # pre-tracking body, gets overwritten below
                "status":              "pending",
                "metadata": {
                    "to_email":   actual_to,
                    "test_self":  args.test_self,
                    "model":      ANTHROPIC_MODEL,
                },
            }])
            outreach_id = pre[0]["id"]
        except Exception as ex:  # noqa: BLE001
            print(f"      ❌ pre-insert outreach failed: {ex}")
            stats["failed"] += 1
            continue

        # 3c.ii. Build text + html bodies with tracking
        #   Plain text body: keep original URLs visible (untouched).
        #   HTML body: link TEXT shows the clean original; href points to the tracker.
        text_body = body
        link_map: dict[str, str] = {}
        pixel_url = None

        if not args.no_tracking:
            # Make sure the property URL is in the body so we have something to track
            if "sunandmoon30a.com" not in text_body:
                text_body = text_body.rstrip() + f"\n\nMore on the houses: {PROPERTY_URL}"

            def create_link(orig_url: str) -> str:
                _, tracked = create_tracking_link(
                    base, key,
                    destination_url=orig_url,
                    contact_id=contact["contact_id"],
                    company_id=contact["company_id"],
                    campaign_id=campaign_id,
                    utm_campaign=args.segment,
                )
                return tracked

            link_map  = collect_tracked_links(text_body, create_link)
            pixel_url = f"{TRACKING_DOMAIN}/o/{outreach_id}"

        # Embed the property photo inline if it exists on disk
        photo_cid = PROPERTY_PHOTO_CID if os.path.exists(PROPERTY_PHOTO_PATH) else None
        html_body = html_from_text(
            text_body, pixel_url or "about:blank", link_map,
            embed_photo_cid=photo_cid,
        )
        # text_body remains as-is — original URLs intact, no ugly track domain visible
        tracked_body = text_body

        # 3c.iii. Send
        try:
            msg_id = send_via_gmail(
                smtp_user=gmail_user,
                smtp_pass=gmail_pass,
                from_display_name=cristian_nm,
                to_email=actual_to,
                subject=subject,
                text_body=tracked_body,
                html_body=html_body,
                reply_to=gmail_user,
                inline_image_path=PROPERTY_PHOTO_PATH if photo_cid else None,
                inline_image_cid=photo_cid,
            )
        except Exception as ex:  # noqa: BLE001
            print(f"      ❌ SMTP failed: {ex}")
            # Mark outreach as failed
            try:
                sb_patch(base, key, f"outreach?id=eq.{outreach_id}",
                         {"status": "failed", "metadata": {"error": str(ex)[:300]}})
            except Exception:  # noqa: BLE001
                pass
            stats["failed"] += 1
            continue

        # 3c.iv. Mark outreach row as sent + persist the tracked body
        try:
            sb_patch(base, key, f"outreach?id=eq.{outreach_id}", {
                "status":              "sent",
                "sent_at":             datetime.now(timezone.utc).isoformat(),
                "body":                tracked_body,
                "provider_message_id": msg_id,
            })
        except Exception as ex:  # noqa: BLE001
            print(f"      ⚠ outreach update failed (email was sent though): {ex}")

        # Advance company_segments status: prospect → contacted (only in live mode, not test-self)
        if not args.test_self:
            try:
                sb_patch(
                    base, key,
                    f"company_segments?company_id=eq.{contact['company_id']}&segment_id=in.(select id from segments where slug='{args.segment}')",
                    {"status": "contacted", "first_contacted_at": datetime.now(timezone.utc).isoformat()},
                )
            except Exception as ex:  # noqa: BLE001
                # Not fatal — the outreach row is logged with sent_at, so status can be backfilled
                print(f"      ⚠ status patch failed (recoverable): {ex}")

        stats["sent"] += 1
        print(f"      ✓ sent  (msg-id: {msg_id})")
        # Pacing — gentle for Gmail, prevents burst-pattern flags
        if i < len(contacts):
            time.sleep(args.pace)

    # 4. Summary
    print()
    print("=" * 70)
    print("OUTREACH RUN SUMMARY")
    print("=" * 70)
    print(f"Segment:           {args.segment}")
    print(f"Mode:              {'LIVE' if args.send and not args.test_self else ('TEST-SELF' if args.test_self else 'DRY-RUN')}")
    print(f"Contacts queued:   {len(contacts)}")
    print(f"Emails generated:  {stats['generated']}")
    print(f"Emails sent:       {stats['sent']}")
    print(f"Failed:            {stats['failed']}")
    print(f"Skipped:           {stats['skipped']}")
    if args.send and stats["sent"] > 0:
        print(f"\nLogged in `outreach` table with campaign_id={campaign_id}")
        if not args.test_self:
            print(f"`company_segments.status` advanced: prospect → contacted")

    # 5. Write the preview file if requested
    if args.preview_to_file and preview_rows:
        with open(args.preview_to_file, "w") as f:
            f.write(f"# Email preview — segment `{args.segment}`\n\n")
            f.write(f"Generated {len(preview_rows)} emails on "
                    f"{datetime.now().strftime('%Y-%m-%d %H:%M')}.  "
                    f"No emails were sent. No DB writes.\n\n")
            f.write(f"**Voice samples loaded from:** `{VOICE_SAMPLE_PATH}` "
                    f"({'PLACEHOLDERS PRESENT' if voice_samples.count('(PLACEHOLDER') > 0 else 'real voice in use'})\n\n")
            f.write("---\n\n")
            for p in preview_rows:
                c = p["contact"]
                f.write(f"## {p['i']}. {c['company_name']}\n\n")
                f.write(f"**Recipient:** `{c['email']}`")
                if c.get("first_name"):
                    f.write(f" · first name **{c['first_name']}**")
                f.write("\n")
                f.write(f"**Segment:** {c['segment_slug']} · "
                        f"tier {c['tier']} · "
                        f"{c.get('city') or '?'}, {c.get('state') or '?'} · "
                        f"market_role={c.get('market_role') or '?'} ({c.get('market_metro') or '?'})\n")
                if c.get("rating") and c.get("reviews"):
                    f.write(f"**Signal:** {c['rating']}★ ({c['reviews']} reviews)\n")
                if c.get("editorial_summary"):
                    f.write(f"**Google says:** \"{c['editorial_summary']}\"\n")
                f.write(f"**Website:** {c.get('website') or '—'}\n\n")
                f.write(f"**Subject:** {p['subject']}\n\n")
                f.write("```\n")
                f.write(p["body"])
                f.write("\n```\n\n---\n\n")
        print(f"\n✓ Preview written to: {args.preview_to_file}")
        print(f"  Open it: open {args.preview_to_file}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
