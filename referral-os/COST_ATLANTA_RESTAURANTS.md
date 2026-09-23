# Cost estimate — Atlanta Restaurants ingest

_Last priced June 2026 against Google Maps Platform's SKU model (the March 2025
pricing overhaul: the old flat $200 credit is gone, replaced by **per-SKU free
monthly caps**). Verify live numbers in your Google Cloud billing console before
a big run — costs depend on the exact field mask, which decides the SKU you're
billed at._

## The one thing that controls cost: the field mask

Every Places request is billed at the **highest SKU** your requested fields
touch. Our ingest asks for `priceLevel` + `rating` + `userRatingCount` →
that lands every call in the **Pro** tier. We deliberately **do not** request
`reviews` (review text), which would bump every call to **Enterprise +
Atmosphere** and roughly double the rate. If you only need name/address/phone,
drop those fields and you fall to **Essentials** (cheaper, bigger free cap).

| SKU triggered | Free monthly cap | Rate after cap |
|---|---|---|
| Nearby Search **Essentials** (ids/location only) | 10,000 | ~$17 / 1,000 |
| Nearby Search **Pro** (our ingest: +price, +rating) | **5,000** | ~$32 / 1,000 |
| Nearby Search **Enterprise + Atmosphere** (+reviews) | 1,000 | ~$40 / 1,000 |
| Place Details **Pro** (only if you re-fetch per place) | 5,000 | ~$17 / 1,000 |

## What our ingest actually costs

The script tiles Atlanta into a grid of `searchNearby` calls (one call per tile
in the current single-page implementation) and dedupes by `place_id`.

| Run | Tiles / calls | Pro SKU cost |
|---|---|---|
| `--sample` (Midtown bbox, 1.25 mi grid) | **4 calls** | **$0** (free tier) |
| Full Atlanta bbox (1.25 mi grid) | **182 calls** | **$0** (under the 5,000 Pro free cap) |
| Full Atlanta, finer 0.75 mi grid (denser coverage) | ~500 calls | **$0** (still under free cap) |
| Full Atlanta + subdivide every dense tile for true 100% coverage | ~800–1,200 calls | **$0** (still under free cap) |

**Bottom line: a complete Atlanta restaurant ingest is effectively free** as
long as your *total* monthly Pro-SKU usage across all projects stays under
5,000 calls. Even an aggressive, re-run-weekly cadence stays inside the free
tier. You only start paying ~$32/1,000 if you blow past 5,000 Pro calls/month.

> ⚠️ The free cap is **per billing account, per SKU, aggregated across all your
> projects.** If the Sun & Moon ingests already use Places Pro calls this month,
> they share the same 5,000 bucket. Check the console's SKU usage page.

## Coverage vs. the 60-result cap

A single `searchNearby` returns at most 20 results (the New API tops out at ~60
across pages). Dense areas (Midtown, Buckhead, O4W) will hit that cap in one
tile and silently undercount. The script prints a `⚠️ N tiles returned the full
20` warning; re-run those areas with a smaller `--step-miles` to subdivide.
Estimated true universe in the default bbox: **~3,000–4,500 unique restaurants.**

## Menu scrape cost

`scrape_restaurant_menus.py` hits each restaurant's own website — **$0 in API
fees**, just time (1.5s/site politeness delay → 25 sites ≈ 1 minute). Coverage
is heuristic: clean text menus parse well; image/JS menus return 0 items and are
flagged `confidence: none` for manual review or a paid menu-data provider later.

## If you outgrow the free tier

- **Subscription**: Google's *Starter* plan is $100/mo for 50,000 combined
  calls — only worth it if you're running far more than restaurant ingest.
- **Daily cost cap**: set one in the Cloud console as a guardrail.
- **Paid menu data** (real item prices at scale): budget separately; the scraper
  is the free 80% solution, a menu-data API is the paid 100%.
