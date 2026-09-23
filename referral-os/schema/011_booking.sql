-- =====================================================================
-- 011: Booking OS — rates & reservations synced from VacayRentalNetwork
--      plus direct reservations, rate adjustments, and rev-share partners.
--
-- Sync source: VRN public API (Hostaway-backed)
--   GET /api/public/properties/{id}/availability?start&end  → blocked ranges
--   GET /api/public/properties/{id}/quote?checkIn&checkOut&guests
--       → nightlyRate, cleaningFee, fees[] (Booking Fee excluded for
--         direct quotes), taxes[] (6% FL sales + 5% Walton TDT on
--         nightly+cleaning)
-- =====================================================================

CREATE TABLE IF NOT EXISTS booking_properties (
    id            TEXT PRIMARY KEY,          -- VRN/Hostaway listing id, e.g. '503319'
    slug          TEXT UNIQUE NOT NULL,      -- 'golden-sun' | 'blue-moon' | 'full-property'
    name          TEXT NOT NULL,
    vrn_url       TEXT NOT NULL,
    active        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO booking_properties (id, slug, name, vrn_url) VALUES
  ('503319', 'golden-sun',   'Golden Sun · 53 Crystal Court',
   'https://www.vacayrentalnetwork.com/property/golden-sun-503319'),
  ('232268', 'blue-moon',    'Blue Moon · 65 Crystal Court',
   'https://www.vacayrentalnetwork.com/property/blue-moon-prime-30a-location-3-minute-walk-to-the--232268'),
  ('507559', 'full-property','Sun & Moon Together · Full Property',
   'https://www.vacayrentalnetwork.com/property/sun-and-moon-at-30a-507559')
ON CONFLICT (id) DO NOTHING;

-- One row per property per date: the synced rate + availability picture
CREATE TABLE IF NOT EXISTS rate_calendar (
    property_id   TEXT NOT NULL REFERENCES booking_properties(id),
    day           DATE NOT NULL,
    nightly_rate  NUMERIC(10,2),             -- VRN base rate (pre-adjustment)
    available     BOOLEAN NOT NULL DEFAULT TRUE,
    block_source  TEXT,                      -- 'vrn_sync' | 'direct' | 'webhook'
    synced_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (property_id, day)
);
CREATE INDEX IF NOT EXISTS idx_rate_calendar_day ON rate_calendar (day);

-- Reservations — both mirrored VRN blocks and our own direct bookings
CREATE TABLE IF NOT EXISTS reservations (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id    TEXT NOT NULL REFERENCES booking_properties(id),
    source         TEXT NOT NULL CHECK (source IN ('vrn_sync','direct','webhook')),
    status         TEXT NOT NULL DEFAULT 'reserved'
                     CHECK (status IN ('blocked','pending','reserved','cancelled')),
    check_in       DATE NOT NULL,
    check_out      DATE NOT NULL,             -- exclusive (departure day)
    guest_name     TEXT,
    guest_email    TEXT,
    guest_phone    TEXT,
    guests         INT,
    -- money (direct reservations)
    nightly_total  NUMERIC(10,2),
    cleaning_fee   NUMERIC(10,2),
    other_fees     JSONB NOT NULL DEFAULT '[]'::jsonb,   -- e.g. damage protection
    taxes          JSONB NOT NULL DEFAULT '[]'::jsonb,   -- [{name, amount}]
    excluded_fees  JSONB NOT NULL DEFAULT '[]'::jsonb,   -- the VRN booking fee we dropped
    total          NUMERIC(10,2),
    currency       TEXT NOT NULL DEFAULT 'USD',
    partner_slug   TEXT,                      -- rev-share partner attribution
    adjustment_pct NUMERIC(6,2) NOT NULL DEFAULT 0,      -- rate adjustment applied
    external_ref   TEXT,                      -- dedupe key for synced/webhook blocks
    notes          TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (property_id, external_ref)
);
CREATE INDEX IF NOT EXISTS idx_reservations_prop_dates ON reservations (property_id, check_in, check_out);
CREATE INDEX IF NOT EXISTS idx_reservations_status     ON reservations (status, created_at DESC);

-- Rate adjustments: global, per-property, or per-partner percentage
CREATE TABLE IF NOT EXISTS rate_adjustments (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scope         TEXT NOT NULL CHECK (scope IN ('global','property','partner')),
    property_id   TEXT REFERENCES booking_properties(id),
    partner_slug  TEXT,
    pct           NUMERIC(6,2) NOT NULL,     -- +10 = raise 10%, -15 = discount 15%
    note          TEXT,
    active        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Rev-share partners (foundation for the partner-pages phase)
CREATE TABLE IF NOT EXISTS rev_share_partners (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug          TEXT UNIQUE NOT NULL,      -- used in URLs: /p/<slug>
    name          TEXT NOT NULL,
    email         TEXT,
    guest_discount_pct NUMERIC(6,2) NOT NULL DEFAULT 0,  -- what their guests save
    rev_share_pct NUMERIC(6,2) NOT NULL DEFAULT 0,       -- partner's cut of bookings
    landing_copy  JSONB NOT NULL DEFAULT '{}'::jsonb,    -- custom webpage content
    active        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Outbound notifications queue (delivered by booking-os/notify_agent.py)
CREATE TABLE IF NOT EXISTS booking_notifications (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    reservation_id UUID REFERENCES reservations(id) ON DELETE CASCADE,
    channel        TEXT NOT NULL CHECK (channel IN ('email','sms')),
    recipient      TEXT NOT NULL,
    subject        TEXT,
    body           TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'queued'
                     CHECK (status IN ('queued','sent','failed')),
    error          TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at        TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_booking_notifications_queued
    ON booking_notifications (created_at) WHERE status = 'queued';

-- Sync audit trail
CREATE TABLE IF NOT EXISTS sync_runs (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trigger      TEXT NOT NULL DEFAULT 'cron',   -- 'cron' | 'manual'
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ,
    ok           BOOLEAN,
    stats        JSONB NOT NULL DEFAULT '{}'::jsonb,
    error        TEXT
);
