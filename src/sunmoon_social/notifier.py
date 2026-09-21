"""Email alerts: new bookings, imminent check-ins, and the daily digest.

Routed per config/notifications.yaml — to experience@sunandmoon30a.com with a
configurable CC. Sends through Resend when RESEND_API_KEY is set (the site's
other workers already use it), otherwise plain SMTP.
"""

from __future__ import annotations

import os
import smtplib

import requests
from datetime import date, datetime, timedelta
from email.message import EmailMessage

from .config import load_brand, load_notifications


def _house(unit_key: str) -> str:
    """Resolve a unit key (sun/moon) to its house name (Golden Sun / Blue Moon)."""
    for u in load_brand().get("brand", {}).get("units", []):
        if u.get("key") == unit_key:
            return u.get("label", unit_key.title())
    return unit_key.title()


def _send(subject: str, body: str, dry_run: bool) -> dict:
    cfg = load_notifications()["email"]
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["from"]
    msg["To"] = cfg["to"]
    if cfg.get("cc"):
        msg["Cc"] = ", ".join(cfg["cc"])
    msg.set_content(body)

    if dry_run:
        print(f"  [dry-run:email] To: {msg['To']} Cc: {msg.get('Cc', '')}\n  Subject: {subject}\n{body}\n")
        return {"status": "dry-run", "subject": subject}

    resend_key = os.environ.get("RESEND_API_KEY")
    if resend_key:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {resend_key}"},
            json={"from": cfg["from"], "to": [cfg["to"]], "cc": cfg.get("cc") or [],
                  "subject": subject, "text": body},
            timeout=30)
        if resp.status_code >= 300:
            return {"status": "error", "reason": f"resend {resp.status_code}: {resp.text[:200]}"}
        return {"status": "sent", "subject": subject, "id": resp.json().get("id")}

    smtp_cfg = cfg["smtp"]
    host = os.environ.get(smtp_cfg["host_env"], "")
    if not host:
        return {"status": "skipped", "reason": "no mailer configured (set RESEND_API_KEY, or SMTP_HOST/PORT/USER/PASS)"}
    port = int(os.environ.get(smtp_cfg["port_env"], "587"))
    with smtplib.SMTP(host, port, timeout=30) as server:
        server.starttls()
        server.login(os.environ[smtp_cfg["user_env"]], os.environ[smtp_cfg["pass_env"]])
        server.send_message(msg)
    return {"status": "sent", "subject": subject}


def _alertable_units() -> set[str]:
    units = load_calendars().get("units") or {}
    return {k for k, v in units.items() if not (v or {}).get("derived")}


def booking_alerts(queue: dict, dry_run: bool = True) -> list[dict]:
    """One email per newly detected booking, plus imminent check-in reminders.

    Check-ins are remembered in state/alerted.json so a 48h window doesn't
    email the same arrival two mornings in a row. Blocks that start *today*
    are skipped: the booking API clips in-progress stays to today, so they
    can't be told apart from a same-day arrival.
    """
    cfg = load_notifications()
    units = _alertable_units()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    alerted_file = STATE_DIR / "alerted.json"
    alerted = set(json.loads(alerted_file.read_text())) if alerted_file.exists() else set()
    results = []
    if cfg.get("alerts", {}).get("new_booking", True):
        for booking in queue.get("new_bookings", []):
            if booking["unit"] not in units:
                continue
            house = _house(booking["unit"])
            results.append(_send(
                f"[Sun & Moon 30A] New booking — {house}, {booking['start']} → {booking['end']}",
                f"A new booking appeared on the {house} calendar.\n\n"
                f"  Check-in:  {booking['start']}\n  Check-out: {booking['end']}\n\n"
                "Confirm guest details in the booking platform and prep the experience plan.\n",
                dry_run))
    hours = cfg.get("alerts", {}).get("imminent_checkin_hours", 48)
    cutoff = (date.today() + timedelta(days=hours / 24)).isoformat()
    for unit, blocks in queue.get("busy_map", {}).items():
        if unit not in units:
            continue
        house = _house(unit)
        for start, end in blocks:
            key = f"checkin:{unit}:{start}"
            if date.today().isoformat() < start <= cutoff and key not in alerted:
                results.append(_send(
                    f"[Sun & Moon 30A] Imminent check-in — {house} arrives {start}",
                    f"Guest checks in to {house} on {start} (out {end}).\n"
                    "Time to run the arrival checklist.\n",
                    dry_run))
                if not dry_run:
                    alerted.add(key)
    # Keep the file small: only remember keys from the last 60 days.
    floor = (date.today() - timedelta(days=60)).isoformat()
    alerted = {k for k in alerted if k.split(":")[-1] >= floor}
    if not dry_run:
        alerted_file.write_text(json.dumps(sorted(alerted), indent=2))
    return results


def daily_digest(queue: dict, publish_results: list[dict], dry_run: bool = True) -> dict:
    cfg = load_notifications()
    if not cfg.get("alerts", {}).get("daily_digest", True):
        return {"status": "disabled"}
    lines = [f"Sun & Moon 30A — daily engine digest, {queue['date']}",
             f"Pillar of the day: {queue['pillar_of_day']}", ""]
    lines.append(f"Active platforms: {', '.join(queue['active_platforms']) or 'none yet'}")
    lines.append(f"Awaiting activation: {', '.join(queue['inactive_platforms'])}")
    lines.append("")
    lines.append("Top promotable stays (score | unit | dates):")
    for w in queue.get("top_stays", [])[:6]:
        lines.append(f"  {w['score']:>3} | {w['unit']:<5} | {w['start']} → {w['end']} ({w['nights']} nights, {w['kind']}"
                     + (f", {w['event_title']}" if w.get("event_title") else "") + ")")
    lines.append("Open windows (raw):")
    for w in queue.get("open_windows", [])[:5]:
        lines.append(f"  {w['unit']:<5} | {w['start']} → {w['end']} ({w['nights']} nights)")
    if not queue.get("open_windows"):
        lines.append("  (no open windows — either fully booked or every availability source failed)")
    lines.append("")
    lines.append("Upcoming 30A events in play:")
    for e in queue.get("upcoming_events", [])[:5]:
        lines.append(f"  {e['start'][:10]}  {e['title']}" + (f" ({e['location']})" if e.get("location") else ""))
    lines.append("")
    lines.append("Today's briefs:")
    for b in queue.get("briefs", []):
        head = b.get("open_dates") or b.get("event_summary") or b.get("pillar")
        lines.append(f"  [{b.get('pillar')}] {b.get('unit_label') or ''} {head}"
                     + (f" — copy by {b['copy_source']}" if b.get("copy_source") else "")
                     + ("  ← needs a human photo/caption" if b.get("needs_human_media") else ""))
        copy = b.get("copy", {})
        preview = copy.get("instagram") if isinstance(copy, dict) else str(copy)
        for line in (preview or "").splitlines():
            lines.append(f"      {line}")
        lines.append("")
    lines.append("Publish results:")
    for r in publish_results or [{"platform": "-", "status": "nothing queued"}]:
        lines.append(f"  {r.get('platform', '-'):<24} {r.get('status')}"
                     + (f" — {r['reason']}" if r.get("reason") else "")
                     + (f" — {r['id']}" if r.get("id") else ""))
    lines.append("")
    lines.append(f"Generated {datetime.utcnow().isoformat()}Z by the Sun & Moon 30A content engine.")
    return _send(f"[Sun & Moon 30A] Daily digest — {queue['date']}", "\n".join(lines), dry_run)
