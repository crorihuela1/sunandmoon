var __defProp = Object.defineProperty;
var __name = (target, value) => __defProp(target, "name", { value, configurable: true });

// src/index.ts
var LISTING_MAP = {
  232268: "blue-moon",
  // 65 Crystal Ct (Tusk Sands LLC)
  503319: "golden-sun"
  // 53 Crystal Ct (Mellow Elephant LLC)
};
var PROPERTIES = ["blue-moon", "golden-sun"];
var index_default = {
  async fetch(req, env, ctx) {
    const url = new URL(req.url);
    try {
      if (req.method === "POST" && url.pathname === "/hostaway") return handleWebhook(req, env, ctx);
      if (req.method === "GET" && url.pathname === "/availability") return handleAvailability(req, env);
      if (req.method === "POST" && url.pathname === "/book") return handleBook(req, env, ctx);
      if (req.method === "OPTIONS") return corsPreflight(env);
      if (req.method === "GET" && url.pathname === "/health") return handleHealth(env);
      return json({ error: "not found" }, 404);
    } catch (err) {
      console.error("unhandled", err);
      return json({ error: "internal" }, 500);
    }
  },
  // Cron: iCal reconciliation.
  async scheduled(_evt, env, ctx) {
    ctx.waitUntil(syncAllIcal(env));
  }
};
async function handleWebhook(req, env, ctx) {
  if (!checkBasicAuth(req.headers.get("Authorization"), env.WEBHOOK_USER, env.WEBHOOK_PASS)) {
    return json({ error: "unauthorized" }, 401);
  }
  const body = await req.text();
  let payload = null;
  try {
    payload = JSON.parse(body);
  } catch {
  }
  const event = classifyEvent(payload);
  ctx.waitUntil(processWebhook(env, event, body, payload));
  return json({ ok: true });
}
__name(handleWebhook, "handleWebhook");
function classifyEvent(p) {
  if (!p) return "unparseable";
  const e = p.event ?? p.eventType ?? "";
  if (typeof e === "string" && e.length) return e;
  if (p.object === "message" || p.conversationId) return "message";
  if (p.reservationId || p.id || p.data?.id) return "reservation.unknown";
  return "unknown";
}
__name(classifyEvent, "classifyEvent");
async function processWebhook(env, event, rawBody, payload) {
  await env.DB.prepare(
    "INSERT INTO webhook_log (event, http_status, body) VALUES (?, 200, ?)"
  ).bind(event, rawBody.slice(0, 1e5)).run();
  if (event.startsWith("message") || event === "new message received") return;
  if (!payload) return;
  const r = extractReservation(payload);
  if (!r.id) return;
  await env.DB.prepare(`
    INSERT INTO reservations (hostaway_id, listing_map_id, property, arrival, departure, status, channel, guest_name, raw, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
    ON CONFLICT(hostaway_id) DO UPDATE SET
      listing_map_id = excluded.listing_map_id,
      property       = excluded.property,
      arrival        = excluded.arrival,
      departure      = excluded.departure,
      status         = excluded.status,
      channel        = excluded.channel,
      guest_name     = COALESCE(excluded.guest_name, reservations.guest_name),
      raw            = excluded.raw,
      updated_at     = datetime('now')
  `).bind(r.id, r.listingMapId, r.property, r.arrival, r.departure, r.status, r.channel, r.guestName, rawBody.slice(0, 1e5)).run();
  if (r.property !== "unknown" && r.arrival && r.departure && !isCancelled(r.status)) {
    const match = await env.DB.prepare(
      "SELECT id, guest_email, guest_name FROM direct_requests WHERE status='pending' AND property=? AND arrival=? AND departure=? LIMIT 1"
    ).bind(r.property, r.arrival, r.departure).first();
    if (match) {
      await env.DB.prepare(
        "UPDATE direct_requests SET status='confirmed', confirmed_reservation_id=? WHERE id=?"
      ).bind(r.id, match.id).run();
      await sendEmail(env, {
        to: [match.guest_email],
        bcc: [env.OWNER_EMAIL],
        subject: `Your Sun & Moon at 30A reservation is confirmed (${match.id})`,
        text: `Hi ${match.guest_name},

Great news — your stay is confirmed!

Property: ${prettyName(r.property)}
Check-in: ${r.arrival}
Check-out: ${r.departure}
Reference: ${match.id}

You'll receive check-in details closer to your arrival. We can't wait to host you on 30A.

— Sun & Moon at 30A
sunandmoon30a.com`
      });
    }
  }
}
__name(processWebhook, "processWebhook");
function extractReservation(p) {
  const d = p.data ?? p.reservation ?? p;
  const listingMapId = num(d.listingMapId ?? d.listingId ?? p.listingMapId);
  return {
    id: num(d.id ?? d.reservationId ?? p.reservationId),
    listingMapId,
    property: listingMapId != null ? LISTING_MAP[listingMapId] ?? "unknown" : "unknown",
    arrival: str(d.arrivalDate ?? d.checkIn ?? d.arrival),
    departure: str(d.departureDate ?? d.checkOut ?? d.departure),
    status: str(d.status ?? p.status) ?? "unknown",
    channel: str(d.channelName ?? d.channel ?? d.channelId),
    guestName: str(d.guestName ?? ([d.guestFirstName, d.guestLastName].filter(Boolean).join(" ") || null))
  };
}
__name(extractReservation, "extractReservation");
var isCancelled = /* @__PURE__ */ __name((s) => !!s && /cancel|declin|expire/i.test(s), "isCancelled");
var num = /* @__PURE__ */ __name((v) => v == null || v === "" || isNaN(Number(v)) ? null : Number(v), "num");
var str = /* @__PURE__ */ __name((v) => v == null ? null : String(v), "str");
async function syncAllIcal(env) {
  const feeds = [
    ["blue-moon", env.ICAL_URL_BLUE_MOON],
    ["golden-sun", env.ICAL_URL_GOLDEN_SUN]
  ];
  for (const [property, feedUrl] of feeds) {
    if (!feedUrl) continue;
    try {
      const res = await fetch(feedUrl, { headers: { "User-Agent": "sunmoon-hooks/1.0" } });
      if (!res.ok) {
        console.error(`ical ${property} HTTP ${res.status}`);
        continue;
      }
      const events = parseIcal(await res.text());
      const stmts = [env.DB.prepare("DELETE FROM ical_blocks WHERE property=?").bind(property)];
      for (const ev of events) {
        stmts.push(env.DB.prepare(
          "INSERT OR REPLACE INTO ical_blocks (property, start_date, end_date, uid, summary) VALUES (?,?,?,?,?)"
        ).bind(property, ev.start, ev.end, ev.uid ?? "", ev.summary ?? ""));
      }
      await env.DB.batch(stmts);
    } catch (err) {
      console.error(`ical ${property} sync failed`, err);
    }
  }
}
__name(syncAllIcal, "syncAllIcal");
function parseIcal(text) {
  const unfolded = text.replace(/\r\n[ \t]/g, "").replace(/\r/g, "");
  const out = [];
  let cur = null;
  for (const line of unfolded.split("\n")) {
    if (line.startsWith("BEGIN:VEVENT")) cur = {};
    else if (line.startsWith("END:VEVENT")) {
      if (cur?.start && cur?.end) out.push(cur);
      cur = null;
    } else if (cur) {
      const [k, ...rest] = line.split(":");
      const val = rest.join(":").trim();
      const key = k.split(";")[0].toUpperCase();
      if (key === "DTSTART") cur.start = icalDate(val);
      else if (key === "DTEND") cur.end = icalDate(val);
      else if (key === "UID") cur.uid = val;
      else if (key === "SUMMARY") cur.summary = val.slice(0, 200);
    }
  }
  return out;
}
__name(parseIcal, "parseIcal");
var icalDate = /* @__PURE__ */ __name((v) => `${v.slice(0, 4)}-${v.slice(4, 6)}-${v.slice(6, 8)}`, "icalDate");
async function handleAvailability(req, env) {
  const property = new URL(req.url).searchParams.get("property") ?? "";
  if (!PROPERTIES.includes(property)) return json({ error: "property must be blue-moon or golden-sun" }, 400, env);
  const [res, ical] = await Promise.all([
    env.DB.prepare(
      "SELECT arrival AS start, departure AS end FROM reservations WHERE property=? AND status NOT LIKE '%cancel%' AND departure >= date('now')"
    ).bind(property).all(),
    env.DB.prepare(
      "SELECT start_date AS start, end_date AS end FROM ical_blocks WHERE property=? AND end_date >= date('now')"
    ).bind(property).all()
  ]);
  const blocked = mergeRanges([...res.results ?? [], ...ical.results ?? []]);
  return json({ property, blocked, generatedAt: (/* @__PURE__ */ new Date()).toISOString() }, 200, env);
}
__name(handleAvailability, "handleAvailability");
function mergeRanges(ranges) {
  const sorted = ranges.filter((r) => r.start && r.end).sort((a, b) => a.start.localeCompare(b.start));
  const out = [];
  for (const r of sorted) {
    const last = out[out.length - 1];
    if (last && r.start <= last.end) {
      if (r.end > last.end) last.end = r.end;
    } else out.push({ ...r });
  }
  return out;
}
__name(mergeRanges, "mergeRanges");
async function handleBook(req, env, ctx) {
  const b = await req.json().catch(() => null);
  if (!b) return json({ error: "invalid json" }, 400, env);
  if (b.website) return json({ ok: true }, 200, env);
  const property = String(b.property ?? "");
  const arrival = String(b.arrival ?? "");
  const departure = String(b.departure ?? "");
  const guestName = String(b.guestName ?? "").trim().slice(0, 120);
  const guestEmail = String(b.guestEmail ?? "").trim().slice(0, 200);
  const dateRe = /^\d{4}-\d{2}-\d{2}$/;
  if (!PROPERTIES.includes(property)) return json({ error: "invalid property" }, 400, env);
  if (!dateRe.test(arrival) || !dateRe.test(departure) || departure <= arrival)
    return json({ error: "invalid dates" }, 400, env);
  if (!guestName || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(guestEmail))
    return json({ error: "name and valid email required" }, 400, env);
  const conflict = await env.DB.prepare(`
    SELECT 1 FROM (
      SELECT arrival AS s, departure AS e FROM reservations WHERE property=?1 AND status NOT LIKE '%cancel%'
      UNION ALL
      SELECT start_date, end_date FROM ical_blocks WHERE property=?1
    ) WHERE s < ?3 AND e > ?2 LIMIT 1
  `).bind(property, arrival, departure).first();
  if (conflict) return json({ error: "dates_unavailable" }, 409, env);
  const holdId = "SM-" + crypto.randomUUID().slice(0, 6).toUpperCase();
  await env.DB.prepare(`
    INSERT INTO direct_requests (id, property, arrival, departure, guest_name, guest_email, guest_phone, guests_count, note)
    VALUES (?,?,?,?,?,?,?,?,?)
  `).bind(
    holdId,
    property,
    arrival,
    departure,
    guestName,
    guestEmail,
    str(b.guestPhone)?.slice(0, 40) ?? null,
    num(b.guests),
    str(b.note)?.slice(0, 1e3) ?? null
  ).run();
  ctx.waitUntil((async () => {
    const nights = Math.round((Date.parse(departure) - Date.parse(arrival)) / 864e5);
    await sendEmail(env, {
      to: [env.VRN_EMAIL],
      cc: [env.OWNER_EMAIL],
      subject: `NEW DIRECT BOOKING — ${prettyName(property)} — ${arrival} to ${departure} (${holdId})`,
      text: `Hi Mindy,

A new direct booking request just came in through sunandmoon30a.com. Please enter it into Hostaway as an owner-sourced direct reservation at your earliest convenience — the dates are open as of ${(/* @__PURE__ */ new Date()).toISOString()}.

Reference:   ${holdId}
Property:    ${prettyName(property)}
Check-in:    ${arrival}
Check-out:   ${departure}  (${nights} nights)
Guest:       ${guestName}
Email:       ${guestEmail}
Phone:       ${b.guestPhone ?? "—"}
Guests:      ${b.guests ?? "—"}
Notes:       ${b.note ?? "—"}

Once it's entered, my system will pick up the Hostaway confirmation automatically and send the guest their confirmation — no further action needed on your side.

Thanks!
Cristian (automated via sunandmoon30a.com)`
    });
    await env.DB.prepare("UPDATE direct_requests SET emailed_at=datetime('now') WHERE id=?").bind(holdId).run();
    await sendEmail(env, {
      to: [guestEmail],
      subject: `We received your booking request — Sun & Moon at 30A (${holdId})`,
      text: `Hi ${guestName},

Thanks for your request to stay at ${prettyName(property)} from ${arrival} to ${departure}.

Your reference is ${holdId}. We're placing a hold and will confirm your reservation shortly — typically within a few business hours. You'll receive a confirmation email as soon as it's locked in.

— Sun & Moon at 30A
sunandmoon30a.com`
    });
  })());
  return json({ ok: true, holdId, status: "pending" }, 201, env);
}
__name(handleBook, "handleBook");
async function handleHealth(env) {
  const [lastHook, lastSync] = await Promise.all([
    env.DB.prepare("SELECT received_at FROM webhook_log ORDER BY id DESC LIMIT 1").first(),
    env.DB.prepare("SELECT MAX(synced_at) AS t FROM ical_blocks").first()
  ]).catch(() => [null, null]);
  return json({ ok: true, lastWebhook: lastHook?.received_at ?? null, lastIcalSync: lastSync?.t ?? null });
}
__name(handleHealth, "handleHealth");
function checkBasicAuth(header, user, pass) {
  if (!header?.startsWith("Basic ") || !user || !pass) return false;
  let decoded = "";
  try {
    decoded = atob(header.slice(6));
  } catch {
    return false;
  }
  return timingSafeEqual(decoded, `${user}:${pass}`);
}
__name(checkBasicAuth, "checkBasicAuth");
function timingSafeEqual(a, b) {
  const ea = new TextEncoder().encode(a), eb = new TextEncoder().encode(b);
  if (ea.length !== eb.length) return false;
  let diff = 0;
  for (let i = 0; i < ea.length; i++) diff |= ea[i] ^ eb[i];
  return diff === 0;
}
__name(timingSafeEqual, "timingSafeEqual");
async function sendEmail(env, msg) {
  if (!env.RESEND_API_KEY) {
    console.error("RESEND_API_KEY not set — email skipped:", msg.subject);
    return;
  }
  const res = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: { Authorization: `Bearer ${env.RESEND_API_KEY}`, "Content-Type": "application/json" },
    body: JSON.stringify({ from: `Sun & Moon at 30A <${env.FROM_EMAIL}>`, ...msg })
  });
  if (!res.ok) console.error("resend failed", res.status, await res.text());
}
__name(sendEmail, "sendEmail");
var prettyName = /* @__PURE__ */ __name((slug) => slug === "blue-moon" ? "Blue Moon (65 Crystal Ct)" : slug === "golden-sun" ? "Golden Sun (53 Crystal Ct)" : slug, "prettyName");
function corsPreflight(env) {
  return new Response(null, { status: 204, headers: corsHeaders(env) });
}
__name(corsPreflight, "corsPreflight");
function corsHeaders(env) {
  return {
    "Access-Control-Allow-Origin": env.SITE_ORIGIN,
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "86400"
  };
}
__name(corsHeaders, "corsHeaders");
function json(data, status = 200, env) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json", ...env ? corsHeaders(env) : {} }
  });
}
__name(json, "json");
export {
  index_default as default
};
//# sourceMappingURL=index.js.map
