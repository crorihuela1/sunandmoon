#!/usr/bin/env python3
"""
RESTAURANT OUTREACH ENGINE — cold/warm email with a personalized pricing "taste".

We sell a pricing AI agent to restaurants: free 3 months, then $39.99/mo. The
first email doesn't pitch — it shows the owner 2 concrete pricing moves the agent
already found in THEIR menu, with a conservative $/yr estimate.

Two positionings (pick with --region)
-------------------------------------
  --region atlanta  →  "prix_cold": stranger selling a SaaS. From a product inbox.
  --region 30a      →  "30a_local": Cristian as a 30A NEIGHBOR who runs Sun & Moon,
                       already sends guests to these restaurants, and built the tool
                       for fellow 30A spots. Sends as experience@sunandmoon30a.com.

Mirrors outreach_email.py conventions (same .env loader, Supabase REST, dry-run
default, Gmail send). Personalization comes from pricing_agent.py.

Modes
-----
    python3 outreach_restaurants.py --region 30a                     # SAMPLE, dry-run
    python3 outreach_restaurants.py --region 30a --from-db --limit 10
    python3 outreach_restaurants.py --region 30a --from-db --send --test-self \\
            --cc marianaorihuela@gmail.com
    python3 outreach_restaurants.py --region 30a --from-db --send \\
            --cc marianaorihuela@gmail.com --limit 10

Safety: default DRY-RUN. --send required. --test-self redirects to GMAIL_USER.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

import pricing_agent as pa

HTTP_TIMEOUT = 30

# ------------------------------------------------------------------
# Positionings — product + voice + sender identity in one place.
# ------------------------------------------------------------------
POSITIONINGS = {
    "prix_cold": {
        "style": "cold",
        "product_name": "Prix",
        "tagline": "a pricing AI agent for independent restaurants",
        "free_months": 3, "price_monthly": 39.99,
        "sender_name": "Cristian Orihuela",
        "signoff": "Cristian Orihuela",
        "sender_email": "cristian@getprix.ai",     # placeholder — set real
        "signature": "Prix · cristian@getprix.ai",
        "footer": "Prix · [your business mailing address] · "
                  "Not interested? Reply \"unsubscribe\" and we'll remove you.",
    },
    "30a_local": {
        "style": "local",
        "product_name": "Prix",                     # the tool's name (configurable)
        "tagline": "a pricing tool for fellow 30A restaurants",
        "free_months": 3, "price_monthly": 39.99,
        "sender_name": "Cristian Orihuela",
        "signoff": "Cristian & Mariana",
        "sender_email": "experience@sunandmoon30a.com",
        "signature": "Sun & Moon at 30A · experience@sunandmoon30a.com",
        # CAN-SPAM: real commercial email needs a physical address + opt-out.
        # Confirm/replace the address below before a volume send.
        "footer": "Sun & Moon at 30A · 53 Crystal Court, Seagrove Beach, FL 32459 · "
                  "Not the right fit? Just reply \"unsubscribe\" and we'll take you off the list.",
    },
}

REGIONS = {
    "atlanta": {"project_slug": "atlanta-restaurants-b2b", "positioning": "prix_cold", "market": "Atlanta"},
    "30a":     {"project_slug": "30a-restaurants",         "positioning": "30a_local", "market": "30A"},
}


# ==================================================================
# Email generation
# ==================================================================
def _dollars(n: int) -> str:
    return f"${n:,}"


def _round_money(n: int) -> int:
    if n <= 0:
        return 0
    step = 500 if n < 20000 else 1000
    return int(round(n / step) * step)


def generate_email(r: dict, analysis: pa.Analysis, pos: dict) -> dict:
    """Return {subject, body_text}. Warm, brief, real-person — voice = Cristian."""
    name = r["name"]
    fm, price = pos["free_months"], pos["price_monthly"]
    prod = pos["product_name"]
    top = analysis.top(2)
    shown_upside = _round_money(sum(rec.annual_upside for rec in top))
    upside = _dollars(shown_upside)
    local = pos["style"] == "local"

    # ---- subject ----
    if analysis.has_menu and shown_upside >= 2000:
        subject = (f"found ~{upside}/yr in {name}'s menu (from a 30A neighbor)"
                   if local else f"found ~{upside}/yr in {name}'s menu pricing")
    else:
        subject = (f"a quick menu note from a 30A neighbor" if local
                   else f"a quick pricing note on {name}")

    L = ["Hi there,", ""]

    # ---- opener (positioning) ----
    if local:
        L.append(
            f"We're Cristian and Mariana — a brother and sister who spend a good "
            f"chunk of the year on 30A. We run Sun & Moon (two little rental houses "
            f"in Seagrove), we love championing the small businesses and restaurants "
            f"down here, and we're pretty obsessed with great food and good company. "
            f"That's why we built {prod}, {pos['tagline']} — and before we say "
            f"anything else, here's what it flagged on {name}'s menu:"
        )
    else:
        L.append(
            f"I'm {pos['sender_name']} — I build {prod}, {pos['tagline']}. Before I "
            f"say anything else, here's what it found when I pointed it at {name}:"
        )
    L.append("")

    # ---- the taste ----
    if analysis.has_menu:
        for idx, rec in enumerate(top):
            line = f"• {rec.headline}"
            # Annotate only the lead bullet with $ — avoids a templated look when
            # several recs hit the credibility cap; the total lands in the line below.
            if idx == 0 and rec.annual_upside:
                line += f"  (~{_dollars(rec.annual_upside)}/yr)"
            L.append(line)
        L.append("")
        if shown_upside >= 1500:
            L.append(
                f"Conservatively that's about {upside}/yr you're leaving on the "
                f"table — and that's just the two items that jumped out first."
            )
            L.append("")
    else:
        rec = top[0]
        L.append(f"• {rec.headline}")
        L.append(f"  {rec.evidence}")
        L.append("")
        L.append("That's just from your Google profile — connect your menu and the "
                 "agent shows you the exact items and prices to test.")
        L.append("")

    # ---- offer ----
    if local:
        L.append(
            f"It's free for your first {fm} months, then ${price:g}/month — and "
            f"since 30A's home for us, we're rolling it out to local spots first. "
            f"It works off your public menu, so there's nothing to install."
        )
        L.append("")
        L.append(
            f"Want the full breakdown for {name}? Just reply \"send it\" and we'll "
            f"get it over to you — and either way, keep doing what you do, we love "
            f"what y'all have built."
        )
    else:
        L.append(
            f"{prod} watches your menu and your competitors and sends you small, "
            f"testable pricing moves like these. It's free for your first {fm} "
            f"months, then ${price:g}/month — cancel anytime. No POS integration to "
            f"start; it works off your public menu."
        )
        L.append("")
        L.append(f"Want the full breakdown for {name}? Just reply \"send it\" and I will.")

    L.append("")
    L.append(f"— {pos.get('signoff', pos['sender_name'])}")
    L.append(f"  {pos['signature']}")
    if pos.get("footer"):
        L.append("")
        L.append(pos["footer"])
    return {"subject": subject, "body_text": "\n".join(L)}


# ==================================================================
# SAMPLE restaurants (named menus → concrete taste). Illustrative only.
# ==================================================================
def _sample_atlanta() -> list[dict]:
    return [
        {"name": "Banshee", "neighborhood": "East Atlanta", "price_tier": 2,
         "rating": 4.6, "review_count": 760, "website": "bansheeatl.com",
         "menu": {"item_count": 7, "items": [
            {"name": "Wood-Grilled Half Chicken", "price": 24.0, "section": "Entree"},
            {"name": "Berkshire Pork Chop", "price": 28.0, "section": "Entree"},
            {"name": "Cavatelli", "price": 18.0, "section": "Entree"},
            {"name": "Banshee Burger", "price": 15.0, "section": "Entree"},
            {"name": "Little Gem Salad", "price": 12.0, "section": "Starter"},
            {"name": "Crispy Potatoes", "price": 8.0, "section": "Side"},
            {"name": "Chocolate Tart", "price": 10.0, "section": "Dessert"}]}},
        {"name": "Nina & Rafi", "neighborhood": "Old Fourth Ward", "price_tier": 2,
         "rating": 4.4, "review_count": 930, "website": "ninaandrafi.com",
         "menu": {"item_count": 6, "items": [
            {"name": "Detroit Pepperoni Pie", "price": 19.0, "section": "Entree"},
            {"name": "Margherita Pie", "price": 17.0, "section": "Entree"},
            {"name": "Rigatoni Vodka", "price": 21.0, "section": "Entree"},
            {"name": "Chicken Parm", "price": 24.0, "section": "Entree"},
            {"name": "Caesar", "price": 13.0, "section": "Starter"},
            {"name": "Garlic Knots", "price": 9.0, "section": "Starter"}]}},
        {"name": "El Tesoro", "neighborhood": "Edgewood", "price_tier": 2,
         "rating": 4.6, "review_count": 540, "website": None},
    ]


def _sample_30a() -> list[dict]:
    return [
        {"name": "Stinky's Fish Camp", "neighborhood": "Santa Rosa Beach", "price_tier": 3,
         "rating": 4.5, "review_count": 3120, "website": "stinkysfishcamp.com",
         "menu": {"item_count": 7, "items": [
            {"name": "Whole Fried Snapper", "price": 34.0, "section": "Entree"},
            {"name": "Shrimp & Grits", "price": 26.0, "section": "Entree"},
            {"name": "Seafood Gumbo Bowl", "price": 18.0, "section": "Entree"},
            {"name": "Fish Tacos", "price": 19.0, "section": "Entree"},
            {"name": "Steamed Oysters (dozen)", "price": 22.0, "section": "Raw Bar"},
            {"name": "Hush Puppies", "price": 9.0, "section": "Starter"},
            {"name": "Key Lime Pie", "price": 11.0, "section": "Dessert"}]}},
        {"name": "Cowgirl Kitchen", "neighborhood": "Rosemary Beach", "price_tier": 2,
         "rating": 4.4, "review_count": 1180, "website": "cowgirlkitchen.com",
         "menu": {"item_count": 6, "items": [
            {"name": "Cowgirl Burger", "price": 16.0, "section": "Entree"},
            {"name": "Shrimp Tacos", "price": 18.0, "section": "Entree"},
            {"name": "Margherita Flatbread", "price": 15.0, "section": "Entree"},
            {"name": "Chicken Pesto Pasta", "price": 19.0, "section": "Entree"},
            {"name": "House Salad", "price": 11.0, "section": "Starter"},
            {"name": "Brisket Tacos", "price": 14.0, "section": "Entree"}]}},
        {"name": "Café Thirty-A", "neighborhood": "Seagrove Beach", "price_tier": 4,
         "rating": 4.6, "review_count": 1640, "website": "cafethirtya.com",
         "menu": {"item_count": 6, "items": [
            {"name": "Wood-Oven Filet", "price": 48.0, "section": "Entree"},
            {"name": "Pan-Seared Grouper", "price": 42.0, "section": "Entree"},
            {"name": "Maple Leaf Duck", "price": 39.0, "section": "Entree"},
            {"name": "Lobster Risotto", "price": 44.0, "section": "Entree"},
            {"name": "Crab Cake", "price": 24.0, "section": "Starter"},
            {"name": "Crème Brûlée", "price": 14.0, "section": "Dessert"}]}},
        {"name": "Local Catch Bar & Grill", "neighborhood": "Santa Rosa Beach", "price_tier": 2,
         "rating": 4.5, "review_count": 1990, "website": "localcatch30a.com",
         "menu": {"item_count": 6, "items": [
            {"name": "Grilled Mahi Plate", "price": 25.0, "section": "Entree"},
            {"name": "Fried Shrimp Basket", "price": 19.0, "section": "Entree"},
            {"name": "Catch Burger", "price": 15.0, "section": "Entree"},
            {"name": "Blackened Fish Tacos", "price": 17.0, "section": "Entree"},
            {"name": "Gumbo Cup", "price": 8.0, "section": "Starter"},
            {"name": "Bread Pudding", "price": 9.0, "section": "Dessert"}]}},
        {"name": "Chiringo", "neighborhood": "Grayton Beach", "price_tier": 2,
         "rating": 4.3, "review_count": 1450, "website": "chiringo30a.com",
         "menu": {"item_count": 5, "items": [
            {"name": "Baja Fish Tacos", "price": 18.0, "section": "Entree"},
            {"name": "Grayton Burger", "price": 16.0, "section": "Entree"},
            {"name": "Jerk Chicken Bowl", "price": 17.0, "section": "Entree"},
            {"name": "Shrimp Po'Boy", "price": 16.0, "section": "Entree"},
            {"name": "Chips & Queso", "price": 10.0, "section": "Starter"}]}},
        {"name": "La Cocina 30A", "neighborhood": "Seagrove Beach", "price_tier": 2,
         "rating": 4.7, "review_count": 410, "website": None},   # no website → tier taste
    ]


# ==================================================================
# Supabase REST
# ==================================================================
def load_env(path: str = ".env") -> dict[str, str]:
    env = {}
    for ln in open(path):
        ln = ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            k, _, v = ln.partition("="); env[k.strip()] = v.strip()
    return env


def sb_get(base, key, path):
    req = urllib.request.Request(f"{base.rstrip('/')}/rest/v1/{path}",
        headers={"apikey": key, "Authorization": f"Bearer {key}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read())


def load_from_db(base, key, project_slug, limit):
    try:
        from scrape_restaurant_menus import is_excluded
    except Exception:  # noqa: BLE001
        def is_excluded(_n): return False
    try:
        # over-fetch, then drop chains/groceries/hotels, then take `limit`
        rows = sb_get(base, key,
            "v_restaurants?select=company_id,name,price_tier,rating,review_count,website,email"
            f"&project_slug=eq.{project_slug}&order=review_count.desc.nullslast&limit={limit*3}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise SystemExit(
                "❌ View v_restaurants not found (HTTP 404).\n"
                "   Apply the generic views first:\n"
                "   python3 apply_migrations.py schema/009_restaurant_views_generic.sql")
        raise
    rows = [r for r in rows if not is_excluded(r.get("name", ""))][:limit]
    for r in rows:
        items = sb_get(base, key,
            "v_restaurant_menu_pricing?select=item_name,item_price,section,item_count"
            f"&project_slug=eq.{project_slug}&company_id=eq.{r['company_id']}")
        if items:
            r["menu"] = {"item_count": items[0].get("item_count"),
                         "items": [{"name": i["item_name"], "price": i["item_price"],
                                    "section": i.get("section")} for i in items if i.get("item_price")]}
    return rows


# ==================================================================
# Rendering
# ==================================================================
def render_markdown(emails, pos, region, is_sample):
    out = [f"# Restaurant outreach — {region} reachouts", "",
           f"Positioning: **{pos['style']}** · sends as **{pos['sender_email']}** · "
           f"free {pos['free_months']} mo, then ${pos['price_monthly']:g}/mo", ""]
    if is_sample:
        out += ["> **SAMPLE** — illustrative restaurants/menus; pricing taste computed live by "
                "`pricing_agent.py`. Use `--from-db` for real data.", ""]
    out.append("---\n")
    for i, e in enumerate(emails, 1):
        a = e["_analysis"]
        meta = f"{'$'*e['_tier']} · {e['_rating']}★ ({e['_reviews']} reviews)"
        if e.get("_neighborhood"):
            meta += f" · {e['_neighborhood']}"
        meta += " · " + ("menu read" if a.has_menu else "no website — tier taste")
        out += [f"## {i}. {e['_name']}", f"_{meta}_"]
        if a.has_menu and a.total_upside:
            shown = _round_money(sum(x.annual_upside for x in a.top(2)))
            out.append(f"_Agent found ~${shown:,}/yr in the two items shown._")
        out += ["", f"**Subject:** {e['subject']}", "",
                "> " + e["body_text"].replace("\n", "\n> "), "\n---\n"]
    return "\n".join(out)


def render_html(emails, pos, region, is_sample):
    cards = []
    for i, e in enumerate(emails, 1):
        a = e["_analysis"]
        body = html.escape(e["body_text"]).replace("\n", "<br>")
        shown = _round_money(sum(x.annual_upside for x in a.top(2))) if a.has_menu else 0
        up = (f'<span class="up">~${shown:,}/yr found</span>' if shown
              else '<span class="muted">tier-level taste</span>')
        cards.append(f"""
        <div class="card"><div class="head">
          <div><b>{i}. {html.escape(e['_name'])}</b>
            <span class="muted">{'$'*e['_tier']} · {e['_rating']}★ · {e['_reviews']} reviews
            {('· '+html.escape(e['_neighborhood'])) if e.get('_neighborhood') else ''}</span></div>
          {up}</div>
          <div class="subj"><span class="lbl">Subject</span> {html.escape(e['subject'])}</div>
          <div class="body">{body}</div></div>""")
    banner = ('<div class="banner"><b>SAMPLE</b> — illustrative restaurants/menus; pricing taste '
              'is live from <code>pricing_agent.py</code>. Use <code>--from-db</code> for real data.</div>'
              ) if is_sample else ""
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{region} reachouts</title><style>
 body{{margin:0;font:15px/1.55 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#1b1b1b;background:#faf8f3;}}
 .wrap{{max-width:760px;margin:0 auto;padding:26px 20px 60px;}}
 h1{{font-size:23px;margin:0 0 2px;}} .sub{{color:#6b675e;margin:0 0 16px;}}
 .banner{{background:#fff7ed;border:1px solid #fed7aa;color:#7c2d12;padding:9px 13px;border-radius:10px;font-size:13px;margin-bottom:18px;}}
 .banner code{{background:#ffedd5;padding:1px 5px;border-radius:4px;}}
 .card{{background:#fff;border:1px solid #e7e3da;border-radius:13px;margin-bottom:18px;overflow:hidden;}}
 .head{{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:13px 16px;background:#f3efe6;border-bottom:1px solid #e7e3da;}}
 .muted{{color:#8a857b;font-size:12.5px;font-weight:400;}}
 .up{{background:#dcfce7;color:#166534;font-weight:700;font-size:12.5px;padding:3px 9px;border-radius:20px;white-space:nowrap;}}
 .subj{{padding:11px 16px 0;}} .lbl{{display:inline-block;background:#eee9df;color:#6b675e;font-size:11px;text-transform:uppercase;letter-spacing:.04em;padding:1px 6px;border-radius:4px;margin-right:6px;}}
 .body{{padding:12px 16px 18px;color:#2c2924;font-size:14.5px;}} code{{background:#f0ece2;padding:1px 5px;border-radius:4px;}}
</style></head><body><div class="wrap">
  <h1>🍽️ {region} reachouts</h1>
  <p class="sub">{pos['style']} positioning · from {pos['sender_email']} · free {pos['free_months']} mo,
   then ${pos['price_monthly']:g}/mo · {datetime.now():%b %d, %Y}</p>
  {banner}{''.join(cards)}
</div></body></html>"""


# ==================================================================
# Send-prep helpers — suppression, dedupe log, reviewable queue
# ==================================================================
def load_suppression(suppress_file: str, log_file: str) -> set:
    """Emails we must NOT send to: explicit opt-outs + anyone already emailed."""
    blocked = set()
    if os.path.exists(suppress_file):
        for ln in open(suppress_file):
            e = ln.strip().lower()
            if e and not e.startswith("#"):
                blocked.add(e)
    if os.path.exists(log_file):
        try:
            with open(log_file, newline="") as f:
                for row in csv.DictReader(f):
                    if row.get("email"):
                        blocked.add(row["email"].strip().lower())
        except Exception:  # noqa: BLE001
            pass
    return blocked


def append_sent_log(log_file: str, rows: list[dict]) -> None:
    new = not os.path.exists(log_file)
    with open(log_file, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["sent_at", "project", "restaurant", "email", "subject"])
        if new:
            w.writeheader()
        for r in rows:
            w.writerow(r)


def write_queue_csv(path: str, rows: list[dict]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["status", "to_email", "restaurant",
                                          "est_upside", "subject", "body"])
        w.writeheader()
        for r in rows:
            w.writerow(r)


# ==================================================================
# Gmail send — From = positioning sender (e.g. experience@), auth = GMAIL_USER
# ==================================================================
def send_via_gmail(env, to_addr, subject, body_text, pos, cc_addrs=None):
    import smtplib
    from email.mime.text import MIMEText
    from email.utils import formataddr
    user = env["GMAIL_USER"]; pw = env["GMAIL_APP_PASSWORD"]
    from_addr = pos["sender_email"]
    cc_addrs = [c.strip() for c in (cc_addrs or []) if c.strip()]
    msg = MIMEText(body_text, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = formataddr((pos["sender_name"], from_addr))
    msg["To"] = to_addr
    msg["Reply-To"] = from_addr
    if cc_addrs:
        msg["Cc"] = ", ".join(cc_addrs)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(user, pw)
        s.send_message(msg, from_addr=user, to_addrs=[to_addr] + cc_addrs)


# ==================================================================
# Main
# ==================================================================
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", choices=list(REGIONS), default="30a",
                    help="Which market/positioning (default: 30a).")
    ap.add_argument("--from-db", action="store_true")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--send", action="store_true", help="Actually send (default: dry-run).")
    ap.add_argument("--test-self", action="store_true", help="Redirect all sends to GMAIL_USER.")
    ap.add_argument("--cc", default="", help="Comma-separated CC addresses.")
    ap.add_argument("--yes", action="store_true", help="Skip the confirm prompt for a real send.")
    ap.add_argument("--suppress-file", default="", help="Opt-out emails, one per line.")
    ap.add_argument("--log-file", default="", help="CSV of who's been emailed (dedupe).")
    ap.add_argument("--queue-file", default="", help="Where to write the reviewable send queue.")
    ap.add_argument("--out-md", default="")
    ap.add_argument("--out-html", default="")
    args = ap.parse_args()

    region_cfg = REGIONS[args.region]
    pos = POSITIONINGS[region_cfg["positioning"]]
    is_sample = not args.from_db

    if args.from_db:
        env = load_env()
        base, key = env.get("SUPABASE_URL", ""), env.get("SUPABASE_SERVICE_ROLE_KEY", "")
        if not base or not key:
            print("❌ SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY empty in .env"); return 1
        restaurants = load_from_db(base, key, region_cfg["project_slug"], args.limit)
    else:
        restaurants = (_sample_30a() if args.region == "30a" else _sample_atlanta())[:args.limit]
    if not restaurants:
        print("No restaurants found."); return 0

    emails = []
    for r in restaurants:
        a = pa.analyze(r, market=region_cfg["market"])
        e = generate_email(r, a, pos)
        e.update({"_analysis": a, "_name": r["name"], "_tier": r.get("price_tier") or 2,
                  "_rating": r.get("rating") or "—", "_reviews": r.get("review_count") or 0,
                  "_neighborhood": r.get("neighborhood")})
        emails.append(e)

    out_md = args.out_md or f"sample_{args.region}_restaurant_reachouts.md"
    out_html = args.out_html or f"sample_{args.region}_restaurant_reachouts.html"
    with open(out_md, "w") as f:
        f.write(render_markdown(emails, pos, args.region, is_sample))
    with open(out_html, "w") as f:
        f.write(render_html(emails, pos, args.region, is_sample))
    print(f"Wrote {out_md} and {out_html}  ({len(emails)} emails, region={args.region})\n")

    # ---- build the send QUEUE (resolve recipient, apply suppression/dedupe) ----
    suppress_file = args.suppress_file or f"suppress_{args.region}.txt"
    log_file = args.log_file or f"sent_{args.region}_log.csv"
    queue_file = args.queue_file or f"send_queue_{args.region}.csv"
    blocked = load_suppression(suppress_file, log_file) if not args.test_self else set()

    env = load_env() if (args.send or args.from_db) else {}
    self_addr = env.get("GMAIL_USER", "you@yourself") if args.test_self else None

    queue = []   # rows we'd actually send
    for r, e in zip(restaurants, emails):
        real_email = (r.get("email") or "").strip()
        to = self_addr if args.test_self else real_email
        upside = _round_money(sum(x.annual_upside for x in e["_analysis"].top(2)))
        if not to:
            status = "skip:no-email"
        elif real_email.lower() in blocked:
            status = "skip:suppressed/already-sent"
        else:
            status = "ready"
        queue.append({"status": status, "to_email": to or "", "restaurant": e["_name"],
                      "est_upside": upside, "subject": e["subject"], "body": e["body_text"]})

    write_queue_csv(queue_file, queue)
    ready = [q for q in queue if q["status"] == "ready"]
    skipped = [q for q in queue if q["status"] != "ready"]
    print(f"Queue written → {queue_file}  ({len(ready)} ready, {len(skipped)} skipped)")
    for q in skipped[:8]:
        print(f"   ⏭  {q['restaurant'][:32]:32s} {q['status']}")

    print("\n" + "=" * 70)
    print(f"PREVIEW — email #1  (sends From: {pos['sender_email']})")
    print("=" * 70)
    print(f"To: {emails[0]['_name']}\nSubject: {emails[0]['subject']}\n")
    print(emails[0]["body_text"]); print("=" * 70)

    if not args.send:
        print(f"\nDRY-RUN. Nothing sent. Review {queue_file}, then add --send "
              f"(and --test-self to send to yourself first).")
        return 0

    if not env.get("GMAIL_USER") or not env.get("GMAIL_APP_PASSWORD"):
        print("❌ GMAIL_USER / GMAIL_APP_PASSWORD missing in .env"); return 1
    if not ready:
        print("Nothing ready to send (all skipped)."); return 0

    # ---- confirmation guard for a REAL send ----
    if not args.test_self and not args.yes:
        kind = f"{len(ready)} REAL emails as {pos['sender_email']}"
        try:
            ans = input(f"\n⚠️  About to send {kind}. Type 'send' to confirm: ").strip()
        except EOFError:
            ans = ""
        if ans.lower() != "send":
            print("Aborted. (Re-run with --yes to skip this prompt.)"); return 0

    cc_list = [c.strip() for c in args.cc.split(",") if c.strip()]
    sent, log_rows = 0, []
    for q in ready:
        try:
            send_via_gmail(env, q["to_email"], q["subject"], q["body"], pos, cc_addrs=cc_list)
            sent += 1
            cc_note = f"  (cc {', '.join(cc_list)})" if cc_list else ""
            print(f"  ✅ {q['to_email']}{cc_note}  ({q['restaurant']})")
            if not args.test_self:
                log_rows.append({"sent_at": datetime.now(timezone.utc).isoformat(),
                                 "project": region_cfg["project_slug"],
                                 "restaurant": q["restaurant"], "email": q["to_email"],
                                 "subject": q["subject"]})
        except Exception as ex:  # noqa: BLE001
            print(f"  ❌ {q['restaurant']}: {ex}")
    if log_rows:
        append_sent_log(log_file, log_rows)
        print(f"\nLogged {len(log_rows)} sends → {log_file} (skipped on future runs).")
    print(f"Sent {sent}/{len(ready)} as {pos['sender_email']}."
          + ("  (test-self: all went to you)" if args.test_self else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
