# Atlanta Restaurants — B2B instance

A **second project** running on the same Referral OS schema, proving the
multi-tenant design: zero new tables, just a new `projects` row + its own
segments + a Google-Places data source. Where `sun-moon-30a` recruits partners
who *send you guests*, this one treats Atlanta restaurants as **prospects you
sell to**.

## What's different from Sun & Moon

| | sun-moon-30a | atlanta-restaurants-b2b |
|---|---|---|
| Companies are… | referral partners | sales prospects |
| Source | ZoomInfo + Apollo | **Google Places (New)** |
| `referrals` table | bookings + commission | unused (no referral $) |
| `company_segments.status='partner'` | active referrer | **signed customer** |
| Value driver | referral revenue | deal ACV × close rate |

## Files added

```
schema/006_atlanta_restaurants_seed.sql   project + 6 restaurant segments
schema/007_restaurant_views.sql           v_atlanta_restaurants,
                                          v_atlanta_restaurant_targets (recs),
                                          v_atlanta_menu_pricing
ingest_atlanta_restaurants.py             grid-tiled Places ingest (ALL restaurants)
scrape_restaurant_menus.py                real menu prices for a random 25
generate_sample_view.py                   the personalized HTML view (sample or --from-db)
atlanta_restaurants_view.html             rendered sample you can open now
COST_ATLANTA_RESTAURANTS.md               Places API cost math
```

## Run order (on your machine — the sandbox can't reach googleapis.com)

```bash
cd "/Users/samantha/Documents/Claude/Projects/Sun & Moon at 30a/referral-os"

# 1. migrate the two new SQL files into Supabase
psql "$DATABASE_URL" -f schema/006_atlanta_restaurants_seed.sql
psql "$DATABASE_URL" -f schema/007_restaurant_views.sql

# 2. smoke test the ingest (free — 4 Places calls)
python3 ingest_atlanta_restaurants.py --sample

# 3. full Atlanta (still free under the Pro tier — see COST doc)
python3 ingest_atlanta_restaurants.py

# 4. scrape real menu prices for a random 25
python3 scrape_restaurant_menus.py --limit 25

# 5. regenerate the view from live data
python3 generate_sample_view.py --from-db
```

The Streamlit dashboard already auto-discovers new projects — pick
"Atlanta Restaurants — B2B Outreach" in the sidebar.

## The "recommendations" (v_atlanta_restaurant_targets)

A transparent SQL fit score, 0–100, so ranking works before any ML:

- **ability_to_pay** — $-tier (can they afford what you sell?)
- **scale** — review count as a traffic/size proxy
- **quality_window** — rewards 4.0–4.6★ (good, but room to grow)
- **digital_gap** — no website = the easiest value story, regardless of product

Each row gets a Tier (A/B/C) and a plain-English `pitch_angle` tailored to its
dominant signal. **Set what you're actually selling** by editing `product_pitch`
context in your outreach templates — the scoring is product-agnostic by design.

## Menu pricing reality check

Google Places only returns a $-tier ($–$$$$), never real item prices. The
scraper (`scrape_restaurant_menus.py`) pulls actual prices via a **source
waterfall**: website text → website PDFs → website menu **images (OCR)** →
**Google Places menu photos (OCR)** → Yelp (best-effort). Restaurants with no
website are still attempted via photos/Yelp. OCR uses bounding-box pairing so
two-column image menus parse correctly.

Optional deps (script degrades gracefully without them, just fewer sources):
`brew install tesseract` + `pip3 install pytesseract pillow pdfplumber --break-system-packages`.
