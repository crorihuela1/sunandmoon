# Airtable Operator UI — table & field spec

Airtable is the **operator view**, not the source of truth. Postgres is canonical. We use the [Airtable Postgres Sync](https://www.airtable.com/platform/sync) or [Sequin](https://sequin.io) (free tier covers this) to keep Airtable mirrored against the DB.

**Why both?** Postgres gives you SQL, FK integrity, and zero per-seat fees on querying. Airtable gives Cristian and any future operators a beautiful CRUD UI without us building one. The cost is one $24/mo seat — cheaper than a week of building a custom admin panel.

**Edit policy:** to start, Airtable is read-mostly. Edits happen in two places:
1. **Status fields** (`company_segments.status`, `outreach.status`, free-text `notes`) — Airtable → Postgres via Sequin's write-back, or a nightly Zapier sync.
2. **Everything else** — Postgres-side via SQL or the Python CLI.

Don't try to two-way sync data fields like `email` or `industry`. That way lies the spreadsheet graveyard.

---

## Base structure

One Airtable base: **Referral OS — Sun & Moon 30A**. Tables map 1:1 to Postgres.

### Table: `Companies`

| Field | Type | Source column | Notes |
|---|---|---|---|
| Name | Single-line text (primary) | `companies.name` | |
| Domain | URL | `companies.domain` | |
| Industry | Single-select | `companies.industry` | Pre-seed options from `_distinct_industries.sql` |
| City | Single-line | `companies.city` | |
| State | Single-line | `companies.state` | |
| Phone | Phone | `companies.main_phone` | |
| Website | URL | `companies.website` | |
| LinkedIn | URL | `companies.linkedin_url` | |
| Instagram | Single-line | `companies.instagram_handle` | |
| Segments | Linked → `Segments` | via `company_segments` | Multi-select |
| Partner status | Single-select | `company_segments.status` | prospect / contacted / engaged / partner / dormant |
| Partner tier | Single-select | `company_segments.partner_tier` | A / B / C |
| Relevance | Number (0-100) | `company_segments.relevance_score` | |
| Data completeness | Number (0-100) | `companies.enrichment_score` | Color-formula red < 40, amber < 70, green ≥ 70 |
| Last enriched | Date | `companies.last_enriched_at` | |
| Events 7d | Rollup count | `tracking_events` filtered to last 7d | Built via view `v_partner_heat` |
| Events 30d | Rollup count | `tracking_events` filtered to last 30d | |
| Last touched | Date | `MAX(tracking_events.occurred_at)` | |
| Notes | Long text | `companies.notes` | Two-way sync OK |
| Postgres ID | Single-line | `companies.id` | **read-only**, never edit |

### Table: `Contacts`

| Field | Type | Source column |
|---|---|---|
| Full name | Single-line (primary) | `contacts.full_name` |
| Company | Linked → `Companies` | via `contacts.company_id` |
| Title | Single-line | `contacts.title` |
| Email | Email | `contacts.email` |
| Verified | Checkbox | `contacts.email_verified` |
| Owner / DM | Checkbox | `contacts.is_owner` OR `is_decision_maker` |
| Phone | Phone | `contacts.direct_phone` |
| LinkedIn | URL | `contacts.linkedin_url` |
| Last outreach | Date | `MAX(outreach.sent_at)` |
| Last reply | Date | `MAX(outreach.replied_at)` |
| Reply sentiment | Single-select | `outreach.reply_sentiment` |
| Notes | Long text | `contacts.notes` |
| Postgres ID | Single-line | `contacts.id` (read-only) |

### Table: `Segments`

| Field | Type | Source column |
|---|---|---|
| Name | Single-line (primary) | `segments.name` |
| Slug | Single-line | `segments.slug` |
| Tier | Number | `segments.priority_tier` |
| Target count | Number | `segments.target_count` |
| Companies count | Count | rollup from `Companies` |
| Partners count | Count w/ filter `partner_status="partner"` | |
| Avg referral $ | Currency | `segments.expected_referral_value_usd` |
| Pipeline value | Formula | `Partners count × Avg referral $ × Expected conv` |
| Description | Long text | `segments.description` |

### Table: `Campaigns`

| Field | Type | Source column |
|---|---|---|
| Name | Single-line | `campaigns.name` |
| Slug | Single-line | `campaigns.slug` |
| Channel | Single-select | `campaigns.channel` |
| Status | Single-select | `campaigns.status` |
| Starts | Date | `campaigns.starts_at` |
| Ends | Date | `campaigns.ends_at` |
| Sent count | Rollup | from `outreach` |
| Opened count | Rollup w/ filter | |
| Clicked count | Rollup w/ filter | |
| Replied count | Rollup w/ filter | |
| Open rate | Formula | Opened / Sent |
| Click rate | Formula | Clicked / Sent |
| Reply rate | Formula | Replied / Sent |
| Subject | Long text | `campaigns.template_subject` |
| Body | Long text | `campaigns.template_body` |

### Table: `Outreach` (per-touch detail)

| Field | Type | Source column |
|---|---|---|
| Contact | Linked → `Contacts` | `outreach.contact_id` |
| Campaign | Linked → `Campaigns` | `outreach.campaign_id` |
| Step | Number | `outreach.sequence_step` |
| Sent at | Date | `outreach.sent_at` |
| Opened? | Checkbox | derived from `outreach.opened_at` |
| Clicked? | Checkbox | derived from `outreach.first_clicked_at` |
| Replied? | Checkbox | derived from `outreach.replied_at` |
| Status | Single-select | `outreach.status` |
| Sentiment | Single-select | `outreach.reply_sentiment` |
| Subject | Single-line | `outreach.subject` |
| Body | Long text | `outreach.body` |

### Table: `Referrals` (the money)

| Field | Type | Source column |
|---|---|---|
| Partner | Linked → `Companies` | `referrals.partner_company_id` |
| Guest name | Single-line | `referrals.guest_name` |
| Booking value | Currency | `referrals.booking_value_usd` |
| Nights | Number | `referrals.booking_nights` |
| Check-in | Date | `referrals.booking_dates_in` |
| Check-out | Date | `referrals.booking_dates_out` |
| Platform | Single-select | `referrals.booking_platform` |
| Commission paid | Currency | `referrals.commission_paid_usd` |
| Attributed via | Single-select | `referrals.attributed_via` |

---

## Views Cristian will actually live in

**Heat map** (Companies table, sorted by `Events 30d` desc, filtered `Partner status != disqualified`) — "who's paying attention this week?"

**Cold queue** (Companies, filter `Partner status = prospect`, sorted by `Relevance` desc + `Tier` asc) — daily outreach worklist.

**Active partners** (Companies, filter `Partner status = partner`) — your existing book of referral relationships.

**Stale enrichment** (Companies, filter `Last enriched > 60 days ago`) — feeds the `enrich-stale` CLI command.

**Reply triage** (Outreach, filter `Replied? = true AND Sentiment is empty`) — Cristian's morning inbox.

**Segment funnel** (Segments, sorted by Tier, showing Target → Companies → Partners → Pipeline value) — strategic view.

---

## Sync mechanics

Two options, in order of recommendation:

1. **Sequin.io** (free tier handles 1M rows): point at Supabase, define which tables sync to Airtable, write-back enabled on specific columns. ~30 min setup.
2. **Airtable Web Clipper + custom Postgres script**: nightly cron uploads CSV diffs via Airtable API. Free but less real-time.

`scripts/sync_airtable.py` (TODO in roadmap) implements option 2 as a backup.
