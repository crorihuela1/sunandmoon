var __defProp = Object.defineProperty;
var __name = (target, value) => __defProp(target, "name", { value, configurable: true });

// worker.js
var VRN_BASE = "https://www.vacayrentalnetwork.com/api/public/properties";
var UA = "SunMoonBookingOS/1.0 (sunandmoon30a.com; owner-operated channel sync)";
var DISCLAIMERS = [
  "This is a direct reservation with Sun & Moon at 30A, Seagrove Beach, Florida \u2014 not a VacayRentalNetwork or OTA booking. Your total includes the nightly rate, cleaning fee, damage protection, and all applicable Florida taxes (6% State Sales Tax + 5% Walton County Tourist Development Tax). The platform booking fee is NOT charged on direct reservations.",
  "A direct reservation is confirmed once the short-term rental agreement is signed and payment is received. Your dates are held for 48 hours from this request while we finalize both; if we cannot reach you within 48 hours the hold is released.",
  "Cancellation policy: [EDIT ME \u2014 e.g. full refund 60+ days before check-in; 50% refund 30\u201359 days; non-refundable within 30 days of arrival].",
  "House rules: primary renter must be 25 or older; no parties or events without prior written approval; no smoking anywhere on the property; pets only where expressly permitted in the rental agreement.",
  "Rates and availability sync continuously with our channel calendar and may change until your reservation is confirmed in writing."
];
function sbHeaders(env, extra = {}) {
  return {
    "apikey": env.SUPABASE_SERVICE_KEY,
    "Authorization": `Bearer ${env.SUPABASE_SERVICE_KEY}`,
    "Content-Type": "application/json",
    ...extra
  };
}
__name(sbHeaders, "sbHeaders");
async function sb(env, method, path, payload = null, extraHeaders = {}) {
  const r = await fetch(`${env.SUPABASE_URL.replace(/\/$/, "")}${path}`, {
    method,
    headers: sbHeaders(env, extraHeaders),
    body: payload === null ? void 0 : JSON.stringify(payload)
  });
  if (!r.ok) {
    const text = await r.text();
    throw new Error(`supabase ${method} ${path} \u2192 ${r.status}: ${text.slice(0, 300)}`);
  }
  const body = await r.text();
  return body ? JSON.parse(body) : null;
}
__name(sb, "sb");
async function queueNotifications(env, rows) {
  const smsEnabled = (env.SMS_ENABLED || "false") === "true";
  const expanded = rows.flatMap((r) => r.channel === "email" && String(r.recipient || "").includes(",") ? String(r.recipient).split(",").map((e) => e.trim()).filter(Boolean).map((e) => ({ ...r, recipient: e })) : [r]);
  const clean = expanded.filter((r) => r.recipient).filter((r) => r.channel !== "sms" || smsEnabled);
  if (!clean.length) return;
  await sb(env, "POST", "/rest/v1/booking_notifications", clean, { "Prefer": "return=minimal" }).catch(() => {
  });
}
__name(queueNotifications, "queueNotifications");
async function notifyOwnerAlert(env, subject, body) {
  const rows = [{ channel: "email", recipient: env.NOTIFY_OWNER_EMAIL, subject: `\u26A0\uFE0E ${subject}`, body }];
  if (env.ADMIN_ALERT_PHONE) rows.push({ channel: "sms", recipient: env.ADMIN_ALERT_PHONE, subject: null, body: `${subject}: ${body.slice(0, 300)}` });
  await queueNotifications(env, rows);
}
__name(notifyOwnerAlert, "notifyOwnerAlert");
async function notifyMindyBooking(env, prop, resv, quote) {
  const conf = resv.confirmation_code || resv.id;
  const emailBody = `DIRECT SUN & MOON RESERVATION \u2014 please block these dates on Vacay.

Property:        ${prop.name}
Check-in:        ${resv.check_in}
Check-out:       ${resv.check_out}  (${quote.nights} nights)
Guests:          ${resv.guests}
Guest name:      ${resv.guest_name}
Confirmation #:  ${conf}

This stay was booked DIRECT on book.sunandmoon30a.com and is now blocked on
our calendar. Please block ${resv.check_in} \u2192 ${resv.check_out} for ${prop.name}
on the Vacay side to prevent a double-booking.

\u2014 Sun & Moon at 30A (automated)`;
  const sms = `Sun & Moon DIRECT booking \u2014 please block on Vacay: ${prop.name}, ${resv.check_in}\u2192${resv.check_out}, ${resv.guests} guests, guest ${resv.guest_name}, conf ${conf}.`;
  const rows = [];
  if (env.VACAY_CONTACT_EMAIL) rows.push({ reservation_id: resv.id, channel: "email", recipient: env.VACAY_CONTACT_EMAIL, subject: `Please block on Vacay \u2014 ${prop.name} ${resv.check_in}\u2192${resv.check_out}`, body: emailBody });
  if (env.VACAY_CONTACT_PHONE) rows.push({ reservation_id: resv.id, channel: "sms", recipient: env.VACAY_CONTACT_PHONE, subject: null, body: sms });
  rows.push({ reservation_id: resv.id, channel: "email", recipient: env.NOTIFY_OWNER_EMAIL, subject: `[CC] Vacay block request \u2014 ${prop.name} ${resv.check_in}\u2192${resv.check_out}`, body: emailBody });
  await queueNotifications(env, rows);
}
__name(notifyMindyBooking, "notifyMindyBooking");
function stripeEncode(obj, prefix) {
  const parts = [];
  for (const [k, v] of Object.entries(obj)) {
    if (v === void 0 || v === null) continue;
    const key = prefix ? `${prefix}[${k}]` : k;
    if (Array.isArray(v)) {
      v.forEach((item, i) => {
        if (item !== null && typeof item === "object") parts.push(stripeEncode(item, `${key}[${i}]`));
        else parts.push(`${encodeURIComponent(`${key}[${i}]`)}=${encodeURIComponent(item)}`);
      });
    } else if (typeof v === "object") {
      const nested = stripeEncode(v, key);
      if (nested) parts.push(nested);
    } else {
      parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(v)}`);
    }
  }
  return parts.join("&");
}
__name(stripeEncode, "stripeEncode");
async function stripe(env, method, path, params = null, idemKey = null) {
  const headers = {
    "Authorization": `Bearer ${env.STRIPE_SECRET_KEY}`,
    "Content-Type": "application/x-www-form-urlencoded"
  };
  if (idemKey) headers["Idempotency-Key"] = idemKey;
  const r = await fetch(`https://api.stripe.com/v1${path}`, {
    method,
    headers,
    body: params ? stripeEncode(params) : void 0
  });
  const j = await r.json();
  if (!r.ok) throw new Error(`stripe ${method} ${path}: ${j.error?.message || r.status}`);
  return j;
}
__name(stripe, "stripe");
var stripeConfigured = /* @__PURE__ */ __name((env) => !!env.STRIPE_SECRET_KEY, "stripeConfigured");
var cents = /* @__PURE__ */ __name((n) => Math.round(Number(n) * 100), "cents");
async function verifyStripeSig(secret, payload, sigHeader) {
  if (!secret) return { ok: false, why: "no signing secret configured" };
  const parts = Object.fromEntries((sigHeader || "").split(",").map((p) => p.split("=")));
  const t = parts.t, v1 = parts.v1;
  if (!t || !v1) return { ok: false, why: "malformed signature header" };
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const mac = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(`${t}.${payload}`));
  const expected = [...new Uint8Array(mac)].map((b) => b.toString(16).padStart(2, "0")).join("");
  if (expected.length !== v1.length) return { ok: false, why: "signature mismatch" };
  let diff = 0;
  for (let i = 0; i < expected.length; i++) diff |= expected.charCodeAt(i) ^ v1.charCodeAt(i);
  if (diff !== 0) return { ok: false, why: "signature mismatch" };
  if (Math.abs(Date.now() / 1e3 - Number(t)) > 300) return { ok: false, why: "timestamp too old" };
  return { ok: true };
}
__name(verifyStripeSig, "verifyStripeSig");
async function vrnQuote(propertyId, checkIn, checkOut, guests) {
  const u = `${VRN_BASE}/${propertyId}/quote?checkIn=${checkIn}&checkOut=${checkOut}&guests=${guests}`;
  const r = await fetch(u, { headers: { "User-Agent": UA } });
  if (!r.ok) throw new Error(`VRN quote ${r.status}`);
  const j = await r.json();
  if (!j.success) throw new Error(`VRN quote error: ${j.error || "unknown"}`);
  return j.data;
}
__name(vrnQuote, "vrnQuote");
async function vrnAvailability(propertyId, start, end) {
  const u = `${VRN_BASE}/${propertyId}/availability?start=${start}&end=${end}`;
  const r = await fetch(u, { headers: { "User-Agent": UA } });
  if (!r.ok) throw new Error(`VRN availability ${r.status}`);
  const j = await r.json();
  if (!j.success) throw new Error(`VRN availability error: ${j.error || "unknown"}`);
  return j.data.blockedDates || [];
}
__name(vrnAvailability, "vrnAvailability");
var round2 = /* @__PURE__ */ __name((n) => Math.round(n * 100) / 100, "round2");
var fmt = /* @__PURE__ */ __name((n) => Number(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }), "fmt");
var isDate = /* @__PURE__ */ __name((s) => /^\d{4}-\d{2}-\d{2}$/.test(s || ""), "isDate");
var addDays = /* @__PURE__ */ __name((iso, n) => {
  const d = /* @__PURE__ */ new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}, "addDays");
var nightsBetween = /* @__PURE__ */ __name((a, b) => Math.round((/* @__PURE__ */ new Date(`${b}T00:00:00Z`) - /* @__PURE__ */ new Date(`${a}T00:00:00Z`)) / 864e5), "nightsBetween");
var esc = /* @__PURE__ */ __name((s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"), "esc");
async function getProperty(env, slugOrId) {
  const rows = await sb(
    env,
    "GET",
    `/rest/v1/booking_properties?or=(slug.eq.${slugOrId},id.eq.${slugOrId})&limit=1`
  );
  return rows[0] || null;
}
__name(getProperty, "getProperty");
async function hasActiveOverlap(env, propId, checkIn, checkOut, excludeId = null) {
  const rows = await sb(
    env,
    "GET",
    `/rest/v1/reservations?property_id=eq.${propId}&status=in.(pending,reserved,blocked)&check_in=lt.${checkOut}&check_out=gt.${checkIn}&source=in.(direct,webhook)&select=id,status,soft_hold_expires_at`
  );
  const now = Date.now();
  return rows.some((r) => {
    if (excludeId && r.id === excludeId) return false;
    if (r.status === "pending") {
      return r.soft_hold_expires_at ? new Date(r.soft_hold_expires_at).getTime() > now : false;
    }
    return true;
  });
}
__name(hasActiveOverlap, "hasActiveOverlap");
async function reconcileCalendarWindow(env, propertyId, start, end, live, excludeReservationId = null) {
  const vrnBlocked = /* @__PURE__ */ new Set();
  const liveRefs = /* @__PURE__ */ new Set();
  const upserts = [];
  for (const b of live) {
    if (!(b.start < end && b.end > start)) continue;
    liveRefs.add(`vrn_${b.start}_${b.end}`);
    const from = b.start < start ? start : b.start;
    const to = b.end > end ? end : b.end;
    for (let d = from; d < to; d = addDays(d, 1)) vrnBlocked.add(d);
    upserts.push({
      property_id: propertyId,
      source: "vrn_sync",
      status: "blocked",
      check_in: b.start,
      check_out: b.end,
      external_ref: `vrn_${b.start}_${b.end}`,
      notes: b.type || "booked"
    });
  }
  if (upserts.length) {
    await sb(
      env,
      "POST",
      "/rest/v1/reservations?on_conflict=property_id,external_ref",
      upserts,
      { "Prefer": "resolution=merge-duplicates,return=minimal" }
    ).catch(() => {
    });
  }
  const mirrored = await sb(
    env,
    "GET",
    `/rest/v1/reservations?property_id=eq.${propertyId}&source=eq.vrn_sync&status=eq.blocked&check_in=lt.${end}&check_out=gt.${start}&select=id,external_ref,check_in,check_out`
  ).catch(() => []);
  for (const m of mirrored) {
    if (m.check_in >= start && m.check_out <= end && !liveRefs.has(m.external_ref)) {
      await sb(
        env,
        "PATCH",
        `/rest/v1/reservations?id=eq.${m.id}`,
        { status: "cancelled", notes: "released: no longer blocked on Vacay" },
        { "Prefer": "return=minimal" }
      ).catch(() => {
      });
    }
  }
  const ours = await sb(
    env,
    "GET",
    `/rest/v1/reservations?property_id=eq.${propertyId}&status=in.(reserved,blocked,pending)&source=in.(direct,webhook)&check_in=lt.${end}&check_out=gt.${start}&select=id,check_in,check_out,status,soft_hold_expires_at`
  ).catch(() => []);
  const ourBlocked = /* @__PURE__ */ new Set();
  const ourBlockedAll = /* @__PURE__ */ new Set();
  const now = Date.now();
  for (const r of ours) {
    if (r.status === "pending" && !(r.soft_hold_expires_at && new Date(r.soft_hold_expires_at).getTime() > now)) continue;
    for (let d = r.check_in < start ? start : r.check_in; d < r.check_out && d < end; d = addDays(d, 1)) {
      ourBlockedAll.add(d);
      if (!(excludeReservationId && r.id === excludeReservationId)) ourBlocked.add(d);
    }
  }
  const rows = [];
  for (let d = start; d < end; d = addDays(d, 1)) {
    const blocked = vrnBlocked.has(d) || ourBlockedAll.has(d);
    rows.push({
      property_id: propertyId,
      day: d,
      available: !blocked,
      block_source: ourBlockedAll.has(d) ? "direct" : vrnBlocked.has(d) ? "vrn_sync" : null,
      synced_at: (/* @__PURE__ */ new Date()).toISOString()
    });
  }
  for (let i = 0; i < rows.length; i += 200) {
    await sb(
      env,
      "POST",
      "/rest/v1/rate_calendar?on_conflict=property_id,day",
      rows.slice(i, i + 200),
      { "Prefer": "resolution=merge-duplicates,return=minimal" }
    ).catch(() => {
    });
  }
  return { vrnBlocked, ourBlocked };
}
__name(reconcileCalendarWindow, "reconcileCalendarWindow");
async function refreshAvailabilityWindow(env, propertyId, checkIn, checkOut, source = "poll", excludeReservationId = null) {
  let live;
  try {
    live = await vrnAvailability(propertyId, checkIn, checkOut);
  } catch (e) {
    await recordSyncState(env, propertyId, "availability", false, `pre-booking refresh failed: ${String(e).slice(0, 120)}`);
    return { ok: false, windowOpen: false, error: String(e).slice(0, 200) };
  }
  const { vrnBlocked, ourBlocked } = await reconcileCalendarWindow(env, propertyId, checkIn, checkOut, live, excludeReservationId);
  let windowOpen = true;
  for (let d = checkIn; d < checkOut; d = addDays(d, 1)) {
    if (vrnBlocked.has(d) || ourBlocked.has(d)) {
      windowOpen = false;
      break;
    }
  }
  await recordSyncState(env, propertyId, "availability", true, `pre-booking refresh ${checkIn}..${checkOut}`);
  return { ok: true, windowOpen };
}
__name(refreshAvailabilityWindow, "refreshAvailabilityWindow");
async function effectiveAdjustmentPct(env, propertyId, partnerSlug) {
  const adjustments = await sb(env, "GET", `/rest/v1/rate_adjustments?active=eq.true&select=*`);
  let pct = 0;
  const applied = [];
  for (const a of adjustments) {
    if (a.scope === "global") {
      pct += Number(a.pct);
      applied.push(a);
    } else if (a.scope === "property" && a.property_id === propertyId) {
      pct += Number(a.pct);
      applied.push(a);
    } else if (a.scope === "partner" && partnerSlug && a.partner_slug === partnerSlug) {
      pct += Number(a.pct);
      applied.push(a);
    }
  }
  let partner = null;
  if (partnerSlug) {
    const rows = await sb(
      env,
      "GET",
      `/rest/v1/rev_share_partners?slug=eq.${encodeURIComponent(partnerSlug)}&active=eq.true&limit=1`
    );
    partner = rows[0] || null;
    if (partner && Number(partner.guest_discount_pct)) {
      pct -= Number(partner.guest_discount_pct);
      applied.push({ scope: "partner-discount", pct: -Number(partner.guest_discount_pct), note: partner.name });
    }
  }
  return { pct: round2(pct), applied, partner };
}
__name(effectiveAdjustmentPct, "effectiveAdjustmentPct");
async function getConfig(env) {
  const rows = await sb(env, "GET", "/rest/v1/booking_config?id=eq.1&limit=1").catch(() => []);
  return rows[0] || {
    markup_pct: 0,
    admin_fee_pct: 8.9,
    deposit_pct: 25,
    balance_days_before: 30,
    stale_breaker_hours: 6,
    exclude_booking_fee: true
  };
}
__name(getConfig, "getConfig");
async function isSyncStale(env, propertyId, breakerHours) {
  const rows = await sb(
    env,
    "GET",
    `/rest/v1/sync_state?property_id=eq.${propertyId}&domain=in.(availability,validation)&select=domain,last_success_at`
  ).catch(() => []);
  if (!rows.length) return false;
  const cutoff = Date.now() - breakerHours * 3600 * 1e3;
  const anyFresh = rows.some((r) => r.last_success_at && new Date(r.last_success_at).getTime() >= cutoff);
  return !anyFresh;
}
__name(isSyncStale, "isSyncStale");
async function validateCoupon(env, rawCode, propertyId, checkIn, checkOut) {
  const code = String(rawCode || "").trim().toUpperCase();
  if (!code) return { valid: false, pct: 0, code: "", reason: null };
  const rows = await sb(
    env,
    "GET",
    `/rest/v1/coupons?code=eq.${encodeURIComponent(code)}&limit=1`
  ).catch(() => []);
  const c = rows[0];
  if (!c) return { valid: false, pct: 0, code, reason: "That code isn\u2019t recognized." };
  if (!c.active) return { valid: false, pct: 0, code, reason: "That code is no longer active." };
  const today = (/* @__PURE__ */ new Date()).toISOString().slice(0, 10);
  if (c.effective_start && today < c.effective_start) return { valid: false, pct: 0, code, reason: "That code isn\u2019t active yet." };
  if (c.effective_end && today > c.effective_end) return { valid: false, pct: 0, code, reason: "That code has expired." };
  if (c.property_id && c.property_id !== propertyId) return { valid: false, pct: 0, code, reason: "That code doesn\u2019t apply to this cottage." };
  if (c.stay_start && checkIn < c.stay_start) return { valid: false, pct: 0, code, reason: "That code applies to different stay dates." };
  if (c.stay_end && checkOut > c.stay_end) return { valid: false, pct: 0, code, reason: "That code applies to different stay dates." };
  if (c.max_redemptions != null && Number(c.redemptions) >= Number(c.max_redemptions))
    return { valid: false, pct: 0, code, reason: "That code has reached its redemption limit." };
  return { valid: true, pct: Number(c.pct), code, reason: null, coupon: c };
}
__name(validateCoupon, "validateCoupon");
var hash32 = /* @__PURE__ */ __name((s) => {
  let h = 0;
  for (let i = 0; i < s.length; i++) {
    h = h * 31 + s.charCodeAt(i) | 0;
  }
  return Math.abs(h);
}, "hash32");
async function getYieldRules(env, propertyId) {
  const rows = await sb(
    env,
    "GET",
    `/rest/v1/yield_rules?or=(property_id.eq.${propertyId},property_id.is.null)`
  ).catch(() => []);
  return rows.find((r) => r.property_id === propertyId) || rows.find((r) => !r.property_id) || null;
}
__name(getYieldRules, "getYieldRules");
async function stayIsProtected(env, propertyId, checkIn, checkOut) {
  const rows = await sb(
    env,
    "GET",
    `/rest/v1/protected_dates?active=eq.true&or=(property_id.eq.${propertyId},property_id.is.null)&start_date=lt.${checkOut}&end_date=gte.${checkIn}&limit=1`
  ).catch(() => []);
  return rows.length > 0;
}
__name(stayIsProtected, "stayIsProtected");
async function effectiveFloorPct(env, rules, propertyId, checkIn, checkOut) {
  const rows = await sb(
    env,
    "GET",
    `/rest/v1/yield_floor_overrides?active=eq.true&or=(property_id.eq.${propertyId},property_id.is.null)&start_date=lt.${checkOut}&end_date=gte.${checkIn}&select=floor_pct`
  ).catch(() => []);
  const overrides = rows.map((r) => Number(r.floor_pct));
  return overrides.length ? Math.min(...overrides) : Number(rules?.floor_pct ?? 100);
}
__name(effectiveFloorPct, "effectiveFloorPct");
async function isOrphanGap(env, propertyId, checkIn, checkOut, maxNights) {
  const today = (/* @__PURE__ */ new Date()).toISOString().slice(0, 10);
  const from = addDays(checkIn, -(maxNights + 1));
  const to = addDays(checkOut, maxNights + 1);
  const rows = await sb(
    env,
    "GET",
    `/rest/v1/rate_calendar?property_id=eq.${propertyId}&day=gte.${from}&day=lt.${to}&select=day,available&order=day.asc`
  ).catch(() => []);
  if (!rows.length) return false;
  const open = new Map(rows.map((r) => [r.day, r.available]));
  for (let d = checkIn; d < checkOut; d = addDays(d, 1)) if (open.get(d) !== true) return false;
  let start = checkIn;
  while (open.get(addDays(start, -1)) === true && addDays(start, -1) >= today) start = addDays(start, -1);
  let end = checkOut;
  while (open.get(end) === true) {
    end = addDays(end, 1);
    if (nightsBetween(start, end) > maxNights + 1) return false;
  }
  const pocket = nightsBetween(start, end);
  const leftBounded = start === today || open.get(addDays(start, -1)) === false;
  const rightBounded = open.get(end) === false;
  return pocket >= 1 && pocket <= maxNights && leftBounded && rightBounded;
}
__name(isOrphanGap, "isOrphanGap");
async function getActiveLockin(env, session, propertyId, checkIn, checkOut) {
  if (!session) return null;
  const rows = await sb(
    env,
    "GET",
    `/rest/v1/lockin_offers?session_id=eq.${encodeURIComponent(session)}&limit=1`
  ).catch(() => []);
  const o = rows[0];
  if (!o) return null;
  if (o.status === "active" && new Date(o.expires_at).getTime() <= Date.now()) {
    await sb(
      env,
      "PATCH",
      `/rest/v1/lockin_offers?id=eq.${o.id}`,
      { status: "expired" },
      { "Prefer": "return=minimal" }
    ).catch(() => {
    });
    return null;
  }
  if (o.status !== "active") return null;
  if (o.property_id !== propertyId || o.check_in !== checkIn || o.check_out !== checkOut) return null;
  return o;
}
__name(getActiveLockin, "getActiveLockin");
async function maybeTriggerLockin(env, rules, prop, checkIn, checkOut, session, daysToCheckIn) {
  if (!session || !rules?.lockin_enabled) return null;
  const maxWindow = Math.max(...(rules.ladder || []).map((s) => Number(s.days) || 0), 0);
  if (daysToCheckIn > maxWindow) return null;
  const existing = await sb(
    env,
    "GET",
    `/rest/v1/lockin_offers?session_id=eq.${encodeURIComponent(session)}&limit=1`
  ).catch(() => []);
  if (existing.length) return null;
  const since = new Date(Date.now() - 48 * 36e5).toISOString();
  let reason = null;
  const views = await sb(
    env,
    "GET",
    `/rest/v1/booking_events?session_id=eq.${encodeURIComponent(session)}&event=eq.date_search&at=gte.${since}&metadata->>checkIn=eq.${checkIn}&metadata->>property=eq.${prop.slug}&select=id&limit=3`
  ).catch(() => []);
  if (views.length >= 2) reason = "repeat_views";
  if (!reason) {
    const ab = await sb(
      env,
      "GET",
      `/rest/v1/abandoned_checkouts?session_id=eq.${encodeURIComponent(session)}&select=id&limit=1`
    ).catch(() => []);
    if (ab.length) reason = "abandoned_checkout";
  }
  if (!reason) {
    const na = await sb(
      env,
      "GET",
      `/rest/v1/booking_events?session_id=eq.${encodeURIComponent(session)}&event=eq.no_availability&at=gte.${since}&select=id&limit=1`
    ).catch(() => []);
    if (na.length) reason = "nearby_unavailable";
  }
  if (!reason) return null;
  const expiresAt = new Date(Date.now() + Number(rules.lockin_hours || 4) * 36e5).toISOString();
  const rows = await sb(env, "POST", "/rest/v1/lockin_offers", {
    session_id: session,
    property_id: prop.id,
    check_in: checkIn,
    check_out: checkOut,
    pct: Number(rules.lockin_extra_pct || 5),
    trigger_reason: reason,
    expires_at: expiresAt
  }, { "Prefer": "return=representation" }).catch(() => []);
  return rows[0] || null;
}
__name(maybeTriggerLockin, "maybeTriggerLockin");
async function applyYield(env, { prop, checkIn, checkOut, nights, baseNightlyTotal, markedNightlyTotal, session }) {
  const out = {
    nightlyTotal: markedNightlyTotal,
    adjustments: [],
    abBucket: "A",
    lockinOffer: null,
    protected: false,
    floorPct: 100
  };
  const rules = await getYieldRules(env, prop.id);
  if (!rules || !rules.enabled) return out;
  const split = Number(rules.ab_split_pct || 0);
  if (split > 0 && session) out.abBucket = hash32(session) % 100 < split ? "B" : "A";
  const ladder = out.abBucket === "B" && Array.isArray(rules.ab_variant_ladder) && rules.ab_variant_ladder.length ? rules.ab_variant_ladder : rules.ladder || [];
  if (await stayIsProtected(env, prop.id, checkIn, checkOut)) {
    out.protected = true;
    return out;
  }
  const daysToCheckIn = Math.floor(((/* @__PURE__ */ new Date(`${checkIn}T00:00:00Z`)).getTime() - Date.now()) / 864e5);
  out.floorPct = await effectiveFloorPct(env, rules, prop.id, checkIn, checkOut);
  const floorAmt = round2(baseNightlyTotal * out.floorPct / 100);
  let autoPct = 0, autoRule = null;
  for (const step of ladder) {
    if (daysToCheckIn <= Number(step.days) && Number(step.pct) > autoPct) {
      autoPct = Number(step.pct);
      autoRule = `ladder_${step.days}d`;
    }
  }
  if (rules.gap_enabled && nights <= Number(rules.gap_max_nights) && Number(rules.gap_discount_pct) > autoPct && await isOrphanGap(env, prop.id, checkIn, checkOut, Number(rules.gap_max_nights))) {
    autoPct = Number(rules.gap_discount_pct);
    autoRule = "gap_fill";
  }
  let nightly = markedNightlyTotal;
  if (autoPct > 0) {
    let target = round2(nightly * (1 - autoPct / 100));
    const clamped = target < floorAmt;
    if (clamped) target = floorAmt;
    if (target < nightly) {
      out.adjustments.push({
        rule: autoRule,
        pct: -autoPct,
        amount: round2(target - nightly),
        clamped_to_floor: clamped || void 0
      });
      nightly = target;
    }
  }
  let offer = await getActiveLockin(env, session, prop.id, checkIn, checkOut);
  if (!offer && nightly > floorAmt + 0.01) {
    offer = await maybeTriggerLockin(env, rules, prop, checkIn, checkOut, session, daysToCheckIn);
  }
  if (offer) {
    let target = round2(nightly * (1 - Number(offer.pct) / 100));
    const clamped = target < floorAmt;
    if (clamped) target = floorAmt;
    if (target < nightly) {
      out.adjustments.push({
        rule: "lockin_offer",
        pct: -Number(offer.pct),
        amount: round2(target - nightly),
        clamped_to_floor: clamped || void 0
      });
      nightly = target;
      out.lockinOffer = { pct: Number(offer.pct), expiresAt: offer.expires_at, reason: offer.trigger_reason };
    }
  }
  out.nightlyTotal = nightly;
  return out;
}
__name(applyYield, "applyYield");
async function gapFallbackQuote(env, prop, checkIn, checkOut) {
  const rules = await getYieldRules(env, prop.id);
  if (!rules?.enabled || !rules.gap_enabled || !rules.gap_relax_min_stay) return null;
  const nights = nightsBetween(checkIn, checkOut);
  if (nights > Number(rules.gap_max_nights)) return null;
  if (!await isOrphanGap(env, prop.id, checkIn, checkOut, Number(rules.gap_max_nights))) return null;
  const days = await sb(
    env,
    "GET",
    `/rest/v1/rate_calendar?property_id=eq.${prop.id}&day=gte.${checkIn}&day=lt.${checkOut}&select=day,nightly_rate,available&order=day.asc`
  ).catch(() => []);
  if (days.length !== nights || days.some((d) => d.available !== true || d.nightly_rate == null)) return null;
  const nightlyTotal = round2(days.reduce((a, d) => a + Number(d.nightly_rate), 0));
  const snaps = await sb(
    env,
    "GET",
    `/rest/v1/rate_snapshots?property_id=eq.${prop.id}&order=captured_at.desc&limit=1`
  ).catch(() => []);
  const snap = snaps[0];
  if (!snap) return null;
  const cleaning = Number(snap.cleaning_fee || 0);
  const raw = snap.raw || {};
  const sampleBase = Number(raw.nightlyTotal || 0) + Number(raw.cleaningFee || 0);
  const taxes = (snap.taxes || []).map((t) => {
    const rate = sampleBase > 0 ? Number(t.amount) / sampleBase : 0;
    return { name: t.name, amount: round2((nightlyTotal + cleaning) * rate) };
  });
  return {
    available: true,
    nights,
    nightlyTotal,
    cleaningFee: cleaning,
    fees: snap.fees || [],
    taxes,
    total: null,
    currency: "USD",
    minStayRelaxed: true
  };
}
__name(gapFallbackQuote, "gapFallbackQuote");
async function buildQuote(env, { property, checkIn, checkOut, guests, partner, coupon, session }) {
  const prop = await getProperty(env, property);
  if (!prop) return { ok: false, status: 404, error: "unknown property" };
  if (!isDate(checkIn) || !isDate(checkOut) || nightsBetween(checkIn, checkOut) < 1) {
    return { ok: false, status: 400, error: "invalid dates" };
  }
  const g = Math.max(1, parseInt(guests || "2", 10) || 2);
  const cfg = await getConfig(env);
  if (await isSyncStale(env, prop.id, Number(cfg.stale_breaker_hours))) {
    return { ok: true, quote: {
      property: prop.slug,
      propertyName: prop.name,
      checkIn,
      checkOut,
      available: false,
      mode: "call_to_book",
      message: "Live availability is briefly unavailable. Please call or email to book these dates and we\u2019ll confirm personally."
    } };
  }
  let vrn = await vrnQuote(prop.id, checkIn, checkOut, g);
  if (!vrn.available) {
    const fallback = await gapFallbackQuote(env, prop, checkIn, checkOut).catch(() => null);
    if (!fallback) {
      return { ok: true, quote: { property: prop.slug, propertyName: prop.name, checkIn, checkOut, available: false } };
    }
    vrn = fallback;
  }
  if (await hasActiveOverlap(env, prop.id, checkIn, checkOut)) {
    return { ok: true, quote: { property: prop.slug, propertyName: prop.name, checkIn, checkOut, available: false } };
  }
  const { pct: adjPct, applied, partner: partnerRow } = await effectiveAdjustmentPct(env, prop.id, partner);
  const markupPct = Number(cfg.markup_pct);
  const baseNightlyTotal = round2(Number(vrn.nightlyTotal));
  const cleaning = Number(vrn.cleaningFee || 0);
  const displayPct = Math.round((markupPct + adjPct) * 1e3) / 1e3;
  const markedNightlyTotal = round2(baseNightlyTotal * (1 + displayPct / 100));
  const yld = await applyYield(env, {
    prop,
    checkIn,
    checkOut,
    nights: vrn.nights,
    baseNightlyTotal,
    markedNightlyTotal,
    session: session || null
  });
  const cpn = await validateCoupon(env, coupon, prop.id, checkIn, checkOut);
  const couponDiscount = cpn.valid ? round2(yld.nightlyTotal * cpn.pct / 100) : 0;
  const nightlyTotal = round2(yld.nightlyTotal - couponDiscount);
  const adjustmentTrail = [
    { rule: "markup", pct: markupPct, amount: round2(baseNightlyTotal * markupPct / 100) },
    ...adjPct ? [{ rule: "owner_adjustments", pct: adjPct, amount: round2(baseNightlyTotal * adjPct / 100) }] : [],
    ...yld.adjustments,
    ...couponDiscount ? [{ rule: `coupon_${cpn.code}`, pct: -cpn.pct, amount: -couponDiscount }] : []
  ];
  const waiveFees = cpn.valid && cpn.coupon?.waive_fees === true;
  const chargedCleaning = waiveFees ? 0 : cleaning;
  const excludeBF = cfg.exclude_booking_fee !== false;
  const keptFees = waiveFees ? [] : (vrn.fees || []).filter((f) => !(excludeBF && /booking\s*fee/i.test(f.name)));
  const excludedFees = waiveFees ? vrn.fees || [] : (vrn.fees || []).filter((f) => excludeBF && /booking\s*fee/i.test(f.name));
  const adminFeePct = Number(cfg.admin_fee_pct || 0);
  const adminFee = !waiveFees && adminFeePct > 0 ? round2(nightlyTotal * adminFeePct / 100) : 0;
  if (adminFee > 0) {
    keptFees.push({ name: "Admin Fee", amount: adminFee });
    adjustmentTrail.push({ rule: "admin_fee", pct: adminFeePct, amount: adminFee });
  }
  const keptFeesTotal = round2(keptFees.reduce((a, f) => a + Number(f.amount), 0));
  if (waiveFees) {
    adjustmentTrail.push({
      rule: `coupon_${cpn.code}_waive_fees`,
      amount: -round2(cleaning + (vrn.fees || []).reduce((a, f) => a + Number(f.amount), 0))
    });
  }
  const vrnTaxBase = baseNightlyTotal + cleaning;
  const taxes = (vrn.taxes || []).map((t) => {
    const rate = vrnTaxBase > 0 ? Number(t.amount) / vrnTaxBase : 0;
    return { name: t.name, rate: round2(rate * 100), amount: round2((nightlyTotal + chargedCleaning) * rate) };
  });
  const taxesTotal = round2(taxes.reduce((a, t) => a + t.amount, 0));
  const total = round2(nightlyTotal + chargedCleaning + keptFeesTotal + taxesTotal);
  const checkInMs = (/* @__PURE__ */ new Date(`${checkIn}T00:00:00Z`)).getTime();
  const daysToCheckIn = Math.floor((checkInMs - Date.now()) / 864e5);
  const balanceDaysBefore = Number(cfg.balance_days_before);
  const fullNow = daysToCheckIn <= balanceDaysBefore;
  const depositPct = Number(cfg.deposit_pct);
  const deposit = fullNow ? total : round2(total * depositPct / 100);
  const balance = round2(total - deposit);
  const balanceDueDate = fullNow ? null : addDays(checkIn, -balanceDaysBefore);
  const vrnTotal = vrn.total != null ? Number(vrn.total) : null;
  return {
    ok: true,
    quote: {
      property: prop.slug,
      propertyName: prop.name,
      checkIn,
      checkOut,
      nights: vrn.nights,
      guests: g,
      available: true,
      currency: vrn.currency || "USD",
      // pricing
      baseNightlyTotal,
      markupPct,
      adjustmentPct: adjPct,
      nightlyRate: round2(nightlyTotal / vrn.nights),
      nightlyTotal,
      cleaningFee: chargedCleaning,
      fees: keptFees,
      taxes,
      taxesTotal,
      total,
      // yield engine
      yieldAdjustments: adjustmentTrail,
      yieldProtected: yld.protected || void 0,
      yieldFloorPct: yld.floorPct,
      lockinOffer: yld.lockinOffer,
      abBucket: yld.abBucket,
      minStayRelaxed: vrn.minStayRelaxed || void 0,
      // coupon
      coupon: cpn.valid ? { code: cpn.code, pct: cpn.pct, discount: couponDiscount } : null,
      couponError: coupon && !cpn.valid ? cpn.reason : null,
      // payment split
      depositPct: fullNow ? 100 : depositPct,
      deposit,
      balance,
      balanceDueDate,
      fullPaymentNow: fullNow,
      // context
      adjustmentsApplied: applied.map((a) => ({ scope: a.scope, pct: Number(a.pct), note: a.note || null })),
      partner: partnerRow ? { slug: partnerRow.slug, name: partnerRow.name } : null,
      excludedFees,
      compare: vrnTotal != null ? { vrnTotal, directSavings: round2(vrnTotal - total) } : null,
      disclaimers: DISCLAIMERS
    }
  };
}
__name(buildQuote, "buildQuote");
function reservationEmailBody(prop, resv, q) {
  const feeLines = (q.fees || []).map((f) => `  ${f.name}: $${fmt(f.amount)}`).join("\n");
  const taxLines = (q.taxes || []).map((t) => `  ${t.name} (${t.rate}%): $${fmt(t.amount)}`).join("\n");
  return `DIRECT SUN & MOON RESERVATION
=====================================
Property:   ${prop.name}
Check-in:   ${resv.check_in}
Check-out:  ${resv.check_out}  (${q.nights} nights)
Guests:     ${resv.guests}

Guest:      ${resv.guest_name}
Email:      ${resv.guest_email}
Phone:      ${resv.guest_phone || "\u2014"}
${resv.partner_slug ? `Partner:    ${resv.partner_slug}
` : ""}
PRICING (booking fee excluded)
  Nightly total:   $${fmt(q.nightlyTotal)}${q.adjustmentPct ? `  (rate adjustment ${q.adjustmentPct > 0 ? "+" : ""}${q.adjustmentPct}%)` : ""}
  Cleaning fee:    $${fmt(q.cleaningFee)}
${feeLines}
${taxLines}
  TOTAL:           $${fmt(q.total)}
  (VacayRentalNetwork total for the same stay: $${fmt(q.compare.vrnTotal)} \u2014 direct saves $${fmt(q.compare.directSavings)})

Reservation ID: ${resv.id}
Status: RESERVED \u2014 dates are blocked on the direct calendar.
Next steps: send the rental agreement + payment instructions within 48h.

-------------------------------------
DISCLOSURES PROVIDED TO GUEST
${DISCLAIMERS.map((d, i) => `${i + 1}. ${d}`).join("\n")}
`;
}
__name(reservationEmailBody, "reservationEmailBody");
async function handleReserve(request, env, ctx) {
  let body;
  try {
    body = await request.json();
  } catch {
    return json(400, { ok: false, error: "invalid JSON" });
  }
  const name = String(body.name || "").trim().slice(0, 200);
  const email = String(body.email || "").trim().slice(0, 320);
  const phone = String(body.phone || "").trim().slice(0, 40);
  if (!name || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    return json(400, { ok: false, error: "name and a valid email are required" });
  }
  const q = await buildQuote(env, { ...body, session: body.session_id || null });
  if (!q.ok) return json(q.status, { ok: false, error: q.error });
  if (!q.quote.available) return json(409, { ok: false, error: "dates not available" });
  const prop = await getProperty(env, body.property);
  const quote = q.quote;
  const refresh = await refreshAvailabilityWindow(env, prop.id, quote.checkIn, quote.checkOut, "pre-booking");
  if (!refresh.windowOpen || await hasActiveOverlap(env, prop.id, quote.checkIn, quote.checkOut)) {
    return json(409, { ok: false, error: refresh.ok ? "dates not available" : "We couldn't verify live availability just now \u2014 please try again in a moment." });
  }
  let partnerId = null;
  const partnerSlug = quote.partner?.slug || body.partner || null;
  if (partnerSlug) {
    const pr = await sb(
      env,
      "GET",
      `/rest/v1/rev_share_partners?slug=eq.${encodeURIComponent(partnerSlug)}&select=id&limit=1`
    ).catch(() => []);
    partnerId = pr[0]?.id || null;
  }
  const confirmationCode = "SM-" + quote.checkIn.replace(/-/g, "").slice(2) + "-" + Math.random().toString(36).slice(2, 6).toUpperCase();
  const [resv] = await sb(env, "POST", "/rest/v1/reservations", {
    property_id: prop.id,
    source: "direct",
    status: "reserved",
    confirmation_code: confirmationCode,
    check_in: quote.checkIn,
    check_out: quote.checkOut,
    guest_name: name,
    guest_email: email,
    guest_phone: phone || null,
    guests: quote.guests,
    base_nightly_total: quote.baseNightlyTotal,
    markup_pct: quote.markupPct,
    nightly_total: quote.nightlyTotal,
    cleaning_fee: quote.cleaningFee,
    other_fees: quote.fees,
    taxes: quote.taxes,
    tax_breakdown: quote.taxes,
    // per-booking, for occupancy-tax remittance reporting
    excluded_fees: quote.excludedFees,
    total: quote.total,
    currency: quote.currency,
    partner_slug: partnerSlug,
    partner_id: partnerId,
    adjustment_pct: quote.adjustmentPct,
    coupon_code: quote.coupon?.code || null,
    coupon_discount: quote.coupon?.discount || 0,
    deposit_amount: quote.deposit,
    balance_amount: quote.balance,
    balance_due_date: quote.balanceDueDate,
    payment_status: "unpaid",
    // becomes deposit_paid/paid once Stripe checkout lands
    yield_adjustments: quote.yieldAdjustments || null,
    ab_bucket: quote.abBucket || null,
    notes: String(body.notes || "").slice(0, 2e3) || null
  }, { "Prefer": "return=representation" });
  if (body.session_id && quote.lockinOffer) {
    ctx.waitUntil(sb(
      env,
      "PATCH",
      `/rest/v1/lockin_offers?session_id=eq.${encodeURIComponent(body.session_id)}&status=eq.active`,
      { status: "redeemed", reservation_id: resv.id },
      { "Prefer": "return=minimal" }
    ).catch(() => {
    }));
  }
  if (quote.coupon?.code) {
    const cr = await sb(
      env,
      "GET",
      `/rest/v1/coupons?code=eq.${encodeURIComponent(quote.coupon.code)}&select=id,redemptions&limit=1`
    ).catch(() => []);
    if (cr[0]) {
      await sb(env, "POST", "/rest/v1/coupon_redemptions", [{
        coupon_id: cr[0].id,
        code: quote.coupon.code,
        reservation_id: resv.id,
        discount: quote.coupon.discount
      }], { "Prefer": "return=minimal" }).catch(() => {
      });
      await sb(
        env,
        "PATCH",
        `/rest/v1/coupons?id=eq.${cr[0].id}`,
        { redemptions: Number(cr[0].redemptions) + 1 },
        { "Prefer": "return=minimal" }
      ).catch(() => {
      });
    }
  }
  const days = [];
  for (let d = quote.checkIn; d < quote.checkOut; d = addDays(d, 1)) {
    days.push({
      property_id: prop.id,
      day: d,
      available: false,
      block_source: "direct",
      synced_at: (/* @__PURE__ */ new Date()).toISOString()
    });
  }
  await sb(
    env,
    "POST",
    "/rest/v1/rate_calendar?on_conflict=property_id,day",
    days,
    { "Prefer": "resolution=merge-duplicates,return=minimal" }
  );
  const emailBody = reservationEmailBody(prop, resv, quote);
  const subject = `DIRECT Sun & Moon reservation \u2014 ${prop.name}, ${quote.checkIn} \u2192 ${quote.checkOut} (${name}) \xB7 ${confirmationCode}`;
  const sms = `Direct Sun & Moon booking ${confirmationCode}: ${prop.name}, ${quote.checkIn}->${quote.checkOut}, ${quote.guests} guests, $${fmt(quote.total)}. Guest ${name} ${phone || email}.`;
  await queueNotifications(env, [
    { reservation_id: resv.id, channel: "email", recipient: env.NOTIFY_OWNER_EMAIL, subject, body: emailBody },
    { reservation_id: resv.id, channel: "sms", recipient: env.NOTIFY_OWNER_SMS, subject: null, body: sms }
  ]);
  ctx.waitUntil(notifyMindyBooking(env, prop, resv, quote).catch(() => {
  }));
  ctx.waitUntil(sb(env, "POST", "/rest/v1/booking_events", {
    session_id: body.session_id || null,
    event: "confirmed",
    property_id: prop.id,
    partner_slug: partnerSlug,
    metadata: { reservation_id: resv.id, total: quote.total, confirmation: confirmationCode }
  }, { "Prefer": "return=minimal" }).catch(() => {
  }));
  ctx.waitUntil(sb(env, "POST", "/rest/v1/tracking_events", {
    project_id: env.PROJECT_ID,
    event_type: "booking_inquiry",
    source: "direct_booking",
    metadata: { kind: "direct_reservation", reservation_id: resv.id, property: prop.slug, total: quote.total, partner: partnerSlug }
  }, { "Prefer": "return=minimal" }).catch(() => {
  }));
  return json(200, {
    ok: true,
    reservationId: resv.id,
    confirmationCode,
    status: "reserved",
    holdHours: 48,
    quote,
    message: "Dates reserved on the direct calendar. You will receive the rental agreement and payment instructions to confirm.",
    disclaimers: DISCLAIMERS
  });
}
__name(handleReserve, "handleReserve");
var HOLD_MINUTES = 20;
async function handleCheckoutCreate(request, env, ctx) {
  if (!stripeConfigured(env)) return json(503, { ok: false, error: "payments not configured" });
  let body;
  try {
    body = await request.json();
  } catch {
    return json(400, { ok: false, error: "invalid JSON" });
  }
  const name = String(body.name || "").trim().slice(0, 200);
  const email = String(body.email || "").trim().slice(0, 320);
  const phone = String(body.phone || "").trim().slice(0, 40);
  if (!name || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    return json(400, { ok: false, error: "name and a valid email are required" });
  }
  const q = await buildQuote(env, { ...body, session: body.session_id || null });
  if (!q.ok) return json(q.status, { ok: false, error: q.error });
  if (!q.quote.available) return json(409, { ok: false, error: "dates not available" });
  const quote = q.quote;
  const prop = await getProperty(env, body.property);
  const refresh = await refreshAvailabilityWindow(env, prop.id, quote.checkIn, quote.checkOut, "pre-booking");
  if (!refresh.windowOpen || await hasActiveOverlap(env, prop.id, quote.checkIn, quote.checkOut)) {
    return json(409, { ok: false, error: refresh.ok ? "dates not available" : "We couldn't verify live availability just now \u2014 please try again in a moment." });
  }
  let partnerId = null;
  const partnerSlug = quote.partner?.slug || body.partner || null;
  if (partnerSlug) {
    const pr = await sb(
      env,
      "GET",
      `/rest/v1/rev_share_partners?slug=eq.${encodeURIComponent(partnerSlug)}&select=id&limit=1`
    ).catch(() => []);
    partnerId = pr[0]?.id || null;
  }
  const confirmationCode = "SM-" + quote.checkIn.replace(/-/g, "").slice(2) + "-" + Math.random().toString(36).slice(2, 6).toUpperCase();
  const holdExpires = new Date(Date.now() + HOLD_MINUTES * 6e4).toISOString();
  const [resv] = await sb(env, "POST", "/rest/v1/reservations", {
    property_id: prop.id,
    source: "direct",
    status: "pending",
    confirmation_code: confirmationCode,
    check_in: quote.checkIn,
    check_out: quote.checkOut,
    guest_name: name,
    guest_email: email,
    guest_phone: phone || null,
    guests: quote.guests,
    base_nightly_total: quote.baseNightlyTotal,
    markup_pct: quote.markupPct,
    nightly_total: quote.nightlyTotal,
    cleaning_fee: quote.cleaningFee,
    other_fees: quote.fees,
    taxes: quote.taxes,
    tax_breakdown: quote.taxes,
    excluded_fees: quote.excludedFees,
    total: quote.total,
    currency: quote.currency,
    partner_slug: partnerSlug,
    partner_id: partnerId,
    adjustment_pct: quote.adjustmentPct,
    coupon_code: quote.coupon?.code || null,
    coupon_discount: quote.coupon?.discount || 0,
    deposit_amount: quote.deposit,
    balance_amount: quote.balance,
    balance_due_date: quote.balanceDueDate,
    payment_status: "unpaid",
    soft_hold_expires_at: holdExpires,
    yield_adjustments: quote.yieldAdjustments || null,
    ab_bucket: quote.abBucket || null,
    notes: String(body.notes || "").slice(0, 2e3) || null
  }, { "Prefer": "return=representation" });
  if (body.session_id && quote.lockinOffer) {
    ctx.waitUntil(sb(
      env,
      "PATCH",
      `/rest/v1/lockin_offers?session_id=eq.${encodeURIComponent(body.session_id)}&status=eq.active`,
      { status: "redeemed", reservation_id: resv.id },
      { "Prefer": "return=minimal" }
    ).catch(() => {
    }));
  }
  try {
    const customer = await stripe(env, "POST", "/customers", {
      email,
      name,
      metadata: { reservation_id: resv.id, confirmation_code: confirmationCode }
    });
    const pi = await stripe(env, "POST", "/payment_intents", {
      amount: cents(quote.deposit),
      currency: (quote.currency || "usd").toLowerCase(),
      customer: customer.id,
      setup_future_usage: quote.fullPaymentNow ? void 0 : "off_session",
      // save card for the balance
      automatic_payment_methods: { enabled: true },
      description: `${quote.fullPaymentNow ? "Full payment" : "Deposit"} \u2014 ${prop.name} ${quote.checkIn}\u2192${quote.checkOut}`,
      receipt_email: email,
      metadata: {
        reservation_id: resv.id,
        confirmation_code: confirmationCode,
        kind: quote.fullPaymentNow ? "full" : "deposit",
        property: prop.slug
      }
    }, `pi_create_${resv.id}`);
    await sb(
      env,
      "PATCH",
      `/rest/v1/reservations?id=eq.${resv.id}`,
      { stripe_customer_id: customer.id, stripe_payment_intent_id: pi.id },
      { "Prefer": "return=minimal" }
    );
    ctx.waitUntil(sb(env, "POST", "/rest/v1/booking_events", {
      session_id: body.session_id || null,
      event: "payment_started",
      property_id: prop.id,
      partner_slug: partnerSlug,
      metadata: { reservation_id: resv.id, amount: quote.deposit }
    }, { "Prefer": "return=minimal" }).catch(() => {
    }));
    return json(200, {
      ok: true,
      reservationId: resv.id,
      confirmationCode,
      paymentIntentId: pi.id,
      clientSecret: pi.client_secret,
      publishableKey: env.STRIPE_PUBLISHABLE_KEY,
      amountDue: quote.deposit,
      fullPaymentNow: quote.fullPaymentNow,
      quote,
      holdMinutes: HOLD_MINUTES
    });
  } catch (e) {
    await sb(
      env,
      "PATCH",
      `/rest/v1/reservations?id=eq.${resv.id}`,
      { status: "cancelled", last_payment_error: String(e).slice(0, 400) },
      { "Prefer": "return=minimal" }
    ).catch(() => {
    });
    console.log("checkout create error", e?.stack || e);
    return json(502, { ok: false, error: "could not start payment" });
  }
}
__name(handleCheckoutCreate, "handleCheckoutCreate");
async function handleCheckoutFinalize(request, env, ctx) {
  if (!stripeConfigured(env)) return json(503, { ok: false, error: "payments not configured" });
  let body;
  try {
    body = await request.json();
  } catch {
    return json(400, { ok: false, error: "invalid JSON" });
  }
  const { reservationId, paymentIntentId } = body;
  if (!reservationId || !paymentIntentId) return json(400, { ok: false, error: "reservationId and paymentIntentId required" });
  const rows = await sb(env, "GET", `/rest/v1/reservations?id=eq.${reservationId}&select=*&limit=1`);
  const resv = rows[0];
  if (!resv) return json(404, { ok: false, error: "reservation not found" });
  if (["deposit_paid", "paid"].includes(resv.payment_status)) {
    return json(200, {
      ok: true,
      confirmationCode: resv.confirmation_code,
      status: resv.status,
      paymentStatus: resv.payment_status,
      alreadyFinalized: true
    });
  }
  const pi = await stripe(env, "GET", `/payment_intents/${encodeURIComponent(paymentIntentId)}`);
  if (pi.metadata?.reservation_id !== reservationId) return json(400, { ok: false, error: "payment/reservation mismatch" });
  if (pi.status !== "succeeded") return json(402, { ok: false, error: "payment not completed", paymentStatus: pi.status });
  const prop = await getProperty(env, resv.property_id);
  const refresh = await refreshAvailabilityWindow(env, resv.property_id, resv.check_in, resv.check_out, "pre-booking", resv.id);
  const conflict = !refresh.windowOpen || await hasActiveOverlap(env, resv.property_id, resv.check_in, resv.check_out, resv.id);
  if (conflict) {
    await stripe(
      env,
      "POST",
      "/refunds",
      { payment_intent: pi.id, reason: "requested_by_customer" },
      `refund_conflict_${resv.id}`
    ).catch(() => {
    });
    await sb(
      env,
      "PATCH",
      `/rest/v1/reservations?id=eq.${resv.id}`,
      {
        status: "cancelled",
        payment_status: "refunded",
        amount_refunded: resv.deposit_amount,
        last_payment_error: refresh.ok ? "dates taken during checkout \u2014 auto-refunded" : `availability refresh failed (${refresh.error}) \u2014 auto-refunded`
      },
      { "Prefer": "return=minimal" }
    );
    return json(409, { ok: false, error: "Those dates were just taken \u2014 your deposit was refunded in full. Please choose different nights." });
  }
  const isFull = !resv.balance_amount || Number(resv.balance_amount) <= 0;
  const paidNow = pi.amount_received / 100;
  await sb(env, "PATCH", `/rest/v1/reservations?id=eq.${resv.id}`, {
    status: "reserved",
    payment_status: isFull ? "paid" : "deposit_paid",
    amount_paid: paidNow,
    deposit_paid_at: (/* @__PURE__ */ new Date()).toISOString(),
    stripe_payment_method_id: pi.payment_method || null,
    soft_hold_expires_at: null,
    updated_at: (/* @__PURE__ */ new Date()).toISOString()
  }, { "Prefer": "return=minimal" });
  await finalizeConfirmedBooking(env, ctx, prop, { ...resv, payment_status: isFull ? "paid" : "deposit_paid" });
  return json(200, {
    ok: true,
    confirmationCode: resv.confirmation_code,
    status: "reserved",
    paymentStatus: isFull ? "paid" : "deposit_paid",
    amountPaid: paidNow,
    balance: isFull ? 0 : Number(resv.balance_amount),
    balanceDueDate: isFull ? null : resv.balance_due_date
  });
}
__name(handleCheckoutFinalize, "handleCheckoutFinalize");
async function finalizeConfirmedBooking(env, ctx, prop, resv) {
  const days = [];
  for (let d = resv.check_in; d < resv.check_out; d = addDays(d, 1)) {
    days.push({
      property_id: prop.id,
      day: d,
      available: false,
      block_source: "direct",
      synced_at: (/* @__PURE__ */ new Date()).toISOString()
    });
  }
  await sb(
    env,
    "POST",
    "/rest/v1/rate_calendar?on_conflict=property_id,day",
    days,
    { "Prefer": "resolution=merge-duplicates,return=minimal" }
  ).catch(() => {
  });
  if (resv.coupon_code) {
    const already = await sb(
      env,
      "GET",
      `/rest/v1/coupon_redemptions?reservation_id=eq.${resv.id}&select=id&limit=1`
    ).catch(() => []);
    if (!already.length) {
      const cr = await sb(
        env,
        "GET",
        `/rest/v1/coupons?code=eq.${encodeURIComponent(resv.coupon_code)}&select=id,redemptions&limit=1`
      ).catch(() => []);
      if (cr[0]) {
        await sb(env, "POST", "/rest/v1/coupon_redemptions", [{
          coupon_id: cr[0].id,
          code: resv.coupon_code,
          reservation_id: resv.id,
          discount: resv.coupon_discount || 0
        }], { "Prefer": "return=minimal" }).catch(() => {
        });
        await sb(
          env,
          "PATCH",
          `/rest/v1/coupons?id=eq.${cr[0].id}`,
          { redemptions: Number(cr[0].redemptions) + 1 },
          { "Prefer": "return=minimal" }
        ).catch(() => {
        });
      }
    }
  }
  const paidLabel = resv.payment_status === "paid" ? `PAID IN FULL ($${fmt(resv.total)})` : `DEPOSIT PAID ($${fmt(resv.deposit_amount)}); balance $${fmt(resv.balance_amount)} auto-charges ${resv.balance_due_date}`;
  const ownerBody = `CONFIRMED DIRECT BOOKING ${resv.confirmation_code}
${prop.name}
${resv.check_in} \u2192 ${resv.check_out} \xB7 ${resv.guests} guests
Guest: ${resv.guest_name} \xB7 ${resv.guest_email} \xB7 ${resv.guest_phone || "\u2014"}
${paidLabel}
Total $${fmt(resv.total)} (incl. cleaning + taxes; booking fee excluded)
${resv.coupon_code ? `Coupon: ${resv.coupon_code} (\u2212$${fmt(resv.coupon_discount)})
` : ""}${resv.partner_slug ? `Partner: ${resv.partner_slug}
` : ""}Dates are blocked on the direct calendar.`;
  await queueNotifications(env, [
    {
      reservation_id: resv.id,
      channel: "email",
      recipient: env.NOTIFY_OWNER_EMAIL,
      subject: `PAID booking ${resv.confirmation_code} \u2014 ${prop.name} ${resv.check_in}\u2192${resv.check_out}`,
      body: ownerBody
    },
    {
      reservation_id: resv.id,
      channel: "sms",
      recipient: env.NOTIFY_OWNER_SMS,
      subject: null,
      body: `Confirmed booking ${resv.confirmation_code}: ${prop.name} ${resv.check_in}\u2192${resv.check_out}, ${resv.guests} guests, ${paidLabel}.`
    }
  ]);
  const taxLines = (resv.tax_breakdown || []).map((t) => `  ${t.name} (${t.rate}%): $${fmt(t.amount)}`).join("\n");
  const receipt = `Thank you, ${resv.guest_name}! Your reservation is confirmed.

Confirmation #: ${resv.confirmation_code}
${prop.name}
Check-in:  ${resv.check_in}
Check-out: ${resv.check_out}  (${resv.guests} guests)

Nightly:    $${fmt(resv.nightly_total)}
Cleaning:   $${fmt(resv.cleaning_fee)}
${taxLines}
Total:      $${fmt(resv.total)}
${resv.payment_status === "paid" ? `Paid in full: $${fmt(resv.total)}` : `Deposit paid: $${fmt(resv.deposit_amount)}
Balance:      $${fmt(resv.balance_amount)} \u2014 automatically charged to your card on ${resv.balance_due_date}`}

We can't wait to host you at Sun & Moon at 30A.
Questions? experience@sunandmoon30a.com`;
  await queueNotifications(env, [
    {
      reservation_id: resv.id,
      channel: "email",
      recipient: resv.guest_email,
      subject: `Your Sun & Moon reservation is confirmed \u2014 ${resv.confirmation_code}`,
      body: receipt
    }
  ]);
  ctx.waitUntil(notifyMindyBooking(env, prop, resv, {
    nights: nightsBetween(resv.check_in, resv.check_out),
    guests: resv.guests
  }).catch(() => {
  }));
  ctx.waitUntil(sb(env, "POST", "/rest/v1/booking_events", {
    session_id: null,
    event: "confirmed",
    property_id: prop.id,
    partner_slug: resv.partner_slug,
    metadata: { reservation_id: resv.id, total: resv.total, confirmation: resv.confirmation_code, paid: resv.payment_status }
  }, { "Prefer": "return=minimal" }).catch(() => {
  }));
  ctx.waitUntil(sb(env, "POST", "/rest/v1/tracking_events", {
    project_id: env.PROJECT_ID,
    event_type: "booking_inquiry",
    source: "direct_booking",
    metadata: { kind: "paid_reservation", reservation_id: resv.id, property: prop.slug, total: resv.total, partner: resv.partner_slug }
  }, { "Prefer": "return=minimal" }).catch(() => {
  }));
}
__name(finalizeConfirmedBooking, "finalizeConfirmedBooking");
async function verifyWebhookAuth(env, request, rawBody) {
  if (env.WEBHOOK_HMAC_SECRET) {
    const sig = (request.headers.get("x-vacay-signature") || "").replace(/^sha256=/, "").toLowerCase();
    if (!sig) return { ok: false, why: "missing x-vacay-signature" };
    const key = await crypto.subtle.importKey(
      "raw",
      new TextEncoder().encode(env.WEBHOOK_HMAC_SECRET),
      { name: "HMAC", hash: "SHA-256" },
      false,
      ["sign"]
    );
    const mac = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(rawBody));
    const expected = [...new Uint8Array(mac)].map((b) => b.toString(16).padStart(2, "0")).join("");
    if (sig.length !== expected.length) return { ok: false, why: "signature mismatch" };
    let diff = 0;
    for (let i = 0; i < sig.length; i++) diff |= sig.charCodeAt(i) ^ expected.charCodeAt(i);
    return diff === 0 ? { ok: true, method: "hmac" } : { ok: false, why: "signature mismatch" };
  }
  if (env.WEBHOOK_SECRET && request.headers.get("x-webhook-secret") === env.WEBHOOK_SECRET) {
    return { ok: true, method: "shared-secret" };
  }
  return { ok: false, why: "bad or missing credentials" };
}
__name(verifyWebhookAuth, "verifyWebhookAuth");
async function handleWebhook(request, env) {
  const rawBody = await request.text();
  const auth = await verifyWebhookAuth(env, request, rawBody);
  if (!auth.ok) {
    console.log(JSON.stringify({ at: (/* @__PURE__ */ new Date()).toISOString(), evt: "webhook_rejected", reason: auth.why }));
    return json(401, { ok: false, error: "unauthorized" });
  }
  let body;
  try {
    body = JSON.parse(rawBody);
  } catch {
    return json(400, { ok: false, error: "invalid JSON" });
  }
  const prop = await getProperty(env, String(body.property || ""));
  const { start, end } = body;
  const action = body.action === "cancelled" ? "cancelled" : "booked";
  if (!prop || !isDate(start) || !isDate(end) || nightsBetween(start, end) < 1) {
    console.log(JSON.stringify({ at: (/* @__PURE__ */ new Date()).toISOString(), evt: "webhook_bad_payload", auth: auth.method, body }));
    return json(400, { ok: false, error: "need property, start, end (YYYY-MM-DD)" });
  }
  const ref = String(body.ref || `wh_${start}_${end}`);
  console.log(JSON.stringify({
    at: (/* @__PURE__ */ new Date()).toISOString(),
    evt: "webhook_ok",
    auth: auth.method,
    property: prop.slug,
    action,
    start,
    end,
    ref
  }));
  await recordSyncState(env, prop.id, "webhook", true, `${action} ${start}..${end}`);
  await writeAudit(env, [{
    entity: "availability",
    property_id: prop.id,
    day: start,
    old_value: null,
    new_value: { action, range: `${start}..${end}`, ref },
    source: "webhook",
    note: `webhook ${action}`
  }]);
  if (action === "booked") {
    await sb(env, "POST", "/rest/v1/reservations?on_conflict=property_id,external_ref", [{
      property_id: prop.id,
      source: "webhook",
      status: "blocked",
      check_in: start,
      check_out: end,
      external_ref: ref,
      notes: "blocked via webhook"
    }], { "Prefer": "resolution=merge-duplicates,return=minimal" });
  } else {
    await sb(
      env,
      "PATCH",
      `/rest/v1/reservations?property_id=eq.${prop.id}&external_ref=eq.${encodeURIComponent(ref)}`,
      { status: "cancelled", updated_at: (/* @__PURE__ */ new Date()).toISOString() },
      { "Prefer": "return=minimal" }
    );
  }
  const days = [];
  for (let d = start; d < end; d = addDays(d, 1)) {
    days.push({
      property_id: prop.id,
      day: d,
      available: action === "cancelled",
      block_source: action === "booked" ? "webhook" : null,
      synced_at: (/* @__PURE__ */ new Date()).toISOString()
    });
  }
  await sb(
    env,
    "POST",
    "/rest/v1/rate_calendar?on_conflict=property_id,day",
    days,
    { "Prefer": "resolution=merge-duplicates,return=minimal" }
  );
  return json(200, { ok: true, property: prop.slug, action, start, end, ref });
}
__name(handleWebhook, "handleWebhook");
async function handleStripeWebhook(request, env, ctx) {
  const raw = await request.text();
  const sig = request.headers.get("stripe-signature");
  if (env.STRIPE_WEBHOOK_SECRET) {
    const v = await verifyStripeSig(env.STRIPE_WEBHOOK_SECRET, raw, sig);
    if (!v.ok) {
      console.log(JSON.stringify({ at: (/* @__PURE__ */ new Date()).toISOString(), evt: "stripe_webhook_rejected", why: v.why }));
      return json(400, { ok: false, error: "signature verification failed" });
    }
  }
  let event;
  try {
    event = JSON.parse(raw);
  } catch {
    return json(400, { ok: false });
  }
  const obj = event.data?.object || {};
  const resvId = obj.metadata?.reservation_id;
  console.log(JSON.stringify({ at: (/* @__PURE__ */ new Date()).toISOString(), evt: "stripe_webhook", type: event.type, resv: resvId || null }));
  try {
    if (event.type === "payment_intent.succeeded" && resvId) {
      const rows = await sb(env, "GET", `/rest/v1/reservations?id=eq.${resvId}&select=*&limit=1`);
      const resv = rows[0];
      if (resv && !["deposit_paid", "paid"].includes(resv.payment_status) && obj.metadata?.kind !== "balance") {
        const isFull = !resv.balance_amount || Number(resv.balance_amount) <= 0;
        await sb(env, "PATCH", `/rest/v1/reservations?id=eq.${resv.id}`, {
          status: "reserved",
          payment_status: isFull ? "paid" : "deposit_paid",
          amount_paid: obj.amount_received / 100,
          deposit_paid_at: (/* @__PURE__ */ new Date()).toISOString(),
          stripe_payment_method_id: obj.payment_method || null,
          soft_hold_expires_at: null
        }, { "Prefer": "return=minimal" });
        const prop = await getProperty(env, resv.property_id);
        await finalizeConfirmedBooking(env, ctx, prop, { ...resv, payment_status: isFull ? "paid" : "deposit_paid" });
      }
    } else if (event.type === "payment_intent.payment_failed" && resvId) {
      const kind = obj.metadata?.kind;
      if (kind === "balance") {
        await notifyOwnerAlert(
          env,
          `Balance charge FAILED \u2014 ${obj.metadata?.confirmation_code || resvId}`,
          `The scheduled balance charge failed: ${obj.last_payment_error?.message || "card declined"}. Guest card may need updating; dunning retry scheduled.`
        );
      }
    } else if (event.type === "charge.dispute.created") {
      await notifyOwnerAlert(
        env,
        "Stripe DISPUTE opened",
        `A charge was disputed (${obj.amount / 100} ${(obj.currency || "usd").toUpperCase()}). Respond in the Stripe dashboard before the deadline.`
      );
    } else if (event.type === "charge.refunded" && obj.payment_intent) {
      const rows = await sb(
        env,
        "GET",
        `/rest/v1/reservations?stripe_payment_intent_id=eq.${encodeURIComponent(obj.payment_intent)}&select=id,total&limit=1`
      ).catch(() => []);
      if (rows[0]) {
        const refunded = obj.amount_refunded / 100;
        await sb(
          env,
          "PATCH",
          `/rest/v1/reservations?id=eq.${rows[0].id}`,
          { amount_refunded: refunded, payment_status: refunded >= Number(rows[0].total) ? "refunded" : "partial_refund" },
          { "Prefer": "return=minimal" }
        );
      }
    }
  } catch (e) {
    console.log("stripe webhook handler error", e?.stack || e);
  }
  return json(200, { received: true });
}
__name(handleStripeWebhook, "handleStripeWebhook");
async function handleRefund(env, base, reservationId, amount) {
  const rows = await sb(env, "GET", `/rest/v1/reservations?id=eq.${reservationId}&select=*&limit=1`);
  const resv = rows[0];
  if (!resv || !resv.stripe_payment_intent_id) return adminRedirect(base, "No payment on file to refund.");
  const params = { payment_intent: resv.stripe_payment_intent_id, reason: "requested_by_customer" };
  if (amount && Number(amount) > 0) params.amount = cents(amount);
  try {
    const refund = await stripe(env, "POST", "/refunds", params, `refund_${reservationId}_${Date.now()}`);
    const refundedTotal = Number(resv.amount_refunded || 0) + refund.amount / 100;
    await sb(env, "PATCH", `/rest/v1/reservations?id=eq.${resv.id}`, {
      amount_refunded: refundedTotal,
      payment_status: refundedTotal >= Number(resv.amount_paid || resv.total) ? "refunded" : "partial_refund",
      updated_at: (/* @__PURE__ */ new Date()).toISOString()
    }, { "Prefer": "return=minimal" });
    await queueNotifications(env, [{
      reservation_id: resv.id,
      channel: "email",
      recipient: resv.guest_email,
      subject: `Refund processed \u2014 ${resv.confirmation_code}`,
      body: `A refund of $${fmt(refund.amount / 100)} has been issued to your card for reservation ${resv.confirmation_code}. It typically appears in 5\u201310 business days.`
    }]);
    return adminRedirect(base, `Refunded $${fmt(refund.amount / 100)} for ${resv.confirmation_code}.`);
  } catch (e) {
    return adminRedirect(base, `Refund failed: ${String(e).slice(0, 200)}`);
  }
}
__name(handleRefund, "handleRefund");
async function runBalanceCharges(env, ctx) {
  if (!stripeConfigured(env)) return { ok: false, skipped: "stripe not configured" };
  const today = (/* @__PURE__ */ new Date()).toISOString().slice(0, 10);
  const due = await sb(
    env,
    "GET",
    `/rest/v1/reservations?payment_status=eq.deposit_paid&status=eq.reserved&balance_due_date=lte.${today}&balance_amount=gt.0&select=*`
  ).catch(() => []);
  const stats = { due: due.length, charged: 0, failed: 0 };
  for (const resv of due) {
    if (!resv.stripe_customer_id || !resv.stripe_payment_method_id) {
      stats.failed++;
      continue;
    }
    try {
      const pi = await stripe(env, "POST", "/payment_intents", {
        amount: cents(resv.balance_amount),
        currency: (resv.currency || "usd").toLowerCase(),
        customer: resv.stripe_customer_id,
        payment_method: resv.stripe_payment_method_id,
        off_session: true,
        confirm: true,
        description: `Balance \u2014 ${resv.confirmation_code}`,
        receipt_email: resv.guest_email,
        metadata: { reservation_id: resv.id, confirmation_code: resv.confirmation_code, kind: "balance" }
      }, `balance_${resv.id}`);
      if (pi.status === "succeeded") {
        stats.charged++;
        await sb(env, "PATCH", `/rest/v1/reservations?id=eq.${resv.id}`, {
          payment_status: "paid",
          amount_paid: Number(resv.amount_paid || 0) + pi.amount_received / 100,
          balance_paid_at: (/* @__PURE__ */ new Date()).toISOString(),
          balance_attempts: Number(resv.balance_attempts) + 1
        }, { "Prefer": "return=minimal" });
        await queueNotifications(env, [{
          reservation_id: resv.id,
          channel: "email",
          recipient: resv.guest_email,
          subject: `Balance paid \u2014 ${resv.confirmation_code}`,
          body: `Your remaining balance of $${fmt(resv.balance_amount)} for ${resv.confirmation_code} has been charged. You're all set \u2014 see you soon!`
        }]);
      } else {
        throw new Error(`status ${pi.status}`);
      }
    } catch (e) {
      stats.failed++;
      const attempts = Number(resv.balance_attempts) + 1;
      await sb(
        env,
        "PATCH",
        `/rest/v1/reservations?id=eq.${resv.id}`,
        { balance_attempts: attempts, last_payment_error: String(e).slice(0, 300) },
        { "Prefer": "return=minimal" }
      );
      await notifyOwnerAlert(
        env,
        `Balance charge failed \u2014 ${resv.confirmation_code} (attempt ${attempts})`,
        `${resv.guest_name}'s balance of $${fmt(resv.balance_amount)} for ${resv.check_in}\u2192${resv.check_out} failed: ${String(e).slice(0, 200)}. ${attempts >= 3 ? "MAX ATTEMPTS \u2014 contact the guest." : "Will retry."}`
      );
      if (attempts <= 2) {
        await queueNotifications(env, [{
          reservation_id: resv.id,
          channel: "email",
          recipient: resv.guest_email,
          subject: `Action needed: payment for ${resv.confirmation_code}`,
          body: `We couldn't process the balance of $${fmt(resv.balance_amount)} for your stay (${resv.check_in}\u2192${resv.check_out}). Please reply or email experience@sunandmoon30a.com to update your card. We'll retry shortly.`
        }]);
      }
    }
  }
  return { ok: true, stats };
}
__name(runBalanceCharges, "runBalanceCharges");
async function recordSyncState(env, propertyId, domain, ok, detail = null) {
  const now = (/* @__PURE__ */ new Date()).toISOString();
  const existing = await sb(
    env,
    "GET",
    `/rest/v1/sync_state?property_id=eq.${propertyId}&domain=eq.${domain}&select=consecutive_failures&limit=1`
  ).catch(() => []);
  const fails = ok ? 0 : (existing[0]?.consecutive_failures || 0) + 1;
  const patch = {
    last_attempt_at: now,
    consecutive_failures: fails,
    status: ok ? "ok" : fails >= 3 ? "failed" : "degraded",
    detail: detail ? String(detail).slice(0, 400) : null
  };
  if (ok) patch.last_success_at = now;
  await sb(
    env,
    "PATCH",
    `/rest/v1/sync_state?property_id=eq.${propertyId}&domain=eq.${domain}`,
    patch,
    { "Prefer": "return=minimal" }
  ).catch(() => {
  });
}
__name(recordSyncState, "recordSyncState");
async function writeAudit(env, entries) {
  if (!entries.length) return;
  await sb(env, "POST", "/rest/v1/sync_audit", entries, { "Prefer": "return=minimal" }).catch(() => {
  });
}
__name(writeAudit, "writeAudit");
async function captureSnapshot(env, propertyId, source, runId) {
  const today = (/* @__PURE__ */ new Date()).toISOString().slice(0, 10);
  for (let off = 1; off <= 120; off += 3) {
    const a = addDays(today, off), b = addDays(today, off + 3);
    try {
      const q = await vrnQuote(propertyId, a, b, 2);
      if (q && q.available) {
        await sb(env, "POST", "/rest/v1/rate_snapshots", [{
          property_id: propertyId,
          source,
          run_id: runId,
          nightly_rate: q.nightlyRate,
          cleaning_fee: q.cleaningFee,
          fees: q.fees || [],
          taxes: q.taxes || [],
          sample_range: `${a}..${b}`,
          raw: q
        }], { "Prefer": "return=minimal" }).catch(() => {
        });
        return { ...q, sample_range: `${a}..${b}` };
      }
    } catch (_e) {
    }
  }
  return null;
}
__name(captureSnapshot, "captureSnapshot");
async function queueAlert(env, subject, body) {
  await notifyOwnerAlert(env, subject, body);
}
__name(queueAlert, "queueAlert");
async function runValidation(env, trigger = "cron", propIndex = null) {
  const [run] = await sb(
    env,
    "POST",
    "/rest/v1/sync_runs",
    { trigger: `validation:${trigger}` },
    { "Prefer": "return=representation" }
  );
  const stats = { properties: 0, mismatches: 0, corrected: 0, checked: 0, rotation: null };
  const mismatchLog = [];
  try {
    let props = await sb(env, "GET", "/rest/v1/booking_properties?active=eq.true&select=id,slug,name");
    if (propIndex !== null && props.length) {
      props = [props[propIndex % props.length]];
      stats.rotation = props[0].slug;
    }
    const today = (/* @__PURE__ */ new Date()).toISOString().slice(0, 10);
    const audit = [];
    for (const prop of props) {
      stats.properties++;
      let propOk = true;
      try {
        const live = await captureSnapshot(env, prop.id, "validation", run.id);
        const prevRows = await sb(
          env,
          "GET",
          `/rest/v1/rate_snapshots?property_id=eq.${prop.id}&source=neq.validation&order=captured_at.desc&limit=1`
        ).catch(() => []);
        const prev = prevRows[0];
        if (live && prev) {
          stats.checked++;
          const fields = [
            ["rate", "nightly_rate", Number(prev.nightly_rate), Number(live.nightlyRate)],
            ["cleaning_fee", "cleaning_fee", Number(prev.cleaning_fee), Number(live.cleaningFee)]
          ];
          for (const [entity, _k, was, now] of fields) {
            if (was && Math.abs(was - now) > 5e-3) {
              stats.mismatches++;
              stats.corrected++;
              audit.push({
                entity,
                property_id: prop.id,
                old_value: { value: was },
                new_value: { value: now },
                source: "validation",
                run_id: run.id,
                note: "auto-corrected to Vacay"
              });
              mismatchLog.push(`${prop.slug}: ${entity} ${was} \u2192 ${now}`);
            }
          }
        }
      } catch (e) {
        propOk = false;
      }
      try {
        const live = await vrnAvailability(prop.id, today, addDays(today, 365));
        const liveKeys = new Set(live.map((b) => `${b.start}_${b.end}`));
        const storedRows = await sb(
          env,
          "GET",
          `/rest/v1/reservations?property_id=eq.${prop.id}&source=eq.vrn_sync&status=eq.blocked&check_out=gt.${today}&select=check_in,check_out,external_ref`
        ).catch(() => []);
        const storedKeys = new Set(storedRows.map((r) => `${r.check_in}_${r.check_out}`));
        for (const b of live) {
          const key = `${b.start}_${b.end}`;
          if (!storedKeys.has(key)) {
            stats.mismatches++;
            stats.corrected++;
            await sb(env, "POST", "/rest/v1/reservations?on_conflict=property_id,external_ref", [{
              property_id: prop.id,
              source: "vrn_sync",
              status: "blocked",
              check_in: b.start,
              check_out: b.end,
              external_ref: `vrn_${b.start}_${b.end}`,
              notes: b.type || "booked"
            }], { "Prefer": "resolution=merge-duplicates,return=minimal" });
            for (let d = b.start; d < b.end; d = addDays(d, 1)) {
              await sb(
                env,
                "POST",
                "/rest/v1/rate_calendar?on_conflict=property_id,day",
                [{ property_id: prop.id, day: d, available: false, block_source: "vrn_sync", synced_at: (/* @__PURE__ */ new Date()).toISOString() }],
                { "Prefer": "resolution=merge-duplicates,return=minimal" }
              );
            }
            audit.push({
              entity: "availability",
              property_id: prop.id,
              day: b.start,
              old_value: null,
              new_value: { blocked: `${b.start}..${b.end}` },
              source: "validation",
              run_id: run.id,
              note: "block present on Vacay, missing locally \u2014 added"
            });
            mismatchLog.push(`${prop.slug}: added missing block ${b.start}..${b.end}`);
          }
        }
        for (const r of storedRows) {
          const key = `${r.check_in}_${r.check_out}`;
          if (!liveKeys.has(key)) {
            stats.mismatches++;
            stats.corrected++;
            await sb(
              env,
              "PATCH",
              `/rest/v1/reservations?property_id=eq.${prop.id}&external_ref=eq.${encodeURIComponent(r.external_ref)}`,
              { status: "cancelled", updated_at: (/* @__PURE__ */ new Date()).toISOString() },
              { "Prefer": "return=minimal" }
            );
            audit.push({
              entity: "availability",
              property_id: prop.id,
              day: r.check_in,
              old_value: { blocked: `${r.check_in}..${r.check_out}` },
              new_value: null,
              source: "validation",
              run_id: run.id,
              note: "block gone on Vacay \u2014 released locally"
            });
            mismatchLog.push(`${prop.slug}: released stale block ${r.check_in}..${r.check_out}`);
          }
        }
      } catch (e) {
        propOk = false;
      }
      await recordSyncState(env, prop.id, "validation", propOk, propOk ? null : "validation error");
    }
    await writeAudit(env, audit);
    await sb(
      env,
      "PATCH",
      `/rest/v1/sync_runs?id=eq.${run.id}`,
      { finished_at: (/* @__PURE__ */ new Date()).toISOString(), ok: true, stats },
      { "Prefer": "return=minimal" }
    );
    if (stats.mismatches > 0) {
      await queueAlert(
        env,
        `Booking sync validation: ${stats.mismatches} mismatch(es) auto-corrected`,
        `The twice-daily validation found and corrected ${stats.mismatches} discrepancy(ies) vs. live Vacay:

` + mismatchLog.map((m) => `  \u2022 ${m}`).join("\n") + `

All corrected to Vacay's values. Full detail in the sync_audit log.`
      );
    }
    return { ok: true, stats, mismatches: mismatchLog };
  } catch (e) {
    await sb(
      env,
      "PATCH",
      `/rest/v1/sync_runs?id=eq.${run.id}`,
      { finished_at: (/* @__PURE__ */ new Date()).toISOString(), ok: false, stats, error: String(e).slice(0, 500) },
      { "Prefer": "return=minimal" }
    ).catch(() => {
    });
    await queueAlert(env, "Booking sync validation FAILED", `Validation run errored: ${String(e).slice(0, 400)}`);
    throw e;
  }
}
__name(runValidation, "runValidation");
async function runSync(env, trigger = "cron", tick = null) {
  const [run] = await sb(
    env,
    "POST",
    "/rest/v1/sync_runs",
    { trigger },
    { "Prefer": "return=representation" }
  );
  const stats = { properties: 0, blockedRanges: 0, daysWritten: 0, rateQuotes: 0, rateFailures: 0, rotation: null };
  try {
    const allProps = await sb(env, "GET", "/rest/v1/booking_properties?active=eq.true&select=id,slug");
    const t = tick ?? Math.floor(Date.now() / 9e5);
    const props = [allProps[t % allProps.length]];
    const rateSlice = t % 5;
    stats.rotation = `${props[0]?.slug} slice ${rateSlice}`;
    const today = (/* @__PURE__ */ new Date()).toISOString().slice(0, 10);
    const horizonAvail = addDays(today, 365);
    const horizonRates = 90;
    for (const prop of props) {
      stats.properties++;
      const blocked = await vrnAvailability(prop.id, today, horizonAvail);
      stats.blockedRanges += blocked.length;
      const blockedDays = /* @__PURE__ */ new Set();
      const upserts = [];
      for (const b of blocked) {
        for (let d = b.start; d < b.end; d = addDays(d, 1)) blockedDays.add(d);
        upserts.push({
          property_id: prop.id,
          source: "vrn_sync",
          status: "blocked",
          check_in: b.start,
          check_out: b.end,
          external_ref: `vrn_${b.start}_${b.end}`,
          notes: b.type || "booked"
        });
      }
      if (upserts.length) {
        await sb(
          env,
          "POST",
          "/rest/v1/reservations?on_conflict=property_id,external_ref",
          upserts,
          { "Prefer": "resolution=merge-duplicates,return=minimal" }
        );
      }
      const liveRefs = new Set(blocked.map((b) => `vrn_${b.start}_${b.end}`));
      const mirrored = await sb(
        env,
        "GET",
        `/rest/v1/reservations?property_id=eq.${prop.id}&source=eq.vrn_sync&status=eq.blocked&check_in=lt.${horizonAvail}&check_out=gt.${today}&select=id,external_ref,check_in,check_out`
      ).catch(() => []);
      const releaseIds = mirrored.filter((m) => m.check_in >= today && m.check_out <= horizonAvail && !liveRefs.has(m.external_ref)).map((m) => m.id);
      if (releaseIds.length) {
        await sb(
          env,
          "PATCH",
          `/rest/v1/reservations?id=in.(${releaseIds.join(",")})`,
          { status: "cancelled", notes: "released: no longer blocked on Vacay" },
          { "Prefer": "return=minimal" }
        ).catch(() => {
        });
      }
      const ours = await sb(
        env,
        "GET",
        `/rest/v1/reservations?property_id=eq.${prop.id}&status=not.in.(cancelled)&source=in.(direct,webhook)&check_out=gt.${today}&select=check_in,check_out`
      );
      const ourDays = /* @__PURE__ */ new Set();
      for (const r of ours) {
        for (let d = r.check_in < today ? today : r.check_in; d < r.check_out; d = addDays(d, 1)) ourDays.add(d);
      }
      const rateByDay = {};
      const sliceStart = rateSlice * 18;
      for (let offset = sliceStart; offset < Math.min(sliceStart + 18, horizonRates); offset += 3) {
        const a = addDays(today, offset), b = addDays(today, Math.min(offset + 3, horizonRates));
        try {
          const qres = await vrnQuote(prop.id, a, b, 2);
          stats.rateQuotes++;
          if (qres.nightlyRate) {
            for (let d = a; d < b; d = addDays(d, 1)) rateByDay[d] = Number(qres.nightlyRate);
          }
        } catch (_e) {
          stats.rateFailures++;
        }
      }
      const rows = [];
      for (let d = today; d < horizonAvail; d = addDays(d, 1)) {
        rows.push({
          property_id: prop.id,
          day: d,
          available: !(blockedDays.has(d) || ourDays.has(d)),
          block_source: ourDays.has(d) ? "direct" : blockedDays.has(d) ? "vrn_sync" : null,
          synced_at: (/* @__PURE__ */ new Date()).toISOString()
        });
      }
      for (let i = 0; i < rows.length; i += 200) {
        await sb(
          env,
          "POST",
          "/rest/v1/rate_calendar?on_conflict=property_id,day",
          rows.slice(i, i + 200),
          { "Prefer": "resolution=merge-duplicates,return=minimal" }
        );
      }
      const rateRows = Object.entries(rateByDay).map(([d, rate]) => ({
        property_id: prop.id,
        day: d,
        nightly_rate: rate,
        available: !(blockedDays.has(d) || ourDays.has(d)),
        block_source: ourDays.has(d) ? "direct" : blockedDays.has(d) ? "vrn_sync" : null,
        synced_at: (/* @__PURE__ */ new Date()).toISOString()
      }));
      if (rateRows.length) {
        await sb(
          env,
          "POST",
          "/rest/v1/rate_calendar?on_conflict=property_id,day",
          rateRows,
          { "Prefer": "resolution=merge-duplicates,return=minimal" }
        );
      }
      stats.daysWritten += rows.length;
      await captureSnapshot(env, prop.id, trigger === "cron" ? "poll" : "manual", run.id).catch(() => {
      });
      await recordSyncState(env, prop.id, "availability", true);
      await recordSyncState(env, prop.id, "rates", true);
    }
    await sb(
      env,
      "PATCH",
      `/rest/v1/sync_runs?id=eq.${run.id}`,
      { finished_at: (/* @__PURE__ */ new Date()).toISOString(), ok: true, stats },
      { "Prefer": "return=minimal" }
    );
    return { ok: true, stats };
  } catch (e) {
    await sb(
      env,
      "PATCH",
      `/rest/v1/sync_runs?id=eq.${run.id}`,
      { finished_at: (/* @__PURE__ */ new Date()).toISOString(), ok: false, stats, error: String(e).slice(0, 500) },
      { "Prefer": "return=minimal" }
    ).catch(() => {
    });
    throw e;
  }
}
__name(runSync, "runSync");
async function adminPage(env, base, notice = "") {
  const [props, adjustments, partners, reservations, notifications, runs, calendar, cfg, coupons] = await Promise.all([
    sb(env, "GET", "/rest/v1/booking_properties?select=*&order=slug"),
    sb(env, "GET", "/rest/v1/rate_adjustments?select=*&order=created_at.desc&limit=20"),
    sb(env, "GET", "/rest/v1/rev_share_partners?select=*&order=created_at.desc&limit=20"),
    sb(env, "GET", "/rest/v1/reservations?select=*&order=created_at.desc&limit=20"),
    sb(env, "GET", "/rest/v1/booking_notifications?select=*&order=created_at.desc&limit=12"),
    sb(env, "GET", "/rest/v1/sync_runs?select=*&order=started_at.desc&limit=5"),
    sb(env, "GET", `/rest/v1/rate_calendar?day=gte.${(/* @__PURE__ */ new Date()).toISOString().slice(0, 10)}&day=lt.${addDays((/* @__PURE__ */ new Date()).toISOString().slice(0, 10), 90)}&select=property_id,day,nightly_rate,available,block_source&order=day`),
    getConfig(env),
    sb(env, "GET", "/rest/v1/coupons?select=*&order=created_at.desc&limit=30")
  ]);
  const [yieldRules, floorOverrides, protectedRows, priceLogs, yieldBookings, lockins] = await Promise.all([
    sb(env, "GET", "/rest/v1/yield_rules?select=*&order=property_id.nullsfirst").catch(() => []),
    sb(env, "GET", "/rest/v1/yield_floor_overrides?select=*&order=start_date&limit=40").catch(() => []),
    sb(env, "GET", "/rest/v1/protected_dates?select=*&order=start_date&limit=40").catch(() => []),
    sb(env, "GET", "/rest/v1/price_display_log?select=adjustments,at&order=at.desc&limit=500").catch(() => []),
    sb(env, "GET", "/rest/v1/reservations?yield_adjustments=not.is.null&status=in.(reserved,pending)&select=yield_adjustments,nightly_total,base_nightly_total,total,ab_bucket&order=created_at.desc&limit=200").catch(() => []),
    sb(env, "GET", "/rest/v1/lockin_offers?select=status,trigger_reason&order=created_at.desc&limit=200").catch(() => [])
  ]);
  const couponRows = coupons.map((c) => `
    <tr><td><strong>${esc(c.code)}</strong></td><td class="num">${c.pct}%</td>
    <td>${esc(c.effective_start || "\u2014")} \u2192 ${esc(c.effective_end || "\u2014")}</td>
    <td>${c.stay_start ? esc(c.stay_start) + "\u2192" + esc(c.stay_end || "") : "any stay"}</td>
    <td>${esc(c.property_id ? props.find((p) => p.id === c.property_id)?.slug || c.property_id : "any")}</td>
    <td class="num">${c.redemptions}${c.max_redemptions != null ? "/" + c.max_redemptions : ""}</td>
    <td>${c.partner_id ? "partner" : "\u2014"}</td>
    <td>${c.active ? "\u2705" : "off"}</td>
    <td>${c.active ? `<form method="post" action="${base}/coupon"><input type="hidden" name="disable" value="${c.id}"><button>disable</button></form>` : ""}</td></tr>`).join("");
  const calByProp = {};
  for (const c of calendar) (calByProp[c.property_id] ||= []).push(c);
  const strip = /* @__PURE__ */ __name((p) => {
    const cells = (calByProp[p.id] || []).map((c) => {
      const cls = c.available ? "av" : c.block_source === "direct" ? "dr" : "bk";
      const label = `${c.day}${c.nightly_rate ? ` \xB7 $${c.nightly_rate}` : ""}${c.available ? "" : ` \xB7 ${c.block_source || "blocked"}`}`;
      return `<i class="${cls}" title="${label}"></i>`;
    }).join("");
    return `<div class="prop"><h3>${esc(p.name)}</h3><div class="strip">${cells || "<small>No calendar yet \u2014 run a sync.</small>"}</div></div>`;
  }, "strip");
  const adjRows = adjustments.map((a) => `
    <tr><td>${esc(a.scope)}</td><td>${esc(a.property_id || a.partner_slug || "\u2014")}</td>
    <td class="num">${a.pct > 0 ? "+" : ""}${a.pct}%</td><td>${esc(a.note || "")}</td>
    <td>${a.active ? "\u2705" : "off"}</td>
    <td>${a.active ? `<form method="post" action="${base}/adjust"><input type="hidden" name="deactivate" value="${a.id}"><button>disable</button></form>` : ""}</td></tr>`).join("");
  const partnerRows = partners.map((p) => `
    <tr><td>${esc(p.slug)}</td><td>${esc(p.name)}</td><td>${esc(p.email || "\u2014")}</td>
    <td class="num">${p.guest_discount_pct}%</td><td class="num">${p.rev_share_pct}%</td>
    <td>${p.active ? "\u2705" : "off"}</td></tr>`).join("");
  const payouts = await sb(
    env,
    "GET",
    "/rest/v1/v_partner_payouts?select=*&order=payout_status,check_out.desc&limit=100"
  ).catch(() => []);
  const payoutTotals = payouts.reduce((m, x) => (m[x.payout_status] = (m[x.payout_status] || 0) + Number(x.payout_amount || 0), m), {});
  const payoutRows = payouts.filter((x) => x.payout_status !== "void").map((x) => `
    <tr><td><strong>${esc(x.partner_slug)}</strong></td><td>${esc(x.confirmation_code || "\u2014")}</td>
    <td>${esc(x.check_in)} \u2192 ${esc(x.check_out)}</td>
    <td class="num">$${fmt(x.nightly_total)}</td><td class="num">${x.rev_share_pct}%</td>
    <td class="num"><strong>$${fmt(x.payout_amount)}</strong></td>
    <td>${x.payout_status === "payable" ? "\u{1F4B0} payable" : x.payout_status === "settled" ? `\u2705 ${esc((x.rev_share_paid_at || "").slice(0, 10))}` : esc(x.payout_status)}</td>
    <td>${x.payout_status === "payable" ? `<form method="post" action="${base}/payout-paid"><input type="hidden" name="id" value="${x.reservation_id}"><button>mark paid</button></form>` : ""}</td></tr>`).join("");
  const resvRows = reservations.map((r) => `
    <tr><td>${esc(r.created_at.slice(0, 16).replace("T", " "))}</td>
    <td>${esc((props.find((p) => p.id === r.property_id) || {}).slug || r.property_id)}</td>
    <td>${esc(r.source)}</td><td>${esc(r.status)}</td>
    <td>${esc(r.check_in)} \u2192 ${esc(r.check_out)}</td>
    <td>${esc(r.guest_name || "\u2014")}</td>
    <td class="num">${r.total ? "$" + fmt(r.total) : "\u2014"}</td>
    <td>${r.status !== "cancelled" && r.source === "direct" ? `<form method="post" action="${base}/cancel"><input type="hidden" name="id" value="${r.id}"><button>cancel</button></form>` : ""}</td></tr>`).join("");
  const notifRows = notifications.map((n) => `
    <tr><td>${esc(n.created_at.slice(0, 16).replace("T", " "))}</td><td>${esc(n.channel)}</td>
    <td>${esc(n.recipient)}</td><td>${esc(n.status)}</td><td class="msg">${esc((n.body || "").slice(0, 80))}\u2026</td></tr>`).join("");
  const runRows = runs.map((r) => `
    <tr><td>${esc(r.started_at.slice(0, 19).replace("T", " "))}</td><td>${esc(r.trigger)}</td>
    <td>${r.ok === null ? "\u2026" : r.ok ? "\u2705" : "\u274C"}</td>
    <td class="msg">${esc(JSON.stringify(r.stats))}${r.error ? " \u2014 " + esc(r.error.slice(0, 120)) : ""}</td></tr>`).join("");
  const propOptions = props.map((p) => `<option value="${p.slug}">${esc(p.name)}</option>`).join("");
  const ruleName = /* @__PURE__ */ __name((r) => r.rule && r.rule.indexOf("ladder_") === 0 ? r.rule : r.rule || "?", "ruleName");
  const yieldAgg = {};
  for (const row of priceLogs) for (const a of row.adjustments || []) {
    if (a.amount >= 0 || String(a.rule).indexOf("coupon_") === 0) continue;
    (yieldAgg[ruleName(a)] ||= { displays: 0, bookings: 0, discountGiven: 0, revenue: 0 }).displays++;
  }
  for (const b of yieldBookings) for (const a of b.yield_adjustments || []) {
    if (a.amount >= 0 || String(a.rule).indexOf("coupon_") === 0) continue;
    const g = yieldAgg[ruleName(a)] ||= { displays: 0, bookings: 0, discountGiven: 0, revenue: 0 };
    g.bookings++;
    g.discountGiven += Math.abs(Number(a.amount) || 0);
    g.revenue += Number(b.nightly_total) || 0;
  }
  const outcomeRows = Object.entries(yieldAgg).map(([rule, g]) => `
    <tr><td>${esc(rule)}</td><td class="num">${g.displays}</td><td class="num">${g.bookings}</td>
    <td class="num">${g.displays ? (100 * g.bookings / g.displays).toFixed(1) + "%" : "\u2014"}</td>
    <td class="num">$${fmt(g.discountGiven)}</td><td class="num">$${fmt(g.revenue)}</td></tr>`).join("");
  const lockinAgg = lockins.reduce((m, o) => (m[o.status] = (m[o.status] || 0) + 1, m), {});
  const cutoff30 = addDays((/* @__PURE__ */ new Date()).toISOString().slice(0, 10), 30);
  const occRows = props.map((p) => {
    const days = (calByProp[p.id] || []).filter((c) => c.day < cutoff30);
    const booked = days.filter((c) => !c.available).length;
    return `<tr><td>${esc(p.slug)}</td><td class="num">${booked}/${days.length}</td>
    <td class="num">${days.length ? (100 * booked / days.length).toFixed(0) + "%" : "\u2014"}</td></tr>`;
  }).join("");
  const defaultRules = yieldRules.find((r) => !r.property_id) || {};
  const rulesRows = yieldRules.map((r) => `
    <tr><td>${r.property_id ? esc(props.find((p) => p.id === r.property_id)?.slug || r.property_id) : "<strong>default (all)</strong>"}</td>
    <td>${r.enabled ? "\u2705" : "off"}</td>
    <td>${esc((r.ladder || []).map((s) => `\u2264${s.days}d\u2192${s.pct}%`).join(" \xB7 "))}</td>
    <td>${r.gap_enabled ? `\u2264${r.gap_max_nights}n \u2192 ${r.gap_discount_pct}%${r.gap_relax_min_stay ? " (min-stay relax)" : ""}` : "off"}</td>
    <td>${r.lockin_enabled ? `${r.lockin_extra_pct}% / ${r.lockin_hours}h` : "off"}</td>
    <td class="num">${r.floor_pct}%</td>
    <td class="num">${r.ab_split_pct > 0 ? r.ab_split_pct + "% \u2192 B" : "\u2014"}</td></tr>`).join("");
  const floorRows = floorOverrides.map((f) => `
    <tr><td>${f.property_id ? esc(props.find((p) => p.id === f.property_id)?.slug || f.property_id) : "all"}</td>
    <td>${esc(f.start_date)} \u2192 ${esc(f.end_date)}</td><td class="num">${f.floor_pct}%</td>
    <td>${esc(f.note || "")}</td><td>${f.active ? "\u2705" : "off"}</td>
    <td>${f.active ? `<form method="post" action="${base}/floor"><input type="hidden" name="deactivate" value="${f.id}"><button>disable</button></form>` : ""}</td></tr>`).join("");
  const protRows = protectedRows.map((f) => `
    <tr><td>${f.property_id ? esc(props.find((p) => p.id === f.property_id)?.slug || f.property_id) : "all"}</td>
    <td>${esc(f.start_date)} \u2192 ${esc(f.end_date)}</td><td>${esc(f.note || "")}</td><td>${f.active ? "\u2705" : "off"}</td>
    <td>${f.active ? `<form method="post" action="${base}/protect"><input type="hidden" name="deactivate" value="${f.id}"><button>disable</button></form>` : ""}</td></tr>`).join("");
  const propIdOptions = `<option value="">default (all)</option>` + props.map((p) => `<option value="${p.id}">${esc(p.slug)}</option>`).join("");
  const yieldSection = `
<h2>Yield engine \u2014 keep the calendar full</h2>
<div class="card">
  <div class="legend" style="margin:0 0 10px">An empty night earns zero. Anchor = Vacay base \xD7 (1 + markup). Automatic discounts (deepest single rule) + lock-in offers apply above the floor. Protected dates are exempt from everything automatic. Coupons stack after yield, on the owner's authority.</div>
  <form class="inline" method="post" action="${base}/yield">
    <label>Scope<select name="property_id">${propIdOptions}</select></label>
    <label>Enabled<select name="enabled"><option value="true">on</option><option value="false">off</option></select></label>
    <label>\u2264 days 1<input name="l1_days" type="number" value="${(defaultRules.ladder || [])[0]?.days ?? 14}" style="width:70px"></label>
    <label>% 1<input name="l1_pct" type="number" step="0.5" value="${(defaultRules.ladder || [])[0]?.pct ?? 10}" style="width:70px"></label>
    <label>\u2264 days 2<input name="l2_days" type="number" value="${(defaultRules.ladder || [])[1]?.days ?? 7}" style="width:70px"></label>
    <label>% 2<input name="l2_pct" type="number" step="0.5" value="${(defaultRules.ladder || [])[1]?.pct ?? 15}" style="width:70px"></label>
    <label>\u2264 days 3<input name="l3_days" type="number" value="${(defaultRules.ladder || [])[2]?.days ?? 3}" style="width:70px"></label>
    <label>% 3<input name="l3_pct" type="number" step="0.5" value="${(defaultRules.ladder || [])[2]?.pct ?? 20}" style="width:70px"></label>
    <label>Gap fill<select name="gap_enabled"><option value="true">on</option><option value="false">off</option></select></label>
    <label>Gap \u2264 nights<input name="gap_max_nights" type="number" value="${defaultRules.gap_max_nights ?? 3}" style="width:70px"></label>
    <label>Gap %<input name="gap_discount_pct" type="number" step="0.5" value="${defaultRules.gap_discount_pct ?? 15}" style="width:70px"></label>
    <label>Relax min-stay<select name="gap_relax_min_stay"><option value="true">yes</option><option value="false">no</option></select></label>
    <label>Lock-in<select name="lockin_enabled"><option value="true">on</option><option value="false">off</option></select></label>
    <label>Lock-in %<input name="lockin_extra_pct" type="number" step="0.5" value="${defaultRules.lockin_extra_pct ?? 5}" style="width:70px"></label>
    <label>Lock-in hrs<input name="lockin_hours" type="number" value="${defaultRules.lockin_hours ?? 4}" style="width:70px"></label>
    <label>Floor % of base<input name="floor_pct" type="number" step="0.5" value="${defaultRules.floor_pct ?? 100}" style="width:80px"></label>
    <label>A/B split % \u2192 B<input name="ab_split_pct" type="number" value="${defaultRules.ab_split_pct ?? 0}" style="width:70px"></label>
    <label>B-variant ladder JSON (opt)<input name="ab_variant_ladder" placeholder='[{"days":7,"pct":25}]'></label>
    <button>Save yield rules</button>
  </form>
  <div class="tablewrap" style="margin-top:10px"><table><tr><th>Scope</th><th>On</th><th>Ladder</th><th>Gap fill</th><th>Lock-in</th><th>Floor</th><th>A/B</th></tr>${rulesRows || "<tr><td colspan=7>Defaults active.</td></tr>"}</table></div>
</div>

<div class="card">
  <h3>Floor overrides \u2014 sell below base only where YOU say so</h3>
  <form class="inline" method="post" action="${base}/floor">
    <label>Property<select name="property_id"><option value="">all</option>${props.map((p) => `<option value="${p.id}">${esc(p.slug)}</option>`).join("")}</select></label>
    <label>From<input name="start_date" type="date" required></label>
    <label>To (incl.)<input name="end_date" type="date" required></label>
    <label>Floor % of base<input name="floor_pct" type="number" step="0.5" required placeholder="90"></label>
    <label>Note<input name="note" placeholder="fill the dead week"></label>
    <button>Add override</button>
  </form>
  <div class="tablewrap" style="margin-top:8px"><table><tr><th>Property</th><th>Dates</th><th>Floor</th><th>Note</th><th>Active</th><th></th></tr>${floorRows || "<tr><td colspan=6>None \u2014 floor is 100% of Vacay base everywhere.</td></tr>"}</table></div>
</div>

<div class="card">
  <h3>Protected dates \u2014 never auto-discounted</h3>
  <form class="inline" method="post" action="${base}/protect">
    <label>Property<select name="property_id"><option value="">all</option>${props.map((p) => `<option value="${p.id}">${esc(p.slug)}</option>`).join("")}</select></label>
    <label>From<input name="start_date" type="date" required></label>
    <label>To (incl.)<input name="end_date" type="date" required></label>
    <label>Note<input name="note" placeholder="spring break / July 4th"></label>
    <button>Protect range</button>
  </form>
  <div class="tablewrap" style="margin-top:8px"><table><tr><th>Property</th><th>Dates</th><th>Note</th><th>Active</th><th></th></tr>${protRows || "<tr><td colspan=5>None yet \u2014 flag spring break, summer Saturdays, holidays.</td></tr>"}</table></div>
</div>

<div class="card">
  <h3>Yield outcomes \u2014 what actually works</h3>
  <div class="tablewrap"><table><tr><th>Rule</th><th>Displays</th><th>Bookings</th><th>Conv.</th><th>Discount given</th><th>Nightly revenue won</th></tr>${outcomeRows || "<tr><td colspan=6>No yield-priced displays yet.</td></tr>"}</table></div>
  <div class="legend">Lock-in offers: ${lockinAgg.active || 0} active \xB7 ${lockinAgg.redeemed || 0} redeemed \xB7 ${lockinAgg.expired || 0} expired</div>
  <div class="tablewrap" style="margin-top:8px"><table><tr><th>Occupancy next 30 days</th><th>Nights booked</th><th>Rate</th></tr>${occRows}</table></div>
</div>`;
  return `<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow"><title>Sun &amp; Moon \xB7 Booking Admin</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:Georgia,serif; background:#F5EFE4; color:#2C2318; padding:28px 18px 60px; }
.wrap { max-width:1150px; margin:0 auto; }
h1 { font-weight:400; font-size:26px; } h1 em { color:#9B6B20; font-style:normal; }
.sub { font-family:-apple-system,sans-serif; font-size:11px; letter-spacing:.18em; text-transform:uppercase; color:#7A6A58; margin:2px 0 18px; }
h2 { font-weight:400; font-size:18px; margin:30px 0 10px; }
h3 { font-weight:400; font-size:14px; margin:12px 0 4px; }
.notice { background:#e8f4e0; border:1px solid #b6d3a2; border-radius:4px; padding:10px 14px; font-family:-apple-system,sans-serif; font-size:13px; margin-bottom:14px; }
.card { background:#FDFCFA; border:1px solid rgba(139,111,82,.15); border-radius:4px; padding:14px; margin-bottom:10px; }
.strip { display:flex; gap:1px; flex-wrap:wrap; }
.strip i { width:9px; height:22px; border-radius:1px; }
.strip .av { background:#B9CDA1; } .strip .bk { background:#D77A61; } .strip .dr { background:#3A6080; }
table { border-collapse:collapse; width:100%; font-family:-apple-system,sans-serif; font-size:12.5px; background:#FDFCFA; }
th { text-align:left; font-size:10px; letter-spacing:.12em; text-transform:uppercase; color:#7A6A58; padding:8px 10px; border-bottom:1px solid rgba(139,111,82,.2); }
td { padding:7px 10px; border-bottom:1px solid rgba(139,111,82,.08); }
td.num { text-align:right; } td.msg { color:#7A6A58; }
form.inline { display:flex; gap:8px; flex-wrap:wrap; align-items:end; font-family:-apple-system,sans-serif; font-size:12px; }
label { display:flex; flex-direction:column; gap:3px; font-size:10px; letter-spacing:.1em; text-transform:uppercase; color:#7A6A58; }
input, select { font-family:Georgia,serif; font-size:14px; padding:7px 9px; border:1px solid rgba(139,111,82,.3); border-radius:3px; background:white; }
button { font-family:-apple-system,sans-serif; font-size:11px; letter-spacing:.12em; text-transform:uppercase; background:#3B2F22; color:white; border:none; border-radius:3px; padding:9px 14px; cursor:pointer; }
button:hover { background:#8B6F52; }
.legend { font-family:-apple-system,sans-serif; font-size:11px; color:#7A6A58; margin:6px 0 0; }
.legend i { display:inline-block; width:10px; height:10px; border-radius:2px; margin:0 4px 0 10px; vertical-align:-1px; }
#quoteout { white-space:pre-wrap; font-family:ui-monospace,monospace; font-size:12px; background:#FDFCFA; border:1px solid rgba(139,111,82,.15); border-radius:4px; padding:12px; margin-top:10px; display:none; }
.tablewrap { overflow-x:auto; }
</style></head><body><div class="wrap">
<h1>Sun <em>&amp;</em> Moon \u2014 Booking Admin</h1>
<div class="sub">rates &amp; reservations sync \xB7 direct booking \xB7 hidden url \u2014 treat like a password</div>
${notice ? `<div class="notice">${esc(notice)}</div>` : ""}

<h2>Sync</h2>
<div class="card">
  <form method="post" action="${base}/sync" style="display:inline;margin-bottom:10px"><button>Sync now (VRN \u2192 database)</button></form>
  <form method="post" action="${base}/validate" style="display:inline;margin-left:8px"><button>Validate vs Vacay (auto-correct drift)</button></form>
  <div style="margin:8px 0"></div>
  <div class="tablewrap"><table><tr><th>Started</th><th>Trigger</th><th>OK</th><th>Stats</th></tr>${runRows || "<tr><td colspan=4>Never synced.</td></tr>"}</table></div>
</div>

<h2>Rates &amp; availability \u2014 next 90 days</h2>
<div class="card">
  ${props.map(strip).join("")}
  <div class="legend">Legend:<i style="background:#B9CDA1"></i>available<i style="background:#D77A61"></i>booked (VRN)<i style="background:#3A6080"></i>direct/webhook block \xB7 hover a cell for date &amp; rate</div>
</div>

<h2>Rate adjustments</h2>
<div class="card">
  <form class="inline" method="post" action="${base}/adjust">
    <label>Scope<select name="scope"><option value="global">global (all)</option><option value="property">property</option><option value="partner">partner</option></select></label>
    <label>Property (if scope=property)<select name="property_id"><option value="">\u2014</option>${props.map((p) => `<option value="${p.id}">${esc(p.slug)}</option>`).join("")}</select></label>
    <label>Partner slug (if scope=partner)<input name="partner_slug" placeholder="e.g. seaside-weddings"></label>
    <label>Percent (+raise / \u2212discount)<input name="pct" type="number" step="0.5" required placeholder="-10"></label>
    <label>Note<input name="note" placeholder="fall promo"></label>
    <button>Apply</button>
  </form>
  <div class="tablewrap"><table><tr><th>Scope</th><th>Target</th><th>%</th><th>Note</th><th>Active</th><th></th></tr>${adjRows || "<tr><td colspan=6>No adjustments \u2014 direct rates mirror VRN.</td></tr>"}</table></div>
</div>

<h2>Rev-share partners</h2>
<div class="card">
  <form class="inline" method="post" action="${base}/partner">
    <label>Slug<input name="slug" required placeholder="seaside-weddings"></label>
    <label>Name<input name="name" required placeholder="Seaside Weddings"></label>
    <label>Email<input name="email" type="email" placeholder="partner@\u2026"></label>
    <label>Guest discount %<input name="guest_discount_pct" type="number" step="0.5" value="5"></label>
    <label>Rev share %<input name="rev_share_pct" type="number" step="0.5" value="5"></label>
    <button>Add / update</button>
  </form>
  <div class="tablewrap"><table><tr><th>Slug</th><th>Name</th><th>Email</th><th>Guest disc.</th><th>Rev share</th><th>Active</th></tr>${partnerRows || "<tr><td colspan=6>No partners yet \u2014 this powers the custom partner pages (next phase).</td></tr>"}</table></div>
</div>

<h2>Partner payouts \u2014 rev share on collected rent</h2>
<div class="card">
  <div class="legend" style="margin:0 0 8px">Payout = partner's rev-share % \xD7 the nightly rent actually collected (after yield discounts &amp; coupons \u2014 never taxes/fees). A booking becomes <strong>payable</strong> once the stay is complete and paid in full; cancelled stays are void. Outstanding payable: <strong>$${fmt(payoutTotals.payable || 0)}</strong> \xB7 upcoming pipeline: $${fmt(payoutTotals.upcoming || 0)} \xB7 settled to date: $${fmt(payoutTotals.settled || 0)}</div>
  <div class="tablewrap"><table><tr><th>Partner</th><th>Booking</th><th>Stay</th><th>Rent collected</th><th>%</th><th>Payout</th><th>Status</th><th></th></tr>${payoutRows || "<tr><td colspan=8>No partner-attributed bookings yet \u2014 every booking made through a partner link lands here automatically.</td></tr>"}</table></div>
</div>

<h2>Coupons &amp; discount codes</h2>
<div class="card">
  <form class="inline" method="post" action="${base}/coupon">
    <label>Code<input name="code" required placeholder="FALL25" style="text-transform:uppercase"></label>
    <label>% off nightly<input name="pct" type="number" step="0.5" required placeholder="10"></label>
    <label>Bookable from<input name="effective_start" type="date"></label>
    <label>Bookable until<input name="effective_end" type="date"></label>
    <label>Stay from (opt)<input name="stay_start" type="date"></label>
    <label>Stay until (opt)<input name="stay_end" type="date"></label>
    <label>Property (opt)<select name="property_id"><option value="">any</option>${props.map((p) => `<option value="${p.id}">${esc(p.slug)}</option>`).join("")}</select></label>
    <label>Max redemptions (opt)<input name="max_redemptions" type="number" step="1" placeholder="\u221E"></label>
    <button>Create code</button>
  </form>
  <div class="tablewrap"><table><tr><th>Code</th><th>Off</th><th>Bookable window</th><th>Stay dates</th><th>Property</th><th>Used</th><th>Src</th><th>Active</th><th></th></tr>${couponRows || "<tr><td colspan=9>No codes yet. Discounts apply to the nightly rate only \u2014 never taxes or fees.</td></tr>"}</table></div>
</div>

<h2>Pricing config</h2>
<div class="card">
  <form class="inline" method="post" action="${base}/config">
    <label>Markup % (on nightly)<input name="markup_pct" type="number" step="0.001" value="${cfg.markup_pct}"></label>
    <label>Admin fee % (on rent, untaxed)<input name="admin_fee_pct" type="number" step="0.001" value="${cfg.admin_fee_pct ?? 0}"></label>
    <label>Deposit %<input name="deposit_pct" type="number" step="1" value="${cfg.deposit_pct}"></label>
    <label>Balance days before check-in<input name="balance_days_before" type="number" step="1" value="${cfg.balance_days_before}"></label>
    <label>Stale-data breaker (hrs)<input name="stale_breaker_hours" type="number" step="1" value="${cfg.stale_breaker_hours}"></label>
    <button>Save config</button>
  </form>
  <div class="legend">Vacay base rates are stored untouched; markup and coupons apply at the display layer only. Health &amp; drift: <code>/health</code>.</div>
</div>

${yieldSection}

<h2>Quote tester</h2>
<div class="card">
  <form class="inline" onsubmit="return runQuote(event)">
    <label>Property<select id="qprop">${propOptions}</select></label>
    <label>Check-in<input id="qin" type="date" required></label>
    <label>Check-out<input id="qout" type="date" required></label>
    <label>Guests<input id="qg" type="number" value="4" min="1" max="16"></label>
    <label>Partner slug<input id="qpartner" placeholder="(optional)"></label>
    <button>Get quote</button>
  </form>
  <div id="quoteout"></div>
</div>

<h2>Reservations</h2>
<div class="card tablewrap"><table>
<tr><th>Created</th><th>Property</th><th>Source</th><th>Status</th><th>Dates</th><th>Guest</th><th>Total</th><th></th></tr>
${resvRows || "<tr><td colspan=8>None yet.</td></tr>"}</table></div>

<h2>Notification queue</h2>
<div class="card tablewrap"><table>
<tr><th>Created</th><th>Channel</th><th>Recipient</th><th>Status</th><th>Preview</th></tr>
${notifRows || "<tr><td colspan=5>Empty \u2014 notifications appear when a direct reservation lands.</td></tr>"}</table>
<div class="legend">Mindy CC is ${(env.NOTIFY_MINDY_ENABLED || "false") === "true" ? "ENABLED \u2705" : 'OFF (testing mode) \u2014 flip NOTIFY_MINDY_ENABLED to "true" in wrangler.toml when ready'}.</div></div>

<script>
async function runQuote(e) {
  e.preventDefault();
  const out = document.getElementById('quoteout');
  out.style.display = 'block'; out.textContent = 'Fetching\u2026';
  const p = new URLSearchParams({
    property: document.getElementById('qprop').value,
    checkIn: document.getElementById('qin').value,
    checkOut: document.getElementById('qout').value,
    guests: document.getElementById('qg').value,
  });
  const partner = document.getElementById('qpartner').value.trim();
  if (partner) p.set('partner', partner);
  try {
    const r = await fetch('/quote?' + p.toString());
    out.textContent = JSON.stringify(await r.json(), null, 2);
  } catch (err) { out.textContent = 'Error: ' + err; }
  return false;
}
<\/script>
</div></body></html>`;
}
__name(adminPage, "adminPage");
async function handleAdminPost(request, env, base, action) {
  if (action === "sync") {
    const result = await runSync(env, "manual").catch((e) => ({ ok: false, error: String(e) }));
    return adminRedirect(base, result.ok ? `Sync complete: ${JSON.stringify(result.stats)}` : `Sync failed: ${result.error}`);
  }
  if (action === "validate") {
    const result = await runValidation(env, "manual").catch((e) => ({ ok: false, error: String(e) }));
    return adminRedirect(base, result.ok ? `Validation complete: ${result.stats.mismatches} mismatch(es) auto-corrected. ${(result.mismatches || []).join("; ")}` : `Validation failed: ${result.error}`);
  }
  if (action === "charge-balances") {
    const result = await runBalanceCharges(env, { waitUntil: /* @__PURE__ */ __name(() => {
    }, "waitUntil") }).catch((e) => ({ ok: false, error: String(e) }));
    return adminRedirect(base, result.ok ? `Balance run: ${JSON.stringify(result.stats)}` : `Balance run failed: ${result.error || result.skipped}`);
  }
  const form = await request.formData();
  const f = /* @__PURE__ */ __name((k) => (form.get(k) || "").toString().trim(), "f");
  if (action === "adjust") {
    if (f("deactivate")) {
      await sb(
        env,
        "PATCH",
        `/rest/v1/rate_adjustments?id=eq.${f("deactivate")}`,
        { active: false, updated_at: (/* @__PURE__ */ new Date()).toISOString() },
        { "Prefer": "return=minimal" }
      );
      return adminRedirect(base, "Adjustment disabled.");
    }
    const scope = ["global", "property", "partner"].includes(f("scope")) ? f("scope") : "global";
    await sb(env, "POST", "/rest/v1/rate_adjustments", {
      scope,
      property_id: scope === "property" ? f("property_id") || null : null,
      partner_slug: scope === "partner" ? f("partner_slug") || null : null,
      pct: parseFloat(f("pct")) || 0,
      note: f("note") || null
    }, { "Prefer": "return=minimal" });
    return adminRedirect(base, `Adjustment applied: ${scope} ${f("pct")}%`);
  }
  if (action === "partner") {
    await sb(env, "POST", "/rest/v1/rev_share_partners?on_conflict=slug", [{
      slug: f("slug").toLowerCase().replace(/[^a-z0-9-]+/g, "-"),
      name: f("name"),
      email: f("email") || null,
      guest_discount_pct: parseFloat(f("guest_discount_pct")) || 0,
      rev_share_pct: parseFloat(f("rev_share_pct")) || 0
    }], { "Prefer": "resolution=merge-duplicates,return=minimal" });
    return adminRedirect(base, `Partner saved: ${f("slug")}`);
  }
  if (action === "coupon") {
    if (f("disable")) {
      await sb(
        env,
        "PATCH",
        `/rest/v1/coupons?id=eq.${f("disable")}`,
        { active: false },
        { "Prefer": "return=minimal" }
      );
      return adminRedirect(base, "Code disabled.");
    }
    const code = f("code").toUpperCase().replace(/[^A-Z0-9-]/g, "");
    if (!code || !parseFloat(f("pct"))) return adminRedirect(base, "Code and % are required.");
    await sb(env, "POST", "/rest/v1/coupons?on_conflict=code", [{
      code,
      pct: parseFloat(f("pct")),
      effective_start: f("effective_start") || null,
      effective_end: f("effective_end") || null,
      stay_start: f("stay_start") || null,
      stay_end: f("stay_end") || null,
      property_id: f("property_id") || null,
      max_redemptions: f("max_redemptions") ? parseInt(f("max_redemptions"), 10) : null,
      active: true
    }], { "Prefer": "resolution=merge-duplicates,return=minimal" });
    return adminRedirect(base, `Coupon saved: ${code} (${f("pct")}% off nightly)`);
  }
  if (action === "config") {
    await sb(env, "PATCH", "/rest/v1/booking_config?id=eq.1", {
      markup_pct: parseFloat(f("markup_pct")) || 0,
      admin_fee_pct: parseFloat(f("admin_fee_pct")) || 0,
      deposit_pct: parseFloat(f("deposit_pct")) || 0,
      balance_days_before: parseInt(f("balance_days_before"), 10) || 30,
      stale_breaker_hours: parseInt(f("stale_breaker_hours"), 10) || 6,
      updated_at: (/* @__PURE__ */ new Date()).toISOString()
    }, { "Prefer": "return=minimal" });
    return adminRedirect(base, "Pricing config saved.");
  }
  if (action === "yield") {
    const propertyId = f("property_id") || null;
    const ladder = [];
    for (const i of [1, 2, 3]) {
      const days = parseInt(f(`l${i}_days`), 10), pct = parseFloat(f(`l${i}_pct`));
      if (days > 0 && pct > 0) ladder.push({ days, pct });
    }
    let abVariant = null;
    if (f("ab_variant_ladder")) {
      try {
        const v = JSON.parse(f("ab_variant_ladder"));
        if (Array.isArray(v) && v.length) abVariant = v;
      } catch (e) {
        return adminRedirect(base, "B-variant ladder isn't valid JSON \u2014 not saved.");
      }
    }
    const row = {
      enabled: f("enabled") !== "false",
      ladder: ladder.length ? ladder : [{ days: 14, pct: 10 }, { days: 7, pct: 15 }, { days: 3, pct: 20 }],
      gap_enabled: f("gap_enabled") !== "false",
      gap_max_nights: Math.max(1, parseInt(f("gap_max_nights"), 10) || 3),
      gap_discount_pct: parseFloat(f("gap_discount_pct")) || 15,
      gap_relax_min_stay: f("gap_relax_min_stay") !== "false",
      lockin_enabled: f("lockin_enabled") !== "false",
      lockin_extra_pct: parseFloat(f("lockin_extra_pct")) || 5,
      lockin_hours: Math.max(1, parseInt(f("lockin_hours"), 10) || 4),
      floor_pct: parseFloat(f("floor_pct")) || 100,
      ab_split_pct: Math.min(100, Math.max(0, parseInt(f("ab_split_pct"), 10) || 0)),
      ab_variant_ladder: abVariant,
      updated_at: (/* @__PURE__ */ new Date()).toISOString()
    };
    const existing = await sb(
      env,
      "GET",
      `/rest/v1/yield_rules?property_id=${propertyId ? "eq." + propertyId : "is.null"}&select=id&limit=1`
    );
    if (existing[0]) {
      await sb(env, "PATCH", `/rest/v1/yield_rules?id=eq.${existing[0].id}`, row, { "Prefer": "return=minimal" });
    } else {
      await sb(env, "POST", "/rest/v1/yield_rules", { ...row, property_id: propertyId }, { "Prefer": "return=minimal" });
    }
    return adminRedirect(base, `Yield rules saved (${propertyId ? "property" : "default"}).`);
  }
  if (action === "floor") {
    if (f("deactivate")) {
      await sb(
        env,
        "PATCH",
        `/rest/v1/yield_floor_overrides?id=eq.${f("deactivate")}`,
        { active: false },
        { "Prefer": "return=minimal" }
      );
      return adminRedirect(base, "Floor override disabled.");
    }
    if (!isDate(f("start_date")) || !isDate(f("end_date")) || !parseFloat(f("floor_pct"))) {
      return adminRedirect(base, "Floor override needs dates and a floor %.");
    }
    await sb(env, "POST", "/rest/v1/yield_floor_overrides", {
      property_id: f("property_id") || null,
      start_date: f("start_date"),
      end_date: f("end_date"),
      floor_pct: parseFloat(f("floor_pct")),
      note: f("note") || null
    }, { "Prefer": "return=minimal" });
    return adminRedirect(base, `Floor override added (${f("floor_pct")}% of base).`);
  }
  if (action === "protect") {
    if (f("deactivate")) {
      await sb(
        env,
        "PATCH",
        `/rest/v1/protected_dates?id=eq.${f("deactivate")}`,
        { active: false },
        { "Prefer": "return=minimal" }
      );
      return adminRedirect(base, "Protected range disabled.");
    }
    if (!isDate(f("start_date")) || !isDate(f("end_date"))) {
      return adminRedirect(base, "Protected range needs start and end dates.");
    }
    await sb(env, "POST", "/rest/v1/protected_dates", {
      property_id: f("property_id") || null,
      start_date: f("start_date"),
      end_date: f("end_date"),
      note: f("note") || null
    }, { "Prefer": "return=minimal" });
    return adminRedirect(base, "Dates protected \u2014 no automatic discounts will touch them.");
  }
  if (action === "payout-paid") {
    await sb(
      env,
      "PATCH",
      `/rest/v1/reservations?id=eq.${f("id")}&rev_share_paid_at=is.null`,
      { rev_share_paid_at: (/* @__PURE__ */ new Date()).toISOString() },
      { "Prefer": "return=minimal" }
    );
    return adminRedirect(base, "Payout marked settled.");
  }
  if (action === "refund") {
    return handleRefund(env, base, f("id"), f("amount"));
  }
  if (action === "cancel") {
    const id = f("id");
    const [resv] = await sb(env, "GET", `/rest/v1/reservations?id=eq.${id}&select=*`);
    if (resv) {
      await sb(
        env,
        "PATCH",
        `/rest/v1/reservations?id=eq.${id}`,
        { status: "cancelled", updated_at: (/* @__PURE__ */ new Date()).toISOString() },
        { "Prefer": "return=minimal" }
      );
      const days = [];
      for (let d = resv.check_in; d < resv.check_out; d = addDays(d, 1)) {
        days.push({
          property_id: resv.property_id,
          day: d,
          available: true,
          block_source: null,
          synced_at: (/* @__PURE__ */ new Date()).toISOString()
        });
      }
      await sb(
        env,
        "POST",
        "/rest/v1/rate_calendar?on_conflict=property_id,day",
        days,
        { "Prefer": "resolution=merge-duplicates,return=minimal" }
      );
      let refundNote = "";
      const paid = Number(resv.amount_paid || 0) - Number(resv.amount_refunded || 0);
      if (stripeConfigured(env) && resv.stripe_payment_intent_id && paid > 0) {
        const rf = await stripe(
          env,
          "POST",
          "/refunds",
          { payment_intent: resv.stripe_payment_intent_id },
          `refund_cancel_${resv.id}`
        ).catch((e) => ({ error: String(e) }));
        if (!rf.error) {
          await sb(
            env,
            "PATCH",
            `/rest/v1/reservations?id=eq.${id}`,
            { payment_status: "refunded", amount_refunded: Number(resv.amount_paid) },
            { "Prefer": "return=minimal" }
          );
          refundNote = ` $${fmt(paid)} refunded to the guest's card.`;
        } else {
          refundNote = ` REFUND FAILED (${rf.error.slice(0, 120)}) \u2014 refund manually in Stripe.`;
        }
      }
      const prop = await getProperty(env, resv.property_id);
      const propName = prop?.name || resv.property_id;
      if (resv.guest_email) {
        await queueNotifications(env, [{
          reservation_id: resv.id,
          channel: "email",
          recipient: resv.guest_email,
          subject: `Your Sun & Moon reservation ${resv.confirmation_code || ""} has been cancelled`,
          body: `Hi ${resv.guest_name || "there"},

Your reservation at ${propName} (${resv.check_in} \u2192 ${resv.check_out}) has been cancelled.` + (refundNote.includes("refunded to") ? `

Your payment of $${fmt(paid)} has been refunded in full to your card \u2014 depending on your bank it can take 5\u201310 business days to appear.` : "") + `

If this is unexpected or you'd like different dates, just reply to this email \u2014 we'd love to host you.

\u2014 Sun & Moon at 30A
experience@sunandmoon30a.com`
        }]);
      }
      await notifyOwnerAlert(
        env,
        `Reservation cancelled \u2014 ${resv.confirmation_code || id}`,
        `${propName} ${resv.check_in}\u2192${resv.check_out} \xB7 guest ${resv.guest_name || "?"} (${resv.guest_email || "?"}) \xB7 dates released.${refundNote}`
      );
    }
    return adminRedirect(base, `Reservation cancelled and dates released.`);
  }
  return json(404, { ok: false });
}
__name(handleAdminPost, "handleAdminPost");
function adminRedirect(base, notice) {
  return new Response(null, {
    status: 303,
    headers: { "Location": `${base}?notice=${encodeURIComponent(notice)}` }
  });
}
__name(adminRedirect, "adminRedirect");
function json(status, body) {
  return new Response(JSON.stringify(body, null, 2), {
    status,
    headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
  });
}
__name(json, "json");
async function handleAvailability(env, params, ctx) {
  const prop = await getProperty(env, params.get("property") || "");
  if (!prop) return json(404, { ok: false, error: "unknown property" });
  const today = (/* @__PURE__ */ new Date()).toISOString().slice(0, 10);
  const months = Math.min(18, Math.max(1, parseInt(params.get("months") || "14", 10)));
  const horizon = addDays(today, months * 31);
  let blockedDays = [], source = "vacay-live";
  try {
    const live = await vrnAvailability(prop.id, today, horizon);
    const days = /* @__PURE__ */ new Set();
    for (const b of live) {
      const from = b.start < today ? today : b.start;
      const to = b.end > horizon ? horizon : b.end;
      for (let d = from; d < to; d = addDays(d, 1)) days.add(d);
    }
    const ours = await sb(
      env,
      "GET",
      `/rest/v1/reservations?property_id=eq.${prop.id}&status=in.(reserved,blocked,pending)&source=in.(direct,webhook)&check_in=lt.${horizon}&check_out=gt.${today}&select=check_in,check_out,status,soft_hold_expires_at`
    ).catch(() => []);
    const now = Date.now();
    for (const r of ours) {
      if (r.status === "pending" && !(r.soft_hold_expires_at && new Date(r.soft_hold_expires_at).getTime() > now)) continue;
      for (let d = r.check_in < today ? today : r.check_in; d < r.check_out && d < horizon; d = addDays(d, 1)) days.add(d);
    }
    blockedDays = [...days].sort();
    const reconcile = reconcileCalendarWindow(env, prop.id, today, horizon, live).then(() => recordSyncState(env, prop.id, "availability", true, `live calendar ${today}..${horizon}`)).catch(() => {
    });
    if (ctx) ctx.waitUntil(reconcile);
  } catch (e) {
    source = "synced-copy";
    const rows = await sb(
      env,
      "GET",
      `/rest/v1/rate_calendar?property_id=eq.${prop.id}&day=gte.${today}&day=lt.${horizon}&available=eq.false&select=day&order=day`
    ).catch(() => []);
    blockedDays = rows.map((r) => r.day);
  }
  const blocked = [];
  let start = null, prev = null;
  for (const d of blockedDays) {
    if (start === null) {
      start = d;
      prev = d;
      continue;
    }
    if (d === addDays(prev, 1)) {
      prev = d;
      continue;
    }
    blocked.push({ from: start, to: prev });
    start = d;
    prev = d;
  }
  if (start !== null) blocked.push({ from: start, to: prev });
  return new Response(JSON.stringify({ ok: true, property: prop.slug, today, source, blocked }), {
    headers: {
      "Content-Type": "application/json",
      "Access-Control-Allow-Origin": "*",
      "Cache-Control": "public, max-age=60"
    }
  });
}
__name(handleAvailability, "handleAvailability");
async function handleHealth(env) {
  const cfg = await getConfig(env);
  const [states, lastRuns, lastWebhook] = await Promise.all([
    sb(env, "GET", "/rest/v1/sync_state?select=*&order=property_id,domain").catch(() => []),
    sb(env, "GET", "/rest/v1/sync_runs?select=trigger,ok,started_at,finished_at,stats&order=started_at.desc&limit=5").catch(() => []),
    sb(env, "GET", "/rest/v1/sync_state?domain=eq.webhook&order=last_success_at.desc.nullslast&limit=1").catch(() => [])
  ]);
  const breakerHours = Number(cfg.stale_breaker_hours);
  const cutoffFor = /* @__PURE__ */ __name((domain) => Date.now() - (domain === "validation" ? 13 : breakerHours) * 3600 * 1e3, "cutoffFor");
  const staleDomains = states.filter((s) => ["availability", "validation"].includes(s.domain) && !(s.last_success_at && new Date(s.last_success_at).getTime() >= cutoffFor(s.domain)));
  const webhookLive = lastWebhook[0]?.last_success_at ? true : false;
  return json(200, {
    ok: staleDomains.length === 0,
    now: (/* @__PURE__ */ new Date()).toISOString(),
    config: {
      markup_pct: cfg.markup_pct,
      deposit_pct: cfg.deposit_pct,
      balance_days_before: cfg.balance_days_before,
      stale_breaker_hours: breakerHours
    },
    circuit_breaker: {
      tripped: staleDomains.length > 0,
      stale: staleDomains.map((s) => `${s.property_id}/${s.domain}`)
    },
    webhook: {
      live: webhookLive,
      last_receipt: lastWebhook[0]?.last_success_at || null,
      note: webhookLive ? "Vacay webhook has delivered." : "No webhook receipts yet \u2014 polling + validation are the source of truth."
    },
    sync_state: states,
    recent_runs: lastRuns
  });
}
__name(handleHealth, "handleHealth");
async function handleEvent(request, env, ctx) {
  let b;
  try {
    b = await request.json();
  } catch {
    return json(400, { ok: false });
  }
  const event = String(b.event || "").slice(0, 40);
  if (!event) return json(400, { ok: false, error: "event required" });
  ctx.waitUntil(sb(env, "POST", "/rest/v1/booking_events", {
    session_id: b.session_id || null,
    event,
    property_id: b.property || null,
    device: b.device || null,
    source: b.source || null,
    partner_slug: b.partner || null,
    metadata: b.metadata || {}
  }, { "Prefer": "return=minimal" }).catch(() => {
  }));
  if (b.email && (event === "checkout_started" || event === "payment_started")) {
    ctx.waitUntil(sb(env, "POST", "/rest/v1/abandoned_checkouts", {
      session_id: b.session_id || null,
      email: String(b.email).slice(0, 320),
      property_id: b.property || null,
      check_in: b.checkIn || null,
      check_out: b.checkOut || null,
      guests: b.guests || null,
      partner_slug: b.partner || null,
      last_step: event
    }, { "Prefer": "return=minimal" }).catch(() => {
    }));
  }
  return json(200, { ok: true });
}
__name(handleEvent, "handleEvent");
var PROPERTY_CARDS = [
  {
    slug: "golden-sun",
    icon: "sun.png",
    tag: "53 Crystal Court \xB7 Sleeps 8",
    blurb: "Bright, warm, and made for early risers \u2014 two kings, a queen, fireplace, covered patio & grill.",
    maxGuests: 8
  },
  {
    slug: "blue-moon",
    icon: "moon.png",
    tag: "65 Crystal Court \xB7 Sleeps 8",
    blurb: "A calm coastal cottage three minutes from the beach \u2014 three kings, fireplace, private patio & grill.",
    maxGuests: 8
  },
  {
    slug: "full-property",
    icon: "sunmoon-color.png",
    tag: "Both cottages \xB7 Sleeps 16",
    badge: "Preferred rate \u2014 best value",
    blurb: "The whole compound: six bedrooms, four baths, one address, three minutes to the beach. Our preferred together rate is always better than booking the cottages separately \u2014 built for reunions, weddings, and celebrations.",
    maxGuests: 16
  }
];
async function guestPage(env, params) {
  const preProperty = PROPERTY_CARDS.some((c) => c.slug === params.property) ? params.property : "full-property";
  const partner = String(params.partner || "").trim().toLowerCase().replace(/[^a-z0-9-]/g, "");
  let partnerBanner = "";
  if (partner) {
    const rows = await sb(
      env,
      "GET",
      `/rest/v1/rev_share_partners?slug=eq.${encodeURIComponent(partner)}&active=eq.true&limit=1`
    ).catch(() => []);
    if (rows[0]) {
      const p = rows[0];
      partnerBanner = `<div class="partner-banner">Welcome from <strong>${esc(p.name)}</strong>` + (Number(p.guest_discount_pct) > 0 ? ` \u2014 your ${p.guest_discount_pct}% partner rate is applied automatically.` : `.`) + `</div>`;
    }
  }
  const cards = PROPERTY_CARDS.map((c) => {
    const name = c.slug === "golden-sun" ? "Golden Sun" : c.slug === "blue-moon" ? "Blue Moon" : "Sun &amp; Moon Together";
    return `
    <label class="pcard" data-slug="${c.slug}" data-max="${c.maxGuests}">
      <input type="radio" name="property" value="${c.slug}" ${preProperty === c.slug ? "checked" : ""}>
      ${c.badge ? `<span class="pcard-pill">${c.badge}</span>` : ""}
      <img class="pcard-photo" src="/img/${c.slug}.jpg" alt="${name.replace(/&amp;/g, "and")}" loading="lazy">
      <div class="pcard-body">
        <img class="pcard-icon" src="https://sunandmoon30a.com/images/logos/${c.icon}" alt="">
        <div class="pcard-name">${name}</div>
        <div class="pcard-tag">${c.tag}</div>
        <p class="pcard-blurb">${c.blurb}</p>
      </div>
    </label>`;
  }).join("");
  return `<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Book Direct \xB7 Sun &amp; Moon at 30A</title>
<script src="https://js.stripe.com/v3/"><\/script>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/flatpickr@4.6.13/dist/flatpickr.min.css">
<script src="https://cdn.jsdelivr.net/npm/flatpickr@4.6.13"><\/script>
<meta name="description" content="Book Golden Sun and Blue Moon direct with the owners in Seagrove Beach on Scenic 30A.">
<link rel="icon" href="https://sunandmoon30a.com/favicon.png">
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  :root { --sand:#F5EFE4; --sand2:#EDE4D3; --tan:#C9B89A; --brown:#8B6F52; --deep:#3B2F22;
          --sun:#E8A44A; --sun-deep:#9B6B20; --moon:#8BABC4; --moon-deep:#3A6080;
          --white:#FDFCFA; --text:#2C2318; --muted:#7A6A58; --border:rgba(139,111,82,.16); }
  body { font-family:Georgia,'Times New Roman',serif; background:var(--white); color:var(--text); line-height:1.6; }
  .sans { font-family:-apple-system,BlinkMacSystemFont,sans-serif; }
  img { max-width:100%; display:block; }
  a { color:var(--brown); }

  header { background:linear-gradient(180deg,#FBF3E2,var(--sand)); text-align:center; padding:44px 20px 34px; border-bottom:1px solid var(--border); }
  header img { height:60px; width:auto; margin:0 auto 14px; opacity:.9; }
  .eyebrow { font-family:-apple-system,sans-serif; font-size:11px; letter-spacing:.28em; text-transform:uppercase; color:var(--brown); }
  h1 { font-weight:400; font-size:clamp(30px,5vw,46px); letter-spacing:-.01em; color:var(--deep); margin:6px 0 4px; }
  h1 .amp { color:var(--tan); margin:0 .12em; }
  .lede { font-size:16px; color:var(--muted); max-width:560px; margin:10px auto 0; }
  .badge { display:inline-block; margin-top:16px; font-family:-apple-system,sans-serif; font-size:11px; letter-spacing:.14em; text-transform:uppercase;
           color:var(--sun-deep); background:#FBEFD9; border:1px solid #EAD6AE; border-radius:999px; padding:7px 16px; }

  main { max-width:860px; margin:0 auto; padding:34px 20px 80px; }
  .partner-banner { background:#EAF2F6; border:1px solid #C6DCE6; border-radius:6px; padding:12px 16px; text-align:center;
                    font-family:-apple-system,sans-serif; font-size:13px; color:var(--moon-deep); margin-bottom:24px; }

  .step-label { font-family:-apple-system,sans-serif; font-size:10px; letter-spacing:.24em; text-transform:uppercase; color:var(--tan); margin:26px 0 12px; }
  .pgrid { display:grid; grid-template-columns:repeat(3,1fr); gap:12px; }
  .pcard { position:relative; display:block; cursor:pointer; background:var(--sand); border:1px solid var(--border); border-radius:8px; overflow:hidden; text-align:center; transition:border-color .15s, box-shadow .15s, background .15s; }
  .pcard:hover { border-color:var(--tan); }
  .pcard input { position:absolute; opacity:0; pointer-events:none; }
  .pcard-photo { width:100%; aspect-ratio:4/3; object-fit:cover; display:block; border-bottom:1px solid var(--border); }
  .pcard-pill { position:absolute; top:10px; left:10px; z-index:2; font-family:-apple-system,sans-serif; font-size:10px;
                letter-spacing:.12em; text-transform:uppercase; background:var(--sun-deep); color:#fff;
                border-radius:999px; padding:6px 12px; box-shadow:0 2px 8px rgba(59,47,34,.25); }
  .pcard-body { padding:16px 16px 20px; }
  .pcard-icon { height:38px; width:auto; margin:0 auto 10px; }
  .pcard-name { font-size:19px; color:var(--deep); }
  .pcard-tag { font-family:-apple-system,sans-serif; font-size:10px; letter-spacing:.14em; text-transform:uppercase; color:var(--brown); margin-top:3px; }
  .pcard-blurb { font-size:13.5px; color:var(--muted); margin-top:10px; line-height:1.5; }
  .pcard:has(input:checked) { background:var(--white); border-color:var(--sun); box-shadow:0 0 0 3px rgba(232,164,74,.22); }
  .pcard[data-slug="blue-moon"]:has(input:checked) { border-color:var(--moon); box-shadow:0 0 0 3px rgba(139,171,196,.28); }

  .datebar { display:grid; grid-template-columns:1fr 1fr 1fr auto; gap:12px; align-items:end; background:var(--sand); border:1px solid var(--border); border-radius:8px; padding:18px; margin-top:6px; }
  .field { display:flex; flex-direction:column; gap:5px; }
  .field label { font-family:-apple-system,sans-serif; font-size:10px; letter-spacing:.16em; text-transform:uppercase; color:var(--brown); }
  .field input, .field select { font-family:Georgia,serif; font-size:15px; padding:11px 12px; border:1px solid var(--border); border-radius:4px; background:var(--white); color:var(--text); }
  .field input:focus, .field select:focus { outline:none; border-color:var(--tan); box-shadow:0 0 0 3px rgba(201,184,154,.25); }
  .btn { font-family:-apple-system,sans-serif; font-size:12px; letter-spacing:.18em; text-transform:uppercase; background:var(--deep); color:#fff; border:none; border-radius:4px; padding:13px 22px; cursor:pointer; transition:background .2s; }
  .btn:hover { background:var(--brown); } .btn:disabled { opacity:.5; cursor:wait; }
  .btn-lg { width:100%; padding:16px; font-size:13px; }
  .btn-sun { background:var(--sun-deep); } .btn-sun:hover { background:var(--brown); }

  #result { margin-top:22px; }
  .hidden { display:none; }
  .quote { background:var(--white); border:1px solid var(--border); border-radius:8px; overflow:hidden; }
  .quote-head { padding:18px 20px; border-bottom:1px solid var(--border); background:var(--sand); }
  .quote-head .q-name { font-size:20px; color:var(--deep); }
  .quote-head .q-dates { font-family:-apple-system,sans-serif; font-size:12px; letter-spacing:.06em; color:var(--muted); margin-top:3px; }
  .lines { padding:8px 20px 4px; }
  .line { display:flex; justify-content:space-between; padding:8px 0; font-family:-apple-system,sans-serif; font-size:14px; border-bottom:1px solid rgba(139,111,82,.08); }
  .line.total { border-bottom:none; border-top:2px solid var(--border); margin-top:4px; font-size:17px; font-family:Georgia,serif; color:var(--deep); padding-top:12px; }
  .line .lbl small { color:var(--muted); }
  .savings { margin:0 20px 16px; background:#FBEFD9; border:1px solid #EAD6AE; border-radius:6px; padding:11px 14px; font-family:-apple-system,sans-serif; font-size:13px; color:var(--sun-deep); text-align:center; }
  .savings s { color:var(--muted); }
  .dealbanner { margin:14px 20px 0; background:linear-gradient(135deg,#FBEFD9,#FDF3E3); border:2px solid var(--sun);
                border-radius:8px; padding:14px 18px; font-family:-apple-system,sans-serif; font-size:15px;
                color:var(--sun-deep); text-align:center; box-shadow:0 4px 14px rgba(232,164,74,.18); }
  .dealbanner s { color:var(--muted); font-weight:400; }
  .lockin { margin:14px 20px 0; background:#FDF3E3; border:1px solid var(--sun); border-radius:6px; padding:12px 16px;
            font-family:-apple-system,sans-serif; font-size:13.5px; color:var(--sun-deep); text-align:center; }
  .lockin #lockin-clock { font-variant-numeric:tabular-nums; font-weight:600; }
  .line.discount .lbl, .line.discount span:last-child { color:#2E7D4F; }
  .couponbar { display:flex; align-items:center; gap:12px; margin-top:10px; padding:0 4px; }
  .couponbar label { font-family:-apple-system,sans-serif; font-size:10px; letter-spacing:.16em; text-transform:uppercase; color:var(--brown); white-space:nowrap; }
  .couponbar input { flex:1; max-width:280px; font-family:Georgia,serif; font-size:14px; padding:10px 12px; border:1px solid var(--border); border-radius:4px; background:var(--white); text-transform:uppercase; }
  .line.discount span { color:var(--sun-deep); }
  .coupon-err { color:#a04a3a; } .coupon-ok { color:var(--sun-deep); }
  .unavail { padding:20px; text-align:center; color:var(--moon-deep); font-family:-apple-system,sans-serif; font-size:14px; }
  .disclaimers { margin-top:16px; background:var(--sand); border:1px solid var(--border); border-radius:8px; padding:16px 20px; }
  .disclaimers h4 { font-family:-apple-system,sans-serif; font-size:10px; letter-spacing:.2em; text-transform:uppercase; color:var(--brown); margin-bottom:8px; }
  .disclaimers li { font-family:-apple-system,sans-serif; font-size:11.5px; color:var(--muted); line-height:1.55; margin:0 0 6px 16px; }

  .resv { margin-top:16px; background:var(--white); border:1px solid var(--border); border-radius:8px; padding:20px; }
  .resv h3 { font-weight:400; font-size:18px; color:var(--deep); margin-bottom:4px; }
  .resv p.note { font-family:-apple-system,sans-serif; font-size:12.5px; color:var(--muted); margin-bottom:16px; }
  .resv-grid { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
  .resv-grid .full { grid-column:1/-1; }
  .status { margin-top:14px; font-family:-apple-system,sans-serif; font-size:13.5px; line-height:1.6; display:none; }
  .status.ok { display:block; color:var(--sun-deep); } .status.err { display:block; color:#a04a3a; }
  .securebadge { margin-top:14px; text-align:center; font-family:-apple-system,sans-serif; font-size:11px; color:var(--muted); letter-spacing:.02em; }
  .success { text-align:center; padding:34px 20px; }
  .success .chk { font-size:40px; } .success h2 { font-weight:400; font-size:26px; color:var(--deep); margin:10px 0 6px; }
  .success p { color:var(--muted); max-width:520px; margin:0 auto 10px; font-size:15px; }

  footer { text-align:center; padding:30px 20px 60px; font-family:-apple-system,sans-serif; font-size:12px; color:var(--muted); }
  footer a { color:var(--brown); }

  @media (max-width:720px){ .pgrid{grid-template-columns:1fr;} .datebar{grid-template-columns:1fr 1fr;} .datebar .field.guests{grid-column:1/-1;} .datebar .btn{grid-column:1/-1;} .resv-grid{grid-template-columns:1fr;} }
</style></head><body>
<header>
  <img src="https://sunandmoon30a.com/images/logos/sunmoon-color.png" alt="Sun &amp; Moon at 30A">
  <div class="eyebrow">Seagrove Beach \xB7 Scenic 30A \xB7 Florida</div>
  <h1>Book <span class="amp">Direct</span></h1>
  <p class="lede">Reserve Golden Sun, Blue Moon, or both cottages together \u2014 straight with the owners.</p>
  <div class="badge">Book direct with the owners \xB7 taxes &amp; cleaning included</div>
</header>

<main>
  ${partnerBanner}
  <div id="booking">
    <div class="step-label">1 \xB7 Choose your cottage</div>
    <div class="pgrid">${cards}</div>

    <div class="step-label">2 \xB7 Your dates</div>
    <div class="datebar">
      <div class="field"><label for="ci">Check-in</label><input id="ci" type="date"></div>
      <div class="field"><label for="co">Check-out</label><input id="co" type="date"></div>
      <div class="field guests"><label for="g">Guests</label><select id="g"></select></div>
      <button class="btn btn-sun" id="quoteBtn" onclick="getQuote()">See price</button>
    </div>
    <div class="couponbar">
      <label for="coupon">Have a code?</label>
      <input id="coupon" type="text" placeholder="Optional discount code" autocapitalize="characters">
    </div>

    <div id="result"></div>
  </div>
</main>

<footer>
  Questions? <a href="mailto:experience@sunandmoon30a.com">experience@sunandmoon30a.com</a>
  \xB7 Prefer the listing site? <a href="https://sunandmoon30a.com">sunandmoon30a.com</a>
</footer>

<script>
  const PARTNER = ${JSON.stringify(partner || null)};
  const PAYMENTS = ${stripeConfigured(env)};
  const MAX = { "golden-sun":8, "blue-moon":8, "full-property":16 };
  let lastQuote = null;
  // Anonymous session id \u2014 powers funnel analytics and per-session offers.
  const SID = (() => { try {
    let s = localStorage.getItem('sm_sid');
    if (!s) { s = 's_' + Math.random().toString(36).slice(2) + Date.now().toString(36); localStorage.setItem('sm_sid', s); }
    return s;
  } catch (e) { return 's_' + Math.random().toString(36).slice(2); } })();
  let lockinTimer = null;

  // guest dropdown reflects selected property
  function syncGuests() {
    const sel = document.querySelector('input[name=property]:checked');
    const max = sel ? (MAX[sel.value] || 8) : 8;
    const g = document.getElementById('g');
    const cur = parseInt(g.value || '2', 10);
    g.innerHTML = '';
    for (let i = 1; i <= max; i++) g.innerHTML += '<option value="'+i+'"'+(i===Math.min(cur,max)?' selected':'')+'>'+i+(i===1?' guest':' guests')+'</option>';
  }
  document.querySelectorAll('input[name=property]').forEach(r => r.addEventListener('change', () => { syncGuests(); clearResult(); loadCalendar(); }));
  syncGuests();

  // \u2500\u2500 Calendar: flatpickr pickers that disable dates blocked on Vacay \u2500\u2500
  const today = new Date().toISOString().slice(0,10);
  let blockedRanges = [];
  const fpCheckIn = flatpickr('#ci', {
    dateFormat: 'Y-m-d', minDate: 'today', disable: blockedRanges,
    onChange: (sel, str) => {
      clearResult();
      // check-out must be after check-in
      const min = str ? new Date(new Date(str+'T00:00:00').getTime() + 86400000).toISOString().slice(0,10) : 'today';
      fpCheckOut.set('minDate', min);
    },
  });
  const fpCheckOut = flatpickr('#co', { dateFormat: 'Y-m-d', minDate: 'today', disable: blockedRanges, onChange: clearResult });

  async function loadCalendar() {
    const sel = document.querySelector('input[name=property]:checked');
    if (!sel) return;
    try {
      const r = await fetch('/availability?property=' + encodeURIComponent(sel.value) + '&months=14');
      const j = await r.json();
      if (!j.ok) return;
      // flatpickr disable format: {from, to} inclusive
      blockedRanges = (j.blocked || []).map(b => ({ from: b.from, to: b.to }));
      fpCheckIn.set('disable', blockedRanges);
      fpCheckOut.set('disable', blockedRanges);
    } catch (e) { /* picker still works without disabled dates; quote is authoritative */ }
  }
  loadCalendar();

  function clearResult(){ document.getElementById('result').innerHTML=''; lastQuote=null; }
  const money = n => '$'+Number(n).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
  const round2 = n => Math.round(Number(n)*100)/100;
  const escp = s => String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

  async function getQuote() {
    const sel = document.querySelector('input[name=property]:checked');
    const ci = document.getElementById('ci').value, co = document.getElementById('co').value;
    const g = document.getElementById('g').value;
    const out = document.getElementById('result');
    if (!sel) { out.innerHTML = '<div class="unavail">Please choose a cottage first.</div>'; return; }
    if (!ci || !co || co <= ci) { out.innerHTML = '<div class="unavail">Please pick a check-in and a later check-out date.</div>'; return; }
    const btn = document.getElementById('quoteBtn'); btn.disabled = true; btn.textContent = 'Checking\u2026';
    out.innerHTML = '<div class="unavail">Checking availability &amp; pricing\u2026</div>';
    try {
      const p = new URLSearchParams({ property: sel.value, checkIn: ci, checkOut: co, guests: g, session: SID });
      if (PARTNER) p.set('partner', PARTNER);
      const coupon = document.getElementById('coupon').value.trim();
      if (coupon) p.set('coupon', coupon);
      const r = await fetch('/quote?' + p.toString());
      const j = await r.json();
      if (!j.ok || !j.quote) { out.innerHTML = '<div class="unavail">'+escp(j.error||'Unable to price these dates.')+'</div>'; return; }
      renderQuote(j.quote);
    } catch (e) {
      out.innerHTML = '<div class="unavail">Something went wrong. Please email experience@sunandmoon30a.com.</div>';
    } finally { btn.disabled = false; btn.textContent = 'See price'; }
  }

  // Honest countdown: when it hits zero the price is re-quoted without the offer.
  function lockinBanner(q) {
    if (lockinTimer) { clearInterval(lockinTimer); lockinTimer = null; }
    if (!q.lockinOffer || !q.lockinOffer.expiresAt) return '';
    const ends = new Date(q.lockinOffer.expiresAt).getTime();
    if (ends <= Date.now()) return '';
    lockinTimer = setInterval(() => {
      const el = document.getElementById('lockin-clock');
      if (!el) { clearInterval(lockinTimer); lockinTimer = null; return; }
      const ms = ends - Date.now();
      if (ms <= 0) { clearInterval(lockinTimer); lockinTimer = null; getQuote(); return; }
      const h = Math.floor(ms/3600e3), m = Math.floor(ms%3600e3/60e3), s = Math.floor(ms%60e3/1e3);
      el.textContent = (h>0 ? h+'h ' : '') + String(m).padStart(2,'0') + 'm ' + String(s).padStart(2,'0') + 's';
    }, 1000);
    return '<div class="lockin">&#9203; <strong>'+q.lockinOffer.pct+'% book-now saving applied</strong> \u2014 this price is held for <span id="lockin-clock">\u2026</span>. When the timer ends, the offer expires.</div>';
  }

  function renderQuote(q) {
    lastQuote = q;
    const out = document.getElementById('result');
    if (!q.available) {
      const msg = q.mode === 'call_to_book'
        ? escp(q.message) + ' <a href="mailto:experience@sunandmoon30a.com">Email us</a> or call to book.'
        : 'Those dates aren\\'t available. Try different nights, or <a href="mailto:experience@sunandmoon30a.com">ask us</a> about alternatives.';
      out.innerHTML = '<div class="quote"><div class="quote-head"><div class="q-name">'+escp(q.propertyName)+'</div>'+
        '<div class="q-dates">'+escp(q.checkIn)+' \u2192 '+escp(q.checkOut)+'</div></div>'+
        '<div class="unavail">'+msg+'</div></div>';
      return;
    }
    const feeLines = (q.fees||[]).map(f => '<div class="line"><span class="lbl">'+escp(f.name)+'</span><span>'+money(f.amount)+'</span></div>').join('');
    const taxLines = (q.taxes||[]).map(t => '<div class="line"><span class="lbl">'+escp(t.name)+' <small>('+t.rate+'%)</small></span><span>'+money(t.amount)+'</span></div>').join('');
    const couponLine = q.coupon
      ? '<div class="line discount"><span class="lbl">Discount ('+escp(q.coupon.code)+' \xB7 '+q.coupon.pct+'% off nightly)</span><span>\u2212'+money(q.coupon.discount)+'</span></div>'
      : '';
    const couponMsg = q.couponError ? '<div class="line"><span class="coupon-err">'+escp(q.couponError)+'</span></div>'
      : (q.coupon ? '<div class="line"><span class="coupon-ok">Code '+escp(q.coupon.code)+' applied \u2713</span></div>' : '');

    // Yield discounts (ladder / gap / lock-in) shown as their own lines.
    const YIELD_LABELS = { gap_fill: 'Short-stay special', lockin_offer: 'Book-now offer', owner_adjustments: 'Preferred rate' };
    const yieldAdj = (q.yieldAdjustments||[]).filter(a => a.amount < 0 && a.rule.indexOf('coupon_') !== 0);
    const yieldLines = yieldAdj.map(a => {
      const lbl = YIELD_LABELS[a.rule] || (a.rule.indexOf('ladder_') === 0 ? 'Last-minute savings' : 'Special rate');
      return '<div class="line discount"><span class="lbl">'+lbl+' ('+Math.abs(a.pct)+'% off nightly)</span><span>\u2212'+money(Math.abs(a.amount))+'</span></div>';
    }).join('');
    const yieldTotal = yieldAdj.reduce((s,a) => s + Math.abs(a.amount), 0);
    const anchorNightlyTotal = round2(q.nightlyTotal + yieldTotal + (q.coupon ? q.coupon.discount : 0));
    const nightlyLbl = money(round2(anchorNightlyTotal / q.nights)) + ' \xD7 ' + q.nights + ' nights';
    const nightlyBeforeCoupon = anchorNightlyTotal;

    // Showcase banner: near-date / gap discounts get top billing with was\u2192now
    const showcaseAdj = yieldAdj.find(a => a.rule.indexOf('ladder_') === 0 || a.rule === 'gap_fill');
    const showcasePct = yieldAdj.filter(a => a.rule.indexOf('ladder_') === 0 || a.rule === 'gap_fill' || a.rule === 'lockin_offer')
                                .reduce((s,a) => s + Math.abs(a.pct), 0);
    const wasRate = round2(anchorNightlyTotal / q.nights), nowRate = round2(q.nightlyTotal / q.nights);
    const showcase = showcaseAdj
      ? '<div class="dealbanner">\u2600\uFE0F <strong>' + Math.round(showcasePct) + '% off these nights</strong> \u2014 ' +
        '<s>' + money(wasRate) + '</s> <strong>' + money(nowRate) + '</strong>/night. ' +
        (showcaseAdj.rule === 'gap_fill' ? 'A rare short-stay opening between bookings.' : 'Last-minute rate for unbooked dates \u2014 while they last.') + '</div>'
      : '';
    const shortStayNote = q.minStayRelaxed
      ? '<div class="line"><span class="coupon-ok">Rare short-stay opening \u2014 these nights fit perfectly between other stays.</span></div>' : '';
    const savings = q.compare && q.compare.directSavings > 1
      ? '<div class="savings">You save <strong>'+money(q.compare.directSavings)+'</strong> vs. the listing-site price for these dates.</div>' : '';
    const payNote = q.fullPaymentNow
      ? '<div class="line"><span class="lbl"><small>Paid in full at booking (within '+30+' days of arrival)</small></span><span></span></div>'
      : '<div class="line"><span class="lbl"><small>'+q.depositPct+'% deposit today ('+money(q.deposit)+'); balance '+money(q.balance)+' on '+escp(q.balanceDueDate)+'</small></span><span></span></div>';
    const discl = (q.disclaimers||[]).map(d => '<li>'+escp(d)+'</li>').join('');
    out.innerHTML =
      '<div class="quote">'+
        '<div class="quote-head"><div class="q-name">'+escp(q.propertyName)+'</div>'+
        '<div class="q-dates">'+escp(q.checkIn)+' \u2192 '+escp(q.checkOut)+' \xB7 '+q.nights+' night'+(q.nights===1?'':'s')+' \xB7 '+q.guests+' guest'+(q.guests===1?'':'s')+'</div></div>'+
        showcase + lockinBanner(q) +
        '<div class="lines">'+
          '<div class="line"><span class="lbl">'+nightlyLbl+'</span><span>'+money(nightlyBeforeCoupon)+'</span></div>'+
          yieldLines + couponLine +
          '<div class="line"><span class="lbl">Cleaning fee</span><span>'+money(q.cleaningFee)+'</span></div>'+
          feeLines + taxLines +
          '<div class="line total"><span>Total'+(q.currency&&q.currency!=='USD'?' ('+q.currency+')':'')+'</span><span>'+money(q.total)+'</span></div>'+
          payNote + couponMsg + shortStayNote +
        '</div>'+ savings +
      '</div>'+
      '<div class="disclaimers"><h4>Please note</h4><ul>'+discl+'</ul></div>'+
      '<div class="resv">'+
        '<h3>'+(PAYMENTS ? 'Reserve &amp; secure your dates' : 'Request this reservation')+'</h3>'+
        '<p class="note">'+(PAYMENTS
          ? (q.fullPaymentNow
              ? 'Pay in full ('+money(q.deposit)+') to confirm instantly \u2014 your dates are held while you pay.'
              : money(q.deposit)+' deposit today to confirm; the balance of '+money(q.balance)+' is automatically charged to the same card on '+escp(q.balanceDueDate)+'.')
          : 'We hold your dates for 48 hours while we send the rental agreement and payment instructions. No charge is made on this page.')+'</p>'+
        '<div class="resv-grid">'+
          '<div class="field"><label>Full name</label><input id="rname" autocomplete="name"></div>'+
          '<div class="field"><label>Email</label><input id="remail" type="email" autocomplete="email"></div>'+
          '<div class="field full"><label>Phone</label><input id="rphone" type="tel" autocomplete="tel"></div>'+
          '<div class="field full"><label>Anything we should know? (optional)</label><input id="rnotes"></div>'+
        '</div>'+
        (PAYMENTS
          ? '<button class="btn btn-lg btn-sun" id="resvBtn" style="margin-top:16px" onclick="startCheckout()">Continue to secure payment \u2192</button>'
          : '<button class="btn btn-lg" id="resvBtn" style="margin-top:16px" onclick="reserve()">Request reservation \u2192</button>')+
        '<div id="payarea"></div>'+
        '<div class="status" id="rstatus"></div>'+
        '<div class="securebadge">\u{1F512} Secure payment by Stripe \xB7 your card details never touch our servers</div>'+
      '</div>';
  }

  // \u2500\u2500 Checkout (Stripe Payment Element) \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
  let stripeObj = null, elements = null, checkoutState = null;

  async function startCheckout() {
    if (!lastQuote) return;
    const name = document.getElementById('rname').value.trim();
    const email = document.getElementById('remail').value.trim();
    const phone = document.getElementById('rphone').value.trim();
    const notes = document.getElementById('rnotes').value.trim();
    const st = document.getElementById('rstatus'); st.className = 'status';
    if (!name || !/^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$/.test(email)) {
      st.className = 'status err'; st.textContent = 'Please enter your name and a valid email.'; return;
    }
    const btn = document.getElementById('resvBtn'); btn.disabled = true; btn.textContent = 'Preparing secure checkout\u2026';
    try {
      const body = { property: lastQuote.property, checkIn: lastQuote.checkIn, checkOut: lastQuote.checkOut,
        guests: lastQuote.guests, name, email, phone, notes, session_id: SID };
      if (PARTNER) body.partner = PARTNER;
      if (lastQuote.coupon) body.coupon = lastQuote.coupon.code;
      const r = await fetch('/checkout/create', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body) });
      const j = await r.json();
      if (!r.ok || !j.ok) throw new Error(j.error || 'could not start checkout');
      checkoutState = { reservationId: j.reservationId, paymentIntentId: j.paymentIntentId, name, email, amount: j.amountDue };
      stripeObj = Stripe(j.publishableKey);
      elements = stripeObj.elements({ clientSecret: j.clientSecret, appearance: { theme: 'flat', variables: { colorPrimary: '#9B6B20', fontFamily: 'Georgia, serif', borderRadius: '4px' } } });
      const pe = elements.create('payment');
      // reveal payment area
      document.getElementById('payarea').innerHTML =
        '<div id="pe" style="margin:16px 0"></div>'+
        '<button class="btn btn-lg btn-sun" id="payBtn" onclick="payDeposit()">Pay '+money(j.amountDue)+(j.fullPaymentNow?' (in full)':' deposit')+' \u2192</button>';
      pe.mount('#pe');
      btn.style.display = 'none';
    } catch (e) {
      btn.disabled = false; btn.textContent = 'Continue to secure payment \u2192';
      st.className = 'status err';
      st.innerHTML = (String(e).includes('not available')
        ? 'Sorry \u2014 those dates were just taken. Please try different nights.'
        : 'Could not start checkout. Please email <a href="mailto:experience@sunandmoon30a.com">experience@sunandmoon30a.com</a>.');
    }
  }

  async function payDeposit() {
    const st = document.getElementById('rstatus'); st.className = 'status';
    const payBtn = document.getElementById('payBtn'); payBtn.disabled = true; payBtn.textContent = 'Processing\u2026';
    try {
      const { error } = await stripeObj.confirmPayment({ elements, redirect: 'if_required' });
      if (error) { throw new Error(error.message || 'payment failed'); }
      // payment succeeded \u2192 finalize server-side (authoritative)
      const r = await fetch('/checkout/finalize', { method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ reservationId: checkoutState.reservationId, paymentIntentId: checkoutState.paymentIntentId }) });
      const j = await r.json();
      if (!r.ok || !j.ok) throw new Error(j.error || 'finalize failed');
      const bal = j.balance > 0
        ? '<p>We\\'ve charged your '+money(j.amountPaid)+' deposit. The remaining <strong>'+money(j.balance)+'</strong> will be automatically charged to the same card on <strong>'+escp(j.balanceDueDate)+'</strong>.</p>'
        : '<p>Paid in full: <strong>'+money(j.amountPaid)+'</strong>. You\\'re all set!</p>';
      document.getElementById('booking').innerHTML =
        '<div class="success"><div class="chk">\u{1F305}</div><h2>You\\'re booked!</h2>'+
        '<p>Thank you, '+escp(checkoutState.name)+'. Your stay at <strong>'+escp(lastQuote.propertyName)+'</strong> ('+escp(lastQuote.checkIn)+' \u2192 '+escp(lastQuote.checkOut)+') is confirmed.</p>'+
        '<p>Confirmation <strong>'+escp(j.confirmationCode)+'</strong> \u2014 a receipt is on its way to <strong>'+escp(checkoutState.email)+'</strong>.</p>'+
        bal + '</div>';
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } catch (e) {
      payBtn.disabled = false; payBtn.textContent = 'Try payment again \u2192';
      st.className = 'status err'; st.textContent = String(e).replace('Error: ','');
    }
  }

  async function reserve() {
    if (!lastQuote) return;
    const name = document.getElementById('rname').value.trim();
    const email = document.getElementById('remail').value.trim();
    const phone = document.getElementById('rphone').value.trim();
    const notes = document.getElementById('rnotes').value.trim();
    const st = document.getElementById('rstatus'); st.className = 'status';
    if (!name || !/^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$/.test(email)) {
      st.className = 'status err'; st.textContent = 'Please enter your name and a valid email.'; return;
    }
    const btn = document.getElementById('resvBtn'); btn.disabled = true; btn.textContent = 'Sending\u2026';
    try {
      const body = { property: lastQuote.property, checkIn: lastQuote.checkIn, checkOut: lastQuote.checkOut,
        guests: lastQuote.guests, name, email, phone, notes, session_id: SID };
      if (PARTNER) body.partner = PARTNER;
      if (lastQuote.coupon) body.coupon = lastQuote.coupon.code;
      const r = await fetch('/reserve', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body) });
      const j = await r.json();
      if (!r.ok || !j.ok) throw new Error(j.error || 'reservation failed');
      document.getElementById('booking').innerHTML =
        '<div class="success"><div class="chk">\u{1F305}</div><h2>Reservation requested</h2>'+
        '<p>Thank you, '+escp(name)+'. Your dates at <strong>'+escp(lastQuote.propertyName)+'</strong> ('+escp(lastQuote.checkIn)+' \u2192 '+escp(lastQuote.checkOut)+') are held for 48 hours.</p>'+
        '<p>We\\'ll email you at <strong>'+escp(email)+'</strong> with the rental agreement and payment instructions to confirm. Watch for a note from experience@sunandmoon30a.com.</p></div>';
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } catch (e) {
      btn.disabled = false; btn.textContent = 'Request reservation \u2192';
      st.className = 'status err';
      st.innerHTML = (String(e).includes('not available')
        ? 'Sorry \u2014 those dates were just taken. Please try different nights.'
        : 'Something went wrong. Please email <a href="mailto:experience@sunandmoon30a.com">experience@sunandmoon30a.com</a> and we\\'ll help directly.');
    }
  }
<\/script>
</body></html>`;
}
__name(guestPage, "guestPage");
var worker_default = {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;
    const host = (request.headers.get("host") || "").toLowerCase();
    const subMatch = host.match(/^([a-z0-9-]+)\.sunandmoon30a\.com$/);
    const subPartner = subMatch && !["book", "www", "track", "sunandmoon30a"].includes(subMatch[1]) ? subMatch[1] : null;
    try {
      if (path === "/healthz") return new Response("ok");
      if (path === "/health") return await handleHealth(env);
      if (path === "/event" && request.method === "POST") return await handleEvent(request, env, ctx);
      if ((path === "/" || path === "/book") && request.method === "GET") {
        const params = Object.fromEntries(url.searchParams);
        if (subPartner && !params.partner) params.partner = subPartner;
        const html = await guestPage(env, params);
        return new Response(html, { headers: {
          "Content-Type": "text/html; charset=utf-8",
          "Cache-Control": "no-store"
        } });
      }
      if (path === "/quote" && request.method === "GET") {
        const params = Object.fromEntries(url.searchParams);
        if (subPartner && !params.partner) params.partner = subPartner;
        const q = await buildQuote(env, params);
        if (q.ok && q.quote) {
          const qt = q.quote;
          const sid = params.session || null;
          const propRow = await getProperty(env, params.property).catch(() => null);
          if (qt.available && propRow) {
            ctx.waitUntil(sb(env, "POST", "/rest/v1/price_display_log", {
              session_id: sid,
              property_id: propRow.id,
              check_in: qt.checkIn,
              check_out: qt.checkOut,
              ab_bucket: qt.abBucket || "A",
              base_nightly_total: qt.baseNightlyTotal,
              displayed_nightly_total: qt.nightlyTotal,
              total: qt.total,
              adjustments: qt.yieldAdjustments || [],
              context: {
                guests: qt.guests,
                nights: qt.nights,
                coupon: qt.coupon?.code || null,
                partner: qt.partner?.slug || null,
                lockin: !!qt.lockinOffer,
                min_stay_relaxed: !!qt.minStayRelaxed,
                protected: !!qt.yieldProtected
              }
            }, { "Prefer": "return=minimal" }).catch(() => {
            }));
          }
          ctx.waitUntil(sb(env, "POST", "/rest/v1/booking_events", {
            session_id: sid,
            event: qt.available ? "date_search" : "no_availability",
            property_id: propRow?.id || null,
            metadata: { property: params.property, checkIn: qt.checkIn, checkOut: qt.checkOut }
          }, { "Prefer": "return=minimal" }).catch(() => {
          }));
        }
        return q.ok ? json(200, { ok: true, ...q.quote ? { quote: q.quote } : {} }) : json(q.status, { ok: false, error: q.error });
      }
      if (path === "/availability" && request.method === "GET") {
        return await handleAvailability(env, url.searchParams, ctx);
      }
      if (path === "/reserve" && request.method === "POST") return handleReserve(request, env, ctx);
      if (path === "/checkout/create" && request.method === "POST") return handleCheckoutCreate(request, env, ctx);
      if (path === "/checkout/finalize" && request.method === "POST") return handleCheckoutFinalize(request, env, ctx);
      if (path === "/webhook/reservation" && request.method === "POST") return handleWebhook(request, env);
      if (path === "/webhook/stripe" && request.method === "POST") return handleStripeWebhook(request, env, ctx);
      if (path.startsWith("/admin/")) {
        const rest = path.slice(7);
        const [token, action] = rest.split("/");
        if (!env.ADMIN_TOKEN || token !== env.ADMIN_TOKEN) return new Response("Not found", { status: 404 });
        const base = `/admin/${token}`;
        if (!action && request.method === "GET") {
          const html = await adminPage(env, base, url.searchParams.get("notice") || "");
          return new Response(html, { headers: {
            "Content-Type": "text/html; charset=utf-8",
            "Cache-Control": "no-store",
            "X-Robots-Tag": "noindex, nofollow",
            "Referrer-Policy": "no-referrer"
          } });
        }
        if (action && request.method === "POST") return handleAdminPost(request, env, base, action);
        return new Response("Not found", { status: 404 });
      }
      return new Response("Not found", { status: 404 });
    } catch (e) {
      console.log("booking worker error", e?.stack || e);
      return json(500, { ok: false, error: "internal" });
    }
  },
  // Cron dispatch (see wrangler.toml [triggers]):
  //   */15 * * * *  → availability + rate poll (interim primary; webhook safety net)
  //   10 11,23 * * * → twice-daily FULL validation vs live Vacay (runs forever)
  async scheduled(event, env, ctx) {
    const cron = event.cron || "";
    const vm = cron.match(/^(10|30|50) 11,23/);
    if (vm) {
      const propIndex = { "10": 0, "30": 1, "50": 2 }[vm[1]];
      ctx.waitUntil(runValidation(env, "cron", propIndex).catch((e) => console.log("validation error", e)));
      if (vm[1] === "10") {
        ctx.waitUntil(runBalanceCharges(env, ctx).catch((e) => console.log("balance charge error", e)));
      }
    } else {
      ctx.waitUntil(runSync(env, "cron").catch((e) => console.log("sync error", e)));
    }
  }
};
export {
  worker_default as default
};
//# sourceMappingURL=worker.js.map
