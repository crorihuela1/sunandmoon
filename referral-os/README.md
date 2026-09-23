# Referral OS

A code-driven B2B referral partner platform. Multi-project from day zero.

**What it does:** Sources 2,000+ relevant referral targets from ZoomInfo (golf cart rentals, wedding planners, corporate retreat coordinators, photographers, travel advisors), enriches them via a Clay-style waterfall (ZoomInfo → Apollo → Hunter), gives each one a unique tracked URL, and reports back on every click, email open, site visit, and form submit. Built so you know which partners are warm before you call them.

**What makes it different from a list in Airtable:** every table carries `project_id`, every event is logged immutably, every enrichment is auditable — the same code base supports your next property, your next venture, your next anything.

## Quick start

```bash
# 1. Provision Postgres (Supabase Pro)
psql $DATABASE_URL -f schema/001_init.sql
psql $DATABASE_URL -f schema/002_seed.sql
psql $DATABASE_URL -f schema/003_rpc.sql

# 2. Set environment
cp .env.example .env
# fill in DATABASE_URL, ZOOMINFO_*, APOLLO_API_KEY, HUNTER_API_KEY

# 3. Install Python deps
pip install -r requirements.txt

# 4. Smoke test — pull 10 golf cart rental companies
python -m enrichment.pipeline build-anchor-list \
  --only-segments golf-cart-rental --limit 10

# 5. Inspect
psql $DATABASE_URL -c "SELECT name, city, industry FROM companies LIMIT 10;"

# 6. Once happy, run the full anchor list (see ops/anchor_list_plan.md)
python -m enrichment.pipeline build-anchor-list

# 7. Deploy tracking
cd tracking && wrangler deploy

# 8. Run the dashboard
streamlit run dashboard/streamlit_app.py
```

## What you get on day one

- **2,000-business anchor list** segmented by 17 referral categories across 3 tiers.
- **Enrichment pipeline** that re-runs on demand and self-audits cost + provenance.
- **Cloudflare Worker** at `r.sunmoon30a.com/r/{code}` that logs every click with full partner attribution.
- **Email open/click tracking** via a 1x1 pixel + redirect handler.
- **Airtable operator UI** synced from Postgres so Cristian can triage in a friendly interface.
- **Streamlit dashboard** with partner heat-map, segment funnel, and revenue attribution.

## What this costs

$157-211/mo in steady state. See `COST_BREAKDOWN.md`.

## File layout

```
referral-os/
├── README.md                       <- you are here
├── ARCHITECTURE.md                 <- component map + design decisions
├── COST_BREAKDOWN.md               <- monthly $ at $200-300 cap
├── ROADMAP.md                      <- phase 1 → 2 → 3
│
├── schema/
│   ├── 001_init.sql                <- tables, indexes, triggers, views
│   ├── 002_seed.sql                <- sun-moon-30a project + 17 segments
│   ├── 003_rpc.sql                 <- mint_tracking_link, increment_link_click
│   └── airtable_schema.md          <- table/field spec for the operator UI
│
├── enrichment/
│   ├── segments.yaml               <- canonical segment definitions
│   ├── zoominfo_client.py          <- MCP + HTTP modes
│   ├── waterfall.py                <- ZoomInfo → Apollo → Hunter chain
│   ├── pipeline.py                 <- CLI: build-anchor-list, enrich-stale
│   └── scoring.py                  <- relevance + completeness scoring
│
├── tracking/
│   ├── worker.js                   <- Cloudflare Worker
│   ├── wrangler.toml
│   └── README.md
│
├── dashboard/
│   ├── streamlit_app.py            <- operator dashboard
│   └── requirements.txt
│
├── ops/
│   ├── anchor_list_plan.md         <- phased ZoomInfo build plan
│   └── Makefile                    <- common commands
│
├── requirements.txt                <- Python deps
├── .env.example
└── .gitignore
```

## Adding a second project (the extensibility test)

When you launch your next venture, follow this in <30 minutes:

```sql
INSERT INTO projects (slug, name, primary_domain, tracking_base_url)
VALUES ('next-venture', 'Next Venture',
        'nextventure.com', 'https://r.nextventure.com');
```

Then write a `enrichment/segments-next-venture.yaml` (start by copying the existing one), point a subdomain at the same Cloudflare Worker, and you're done. No code changes. That's the test.

## Citations

This setup pulls inspiration from Clay.com's enrichment waterfalls, Common Room's identity resolution model, and PostHog's analytics ergonomics — implemented as code you own, on infrastructure you control.
