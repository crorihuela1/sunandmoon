# Outreach + Tracking System — Deployment & Daily Operation

This document covers everything needed to (1) deploy the tracking infrastructure to Cloudflare, (2) press one button to queue 3 weeks of personalized outreach, and (3) set the dispatcher to auto-fire the queue via macOS launchd.

---

## What you have now

```
referral-os/
├── outreach_email.py            ← interactive send (single-segment, on-demand)
├── outreach_schedule.py         ← ONE-BUTTON: queue all 3 weeks of emails
├── outreach_dispatch.py         ← cron-fired: sends queued rows when due
├── outreach_voice_samples.md    ← your voice template (Claude reads this)
└── workers/
    ├── tracking_worker.js       ← Cloudflare Worker for open pixel + click tracking
    └── wrangler.toml            ← Worker config
```

Three URLs in play when this is live:
- **`https://sunandmoon30a.com`** — your real website (destination of links in emails)
- **`https://track.sunandmoon30a.com/o/<outreach_id>`** — invisible 1×1 GIF; loaded by recipient = open event
- **`https://track.sunandmoon30a.com/c/<short_code>`** — clicked links; 302-redirect to destination with UTM params

---

## Part 1 — Deploy the Cloudflare tracking Worker (10 min)

You already have Cloudflare set up for `sunandmoon30a.com` (the existing `sunandmoonhome` worker). Adding a second worker on a subdomain.

### Step 1: Install Wrangler (Cloudflare's CLI)

```bash
npm install -g wrangler
wrangler --version    # should print a version like 3.x.x
wrangler login        # opens browser, auth with your Cloudflare account
```

### Step 2: Set secrets

```bash
cd "/Users/samantha/Documents/Claude/Projects/Sun & Moon at 30a/referral-os/workers"

wrangler secret put SUPABASE_URL
# Paste:  https://iwxzishzszducqwxfmbd.supabase.co

wrangler secret put SUPABASE_SERVICE_KEY
# Paste your service role key (the long JWT from your .env)

wrangler secret put PROJECT_ID
# Paste: 310c4ae6-eec9-47b7-9e22-b95a6dc69365
```

### Step 3: Deploy

```bash
wrangler deploy
```

You'll see `Published sunandmoon-tracking` and a `https://sunandmoon-tracking.<your-cf-account>.workers.dev` URL.

### Step 4: Bind to `track.sunandmoon30a.com`

In the Cloudflare dashboard → Workers & Pages → **sunandmoon-tracking** → Settings → Triggers → Add Custom Domain:
- Domain: `track.sunandmoon30a.com`

Cloudflare auto-creates the DNS CNAME for you. Wait ~30 seconds for SSL to provision.

### Step 5: Smoke test

```bash
curl -I "https://track.sunandmoon30a.com/healthz"
# Expect HTTP/2 200, body "ok"
```

If you see `ok` — Worker is live. Tracking infrastructure is ready.

---

## Part 2 — Press one button to queue 3 weeks of email (30 min)

### Step 1: Dry-run preview

```bash
cd "/Users/samantha/Documents/Claude/Projects/Sun & Moon at 30a/referral-os"
python3 outreach_schedule.py --dry-run
```

You'll see the full 3-week schedule:

```
Date (CT)      Time    Segment                    Target
--------------------------------------------------------------------
2026-06-02     09:00   private-chef                   14
2026-06-03     09:00   wedding-planner-local          30
2026-06-04     09:00   wedding-planner-local          50
2026-06-09     09:00   wedding-planner-local          75
2026-06-09     10:00   photographer-family            43
2026-06-10     09:00   wedding-planner-feeder        100
2026-06-11     09:00   wedding-planner-feeder        125
2026-06-16     09:00   wedding-planner-feeder        150
2026-06-17     09:00   wedding-planner-feeder        200
2026-06-18     09:00   bachelorette-planner          150
--------------------------------------------------------------------
TOTAL                                                  937
```

Plus a sample contact for each day so you can confirm the audience is real.

### Step 2: Queue everything (THE ONE BUTTON)

```bash
python3 outreach_schedule.py --queue
```

This generates ALL 937 personalized emails via Claude (about 30 min runtime, ~$2 in Claude API), creates tracking_links per email, embeds the open pixel, and writes everything as `outreach` rows with `status='queued'` and `metadata.scheduled_for=<send time>`.

**After this command finishes, nothing has been sent yet.** Everything is sitting in Postgres ready to go.

### Step 3: Review the queue

In Supabase SQL Editor:

```sql
SELECT
  to_timestamp((metadata->>'scheduled_for')) AS sends_at,
  COUNT(*)                            AS count,
  campaign_id
FROM outreach
WHERE status = 'queued'
GROUP BY 1, 3
ORDER BY 1;
```

You'll see one row per scheduled send time, with counts. Eyeball a few individual emails:

```sql
SELECT subject, body, metadata->>'scheduled_for' AS when_send
FROM outreach
WHERE status = 'queued'
ORDER BY metadata->>'scheduled_for'
LIMIT 5;
```

If anything looks off, fix the voice samples or template, then delete + requeue:

```sql
-- Nuclear option: wipe queued rows + requeue
DELETE FROM outreach WHERE status='queued';
-- Then back to your terminal:
-- python3 outreach_schedule.py --queue
```

---

## Part 3 — Auto-fire the queue with macOS launchd (15 min, one-time)

Once the queue is loaded, you need something to actually fire the emails when their `scheduled_for` time arrives. macOS launchd is the right tool — runs in the background, restarts on reboot, free.

### Step 1: Create the launchd plist

Save this as `~/Library/LaunchAgents/com.sunandmoon.outreach-dispatch.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>com.sunandmoon.outreach-dispatch</string>

    <key>ProgramArguments</key>
    <array>
      <string>/usr/bin/python3</string>
      <string>/Users/samantha/Documents/Claude/Projects/Sun &amp; Moon at 30a/referral-os/outreach_dispatch.py</string>
      <string>--dispatch-due</string>
      <string>--limit</string>
      <string>50</string>
      <string>--pace</string>
      <string>30</string>
    </array>

    <key>WorkingDirectory</key>
    <string>/Users/samantha/Documents/Claude/Projects/Sun &amp; Moon at 30a/referral-os</string>

    <!-- Fire every 5 minutes -->
    <key>StartInterval</key>
    <integer>300</integer>

    <key>StandardOutPath</key>
    <string>/Users/samantha/Documents/Claude/Projects/Sun &amp; Moon at 30a/referral-os/.dispatcher.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/samantha/Documents/Claude/Projects/Sun &amp; Moon at 30a/referral-os/.dispatcher.err</string>

    <key>RunAtLoad</key>
    <false/>
  </dict>
</plist>
```

### Step 2: Activate it

```bash
launchctl load ~/Library/LaunchAgents/com.sunandmoon.outreach-dispatch.plist
launchctl list | grep sunandmoon
```

That second command should print your job. From now on, every 5 minutes macOS will run `outreach_dispatch.py --dispatch-due`, which:
- Queries Postgres for rows where `status='queued'` AND `metadata.scheduled_for <= NOW()`
- Sends each via Gmail SMTP with 30-sec pacing between sends
- Caps at 50 rows per wave (safety)
- Updates the row to `status='sent'`
- Advances `company_segments.status` to `contacted`

### Step 3: Watch it work

```bash
tail -f /Users/samantha/Documents/Claude/Projects/Sun & Moon at 30a/referral-os/.dispatcher.log
```

The first time anything actually sends will be Tuesday 6/2 at 9 AM CT (per the schedule). Before that the log will just show "nothing due" every 5 min.

### To pause dispatching (e.g., emergency stop)

```bash
launchctl unload ~/Library/LaunchAgents/com.sunandmoon.outreach-dispatch.plist
```

Queued rows stay in DB, nothing's lost. Reload to resume.

---

## Part 4 — Verifying tracking actually works

After your first real send wave (Tuesday 6/2):

```sql
-- Opens by company
SELECT c.name, c.metadata->>'market_metro' AS metro,
       COUNT(*) FILTER (WHERE te.event_type='email_open') AS opens,
       COUNT(*) FILTER (WHERE te.event_type='email_click') AS clicks,
       MIN(te.occurred_at) AS first_event
FROM companies c
JOIN tracking_events te ON te.company_id = c.id
GROUP BY c.id, c.name, metro
ORDER BY opens DESC;

-- Engagement funnel
SELECT
  COUNT(DISTINCT outreach.id)                                     AS sent,
  COUNT(DISTINCT outreach.id) FILTER (WHERE opens.contact_id IS NOT NULL)  AS opened,
  COUNT(DISTINCT outreach.id) FILTER (WHERE clicks.contact_id IS NOT NULL) AS clicked,
  COUNT(DISTINCT outreach.id) FILTER (WHERE outreach.replied_at IS NOT NULL) AS replied
FROM outreach
LEFT JOIN tracking_events opens
  ON opens.contact_id = outreach.contact_id
 AND opens.event_type = 'email_open'
LEFT JOIN tracking_events clicks
  ON clicks.contact_id = outreach.contact_id
 AND clicks.event_type = 'email_click'
WHERE outreach.status = 'sent';
```

Healthy numbers for cold outreach at this audience:
- **Sent → Opened**: 35-50% (anything below 20% means deliverability is suffering)
- **Opened → Clicked**: 5-15%
- **Sent → Replied**: 3-10% (the metric that actually matters)

---

## TL;DR — the one-button workflow

```bash
# (one-time setup — Cloudflare, launchd)
cd "/Users/samantha/Documents/Claude/Projects/Sun & Moon at 30a/referral-os/workers" && wrangler deploy
launchctl load ~/Library/LaunchAgents/com.sunandmoon.outreach-dispatch.plist

# (each campaign cycle — ONE button)
cd "/Users/samantha/Documents/Claude/Projects/Sun & Moon at 30a/referral-os"
python3 outreach_schedule.py --dry-run    # verify schedule looks right
python3 outreach_schedule.py --queue      # THE BUTTON — generates + queues all 937 emails

# (then walk away — dispatcher fires them on schedule)
```

That's it. From the moment you run `--queue`, the system handles everything: generation, queueing, pacing, sending, tracking, status updates. All you do is check the engagement dashboard once a day and reply to humans who reply to you.
