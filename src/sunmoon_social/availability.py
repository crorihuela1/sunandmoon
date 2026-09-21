"""Read unit calendars, compute open windows, and score them for promotion.

Busy blocks (bookings) come from any mix of sources per unit:
  - the live booking engine (book.sunandmoon30a.com/availability — Vacay data)
  - iCal export URLs (Airbnb, VRBO, ...)
  - public Google Calendars (needs GOOGLE_CALENDAR_API_KEY)

Local 30A events come from the site's own events.html (kept current by the
events-refresh routine) and/or an iCal feed.

Everything not busy inside the scoring horizon is an open window. Windows are
scored so the content engine promotes the most valuable nights first.
"""

from __future__ import annotations

import html
import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from icalendar import Calendar

from .config import REPO_ROOT, STATE_DIR, load_calendars

US_HOLIDAYS_2026 = {  # fixed-date highlights worth a bonus; extend as needed
    date(2026, 7, 3), date(2026, 7, 4), date(2026, 9, 7), date(2026, 10, 31),
    date(2026, 11, 26), date(2026, 12, 24), date(2026, 12, 25), date(2026, 12, 31),
    date(2027, 1, 1),
}

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


@dataclass
class OpenWindow:
    unit: str
    start: date
    end: date  # exclusive (checkout day)
    score: int = 0
    kind: str = "window"  # window | weekend | long_weekend | short_stay | event_stay | gap
    event_title: str = ""

    @property
    def nights(self) -> int:
        return (self.end - self.start).days

    def to_dict(self) -> dict:
        return {"unit": self.unit, "start": self.start.isoformat(),
                "end": self.end.isoformat(), "nights": self.nights, "score": self.score,
                "kind": self.kind, "event_title": self.event_title}


@dataclass
class PropertyEvent:
    title: str
    start: datetime
    end: datetime | None = None
    description: str = ""
    location: str = ""
    url: str = ""
    recurring: str = ""  # e.g. "Sat" for a weekly market; empty for dated events

    def to_dict(self) -> dict:
        return {"title": self.title, "start": self.start.isoformat(),
                "end": self.end.isoformat() if self.end else None,
                "description": self.description, "location": self.location,
                "url": self.url, "recurring": self.recurring}


# --------------------------------------------------------------------------- #
# Busy-block sources
# --------------------------------------------------------------------------- #

def _fetch_booking_api_busy(url: str, prop: str, horizon_days: int) -> list[tuple[date, date]]:
    """book.sunandmoon30a.com/availability — `to` is the last blocked NIGHT (inclusive)."""
    months = max(1, min(18, -(-horizon_days // 31)))
    resp = requests.get(url, params={"property": prop, "months": months}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"booking api returned {data}")
    return [(date.fromisoformat(b["from"]), date.fromisoformat(b["to"]) + timedelta(days=1))
            for b in data.get("blocked", [])]


def _fetch_ical_busy(url: str) -> list[tuple[date, date]]:
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    busy = []
    cal = Calendar.from_ical(resp.content)
    for comp in cal.walk("VEVENT"):
        start, end = comp.get("DTSTART"), comp.get("DTEND")
        if not start or not end:
            continue
        s, e = start.dt, end.dt
        s = s.date() if isinstance(s, datetime) else s
        e = e.date() if isinstance(e, datetime) else e
        busy.append((s, e))
    return busy


def _fetch_gcal_busy(calendar_id: str, horizon_days: int) -> list[tuple[date, date]]:
    key = os.environ.get("GOOGLE_CALENDAR_API_KEY")
    if not key:
        return []
    now = datetime.utcnow()
    resp = requests.get(
        f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events",
        params={"key": key, "singleEvents": "true",
                "timeMin": now.isoformat() + "Z",
                "timeMax": (now + timedelta(days=horizon_days)).isoformat() + "Z"},
        timeout=30,
    )
    resp.raise_for_status()
    busy = []
    for item in resp.json().get("items", []):
        start = item.get("start", {}).get("date") or item.get("start", {}).get("dateTime", "")[:10]
        end = item.get("end", {}).get("date") or item.get("end", {}).get("dateTime", "")[:10]
        if start and end:
            busy.append((date.fromisoformat(start), date.fromisoformat(end)))
    return busy


def _source_configured(src: dict) -> bool:
    kind = src.get("type")
    if kind == "booking_api":
        return bool(src.get("url") and src.get("property"))
    return bool(src.get("url") or src.get("calendar_id"))


def _unit_busy(sources: list[dict], horizon_days: int) -> tuple[list[tuple[date, date]], bool]:
    """Returns (busy blocks, ok). ok=False when every source failed — the
    caller must then treat availability as unknown, never as wide open."""
    busy, ok = [], False
    for src in sources or []:
        kind = src.get("type")
        try:
            if kind == "booking_api":
                busy.extend(_fetch_booking_api_busy(src["url"], src["property"], horizon_days))
            elif kind == "ical":
                busy.extend(_fetch_ical_busy(src["url"]))
            elif kind == "google_calendar":
                busy.extend(_fetch_gcal_busy(src["calendar_id"], horizon_days))
            else:
                continue
            ok = True
        except Exception as exc:  # one broken feed must not silence the others
            print(f"  ! calendar source failed ({kind}): {exc}")
    return busy, ok


def _merge(blocks: list[tuple[date, date]]) -> list[tuple[date, date]]:
    merged: list[tuple[date, date]] = []
    for s, e in sorted(blocks):
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #

def _score(window: OpenWindow, scoring: dict, has_neighbors: bool) -> int:
    score = 0
    day = window.start
    while day < window.end:
        if day.weekday() in (4, 5):
            score += scoring.get("weekend_bonus", 3)
        if day in US_HOLIDAYS_2026:
            score += scoring.get("holiday_bonus", 5)
        if (day - date.today()).days <= 14:
            score += scoring.get("near_term_bonus", 2)
        day += timedelta(days=1)
    if window.kind == "gap" and has_neighbors:
        score += scoring.get("gap_night_bonus", 2)
    if window.kind == "event_stay":
        score += scoring.get("event_bonus", 4)
    return score


def promotable_stays(windows: list[OpenWindow], events: list["PropertyEvent"] | None = None) -> list[OpenWindow]:
    """Slice raw open windows into stays worth a post (2–7 nights), scored.

    A 40-night off-season stretch is not a hook; the Fri–Sun inside it is.
    Candidates per window: the whole window if short, every weekend and long
    weekend inside it, and the nights wrapped around any local event.
    """
    cfg = load_calendars()
    scoring = cfg.get("scoring", {})
    min_n = scoring.get("min_window_nights", 2)
    max_n = scoring.get("max_stay_nights", 7)
    cands: dict[tuple, OpenWindow] = {}

    def add(unit, start, end, kind, event_title=""):
        n = (end - start).days
        if n < min_n or n > max_n:
            return
        key = (unit, start, end)
        if key in cands and cands[key].kind == "event_stay":
            return
        st = OpenWindow(unit=unit, start=start, end=end, kind=kind, event_title=event_title)
        st.score = _score(st, scoring, has_neighbors=True)
        cands[key] = st

    for w in windows:
        if w.nights <= max_n:
            add(w.unit, w.start, w.end, "gap" if w.nights <= 3 else "short_stay")
        day = w.start
        while day < w.end:
            if day.weekday() == 4:  # Friday
                add(w.unit, day, min(day + timedelta(days=2), w.end), "weekend")
                add(w.unit, day, min(day + timedelta(days=3), w.end), "long_weekend")
            if day.weekday() == 3:  # Thursday
                add(w.unit, day, min(day + timedelta(days=3), w.end), "long_weekend")
            day += timedelta(days=1)
        for ev in events or []:
            if ev.recurring:
                continue
            ev_s, ev_e = ev.start.date(), (ev.end or ev.start).date()
            start = max(w.start, ev_s - timedelta(days=1))
            end = min(w.end, ev_e + timedelta(days=1))
            if end - start >= timedelta(days=min_n):
                add(w.unit, start, end, "event_stay", ev.title)
    stays = sorted(cands.values(), key=lambda s: (s.score, -abs(s.nights - 3)), reverse=True)
    return stays


def open_windows() -> tuple[list[OpenWindow], dict[str, list]]:
    """Return scored open windows per unit, plus the raw busy map (for booking alerts)."""
    cfg = load_calendars()
    scoring = cfg.get("scoring", {})
    horizon = scoring.get("horizon_days", 60)
    today = date.today()
    end_horizon = today + timedelta(days=horizon)

    windows: list[OpenWindow] = []
    busy_map: dict[str, list] = {}
    for unit, ucfg in (cfg.get("units") or {}).items():
        sources = [s for s in (ucfg.get("sources") or []) if _source_configured(s)]
        if not sources:
            # No calendar wired up yet: availability is unknown, never claim
            # the unit is open. The digest will flag the missing config.
            busy_map[unit] = []
            continue
        busy, ok = _unit_busy(sources, horizon)
        if not ok:
            print(f"  ! {unit}: every availability source failed — skipping (unknown ≠ open)")
            busy_map[unit] = []
            continue
        busy = _merge(busy)
        busy_map[unit] = [[s.isoformat(), e.isoformat()] for s, e in busy]
        cursor = today
        for s, e in busy + [(end_horizon, end_horizon)]:
            if s > cursor:
                w = OpenWindow(unit=unit, start=cursor, end=min(s, end_horizon))
                if w.nights >= scoring.get("min_window_nights", 1):
                    w.score = _score(w, scoring, has_neighbors=bool(busy))
                    windows.append(w)
            cursor = max(cursor, e)
            if cursor >= end_horizon:
                break
    windows.sort(key=lambda w: (w.score, -w.nights), reverse=True)
    return windows, busy_map


# --------------------------------------------------------------------------- #
# Local events
# --------------------------------------------------------------------------- #

_CARD_RE = re.compile(
    r'<a href="(?P<url>[^"]*)"[^>]*class="event-card">.*?'
    r'<span class="month">(?P<month>[^<]*)</span>\s*'
    r'<span class="day">(?P<day>[^<]*)</span>\s*'
    r'<span class="span">(?P<span>[^<]*)</span>.*?'
    r'<div class="event-info-name">(?P<name>.*?)</div>\s*'
    r'<div class="event-info-meta">(?P<meta>.*?)</div>',
    re.S)

_FEATURED_RE = re.compile(
    r'<div class="event-featured">.*?'
    r'<span class="month">(?P<month>[^<]*)</span>\s*'
    r'<span class="day">(?P<day>[^<]*)</span>\s*'
    r'<span class="year">(?P<span>[^<]*)</span>.*?'
    r'<div class="event-featured-name">(?P<name>.*?)</div>\s*'
    r'<div class="event-featured-meta">(?P<meta>.*?)</div>.*?'
    r'<a href="(?P<url>[^"]*)"',
    re.S)


def _clean(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def _card_to_event(m: re.Match) -> PropertyEvent | None:
    month, day, span = m["month"].strip(), m["day"].strip(), m["span"].strip()
    name, meta, url = _clean(m["name"]), _clean(m["meta"]), m["url"].strip()
    title, _, location = name.partition(" · ")
    mnum = MONTHS.get(month[:3].lower())
    if mnum and re.fullmatch(r"\d{4}", span) and re.fullmatch(r"\d{1,2}(-\d{1,2})?", day):
        year = int(span)
        first, _, last = day.partition("-")
        start = datetime(year, mnum, int(first))
        end = datetime(year, mnum, int(last or first))
        return PropertyEvent(title=title, start=start, end=end, description=meta,
                             location=location, url=url)
    if re.fullmatch(r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun|Daily)", day):
        # Weekly / recurring: anchor to the next occurrence so it sorts sensibly.
        today = date.today()
        if day == "Daily":
            nxt = today
        else:
            wd = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].index(day)
            nxt = today + timedelta(days=(wd - today.weekday()) % 7)
        return PropertyEvent(title=title, start=datetime.combine(nxt, datetime.min.time()),
                             description=f"{month} · {day} {span} · {meta}", location=location,
                             url=url, recurring=day)
    return None


def _site_events(path: str) -> list[PropertyEvent]:
    p = Path(path)
    if not p.is_absolute():
        p = REPO_ROOT / p
    text = p.read_text(encoding="utf-8")
    events = []
    feat = _FEATURED_RE.search(text)
    if feat:
        ev = _card_to_event(feat)
        if ev:
            events.append(ev)
    for m in _CARD_RE.finditer(text):
        ev = _card_to_event(m)
        if ev:
            events.append(ev)
    return events


def _ical_events(url: str, horizon: int) -> list[PropertyEvent]:
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    events = []
    for comp in Calendar.from_ical(resp.content).walk("VEVENT"):
        start = comp.get("DTSTART")
        if not start:
            continue
        dt = start.dt if isinstance(start.dt, datetime) else datetime.combine(start.dt, datetime.min.time())
        if datetime.now(dt.tzinfo) <= dt <= datetime.now(dt.tzinfo) + timedelta(days=horizon):
            events.append(PropertyEvent(
                title=str(comp.get("SUMMARY", "Event")), start=dt,
                description=str(comp.get("DESCRIPTION", "") or ""),
                location=str(comp.get("LOCATION", "") or "")))
    return events


def upcoming_events() -> list[PropertyEvent]:
    cfg = load_calendars()
    horizon = cfg.get("scoring", {}).get("horizon_days", 60)
    today = date.today()
    events: list[PropertyEvent] = []
    for src in (cfg.get("events", {}).get("sources") or []):
        try:
            if src.get("type") == "site_events" and src.get("path"):
                events.extend(_site_events(src["path"]))
            elif src.get("type") == "ical" and src.get("url"):
                events.extend(_ical_events(src["url"], horizon))
        except Exception as exc:
            print(f"  ! events source failed: {exc}")
    # Keep dated events still ahead of us (an event is live until its last day)
    # and inside the horizon; recurring ones are always "upcoming".
    keep = []
    for ev in events:
        last = (ev.end or ev.start).date()
        if ev.recurring or today <= last <= today + timedelta(days=horizon):
            keep.append(ev)
    keep.sort(key=lambda e: (bool(e.recurring), e.start))
    return keep


def _nights(blocks: list) -> set[str]:
    out = set()
    for s, e in blocks:
        d = date.fromisoformat(s)
        while d < date.fromisoformat(e):
            out.add(d.isoformat())
            d += timedelta(days=1)
    return out


def _ranges(nights: set[str]) -> list[tuple[str, str]]:
    """Group consecutive ISO nights into [start, end-exclusive) ranges."""
    out = []
    for n in sorted(nights):
        d = date.fromisoformat(n)
        if out and date.fromisoformat(out[-1][1]) == d:
            out[-1] = (out[-1][0], (d + timedelta(days=1)).isoformat())
        else:
            out.append((n, (d + timedelta(days=1)).isoformat()))
    return out


def detect_new_bookings(busy_map: dict[str, list]) -> list[dict]:
    """Diff busy *nights* against the last run; newly busy nights = new bookings."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state_file = STATE_DIR / "known_busy.json"
    known = {}
    if state_file.exists():
        known = json.loads(state_file.read_text())
    today = date.today().isoformat()
    new = []
    for unit, blocks in busy_map.items():
        if not blocks and known.get(unit):
            # A source outage returns nothing; keep what we knew rather than
            # re-alerting every block as "new" when the feed comes back.
            busy_map[unit] = known[unit]
            continue
        before = {n for n in _nights(known.get(unit, [])) if n >= today}
        after = {n for n in _nights(blocks) if n >= today}
        for s, e in _ranges(after - before):
            new.append({"unit": unit, "start": s, "end": e})
    state_file.write_text(json.dumps(busy_map, indent=2))
    return new
