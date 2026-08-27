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
   Every feed is normalised to the property's timezone (`property.timezone`,
   default `America/Chicago`) before it becomes a night, so an all-day OTA
   export and a UTC-timestamped booking-engine export describe the same stay.
   If a feed cannot be read, that unit is reported as *availability unknown* —
   never as open — so a calendar outage can't advertise a sold night. List every
   listing for a unit and the engine cross-checks them: any night the site feed
   and the vacay-network feed disagree about is reported (email alert + daily
   digest + `sunmoon_social sync`) instead of being merged away silently.
3. **Content engine** — generates a queue of posts from the brand pillars in
   `config/brand.yaml`, targeted at the highest-value open dates and at the
   events/experiences hosted at the property.
4. **Publishers** — one thin adapter per platform API. Dry-run by default;
   set `SOCIAL_LIVE=true` to post for real.
5. **Notifier** — emails imminent info (new bookings detected as new busy blocks,
   calendar mismatches between a unit's listings, plus a daily digest of what
   was published and what's open) to
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

python -m sunmoon_social sync   # do the site and the vacay listing agree?

python -m unittest discover -s tests -t .   # date-handling regressions
```

## Activating a platform

See `docs/api-activation-checklist.md` for the per-platform walkthrough.
Short version:

1. Apply for the API / create the app (links in `config/apis.yaml`).
2. Put credentials in repository **Actions secrets** (names listed per platform).
3. Run the vetting checklist (test post, token refresh, rate limits).
4. Set `status: active` in `config/apis.yaml` and commit.

## Things to plug in (current gaps)

- **No calendar source is wired up yet**, so both units report *availability
  unknown* every run and nothing gets promoted. sunandmoon30a.com resolves now
  (checked 2026-08-23); set the booking engine's iCal export URL — and the
  Airbnb/VRBO export for the same listing — in `config/calendars.yaml`. Listing
  both is what keeps the site and the vacay listing showing the same dates —
  and `python -m sunmoon_social sync` will tell you straight away whether they do
  (exit code 0 = the feeds agree).
- No "Sun"/"Moon" calendars exist in the connected Google account yet. Either
  create them (and share with the engine), or use the iCal export URLs from the
  booking platform (Airbnb/VRBO/Lodgify/OwnerRez all provide one per listing).
- SMTP credentials for the notifier (`SMTP_HOST/PORT/USER/PASS` in secrets).
- The CC recipient for booking alerts defaults to `corihuela@gmail.com` —
  adjust in `config/notifications.yaml` if a different mailbox was intended.
