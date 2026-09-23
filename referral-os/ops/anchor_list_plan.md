# Anchor list build plan — 2,000 businesses via ZoomInfo

## Target distribution

| Tier | Description | Segment count | Total target |
|---|---|---:|---:|
| 1 | Highest-leverage referral types (planners, retreats) | 3 | 570 |
| 2 | High-frequency / mid-leverage (advisors, golf carts, bike) | 4 | 505 |
| 3 | Brand alignment / guest experience (photographers, chefs, yoga) | 4 | 280 |
| 4 | Complementary lodging (hotels, concierges) | 2 | 55 |
| 5 | Long-tail / opportunistic (restaurants, realtors, venues, florists) | 4 | 370 |
| **Total** | | **17** | **1,780** |

Headroom: with ZoomInfo's typical 60-80% match rate against our keyword filters, ~1,780 target → ~1,200-1,500 actual records. We have ~220 segment-target buffer to push toward 2,000 if needed — most likely raise `travel-advisor` (300→400) and `corp-retreat-planner` (200→300) since those have deepest data depth in ZoomInfo.

## Phased execution (don't burn credits blindly)

### Phase 0 — single-segment smoke test (10 records)
```bash
python -m enrichment.pipeline build-anchor-list \
  --only-segments golf-cart-rental \
  --limit 10
```
**Validates:** ZoomInfo MCP wiring, DB schema, scoring. Inspect rows in Postgres. ~10 credits spent.

### Phase 1 — tier 1 segments at half-target (~285 records)
```bash
python -m enrichment.pipeline build-anchor-list \
  --only-segments wedding-planner-local wedding-planner-feeder corp-retreat-planner \
  --limit 100
```
**Validates:** Match rates, geographic filters work, decision-maker titles surface. ~300 credits.

### Phase 2 — tiers 2-3 (~785 records)
```bash
python -m enrichment.pipeline build-anchor-list \
  --only-segments travel-advisor family-reunion-planner golf-cart-rental bike-rental \
                  photographer-family wedding-photographer private-chef yoga-instructor
```

### Phase 3 — tiers 4-5 (~425 records)
```bash
python -m enrichment.pipeline build-anchor-list \
  --only-segments boutique-hotel-30a concierge-service restaurant-30a \
                  realtor-30a event-venue florist-30a
```

### Phase 4 — decision-maker contacts (~3-5 contacts per company)

After companies are loaded, pull decision-makers using `decision_maker_titles` from segments.yaml. This is a separate pipeline command (`pull-contacts`) and is where you make the actual revenue decision: **only pull contacts for tier 1-3 segments at first** — tiers 4-5 can stay at company level until activated.

Expected contact volume: ~1,100 companies × 2 contacts avg = ~2,200 contacts. ZoomInfo contact credits are pricier than company; budget accordingly.

## Cost estimate (ZoomInfo credits)

Assumes connector seat covers basic usage. If you're on a credit-metered plan:

| Operation | Calls | Cost-per | Subtotal |
|---|---:|---:|---:|
| Company search (build_list pages) | ~50 (40 records/page × 50 pages) | 1 cr/page | 50 cr |
| Company enrich (waterfall fill-in) | ~1,500 | 1 cr | 1,500 cr |
| Contact pull (decision-makers, T1-T3) | ~1,700 | 1 cr | 1,700 cr |
| Contact enrich (direct dials/emails) | ~1,000 | 2 cr | 2,000 cr |
| **Total** | | | **~5,250 cr** |

Confirm with your ZoomInfo CSM; some plans are seat-based and these are free.

## Quality gates before going wide

Before running Phase 2+:

- [ ] Phase 0 produced ≥ 5 valid rows with name, domain, industry, city
- [ ] `data_completeness_score` averages ≥ 50 on Phase 1 records
- [ ] `relevance_score` averages ≥ 60 on Phase 1 records (otherwise tune keywords)
- [ ] At least one decision-maker contact surfaces per company sampled
- [ ] Geographic filters work — < 5% of records in wrong state/metro

If any gate fails, fix `segments.yaml` and re-run Phase 0 against the affected segment before proceeding.

## How to extend to a future project

When you onboard a second property/venture:

1. `INSERT INTO projects (slug, name, primary_domain, tracking_base_url) VALUES ('next-project', ...)`
2. Copy `segments.yaml` → `segments-next-project.yaml`, swap segment slugs + tune queries
3. Run a new `002_seed_<project>.sql` with `WHERE slug='next-project'`
4. Same pipeline command, scoped by project_id. **No code changes.**

This is the payoff for the multi-tenant schema. The 4 hours invested in `project_id`-everywhere saves a month later.
