# Booking OS

Direct-booking platform for `book.sunandmoon30a.com`. Vacay is the single
source of truth for base rates, fees, taxes, and availability; markup and
coupons apply at the display layer only and never mutate base data.

**Pricing model (2026-07-15): gross-to-gross parity.** Vacay's booking fee is
exactly **8.900% of nightly** (validated across all 3 listings & stay lengths).
We exclude that fee and instead mark up nightly by **8.018%** (= 8.9 ÷ 1.11,
because our markup is taxed at 11% while Vacay's booking fee isn't) — so the
guest's grand total matches Vacay to the penny (±1¢ rounding), and coupons
discount off that parity price. `markup_pct` lives in `booking_config`
(3-decimal precision) and is editable in the admin dashboard.

**Status: platform foundation built & tested locally — NOT deployed.**
Checkout (Stripe) is the next increment and gates the deploy (see below).

## Platform status (increment 2 — 2026-07-11)

| Area | Status |
|---|---|
| **+15% markup** (config-driven, display layer; base untouched) | ✅ built, math-verified |
| **Coupons** (%-off nightly; effective window; stay-date limits; max redemptions; usage tracking; partner-linkable) | ✅ built, all cases tested; admin CRUD |
| **Deposit/balance split** (25% deposit, balance 30d pre-arrival; full if inside 30d) | ✅ computed & stored per booking |
| **Per-booking tax breakdown** (occupancy-tax remittance = a query) | ✅ stored |
| **Sync hardening**: unified ingest, versioned `rate_snapshots`, `sync_audit`, `sync_state` health, **twice-daily validation** vs live Vacay w/ auto-correct + alert, **stale-data circuit breaker** ("call to book") | ✅ built, drift-tested |
| **Webhook** (Vacay→us): shared-secret **+ HMAC** auth, structured logging, `/health` receipt signal, endpoint spec | ✅ built; see `VACAY_INTEGRATION.md` |
| **Phase-1 two-way**: Mindy text+email + owner CC + instant our-side block on confirmed booking | ✅ built (silent until `VACAY_CONTACT_*` set) |
| **Twilio service**: all SMS wrapped; degrades to iMessage/email; live when creds land | ✅ scaffolded (`TWILIO_FROM_NUMBER` pending) |
| **Referral hooks**: `partner_id` on bookings, coupon↔partner link, `{partner}.sunandmoon30a.com` subdomain resolution | ✅ built (no UI, per spec) |
| **Analytics capture**: `/event` funnel endpoint + `booking_events` + abandoned-checkout table | ✅ capture built (dashboard deferred) |
| **Stripe checkout** — Payment Intents, deposit + saved-card **auto-balance 30d pre-arrival**, refunds (admin), **dunning** on failed charges, Stripe webhook (success/fail/dispute/refund), atomic availability re-check + 20-min soft-hold, guest Payment Element + itemized receipt | ✅ built; **server-side lifecycle test-mode verified** (deposit→finalize→balance→refund); guest Payment Element mounts (final card-entry click-through best confirmed live) |
| **Analytics dashboard / GA4** | ⛔ deferred (events are already captured) |
| **Phase-2 outbound block-back** (push booking to Vacay) | ⛔ needs Nick's answer (Hostaway API vs iCal) — see `VACAY_INTEGRATION.md` |
| **Deploy** | ⛔ gated on Stripe test-mode verification |

### Legacy summary
Syncs rates + reservations from Vacay, serves a hidden admin, quotes and takes
direct reservations (taxes + cleaning like Vacay, booking fee excluded), rate
adjustments, and owner email+text notifications.

## How it works

| Piece | What it does |
|---|---|
| `worker.js` | Cloudflare Worker. **Guest booking page at `/`** (public front door → `book.sunandmoon30a.com`), daily cron sync (VRN availability 365d + rate sampling 90d), `/quote`, `/reserve`, `/webhook/reservation` (instant availability updates when a booking closes), hidden `/admin/<token>` dashboard |
| Supabase | `booking_properties`, `rate_calendar`, `reservations`, `rate_adjustments`, `rev_share_partners`, `booking_notifications`, `sync_runs` (migration `referral-os/schema/011_booking.sql`) |
| `notify_agent.py` | Delivers queued notifications: email via Gmail SMTP; SMS via Twilio or carrier gateway (falls back to emailing the owner so nothing is lost). `bash install-launchd.sh` runs it every 5 min |

Data source: VRN's public property API (Hostaway-backed) — the same
quote/availability endpoints the VRN website itself uses. Verified exact:
direct total = VRN total − booking fee, taxes recomputed at VRN's own
effective rates (6% FL sales + 5% Walton TDT on nightly+cleaning).

## Test it locally (no deploy)

```bash
cd booking-os
npx wrangler dev --port 8787        # uses .dev.vars (ADMIN_TOKEN=localtest)

open http://localhost:8787/                          # GUEST booking page
open http://localhost:8787/?property=blue-moon       # pre-selected cottage
open http://localhost:8787/?partner=seaside-weddings # partner deep-link (auto-discount)
open http://localhost:8787/admin/localtest           # admin dashboard
# quote:   curl "http://localhost:8787/quote?property=golden-sun&checkIn=2026-11-02&checkOut=2026-11-06&guests=4"
# reserve: POST /reserve {property, checkIn, checkOut, guests, name, email, phone}
# webhook: POST /webhook/reservation  (header x-webhook-secret: localhook)
python3 notify_agent.py             # deliver queued notifications once
```

## The guest booking page (`/`)

The public front door. Guest picks a cottage → dates → guests → **See price**
(live `/quote`, booking fee stripped, taxes/cleaning shown, savings-vs-VRN
callout) → fills name/email/phone → **Request reservation** (`/reserve`, holds
the dates 48h, fires owner email + text). Brand-matched to sunandmoon30a.com.
Query params: `?property=<slug>` pre-selects a cottage; `?partner=<slug>` shows
the partner welcome banner and auto-applies their guest discount (this is what
the rev-share partner pages will link to).

## Go live (when you're happy)

```bash
cd booking-os
npx wrangler secret put SUPABASE_URL           # from referral-os/.env
npx wrangler secret put SUPABASE_SERVICE_KEY   # from referral-os/.env
npx wrangler secret put ADMIN_TOKEN            # openssl rand -hex 12
npx wrangler secret put WEBHOOK_SECRET         # openssl rand -hex 12
npx wrangler deploy                            # → book.sunandmoon30a.com
bash install-launchd.sh                        # notification delivery every 5 min
```

Admin: `https://book.sunandmoon30a.com/admin/<ADMIN_TOKEN>`

## Settings you'll care about

- **Mindy CC** — `wrangler.toml` → `NOTIFY_MINDY_ENABLED = "false"`. She
  receives NOTHING until you flip this to `"true"` and redeploy.
- **Real SMS to 305-775-3405** — sent as an **iMessage from this Mac's
  Messages.app** (`notify_config.json` → `sms_method: "imessage"`). No accounts
  or fees; requires the Mac awake and signed into iMessage. AT&T retired its
  email-to-SMS gateway (verified: `txt.att.net` bounces), so that path is dead.
  For an always-on cloud path independent of this Mac, add
  `TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN/TWILIO_FROM` to `referral-os/.env` and
  the agent uses Twilio automatically. Either way, if the text ever fails it
  falls back to an `[SMS →]` email so nothing is lost.
- **Disclaimers** — `DISCLAIMERS` at the top of `worker.js`; the cancellation
  policy line is marked `[EDIT ME]`.
- **Rate adjustments & partners** — managed live in the admin dashboard.
- **Daily sync time** — `wrangler.toml` `[triggers]` (09:10 UTC).

## Local test results (2026-07-10)

- Quote Golden Sun 4 nights: $1,648.52 direct vs $1,747.13 VRN — exactly the
  $98.61 booking fee excluded; taxes match VRN to the cent ✅
- −10% global adjustment: taxes recomputed correctly ✅
- Partner discount stacks (−10 global + −5 partner = −15) ✅
- Full sync: 3 properties, 90 rate quotes, 1,095 days, 29 blocked ranges ✅
- Direct reservation → calendar blocked → double-booking refused ✅
- Webhook booked/cancelled: availability flips instantly, bad secret → 401 ✅
- Notifications: owner email + SMS queued & delivered; NO Mindy row while
  the testing switch is off ✅
- Admin cancel → dates released ✅
```
