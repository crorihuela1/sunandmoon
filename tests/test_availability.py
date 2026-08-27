"""Date-handling regressions for the availability sync.

Run: python -m unittest discover -s tests -t .
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sunmoon_social import availability as av  # noqa: E402


def ical(events: str) -> bytes:
    return f"BEGIN:VCALENDAR\nVERSION:2.0\n{events}END:VCALENDAR\n".encode()


class _Resp:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        pass


class LocalDateTests(unittest.TestCase):
    """A stay is a run of nights in the property's timezone, however the feed
    chooses to express it."""

    def test_all_day_and_utc_feeds_agree_on_the_same_stay(self):
        # Fri 2026-09-04 16:00 CDT -> Mon 2026-09-07 10:00 CDT, three ways.
        ota = ical("BEGIN:VEVENT\nUID:1\nDTSTART;VALUE=DATE:20260904\n"
                   "DTEND;VALUE=DATE:20260907\nEND:VEVENT\n")
        utc = ical("BEGIN:VEVENT\nUID:2\nDTSTART:20260904T210000Z\n"
                   "DTEND:20260907T150000Z\nEND:VEVENT\n")
        zoned = ical("BEGIN:VEVENT\nUID:3\nDTSTART;TZID=America/Chicago:20260904T160000\n"
                     "DTEND;TZID=America/Chicago:20260907T100000\nEND:VEVENT\n")
        expected = [(date(2026, 9, 4), date(2026, 9, 7))]
        for name, raw in (("ota", ota), ("utc", utc), ("zoned", zoned)):
            with self.subTest(feed=name), mock.patch.object(
                    av.requests, "get", return_value=_Resp(raw)):
                self.assertEqual(av._fetch_ical_busy("http://x"), expected)

    def test_evening_checkout_does_not_block_an_extra_night(self):
        # 19:00 CDT departure == 00:00Z the next day. Reading the UTC date used
        # to mark 2026-09-07 busy and withhold a bookable night.
        raw = ical("BEGIN:VEVENT\nUID:4\nDTSTART:20260904T210000Z\n"
                   "DTEND:20260908T000000Z\nEND:VEVENT\n")
        with mock.patch.object(av.requests, "get", return_value=_Resp(raw)):
            self.assertEqual(av._fetch_ical_busy("http://x"),
                             [(date(2026, 9, 4), date(2026, 9, 7))])

    def test_evening_checkin_does_not_free_a_sold_night(self):
        # 20:00 CDT arrival == 01:00Z the next day; the night of the 5th is sold.
        raw = ical("BEGIN:VEVENT\nUID:5\nDTSTART:20260906T010000Z\n"
                   "DTEND:20260907T150000Z\nEND:VEVENT\n")
        with mock.patch.object(av.requests, "get", return_value=_Resp(raw)):
            self.assertEqual(av._fetch_ical_busy("http://x"),
                             [(date(2026, 9, 5), date(2026, 9, 7))])

    def test_zero_night_block_is_ignored(self):
        raw = ical("BEGIN:VEVENT\nUID:6\nDTSTART;VALUE=DATE:20260904\n"
                   "DTEND;VALUE=DATE:20260904\nEND:VEVENT\n")
        with mock.patch.object(av.requests, "get", return_value=_Resp(raw)):
            self.assertEqual(av._fetch_ical_busy("http://x"), [])


class GoogleCalendarEdgeTests(unittest.TestCase):
    def test_all_day_edge(self):
        self.assertEqual(av._gcal_edge({"date": "2026-09-04"}), date(2026, 9, 4))

    def test_utc_datetime_edge_uses_local_date(self):
        self.assertEqual(av._gcal_edge({"dateTime": "2026-09-08T00:00:00Z"}),
                         date(2026, 9, 7))

    def test_offset_datetime_edge(self):
        self.assertEqual(av._gcal_edge({"dateTime": "2026-09-04T16:00:00-05:00"}),
                         date(2026, 9, 4))

    def test_missing_edge(self):
        self.assertIsNone(av._gcal_edge(None))
        self.assertIsNone(av._gcal_edge({}))


class HolidayTests(unittest.TestCase):
    def test_holidays_follow_the_year_not_a_hardcoded_2026(self):
        for year in (2026, 2027, 2030):
            with self.subTest(year=year):
                self.assertIn(date(year, 7, 4), av.us_holidays(year))
                self.assertIn(date(year, 12, 25), av.us_holidays(year))

    def test_floating_holidays(self):
        self.assertIn(date(2026, 11, 26), av.us_holidays(2026))   # Thanksgiving
        self.assertIn(date(2027, 11, 25), av.us_holidays(2027))
        self.assertIn(date(2026, 9, 7), av.us_holidays(2026))     # Labor Day
        self.assertIn(date(2026, 5, 25), av.us_holidays(2026))    # Memorial Day


class FeedOutageTests(unittest.TestCase):
    """A feed that fails must not read as 'the calendar is empty'."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(av, "STATE_DIR", Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def state(self):
        return json.loads((Path(self.tmp.name) / "known_busy.json").read_text())

    def test_unit_busy_reports_incomplete_reads(self):
        with mock.patch.object(av.requests, "get", side_effect=RuntimeError("500")):
            busy, complete = av._unit_busy([{"type": "ical", "url": "http://x"}], 60)
        self.assertEqual(busy, [])
        self.assertFalse(complete)

    def test_outage_does_not_wipe_state_or_replay_bookings(self):
        healthy = {"sun": [["2026-09-04", "2026-09-07"]], "moon": []}
        self.assertEqual(len(av.detect_new_bookings(healthy, [])), 1)

        # Feed 500s: no blocks read, unit flagged unknown.
        self.assertEqual(av.detect_new_bookings({"sun": [], "moon": []}, ["sun"]), [])
        self.assertEqual(self.state()["sun"], [["2026-09-04", "2026-09-07"]])

        # Feed recovers: the existing stay is not re-announced as new.
        self.assertEqual(av.detect_new_bookings(healthy, []), [])

    def test_genuinely_new_booking_still_alerts(self):
        av.detect_new_bookings({"sun": [["2026-09-04", "2026-09-07"]]}, [])
        new = av.detect_new_bookings(
            {"sun": [["2026-09-04", "2026-09-07"], ["2026-09-11", "2026-09-14"]]}, [])
        self.assertEqual(new, [{"unit": "sun", "start": "2026-09-11", "end": "2026-09-14"}])


class OpenWindowTests(unittest.TestCase):
    def _cfg(self, sources):
        return {"property": {"timezone": "America/Chicago"},
                "units": {"sun": {"label": "Golden Sun", "sources": sources}},
                "scoring": {"horizon_days": 30, "min_window_nights": 1}}

    def test_unreadable_feed_yields_no_open_windows(self):
        with mock.patch.object(av, "load_calendars",
                               return_value=self._cfg([{"type": "ical", "url": "http://x"}])), \
             mock.patch.object(av.requests, "get", side_effect=RuntimeError("boom")):
            windows, busy_map, unknown, _ = av.open_windows()
        self.assertEqual(windows, [])
        self.assertEqual(unknown, ["sun"])
        self.assertEqual(busy_map, {"sun": []})

    def test_unconfigured_unit_is_unknown_not_wide_open(self):
        with mock.patch.object(av, "load_calendars",
                               return_value=self._cfg([{"type": "ical", "url": ""}])):
            windows, _, unknown, _ = av.open_windows()
        self.assertEqual(windows, [])
        self.assertEqual(unknown, ["sun"])

    def test_healthy_feed_splits_windows_around_the_stay(self):
        today = av.local_today()
        start, end = today + timedelta(days=5), today + timedelta(days=8)
        raw = ical(f"BEGIN:VEVENT\nUID:7\nDTSTART;VALUE=DATE:{start:%Y%m%d}\n"
                   f"DTEND;VALUE=DATE:{end:%Y%m%d}\nEND:VEVENT\n")
        with mock.patch.object(av, "load_calendars",
                               return_value=self._cfg([{"type": "ical", "url": "http://x"}])), \
             mock.patch.object(av.requests, "get", return_value=_Resp(raw)):
            windows, busy_map, unknown, _ = av.open_windows()
        self.assertEqual(unknown, [])
        self.assertEqual(busy_map["sun"], [[start.isoformat(), end.isoformat()]])
        spans = sorted((w.start, w.end) for w in windows)
        self.assertEqual(spans, [(today, start), (end, today + timedelta(days=30))])


class DivergenceTests(unittest.TestCase):
    """The whole point of listing the site feed and the vacay-network feed
    together: notice when they stop agreeing."""

    def _reads(self, a_blocks, b_blocks, a_ok=True, b_ok=True):
        return [av.SourceRead("ical:sunandmoon30a.com", a_blocks, a_ok),
                av.SourceRead("ical:vacay-network", b_blocks, b_ok)]

    def test_agreeing_feeds_report_nothing(self):
        stay = [(date(2026, 9, 4), date(2026, 9, 7))]
        diffs = av.divergences_for("sun", self._reads(stay, list(stay)),
                                   date(2026, 9, 1), date(2026, 10, 1))
        self.assertEqual(diffs, [])

    def test_night_sold_on_one_feed_only_is_reported(self):
        diffs = av.divergences_for(
            "sun",
            self._reads([(date(2026, 9, 4), date(2026, 9, 7))],
                        [(date(2026, 9, 4), date(2026, 9, 6))]),
            date(2026, 9, 1), date(2026, 10, 1))
        self.assertEqual([d.night for d in diffs], [date(2026, 9, 6)])
        self.assertEqual(diffs[0].busy_on, ["ical:sunandmoon30a.com"])
        self.assertEqual(diffs[0].open_on, ["ical:vacay-network"])

    def test_divergences_outside_the_horizon_are_ignored(self):
        diffs = av.divergences_for(
            "sun",
            self._reads([(date(2027, 1, 4), date(2027, 1, 7))], []),
            date(2026, 9, 1), date(2026, 10, 1))
        self.assertEqual(diffs, [])

    def test_unreadable_feed_is_not_treated_as_disagreement(self):
        diffs = av.divergences_for(
            "sun",
            self._reads([(date(2026, 9, 4), date(2026, 9, 7))], [], b_ok=False),
            date(2026, 9, 1), date(2026, 10, 1))
        self.assertEqual(diffs, [])

    def test_single_source_has_nothing_to_compare(self):
        reads = [av.SourceRead("ical:only", [(date(2026, 9, 4), date(2026, 9, 7))], True)]
        self.assertEqual(av.divergences_for("sun", reads, date(2026, 9, 1), date(2026, 10, 1)), [])

    def test_open_windows_surfaces_divergence_between_two_feeds(self):
        today = av.local_today()
        start, end = today + timedelta(days=5), today + timedelta(days=8)
        site = ical(f"BEGIN:VEVENT\nUID:a\nDTSTART;VALUE=DATE:{start:%Y%m%d}\n"
                    f"DTEND;VALUE=DATE:{end:%Y%m%d}\nEND:VEVENT\n")
        vacay = ical(f"BEGIN:VEVENT\nUID:b\nDTSTART;VALUE=DATE:{start:%Y%m%d}\n"
                     f"DTEND;VALUE=DATE:{end - timedelta(days=1):%Y%m%d}\nEND:VEVENT\n")
        cfg = {"property": {"timezone": "America/Chicago"},
               "units": {"sun": {"label": "Golden Sun", "sources": [
                   {"type": "ical", "url": "https://sunandmoon30a.com/sun.ics"},
                   {"type": "ical", "url": "https://vacay.example/sun.ics"}]}},
               "scoring": {"horizon_days": 30, "min_window_nights": 1}}
        with mock.patch.object(av, "load_calendars", return_value=cfg), \
             mock.patch.object(av.requests, "get",
                               side_effect=[_Resp(site), _Resp(vacay)]):
            _, _, unknown, diffs = av.open_windows()
        self.assertEqual(unknown, [])
        self.assertEqual([d.night for d in diffs], [end - timedelta(days=1)])


class MergeTests(unittest.TestCase):
    def test_duplicate_stay_from_two_feeds_becomes_one_block(self):
        stay = (date(2026, 9, 4), date(2026, 9, 7))
        self.assertEqual(av.merge_overlapping([stay, stay]), [stay])

    def test_feeds_that_disagree_merge_to_the_conservative_union(self):
        self.assertEqual(
            av.merge_overlapping([(date(2026, 9, 4), date(2026, 9, 7)),
                                  (date(2026, 9, 4), date(2026, 9, 6))]),
            [(date(2026, 9, 4), date(2026, 9, 7))])

    def test_back_to_back_stays_stay_separate(self):
        # Checkout and the next check-in on the same day are two arrivals.
        blocks = [(date(2026, 9, 1), date(2026, 9, 4)),
                  (date(2026, 9, 4), date(2026, 9, 6))]
        self.assertEqual(av.merge_overlapping(blocks), blocks)

    def test_one_stay_reported_once_end_to_end(self):
        today = av.local_today()
        start, end = today + timedelta(days=5), today + timedelta(days=8)
        feed = ical(f"BEGIN:VEVENT\nUID:x\nDTSTART;VALUE=DATE:{start:%Y%m%d}\n"
                    f"DTEND;VALUE=DATE:{end:%Y%m%d}\nEND:VEVENT\n")
        cfg = {"property": {"timezone": "America/Chicago"},
               "units": {"sun": {"label": "Golden Sun", "sources": [
                   {"type": "ical", "url": "https://sunandmoon30a.com/sun.ics"},
                   {"type": "ical", "url": "https://vacay.example/sun.ics"}]}},
               "scoring": {"horizon_days": 30, "min_window_nights": 1}}
        with mock.patch.object(av, "load_calendars", return_value=cfg), \
             mock.patch.object(av.requests, "get",
                               side_effect=[_Resp(feed), _Resp(feed)]):
            _, busy_map, _, diffs = av.open_windows()
        self.assertEqual(busy_map["sun"], [[start.isoformat(), end.isoformat()]])
        self.assertEqual(diffs, [])


class SourceLabelTests(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(av.source_label({"type": "ical", "url": "https://vacay.example/a.ics"}),
                         "ical:vacay.example")
        self.assertEqual(av.source_label({"type": "google_calendar", "calendar_id": "x@g"}),
                         "google:x@g")
        self.assertEqual(av.source_label({"type": "ical", "url": "https://x/a.ics",
                                          "label": "Vacay network"}), "Vacay network")


class ClockTests(unittest.TestCase):
    def test_local_today_is_the_property_date(self):
        with mock.patch.object(av, "load_calendars",
                               return_value={"property": {"timezone": "America/Chicago"}}):
            self.assertEqual(av.local_today(),
                             datetime.now(av.ZoneInfo("America/Chicago")).date())

    def test_unknown_timezone_falls_back(self):
        with mock.patch.object(av, "load_calendars",
                               return_value={"property": {"timezone": "Mars/Olympus"}}):
            self.assertEqual(str(av.property_tz()), av.DEFAULT_TZ)


if __name__ == "__main__":
    unittest.main()
