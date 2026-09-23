# Roadmap

## Phase 1 — Foundation (this delivery)
- Postgres schema + seed (multi-project from day zero)
- ZoomInfo MCP + HTTP client wrappers
- Multi-source enrichment waterfall (ZI → Apollo → Hunter)
- Pipeline CLI (`build-anchor-list`, `enrich-stale`)
- Cloudflare Worker tracking service
- Airtable operator UI spec
- Streamlit dashboard
- Cost + architecture docs

## Phase 2 — Activate (next 2-4 weeks)
- [ ] Provision Supabase project, run migrations
- [ ] Wire ZoomInfo MCP into pipeline (smoke test with 10 records)
- [ ] Phase the anchor list build per `ops/anchor_list_plan.md`
- [ ] Stand up tracking Worker on `r.sunmoon30a.com`
- [ ] Build outreach send script (`scripts/outreach_send.py`)
  - Reads next-due `outreach` rows
  - Mints tracking links per contact
  - Sends via Postmark, stores `provider_message_id`
- [ ] Wire Postmark webhooks → `outreach.opened_at` / `replied_at`
- [ ] Run first campaign: 50 tier-1 wedding planners, multi-touch sequence
- [ ] Spin up Airtable base, configure Sequin sync

## Phase 3 — Scale (months 2-6)
- [ ] **Contact pull subcommand** — `pull-contacts --segment X --titles Owner,Founder,President` (uses ZoomInfo recommend-contacts MCP)
- [ ] **AI-assisted personalization** — for each outreach send, generate a one-liner referencing the partner's company. Use Haiku for cost; cache by (company_id, campaign_id).
- [ ] **Anonymous visitor reveal** — bolt on RB2B free tier (500 reveals/mo), write to `tracking_events` as `site_visit` with `company_id` resolved.
- [ ] **Referral attribution rules** — when a booking inquiry comes in, auto-match it to the most recent partner tracking_link click for that email/IP within 30 days.
- [ ] **Partner portal** — small Next.js page partners log in to and see their own referral stats. Encourages them to keep sending.
- [ ] **Materialized views** for the dashboard once `tracking_events` crosses 100k rows.

## Phase 4 — Extend (when it earns it)
- [ ] **Second project** onboarding. Validate the extensibility story works.
- [ ] **Slack/Discord alerts** for high-signal events (partner X just visited 3 times today).
- [ ] **Tiered commission engine** — partners earn % on bookings; auto-compute monthly payouts from `referrals.commission_paid_usd`.
- [ ] **Outbound dialer integration** (Aircall/JustCall) for phone-based follow-ups, write `phone_call` events.

## Anti-roadmap (deliberately not building)

- Custom CRM UI. Airtable is good enough until it isn't.
- A Clay.com competitor. We use the engine pattern; we're not building a SaaS.
- ML scoring models. The heuristic scoring is fine until you have 10k+ partners with outcome data.
- Real-time streaming. Polling + batch is cheaper and good enough for referral velocity.
