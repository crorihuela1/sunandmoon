#!/usr/bin/env python3
"""
SCRAPE — real menu item prices, from wherever they live.

Google Places only gives a $-tier ($..$$$$), never actual prices. Real prices
live on the restaurant's site — but often as an IMAGE or PDF, and plenty of
spots have no usable site at all. So this is a SOURCE WATERFALL: for each
restaurant we try sources in order until we have enough priced items.

Sources (in order)
------------------
  1. website_text     HTML text of the homepage + a /menu page.            (free)
  2. website_pdf      menu PDFs linked from the site (pdfplumber).         (free)
  3. website_image    menu IMAGES on the site → OCR (Tesseract).           (free)
  4. places_photos    Google's menu PHOTOS for the place → OCR.   (Places Photo $)
  5. yelp             best-effort Yelp menu page.       (free, often blocked)

Restaurants with NO website are still processed (sources 4–5). Each result
records which source(s) produced it + a confidence level, and NEVER invents a
price — anything we can't parse stays blank with confidence 'none'.

Optional dependencies (the script degrades gracefully without them)
------------------------------------------------------------------
  OCR (sources 3,4):   brew install tesseract
                       pip3 install pytesseract pillow --break-system-packages
  PDF (source 2):      pip3 install pdfplumber --break-system-packages
If a dep is missing, that source is skipped and the script tells you.

Run it (locally — sandbox has no outbound web)
----------------------------------------------
    python3 scrape_restaurant_menus.py --project 30a-restaurants --limit 25
    python3 scrape_restaurant_menus.py --project 30a-restaurants --no-yelp
    python3 scrape_restaurant_menus.py --project 30a-restaurants --dry-run
    python3 scrape_restaurant_menus.py --project 30a-restaurants --only stinky --limit 1
"""
from __future__ import annotations

import argparse
import io
import json
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser

DEFAULT_PROJECT = "30a-restaurants"
HTTP_TIMEOUT = 20
DELAY_S = 1.2
MIN_GOOD_ITEMS = 10           # stop the waterfall once a source yields this many
MIN_STORE_ITEMS = 3           # fewer than this = noise, don't record a "menu"
MAX_IMAGES = 5
MAX_PLACES_PHOTOS = 6
UA = "Mozilla/5.0 (compatible; ReferralOS-MenuBot/1.0; +contact owner)"

# National chains, groceries, big-box, hotels, gas — NOT our independent targets.
# Skipped before scraping (and they shouldn't be outreach targets either).
EXCLUDE_PATTERNS = [
    "walmart", "publix", "target", "costco", "sam's club", "kroger", "winn-dixie",
    "whole foods", "trader joe", "cvs", "walgreens", "dollar general", "supermarket",
    "super market", "grocery", "mcdonald", "burger king", "wendy", "dunkin",
    "starbucks", "chick-fil-a", "chick fil a", "taco bell", "subway", "domino",
    "papa john", "pizza hut", "kfc", "popeyes", "chipotle", "panera", "arby",
    "sonic drive", "five guys", "jersey mike", "jimmy john", "firehouse subs",
    "waffle house", "ihop", "denny", "cracker barrel", "outback", "olive garden",
    "chili's", "applebee", "hilton", "marriott", "hyatt", "embassy suites",
    "holiday inn", "hampton inn", "courtyard", "residence inn", "resort &",
    "shell", "chevron", "exxon", "circle k", "racetrac", "tom thumb", "gas station",
    "convenience", "costco", "bp ", "murphy usa",
]

def is_excluded(name: str) -> bool:
    n = (name or "").lower()
    return any(p in n for p in EXCLUDE_PATTERNS)


def trim_outliers(items: list[dict]) -> list[dict]:
    """Drop implausible 'prices' (OCR/scrape noise: phone digits, SKUs, $360).
    Hard cap $250; relative cap at 5x the set median (min $120)."""
    if not items:
        return items
    import statistics
    prices = sorted(it["price"] for it in items)
    med = statistics.median(prices)
    ceiling = max(120.0, med * 5)
    return [it for it in items if 3 <= it["price"] <= min(250.0, ceiling)]

PRICE_RE = re.compile(r"\$?\s?(\d{1,3}(?:\.\d{2})?)\s?(?:dollars)?")
DOLLAR_PRICE_RE = re.compile(r"\$\s?(\d{1,3}(?:\.\d{2})?)")   # stricter: needs a $
MENU_LINK_RE = re.compile(r"\b(menu|menus|food|dinner|lunch|dineing|our-menu)\b", re.I)
MENU_IMG_RE = re.compile(r"menu|food|dinner|lunch|brunch", re.I)
SECTION_HINT_RE = re.compile(
    r"\b(appetizer|starter|small plate|salad|soup|entree|main|sandwich|burger|"
    r"pasta|pizza|side|dessert|brunch|breakfast|lunch|dinner|drink|cocktail|"
    r"wine|beer|raw bar|shareable|taco|seafood|handheld)\b", re.I)
CONTACT_PATHS = ("", "menu", "menus", "food", "dinner-menu", "our-menu", "lunch")


# ============================================================
# Capabilities — lazy, reported once
# ============================================================
def capabilities() -> dict:
    caps = {"ocr": False, "pdf": False}
    try:
        import pytesseract  # noqa: F401
        from PIL import Image  # noqa: F401
        # confirm the tesseract binary is actually installed
        import shutil
        caps["ocr"] = shutil.which("tesseract") is not None
    except Exception:  # noqa: BLE001
        caps["ocr"] = False
    try:
        import pdfplumber  # noqa: F401
        caps["pdf"] = True
    except Exception:  # noqa: BLE001
        caps["pdf"] = False
    return caps


# ============================================================
# .env / Supabase
# ============================================================
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


def sb_patch(base, k, table, match_qs, payload):
    req = urllib.request.Request(f"{base.rstrip('/')}/rest/v1/{table}?{match_qs}",
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


# ============================================================
# Fetch helpers
# ============================================================
def fetch_text(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            cs = r.headers.get_content_charset() or "utf-8"
            return r.read(2_500_000).decode(cs, "replace")
    except Exception:  # noqa: BLE001
        return None


def fetch_bytes(url, cap=8_000_000):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            return r.read(cap)
    except Exception:  # noqa: BLE001
        return None


# ============================================================
# HTML extraction (text + links + image srcs)
# ============================================================
class _Extract(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text_parts: list[str] = []
        self.links: list[str] = []
        self.images: list[tuple[str, str]] = []   # (src, alt+class hint)
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        d = dict(attrs)
        if tag == "a" and d.get("href"):
            self.links.append(d["href"])
        if tag == "img":
            src = d.get("src") or d.get("data-src") or ""
            hint = " ".join(filter(None, [d.get("alt", ""), d.get("class", ""), src]))
            if src:
                self.images.append((src, hint))

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            t = data.strip()
            if t:
                self.text_parts.append(t)


# ============================================================
# Price parsing (shared by every source)
# ============================================================
def parse_prices_from_parts(parts: list[str], strict_dollar: bool = True) -> list[dict]:
    """parts = list of text fragments (DOM nodes, or OCR lines). Returns
    [{name, price, section}]. strict_dollar requires a literal '$' (good for
    noisy OCR / websites with lots of bare numbers)."""
    rx = DOLLAR_PRICE_RE if strict_dollar else PRICE_RE
    items, section = [], None
    for i, t in enumerate(parts):
        if SECTION_HINT_RE.search(t) and len(t) < 40 and "$" not in t:
            section = t.title()
        m = rx.search(t)
        if not m:
            continue
        try:
            price = float(m.group(1))
        except ValueError:
            continue
        if price < 3 or price > 500:          # plausible menu-item bound
            continue
        name = rx.sub("", t).strip(" -–—·•.\t")
        if len(name) < 2 and i > 0:
            name = parts[i - 1].strip(" -–—·•.\t")
        name = re.sub(r"\s+", " ", name)[:80]
        if name and not name.replace(" ", "").isdigit():
            items.append({"name": name, "price": price, "section": section})
    # dedupe (name, price)
    seen, uniq = set(), []
    for it in items:
        key = (it["name"].lower(), it["price"])
        if key not in seen:
            seen.add(key); uniq.append(it)
    return uniq


def ocr_image_bytes(data: bytes) -> str:
    """OCR raw image bytes → text. Empty string if OCR unavailable/failed."""
    try:
        import pytesseract
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        # upscale small images a touch — helps Tesseract on menu shots
        if max(img.size) < 1200:
            ratio = 1200 / max(img.size)
            img = img.resize((int(img.size[0] * ratio), int(img.size[1] * ratio)))
        return pytesseract.image_to_string(img)
    except Exception:  # noqa: BLE001
        return ""


def ocr_text_to_items(text: str) -> list[dict]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return parse_prices_from_parts(lines, strict_dollar=True)


_PRICE_TOKEN_RE = re.compile(r"^\$\s?(\d{1,3}(?:\.\d{2})?)$")

def ocr_image_to_items(data: bytes) -> list[dict]:
    """OCR an image and pair names↔prices by ROW POSITION (bounding boxes), not
    by line text. This is what makes two-column menus (dish on the left, price on
    the right, or two dish/price pairs side by side) parse correctly — reading the
    plain OCR text would mis-pair them."""
    try:
        import statistics
        import pytesseract
        from pytesseract import Output
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        if max(img.size) < 1400:
            ratio = 1400 / max(img.size)
            img = img.resize((int(img.size[0] * ratio), int(img.size[1] * ratio)))
        d = pytesseract.image_to_data(img, output_type=Output.DICT)
    except Exception:  # noqa: BLE001
        return []

    toks = []
    for i in range(len(d["text"])):
        t = (d["text"][i] or "").strip()
        try:
            conf = float(d["conf"][i])
        except ValueError:
            conf = -1
        if t and conf >= 0:
            toks.append({"t": t, "l": int(d["left"][i]), "top": int(d["top"][i]),
                         "h": int(d["height"][i])})
    if not toks:
        return []
    medh = statistics.median([x["h"] for x in toks]) or 12
    toks.sort(key=lambda x: (x["top"], x["l"]))

    # cluster tokens into rows by vertical position
    rows, cur, cur_top = [], [], None
    for tk in toks:
        if cur_top is None or abs(tk["top"] - cur_top) <= 0.7 * medh:
            cur.append(tk); cur_top = tk["top"] if cur_top is None else cur_top
        else:
            rows.append(cur); cur, cur_top = [tk], tk["top"]
    if cur:
        rows.append(cur)

    section, items = None, []
    for row in rows:
        row.sort(key=lambda x: x["l"])
        names_buf = []
        row_text = " ".join(t["t"] for t in row)
        if SECTION_HINT_RE.search(row_text) and "$" not in row_text and len(row_text) < 40:
            section = row_text.title()
        for tk in row:
            m = _PRICE_TOKEN_RE.match(tk["t"].replace(",", ""))
            if m:
                price = float(m.group(1))
                name = re.sub(r"\s+", " ", " ".join(names_buf)).strip(" -–—·•.")[:80]
                if name and 3 <= price <= 500 and not name.replace(" ", "").isdigit():
                    items.append({"name": name, "price": price, "section": section})
                names_buf = []
            else:
                names_buf.append(tk["t"])
    # dedupe
    seen, uniq = set(), []
    for it in items:
        key = (it["name"].lower(), it["price"])
        if key not in seen:
            seen.add(key); uniq.append(it)
    return uniq or ocr_text_to_items(pytesseract_text_safe(data))


def pytesseract_text_safe(data: bytes) -> str:
    return ocr_image_bytes(data)


# ============================================================
# SOURCES — each returns (items, source_url)
# ============================================================
def src_website_text(website) -> tuple[list[dict], str]:
    parsed = urllib.parse.urlparse(website if "://" in website else "https://" + website)
    base = f"{parsed.scheme or 'https'}://{parsed.netloc or parsed.path}"
    best, best_url, all_links, all_imgs = [], "", [], []
    for path in CONTACT_PATHS:
        url = urllib.parse.urljoin(base + "/", path)
        html = fetch_text(url)
        if not html:
            continue
        ex = _Extract()
        try:
            ex.feed(html)
        except Exception:  # noqa: BLE001
            pass
        all_links += [urllib.parse.urljoin(url, h) for h in ex.links]
        all_imgs += [(urllib.parse.urljoin(url, s), h) for s, h in ex.images]
        items = parse_prices_from_parts(ex.text_parts, strict_dollar=True)
        if len(items) > len(best):
            best, best_url = items, url
        if len(best) >= MIN_GOOD_ITEMS:
            break
        time.sleep(0.4)
    # stash discovered links/images on the function via attributes for reuse
    src_website_text.last_links = list(dict.fromkeys(all_links))
    src_website_text.last_images = all_imgs
    return best, best_url


def src_website_pdf(caps) -> tuple[list[dict], str]:
    if not caps["pdf"]:
        return [], ""
    import pdfplumber
    links = getattr(src_website_text, "last_links", [])
    pdfs = [u for u in links if u.lower().split("?")[0].endswith(".pdf")
            and MENU_LINK_RE.search(u)] or \
           [u for u in links if u.lower().split("?")[0].endswith(".pdf")]
    for url in pdfs[:3]:
        data = fetch_bytes(url)
        if not data:
            continue
        try:
            text_parts = []
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                for page in pdf.pages[:6]:
                    txt = page.extract_text() or ""
                    text_parts += [ln.strip() for ln in txt.splitlines() if ln.strip()]
            items = parse_prices_from_parts(text_parts, strict_dollar=True)
            if items:
                return items, url
        except Exception:  # noqa: BLE001
            continue
        time.sleep(0.3)
    return [], ""


def src_website_image(caps) -> tuple[list[dict], str]:
    if not caps["ocr"]:
        return [], ""
    imgs = getattr(src_website_text, "last_images", [])
    # prefer images that look like menus
    menu_imgs = [u for u, hint in imgs if MENU_IMG_RE.search(hint)]
    menu_imgs = list(dict.fromkeys(menu_imgs))[:MAX_IMAGES]
    best, best_url = [], ""
    for url in menu_imgs:
        if url.lower().split("?")[0].endswith((".svg", ".gif")):
            continue
        data = fetch_bytes(url)
        if not data:
            continue
        items = ocr_image_to_items(data)
        if len(items) > len(best):
            best, best_url = items, url
        if len(best) >= MIN_GOOD_ITEMS:
            break
        time.sleep(0.3)
    return best, best_url


def get_place_photo_names(place_id, key) -> list[str]:
    url = f"https://places.googleapis.com/v1/places/{place_id}"
    req = urllib.request.Request(url, headers={
        "X-Goog-Api-Key": key, "X-Goog-FieldMask": "photos", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            data = json.loads(r.read())
        return [p["name"] for p in data.get("photos", []) if p.get("name")]
    except Exception:  # noqa: BLE001
        return []


def src_places_photos(caps, place_id, key) -> tuple[list[dict], str]:
    if not caps["ocr"] or not place_id or not key:
        return [], ""
    names = get_place_photo_names(place_id, key)[:MAX_PLACES_PHOTOS]
    best, best_url = [], ""
    for name in names:
        media = f"https://places.googleapis.com/v1/{name}/media?maxWidthPx=1400&key={key}"
        data = fetch_bytes(media)
        if not data:
            continue
        items = ocr_image_to_items(data)
        if len(items) > len(best):
            best, best_url = items, f"google_places_photo:{name}"
        if len(best) >= MIN_GOOD_ITEMS:
            break
        time.sleep(0.3)
    return best, best_url


def src_yelp(caps, name, city) -> tuple[list[dict], str]:
    """Best-effort Yelp menu page. Yelp blocks bots aggressively and its menus
    frequently omit prices, so expect this to come up empty often — it's a last
    resort, not a primary source."""
    if not name:
        return [], ""
    slug = re.sub(r"[^a-z0-9]+", "-", f"{name} {city or ''}".strip().lower()).strip("-")
    for url in (f"https://www.yelp.com/menu/{slug}", f"https://www.yelp.com/biz/{slug}"):
        html = fetch_text(url)
        if not html:
            continue
        ex = _Extract()
        try:
            ex.feed(html)
        except Exception:  # noqa: BLE001
            pass
        items = parse_prices_from_parts(ex.text_parts, strict_dollar=True)
        if len(items) >= 4:
            return items, url
        time.sleep(0.5)
    return [], ""


# ============================================================
# Orchestrate one restaurant through the waterfall
# ============================================================
def scrape_one(r: dict, caps: dict, key: str, use_yelp: bool, use_photos: bool) -> dict:
    out = {"items": [], "confidence": "none", "source": None, "sources_tried": [],
           "source_url": "", "scraped_at": datetime.now(timezone.utc).isoformat()}
    website = r.get("website")
    place_id = r.get("google_place_id")
    city = r.get("city")

    plan = []
    if website:
        plan += [("website_text", lambda: src_website_text(website)),
                 ("website_pdf",  lambda: src_website_pdf(caps)),
                 ("website_image", lambda: src_website_image(caps))]
    if use_photos:
        plan += [("places_photos", lambda: src_places_photos(caps, place_id, key))]
    if use_yelp:
        plan += [("yelp", lambda: src_yelp(caps, r.get("name"), city))]

    best_items, best_src, best_url = [], None, ""
    for src_name, fn in plan:
        out["sources_tried"].append(src_name)
        try:
            items, url = fn()
        except Exception:  # noqa: BLE001
            items, url = [], ""
        if len(items) > len(best_items):
            best_items, best_src, best_url = items, src_name, url
        if len(best_items) >= MIN_GOOD_ITEMS:
            break
        time.sleep(0.2)

    best_items = trim_outliers(best_items)
    if len(best_items) >= MIN_STORE_ITEMS:
        prices = [it["price"] for it in best_items]
        conf = ("high" if best_src in ("website_text", "website_pdf") and len(best_items) >= 12
                else "medium" if len(best_items) >= 6 else "low")
        out.update({"items": best_items[:120], "item_count": len(best_items),
                    "avg_price": round(sum(prices) / len(prices), 2),
                    "min_price": min(prices), "max_price": max(prices),
                    "source": best_src, "source_url": best_url, "confidence": conf})
    return out


# ============================================================
# Main
# ============================================================
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--project", default=DEFAULT_PROJECT)
    ap.add_argument("--seed", type=int, default=None, help="RNG seed for repeatable sample.")
    ap.add_argument("--only", default="", help="Substring filter on name (debug a single spot).")
    ap.add_argument("--no-yelp", action="store_true")
    ap.add_argument("--no-photos", action="store_true", help="Skip Google Places photo OCR.")
    ap.add_argument("--no-ocr", action="store_true", help="Disable all OCR sources.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    env = load_env()
    base, skey = env.get("SUPABASE_URL", ""), env.get("SUPABASE_SERVICE_ROLE_KEY", "")
    gkey = env.get("GOOGLE_PLACES_API_KEY", "")
    if not base or not skey:
        print("❌ SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY empty in .env"); return 1

    caps = capabilities()
    if args.no_ocr:
        caps["ocr"] = False
    print("Capabilities:  OCR(images/photos)="
          + ("✅" if caps["ocr"] else "❌ (brew install tesseract; pip3 install pytesseract pillow)")
          + "   PDF="
          + ("✅" if caps["pdf"] else "❌ (pip3 install pdfplumber)"))
    print(f"Sources: website_text, "
          + ("website_pdf, " if caps["pdf"] else "")
          + ("website_image, " if caps["ocr"] else "")
          + ("places_photos, " if caps["ocr"] and not args.no_photos and gkey else "")
          + ("" if args.no_yelp else "yelp"))
    print()

    # Candidates: project restaurants not yet menu-scraped. NOTE: no longer
    # requires a website — alt sources (photos/yelp) can still find a menu.
    rows = sb_get(base, skey,
        "companies?select=id,name,website,city,google_place_id,metadata,"
        "company_segments!inner(segments!inner(projects!inner(slug)))"
        f"&company_segments.segments.projects.slug=eq.{args.project}")
    uniq, skipped_chains = {}, 0
    for r in rows:
        if (r.get("metadata") or {}).get("menu"):
            continue
        if args.only and args.only.lower() not in r["name"].lower():
            continue
        if not args.only and is_excluded(r["name"]):
            skipped_chains += 1
            continue
        uniq[r["id"]] = r
    candidates = list(uniq.values())
    if skipped_chains:
        print(f"(skipped {skipped_chains} chains/groceries/hotels — not independent targets)")
    if not candidates:
        print(f"No un-scraped restaurants for project '{args.project}'. "
              f"Run the ingest first."); return 0

    if args.seed is not None:
        random.seed(args.seed)
    sample = candidates if args.only else random.sample(candidates, min(args.limit, len(candidates)))
    print(f"Candidates: {len(candidates)}  →  processing {len(sample)}\n")

    runs, by_source = [], {}
    ok = 0
    for i, r in enumerate(sample, 1):
        time.sleep(DELAY_S)
        menu = scrape_one(r, caps, gkey, use_yelp=not args.no_yelp,
                          use_photos=not args.no_photos)
        n = menu.get("item_count", 0)
        icon = {"high": "✅", "medium": "🟡", "low": "🟠", "none": "⬜"}[menu["confidence"]]
        src = menu.get("source") or "—"
        rng = (f"${menu['min_price']:.0f}-${menu['max_price']:.0f} avg ${menu['avg_price']:.0f}"
               if n else "no prices")
        site = "web" if r.get("website") else "no-site"
        print(f"  {i:>2}/{len(sample)} {icon} {r['name'][:30]:30s} [{site:7s}] "
              f"{n:>3} items via {src:14s} {rng}")

        if n:
            ok += 1
            by_source[src] = by_source.get(src, 0) + 1
        if not args.dry_run:
            new_meta = dict(r.get("metadata") or {})
            new_meta["menu"] = menu
            try:
                sb_patch(base, skey, "companies", f"id=eq.{r['id']}",
                         {"metadata": new_meta, "last_enriched_at": menu["scraped_at"]})
            except urllib.error.HTTPError as e:
                print(f"      ⚠️ patch HTTP {e.code}")
        runs.append({"entity_type": "company", "entity_id": r["id"],
                     "source": menu.get("source") or "none", "operation": "scrape_menu",
                     "request_payload": {"sources_tried": menu["sources_tried"]},
                     "response_payload": {"item_count": n, "source_url": menu.get("source_url")},
                     "fields_updated": ["metadata.menu"] if n else [],
                     "cost_usd": 0, "succeeded": n > 0})

    if not args.dry_run and runs:
        try:
            sb_insert(base, skey, "enrichment_runs", runs)
        except urllib.error.HTTPError as e:
            print(f"⚠️ enrichment_runs insert HTTP {e.code}")

    print(f"\nParsed prices for {ok}/{len(sample)}.")
    if by_source:
        print("By source: " + ", ".join(f"{k}={v}" for k, v in sorted(by_source.items())))
    print("View: SELECT * FROM v_restaurant_menu_pricing "
          f"WHERE project_slug='{args.project}';")
    if not caps["ocr"]:
        print("\n💡 Install OCR to read image/photo menus (most 30A menus are images):")
        print("   brew install tesseract && pip3 install pytesseract pillow --break-system-packages")
    return 0


if __name__ == "__main__":
    sys.exit(main())
