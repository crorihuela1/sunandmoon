# Vacay ↔ Sun & Moon direct-booking integration — spec & ask

For the Nick & Mindy conversation. Two directions:

- **Vacay → us (inbound):** they push availability/rate changes to our
  webhook so our direct site never sells stale data. Built & waiting.
- **Us → Vacay (outbound / Phase 2):** we push a confirmed direct booking
  back so Vacay blocks the dates on their side. This is the #1 item — until
  it's automated we rely on the Phase-1 manual notification (below), which
  is a double-booking risk if a same-day OTA booking lands in the gap.

Our system already treats **Vacay as the single source of truth** and
validates against live Vacay data twice daily regardless — so this
integration strengthens real-time sync; it doesn't replace our safety net.

---

## 1. Inbound webhook (Vacay → us) — READY NOW

**Endpoint:** `POST https://book.sunandmoon30a.com/webhook/reservation`
(live once we deploy; testable now against a staging URL we can provide)

**Auth — either works, HMAC preferred:**
- Shared secret header: `x-webhook-secret: <secret we exchange>`, **or**
- HMAC-SHA256: `x-vacay-signature: sha256=<hex>` over the raw request body,
  keyed with a signing secret we exchange. (We verify signatures already.)

**Body (JSON):**
```json
{
  "property": "golden-sun | blue-moon | full-property | <Hostaway listing id>",
  "start":  "2026-11-20",          // check-in  (YYYY-MM-DD)
  "end":    "2026-11-23",          // check-out (exclusive)
  "action": "booked | cancelled",
  "ref":    "<Vacay/Hostaway reservation id>"   // for idempotent upsert
}
```

**Behavior:** we block (or release) those nights on our calendar instantly,
write an audit row, and record receipt so our health page shows the webhook
is live. Idempotent on `(property, ref)` — safe to retry/redeliver.

**Response:** `200 {"ok":true,...}` on success; `401` bad auth; `400` bad
payload. Please retry on non-2xx.

**Questions for Nick:**
1. Which auth mechanism can Hostaway/Vacay send — shared header or HMAC?
2. What's the payload shape on your side? We'll map it — the above is our
   default; we can conform to yours instead.
3. Property identifier: listing id (503319 / 232268 / 507559) or a slug?
4. Do you emit both `booked` and `cancelled`? Rate/fee changes too, or
   availability only?

---

## 2. Outbound block-back (us → Vacay) — THE ASK (Phase 2, #1 priority)

When a guest books direct on our site, we must block those dates on Vacay's
side automatically or we risk a double-booking. What we need from you:

**Preferred:** an API endpoint (or Hostaway capability) we can call to
create a **blocked/owner-hold** on a listing for a date range — even a
minimal "block these nights" call is enough. We'll send property + dates +
our confirmation code.

**If no push API exists yet:** we'll publish a signed **iCal feed** per
property that Vacay/Hostaway imports on your side:
- `https://book.sunandmoon30a.com/ical/golden-sun.ics`
- `https://book.sunandmoon30a.com/ical/blue-moon.ics`
(We can stand these up quickly — flagged as the fallback. iCal import lag on
your side is the gap we'd need to discuss.)

**Question for Nick:** does Hostaway expose a calendar-block/owner-hold API
to a connected owner, or is iCal import the path? That answer decides
Phase 2.

---

## 3. Phase-1 stopgap (LIVE now, until Phase 2 lands)

On every confirmed direct booking we automatically send Mindy a **text +
email** with property, check-in/out, guest name, and confirmation # so she
can block the dates on Vacay manually. Owner is CC'd on every one.

- Enable by setting `VACAY_CONTACT_EMAIL` and `VACAY_CONTACT_PHONE`
  (currently blank = off, for testing).
- This is a manual stopgap with human latency — hence Phase 2 is the
  priority.

---

## Current status

| Piece | Status |
|---|---|
| Inbound webhook endpoint + auth (both methods) | ✅ built, tested |
| Webhook health / "is it live yet" signal | ✅ `/health` shows last receipt |
| Twice-daily full validation vs live Vacay | ✅ built, tested (auto-corrects drift) |
| Phase-1 Mindy notification (email now, SMS when Twilio lands) | ✅ built, tested (silent until her contact set) |
| Phase-2 outbound block-back | ⛔ needs Nick's answer on Hostaway block API vs iCal |
