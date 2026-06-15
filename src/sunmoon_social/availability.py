"""Read unit calendars, compute open windows, and score them for promotion.

Busy blocks (bookings) come from any mix of sources per unit:
  - iCal export URLs (the booking engine on sunandmoon30a.com, Airbnb, VRBO, ...)
  - public Google Calendars (needs GOOGLE_CALENDAR_API_KEY)

Everything not busy inside the scoring horizon is an open window. Windows are
scored so the content engine promotes the most valuable nights first.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import requests
from icalendar import Calendar

from .config import STATE_DIR, load_calendars

US_HOLIDAYS_2026 = {  # fixed-date highlights worth a bonus; extend as needed
    date(2026, 7, 3), date(2026, 7, 4), date(2026, 9, 7), date(2026, 10, 31),
    date(2026, 11, 26), date(2026, 12, 24), date(2026, 12, 25), date(2026, 12, 31),
}


@dataclass
class OpenWindow:
    unit: str
    start: date
    end: date  # exclusive (checkout day)
    score: int = 0

    @property
    def nights(self) -> int:
        return (self.end - self.start).days

    def to_dict(self) -> dict:
        return {"unit": self.unit, "start": self.start.isoformat(),
                "end": self.end.isoformat(), "nights": self.nights, "score": self.score}


@dataclass
class PropertyEvent:
    title: str
    start: datetime
    description: str = ""
    location: str = ""

    def to_dict(self) -> dict:
        return {"title": self.title, "start": self.start.isoformat(),
                "description": self.description, "location": self.location}


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


def _unit_busy(sources: list[dict], horizon_days: int) -> list[tuple[date, date]]:
    busy = []
    for src in sources or []:
        kind = src.get("type")
        try:
            if kind == "ical" and src.get("url"):
                busy.extend(_fetch_ical_busy(src["url"]))
            elif kind == "google_calendar" and src.get("calendar_id"):
                busy.extend(_fetch_gcal_busy(src["calendar_id"], horizon_days))
        except Exception as exc:  # one broken feed must not silence the others
            print(f"  ! calendar source failed ({kind}): {exc}")
    return busy


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
    if window.nights <= 2 and has_neighbors:
        score += scoring.get("gap_night_bonus", 2)
    return score


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
        sources = [s for s in (ucfg.get("sources") or [])
                   if s.get("url") or s.get("calendar_id")]
        if not sources:
            # No calendar wired up yet: availability is unknown, never claim
            # the unit is open. The digest will flag the missing config.
            busy_map[unit] = []
            continue
        busy = sorted(_unit_busy(sources, horizon))
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
    windows.sort(key=lambda w: w.score, reverse=True)
    return windows, busy_map


def upcoming_events() -> list[PropertyEvent]:
    cfg = load_calendars()
    horizon = cfg.get("scoring", {}).get("horizon_days", 60)
    events: list[PropertyEvent] = []
    for src in (cfg.get("events", {}).get("sources") or []):
        try:
            if src.get("type") == "ical" and src.get("url"):
                resp = requests.get(src["url"], timeout=30)
                resp.raise_for_status()
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
        except Exception as exc:
            print(f"  ! events source failed: {exc}")
    events.sort(key=lambda e: e.start)
    return events


def detect_new_bookings(busy_map: dict[str, list]) -> list[dict]:
    """Diff current busy blocks against the last run; new blocks = new bookings."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state_file = STATE_DIR / "known_busy.json"
    known = {}
    if state_file.exists():
        known = json.loads(state_file.read_text())
    new = []
    for unit, blocks in busy_map.items():
        seen = {tuple(b) for b in known.get(unit, [])}
        for block in blocks:
            if tuple(block) not in seen:
                new.append({"unit": unit, "start": block[0], "end": block[1]})
    state_file.write_text(json.dumps(busy_map, indent=2))
    return new
