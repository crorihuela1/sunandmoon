# Reachouts — the Pricing AI Agent play

Selling a **pricing AI agent** to independent restaurants. First-touch email
gives a real *taste*: 2 concrete pricing fixes the agent already found in that
restaurant's menu, with a conservative $/yr estimate. The offer (free 3 months,
then $39.99/mo) is the P.S. — the recommendation is the pitch.

## Two markets / positionings (`--region`)

| `--region` | Project | Voice | Sends as |
|---|---|---|---|
| `atlanta` | atlanta-restaurants-b2b | **Cold** — stranger selling a SaaS | cristian@getprix.ai (placeholder) |
| `30a` (default) | 30a-restaurants | **Local** — Cristian as a 30A neighbor who runs Sun & Moon and already refers guests | **experience@sunandmoon30a.com** |

The 30A version opens as a neighbor ("I send guests asking *where should we eat?*
to spots like yours all the time"), which is why it converts better than cold.
Both share the same pricing brain and the same $-credibility guardrails. The
benchmark market label ("30A" vs "Atlanta") is set per region.

## The two pieces

```
pricing_agent.py          The product brain. analyze(restaurant) → recommendations
                          (charm pricing, underpriced anchor, missing premium
                          anchor, tier-vs-Atlanta-benchmark, compression) with
                          transparent, conservative $ upside. Stdlib, deterministic.
outreach_restaurants.py   Cold-email generator in Cristian's voice. Uses the agent
                          for the taste. Sample mode + --from-db; dry-run default;
                          Gmail --send path (mirrors outreach_email.py safety).
sample_restaurant_reachouts.md / .html   5 ready-to-read example emails.
```

## How the "taste" stays believable

- Headline $ = only the **two recommendations actually shown**, not a sum of
  everything (honest + consistent with "just the two that jumped out").
- Volume is proxied from Google review count, **scaled down by $-tier** (fine
  dining turns fewer covers), with conservative multipliers.
- Numbers are **rounded** ($4,500, not $4,569) so they read as estimates.
- Restaurants with no scraped menu get a credible **tier-level** taste + an
  invite to connect their menu for the itemized version.

## Configure the product in one place

Edit the `PRODUCT` dict at the top of `outreach_restaurants.py`:

```python
PRODUCT = {
  "name": "Prix",                      # ← your real product name
  "tagline": "a pricing AI agent for independent restaurants",
  "free_months": 3,
  "price_monthly": 39.99,
  "sender_name": "Cristian Orihuela",
  "sender_email": "cristian@getprix.ai",   # ← placeholder — set your real address
  "report_base": "https://getprix.ai/r",
}
```

> `name` ("Prix"), `sender_email`, and `report_base` are placeholders I picked —
> swap in your real brand before sending.

## Run it — full 30A pipeline (on your Mac; sandbox can't reach Google/Gmail)

```bash
cd "/Users/samantha/Documents/Claude/Projects/Sun & Moon at 30a/referral-os"

# 0. one-time: apply migrations 008 + 009 in Supabase

# 1. preview the emails with no DB/network (writes sample_30a_*.md/.html)
python3 outreach_restaurants.py --region 30a

# 2. load real 30A restaurants, menus, and contact emails
python3 ingest_30a_restaurants.py
python3 scrape_restaurant_menus.py  --project 30a-restaurants --limit 25
python3 find_restaurant_emails.py   --project 30a-restaurants --limit 80

# 3. stage the batch for review — writes send_queue_30a.csv, sends nothing
python3 outreach_restaurants.py --region 30a --from-db --limit 25

# 4. proof to yourself + Mariana first
python3 outreach_restaurants.py --region 30a --from-db --send --test-self \
        --cc marianaorihuela@gmail.com --limit 3

# 5. real send (asks you to type 'send' to confirm), CC Mariana
python3 outreach_restaurants.py --region 30a --from-db --send \
        --cc marianaorihuela@gmail.com --limit 25
```

### Send safety (built in)

- **Review queue:** every run writes `send_queue_30a.csv` (status, recipient,
  subject, full body, est. upside) — open it before sending.
- **Confirm guard:** a real send (not `--test-self`) makes you type `send` to
  proceed. Add `--yes` to skip once you trust it.
- **Dedupe:** each successful send is logged to `sent_30a_log.csv`; those
  addresses are skipped on later runs (no double-emailing).
- **Opt-outs:** put any "unsubscribe" addresses in `suppress_30a.txt` (one per
  line) — they're skipped automatically.
- Rows with no email on file are skipped and listed (call those).

Default is **dry-run** — nothing sends without `--send`. The email goes out
**From `experience@sunandmoon30a.com`** (set in the `30a_local` positioning);
SMTP authenticates with your `GMAIL_USER` + `GMAIL_APP_PASSWORD`. For the From to
literally be experience@, that address must be your `GMAIL_USER` (Workspace
mailbox) or a verified "Send mail as" alias on the Gmail account. Recipient
emails come from `find_restaurant_emails.py`; restaurants with none are skipped
(call them — phone is on file).

## Honest caveats before you ship

- The $ upside is a **directional estimate** from public data, not an audit.
  Keep it framed as "found ~$X" / "conservatively." Don't over-claim.
- Charm-pricing and anchor effects are well-supported in menu-engineering
  research but results vary; the agent proposes **testable** moves, which is the
  right posture.
- Cold email to businesses: include a real reply-to and an unsubscribe path, and
  check CAN-SPAM basics before volume sending.
