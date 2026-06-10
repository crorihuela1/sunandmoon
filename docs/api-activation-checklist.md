# API activation & vetting checklist

How a platform moves from `planned` to `active` in `config/apis.yaml`.
Work the platforms in this order — it front-loads the channels with the best
booking intent for a short-term rental.

**Recommended order:** 1) Google Business Profile, 2) Meta (Instagram +
Facebook + Threads, one app), 3) Pinterest, 4) TikTok, 5) X, 6) YouTube,
7) Nextdoor, 8) LinkedIn. If speed matters more than native control, activate
**Buffer** first — one token posts everywhere while native reviews are pending.

## Lifecycle (every platform)

1. **planned → applied** — create the developer app at the `apply_at` URL in
   `config/apis.yaml`. Use a dedicated `dev@sunandmoon38.com`-style login, not
   a personal account.
2. **applied → keys_received** — when credentials are issued, add them as
   GitHub **Actions secrets** using the exact names in the platform's
   `secrets:` list, and to the workflow env block if new. Never commit keys.
3. **keys_received → vetted** — run the vetting pass:
   - [ ] `python -m sunmoon_social status` shows the platform with all secrets present
   - [ ] one manual test post to a **private/test account** via the adapter
   - [ ] token lifetime understood; refresh documented (note it in `apis.yaml`)
   - [ ] rate limits reviewed against `cadence_per_week`
   - [ ] platform content policy reviewed (e.g., pricing claims, contest rules)
   - [ ] adapter exists in `src/sunmoon_social/publishers/` (implement if it's a stub)
4. **vetted → active** — flip `status: active`, commit, done. The next daily
   run includes the platform automatically.

## Platform notes

- **Meta (Instagram/Facebook/Threads):** one Meta app covers all three.
  Instagram requires a Business account linked to a Facebook Page; image posts
  need a public `media_url` — generate creatives in Canva and export, or host
  images on the website. App Review needed for `instagram_content_publish`.
- **Google Business Profile:** apply for API access (form review takes days to
  weeks — start early). Posts appear directly in Search/Maps, the highest
  booking-intent surface available.
- **Pinterest:** request "standard" access from a business account. Pins are
  evergreen — availability pins should link to the booking page with UTM tags.
- **TikTok:** Content Posting API requires app audit before public posts;
  until then posts land as private drafts — fine for vetting.
- **X:** free tier allows ~500 writes/month, enough for the configured cadence.
- **YouTube:** `videos.insert` costs 1600 quota units (~6 uploads/day on
  default quota) — cadence of 2/week is safe.

## Go-live switches (in order)

1. Calendars wired in `config/calendars.yaml` (see README "Things to plug in").
2. SMTP secrets set → digests/booking alerts flow to `experience@sunandmoon38.com`.
3. Repository **variable** `SOCIAL_LIVE=true` → publishers and emails go live.
   Until then everything runs as a visible dry-run in the Actions log.
