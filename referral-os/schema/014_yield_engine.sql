-- ============================================================
-- 014 — Yield engine (occupancy-first pricing layer)
-- Anchor price = Vacay base × (1 + markup_pct). Yield rules apply
-- ON TOP at display time; synced base data is never mutated.
-- ============================================================

-- Per-property yield configuration (property_id NULL = global default).
CREATE TABLE IF NOT EXISTS yield_rules (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id   TEXT UNIQUE REFERENCES booking_properties(id),  -- NULL = default row
    enabled       BOOLEAN NOT NULL DEFAULT TRUE,
    -- Near-date discount ladder: deepest matching window wins.
    ladder        JSONB NOT NULL DEFAULT '[{"days":14,"pct":10},{"days":7,"pct":15},{"days":3,"pct":20}]'::jsonb,
    -- Orphan-gap filling (1..gap_max_nights between blocked spans)
    gap_enabled       BOOLEAN NOT NULL DEFAULT TRUE,
    gap_max_nights    INT     NOT NULL DEFAULT 3,
    gap_discount_pct  NUMERIC(5,2) NOT NULL DEFAULT 15,
    gap_relax_min_stay BOOLEAN NOT NULL DEFAULT TRUE,
    -- "Book now" lock-in offers
    lockin_enabled    BOOLEAN NOT NULL DEFAULT TRUE,
    lockin_hours      INT     NOT NULL DEFAULT 4,
    lockin_extra_pct  NUMERIC(5,2) NOT NULL DEFAULT 5,
    -- Price floor as % of Vacay base (100 = never below base). Per-date
    -- overrides live in yield_floor_overrides.
    floor_pct     NUMERIC(6,2) NOT NULL DEFAULT 100,
    -- A/B test: % of sessions bucketed to variant B with alternate ladder.
    ab_split_pct  INT NOT NULL DEFAULT 0,
    ab_variant_ladder JSONB,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO yield_rules (property_id) VALUES (NULL) ON CONFLICT DO NOTHING;

-- Admin-set floor overrides for specific date ranges (owner's call only —
-- e.g. allow 90% of base to fill a dead week; never automatic).
CREATE TABLE IF NOT EXISTS yield_floor_overrides (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id  TEXT REFERENCES booking_properties(id),  -- NULL = all
    start_date   DATE NOT NULL,
    end_date     DATE NOT NULL,                           -- inclusive
    floor_pct    NUMERIC(6,2) NOT NULL,                   -- % of base, may be <100
    note         TEXT,
    active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_floor_overrides ON yield_floor_overrides (property_id, start_date, end_date) WHERE active;

-- Peak dates exempt from ALL automatic discounting (ladder/gap/lock-in).
CREATE TABLE IF NOT EXISTS protected_dates (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id  TEXT REFERENCES booking_properties(id),  -- NULL = all
    start_date   DATE NOT NULL,
    end_date     DATE NOT NULL,                           -- inclusive
    note         TEXT,
    active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_protected_dates ON protected_dates (property_id, start_date, end_date) WHERE active;

-- One honest, time-limited "book now" offer per session.
CREATE TABLE IF NOT EXISTS lockin_offers (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id     TEXT NOT NULL,
    property_id    TEXT NOT NULL REFERENCES booking_properties(id),
    check_in       DATE NOT NULL,
    check_out      DATE NOT NULL,
    pct            NUMERIC(5,2) NOT NULL,
    trigger_reason TEXT,                 -- repeat_views | abandoned_checkout | nearby_unavailable
    expires_at     TIMESTAMPTZ NOT NULL,
    status         TEXT NOT NULL DEFAULT 'active',   -- active | expired | redeemed
    reservation_id UUID REFERENCES reservations(id),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_lockin_one_per_session ON lockin_offers (session_id);

-- Audit: every displayed price, with the full adjustment breakdown.
-- Answers "what price did we show and why" alongside rate_snapshots (base).
CREATE TABLE IF NOT EXISTS price_display_log (
    id           BIGSERIAL PRIMARY KEY,
    at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    session_id   TEXT,
    property_id  TEXT NOT NULL,
    check_in     DATE NOT NULL,
    check_out    DATE NOT NULL,
    ab_bucket    TEXT NOT NULL DEFAULT 'A',
    base_nightly_total      NUMERIC(10,2) NOT NULL,   -- Vacay base, untouched
    displayed_nightly_total NUMERIC(10,2) NOT NULL,   -- after markup + yield + coupon
    total        NUMERIC(10,2) NOT NULL,
    adjustments  JSONB NOT NULL DEFAULT '[]'::jsonb,  -- [{rule,pct,amount}] in application order
    context      JSONB NOT NULL DEFAULT '{}'::jsonb   -- {guests, coupon, partner, nights}
);
CREATE INDEX IF NOT EXISTS idx_price_log_stay ON price_display_log (property_id, check_in, at DESC);
CREATE INDEX IF NOT EXISTS idx_price_log_session ON price_display_log (session_id, at);

-- Outcome attribution on bookings: which yield rules were active in the
-- price the guest actually booked (revenue-per-rule = a query).
ALTER TABLE reservations ADD COLUMN IF NOT EXISTS yield_adjustments JSONB;
ALTER TABLE reservations ADD COLUMN IF NOT EXISTS ab_bucket TEXT;

-- §1 anchor: base + 15% markup is the starting point again (supersedes the
-- 8.018% gross-parity experiment; yield discounts come off the anchor).
UPDATE booking_config SET markup_pct = 15, updated_at = now();
