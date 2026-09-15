/**
 * sunmoon-outreach — the referral outreach sender.
 *
 * Replaces `outreach_email.py`, which ran on a local Mac that was destroyed on
 * 2026-08-12, taking the only copy of the source with it. Sending stopped that
 * day. This worker is a clean reimplementation from the surviving evidence in
 * Supabase (outreach rows, tracking_links, campaigns) — see docs/RECOVERY.md.
 *
 * Runs on a Cloudflare cron trigger, so there is no laptop in the loop.
 *
 * Per run it:
 *   1. reads `v_outreach_ready` (contacts with an email, segment status
 *      'prospect', not emailed in the last 60 days),
 *   2. drops suppressed addresses and de-dupes to one contact per company,
 *   3. for each pick, ensures a rev_share_partner + campaign exist,
 *   4. inserts the `outreach` row FIRST (its id is baked into the open pixel),
 *   5. mints a tracking_link whose destination carries ?partner=<slug>,
 *   6. sends via Resend, then marks the row sent and flips the segment row to
 *      'contacted'.
 *
 * Safety: DRY_RUN defaults to true. Nothing sends until you set it to "false".
 */

const DEFAULTS = {
  DAILY_CAP: 25,
  TRACK_BASE: "https://track.sunandmoon30a.com",
  BOOK_BASE: "https://book.sunandmoon30a.com",
  GUEST_DISCOUNT_PCT: 5,
  REV_SHARE_PCT: 10,
};

/* ---------------------------------------------------------------- Supabase */

function sbHeaders(env, extra = {}) {
  return {
    apikey: env.SUPABASE_SERVICE_KEY,
    Authorization: `Bearer ${env.SUPABASE_SERVICE_KEY}`,
    "Content-Type": "application/json",
    ...extra,
  };
}

async function sb(env, method, path, body, extraHeaders = {}) {
  const res = await fetch(`${env.SUPABASE_URL.replace(/\/$/, "")}${path}`, {
    method,
    headers: sbHeaders(env, extraHeaders),
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (!res.ok) {
    throw new Error(`supabase ${method} ${path} -> ${res.status} ${await res.text()}`);
  }
  const text = await res.text();
  return text ? JSON.parse(text) : null;
}

const sbSelect = (env, path) => sb(env, "GET", path, undefined, { Accept: "application/json" });
const sbInsert = (env, path, row) =>
  sb(env, "POST", path, row, { Prefer: "return=representation" });
const sbPatch = (env, path, patch) =>
  sb(env, "PATCH", path, patch, { Prefer: "return=minimal" });

/* ------------------------------------------------------------------ utils */

const B64URL = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";

/** 9-char base64url short code, matching the existing tracking_links format. */
function shortCode(len = 9) {
  const bytes = crypto.getRandomValues(new Uint8Array(len));
  let out = "";
  for (const b of bytes) out += B64URL[b % 64];
  return out;
}

function slugify(s) {
  return String(s || "")
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/&/g, " and ")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60) || "partner";
}

function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** First name if it looks like a real one, else null (so we fall back to "Hi there"). */
function greetingName(contact) {
  const first = String(contact.first_name || "").trim();
  if (!first) return null;
  if (first.length > 24) return null;
  // Reject role mailboxes that got parsed into a name field.
  if (/^(info|hello|contact|admin|office|events|sales|bookings?|team|support)$/i.test(first)) return null;
  return first.charAt(0).toUpperCase() + first.slice(1);
}

const num = (v) => (v == null || v === "" || isNaN(Number(v)) ? null : Number(v));

/* -------------------------------------------------------------- templating */

/**
 * Segment-specific opening. Keys are `segments.slug` prefixes; the observed
 * Jun/Jul 2026 emails used the same structure with the angle swapped.
 */
function segmentAngle(segmentSlug, row) {
  const metro = row.market_metro || row.city || null;
  const s = String(segmentSlug || "");
  if (s.includes("feeder")) {
    return metro
      ? `${metro} is one of the strongest feeder markets for destination weddings on 30A, and I'd love to explore a referral partnership with ${row.company_name}.`
      : `your couples travel to 30A for destination weddings, and I'd love to explore a referral partnership with ${row.company_name}.`;
  }
  if (s.includes("wedding")) {
    return `you plan weddings right here on 30A and the Emerald Coast, and I'd love to explore a referral partnership with ${row.company_name}.`;
  }
  if (s.includes("bachelorette")) {
    return `you put together bachelorette weekends on 30A, and our two-house setup is built for exactly that kind of group.`;
  }
  if (s.includes("photograph")) {
    return `you shoot families and weddings on 30A, and your clients often need somewhere to put a whole group.`;
  }
  if (s.includes("chef") || s.includes("catering")) {
    return `you cook for groups on 30A, and our guests are often looking for exactly that.`;
  }
  return `we host groups on 30A, and I'd love to explore a referral partnership with ${row.company_name}.`;
}

function ratingSentence(row) {
  const rating = num(row.rating);
  const reviews = num(row.reviews);
  if (!rating) return "";
  const tail = reviews && reviews >= 10 ? ` across ${reviews} reviews` : "";
  return `<p style="margin:0 0 14px 0">Your ${rating.toFixed(1)} rating${tail} speaks for itself — these are clearly weekends people remember.</p>`;
}

function renderEmail(env, { row, contact, partnerSlug, clickUrl, pixelUrl, discountPct }) {
  const hi = greetingName(contact);
  const hero = env.HERO_IMAGE_URL
    ? `<div style="margin:18px 0"><img src="${esc(env.HERO_IMAGE_URL)}" alt="Sun &amp; Moon at 30A — two adjacent cottages on Crystal Court" style="display:block;width:100%;max-width:560px;height:auto;border-radius:6px"><div style="font-size:11.5px;color:#64748b;margin-top:6px;font-style:italic">Golden Sun &amp; Blue Moon — two adjacent cottages, Crystal Court, Seagrove Beach</div></div>`
    : "";

  const p = 'style="margin:0 0 14px 0"';
  const html = `<!doctype html>
<html><body style="font-family:-apple-system,Helvetica,Arial,sans-serif;font-size:15px;line-height:1.55;color:#222;max-width:620px">
<p ${p}>Hi ${esc(hi || "there")},</p>
${hero}
<p ${p}>I'm Cristian Orihuela, owner of Sun &amp; Moon at 30A — two adjacent vacation rental houses on Crystal Court in Seagrove Beach. I'm reaching out because ${esc(segmentAngle(row.segment_slug, row))}</p>
${ratingSentence(row)}
<p ${p}>Our two houses sit next door to each other, sleep sixteen combined, and share a backyard between them. For a group coming down for a long weekend, that means one family can take one house and the other can take the second, with a shared outdoor space in between for rehearsal dinners and getting-ready mornings. It's one booking, one address, and one transport coordination — a much simpler setup than booking three or four separate rentals across 30A.</p>
<p ${p}>I've already created a private booking link for ${esc(row.company_name)}. Your clients see live availability and a ${discountPct}% discount, and stays booked through it are tracked automatically so you earn a revenue share — no paperwork, no logging: <a href="${esc(clickUrl)}" style="color:#2563eb;text-decoration:underline">book.sunandmoon30a.com</a></p>
<p ${p}>Have a look, and reply if you'd like me to walk you through how the revenue share works. Any couples who reach out to us directly would hear about your work first.</p>
<p ${p}>Thanks for considering it.</p>
<p ${p}>Best,<br>Cristian</p>
<p style="margin:0;color:#64748b;font-size:13px">experience@sunandmoon30a.com</p>
<img src="${esc(pixelUrl)}" width="1" height="1" alt="" style="display:none">
</body></html>`;

  const subject = `Sun & Moon at 30A — partnership idea for ${row.market_metro || row.city || "30A"} ${
    String(row.segment_slug || "").includes("bachelorette") ? "bachelorette groups" : "destination weddings"
  }`;

  return { subject: subject.slice(0, 180), html };
}

/* ----------------------------------------------------------- domain lookups */

async function ensurePartner(env, row, contactEmail) {
  const base = slugify(row.company_name);
  const existing = await sbSelect(
    env,
    `/rest/v1/rev_share_partners?slug=like.${encodeURIComponent(base + "*")}&select=slug,name,guest_discount_pct&limit=50`
  );
  const exact = existing.find((p) => p.name === row.company_name);
  if (exact) return { slug: exact.slug, discountPct: num(exact.guest_discount_pct) ?? DEFAULTS.GUEST_DISCOUNT_PCT };

  // Same base slug, different company -> append a counter, as the old sender did.
  let slug = base;
  if (existing.some((p) => p.slug === base)) {
    let n = 2;
    while (existing.some((p) => p.slug === `${base}-${n}`)) n++;
    slug = `${base}-${n}`;
  }
  const [created] = await sbInsert(env, "/rest/v1/rev_share_partners", {
    slug,
    name: row.company_name,
    email: contactEmail || null,
    guest_discount_pct: DEFAULTS.GUEST_DISCOUNT_PCT,
    rev_share_pct: DEFAULTS.REV_SHARE_PCT,
    active: true,
  });
  return { slug: created.slug, discountPct: num(created.guest_discount_pct) ?? DEFAULTS.GUEST_DISCOUNT_PCT };
}

async function ensureCampaign(env, segmentSlug) {
  const now = new Date();
  const month = now.toLocaleString("en-US", { month: "short", timeZone: "UTC" });
  const slug = `${segmentSlug || "general"}-email-${now.getUTCFullYear()}${String(now.getUTCMonth() + 1).padStart(2, "0")}`;
  const found = await sbSelect(env, `/rest/v1/campaigns?slug=eq.${encodeURIComponent(slug)}&select=id&limit=1`);
  if (found[0]) return { id: found[0].id, slug };
  const [created] = await sbInsert(env, "/rest/v1/campaigns", {
    project_id: env.PROJECT_ID,
    slug,
    name: `${segmentSlug || "general"} email — ${month} ${now.getUTCFullYear()}`,
    channel: "email",
    status: "active",
    metadata: { created_by: "sunmoon-outreach worker" },
  });
  return { id: created.id, slug };
}

/* ---------------------------------------------------------------- sending */

async function sendViaResend(env, { to, subject, html, replyTo }) {
  const res = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.RESEND_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      from: `Cristian at Sun & Moon 30A <${env.FROM_EMAIL}>`,
      to: [to],
      subject,
      html,
      ...(replyTo ? { reply_to: replyTo } : {}),
    }),
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`resend ${res.status} ${text}`);
  try {
    return JSON.parse(text).id || null;
  } catch {
    return null;
  }
}

/* ------------------------------------------------------------- the daily run */

async function pickRecipients(env, cap) {
  const ready = await sbSelect(
    env,
    `/rest/v1/v_outreach_ready?select=contact_id,email,first_name,company_id,company_name,city,state,rating,reviews,market_metro,market_role,segment_slug,segment_name,tier,relevance&limit=${cap * 6}`
  );
  const suppressed = new Set(
    (await sbSelect(env, `/rest/v1/outreach_suppression?select=email`)).map((r) =>
      String(r.email || "").toLowerCase()
    )
  );

  const picks = [];
  const seenCompany = new Set();
  for (const row of ready) {
    const email = String(row.email || "").toLowerCase();
    if (!email || suppressed.has(email)) continue;
    if (seenCompany.has(row.company_id)) continue; // one contact per company per wave
    seenCompany.add(row.company_id);
    picks.push(row);
    if (picks.length >= cap) break;
  }
  return picks;
}

async function sendOne(env, row, { dryRun }) {
  const contact = { first_name: row.first_name };
  const { slug: partnerSlug, discountPct } = await ensurePartner(env, row, row.email);
  const campaign = await ensureCampaign(env, row.segment_slug);

  const trackBase = (env.TRACK_BASE || DEFAULTS.TRACK_BASE).replace(/\/$/, "");
  const bookBase = (env.BOOK_BASE || DEFAULTS.BOOK_BASE).replace(/\/$/, "");
  const code = shortCode();

  const destination =
    `${bookBase}/?partner=${encodeURIComponent(partnerSlug)}` +
    `&utm_source=referral-os&utm_medium=email` +
    `&utm_campaign=${encodeURIComponent(campaign.slug)}&utm_content=${code}`;

  if (dryRun) {
    const { subject, html } = renderEmail(env, {
      row, contact, partnerSlug,
      clickUrl: `${trackBase}/c/${code}`,
      pixelUrl: `${trackBase}/o/DRY-RUN`,
      discountPct,
    });
    return { dryRun: true, to: row.email, company: row.company_name, partnerSlug, subject, destination, htmlBytes: html.length };
  }

  // The outreach id is baked into the open pixel, so the row must exist first.
  const [outreach] = await sbInsert(env, "/rest/v1/outreach", {
    project_id: env.PROJECT_ID,
    campaign_id: campaign.id,
    contact_id: row.contact_id,
    company_id: row.company_id,
    channel: "email",
    sequence_step: 1,
    status: "queued",
    metadata: { to_email: row.email, partner_slug: partnerSlug, sender: "sunmoon-outreach worker" },
  });

  try {
    await sbInsert(env, "/rest/v1/tracking_links", {
      short_code: code,
      destination_url: destination,
      project_id: env.PROJECT_ID,
      company_id: row.company_id,
      contact_id: row.contact_id,
      campaign_id: campaign.id,
      utm_source: "referral-os",
      utm_medium: "email",
      utm_campaign: campaign.slug,
      utm_content: code,
      is_active: true,
    });

    const { subject, html } = renderEmail(env, {
      row, contact, partnerSlug,
      clickUrl: `${trackBase}/c/${code}`,
      pixelUrl: `${trackBase}/o/${outreach.id}`,
      discountPct,
    });

    const providerId = await sendViaResend(env, {
      to: row.email,
      subject,
      html,
      replyTo: env.REPLY_TO || null,
    });

    await sbPatch(env, `/rest/v1/outreach?id=eq.${outreach.id}`, {
      status: "sent",
      sent_at: new Date().toISOString(),
      subject,
      body: html,
      provider_message_id: providerId,
      metadata: {
        to_email: row.email,
        partner_slug: partnerSlug,
        short_code: code,
        sender: "sunmoon-outreach worker",
      },
    });

    // Stop v_outreach_ready from handing us this company again.
    await sbPatch(
      env,
      `/rest/v1/company_segments?company_id=eq.${row.company_id}&status=eq.prospect`,
      { status: "contacted" }
    );

    return { ok: true, to: row.email, company: row.company_name, partnerSlug, outreachId: outreach.id };
  } catch (err) {
    await sbPatch(env, `/rest/v1/outreach?id=eq.${outreach.id}`, {
      status: "failed",
      metadata: {
        to_email: row.email,
        partner_slug: partnerSlug,
        sender: "sunmoon-outreach worker",
        error: String(err).slice(0, 500),
      },
    }).catch(() => {});
    return { ok: false, to: row.email, company: row.company_name, error: String(err).slice(0, 300) };
  }
}

/**
 * Has this sender already sent anything today (UTC)?
 *
 * The daily run can be driven from more than one place — a Cloudflare cron
 * trigger, the GitHub Actions schedule, or a manual POST /run. Without this
 * guard, two of them firing on the same day would send two batches. Pass
 * force=1 to override (e.g. a deliberate second wave).
 */
async function alreadySentToday(env) {
  const midnight = new Date();
  midnight.setUTCHours(0, 0, 0, 0);
  const rows = await sbSelect(
    env,
    `/rest/v1/outreach?status=eq.sent&sent_at=gte.${midnight.toISOString()}&select=id&limit=1`
  );
  return rows.length > 0;
}

async function runDailySend(env, { dryRunOverride, capOverride, force = false } = {}) {
  const started = new Date().toISOString();
  const cap = capOverride ?? num(env.DAILY_CAP) ?? DEFAULTS.DAILY_CAP;
  const dryRun = dryRunOverride ?? String(env.DRY_RUN ?? "true").toLowerCase() !== "false";

  if (!dryRun && !env.RESEND_API_KEY) {
    return { started, error: "RESEND_API_KEY not set — refusing to run live" };
  }

  if (!dryRun && !force && (await alreadySentToday(env))) {
    return { started, skipped: "already sent today", dryRun, cap, sent: 0 };
  }

  const picks = await pickRecipients(env, cap);
  const results = [];
  for (const row of picks) {
    try {
      results.push(await sendOne(env, row, { dryRun }));
    } catch (err) {
      results.push({ ok: false, to: row.email, company: row.company_name, error: String(err).slice(0, 300) });
    }
  }

  const summary = {
    started,
    finished: new Date().toISOString(),
    dryRun,
    cap,
    eligible: picks.length,
    sent: results.filter((r) => r.ok).length,
    failed: results.filter((r) => r.ok === false).length,
    results,
  };
  console.log("outreach run", JSON.stringify({ ...summary, results: undefined }));
  return summary;
}

/* ------------------------------------------------------------------ worker */

function json(status, body) {
  return new Response(JSON.stringify(body, null, 2), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}

function authed(env, url) {
  return env.ADMIN_TOKEN && url.searchParams.get("token") === env.ADMIN_TOKEN;
}

export default {
  async scheduled(_event, env, ctx) {
    ctx.waitUntil(
      runDailySend(env).catch((err) => console.error("scheduled run failed", err?.stack || err))
    );
  },

  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/health" || url.pathname === "/healthz") {
      return json(200, {
        ok: true,
        dryRun: String(env.DRY_RUN ?? "true").toLowerCase() !== "false",
        cap: num(env.DAILY_CAP) ?? DEFAULTS.DAILY_CAP,
        resendConfigured: !!env.RESEND_API_KEY,
        supabaseConfigured: !!(env.SUPABASE_URL && env.SUPABASE_SERVICE_KEY),
      });
    }

    if (url.pathname === "/preview") {
      if (!authed(env, url)) return json(404, { error: "not found" });
      const limit = num(url.searchParams.get("limit")) ?? 5;
      return json(200, await runDailySend(env, { dryRunOverride: true, capOverride: limit }));
    }

    if (url.pathname === "/run" && request.method === "POST") {
      if (!authed(env, url)) return json(404, { error: "not found" });
      const capOverride = num(url.searchParams.get("cap")) ?? undefined;
      const force = url.searchParams.get("force") === "1";
      return json(200, await runDailySend(env, { capOverride, force }));
    }

    return json(404, { error: "not found" });
  },
};
