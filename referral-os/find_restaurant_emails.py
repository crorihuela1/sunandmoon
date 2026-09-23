#!/usr/bin/env python3
"""
FIND EMAILS — get a contact email for each Atlanta restaurant.

Google Places gives website + phone, never email. Cold email needs an address.
This fills companies.main_email using two cheap sources, in order:

  1. Website scrape (FREE) — fetch the homepage + an obvious /contact|/about page,
     pull mailto: links and visible addresses, and prefer a generic inbox
     (info@, hello@, eat@, contact@) over a personal one.
  2. Hunter domain-search (PAID, optional) — only if the scrape finds nothing and
     HUNTER_API_KEY is set. Hunter's free tier is ~25/mo, Starter $34/mo.

Writes the best email to companies.main_email and logs provenance to
enrichment_runs. Never invents an address; restaurants with none stay blank and
are reported so you can phone them instead.

Run it (locally — sandbox has no outbound web)
----------------------------------------------
    python3 find_restaurant_emails.py --limit 50
    python3 find_restaurant_emails.py --limit 50 --no-hunter   # scrape only, $0
    python3 find_restaurant_emails.py --dry-run                # show, don't write
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request

PROJECT_SLUG = "atlanta-restaurants-b2b"
HTTP_TIMEOUT = 15
DELAY_S = 1.0
UA = "Mozilla/5.0 (compatible; ReferralOS-EmailBot/1.0)"

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
GENERIC_LOCAL = ("info", "hello", "contact", "eat", "reservations", "hi",
                 "events", "catering", "office", "admin", "team")
JUNK_DOMAINS = ("example.com", "sentry.io", "wix.com", "squarespace.com",
                "godaddy.com", "wordpress.com", "yourdomain.com", "email.com",
                "domain.com", "schema.org", "fontawesome.com")
JUNK_LOCAL = ("noreply", "no-reply", "donotreply")
CONTACT_PATHS = ("", "contact", "contact-us", "about", "about-us", "reservations")


def load_env(path: str = ".env") -> dict[str, str]:
    env = {}
    for ln in open(path):
        ln = ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            k, _, v = ln.partition("="); env[k.strip()] = v.strip()
    return env


def sb_headers(k):
    return {"apikey": k, "Authorization": f"Bearer {k}",
            "Content-Type": "application/json", "User-Agent": "ReferralOS/1.0"}


def sb_get(base, k, path):
    req = urllib.request.Request(f"{base.rstrip('/')}/rest/v1/{path}",
        headers={**sb_headers(k), "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read())


def sb_patch(base, k, path, payload):
    req = urllib.request.Request(f"{base.rstrip('/')}/rest/v1/{path}",
        data=json.dumps(payload).encode(), method="PATCH",
        headers={**sb_headers(k), "Prefer": "return=minimal"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return r.status


def sb_insert(base, k, table, rows):
    if not rows:
        return
    req = urllib.request.Request(f"{base.rstrip('/')}/rest/v1/{table}",
        data=json.dumps(rows).encode(), method="POST",
        headers={**sb_headers(k), "Prefer": "return=minimal"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return r.status


def fetch(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            cs = r.headers.get_content_charset() or "utf-8"
            return r.read(1_500_000).decode(cs, "replace")
    except Exception:  # noqa: BLE001
        return None


def score_email(addr: str, site_domain: str | None) -> int:
    """Higher = better. Prefer generic inbox on the restaurant's own domain."""
    local, _, dom = addr.lower().partition("@")
    if any(j in dom for j in JUNK_DOMAINS) or any(j in local for j in JUNK_LOCAL):
        return -1
    s = 0
    if site_domain and site_domain in dom:
        s += 5                       # on their own domain = real
    if local in GENERIC_LOCAL:
        s += 3                       # generic inbox = safe for cold outreach
    if "@gmail.com" in addr or "@yahoo.com" in addr:
        s += 1                       # personal but still a real contact
    return s


def find_on_website(website: str) -> tuple[str | None, str]:
    """Return (best_email, source_url) by scraping a few pages."""
    parsed = urllib.parse.urlparse(website if "://" in website else "https://" + website)
    base = f"{parsed.scheme or 'https'}://{parsed.netloc or parsed.path}"
    site_domain = (parsed.netloc or parsed.path).lower().removeprefix("www.")
    found: dict[str, int] = {}
    used = ""
    for path in CONTACT_PATHS:
        url = urllib.parse.urljoin(base + "/", path)
        html = fetch(url)
        if not html:
            continue
        used = used or url
        for m in EMAIL_RE.findall(html):
            sc = score_email(m, site_domain)
            if sc >= 0:
                found[m.lower()] = max(found.get(m.lower(), -1), sc)
        if any(v >= 5 for v in found.values()):   # got a strong hit; stop early
            break
        time.sleep(0.4)
    if not found:
        return None, used
    best = max(found.items(), key=lambda kv: kv[1])[0]
    return best, used


def hunter_domain_search(domain: str, api_key: str) -> str | None:
    try:
        url = ("https://api.hunter.io/v2/domain-search?"
               + urllib.parse.urlencode({"domain": domain, "api_key": api_key, "limit": 10}))
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            data = json.loads(r.read())
        emails = (data.get("data") or {}).get("emails") or []
        if not emails:
            return None
        emails.sort(key=lambda e: (e.get("type") == "generic",
                                   e.get("confidence", 0)), reverse=True)
        return emails[0].get("value")
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--project", default=PROJECT_SLUG,
                    help="Project slug (e.g. 30a-restaurants or atlanta-restaurants-b2b).")
    ap.add_argument("--no-hunter", action="store_true", help="Scrape only, never call Hunter.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    env = load_env()
    base, key = env.get("SUPABASE_URL", ""), env.get("SUPABASE_SERVICE_ROLE_KEY", "")
    hunter = "" if args.no_hunter else env.get("HUNTER_API_KEY", "")
    if not base or not key:
        print("❌ SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY empty in .env"); return 1

    rows = sb_get(base, key,
        "companies?select=id,name,website,domain,main_email,"
        "company_segments!inner(segments!inner(projects!inner(slug)))"
        f"&company_segments.segments.projects.slug=eq.{args.project}"
        "&website=not.is.null&main_email=is.null")
    uniq = {r["id"]: r for r in rows}
    todo = list(uniq.values())[:args.limit]
    if not todo:
        print(f"No restaurants need an email for project '{args.project}' "
              f"(none loaded yet, all already have one, or none have websites).")
        print(f"If you haven't loaded data yet: python3 ingest_30a_restaurants.py")
        return 0
    print(f"Finding emails for {len(todo)} restaurants "
          f"({'scrape only' if not hunter else 'scrape → Hunter fallback'})\n")

    runs, got_web, got_hunter, blank = [], 0, 0, 0
    for i, r in enumerate(todo, 1):
        time.sleep(DELAY_S)
        email, src_url = find_on_website(r["website"])
        source = "website_scrape"
        if not email and hunter and r.get("domain"):
            email = hunter_domain_search(r["domain"], hunter)
            source = "hunter" if email else source
        tag = "✅" if email else "⬜"
        where = {"website_scrape": "web", "hunter": "hunter"}.get(source, "—")
        print(f"  {i:>3}/{len(todo)} {tag} {r['name'][:32]:32s} {email or '(none)'}"
              + (f"  [{where}]" if email else ""))

        if email and not args.dry_run:
            try:
                sb_patch(base, key, f"companies?id=eq.{r['id']}", {"main_email": email})
            except Exception as ex:  # noqa: BLE001
                print(f"      ⚠️ write failed: {ex}")
        if email:
            got_web += source == "website_scrape"
            got_hunter += source == "hunter"
        else:
            blank += 1
        runs.append({"entity_type": "company", "entity_id": r["id"], "source": source,
                     "operation": "find_email", "succeeded": bool(email),
                     "fields_updated": ["main_email"] if email else [],
                     "cost_usd": 0.034 if source == "hunter" else 0,
                     "response_payload": {"email": email, "src": src_url}})

    if not args.dry_run and runs:
        try:
            sb_insert(base, key, "enrichment_runs", runs)
        except Exception as ex:  # noqa: BLE001
            print(f"⚠️ enrichment_runs insert: {ex}")

    print(f"\nFound {got_web} by scrape, {got_hunter} by Hunter, {blank} still blank.")
    print("Blanks have a phone on file — call those. Next: outreach_restaurants.py --from-db")
    return 0


if __name__ == "__main__":
    sys.exit(main())
