#!/usr/bin/env python3
"""
ENRICHMENT LAYER 1 — scrape partner websites for contact info.

Goal
----
We have 969 companies with websites + addresses but ZERO emails, ZERO social
handles. This script visits each website + a few common contact-y pages
(/contact, /about, /team) and extracts:

  • Emails           → companies.contacts (new rows)
  • Instagram handle → companies.instagram_handle
  • Facebook URL     → companies.facebook_url
  • LinkedIn URL     → companies.linkedin_url

Pure stdlib. Polite scraping (0.5s delay, 10s timeout, identifies itself
via User-Agent). Idempotent — safe to re-run; existing contacts not
duplicated, existing social URLs not overwritten.

Cost: $0 (no APIs).

Run it
------
    cd "/Users/samantha/Documents/Claude/Projects/Sun & Moon at 30a/referral-os"

    # Dry run — show what would be extracted, don't write
    python3 enrich_layer1_websites.py --dry-run --limit 10

    # Real run on a single segment (recommended first)
    python3 enrich_layer1_websites.py --segment private-chef

    # Full run on every company with a website (~880 sites, ~30 min)
    python3 enrich_layer1_websites.py

Flags
-----
    --segment <slug>   only enrich companies in this segment
    --limit N          stop after processing N companies (testing)
    --dry-run          extract and print, don't write to DB
    --skip-scraped     skip companies whose metadata.layer1_scraped_at is set
                       (default: true — set --no-skip to force re-scrape)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

# ============================================================
# Config
# ============================================================
PAGES_TO_TRY = ["", "/contact", "/contact-us", "/about", "/about-us", "/team"]

# Regex catalog
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
IG_RE    = re.compile(r"(?:instagram\.com|instagr\.am)/([A-Za-z0-9._]+)/?", re.I)
FB_RE    = re.compile(r"facebook\.com/(?!sharer|share|tr|plugins|dialog|events|pages)([A-Za-z0-9.\-_]+)/?", re.I)
LI_RE    = re.compile(r"linkedin\.com/(in|company)/([A-Za-z0-9\-_]+)/?", re.I)

# Skip these patterns — junk emails that aren't real outreach targets.
# Substring match (case-insensitive); anywhere in the email triggers a skip.
SKIP_EMAIL_PATTERNS = [
    # canonical no-reply / system addresses
    "noreply@", "no-reply@", "do-not-reply@", "donotreply@", "mailer-daemon",
    # template placeholders that show up on sites whose owner never replaced them
    "example@", "@example.com", "user@domain", "@domain.com",
    "yourname@", "you@", "your-email@", "youremail@",
    # site builder telemetry / Sentry tracking dummy addresses
    "sentry-next", ".sentry.", "sentry.io", "sentry.wix", "sentry@",
    "wix.com", "wixpress.com", "squarespace.com", "godaddy.com",
    "@cloudflare.", "track.", "tracking.",
    # image-path noise (some CSS URL strings parse as emails)
    "@2x.png", "@3x.png", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
    "u003c",  # html-encoded "<" sneaks into javascript blobs
]

# Department / role aliases — recognized as NOT a personal name
GENERIC_HANDLES = {
    "hello", "info", "contact", "events", "team", "inquiries", "inquiry",
    "booking", "bookings", "studio", "office", "admin", "hi", "hey",
    "weddings", "billing", "user", "sales", "support", "service",
    "concierge", "marketing", "press", "media", "general", "accounting",
    "accounts", "ar", "ap", "owner", "manager", "chef", "photo", "photos",
    "photography", "private", "main", "reservations", "reservation",
}

# Skip these IG handles — Instagram path artifacts, not real accounts
SKIP_IG = {"p", "reel", "reels", "tv", "explore", "about", "developer",
           "directory", "accounts", "stories", "share"}

# Skip these FB segments
SKIP_FB = {"events", "pages", "profile", "people", "groups", "marketplace",
           "watch", "gaming", "fundraisers", "policies", "help"}

USER_AGENT  = "ReferralOS Partner Discovery (contact: corihuela@gmail.com)"
HTTP_TIMEOUT = 10
DELAY        = 0.5    # between requests
MAX_BYTES    = 500_000

# ============================================================
# .env
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
# Supabase REST helpers
# ============================================================
def sb_headers(key: str) -> dict[str, str]:
    return {
        "apikey":        key,
        "Authorization": f"Bearer {key}",
        "Content-Type":  "application/json",
        "User-Agent":    "ReferralOS/1.0",
    }


def sb_get(base: str, key: str, path: str) -> list[dict]:
    req = urllib.request.Request(
        f"{base.rstrip('/')}/rest/v1/{path}",
        headers={**sb_headers(key), "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _is_transient(exc: Exception) -> bool:
    """Retry these. Don't retry permanent client errors (4xx)."""
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code >= 500          # 5xx server errors are transient
    if isinstance(exc, (TimeoutError, ConnectionError, urllib.error.URLError, OSError)):
        return True
    return False


def _with_retry(label: str, fn, tries: int = 4):
    """Run fn() with exponential backoff on transient errors. Re-raises on permanent."""
    delay = 1.0
    last_exc = None
    for attempt in range(1, tries + 1):
        try:
            return fn()
        except Exception as ex:  # noqa: BLE001
            last_exc = ex
            if not _is_transient(ex) or attempt == tries:
                raise
            print(f"      ⚠ {label} attempt {attempt}/{tries} failed ({type(ex).__name__}); retrying in {delay:.1f}s")
            time.sleep(delay)
            delay *= 2
    raise last_exc  # unreachable but keeps type checkers happy


def sb_patch(base: str, key: str, path: str, body: dict) -> list[dict]:
    def go():
        req = urllib.request.Request(
            f"{base.rstrip('/')}/rest/v1/{path}",
            data=json.dumps(body).encode(),
            method="PATCH",
            headers={**sb_headers(key), "Prefer": "return=representation"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    return _with_retry("sb_patch", go)


def sb_post(base: str, key: str, table: str, rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    def go():
        req = urllib.request.Request(
            f"{base.rstrip('/')}/rest/v1/{table}",
            data=json.dumps(rows).encode(),
            method="POST",
            headers={**sb_headers(key), "Prefer": "return=representation"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    return _with_retry("sb_post", go)


# ============================================================
# Scraping
# ============================================================
def fetch_page(url: str) -> str | None:
    """Fetch HTML; return None on any error."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            raw = r.read(MAX_BYTES)
            # try to detect encoding from headers; fall back to utf-8
            ctype = r.headers.get("Content-Type", "")
            charset = "utf-8"
            if "charset=" in ctype.lower():
                charset = ctype.lower().split("charset=", 1)[1].split(";", 1)[0].strip()
            try:
                return raw.decode(charset, errors="replace")
            except LookupError:
                return raw.decode("utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError,
            TimeoutError, ConnectionError, OSError):
        return None
    except Exception:  # noqa: BLE001
        return None


def is_keepable_email(email: str, domain: str | None) -> bool:
    el = email.lower()
    if any(skip in el for skip in SKIP_EMAIL_PATTERNS):
        return False
    if el.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")):
        return False
    # length sanity
    if len(el) > 100 or len(el) < 6:
        return False
    # tld sanity — must end in something plausible
    parts = el.rsplit("@", 1)
    if len(parts) != 2 or "." not in parts[1]:
        return False
    return True


def likely_first_name(local: str, company_domain: str | None) -> str | None:
    """
    Heuristic: is the email's local-part likely an actual first name?
    Returns capitalized name or None. Conservative — better to leave null
    than to populate with garbage like 'Cheftommyac' or 'Sharpscatering30a'.

    Rejects: digits, length outside 2-15, generic handles (info/hello/etc.),
    anything that contains/is contained by the company's domain base name.
    """
    if not local:
        return None
    # Take the part before the first separator (., _, -)
    first = re.split(r"[._\-]", local, 1)[0].lower()
    if not first or len(first) < 2 or len(first) > 15:
        return None
    if first in GENERIC_HANDLES:
        return None
    if any(ch.isdigit() for ch in first):
        return None
    # Reject if the candidate name overlaps the company domain base
    # (e.g. domain=sharpscatering30a.com, local=sharpscatering30a)
    if company_domain:
        domain_base = company_domain.split(".")[0].lower()
        if len(domain_base) >= 4 and (domain_base in first or first in domain_base):
            return None
    # Must contain at least one vowel — filters acronyms like "pbm"
    if not any(v in first for v in "aeiouy"):
        return None
    return first.capitalize()


def extract_signals(html: str, domain: str | None) -> dict:
    emails: set[str] = set()
    for m in EMAIL_RE.findall(html):
        if is_keepable_email(m, domain):
            emails.add(m.lower())

    igs: set[str] = set()
    for m in IG_RE.findall(html):
        handle = m.lower().strip("/")
        if handle and handle not in SKIP_IG and len(handle) <= 30:
            igs.add(handle)

    fbs: set[str] = set()
    for m in FB_RE.findall(html):
        handle = m.lower().strip("/")
        if handle and handle not in SKIP_FB and len(handle) <= 60:
            fbs.add(handle)

    lis: set[str] = set()
    for kind, handle in LI_RE.findall(html):
        handle = handle.lower().strip("/")
        if handle and len(handle) <= 60:
            lis.add(f"{kind.lower()}/{handle}")

    return {
        "emails":    sorted(emails),
        "instagram": sorted(igs),
        "facebook":  sorted(fbs),
        "linkedin":  sorted(lis),
    }


def scrape_company(website: str, domain: str | None) -> tuple[dict, list[str]]:
    """
    Returns (merged_signals, urls_fetched).
    Walks PAGES_TO_TRY in order, polite delay between requests.
    """
    if not website:
        return ({"emails": [], "instagram": [], "facebook": [], "linkedin": []}, [])

    # Normalize: ensure https:// prefix and strip trailing slash
    base = website.strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        base = "https://" + base

    fetched: list[str] = []
    agg = {"emails": set(), "instagram": set(), "facebook": set(), "linkedin": set()}

    for suffix in PAGES_TO_TRY:
        url = base + suffix
        html = fetch_page(url)
        if html is None:
            continue
        fetched.append(url)
        sig = extract_signals(html, domain)
        for k in agg:
            agg[k].update(sig[k])
        time.sleep(DELAY)

    return ({k: sorted(v) for k, v in agg.items()}, fetched)


# ============================================================
# Main
# ============================================================
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--segment",       help="only enrich companies in this segment slug")
    ap.add_argument("--limit",         type=int, help="stop after N companies (testing)")
    ap.add_argument("--dry-run",       action="store_true", help="don't write to DB")
    ap.add_argument("--no-skip",       action="store_true", help="re-scrape already-scraped companies")
    args = ap.parse_args()

    env = load_env()
    base = env["SUPABASE_URL"]
    key  = env["SUPABASE_SERVICE_ROLE_KEY"]

    # Build the GET path
    select = "id,name,website,domain,metadata,facebook_url,instagram_handle,linkedin_url"
    if args.segment:
        # Resolve segment id, then fetch via company_segments
        segs = sb_get(base, key, f"segments?slug=eq.{args.segment}&select=id")
        if not segs:
            print(f"❌ Segment '{args.segment}' not found")
            return 1
        seg_id = segs[0]["id"]
        path = f"company_segments?segment_id=eq.{seg_id}&select=company:companies({select})"
        raw = sb_get(base, key, path)
        companies = [r["company"] for r in raw if r.get("company")]
    else:
        # All companies with a website
        path = f"companies?website=not.is.null&select={select}&limit=2000"
        companies = sb_get(base, key, path)

    # Optionally skip already-scraped
    if not args.no_skip:
        before = len(companies)
        companies = [c for c in companies
                     if not (c.get("metadata") or {}).get("layer1_scraped_at")]
        print(f"Skipped {before - len(companies)} already-scraped companies")

    if args.limit:
        companies = companies[: args.limit]

    print(f"\nTargets: {len(companies)} companies")
    if args.dry_run:
        print("(DRY RUN — nothing will be written)\n")

    # ----- pre-fetch existing emails per company to dedup -----
    existing_emails: dict[str, set[str]] = {}
    if not args.dry_run and companies:
        ids = [c["id"] for c in companies]
        # chunked GET (in case of large id list)
        for i in range(0, len(ids), 100):
            chunk_ids = ids[i:i + 100]
            q = "(" + ",".join(chunk_ids) + ")"
            rows = sb_get(base, key, f"contacts?company_id=in.{q}&select=company_id,email")
            for r in rows:
                existing_emails.setdefault(r["company_id"], set()).add(
                    (r["email"] or "").lower()
                )

    # ----- iterate -----
    stats = {
        "scraped": 0, "with_email": 0, "with_ig": 0, "with_fb": 0, "with_li": 0,
        "emails_written": 0, "errors": 0, "no_signal": 0,
    }
    samples: list[tuple] = []  # for dry-run preview

    for i, c in enumerate(companies, 1):
        website = c.get("website")
        if not website:
            continue
        domain = c.get("domain")
        try:
            sig, urls = scrape_company(website, domain)
        except Exception as e:  # noqa: BLE001
            print(f"  [{i}/{len(companies)}] ERROR {c['name']}: {e}")
            stats["errors"] += 1
            continue

        stats["scraped"] += 1
        if sig["emails"]:    stats["with_email"] += 1
        if sig["instagram"]: stats["with_ig"]    += 1
        if sig["facebook"]:  stats["with_fb"]    += 1
        if sig["linkedin"]:  stats["with_li"]    += 1
        if not any(sig.values()):
            stats["no_signal"] += 1

        # progress line
        e_n = len(sig["emails"])
        i_n = len(sig["instagram"])
        f_n = len(sig["facebook"])
        l_n = len(sig["linkedin"])
        print(f"  [{i:>3}/{len(companies)}] {c['name'][:42]:42s}  "
              f"email:{e_n}  ig:{i_n}  fb:{f_n}  li:{l_n}  ({len(urls)} pages)")

        if args.dry_run:
            samples.append((c["name"], sig))
            continue

        # ---- write companies updates (only fill empty fields) ----
        company_patch = {}
        if sig["instagram"] and not c.get("instagram_handle"):
            company_patch["instagram_handle"] = sig["instagram"][0]
        if sig["facebook"] and not c.get("facebook_url"):
            company_patch["facebook_url"] = f"https://facebook.com/{sig['facebook'][0]}"
        if sig["linkedin"] and not c.get("linkedin_url"):
            company_patch["linkedin_url"] = f"https://linkedin.com/{sig['linkedin'][0]}"

        # always update the metadata.layer1_scraped_at marker
        new_meta = dict(c.get("metadata") or {})
        new_meta["layer1_scraped_at"] = datetime.now(timezone.utc).isoformat()
        new_meta["layer1_signal_counts"] = {
            "emails": e_n, "ig": i_n, "fb": f_n, "li": l_n,
        }
        company_patch["metadata"] = new_meta

        try:
            sb_patch(base, key, f"companies?id=eq.{c['id']}", company_patch)
        except urllib.error.HTTPError as he:
            print(f"      ❌ company patch HTTP {he.code}: {he.read().decode()[:200]}")
            stats["errors"] += 1
        except Exception as ex:  # noqa: BLE001 — never crash the whole run on one company
            print(f"      ❌ company patch EXCEPTION: {type(ex).__name__}: {ex}")
            stats["errors"] += 1

        # ---- insert contacts for new emails ----
        # Schema note: contacts.is_owner and is_decision_maker are NOT NULL
        # with default false — so we OMIT them from the payload (don't send
        # nulls). The DB defaults kick in. Same for data_sources (defaults []).
        already = existing_emails.get(c["id"], set())
        new_emails = [e for e in sig["emails"] if e not in already]
        if new_emails:
            contact_rows = []
            for em in new_emails:
                # Owner-name guess via likely_first_name() — conservative.
                local       = em.split("@", 1)[0]
                first_guess = likely_first_name(local, domain)

                # PostgREST PGRST102 requires identical key sets across batch rows,
                # so always include first_name (may be null).
                contact_rows.append({
                    "company_id":   c["id"],
                    "email":        em,
                    "first_name":   first_guess,   # may be None — that's OK
                    "data_sources": ["website_scrape"],
                    "notes":        f"Auto-extracted from {website}",
                })

            try:
                inserted = sb_post(base, key, "contacts", contact_rows)
                # Use server's actual row count if returned, fall back to len sent
                written = len(inserted) if isinstance(inserted, list) else len(contact_rows)
                stats["emails_written"] += written
            except urllib.error.HTTPError as he:
                body = he.read().decode()[:300]
                print(f"      ❌ contacts post HTTP {he.code}: {body}")
                stats["errors"] += 1
            except Exception as ex:  # noqa: BLE001
                print(f"      ❌ contacts post EXCEPTION: {type(ex).__name__}: {ex}")
                stats["errors"] += 1

    # ============================================================
    # Summary
    # ============================================================
    print()
    print("=" * 70)
    print("LAYER 1 RESULTS")
    print("=" * 70)
    print(f"Companies scraped:           {stats['scraped']}")
    print(f"  Yielded any signal:        {stats['scraped'] - stats['no_signal']}")
    print(f"  No signal:                 {stats['no_signal']}")
    print(f"  Errors:                    {stats['errors']}")
    print()
    print(f"By signal type:")
    print(f"  At least one email:        {stats['with_email']:4d} ({100*stats['with_email']/(stats['scraped'] or 1):.0f}%)")
    print(f"  Instagram handle:          {stats['with_ig']:4d} ({100*stats['with_ig']/(stats['scraped'] or 1):.0f}%)")
    print(f"  Facebook URL:              {stats['with_fb']:4d} ({100*stats['with_fb']/(stats['scraped'] or 1):.0f}%)")
    print(f"  LinkedIn URL:              {stats['with_li']:4d} ({100*stats['with_li']/(stats['scraped'] or 1):.0f}%)")
    print()
    print(f"Contacts written:            {stats['emails_written']}")

    if args.dry_run and samples:
        print()
        print("DRY-RUN SAMPLE (first 5):")
        for name, sig in samples[:5]:
            print(f"\n  • {name}")
            if sig["emails"]:    print(f"      emails:    {sig['emails']}")
            if sig["instagram"]: print(f"      instagram: {sig['instagram']}")
            if sig["facebook"]:  print(f"      facebook:  {sig['facebook']}")
            if sig["linkedin"]:  print(f"      linkedin:  {sig['linkedin']}")

    print()
    print("👉 Next steps:")
    print("   SELECT s.slug, COUNT(DISTINCT c.id) AS companies,")
    print("          COUNT(DISTINCT k.id) FILTER (WHERE k.email IS NOT NULL) AS with_email")
    print("   FROM companies c")
    print("   JOIN company_segments cs ON cs.company_id = c.id")
    print("   JOIN segments s ON s.id = cs.segment_id")
    print("   LEFT JOIN contacts k ON k.company_id = c.id")
    print("   GROUP BY s.slug ORDER BY s.priority_tier, s.slug;")
    return 0


if __name__ == "__main__":
    sys.exit(main())
