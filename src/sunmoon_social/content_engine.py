"""Generate the methodical daily content queue.

The engine rotates through the brand pillars (weighted), targets the
highest-scored open windows and the upcoming events, and writes one queue file
per day: queue/YYYY-MM-DD.json. Publishers consume that queue — but only for
platforms whose API status is `active`.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

from .availability import (OpenWindow, PropertyEvent, local_today, open_windows,
                            upcoming_events)
from .config import QUEUE_DIR, active_platforms, load_apis, load_brand


def _fmt_window(w: OpenWindow) -> str:
    same_month = w.start.month == w.end.month
    end_fmt = "%-d" if same_month else "%b %-d"
    return f"{w.start.strftime('%b %-d')}–{w.end.strftime(end_fmt)}"


def _hashtags(brand: dict, n_rotating: int = 3, seed: int = 0) -> str:
    tags = list(brand.get("hashtags", {}).get("always", []))
    rot = brand.get("hashtags", {}).get("rotating", [])
    if rot:
        tags += [rot[(seed + i) % len(rot)] for i in range(min(n_rotating, len(rot)))]
    return " ".join(tags)


def _rate_line(brand: dict, unit_key: str) -> str:
    rates = brand.get("rates", {}) or {}
    rate = rates.get(unit_key)
    cur = rates.get("currency", "USD")
    parts = []
    if rate:
        symbol = "$" if cur == "USD" else f"{cur} "
        parts.append(f"From {symbol}{rate}/night.")
    if rates.get("promo_note"):
        parts.append(rates["promo_note"])
    return " ".join(parts)


def _unit_meta(brand: dict, key: str) -> dict:
    for u in brand.get("brand", {}).get("units", []):
        if u.get("key") == key:
            return u
    return {"label": key.title(), "angle": ""}


def _availability_post(brand: dict, w: OpenWindow, seed: int) -> dict:
    b = brand["brand"]
    unit = _unit_meta(brand, w.unit)
    nights = f"{w.nights} night{'s' if w.nights != 1 else ''}"
    copy = (
        f"{unit['label']} just opened up {_fmt_window(w)} — {nights} of "
        f"{unit.get('angle', 'sky-to-sky calm')}. {_rate_line(brand, w.unit)} "
        f"Dates this good don't sit long. Book at {b['booking_url']}"
    ).replace("  ", " ").strip()
    return {"pillar": "availability_spotlight", "unit": w.unit, "window": w.to_dict(),
            "copy": copy, "hashtags": _hashtags(brand, seed=seed), "media_hint": f"hero shot of {unit['label']}"}


def _event_post(brand: dict, ev: PropertyEvent, seed: int) -> dict:
    b = brand["brand"]
    when = ev.start.strftime("%A, %B %-d")
    days_out = (ev.start.date() - local_today()).days
    lead = f"{days_out} days out: " if 0 < days_out <= 7 else ""
    copy = (
        f"{lead}{ev.title} at {b['name']} — {when}. "
        f"{(ev.description or 'An evening worth planning a stay around.').strip()} "
        f"Stay the night, skip the drive home: {b['booking_url']}"
    )
    return {"pillar": "events_experiences", "event": ev.to_dict(), "copy": copy,
            "hashtags": _hashtags(brand, seed=seed), "media_hint": "event poster / last event's best photo"}


def _evergreen_post(brand: dict, pillar: dict, seed: int) -> dict:
    b = brand["brand"]
    prompts = {
        "local_guide": f"One more reason to visit: share a nearby trail, table, or seasonal happening guests ask about. Tie it back to staying at {b['name']}.",
        "behind_the_scenes": f"Show the ritual: how {b['name']} gets ready for guests — one detail, one photo, one sentence of why it matters.",
        "guest_love": "Repost a recent review or guest photo (with permission). Let their words carry it; add only a thank-you and the booking link.",
    }
    return {"pillar": pillar["key"], "copy": prompts.get(pillar["key"], pillar.get("description", "")),
            "hashtags": _hashtags(brand, seed=seed), "media_hint": "see copy", "needs_human_media": True}


def build_queue(for_date: date | None = None) -> dict:
    for_date = for_date or local_today()
    brand = load_brand()
    apis = load_apis()
    active = active_platforms(apis)
    windows, busy_map, unknown_units, divergences = open_windows()
    events = upcoming_events()
    seed = for_date.toordinal()

    # Weighted pillar rotation, deterministic by date so the mix is methodical.
    pillars = brand.get("pillars", [])
    rotation: list[dict] = []
    for p in pillars:
        rotation += [p] * int(p.get("weight", 1))
    todays_pillar = rotation[seed % len(rotation)] if rotation else {"key": "availability_spotlight"}

    items = []
    if todays_pillar["key"] == "availability_spotlight" and windows:
        items.append(_availability_post(brand, windows[0], seed))
        if len(windows) > 1:  # always keep a second open window in the mix
            items.append(_availability_post(brand, windows[1], seed + 1))
    elif todays_pillar["key"] == "events_experiences" and events:
        items.append(_event_post(brand, events[0], seed))
    else:
        items.append(_evergreen_post(brand, todays_pillar, seed))
    # Event countdowns always ride along inside the final week.
    for ev in events:
        if 0 < (ev.start.date() - for_date).days <= 7 and todays_pillar["key"] != "events_experiences":
            items.append(_event_post(brand, ev, seed))
            break

    # Fan each item out only to platforms that are active and due today.
    posts = []
    for key, cfg in active.items():
        cadence = cfg.get("cadence_per_week", 7)
        if cadence >= 7 or seed % max(1, round(7 / max(1, cadence))) == 0:
            for item in items:
                posts.append({"platform": key, **item})

    queue = {
        "date": for_date.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pillar_of_day": todays_pillar["key"],
        "active_platforms": sorted(active),
        "inactive_platforms": sorted(set((apis.get("platforms") or {})) - set(active)),
        "open_windows": [w.to_dict() for w in windows[:10]],
        "upcoming_events": [e.to_dict() for e in events[:10]],
        "new_bookings": [],  # filled by main after diffing state
        "posts": posts,
        "busy_map": busy_map,
        "unknown_units": unknown_units,
        "calendar_divergences": [d.to_dict() for d in divergences],
    }
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    (QUEUE_DIR / f"{for_date.isoformat()}.json").write_text(json.dumps(queue, indent=2))
    return queue
