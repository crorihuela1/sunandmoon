-- =====================================================================
-- Seed data — Atlanta Restaurants project (B2B: selling TO restaurants)
-- =====================================================================
-- A SECOND project on the same Referral OS schema. Proves the multi-tenant
-- design: zero new tables, just a new projects row + its own segments.
--
-- Difference vs sun-moon-30a:
--   sun-moon-30a  = recruit partners who send US guests (supply/demand referral).
--   atlanta-restaurants-b2b = restaurants are PROSPECTS we sell a product/service
--                             to. So `referrals` is unused here; the value lives in
--                             company_segments.status (prospect→…→partner=customer)
--                             and the outreach/tracking tables.
--
-- Run AFTER 001_init.sql. Idempotent (ON CONFLICT DO NOTHING).
-- =====================================================================

INSERT INTO projects (slug, name, primary_domain, tracking_base_url, metadata) VALUES
('atlanta-restaurants-b2b', 'Atlanta Restaurants — B2B Outreach',
 NULL, 'https://r.sunmoon30a.com',
 '{"owner":"Cristian Orihuela",
   "play":"B2B sell-to-restaurant",
   "market":"Atlanta, GA",
   "data_source":"google_places",
   "notes":"Restaurants are prospects. status partner = signed customer. Set product_pitch in recommend_restaurants.py."}'::jsonb)
ON CONFLICT (slug) DO NOTHING;

-- ---------------------------------------------------------------------
-- Segments = how we slice the Atlanta restaurant universe for outreach.
-- For a SELL-TO play, tier reflects expected deal value / close odds, not
-- referral value. expected_referral_value_usd is repurposed as
-- "expected annual contract value (ACV) per signed restaurant"; and
-- expected_conversion_rate is the expected prospect→customer close rate.
-- ---------------------------------------------------------------------

WITH p AS (SELECT id FROM projects WHERE slug = 'atlanta-restaurants-b2b')
INSERT INTO segments
  (project_id, slug, name, description, priority_tier,
   expected_referral_value_usd, expected_conversion_rate, target_count, zoominfo_query)
VALUES
-- ===== TIER 1: best ability-to-pay + clearest need =====
((SELECT id FROM p), 'independent-fine-dining',
 'Independent Fine Dining ($$$–$$$$)',
 'Owner-operated upscale restaurants. High ticket, real marketing budgets, '
 'decision-maker is reachable. Best ACV and least procurement friction.',
 1, 6000.00, 0.12, 400,
 '{"source":"google_places","place_types":["fine_dining_restaurant","restaurant"],"price_levels":["PRICE_LEVEL_EXPENSIVE","PRICE_LEVEL_VERY_EXPENSIVE"],"city":"Atlanta","state":"GA"}'::jsonb),

((SELECT id FROM p), 'independent-full-service',
 'Independent Full-Service ($$) ',
 'Sit-down independents — the core of the market. Mid budget, high volume of targets, '
 'owner usually answers. The bread-and-butter of the pipeline.',
 1, 3600.00, 0.10, 1500,
 '{"source":"google_places","place_types":["restaurant"],"price_levels":["PRICE_LEVEL_MODERATE"],"exclude_chains":true,"city":"Atlanta","state":"GA"}'::jsonb),

-- ===== TIER 2: high volume, faster but smaller deals =====
((SELECT id FROM p), 'cafe-bakery-brunch',
 'Cafés, Bakeries & Brunch',
 'Daytime concepts. Smaller checks but fast decisions and high openness to new tools.',
 2, 2400.00, 0.11, 800,
 '{"source":"google_places","place_types":["cafe","bakery","brunch_restaurant","breakfast_restaurant"],"city":"Atlanta","state":"GA"}'::jsonb),

((SELECT id FROM p), 'bar-brewery-nightlife',
 'Bars, Breweries & Nightlife',
 'Bars / taprooms with kitchens. Strong margins, event-driven, marketing-hungry.',
 2, 3000.00, 0.09, 500,
 '{"source":"google_places","place_types":["bar","pub","brewpub","wine_bar"],"city":"Atlanta","state":"GA"}'::jsonb),

-- ===== TIER 3: volume long-tail, hardest to close =====
((SELECT id FROM p), 'quick-service-counter',
 'Quick-Service & Counter ($)',
 'Counter-service independents and fast-casual. Lowest ACV, price-sensitive, '
 'but enormous count — good for low-touch/self-serve offers.',
 3, 1200.00, 0.06, 1500,
 '{"source":"google_places","place_types":["fast_food_restaurant","meal_takeaway","sandwich_shop"],"price_levels":["PRICE_LEVEL_INEXPENSIVE"],"city":"Atlanta","state":"GA"}'::jsonb),

((SELECT id FROM p), 'no-website-opportunity',
 'No-Website Opportunity (any tier)',
 'Restaurants with a Google listing but NO website. Highest "digital gap" — the easiest '
 'value story to tell regardless of what you sell. Cross-cuts the other segments.',
 1, 3000.00, 0.18, 1000,
 '{"source":"google_places","filter":"website IS NULL","city":"Atlanta","state":"GA"}'::jsonb)
ON CONFLICT (project_id, slug) DO NOTHING;
