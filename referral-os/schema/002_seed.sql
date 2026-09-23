-- =====================================================================
-- Seed data — Sun & Moon project + 30A referral segments
-- =====================================================================
-- Run AFTER 001_init.sql. Idempotent (ON CONFLICT DO NOTHING).
-- =====================================================================

INSERT INTO projects (slug, name, primary_domain, tracking_base_url, metadata) VALUES
('sun-moon-30a', 'Sun & Moon at 30A',
 'sunandmoon30a.com', 'https://r.sunmoon30a.com',
 '{"owner":"Cristian Orihuela","properties":["Blue Moon","Golden Sun"],"market":"Seagrove Beach, FL"}'::jsonb)
ON CONFLICT (slug) DO NOTHING;

-- ---------------------------------------------------------------------
-- Segments for Sun & Moon. Tier 1 = highest referral value.
-- expected_referral_value_usd = avg $ per booking driven by this partner type.
-- target_count = how many businesses we want in this segment.
-- zoominfo_query is the canonical search spec for the enrichment pipeline.
-- ---------------------------------------------------------------------

WITH p AS (SELECT id FROM projects WHERE slug = 'sun-moon-30a')
INSERT INTO segments
  (project_id, slug, name, description, priority_tier,
   expected_referral_value_usd, expected_conversion_rate, target_count, zoominfo_query)
VALUES
-- ===== TIER 1: highest leverage. One referral = a full-week booking. =====
((SELECT id FROM p), 'wedding-planner-local',
 'Wedding Planners — 30A / Emerald Coast',
 'Local planners book entire houses for wedding-party lodging. One placement can lock 7-night stays.',
 1, 4200.00, 0.15, 120,
 '{"industry":["Event Planning Services","Hospitality"],"keywords":["wedding planner","wedding coordinator"],"states":["FL"],"metros":["Panama City","Pensacola","Fort Walton Beach"],"employee_min":1,"employee_max":50}'::jsonb),

((SELECT id FROM p), 'wedding-planner-feeder',
 'Wedding Planners — Drive-Market Feeders',
 'Birmingham/Atlanta/Nashville/New Orleans planners doing destination weddings on 30A.',
 1, 4500.00, 0.08, 250,
 '{"industry":["Event Planning Services"],"keywords":["destination wedding","wedding planner"],"metros":["Birmingham","Atlanta","Nashville","New Orleans","Memphis","Jackson","Knoxville"],"employee_min":1,"employee_max":50}'::jsonb),

((SELECT id FROM p), 'corp-retreat-planner',
 'Corporate Retreat / Offsite Planners',
 'Companies that book multi-bedroom stays for team offsites. High ADR, mid-week stays.',
 1, 3800.00, 0.05, 200,
 '{"keywords":["corporate retreat","executive offsite","team offsite"],"metros":["Birmingham","Atlanta","Nashville","Memphis","Jackson"],"employee_min":2,"employee_max":200}'::jsonb),

-- ===== TIER 2: high frequency, lower-ticket but compounding. =====
((SELECT id FROM p), 'travel-advisor',
 'Travel Advisors (Virtuoso, Signature, indie)',
 'Agents booking family travel; love direct-book inventory with concierge support.',
 2, 2800.00, 0.10, 300,
 '{"industry":["Travel Agencies","Travel Arrangements"],"keywords":["travel advisor","travel agent","family travel"],"states":["AL","GA","TN","LA","MS","TX","FL"]}'::jsonb),

((SELECT id FROM p), 'family-reunion-planner',
 'Family Reunion / Multi-Gen Travel Planners',
 'Plan large group beach getaways. Often book multiple homes (Blue Moon + Golden Sun = perfect).',
 2, 5200.00, 0.06, 150,
 '{"keywords":["family reunion","multi-generational travel","group travel"],"states":["AL","GA","TN","LA","MS","TX","FL"]}'::jsonb),

((SELECT id FROM p), 'golf-cart-rental',
 'Golf Cart Rental Companies — 30A',
 'Guests rent carts. Mutual referrals: we send guests, they hand out our card.',
 2, 600.00, 0.30, 25,
 '{"keywords":["golf cart rental","LSV rental","beach cart"],"metros":["Santa Rosa Beach","Miramar Beach","Panama City Beach","Destin"],"states":["FL"]}'::jsonb),

((SELECT id FROM p), 'bike-rental',
 'Bike Rental / Beach Service Companies',
 'Same playbook as golf carts. High-frequency, low-value but builds guest goodwill.',
 2, 400.00, 0.35, 30,
 '{"keywords":["bike rental","beach service","beach chair rental","umbrella rental"],"metros":["Santa Rosa Beach","Miramar Beach","Panama City Beach","Destin"],"states":["FL"]}'::jsonb),

-- ===== TIER 3: brand alignment & guest experience. =====
((SELECT id FROM p), 'photographer-family',
 'Family / Vacation Photographers',
 'Beach photo shoot bookings. Drive 5-star reviews; partners love being featured.',
 3, 700.00, 0.25, 80,
 '{"industry":["Photography"],"keywords":["family photographer","vacation photographer","beach photography"],"metros":["Panama City","Pensacola","Fort Walton Beach"],"states":["FL"]}'::jsonb),

((SELECT id FROM p), 'wedding-photographer',
 'Wedding Photographers — Destination 30A',
 'Repeat business with planners; often referred together. Photographer audience matches buyer profile.',
 3, 1100.00, 0.12, 100,
 '{"industry":["Photography"],"keywords":["wedding photographer","engagement photographer"],"states":["FL","AL","GA","TN","LA"]}'::jsonb),

((SELECT id FROM p), 'private-chef',
 'Private Chefs / In-Home Catering',
 'Premium guest experience; chef sees lots of properties and recommends favorites.',
 3, 1800.00, 0.15, 60,
 '{"industry":["Personal Chef","Catering"],"keywords":["private chef","personal chef","in-home catering"],"states":["FL"],"metros":["Panama City","Fort Walton Beach","Destin"]}'::jsonb),

((SELECT id FROM p), 'yoga-instructor',
 'Yoga / Wellness Instructors — In-home & Beach',
 'Guest add-ons. Builds wellness positioning. Instructors often have email lists.',
 3, 250.00, 0.20, 40,
 '{"keywords":["yoga instructor","beach yoga","private yoga","wellness coach"],"metros":["Santa Rosa Beach","Seagrove","Seaside"],"states":["FL"]}'::jsonb),

-- ===== TIER 4: complementary lodging / referral exchanges. =====
((SELECT id FROM p), 'boutique-hotel-30a',
 'Boutique Hotels & Inns — 30A overflow partners',
 'Hotels send overflow / multi-family bookings to private rentals.',
 4, 3200.00, 0.04, 30,
 '{"industry":["Hotels and Motels","Bed and Breakfast"],"states":["FL"],"metros":["Santa Rosa Beach","Panama City","Destin"]}'::jsonb),

((SELECT id FROM p), 'concierge-service',
 'Concierge / Lifestyle Services — 30A',
 'White-glove concierge firms recommend rentals to their clients.',
 4, 2100.00, 0.10, 25,
 '{"keywords":["concierge service","lifestyle management","vacation concierge"],"metros":["Santa Rosa Beach","Destin","Panama City Beach"],"states":["FL"]}'::jsonb),

-- ===== TIER 5: long-tail, opportunistic. =====
((SELECT id FROM p), 'restaurant-30a',
 'Restaurants — 30A flagship spots',
 'Mutual benefit: we recommend, they hand our card to multi-week guests.',
 5, 200.00, 0.40, 80,
 '{"industry":["Restaurants","Full-Service Restaurants"],"metros":["Santa Rosa Beach","Seagrove","Seaside","Rosemary Beach"],"states":["FL"]}'::jsonb),

((SELECT id FROM p), 'realtor-30a',
 'Real Estate Agents — 30A',
 'Agents put visiting prospects in STRs to "try the area." Long-cycle but high-value.',
 5, 2500.00, 0.05, 200,
 '{"industry":["Real Estate","Real Estate Agents and Brokers"],"states":["FL"],"metros":["Santa Rosa Beach","Panama City","Destin","Fort Walton Beach"]}'::jsonb),

((SELECT id FROM p), 'event-venue',
 'Event Venues — 30A & adjacent',
 'Receptions, rehearsal dinners; venue owners get asked for lodging recs constantly.',
 5, 2900.00, 0.06, 50,
 '{"keywords":["event venue","wedding venue","beach venue"],"metros":["Santa Rosa Beach","Panama City","Pensacola","Destin"],"states":["FL"]}'::jsonb),

((SELECT id FROM p), 'florist-30a',
 'Florists — Wedding & Event',
 'Adjacent to wedding planners; often have own client lists.',
 5, 1400.00, 0.08, 40,
 '{"industry":["Florists"],"metros":["Panama City","Pensacola","Destin","Fort Walton Beach"],"states":["FL"]}'::jsonb)
ON CONFLICT (project_id, slug) DO NOTHING;

-- Sanity check
SELECT
  s.priority_tier,
  COUNT(*) AS segment_count,
  SUM(s.target_count) AS total_target,
  ROUND(AVG(s.expected_referral_value_usd)::numeric, 2) AS avg_referral_value
FROM segments s
JOIN projects p ON p.id = s.project_id
WHERE p.slug = 'sun-moon-30a'
GROUP BY s.priority_tier
ORDER BY s.priority_tier;
