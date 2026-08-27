"""Read unit calendars, compute open windows, and score them for promotion.

Busy blocks (bookings) come from any mix of sources per unit:
  - iCal export URLs (the booking engine on sunandmoon30a.com, Airbnb, VRBO, ...)
  - public Google Calendars (needs GOOGLE_CALENDAR_API_KEY)

Everything not busy inside the scoring horizon is an open window. Windows are
scored so the content engine promotes the most valuable nights first.

Dates are the whole game here. A stay is a run of *nights in the property's
local timezone*, but feeds disagree about how to say that: OTAs (Airbnb/VRBO)
export all-day DATE values, while site booking engines commonly export UTC
datetimes. Reading the UTC calendar date off a 7pm-Central checkout lands on
the *next* day and silently blocks a bookable night; a late check-in lands the
other way and advertises a night that is already sold. Every timestamp is
therefore converted to a property-local date before it becomes a night.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

import requests
from icalendar import Calendar

from .config import STATE_DIR, load_calendars

DEFAULT_TZ = "America/Chicago"  # 30A / Walton County, FL is Central


def property_tz() -> ZoneInfo:
    """The timezone the property's nights are counted in."""
    name = (load_calendars().get("property") or {}).get("timezone") or DEFAULT_TZ
    try:
        return ZoneInfo(name)
    except Exception:
        print(f"  ! unknown property timezone {name!r}; falling back to {DEFAULT_TZ}")
        return ZoneInfo(DEFAULT_TZ)


def local_today() -> date:
    """Today at the property — not on whatever timezone the runner happens to use."""
    return datetime.now(property_tz()).date()


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """n-th `weekday` of a month (n=-1 for the last one). Monday == 0."""
    if n > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    last_day = date(year, 12, 31) if month == 12 else date(year, month + 1, 1) - timedelta(days=1)
    return last_day - timedelta(days=(last_day.weekday() - weekday) % 7)


@lru_cache(maxsize=None)
def us_holidays(year: int) -> frozenset[date]:
    """Booking-relevant US holidays for any year (the old set was 2026-only, so
    scoring went quiet as soon as the horizon crossed into the next year)."""
    thanksgiving = _nth_weekday(year, 11, 3, 4)  # 4th Thursday
    return frozenset({
        date(year, 1, 1),                        # New Year's Day
        _nth_weekday(year, 5, 0, -1),            # Memorial Day (last Mon in May)
        date(year, 7, 3), date(year, 7, 4),      # Independence Day
        _nth_weekday(year, 9, 0, 1),             # Labor Day (1st Mon in Sep)
        date(year, 10, 31),                      # Halloween
        thanksgiving, thanksgiving + timedelta(days=1),
        date(year, 12, 24), date(year, 12, 25),  # Christmas
        date(year, 12, 31),                      # New Year's Eve
    })


def _is_holiday(day: date) -> bool:
    return day in us_holidays(day.year)


def _local_date(value) -> date:
    """Collapse an iCal/Google value to the calendar date it falls on locally."""
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(property_tz())
        return value.date()
    return value


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
        s, e = _local_date(start.dt), _local_date(end.dt)
        if e <= s:  # a same-day or inverted block holds no nights
            continue
        busy.append((s, e))
    return busy


def _fetch_gcal_busy(calendar_id: str, horizon_days: int) -> list[tuple[date, date]]:
    key = os.environ.get("GOOGLE_CALENDAR_API_KEY")
    if not key:
        return []
    now = datetime.now(timezone.utc)
    resp = requests.get(
        f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events",
        params={"key": key, "singleEvents": "true",
                "timeMin": now.isoformat(),
                "timeMax": (now + timedelta(days=horizon_days)).isoformat()},
        timeout=30,
    )
    resp.raise_for_status()
    busy = []
    for item in resp.json().get("items", []):
        s, e = _gcal_edge(item.get("start")), _gcal_edge(item.get("end"))
        if s and e and e > s:
            busy.append((s, e))
    return busy


def _gcal_edge(edge: dict | None) -> date | None:
    """Google gives either {'date': 'YYYY-MM-DD'} or {'dateTime': RFC3339}."""
    if not edge:
        return None
    if edge.get("date"):
        return date.fromisoformat(edge["date"])
    raw = edge.get("dateTime")
    if not raw:
        return None
    return _local_date(datetime.fromisoformat(raw.replace("Z", "+00:00")))


def source_label(src: dict) -> str:
    """A short, human name for a feed, so divergence reports say which one."""
    if src.get("label"):
        return str(src["label"])
    if src.get("type") == "google_calendar":
        return f"google:{src.get('calendar_id', '?')}"
    url = str(src.get("url", "?"))
    host = url.split("//", 1)[-1].split("/", 1)[0]
    return f"ical:{host or url}"


@dataclass
class SourceRead:
    """One calendar feed's answer for one unit."""
    label: str
    blocks: list[tuple[date, date]]
    ok: bool

    def nights(self) -> set[date]:
        out: set[date] = set()
        for s, e in self.blocks:
            day = s
            while day < e:
                out.add(day)
                day += timedelta(days=1)
        return out


@dataclass
class Divergence:
    """A night the feeds disagree about — the site says one thing, the vacay
    listing says another. Left unreported, this is how a double booking or a
    silently withheld night happens."""
    unit: str
    night: date
    busy_on: list[str]
    open_on: list[str]

    def to_dict(self) -> dict:
        return {"unit": self.unit, "night": self.night.isoformat(),
                "busy_on": self.busy_on, "open_on": self.open_on}


def read_sources(sources: list[dict], horizon_days: int) -> list[SourceRead]:
    """Read every feed for a unit separately, so the merged view and the
    cross-feed comparison come out of one fetch."""
    reads: list[SourceRead] = []
    for src in sources or []:
        kind = src.get("type")
        label = source_label(src)
        try:
            if kind == "ical" and src.get("url"):
                reads.append(SourceRead(label, _fetch_ical_busy(src["url"]), True))
            elif kind == "google_calendar" and src.get("calendar_id"):
                reads.append(SourceRead(
                    label, _fetch_gcal_busy(src["calendar_id"], horizon_days), True))
        except Exception as exc:  # one broken feed must not silence the others
            print(f"  ! calendar source failed ({kind}): {exc}")
            reads.append(SourceRead(label, [], False))
    return reads


def _unit_busy(sources: list[dict], horizon_days: int) -> tuple[list[tuple[date, date]], bool]:
    """Busy blocks for a unit, plus whether *every* source was read successfully.

    A feed that 500s must never read as "the whole calendar is free" — that is
    how sold nights get advertised, so the caller treats a partial read as
    unknown availability rather than as availability.
    """
    reads = read_sources(sources, horizon_days)
    busy = [b for r in reads for b in r.blocks]
    return busy, all(r.ok for r in reads)


def merge_overlapping(blocks: list[tuple[date, date]]) -> list[tuple[date, date]]:
    """Collapse blocks that overlap into one.

    The same reservation arrives once per feed (site + vacay network), and two
    copies of one stay used to read as two bookings — two "new booking" emails
    for one guest. Blocks that merely *touch* are left alone: a checkout and the
    next guest's check-in on the same day are two stays, and collapsing them
    would swallow the second arrival.
    """
    merged: list[tuple[date, date]] = []
    for s, e in sorted(blocks):
        if merged and s < merged[-1][1]:
            prev_s, prev_e = merged[-1]
            merged[-1] = (prev_s, max(prev_e, e))
        else:
            merged.append((s, e))
    return merged


def divergences_for(unit: str, reads: list[SourceRead], today: date, horizon: date) -> list[Divergence]:
    """Nights inside the horizon that some readable feed calls busy and another
    calls open. Merging hides this; bookings depend on it."""
    usable = [r for r in reads if r.ok]
    if len(usable) < 2:
        return []                      # nothing to compare against
    per_source = {r.label: r.nights() for r in usable}
    contested = set().union(*per_source.values())
    out = []
    for night in sorted(n for n in contested if today <= n < horizon):
        busy_on = sorted(label for label, nights in per_source.items() if night in nights)
        open_on = sorted(label for label in per_source if label not in busy_on)
        if busy_on and open_on:
            out.append(Divergence(unit, night, busy_on, open_on))
    return out


def _score(window: OpenWindow, scoring: dict, has_neighbors: bool, today: date) -> int:
    score = 0
    day = window.start
    while day < window.end:
        if day.weekday() in (4, 5):
            score += scoring.get("weekend_bonus", 3)
        if _is_holiday(day):
            score += scoring.get("holiday_bonus", 5)
        if (day - today).days <= 14:
            score += scoring.get("near_term_bonus", 2)
        day += timedelta(days=1)
    if window.nights <= 2 and has_neighbors:
        score += scoring.get("gap_night_bonus", 2)
    return score


def open_windows() -> tuple[list[OpenWindow], dict[str, list], list[str], list[Divergence]]:
    """Scored open windows per unit, the raw busy map (for booking alerts), the
    units whose availability could not be established this run, and the nights
    the unit's feeds disagree about."""
    cfg = load_calendars()
    scoring = cfg.get("scoring", {})
    horizon = scoring.get("horizon_days", 60)
    today = local_today()
    end_horizon = today + timedelta(days=horizon)

    windows: list[OpenWindow] = []
    busy_map: dict[str, list] = {}
    unknown: list[str] = []
    divergences: list[Divergence] = []
    for unit, ucfg in (cfg.get("units") or {}).items():
        sources = [s for s in (ucfg.get("sources") or [])
                   if s.get("url") or s.get("calendar_id")]
        if not sources:
            # No calendar wired up yet: availability is unknown, never claim
            # the unit is open. The digest will flag the missing config.
            busy_map[unit] = []
            unknown.append(unit)
            continue
        reads = read_sources(sources, horizon)
        divergences.extend(divergences_for(unit, reads, today, end_horizon))
        busy = merge_overlapping([b for r in reads for b in r.blocks])
        busy_map[unit] = [[s.isoformat(), e.isoformat()] for s, e in busy]
        if not all(r.ok for r in reads):
            # Partial read: keep what we learned for the alerting diff, but do
            # not turn the gaps into "book these nights" copy.
            unknown.append(unit)
            continue
        cursor = today
        for s, e in busy + [(end_horizon, end_horizon)]:
            if s > cursor:
                w = OpenWindow(unit=unit, start=cursor, end=min(s, end_horizon))
                if w.nights >= scoring.get("min_window_nights", 1):
                    w.score = _score(w, scoring, bool(busy), today)
                    windows.append(w)
            cursor = max(cursor, e)
            if cursor >= end_horizon:
                break
    windows.sort(key=lambda w: w.score, reverse=True)
    return windows, busy_map, unknown, divergences


def upcoming_events() -> list[PropertyEvent]:
    cfg = load_calendars()
    horizon = cfg.get("scoring", {}).get("horizon_days", 60)
    tz = property_tz()
    now = datetime.now(tz)
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
                    dt = start.dt
                    if not isinstance(dt, datetime):
                        dt = datetime.combine(dt, datetime.min.time())
                    dt = dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)
                    if now <= dt <= now + timedelta(days=horizon):
                        events.append(PropertyEvent(
                            title=str(comp.get("SUMMARY", "Event")), start=dt,
                            description=str(comp.get("DESCRIPTION", "") or ""),
                            location=str(comp.get("LOCATION", "") or "")))
        except Exception as exc:
            print(f"  ! events source failed: {exc}")
    events.sort(key=lambda e: e.start)
    return events


def detect_new_bookings(busy_map: dict[str, list], unknown_units: list[str] | None = None) -> list[dict]:
    """Diff current busy blocks against the last run; new blocks = new bookings.

    Units whose feeds could not be read are skipped and their previously known
    blocks are preserved: overwriting them with a short read makes every
    existing reservation look brand new the moment the feed recovers, and mails
    the owner a booking alert for each one.
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state_file = STATE_DIR / "known_busy.json"
    known = {}
    if state_file.exists():
        known = json.loads(state_file.read_text())
    stale = set(unknown_units or [])
    new = []
    next_state = dict(known)
    for unit, blocks in busy_map.items():
        if unit in stale:
            next_state.setdefault(unit, known.get(unit, []))
            continue
        seen = {tuple(b) for b in known.get(unit, [])}
        for block in blocks:
            if tuple(block) not in seen:
                new.append({"unit": unit, "start": block[0], "end": block[1]})
        next_state[unit] = blocks
    state_file.write_text(json.dumps(next_state, indent=2))
    return new
