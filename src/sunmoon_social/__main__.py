"""CLI: python -m sunmoon_social <plan|publish|digest|status> [--live]"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from .availability import detect_new_bookings
from .config import QUEUE_DIR, active_platforms, load_apis, missing_secrets
from .content_engine import build_queue
from .notifier import booking_alerts, daily_digest
from .publishers import publish_queue


def _load_or_build_queue() -> dict:
    path = QUEUE_DIR / f"{date.today().isoformat()}.json"
    if path.exists():
        return json.loads(path.read_text())
    return cmd_plan()


def cmd_plan() -> dict:
    queue = build_queue()
    queue["new_bookings"] = detect_new_bookings(queue.get("busy_map", {}))
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
    parser.add_argument("command", choices=["plan", "publish", "digest", "status"])
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
