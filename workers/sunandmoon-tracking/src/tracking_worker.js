var __defProp = Object.defineProperty;
var __name = (target, value) => __defProp(target, "name", { value, configurable: true });

// tracking_worker.js
var PIXEL = new Uint8Array([
  71, 73, 70, 56, 57, 97, 1, 0, 1, 0, 128, 0, 0, 255, 255, 255, 0, 0, 0, 33,
  249, 4, 1, 0, 0, 0, 0, 44, 0, 0, 0, 0, 1, 0, 1, 0, 0, 2, 2, 68, 1, 0, 59
]);
function hashIP(ip) {
  let h = 2166136261;
  for (let i = 0; i < ip.length; i++) {
    h ^= ip.charCodeAt(i);
    h = h * 16777619 >>> 0;
  }
  return h.toString(16);
}
__name(hashIP, "hashIP");
function isLikelyBot(ua) {
  if (!ua) return true;
  const lc = ua.toLowerCase();
  return lc.includes("googleimageproxy") || lc.includes("mail.google.com") || lc.includes("yahoomailproxy") || lc.includes("ymailproxy") || lc.includes("outlook.com") || lc.includes("mimecast") || lc.includes("proofpoint") || lc.includes("barracuda") || lc.includes("symantec");
}
__name(isLikelyBot, "isLikelyBot");
async function logEvent(env, body) {
  const url = `${env.SUPABASE_URL.replace(/\/$/, "")}/rest/v1/tracking_events`;
  await fetch(url, {
    method: "POST",
    headers: {
      "apikey": env.SUPABASE_SERVICE_KEY,
      "Authorization": `Bearer ${env.SUPABASE_SERVICE_KEY}`,
      "Content-Type": "application/json",
      "Prefer": "return=minimal"
    },
    body: JSON.stringify(body)
  });
}
__name(logEvent, "logEvent");
async function fetchOutreachContext(env, outreachId) {
  const url = `${env.SUPABASE_URL.replace(/\/$/, "")}/rest/v1/outreach?id=eq.${outreachId}&select=contact_id,company_id,campaign_id&limit=1`;
  const r = await fetch(url, {
    headers: {
      "apikey": env.SUPABASE_SERVICE_KEY,
      "Authorization": `Bearer ${env.SUPABASE_SERVICE_KEY}`,
      "Accept": "application/json"
    }
  });
  if (!r.ok) return null;
  const rows = await r.json();
  return rows[0] || null;
}
__name(fetchOutreachContext, "fetchOutreachContext");
async function fetchTrackingLink(env, shortCode) {
  const url = `${env.SUPABASE_URL.replace(/\/$/, "")}/rest/v1/tracking_links?short_code=eq.${encodeURIComponent(shortCode)}&is_active=eq.true&select=id,destination_url,contact_id,company_id,campaign_id,click_count&limit=1`;
  const r = await fetch(url, {
    headers: {
      "apikey": env.SUPABASE_SERVICE_KEY,
      "Authorization": `Bearer ${env.SUPABASE_SERVICE_KEY}`,
      "Accept": "application/json"
    }
  });
  if (!r.ok) return null;
  const rows = await r.json();
  return rows[0] || null;
}
__name(fetchTrackingLink, "fetchTrackingLink");
async function incrementClickCount(env, linkId, currentCount) {
  const url = `${env.SUPABASE_URL.replace(/\/$/, "")}/rest/v1/tracking_links?id=eq.${linkId}`;
  await fetch(url, {
    method: "PATCH",
    headers: {
      "apikey": env.SUPABASE_SERVICE_KEY,
      "Authorization": `Bearer ${env.SUPABASE_SERVICE_KEY}`,
      "Content-Type": "application/json",
      "Prefer": "return=minimal"
    },
    body: JSON.stringify({
      click_count: currentCount + 1,
      last_clicked_at: (/* @__PURE__ */ new Date()).toISOString()
    })
  });
}
__name(incrementClickCount, "incrementClickCount");
async function handleOpenPixel(request, env, ctx, outreachId) {
  const ip = request.headers.get("CF-Connecting-IP") || "0.0.0.0";
  const ua = request.headers.get("User-Agent") || "";
  const cf = request.cf || {};
  const bot = isLikelyBot(ua);
  ctx.waitUntil((async () => {
    try {
      const ctxRow = await fetchOutreachContext(env, outreachId);
      await logEvent(env, {
        project_id: env.PROJECT_ID,
        event_type: "email_open",
        contact_id: ctxRow?.contact_id || null,
        company_id: ctxRow?.company_id || null,
        campaign_id: ctxRow?.campaign_id || null,
        source: "tracking_worker",
        user_agent: ua.substring(0, 500),
        ip_hash: hashIP(ip),
        country: cf.country || null,
        city: cf.city || null,
        metadata: {
          outreach_id: outreachId,
          likely_prefetch: bot
        }
      });
    } catch (_e) {
    }
  })());
  return new Response(PIXEL, {
    headers: {
      "Content-Type": "image/gif",
      "Cache-Control": "no-cache, no-store, must-revalidate",
      "Pragma": "no-cache",
      "Expires": "0"
    }
  });
}
__name(handleOpenPixel, "handleOpenPixel");
async function handleClick(request, env, ctx, shortCode) {
  const link = await fetchTrackingLink(env, shortCode);
  if (!link) {
    return new Response("Link not found.", { status: 404 });
  }
  const ip = request.headers.get("CF-Connecting-IP") || "0.0.0.0";
  const ua = request.headers.get("User-Agent") || "";
  const cf = request.cf || {};
  ctx.waitUntil((async () => {
    try {
      await logEvent(env, {
        project_id: env.PROJECT_ID,
        event_type: "email_click",
        contact_id: link.contact_id || null,
        company_id: link.company_id || null,
        campaign_id: link.campaign_id || null,
        tracking_link_id: link.id,
        source: "tracking_worker",
        url: link.destination_url,
        user_agent: ua.substring(0, 500),
        ip_hash: hashIP(ip),
        country: cf.country || null,
        city: cf.city || null
      });
      await incrementClickCount(env, link.id, link.click_count || 0);
    } catch (_e) {
    }
  })());
  return Response.redirect(link.destination_url, 302);
}
__name(handleClick, "handleClick");
var CONTACT_ALLOWED_ORIGINS = [
  "https://sunandmoon30a.com",
  "https://www.sunandmoon30a.com",
  "https://sun-and-moon-30a.pages.dev"
];
function contactCorsHeaders(request) {
  const origin = request.headers.get("Origin") || "";
  const allowed = CONTACT_ALLOWED_ORIGINS.includes(origin) ? origin : CONTACT_ALLOWED_ORIGINS[0];
  return {
    "Access-Control-Allow-Origin": allowed,
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "86400"
  };
}
__name(contactCorsHeaders, "contactCorsHeaders");
function contactJson(request, status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...contactCorsHeaders(request) }
  });
}
__name(contactJson, "contactJson");
var VALID_PROPERTY_INTEREST = ["golden-sun", "blue-moon", "both", "not-sure"];
async function handleContact(request, env, ctx) {
  let body;
  try {
    body = await request.json();
  } catch (_e) {
    return contactJson(request, 400, { ok: false, error: "invalid JSON" });
  }
  if (body.website) {
    return contactJson(request, 200, { ok: true });
  }
  const name = String(body.name || "").trim().slice(0, 200);
  const email = String(body.email || "").trim().slice(0, 320);
  const phone = String(body.phone || "").trim().slice(0, 40);
  const message = String(body.message || "").trim().slice(0, 5e3);
  const dates = String(body.dates || "").trim().slice(0, 200);
  const interest = VALID_PROPERTY_INTEREST.includes(body.property_interest) ? body.property_interest : null;
  if (!name || !message || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    return contactJson(request, 400, {
      ok: false,
      error: "name, a valid email, and a message are required"
    });
  }
  const insertResp = await fetch(
    `${env.SUPABASE_URL.replace(/\/$/, "")}/rest/v1/inquiries`,
    {
      method: "POST",
      headers: {
        "apikey": env.SUPABASE_SERVICE_KEY,
        "Authorization": `Bearer ${env.SUPABASE_SERVICE_KEY}`,
        "Content-Type": "application/json",
        "Prefer": "return=representation"
      },
      body: JSON.stringify({
        project_id: env.PROJECT_ID,
        channel: "website_form",
        sender_name: name,
        sender_email: email,
        sender_phone: phone || null,
        subject: "Website contact form",
        message,
        property_interest: interest,
        metadata: { dates_freetext: dates || null }
      })
    }
  );
  if (!insertResp.ok) {
    console.log("inquiry insert failed", insertResp.status, await insertResp.text());
    return contactJson(request, 500, { ok: false, error: "could not save inquiry" });
  }
  const [inquiry] = await insertResp.json();
  const ip = request.headers.get("CF-Connecting-IP") || "0.0.0.0";
  const ua = request.headers.get("User-Agent") || "";
  const cf = request.cf || {};
  ctx.waitUntil(logEvent(env, {
    project_id: env.PROJECT_ID,
    event_type: "form_submit",
    source: "website",
    url: request.headers.get("Referer") || "https://sunandmoon30a.com/#contact",
    user_agent: ua.substring(0, 500),
    ip_hash: hashIP(ip),
    country: cf.country || null,
    city: cf.city || null,
    metadata: {
      inquiry_id: inquiry.id,
      property_interest: interest
    }
  }).catch(() => {
  }));
  if (env.SEND_EMAIL) {
    ctx.waitUntil((async () => {
      try {
        const lines = [
          `Name:     ${name}`,
          `Email:    ${email}`,
          phone ? `Phone:    ${phone}` : null,
          interest ? `Cottage:  ${interest}` : null,
          dates ? `Dates:    ${dates}` : null,
          "",
          message
        ].filter((l) => l !== null);
        await env.SEND_EMAIL.send({
          to: "experience@sunandmoon30a.com",
          from: { email: "website@sunandmoon30a.com", name: "Sun & Moon Website" },
          replyTo: email,
          subject: `New inquiry from ${name} (website form)`,
          text: lines.join("\n"),
          html: `<pre style="font-family:Georgia,serif;font-size:15px;white-space:pre-wrap">${lines.join("\n").replace(/&/g, "&amp;").replace(/</g, "&lt;")}</pre>`
        });
        await fetch(
          `${env.SUPABASE_URL.replace(/\/$/, "")}/rest/v1/inquiries?id=eq.${inquiry.id}`,
          {
            method: "PATCH",
            headers: {
              "apikey": env.SUPABASE_SERVICE_KEY,
              "Authorization": `Bearer ${env.SUPABASE_SERVICE_KEY}`,
              "Content-Type": "application/json",
              "Prefer": "return=minimal"
            },
            body: JSON.stringify({ notified_at: (/* @__PURE__ */ new Date()).toISOString() })
          }
        );
      } catch (e) {
        console.log("contact notification email failed", e?.stack || e);
      }
    })());
  }
  return contactJson(request, 200, { ok: true, id: inquiry.id });
}
__name(handleContact, "handleContact");
async function sbGet(env, path, headers = {}) {
  const r = await fetch(`${env.SUPABASE_URL.replace(/\/$/, "")}${path}`, {
    headers: {
      "apikey": env.SUPABASE_SERVICE_KEY,
      "Authorization": `Bearer ${env.SUPABASE_SERVICE_KEY}`,
      "Accept": "application/json",
      ...headers
    }
  });
  return r;
}
__name(sbGet, "sbGet");
async function sbCount(env, pathWithFilters) {
  const r = await sbGet(
    env,
    pathWithFilters,
    { "Prefer": "count=exact", "Range": "0-0" }
  );
  const cr = r.headers.get("content-range") || "/0";
  return parseInt(cr.split("/")[1], 10) || 0;
}
__name(sbCount, "sbCount");
async function sbAll(env, pathWithFilters, pageSize = 1e3, maxPages = 5) {
  const rows = [];
  for (let p = 0; p < maxPages; p++) {
    const from = p * pageSize;
    const r = await sbGet(
      env,
      pathWithFilters,
      { "Range": `${from}-${from + pageSize - 1}` }
    );
    if (!r.ok) break;
    const page = await r.json();
    rows.push(...page);
    if (page.length < pageSize) break;
  }
  return rows;
}
__name(sbAll, "sbAll");
function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
__name(esc, "esc");
async function handleOps(env) {
  const pid = env.PROJECT_ID;
  const since = new Date(Date.now() - 30 * 864e5).toISOString();
  const [events, inquiries, heat, queuedRows, sentCount, companies] = await Promise.all([
    sbAll(env, `/rest/v1/tracking_events?project_id=eq.${pid}&occurred_at=gte.${since}&select=event_type,occurred_at&order=occurred_at.asc`),
    sbAll(env, `/rest/v1/inquiries?project_id=eq.${pid}&select=created_at,channel,status,sender_name,sender_email,property_interest,message&order=created_at.desc`, 50, 1),
    sbGet(env, `/rest/v1/v_partner_heat?select=company_name,segment_slug,partner_status,events_7d,events_30d,opens_30d,clicks_30d,last_event_at&order=events_30d.desc.nullslast&limit=12`).then((r) => r.ok ? r.json() : []),
    sbAll(env, `/rest/v1/outreach?project_id=eq.${pid}&status=eq.queued&select=metadata`),
    sbCount(env, `/rest/v1/outreach?project_id=eq.${pid}&status=eq.sent&select=id`),
    sbCount(env, `/rest/v1/companies?select=id`)
  ]);
  const byType = {};
  for (const e of events) byType[e.event_type] = (byType[e.event_type] || 0) + 1;
  const realInquiries = inquiries.filter((i) => !["spam", "not_inquiry"].includes(i.status));
  const TYPES = ["email_open", "email_click", "form_submit", "booking_inquiry", "link_click", "site_visit"];
  const COLORS = {
    email_open: "#E8A44A",
    email_click: "#3A6080",
    form_submit: "#8BABC4",
    booking_inquiry: "#9B6B20",
    link_click: "#7BB8C8",
    site_visit: "#C9B89A"
  };
  const days = [];
  for (let d = 29; d >= 0; d--) {
    const day = new Date(Date.now() - d * 864e5).toISOString().slice(0, 10);
    days.push({ day, counts: Object.fromEntries(TYPES.map((t) => [t, 0])) });
  }
  const dayIndex = Object.fromEntries(days.map((d, i) => [d.day, i]));
  for (const e of events) {
    const day = e.occurred_at.slice(0, 10);
    if (day in dayIndex && e.event_type in days[dayIndex[day]].counts) {
      days[dayIndex[day]].counts[e.event_type]++;
    }
  }
  const maxDay = Math.max(1, ...days.map((d) => Object.values(d.counts).reduce((a, b) => a + b, 0)));
  const bars = days.map((d) => {
    const total = Object.values(d.counts).reduce((a, b) => a + b, 0);
    const segs = TYPES.filter((t) => d.counts[t] > 0).map(
      (t) => `<i style="height:${(d.counts[t] / maxDay * 130).toFixed(1)}px;background:${COLORS[t]}" title="${d.day} ${t}: ${d.counts[t]}"></i>`
    ).join("");
    return `<div class="bar" title="${d.day}: ${total}">${segs}</div>`;
  }).join("");
  const legend = TYPES.filter((t) => byType[t]).map((t) => `<span><b style="background:${COLORS[t]}"></b>${t} (${byType[t]})</span>`).join("");
  const waves = {};
  for (const row of queuedRows) {
    const d = ((row.metadata || {}).scheduled_for || "").slice(0, 10) || "unscheduled";
    waves[d] = (waves[d] || 0) + 1;
  }
  const waveRows = Object.entries(waves).sort().map(([d, n]) => `<tr><td>${esc(d)}</td><td class="num">${n}</td></tr>`).join("");
  const inqRows = inquiries.slice(0, 20).map((i) => `
    <tr>
      <td>${esc(i.created_at.slice(0, 16).replace("T", " "))}</td>
      <td>${i.channel === "website_form" ? "\u{1F310} form" : "✉️ email"}</td>
      <td>${esc(i.status)}</td>
      <td>${esc(i.sender_name || "—")}<br><small>${esc(i.sender_email || "")}</small></td>
      <td>${esc(i.property_interest || "—")}</td>
      <td class="msg">${esc((i.message || "").slice(0, 110))}</td>
    </tr>`).join("");
  const heatRows = heat.map((h) => `
    <tr>
      <td>${esc(h.company_name)}</td>
      <td>${esc(h.segment_slug || "—")}</td>
      <td class="num">${h.events_7d ?? 0}</td>
      <td class="num">${h.events_30d ?? 0}</td>
      <td class="num">${h.opens_30d ?? 0}</td>
      <td class="num">${h.clicks_30d ?? 0}</td>
      <td>${h.last_event_at ? esc(h.last_event_at.slice(0, 10)) : "—"}</td>
    </tr>`).join("");
  const kpi = /* @__PURE__ */ __name((label, value) => `<div class="kpi"><div class="v">${value}</div><div class="l">${label}</div></div>`, "kpi");
  const html = `<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<meta http-equiv="refresh" content="300">
<title>Sun &amp; Moon \xB7 Ops</title>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family: Georgia, serif; background:#F5EFE4; color:#2C2318; padding:28px 18px 60px; }
  .wrap { max-width:1100px; margin:0 auto; }
  h1 { font-weight:400; font-size:26px; margin-bottom:2px; }
  h1 em { color:#9B6B20; font-style:normal; }
  .sub { font-family:-apple-system,sans-serif; font-size:11px; letter-spacing:.18em;
         text-transform:uppercase; color:#7A6A58; margin-bottom:26px; }
  h2 { font-weight:400; font-size:18px; margin:34px 0 12px; color:#3B2F22; }
  .kpis { display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:10px; }
  .kpi { background:#FDFCFA; border:1px solid rgba(139,111,82,.15); border-radius:4px; padding:14px; }
  .kpi .v { font-size:26px; }
  .kpi .l { font-family:-apple-system,sans-serif; font-size:10px; letter-spacing:.14em;
            text-transform:uppercase; color:#7A6A58; margin-top:4px; }
  .chart { display:flex; align-items:flex-end; gap:2px; height:140px; background:#FDFCFA;
           border:1px solid rgba(139,111,82,.15); border-radius:4px; padding:10px; }
  .bar { flex:1; display:flex; flex-direction:column-reverse; min-width:4px; }
  .bar i { display:block; width:100%; }
  .legend { font-family:-apple-system,sans-serif; font-size:11px; color:#7A6A58; margin-top:8px;
            display:flex; gap:14px; flex-wrap:wrap; }
  .legend b { display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:5px; vertical-align:-1px; }
  .tablewrap { overflow-x:auto; background:#FDFCFA; border:1px solid rgba(139,111,82,.15); border-radius:4px; }
  table { border-collapse:collapse; width:100%; font-family:-apple-system,sans-serif; font-size:12.5px; }
  th { text-align:left; font-size:10px; letter-spacing:.12em; text-transform:uppercase; color:#7A6A58;
       padding:10px 12px; border-bottom:1px solid rgba(139,111,82,.2); white-space:nowrap; }
  td { padding:9px 12px; border-bottom:1px solid rgba(139,111,82,.08); vertical-align:top; }
  td.num { text-align:right; font-variant-numeric:tabular-nums; }
  td.msg { color:#7A6A58; max-width:320px; }
  small { color:#7A6A58; }
  .foot { font-family:-apple-system,sans-serif; font-size:11px; color:#7A6A58; margin-top:34px; }
</style></head><body><div class="wrap">
  <h1>Sun <em>&amp;</em> Moon — Ops</h1>
  <div class="sub">Last 30 days \xB7 auto-refreshes every 5 min \xB7 read-only</div>

  <div class="kpis">
    ${kpi("Email opens", byType.email_open || 0)}
    ${kpi("Email clicks", byType.email_click || 0)}
    ${kpi("Inquiries", realInquiries.length)}
    ${kpi("Form submits", byType.form_submit || 0)}
    ${kpi("Outreach sent (all time)", sentCount)}
    ${kpi("Outreach queued", queuedRows.length)}
    ${kpi("Companies", companies)}
  </div>

  <h2>Engagement timeline</h2>
  <div class="chart">${bars}</div>
  <div class="legend">${legend || "<span>No events in window</span>"}</div>

  <h2>Inquiries — contact form &amp; inbox</h2>
  <div class="tablewrap"><table>
    <tr><th>When (UTC)</th><th>Via</th><th>Status</th><th>Who</th><th>Cottage</th><th>Message</th></tr>
    ${inqRows || '<tr><td colspan="6">No inquiries yet.</td></tr>'}
  </table></div>

  <h2>Partner heat — who's paying attention</h2>
  <div class="tablewrap"><table>
    <tr><th>Company</th><th>Segment</th><th>7d</th><th>30d</th><th>Opens</th><th>Clicks</th><th>Last seen</th></tr>
    ${heatRows || '<tr><td colspan="7">No partner activity yet.</td></tr>'}
  </table></div>

  <h2>Upcoming outreach waves</h2>
  <div class="tablewrap"><table>
    <tr><th>Scheduled day</th><th>Emails queued</th></tr>
    ${waveRows || '<tr><td colspan="2">Queue is empty.</td></tr>'}
  </table></div>

  <div class="foot">Generated ${(/* @__PURE__ */ new Date()).toISOString().slice(0, 16).replace("T", " ")} UTC \xB7
    full analytics in the local Streamlit dashboard (http://localhost:8501)</div>
</div></body></html>`;
  return new Response(html, {
    headers: {
      "Content-Type": "text/html; charset=utf-8",
      "Cache-Control": "no-store",
      "X-Robots-Tag": "noindex, nofollow",
      "Referrer-Policy": "no-referrer"
    }
  });
}
__name(handleOps, "handleOps");
var tracking_worker_default = {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;
    if (path === "/healthz") {
      return new Response("ok", { headers: { "Content-Type": "text/plain" } });
    }
    if (path.startsWith("/ops/")) {
      const token = path.slice(5).replace(/\/$/, "");
      if (env.OPS_TOKEN && token === env.OPS_TOKEN && request.method === "GET") {
        try {
          return await handleOps(env);
        } catch (e) {
          console.log("ops dashboard error", e?.stack || e);
          return new Response("Internal", { status: 500 });
        }
      }
      return new Response("Not found", { status: 404 });
    }
    if (path === "/contact") {
      if (request.method === "OPTIONS") {
        return new Response(null, { status: 204, headers: contactCorsHeaders(request) });
      }
      if (request.method === "POST") {
        return handleContact(request, env, ctx);
      }
      return new Response("Method not allowed", { status: 405 });
    }
    if (path.startsWith("/o/")) {
      let id = path.slice(3);
      if (id.endsWith(".png") || id.endsWith(".gif")) id = id.slice(0, -4);
      if (!id) return new Response("Bad request", { status: 400 });
      return handleOpenPixel(request, env, ctx, id);
    }
    if (path.startsWith("/c/")) {
      const code = path.slice(3);
      if (!code) return new Response("Bad request", { status: 400 });
      return handleClick(request, env, ctx, code);
    }
    return new Response("Not found", { status: 404 });
  }
};
export {
  tracking_worker_default as default
};
//# sourceMappingURL=tracking_worker.js.map
