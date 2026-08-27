"""CLI: python -m sunmoon_social <plan|publish|digest|status|sync> [--live]"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta

from .availability import (detect_new_bookings, divergences_for, local_today,
                           read_sources)
from .config import (QUEUE_DIR, active_platforms, load_apis, load_calendars,
                     missing_secrets)
from .content_engine import build_queue
from .notifier import booking_alerts, daily_digest
from .publishers import publish_queue


def _load_or_build_queue() -> dict:
    path = QUEUE_DIR / f"{local_today().isoformat()}.json"
    if path.exists():
        return json.loads(path.read_text())
    return cmd_plan()


def cmd_plan() -> dict:
    queue = build_queue()
    queue["new_bookings"] = detect_new_bookings(
        queue.get("busy_map", {}), queue.get("unknown_units", []))
    (QUEUE_DIR / f"{queue['date']}.json").write_text(json.dumps(queue, indent=2))
    print(f"Queue built for {queue['date']}: {len(queue['posts'])} posts, "
          f"pillar={queue['pillar_of_day']}, "
          f"{len(queue['open_windows'])} open windows, "
          f"{len(queue['new_bookings'])} new bookings")
    return queue


def cmd_publish(live: bool) -> list[dict]:
    queue = _load_or_build_queue()
    results = publish_queue(queue, live=live)
    for r in results:
        print(f"  {r.get('platform'):<24} {r.get('status')}" + (f" — {r['reason']}" if r.get("reason") else ""))
    path = QUEUE_DIR / f"{queue['date']}.results.json"
    path.write_text(json.dumps(results, indent=2))
    return results


def cmd_digest(live: bool) -> None:
    queue = _load_or_build_queue()
    results_path = QUEUE_DIR / f"{queue['date']}.results.json"
    results = json.loads(results_path.read_text()) if results_path.exists() else []
    for r in booking_alerts(queue, dry_run=not live):
        print(f"  alert: {r}")
    print(f"  digest: {daily_digest(queue, results, dry_run=not live)}")


def cmd_sync() -> int:
    """Read each unit's calendars feed-by-feed and report whether they agree.

    This is the check to run right after pasting a new iCal URL: it says what
    the feed parsed to and whether it matches the other listings for the same
    unit. Exit code is non-zero if anything is unreadable or in dispute.
    """
    cfg = load_calendars()
    horizon = cfg.get("scoring", {}).get("horizon_days", 60)
    today = local_today()
    end_horizon = today + timedelta(days=horizon)
    problems = 0

    print(f"Calendar sync check — {today} through {end_horizon} "
          f"({cfg.get('property', {}).get('timezone', 'America/Chicago')})")
    for unit, ucfg in (cfg.get("units") or {}).items():
        label = ucfg.get("label", unit)
        sources = [s for s in (ucfg.get("sources") or [])
                   if s.get("url") or s.get("calendar_id")]
        print(f"\n{label} ({unit}):")
        if not sources:
            print("  no calendar source configured — availability unknown, "
                  "nothing will be promoted")
            problems += 1
            continue
        reads = read_sources(sources, horizon)
        for r in reads:
            if not r.ok:
                print(f"  {r.label:<40} UNREADABLE")
                problems += 1
                continue
            nights = sorted(n for n in r.nights() if today <= n < end_horizon)
            print(f"  {r.label:<40} {len(r.blocks)} block(s), "
                  f"{len(nights)} booked night(s) in horizon")
        if len(reads) < 2:
            print("  only one source — nothing to cross-check. Add the other "
                  "listing's iCal export to compare them.")
            continue
        diffs = divergences_for(unit, reads, today, end_horizon)
        if not diffs:
            print("  all feeds agree")
        else:
            problems += len(diffs)
            print(f"  {len(diffs)} night(s) in dispute:")
            for d in diffs:
                print(f"    {d.night}  busy on {', '.join(d.busy_on)}"
                      f"  /  open on {', '.join(d.open_on)}")
    print()
    print("No problems found." if not problems else f"{problems} problem(s) found.")
    return 1 if problems else 0


def cmd_status() -> None:
    apis = load_apis()
    active = active_platforms(apis)
    print("Platform API registry:")
    for group in ("platforms", "aggregators"):
        for key, cfg in (apis.get(group) or {}).items():
            flag = "LIVE" if key in active and not missing_secrets(cfg) else (
                "active, secrets missing" if key in active else cfg["status"])
            print(f"  {key:<26} {flag}")


def main() -> int:
    parser = argparse.ArgumentParser(prog="sunmoon_social")
    parser.add_argument("command", choices=["plan", "publish", "digest", "status", "sync"])
    parser.add_argument("--live", action="store_true",
                        help="actually post/email (also requires SOCIAL_LIVE=true)")
    parser.add_argument("--dry-run", action="store_true", help="explicit no-op flag (default)")
    args = parser.parse_args()

    if args.command == "plan":
        cmd_plan()
    elif args.command == "publish":
        cmd_publish(live=args.live)
    elif args.command == "digest":
        cmd_digest(live=args.live)
    elif args.command == "status":
        cmd_status()
    elif args.command == "sync":
        return cmd_sync()
    return 0


if __name__ == "__main__":
    sys.exit(main())
