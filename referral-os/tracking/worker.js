/**
 * Referral OS — Tracking Worker
 * ---------------------------------------------------------------
 * Deploys to Cloudflare Workers (free tier covers 100k req/day).
 *
 * Endpoints:
 *   GET  /r/:shortCode     — redirect with UTM, log link_click
 *   GET  /p/:linkId.png    — 1x1 pixel, log email_open
 *   GET  /e/:linkId        — log email_click (then redirect)
 *   POST /events           — JSON event ingestion (site visits, etc.)
 *   GET  /health           — uptime check
 *
 * Storage flow:
 *   Worker -> Cloudflare Queue -> Postgres ingest worker (cron)
 *   OR (simpler): Worker -> Supabase Edge Function -> Postgres
 *
 * For the v1 we POST directly to Supabase via PostgREST. It adds 50-100ms
 * to the redirect but stays well under perceptible. Move to Queue if
 * latency or volume becomes an issue.
 *
 * Env (set via `wrangler secret`):
 *   SUPABASE_URL                  https://<ref>.supabase.co
 *   SUPABASE_SERVICE_ROLE_KEY     server-only key with INSERT on tracking_events
 *   IP_SALT                       any random 32+ char string; rotate daily ideally
 *   FALLBACK_REDIRECT             where to send unknown short codes
 */

const PIXEL_GIF_BYTES = Uint8Array.from([
  0x47, 0x49, 0x46, 0x38, 0x39, 0x61, 0x01, 0x00, 0x01, 0x00, 0x80, 0x00,
  0x00, 0xff, 0xff, 0xff, 0x00, 0x00, 0x00, 0x21, 0xf9, 0x04, 0x01, 0x00,
  0x00, 0x00, 0x00, 0x2c, 0x00, 0x00, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00,
  0x00, 0x02, 0x02, 0x44, 0x01, 0x00, 0x3b,
]);

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;

    try {
      if (path === '/health')           return new Response('ok', { status: 200 });
      if (path.startsWith('/r/'))       return await handleShortLink(request, env, ctx, url);
      if (path.startsWith('/p/'))       return await handlePixel(request, env, ctx, url);
      if (path.startsWith('/e/'))       return await handleEmailClick(request, env, ctx, url);
      if (path === '/events' && request.method === 'POST') {
        return await handleEventPost(request, env, ctx);
      }
      return new Response('Not Found', { status: 404 });
    } catch (e) {
      console.log('worker error', e?.stack || e);
      return new Response('Internal', { status: 500 });
    }
  },
};

// ---------------------------------------------------------------
// /r/:shortCode  — main short-link redirector
// ---------------------------------------------------------------
async function handleShortLink(request, env, ctx, url) {
  const shortCode = url.pathname.replace('/r/', '');
  if (!shortCode) return new Response('Missing code', { status: 400 });

  // Look up destination + attribution context from Postgres.
  const linkResp = await supabase(env, 'GET',
    `/rest/v1/tracking_links?short_code=eq.${encodeURIComponent(shortCode)}&select=id,destination_url,project_id,company_id,contact_id,campaign_id,utm_source,utm_medium,utm_campaign,utm_content,utm_term&limit=1`
  );
  const links = await linkResp.json();
  if (!Array.isArray(links) || links.length === 0) {
    return Response.redirect(env.FALLBACK_REDIRECT || 'https://sunandmoon30a.com', 302);
  }
  const link = links[0];

  // Append UTMs to destination.
  const dest = new URL(link.destination_url);
  for (const k of ['utm_source','utm_medium','utm_campaign','utm_content','utm_term']) {
    if (link[k] && !dest.searchParams.has(k)) dest.searchParams.set(k, link[k]);
  }

  // Fire event after responding (waitUntil keeps it async).
  ctx.waitUntil(logEvent(env, {
    event_type:       'link_click',
    project_id:       link.project_id,
    company_id:       link.company_id,
    contact_id:       link.contact_id,
    tracking_link_id: link.id,
    campaign_id:      link.campaign_id,
    source:           link.utm_source || 'tracking_link',
    url:              dest.toString(),
    request,
  }));

  // Best-effort click_count++
  ctx.waitUntil(supabase(env, 'POST', '/rest/v1/rpc/increment_link_click', {
    body: JSON.stringify({ p_link_id: link.id }),
  }).catch(() => {}));

  return Response.redirect(dest.toString(), 302);
}

// ---------------------------------------------------------------
// /p/:linkId.png — 1x1 tracking pixel for email opens
// ---------------------------------------------------------------
async function handlePixel(request, env, ctx, url) {
  const m = url.pathname.match(/^\/p\/([0-9a-f-]+)\.(png|gif)$/i);
  if (!m) return new Response('not found', { status: 404 });
  const linkId = m[1];

  // Lookup minimal context off the tracking_links row.
  const r = await supabase(env, 'GET',
    `/rest/v1/tracking_links?id=eq.${linkId}&select=project_id,company_id,contact_id,campaign_id&limit=1`
  );
  const rows = await r.json();
  const link = Array.isArray(rows) && rows[0] ? rows[0] : {};

  ctx.waitUntil(logEvent(env, {
    event_type:       'email_open',
    project_id:       link.project_id,
    company_id:       link.company_id,
    contact_id:       link.contact_id,
    tracking_link_id: linkId,
    campaign_id:      link.campaign_id,
    source:           'email',
    request,
  }));

  return new Response(PIXEL_GIF_BYTES, {
    status: 200,
    headers: {
      'Content-Type': 'image/gif',
      'Cache-Control': 'no-store, no-cache, must-revalidate, max-age=0',
      'Pragma': 'no-cache',
    },
  });
}

// ---------------------------------------------------------------
// /e/:linkId — email click handler (logs then redirects via short link)
// ---------------------------------------------------------------
async function handleEmailClick(request, env, ctx, url) {
  const linkId = url.pathname.replace('/e/', '');
  const r = await supabase(env, 'GET',
    `/rest/v1/tracking_links?id=eq.${linkId}&select=short_code,destination_url,project_id,company_id,contact_id,campaign_id&limit=1`
  );
  const rows = await r.json();
  if (!rows || !rows[0]) return new Response('not found', { status: 404 });
  const link = rows[0];

  ctx.waitUntil(logEvent(env, {
    event_type:       'email_click',
    project_id:       link.project_id,
    company_id:       link.company_id,
    contact_id:       link.contact_id,
    tracking_link_id: linkId,
    campaign_id:      link.campaign_id,
    source:           'email',
    request,
  }));
  return Response.redirect(link.destination_url, 302);
}

// ---------------------------------------------------------------
// POST /events — JSON event ingestion
// Embed a snippet on the website that POSTs here on key actions
// (booking_inquiry, form_submit, etc.). Schema-validated server-side.
// ---------------------------------------------------------------
async function handleEventPost(request, env, ctx) {
  let body;
  try {
    body = await request.json();
  } catch {
    return new Response('bad json', { status: 400 });
  }
  if (!body.event_type || !body.project_id) {
    return new Response('missing event_type or project_id', { status: 400 });
  }
  await logEvent(env, { ...body, request });
  return new Response('ok', {
    headers: {
      'Content-Type': 'text/plain',
      'Access-Control-Allow-Origin': '*',
    },
  });
}

// ---------------------------------------------------------------
// Shared: insert into tracking_events
// ---------------------------------------------------------------
async function logEvent(env, ev) {
  const req = ev.request;
  const cf  = req?.cf || {};
  const ipHash = await hashIp(req?.headers?.get('cf-connecting-ip') || '', env.IP_SALT);
  const payload = {
    event_type:       ev.event_type,
    project_id:       ev.project_id,
    company_id:       ev.company_id      || null,
    contact_id:       ev.contact_id      || null,
    tracking_link_id: ev.tracking_link_id|| null,
    campaign_id:      ev.campaign_id     || null,
    source:           ev.source          || null,
    url:              ev.url             || req?.url,
    referrer:         req?.headers?.get('referer') || null,
    user_agent:       req?.headers?.get('user-agent') || null,
    ip_hash:          ipHash,
    country:          cf.country || null,
    city:             cf.city || null,
    metadata:         ev.metadata || {},
  };
  return supabase(env, 'POST', '/rest/v1/tracking_events', {
    headers: { 'Prefer': 'return=minimal' },
    body: JSON.stringify(payload),
  });
}

async function hashIp(ip, salt) {
  if (!ip) return null;
  const data = new TextEncoder().encode(`${ip}|${salt || ''}`);
  const digest = await crypto.subtle.digest('SHA-256', data);
  return Array.from(new Uint8Array(digest)).map(b => b.toString(16).padStart(2, '0')).join('');
}

async function supabase(env, method, path, opts = {}) {
  return fetch(`${env.SUPABASE_URL}${path}`, {
    method,
    headers: {
      'apikey':        env.SUPABASE_SERVICE_ROLE_KEY,
      'Authorization': `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`,
      'Content-Type':  'application/json',
      ...(opts.headers || {}),
    },
    body: opts.body,
  });
}
