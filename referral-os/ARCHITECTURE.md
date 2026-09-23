# Architecture

A code-driven B2B referral OS. Postgres canonical, Airtable operator-friendly, Cloudflare for edge tracking, ZoomInfo + Apollo + Hunter for enrichment, Streamlit for analytics.

Designed to be **multi-tenant from day zero** — Sun & Moon at 30A is project #1; any future Cristian venture plugs in by inserting a row in `projects` and a `segments.yaml`.

## Component map

```
                                                       ┌─────────────────────┐
                                                       │  ZoomInfo (MCP)     │
                                                       │  primary anchor     │
                                                       └──────────┬──────────┘
                                                                  │
   ┌──────────────────────────────────────────────────────────────▼───────────┐
   │  Python enrichment pipeline   (enrichment/pipeline.py)                   │
   │   - reads segments.yaml                                                  │
   │   - calls ZI build_list, upserts companies                               │
   │   - waterfall: ZI → Apollo → Hunter for fill-in                          │
   │   - writes enrichment_runs audit log                                     │
   └──────────────────────────┬───────────────────────────────────────────────┘
                              │
                              ▼
   ┌──────────────────────────────────────────────────────────────────────────┐
   │  Postgres   (Supabase Pro)         <-- SOURCE OF TRUTH                   │
   │   projects  segments  companies  contacts  company_segments              │
   │   campaigns  outreach  tracking_links  tracking_events                   │
   │   referrals  enrichment_runs                                             │
   │   + views: v_partner_heat, v_segment_performance                         │
   │   + RPCs:  mint_tracking_link, increment_link_click                      │
   └─────┬───────────────────────────────────────────────────┬────────────────┘
         │                                                   │
         │  Sequin (free tier)                               │  PostgREST
         │  one-way sync                                     │  REST API
         ▼                                                   ▼
   ┌──────────────────────┐                       ┌──────────────────────────┐
   │  Airtable            │                       │  Cloudflare Worker        │
   │  Operator UI         │                       │  tracking/worker.js       │
   │   - Companies        │                       │   /r/:code   redirector   │
   │   - Contacts         │                       │   /p/:id.png pixel        │
   │   - Segments         │                       │   /e/:id     email click  │
   │   - Outreach         │                       │   /events    site events  │
   │   - Campaigns        │                       └────────┬──────────────────┘
   │   - Referrals        │                                │
   └──────────────────────┘                                │
                                                           ▼
                                                  ┌─────────────────────────┐
                                                  │  Postgres tracking_events│
                                                  │  (back through PostgREST)│
                                                  └─────────────────────────┘

   ┌─────────────────────────────────────────────────────────────────────────┐
   │  Streamlit dashboard (dashboard/streamlit_app.py)                       │
   │   - Heat map, funnel, engagement timeline, enrichment health            │
   │   - Reads Postgres views directly                                       │
   └─────────────────────────────────────────────────────────────────────────┘
```

## Data model decisions, explained

### Why `companies` is global but `segments` is project-scoped

A wedding planner in Birmingham may be relevant to many future properties. We never want to duplicate the company row when Cristian launches project #2. So:

- `companies` rows are global (one company = one row, ever).
- `segments` are scoped to a project (Sun & Moon's "wedding-planner-local" segment is separate from a future project's).
- `company_segments` is the M:N bridge that carries project context indirectly through the segment FK.

This is the **single most important schema decision** for extensibility. Don't change it.

### Why every event has its own row (instead of counters on partners)

Counters lie. They can't tell you whether the partner clicked the email last week or three weeks ago. The `tracking_events` table is append-only, every touch is a row, and the views aggregate as needed. Storage is cheap; insight is expensive.

### Why `enrichment_runs` is append-only

When a partner email bounces six months later, you want to know which provider's data you trusted, when, and for how much. An audit log makes this a one-line query.

### Why JSONB metadata on every table

Future projects will need fields we haven't imagined. JSONB is the escape hatch — but only after you've thought hard about whether it should be a real column with constraints.

## Tracking design

The Cloudflare Worker is the only public-internet attack surface. It:

1. Looks up a `tracking_link` row by short_code.
2. Logs a `tracking_events` row asynchronously (via `ctx.waitUntil`).
3. Redirects within ~80ms p95 globally.

No state lives in the worker — restartable, scaleable, free for our volume.

IP addresses are SHA-256 hashed with a daily-rotatable salt before storage. You get bot-detection and rough country-level geo without keeping raw PII.

## Outreach loop (Phase 2 build, not in v1)

The schema supports the loop; the implementation is roadmap:

1. Select a campaign + a contact cohort (`SELECT contact_id FROM contacts c JOIN company_segments cs ON cs.company_id = c.company_id WHERE s.slug = ... AND cs.status = 'prospect'`).
2. For each contact, mint a tracking link (`mint_tracking_link` RPC).
3. Render the email template with the unique link + pixel.
4. Send via Postmark/Resend, capture provider_message_id into `outreach.provider_message_id`.
5. Webhook from email provider updates `outreach.opened_at` / `outreach.first_clicked_at`.
6. Worker logs match these to the same `outreach` row via tracking_link_id.

`scripts/outreach_send.py` is the next thing to build after the anchor list lands.

## Extensibility checklist (when adding project #2)

- [ ] `INSERT INTO projects ...` — one row.
- [ ] Copy `enrichment/segments.yaml` → `enrichment/segments-<slug>.yaml`, tune queries.
- [ ] Create a new seed SQL or use the YAML as the source.
- [ ] Subdomain for tracking (`r.newproject.com`) → point at same Worker.
- [ ] Streamlit picks up new project automatically (sidebar selector).
- [ ] (Optional) New Airtable base, or reuse with project filter.

No code changes anywhere. That's the test.

## Things deliberately NOT in v1

- **Two-way Airtable sync for non-status fields** — leads to spreadsheet-graveyard syndrome.
- **Real-time social listening (Common Room / Customers.ai)** — wait for partners to be warm.
- **A custom CRM UI** — Airtable is good enough until it isn't. Don't build a thing Airtable already does.
- **Email sending in the pipeline** — separate concern; Postmark/Instantly handle it better.
- **AI-generated outreach copy in the pipeline** — separate concern; templating + manual review for v1.
