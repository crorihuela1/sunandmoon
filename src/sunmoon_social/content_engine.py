"""Generate the methodical daily content queue.

Each day the engine:
  1. reads live availability (open windows, scored) and local 30A events,
  2. picks today's pillar and builds 1–2 *briefs* (structured: which dates,
     which event, which photo),
  3. asks Claude to write per-platform captions from the briefs (falls back
     to template copy when no API key is present),
  4. fans the briefs out to every platform that is `active` and due today.

One queue file per day: queue/YYYY-MM-DD.json. Publishers consume that queue.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from .availability import OpenWindow, PropertyEvent, open_windows, promotable_stays, upcoming_events
from .config import QUEUE_DIR, active_platforms, load_apis, load_brand
from .copywriter import write_captions


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #

def _fmt_window(w: OpenWindow) -> str:
    last = w.end - timedelta(days=1)
    if w.start == last:
        return f"{w.start.strftime('%a %b %-d')} (1 night)"
    end_fmt = "%a %b %-d" if w.start.month != last.month else "%a %-d"
    return f"{w.start.strftime('%a %b %-d')} – {last.strftime(end_fmt)} ({w.nights} nights)"


def _fmt_event(ev: PropertyEvent) -> str:
    if ev.recurring:
        return f"{ev.title} (every {ev.recurring})"
    if ev.end and ev.end.date() != ev.start.date():
        return f"{ev.title}, {ev.start.strftime('%b %-d')}–{ev.end.strftime('%-d')}"
    return f"{ev.title}, {ev.start.strftime('%a %b %-d')}"


def _hashtags(brand: dict, n_rotating: int = 4, seed: int = 0) -> str:
    tags = list(brand.get("hashtags", {}).get("always", []))
    rot = brand.get("hashtags", {}).get("rotating", [])
    if rot:
        tags += [rot[(seed + i) % len(rot)] for i in range(min(n_rotating, len(rot)))]
    return " ".join(tags)


def _rate_line(brand: dict, unit_key: str) -> str:
    rates = brand.get("rates", {}) or {}
    rate = rates.get(unit_key)
    parts = []
    if rate:
        parts.append(f"From ${rate}/night.")
    if rates.get("promo_note"):
        parts.append(rates["promo_note"])
    return " ".join(parts)


def _unit_meta(brand: dict, key: str) -> dict:
    for u in brand.get("brand", {}).get("units", []):
        if u.get("key") == key:
            return u
    return {"key": key, "label": key.title(), "angle": ""}


def _media(brand: dict, pool: str, seed: int) -> str:
    media = brand.get("media", {}) or {}
    files = media.get(pool) or media.get("events") or []
    if not files:
        return ""
    return media.get("base_url", "").rstrip("/") + "/" + files[seed % len(files)].lstrip("/")


# --------------------------------------------------------------------------- #
# Brief builders (structured data + template fallback copy)
# --------------------------------------------------------------------------- #

def _availability_brief(brand: dict, w: OpenWindow, seed: int, event: PropertyEvent | None = None) -> dict:
    b = brand["brand"]
    unit = _unit_meta(brand, w.unit)
    dates = _fmt_window(w)
    rate = _rate_line(brand, w.unit)
    hook = f"{unit['label']} is open {dates}"
    if event:
        hook += f" — the same weekend as {_fmt_event(event)}"
    angle = unit.get("angle", "")
    angle = angle[:1].upper() + angle[1:] + "." if angle else ""
    ig = (f"{hook}.\n\n{angle}"
          + (f"\n\n{rate}" if rate else "")
          + "\n\nDates like these don't sit long. Link in bio to book.")
    fb = f"{hook}. {angle} {rate} Book direct at {b['booking_url']}".replace("  ", " ")
    return {
        "id": f"avail-{w.unit}-{w.start.isoformat()}",
        "pillar": "availability_spotlight",
        "unit": w.unit,
        "unit_label": unit["label"],
        "unit_angle": unit.get("angle", ""),
        "open_dates": dates,
        "window": w.to_dict(),
        "event": event.to_dict() if event else None,
        "event_summary": _fmt_event(event) if event else None,
        "rate": rate or None,
        "media_url": _media(brand, w.unit, seed),
        "copy": {"instagram": ig, "facebook": fb},
        "hashtags": _hashtags(brand, seed=seed),
    }


def _event_brief(brand: dict, ev: PropertyEvent, seed: int, window: OpenWindow | None = None) -> dict:
    b = brand["brand"]
    when = _fmt_event(ev)
    days_out = (ev.start.date() - date.today()).days
    lead = f"{days_out} days out — " if 0 < days_out <= 7 and not ev.recurring else ""
    where = f" · {ev.location}" if ev.location else ""
    stay = f"\n\n{_unit_meta(brand, window.unit)['label']} is open {_fmt_window(window)}." if window else ""
    cta_ig = "Stay the weekend, skip the drive home. Link in bio." if window else "Save the date — and follow along for the next open weekend."
    cta_fb = f"Book direct: {b['booking_url']}" if window else f"We're booked that weekend, but the calendar is always live at {b['booking_url']}"
    ig = f"{lead}{when}{where}.\n\n{ev.description[:220].rstrip()}{stay}\n\n{cta_ig}"
    fb = f"{lead}{when}{where}. {ev.description[:220].rstrip()}{stay.strip()} {cta_fb}"
    return {
        "id": f"event-{seed}-{ev.start.date().isoformat()}-{ev.title[:24].lower().replace(' ', '-')}",
        "pillar": "local_events",
        "event": ev.to_dict(),
        "event_summary": when,
        "unit": window.unit if window else None,
        "unit_label": _unit_meta(brand, window.unit)["label"] if window else None,
        "open_dates": _fmt_window(window) if window else None,
        "window": window.to_dict() if window else None,
        "media_url": _media(brand, window.unit if window else "events", seed),
        "copy": {"instagram": ig, "facebook": fb},
        "hashtags": _hashtags(brand, seed=seed),
    }


def _evergreen_brief(brand: dict, pillar: dict, seed: int) -> dict:
    prompts = {
        "local_guide": "Share one nearby thing guests ask about (a trail, a table, a beach access) and tie it back to staying at the cottages.",
        "behind_the_scenes": "Show the ritual: how the cottages get ready for guests — one detail, one photo, one sentence of why it matters.",
        "guest_love": "Repost a recent review or guest photo (with permission). Let their words carry it; add only a thank-you.",
    }
    return {
        "id": f"evergreen-{pillar['key']}-{seed}",
        "pillar": pillar["key"],
        "prompt": prompts.get(pillar["key"], pillar.get("description", "")),
        "media_url": _media(brand, "events", seed),
        "copy": {"instagram": prompts.get(pillar["key"], ""), "facebook": prompts.get(pillar["key"], "")},
        "hashtags": _hashtags(brand, seed=seed),
        "needs_human_media": True,
    }


# --------------------------------------------------------------------------- #
# Selection logic
# --------------------------------------------------------------------------- #

def _overlaps(w: OpenWindow, ev: PropertyEvent) -> bool:
    if ev.recurring:
        return False
    ev_start, ev_end = ev.start.date(), (ev.end or ev.start).date()
    # A stay "covers" an event if at least one open night falls on an event day
    # or the night before it.
    return w.start <= ev_end and (w.end - timedelta(days=1)) >= ev_start - timedelta(days=1)


def _pick_stays(stays: list[OpenWindow], seed: int = 0, n: int = 2, pool: int = 4) -> list[OpenWindow]:
    """Top-scored stays: never two posts about the same weekend, prefer one
    per cottage, rotate which of the top few leads (so consecutive days don't
    repeat), and upgrade to the whole property when both cottages share the
    exact dates."""
    distinct: list[OpenWindow] = []
    for st in stays:
        if st.unit == "full":
            continue
        if any(abs((st.start - p.start).days) < 3 for p in distinct):
            continue
        if distinct and st.unit == distinct[-1].unit:
            other = next((o for o in stays if o.unit not in ("full", st.unit)
                          and not any(abs((o.start - p.start).days) < 3 for p in distinct)), None)
            if other and other.score >= st.score - 3:
                st = other
        distinct.append(st)
        if len(distinct) == pool:
            break
    if not distinct:
        return []
    offset = seed % len(distinct)
    picked = (distinct[offset:] + distinct[:offset])[:n]
    fulls = {(f.start, f.end): f for f in stays if f.unit == "full"}
    return [fulls.get((p.start, p.end), p) for p in picked]


def _pick_pillar(brand: dict, seed: int) -> dict:
    """Weighted rotation, interleaved (a,b,a,c,a,b,…) rather than clumped
    (a,a,a,a,b,b,b,…) so the feed doesn't repeat one note for days."""
    pillars = brand.get("pillars", [])
    if not pillars:
        return {"key": "availability_spotlight"}
    total = sum(int(p.get("weight", 1)) for p in pillars)
    rotation: list[dict] = []
    credit = {p["key"]: 0.0 for p in pillars}
    for _ in range(total):  # smooth weighted round-robin
        for p in pillars:
            credit[p["key"]] += int(p.get("weight", 1))
        best = max(pillars, key=lambda p: credit[p["key"]])
        credit[best["key"]] -= total
        rotation.append(best)
    return rotation[seed % len(rotation)]


# --------------------------------------------------------------------------- #
# Queue
# --------------------------------------------------------------------------- #

def build_queue(for_date: date | None = None) -> dict:
    for_date = for_date or date.today()
    brand = load_brand()
    apis = load_apis()
    active = active_platforms(apis)
    windows, busy_map = open_windows()
    events = upcoming_events()
    dated_events = [e for e in events if not e.recurring]
    stays = promotable_stays(windows, dated_events)
    seed = for_date.toordinal()
    todays_pillar = _pick_pillar(brand, seed)

    def _event_for(st: OpenWindow) -> PropertyEvent | None:
        if st.event_title:
            return next((e for e in dated_events if e.title == st.event_title), None)
        return next((e for e in dated_events if _overlaps(st, e)), None)

    briefs: list[dict] = []
    chosen = _pick_stays(stays, seed)
    if todays_pillar["key"] == "availability_spotlight" and chosen:
        for i, st in enumerate(chosen):
            briefs.append(_availability_brief(brand, st, seed + i, event=_event_for(st)))
    elif todays_pillar["key"] == "local_events" and dated_events:
        ev = dated_events[seed % min(3, len(dated_events))]  # rotate through the next three
        st = next((s for s in stays if s.unit != "full" and s.event_title == ev.title), None)
        briefs.append(_event_brief(brand, ev, seed, window=st))
    elif chosen:
        # Evergreen pillar but we have real dates: lead with dates anyway
        # (the evergreen prompt still lands in the digest as a human to-do).
        briefs.append(_availability_brief(brand, chosen[0], seed))
        briefs.append(_evergreen_brief(brand, todays_pillar, seed))
    else:
        briefs.append(_evergreen_brief(brand, todays_pillar, seed))

    # Inside the final week before a dated event, a countdown always rides along.
    if todays_pillar["key"] != "local_events":
        for ev in dated_events:
            if 0 < (ev.start.date() - for_date).days <= 7 and not any(b.get("event") and b["event"]["title"] == ev.title for b in briefs):
                st = next((s for s in stays if s.unit != "full" and s.event_title == ev.title), None)
                briefs.append(_event_brief(brand, ev, seed, window=st))
                break

    # Never more than two auto-published posts a day per platform.
    max_auto = int(brand.get("max_auto_posts_per_day", 2))
    kept, n_auto = [], 0
    for b in briefs:
        if b.get("needs_human_media"):
            kept.append(b)
        elif n_auto < max_auto:
            kept.append(b)
            n_auto += 1
    briefs = kept

    # Claude writes the captions; template copy stays as the fallback.
    auto = [b for b in briefs if not b.get("needs_human_media")]
    if auto:
        written = write_captions({"posts": [
            {k: v for k, v in b.items() if k not in ("copy", "media_url")} for b in auto]}, brand)
        for b in auto:
            w = (written or {}).get(b["id"])
            if w:
                b["copy"] = {"instagram": w["instagram"], "facebook": w["facebook"]}
                b["hashtags"] = w["hashtags"]
                b["alt_text"] = w["alt_text"]
                b["copy_source"] = "claude"
            else:
                b["copy_source"] = "template"

    # Fan each brief out only to platforms that are active and due today.
    posts = []
    for key, cfg in active.items():
        cadence = cfg.get("cadence_per_week", 7)
        if cadence >= 7 or seed % max(1, round(7 / max(1, cadence))) == 0:
            flavor = "instagram" if key in ("meta_instagram", "threads", "pinterest") else "facebook"
            for b in briefs:
                posts.append({"platform": key, **{k: v for k, v in b.items() if k != "copy"},
                              "copy": b["copy"][flavor]})

    queue = {
        "date": for_date.isoformat(),
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "pillar_of_day": todays_pillar["key"],
        "active_platforms": sorted(active),
        "inactive_platforms": sorted(set((apis.get("platforms") or {})) - set(active)),
        "open_windows": [w.to_dict() for w in windows[:10]],
        "top_stays": [s.to_dict() for s in stays[:8]],
        "upcoming_events": [e.to_dict() for e in events[:10]],
        "new_bookings": [],  # filled by main after diffing state
        "briefs": briefs,
        "posts": posts,
        "busy_map": busy_map,
    }
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    (QUEUE_DIR / f"{for_date.isoformat()}.json").write_text(json.dumps(queue, indent=2))
    return queue
