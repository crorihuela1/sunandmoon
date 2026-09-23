# Tracking Service

Cloudflare Worker that records every partner-attributable touch.

## What it tracks

| Endpoint | When fires | Event written |
|---|---|---|
| `GET /r/:code` | Partner clicks any short link in your email/social | `link_click` |
| `GET /p/:linkId.png` | Email client loads embedded pixel | `email_open` |
| `GET /e/:linkId` | Click on an in-email link (passthrough redirect) | `email_click` |
| `POST /events` | Your website JS fires on form submit / inquiry | `site_visit`, `form_submit`, `booking_inquiry`, … |
| `GET /health` | Uptime check | — |

## Why a Worker (vs. Bitly / Rebrandly / hosted server)

| | Worker | Bitly biz | Self-hosted |
|---|---|---|---|
| Cost @ 100k events/mo | **$0** | $30+ | server $5-20 + ops |
| Custom event types | yes | no | yes |
| Joinable to your DB | yes (Postgres FKs) | no (their walled garden) | yes |
| Subsecond latency global | yes (edge) | yes | depends |

Workers cost $5/mo at 10M req — your volume is rounding error.

## Deploy

```bash
cd tracking
npm install -g wrangler                       # one-time
wrangler login
wrangler secret put SUPABASE_URL              # https://xyz.supabase.co
wrangler secret put SUPABASE_SERVICE_ROLE_KEY # server-only key
wrangler secret put IP_SALT                   # any random 32+ chars
wrangler secret put FALLBACK_REDIRECT         # https://sunandmoon30a.com
wrangler deploy
```

DNS: point `r.sunmoon30a.com` (or whatever subdomain) at the Worker — wrangler walks you through this.

## How short links get created

The pipeline calls the Postgres RPC `mint_tracking_link(...)` for each (partner × campaign) pair. The function returns the short code; you paste it into the partner's email/DM as `https://r.sunmoon30a.com/r/{code}`.

Example: minting links for every "wedding-planner-local" partner for the "Q3 referral kickoff" campaign:

```sql
SELECT mint_tracking_link(
    p.id,
    c.id,
    (SELECT id FROM contacts WHERE company_id = c.id AND is_decision_maker LIMIT 1),
    (SELECT id FROM campaigns WHERE slug = 'q3-referral-kickoff' AND project_id = p.id),
    'https://sunandmoon30a.com/partners?co=' || c.id,
    'referral', 'email', 'q3-kickoff', s.slug
)
FROM companies c
JOIN company_segments cs ON cs.company_id = c.id
JOIN segments s ON s.id = cs.segment_id
JOIN projects p ON p.id = s.project_id
WHERE s.slug = 'wedding-planner-local' AND p.slug = 'sun-moon-30a';
```

## Privacy

IP addresses are SHA-256 hashed with a daily-rotatable salt before storage — you get bot-detection and rough geo without keeping raw PII. Add a privacy line to your site footer disclosing tracking.

## Volume budget

Free Workers tier: 100k requests/day. Even with the entire 2,000-partner list, you'll be at 20-50k events/month. Free tier handles this 60x over.
