-- =====================================================================
-- Seed data — 30A Restaurants project (pricing-agent, LOCAL positioning)
-- =====================================================================
-- Third project on the same schema. Same play as atlanta-restaurants-b2b
-- (sell the pricing AI agent TO restaurants) but:
--   • geography = the 30A / Emerald Coast beach towns, and
--   • Cristian reaches out as a NEIGHBOR — he runs Sun & Moon at 30A and
--     already sends guests to local restaurants. Warm, not cold.
--
-- Uses the SAME 6 segment slugs as the Atlanta project so the shared ingest
-- code (pick_segment) and the generic views work unchanged.
--
-- Run AFTER 001_init.sql. Idempotent.
-- =====================================================================

INSERT INTO projects (slug, name, primary_domain, tracking_base_url, metadata) VALUES
('30a-restaurants', '30A Restaurants — Pricing Agent (local)',
 'sunandmoon30a.com', 'https://r.sunmoon30a.com',
 '{"owner":"Cristian Orihuela",
   "play":"B2B sell-to-restaurant",
   "positioning":"30a_local",
   "market":"30A / Emerald Coast, FL",
   "data_source":"google_places",
   "sender_identity":"experience@sunandmoon30a.com",
   "notes":"Warm local angle — Cristian runs Sun & Moon at 30A and refers guests to restaurants. Restaurants are prospects; status partner = signed customer."}'::jsonb)
ON CONFLICT (slug) DO NOTHING;

WITH p AS (SELECT id FROM projects WHERE slug = '30a-restaurants')
INSERT INTO segments
  (project_id, slug, name, description, priority_tier,
   expected_referral_value_usd, expected_conversion_rate, target_count, zoominfo_query)
VALUES
-- Higher conversion than Atlanta cold: local trust + Cristian sends guests.
((SELECT id FROM p), 'independent-fine-dining',
 'Independent Fine Dining ($$$–$$$$)',
 'Upscale 30A rooms (Seaside, Alys, Rosemary). Strong seasonal pricing power; '
 'owners care about peak-season menu optimization. Warmest path — many already '
 'know Sun & Moon guests.',
 1, 6000.00, 0.25, 60,
 '{"source":"google_places","price_levels":["PRICE_LEVEL_EXPENSIVE","PRICE_LEVEL_VERY_EXPENSIVE"],"region":"30a"}'::jsonb),

((SELECT id FROM p), 'independent-full-service',
 'Independent Full-Service ($$)',
 'Sit-down 30A independents — seafood houses, grills, brunch spots. The core list '
 'Cristian already recommends to guests.',
 1, 3600.00, 0.22, 150,
 '{"source":"google_places","price_levels":["PRICE_LEVEL_MODERATE"],"region":"30a"}'::jsonb),

((SELECT id FROM p), 'cafe-bakery-brunch',
 'Cafés, Bakeries & Brunch',
 'Morning 30A concepts — coffee, bakeries, breakfast. Guests ask for these constantly.',
 2, 2400.00, 0.20, 80,
 '{"source":"google_places","place_types":["cafe","bakery","brunch_restaurant","breakfast_restaurant"],"region":"30a"}'::jsonb),

((SELECT id FROM p), 'bar-brewery-nightlife',
 'Bars, Breweries & Nightlife',
 'Beach bars and taprooms with kitchens. Seasonal swings = real pricing upside.',
 2, 3000.00, 0.18, 50,
 '{"source":"google_places","place_types":["bar","pub","brewpub","wine_bar"],"region":"30a"}'::jsonb),

((SELECT id FROM p), 'quick-service-counter',
 'Quick-Service & Counter ($)',
 'Counter spots, taco shacks, takeaway. Lower ACV but easy yes from a neighbor.',
 3, 1200.00, 0.12, 120,
 '{"source":"google_places","price_levels":["PRICE_LEVEL_INEXPENSIVE"],"region":"30a"}'::jsonb),

((SELECT id FROM p), 'no-website-opportunity',
 'No-Website Opportunity (any tier)',
 '30A spots with a Google listing but no website. Easiest value story.',
 1, 3000.00, 0.28, 60,
 '{"source":"google_places","filter":"website IS NULL","region":"30a"}'::jsonb)
ON CONFLICT (project_id, slug) DO NOTHING;
