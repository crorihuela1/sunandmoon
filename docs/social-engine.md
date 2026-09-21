# Sun & Moon 30A — Social Content Engine

Daily, automated Instagram + Facebook posts for **sunandmoon30a.com**, driven by
what is actually open on the booking calendar and what is actually happening
on 30A that week.

```
book.sunandmoon30a.com/availability      events.html (kept current by the
(live Vacay data, public, no key)         events-refresh routine)
        │                                        │
        ▼                                        ▼
  availability.py ──► promotable stays ──► content_engine.py ──► briefs
  (open windows,       (Fri–Sun, Thu–Sun,        │
   scored)              event weekends)          ▼
                                          copywriter.py (Claude writes
                                          IG + FB captions from the brief;
                                          template copy if no API key)
                                                 │
                                                 ▼
                                   publishers/meta.py  (only `active` platforms
                                   with secrets present; dry-run otherwise)
                                                 │
                                                 ▼
                                   notifier.py → experience@sunandmoon30a.com
                                   (daily digest, new-booking + check-in alerts)
```

Runs every morning from `.github/workflows/social-engine.yml` (9:00 AM ET).
One queue file per day lands in `queue/YYYY-MM-DD.json` (what was planned)
and `queue/YYYY-MM-DD.results.json` (what happened). `state/known_busy.json`
is the last-seen calendar, used to detect new bookings.

## What a day looks like

1. **Read availability** for Golden Sun, Blue Moon and the whole property.
2. **Slice open windows into stays worth posting** — a 42-night off-season
   stretch is not a hook; the Halloween long weekend inside it is. Stays are
   scored: Fri/Sat nights, holidays, near-term dates, and gap nights between
   bookings score highest; a stay wrapped around a 30A event gets a bonus.
3. **Pick today's pillar** (deterministic rotation by date, weights in
   `config/brand.yaml`): availability spotlight 4/10, local events 3/10,
   evergreen (local guide / behind the scenes / guest love) 3/10. Evergreen
   days still lead with real dates; the evergreen prompt goes to the digest
   as a to-do for a human photo.
4. **Build 1–2 briefs**: which cottage, which dates, which event, which photo.
   Two briefs never cover the same weekend, and the second brief prefers the
   other cottage. If both cottages are open on the exact same dates the post
   upgrades to "the whole property".
5. **Claude writes the captions** (`claude-opus-5`, structured JSON: Instagram
   caption, Facebook caption, hashtags, alt text) from the brief and the
   verified facts in `config/brand.yaml`. It is told never to invent prices,
   amenities or distances. No API key → template copy.
6. **Publish** to every platform that is `active` in `config/apis.yaml` and
   whose secrets are present. Instagram: two-step container → publish with a
   public JPEG from the website. Facebook: `/photos` with the caption.
7. **Email the digest** (what was posted, top stays, upcoming events) plus
   any new-booking / imminent check-in alerts.

## Local run

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cd src
../.venv/bin/python -m sunmoon_social status     # which platforms are live
../.venv/bin/python -m sunmoon_social plan       # today's queue (writes queue/)
../.venv/bin/python -m sunmoon_social publish    # dry-run: prints previews
../.venv/bin/python -m sunmoon_social digest     # dry-run: prints the email
```

Set `ANTHROPIC_API_KEY` to see Claude-written captions locally. Nothing posts
or emails unless `SOCIAL_LIVE=true` **and** `--live` are both set.

## Config

| File | What it controls |
|---|---|
| `config/calendars.yaml` | availability sources per unit, events source, scoring weights |
| `config/brand.yaml` | voice, verified facts, photo pools, hashtags, pillar weights, rates |
| `config/apis.yaml` | platform registry + lifecycle (`planned → … → active`) |
| `config/notifications.yaml` | digest/alert recipients |

To feature a nightly rate in availability posts, set `rates.sun` / `rates.moon`
/ `rates.full` in `brand.yaml`. Leave `null` to keep pricing out of copy.

## Go-live checklist

Everything below is a one-time setup. Steps 1–2 are things only the account
owner can do (they involve credentials).

### 1. Meta credentials (Instagram + Facebook, one app)

Prerequisites: a Facebook Page for Sun & Moon 30A, and the
`@sunandmoon30a` Instagram account switched to a **Business** (or Creator)
account and **connected to that Page** (Instagram app → Settings → Business
tools → Connect a Facebook Page).

1. Go to https://developers.facebook.com/apps/ → **Create app** → type
   *Business* → name it `Sun & Moon 30A Engine`.
2. In the app dashboard add the **Instagram** product ("Instagram API with
   Facebook Login") and **Facebook Login for Business**.
3. Open https://developers.facebook.com/tools/explorer/ → pick the app →
   **User or Page** → *Get User Access Token* with these permissions:
   `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`,
   `instagram_basic`, `instagram_content_publish`, `business_management`.
   Approve the dialog (choose the Page + the Instagram account).
4. Exchange it for a **long-lived** user token (60 days), then fetch the
   **Page token**, which does not expire:
   ```
   GET /oauth/access_token?grant_type=fb_exchange_token
       &client_id=APP_ID&client_secret=APP_SECRET&fb_exchange_token=SHORT_TOKEN
   GET /me/accounts?access_token=LONG_LIVED_USER_TOKEN
   ```
   `/me/accounts` returns each Page's `id` (→ `FB_PAGE_ID`) and
   `access_token` (→ `META_ACCESS_TOKEN`).
5. Get the Instagram business account id:
   ```
   GET /{FB_PAGE_ID}?fields=instagram_business_account&access_token=META_ACCESS_TOKEN
   ```
   → `IG_BUSINESS_ACCOUNT_ID`.
6. The app can stay in **Development** mode: publishing to a Page and an
   Instagram account you administer works for anyone with a role on the app
   (you). App Review is only needed to publish on behalf of *other* people's
   accounts.

Sanity-check the token before wiring it in:
```
GET /debug_token?input_token=META_ACCESS_TOKEN&access_token=META_ACCESS_TOKEN
```
should show `"expires_at": 0` and the scopes above.

### 2. GitHub repository secrets and variable

Repo → Settings → Secrets and variables → Actions.

**Secrets**

| Name | Value |
|---|---|
| `META_ACCESS_TOKEN` | Page token from step 1.4 |
| `FB_PAGE_ID` | from step 1.4 |
| `IG_BUSINESS_ACCOUNT_ID` | from step 1.5 |
| `ANTHROPIC_API_KEY` | from https://console.anthropic.com/ — Claude writes the captions (template copy without it) |
| `RESEND_API_KEY` | the same Resend key the `sunmoon-hooks` worker uses — for the digest/alert emails (optional; `SMTP_*` also works) |

**Variable**

| Name | Value |
|---|---|
| `SOCIAL_LIVE` | `false` while vetting → `true` to go live |

### 3. First real run

1. Merge this branch to `main` (scheduled workflows only run from the
   default branch).
2. Actions → *social-engine* → **Run workflow** with `live` unchecked. Read
   the log: `status` should show `meta_instagram LIVE` / `meta_facebook LIVE`,
   and the previews should be today's real dates.
3. Set `SOCIAL_LIVE=true`. Run the workflow once more with `live` checked.
   Check the Instagram feed and the Facebook Page; the digest lands in
   `experience@sunandmoon30a.com`.
4. Done — it runs by itself at 9 AM ET every day. To pause: set
   `SOCIAL_LIVE=false` (everything keeps running as a dry-run) or set a
   platform to `status: paused` in `config/apis.yaml`.

### Optional — stop daily engine commits from redeploying the website

The engine commits `state/` and `queue/` to `main` every morning. Cloudflare
Workers Builds will see a push and redeploy the (unchanged) site. Harmless,
but to skip it: Cloudflare dashboard → the `sunandmoonhome` Worker → Settings
→ Builds → **Build watch paths** → exclude `state/*`, `queue/*`.

## Adding a platform

See `docs/api-activation-checklist.md`. Short version: create the app, add
the secrets under the names in `config/apis.yaml`, run one test post,
flip `status: active`. Publishers for X, Pinterest, Threads and Buffer are
already implemented; TikTok, YouTube, Google Business Profile, LinkedIn and
Nextdoor are registered stubs.

## Safety rails

- Posts never claim dates are open unless the live booking API says so. If
  every availability source fails, the unit is treated as *unknown*, not open.
- Claude only gets the facts in `brand.yaml` and the day's brief; it is
  instructed not to invent prices, amenities or distances. Anything new you
  want it to be able to say goes in `facts:`.
- Evergreen pillars that need a real photo (behind the scenes, guest love)
  are never auto-published — they land in the digest as a prompt.
- `SOCIAL_LIVE` is a repository variable, not code: flipping it to `false`
  pauses everything instantly without a deploy.
