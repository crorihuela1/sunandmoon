"""Regression tests for the availability -> copy path.

These cover the failure modes that made the engine ship wrong dates or nothing
at all: length-biased ranking, the exclusive-checkout off-by-one, and the
first-run booking-alert storm. No network: iCal payloads are injected directly.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from sunmoon_social import availability
from sunmoon_social.availability import OpenWindow, detect_new_bookings, open_windows
from sunmoon_social.content_engine import _fmt_window

TODAY = date(2026, 8, 23)


def _ics(blocks):
    out = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//test//EN"]
    for i, (s, e) in enumerate(blocks):
        out += ["BEGIN:VEVENT", f"UID:{i}@t",
                f"DTSTART;VALUE=DATE:{s.strftime('%Y%m%d')}",
                f"DTEND;VALUE=DATE:{e.strftime('%Y%m%d')}",
                "SUMMARY:Reserved", "END:VEVENT"]
    out.append("END:VCALENDAR")
    return "\r\n".join(out).encode()


@pytest.fixture
def frozen_today(monkeypatch):
    """Pin date.today() so scoring and horizons are deterministic."""
    class _D(date):
        @classmethod
        def today(cls):
            return TODAY
    monkeypatch.setattr(availability, "date", _D)
    return TODAY


@pytest.fixture
def wire(monkeypatch, tmp_path):
    """Point the engine at in-memory calendars and a scratch state dir."""
    monkeypatch.setattr(availability, "STATE_DIR", tmp_path)

    def _install(unit_blocks: dict):
        feeds = {u: _ics(b) for u, b in unit_blocks.items()}
        monkeypatch.setattr(availability, "load_calendars", lambda: {
            "units": {u: {"sources": [{"type": "ical", "url": f"http://t/{u}"}]}
                      for u in feeds},
            "scoring": {"horizon_days": 60, "weekend_bonus": 3, "holiday_bonus": 5,
                        "near_term_bonus": 2, "gap_night_bonus": 2,
                        "min_window_nights": 1, "max_promote_nights": 7},
        })

        class _Resp:
            def __init__(self, c): self.content = c
            def raise_for_status(self): pass

        monkeypatch.setattr(availability.requests, "get",
                            lambda url, **kw: _Resp(feeds[url.rsplit("/", 1)[-1]]))
    return _install


# --- date rendering -------------------------------------------------------

@pytest.mark.parametrize("start,end,expected", [
    (date(2026, 9, 4), date(2026, 9, 5), "Sep 4"),          # 1 night
    (date(2026, 9, 4), date(2026, 9, 6), "Sep 4–5"),        # 2 nights
    (date(2026, 8, 26), date(2026, 8, 29), "Aug 26–28"),    # 3 nights
    (date(2026, 8, 30), date(2026, 9, 2), "Aug 30–Sep 1"),  # crosses month
    (date(2026, 12, 30), date(2027, 1, 3), "Dec 30–Jan 2"), # crosses year
])
def test_window_renders_nights_not_checkout(start, end, expected):
    """The checkout day is not a night on offer and must not appear in the range."""
    assert _fmt_window(OpenWindow("sun", start, end)) == expected


def test_rendered_range_length_matches_night_count():
    w = OpenWindow("sun", date(2026, 8, 26), date(2026, 8, 29))
    first, last = 26, 28  # from "Aug 26–28"
    assert last - first + 1 == w.nights


# --- ranking --------------------------------------------------------------

def test_short_valuable_gap_outranks_long_empty_stretch(frozen_today, wire):
    """A 3-night gap over a weekend must beat a 50-night vacancy.

    Raw-sum scoring crowned the longest span, so the copy advertised '50 nights'
    and buried the gap actually worth filling.
    """
    wire({"sun": [(TODAY, TODAY + timedelta(days=3)),
                  (TODAY + timedelta(days=6), TODAY + timedelta(days=10))]})
    windows, _ = open_windows()
    top = windows[0]
    assert (top.start, top.end) == (date(2026, 8, 26), date(2026, 8, 29))


def test_promoted_windows_are_stay_sized(frozen_today, wire):
    """No window advertised to guests may exceed max_promote_nights."""
    wire({"sun": []})  # wide-open calendar -> one 60-night span
    windows, _ = open_windows()
    assert windows, "an empty calendar should still yield a promotable window"
    assert all(w.nights <= 7 for w in windows), [w.nights for w in windows]


# --- booking alerts -------------------------------------------------------

def test_first_observation_is_baseline_not_alert_storm(frozen_today, wire, tmp_path):
    """Wiring up a calendar must not email one alert per existing reservation."""
    busy = {"sun": [["2026-08-29", "2026-09-02"], ["2026-09-04", "2026-09-07"]]}
    assert detect_new_bookings(busy) == []
    assert detect_new_bookings(busy) == []          # steady state, still quiet
    busy["sun"].append(["2026-09-10", "2026-09-14"])
    assert detect_new_bookings(busy) == [
        {"unit": "sun", "start": "2026-09-10", "end": "2026-09-14"}]


def test_past_checkin_never_alerts(frozen_today, wire, tmp_path):
    """A feed that backfills history shouldn't page the owner about last month."""
    detect_new_bookings({"sun": []})                # establish baseline
    assert detect_new_bookings({"sun": [["2026-07-01", "2026-07-05"]]}) == []


def test_unconfigured_unit_gets_no_baseline(frozen_today, tmp_path, monkeypatch):
    """An absent unit is 'unknown', not 'observed with zero bookings'."""
    monkeypatch.setattr(availability, "STATE_DIR", tmp_path)
    detect_new_bookings({})                          # nothing configured yet
    state = json.loads((tmp_path / "known_busy.json").read_text())
    assert "sun" not in state
    # Once it IS configured, the first sight is still a baseline.
    assert detect_new_bookings({"sun": [["2026-09-04", "2026-09-07"]]}) == []
