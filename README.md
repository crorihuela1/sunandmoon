# Sun & Moon 30A — Social Content Engine

Automated, calendar-aware social media marketing for **sunandmoon30a.com**.
The engine reads availability from the property calendars, generates methodical
brand-building content for every connected platform, publishes it through each
platform's API **only after that API has been vetted and activated**, and emails
booking/activity alerts to `experience@sunandmoon30a.com`.

## How it works

```
calendars (iCal / Google Calendar)          config/apis.yaml (API registry)
        │                                            │
        ▼                                            ▼
  availability.py ──► content_engine.py ──► publishers/* (only status: active)
        │                     │
        ▼                     ▼
   open-date scoring     content queue (queue/YYYY-MM-DD.json)
        │
        ▼
   notifier.py ──► experience@sunandmoon30a.com (+ CC) for new bookings & daily digest
```

1. **API registry** — `config/apis.yaml` lists every platform API with a status
   lifecycle: `planned → applied → keys_received → vetted → active`.
   A platform is *never* posted to until its status is `active`. Flip the status
   and add the secret, and the channel turns on — no code changes.
2. **Availability** — `config/calendars.yaml` points at the Sun unit and Moon
   unit calendars (iCal export URLs and/or Google Calendar IDs). The engine
   computes open windows for *each unit and for both together*, and scores them
   (weekends, holidays, near-term gaps score highest) so promotion is intentional.
3. **Content engine** — generates a queue of posts from the brand pillars in
   `config/brand.yaml`, targeted at the highest-value open dates and at the
   events/experiences hosted at the property.
4. **Publishers** — one thin adapter per platform API. Dry-run by default;
   set `SOCIAL_LIVE=true` to post for real.
5. **Notifier** — emails imminent info (new bookings detected as new busy blocks,
   plus a daily digest of what was published and what's open) to
   `experience@sunandmoon30a.com` with a CC (see `config/notifications.yaml`).
6. **Automation** — `.github/workflows/social-engine.yml` runs the whole loop
   daily with zero manual intervention.

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env            # fill in secrets as APIs are activated
python -m sunmoon_social plan   # build today's content queue (no posting)
python -m sunmoon_social publish --dry-run
python -m sunmoon_social digest --dry-run
```

## Activating a platform

See `docs/api-activation-checklist.md` for the per-platform walkthrough.
Short version:

1. Apply for the API / create the app (links in `config/apis.yaml`).
2. Put credentials in repository **Actions secrets** (names listed per platform).
3. Run the vetting checklist (test post, token refresh, rate limits).
4. Set `status: active` in `config/apis.yaml` and commit.

## Things to plug in (current gaps)

- **The engine has published nothing since 2026-06-16.** Every daily run builds
  an empty queue because no calendar source and no `active` platform is
  configured — the two items below. A no-op run now prints `!!` warnings in the
  workflow log instead of exiting quietly green.
- **No calendar is wired up**, so availability is unknown and no availability
  post can be generated. Set at least one iCal URL in `config/calendars.yaml`.
  sunandmoon30a.com *does* resolve in public DNS (checked 2026-08-23; the
  earlier "no DNS yet" note was stale), but the site could not be reached from
  CI to confirm it exposes a calendar feed. Airbnb/VRBO/Lodgify/OwnerRez each
  provide one iCal export URL per listing and work equally well.
- No "Sun"/"Moon" calendars exist in the connected Google account yet. Either
  create them (and share with the engine), or use the iCal export URLs from the
  booking platform (Airbnb/VRBO/Lodgify/OwnerRez all provide one per listing).
- SMTP credentials for the notifier (`SMTP_HOST/PORT/USER/PASS` in secrets).
- The CC recipient for booking alerts defaults to `corihuela@gmail.com` —
  adjust in `config/notifications.yaml` if a different mailbox was intended.
