# Sun & Moon 30A — systems recovery (2026-09-15, corrected 2026-09-23)

> **2026-09-23 — most of this document's premise was wrong.** Samantha's whole
> project tree had been syncing to iCloud Drive the entire time, under
> "Sun & Moon at 30a". `outreach_email.py`, the Streamlit dashboard, every
> ingest script and two of the three original worker sources were never lost.
> They are now committed at `referral-os/`, `booking-os/`,
> `sunmoon-gmail-mcp-server/`, `postiz/` and `daily-briefings/`. Corrections are
> marked inline below. What remains true: the reimplemented
> `workers/sunmoon-outreach` works and is deployable, the partner-attribution
> bug was real and is fixed, and `sunmoon-hooks` really does have no original.

## What happened

The referral outreach sender (`outreach_email.py`) and a Streamlit analytics
dashboard ran **only on a local Mac**. That machine was rained on and died
around **2026-08-12**.

**Corrected 2026-09-23 — the dates here were wrong.** Sending did not stop on
2026-08-12. The `outreach` table shows 31 sent that day, then two more partial
runs: 14 on 2026-08-15 and 13 on 2026-08-19. It tapered off over a week rather
than stopping dead, and 2026-08-19 is the real last send. This matters because
the sender's "not contacted in the last 60 days" window counts from these rows.

**Corrected 2026-09-23 — the source is not gone.** Neither file was committed to
git, but both were in iCloud and are now at `referral-os/outreach_email.py`
(37 KB) and `referral-os/dashboard/streamlit_app.py`. The reimplementation at
`workers/sunmoon-outreach/` stands on its own merits — it is a Worker on a cron
rather than a script on a laptop — but it is no longer the only copy, and the
original is worth reading for the `claude-haiku-4-5` email bodies the rewrite
deliberately dropped.

Separately, four Cloudflare Workers were running the live site, booking flow,
tracking and webhooks. None of them were in git either; the only copies were
the deployed bundles. They have now been recovered from Cloudflare and
committed here.

## The four systems

| Directory | Worker | Role | Datastore |
|---|---|---|---|
| `workers/sunmoon-booking` | `sunmoon-booking` | `book.sunandmoon30a.com` — quotes, Stripe, reservations, admin | Supabase |
| `workers/sunandmoon-tracking` | `sunandmoon-tracking` | `track.sunandmoon30a.com` — open pixel, click redirect, contact form, ops dashboard | Supabase |
| `workers/sunmoon-hooks` | `sunmoon-hooks` | Hostaway webhooks, iCal cron sync, direct booking requests | **Cloudflare D1** (`sunmoon-db`) |
| `workers/sunmoon-outreach` | *(new)* | Referral outreach sender, cron-driven | Supabase |

A fifth worker, `sunandmoonhome` (the marketing site), serves static assets;
the API returned no fetchable script, so it is **not** recovered here.

### Provenance and limits of the recovered code

`sunmoon-booking`, `sunandmoon-tracking` and `sunmoon-hooks` were pulled from
the deployed Cloudflare bundles. They are **esbuild output**, not the original
TypeScript — readable and syntactically valid (all three pass `node --check`),
but they carry bundler artifacts (`__name`, `__defProp`).

**Corrected 2026-09-23 — two of the three originals are back.** They came out of
iCloud with zero bundler artifacts:

| Worker | Original source | Bundle under `workers/` |
|---|---|---|
| `sunandmoon-tracking` | `referral-os/workers/tracking_worker.js` — 24,474 B | 21,546 B, 19 artifacts |
| `sunmoon-booking` | `booking-os/worker.js` — 177,973 B | 164,856 B, 67 artifacts |

They are deliberately left where they landed rather than swapped over the
bundles under `workers/`, because `wrangler.toml` points `main` at those paths
and the substitution changes what gets deployed. Diff them, deploy to a preview,
then promote. **`sunmoon-hooks` has no original anywhere in the tree** and its
`src/*.ts` really is still lost. Treat these as a recovery point that stops the
code from disappearing, not as a pristine source tree. The sourcemaps
(`*.js.map`) referenced at the bottom of each file were not retrievable.

Because these are the deployed artifacts, they should round-trip: redeploying
them reproduces current behaviour. Verify in a preview environment before
pushing to production.

## Two booking systems, two databases

Worth knowing before touching anything:

- `sunmoon-hooks` writes reservations to **Cloudflare D1** (`sunmoon-db`),
  driven by Hostaway webhooks and iCal feeds, and emails through Resend.
- `sunmoon-booking` writes reservations to **Supabase**, driven by its own
  Stripe checkout flow.

These are independent and do not reconcile with each other. The Supabase
`reservations` table containing only `cancelled` / `blocked` rows is a
consequence of this split, not necessarily a bug in either system.

## Partner attribution

The referral chain works end to end:

1. Email CTA points at `track.sunandmoon30a.com/c/<short_code>`.
2. The tracking worker logs `email_click` and 302s to the link's
   `destination_url`, which carries `?partner=<slug>`.
3. `sunmoon-booking` reads `params.partner`, renders the partner banner,
   applies the guest discount, and threads the slug through the quote into
   `reservations.partner_slug`.

**One bug was found and fixed** (this commit): the `booking_events` insert for
`date_search` / `no_availability` omitted `partner_slug`, even though
`qt.partner?.slug` was available on the adjacent line and the neighbouring
`price_display_log` insert recorded it. Every other `booking_events` insert
already carried it. The effect was that partner-referred sessions looked
entirely absent from the funnel table while `price_display_log` showed the
truth: 9 partner-attributed quotes across 3 partners
(`the-treasury-on-the-plaza`, `avenue-d-events`, `fervent-designs-llc`).

### Known remaining gap

The `confirmed` event emitted from the Stripe webhook writes
`session_id: null`, because the `reservations` row it is built from carries no
session. Tying confirmations back to a browsing session needs a schema change
(persist `session_id` on `reservations`) and was left alone.

## The new sender

`workers/sunmoon-outreach` is a reimplementation, reconstructed from the
surviving `outreach`, `tracking_links` and `campaigns` rows.

Behaviour it reproduces:

- reads `v_outreach_ready` (has an email, segment status `prospect`, not
  emailed in the last 60 days),
- filters `outreach_suppression`, de-dupes to one contact per company,
- caps at 25 first-touch emails per weekday run,
- mints a `rev_share_partners` slug per company (with the `-2`, `-3` suffix
  convention on collision), 5% guest discount / 10% revenue share,
- inserts the `outreach` row first so its id can be baked into the open pixel
  at `track.sunandmoon30a.com/o/<outreach_id>`,
- mints a `tracking_links` row whose destination carries
  `?partner=<slug>&utm_source=referral-os&utm_medium=email&utm_campaign=<campaign>&utm_content=<short_code>`,
- sends through Resend, marks the row `sent`, and flips
  `company_segments.status` from `prospect` to `contacted`.

**Deliberate difference:** the old sender generated each email body with
`claude-haiku-4-5` (visible in the `model` key of surviving `outreach.metadata`).
This one uses a deterministic template with the same structure and the same
merge fields (company, metro, rating, review count, segment angle). No
Anthropic API key, no per-send cost, no generation failure mode. If you want
LLM-written bodies back, that is an additive change to `renderEmail()`.

**It ships with `DRY_RUN = "true"`.** It will not send until that is flipped.

### Deploying it

```bash
cd workers/sunmoon-outreach
wrangler secret put SUPABASE_URL
wrangler secret put SUPABASE_SERVICE_KEY
wrangler secret put RESEND_API_KEY
wrangler secret put ADMIN_TOKEN
# set PROJECT_ID in wrangler.toml [vars] first
wrangler deploy

# dry-run preview of the next 5 recipients, no email sent:
curl "https://sunmoon-outreach.<subdomain>.workers.dev/preview?token=$ADMIN_TOKEN&limit=5"

# when the preview looks right: set DRY_RUN="false" in wrangler.toml, redeploy,
# then force one run outside the cron schedule with a small cap:
curl -X POST "https://sunmoon-outreach.<subdomain>.workers.dev/run?token=$ADMIN_TOKEN&cap=3"
```

Start with `cap=3` and confirm in Supabase that the `outreach` rows land as
`sent`, the `tracking_links` rows exist, and the pixel/click URLs resolve.
Only then let the weekday cron run at the full 25.

## Current runway

At the time of recovery: **292 companies** eligible to email (522 contacts,
de-duped to one per company), roughly 12 business days at 25/day. A further
~568 companies exist with no contact email and would need enrichment.

## Where the company list actually came from

Not stated in the original write-up, and worth recording. Of the 1,143 companies
in Supabase, **all 1,143 carry a `google_place_id` and `data_sources =
["google_places"]`**, loaded 2026-05-22 → 06-17. The 901 contact emails came
from a single `website_scrape` batch on 2026-05-30. Both scripts are now in the
repo: the loaders are `referral-os/ingest_*.py`, the scraper is
`referral-os/enrich_layer1_websites.py`.

### The Apollo half was built and never run

`referral-os/ingest_all.py` defines 17 segments across two sources — 12 via
Google Places (supply-side, 30A hyperlocal) and 4 via Apollo (demand-side
drive-markets). `referral-os/ingest_apollo_wedding_atlanta.py` is a standalone
smoke test, and `.env` carries a live `APOLLO_API_KEY`.

None of it ever wrote. The Apollo path sets `apollo_organization_id` and
`data_sources = ["apollo"]`; the database has **zero** rows with either, and the
smoke test required an explicit `--write` that was never passed.

| Apollo segment | Tier | Companies |
|---|---|---|
| `wedding-planner-feeder` | 1 | 467 — but filled from Google Places instead |
| `corp-retreat-planner` | 1 | **0** |
| `travel-advisor` | 2 | **0** |
| `family-reunion-planner` | 2 | **0** |

Three demand-side segments, one of them tier 1, are coded and funded and empty.
That is also the most promising route to the 568 companies that have no contact
email. (`ingest_all.py`'s header says five Apollo segments; only four are
defined.)

## What is still not in git

- Original TypeScript for `sunmoon-hooks`. The other two workers' sources were
  recovered — see the corrected provenance section above.
- The `sunandmoonhome` marketing site worker.
- `tests/test_availability.py`, which exists only on the two
  `origin/claude/*availability*` branches. It is the only test file in the
  project and was written against an August `availability.py`, so it may need
  updating before it passes.

## Secrets

`referral-os/.env`, four `.env.bak-*` and `booking-os/.dev.vars` came with the
recovered tree and hold live values — Stripe, Supabase service role, Gmail app
password, Twilio, Anthropic, Apollo, Google Places. They are kept **outside the
repo** and are covered by both `.gitignore` and `.assetsignore`.

`.assetsignore` is the one that matters most: `wrangler.jsonc` publishes assets
from `"."`, the whole repo root, and wrangler uploads from the filesystem rather
than from git. A gitignored directory sitting in the working tree is still
served unless `.assetsignore` excludes it.
