"""Email alerts: new bookings, imminent check-ins, and the daily digest.

Routed per config/notifications.yaml — to experience@sunandmoon38.com with a
configurable CC. Uses plain SMTP so it works with Google Workspace, Zoho, or
any transactional provider.
"""

from __future__ import annotations

import os
import smtplib
from datetime import date, datetime, timedelta
from email.message import EmailMessage

from .config import load_notifications


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

    smtp_cfg = cfg["smtp"]
    host = os.environ.get(smtp_cfg["host_env"], "")
    if not host:
        return {"status": "skipped", "reason": f"SMTP not configured (set {smtp_cfg['host_env']} etc.)"}
    port = int(os.environ.get(smtp_cfg["port_env"], "587"))
    with smtplib.SMTP(host, port, timeout=30) as server:
        server.starttls()
        server.login(os.environ[smtp_cfg["user_env"]], os.environ[smtp_cfg["pass_env"]])
        server.send_message(msg)
    return {"status": "sent", "subject": subject}


def booking_alerts(queue: dict, dry_run: bool = True) -> list[dict]:
    """One email per newly detected booking, plus imminent check-in reminders."""
    cfg = load_notifications()
    results = []
    if cfg.get("alerts", {}).get("new_booking", True):
        for booking in queue.get("new_bookings", []):
            unit = booking["unit"].title()
            results.append(_send(
                f"[Sun & Moon 38] New booking — {unit} Suite, {booking['start']} → {booking['end']}",
                f"A new booking appeared on the {unit} Suite calendar.\n\n"
                f"  Check-in:  {booking['start']}\n  Check-out: {booking['end']}\n\n"
                "Confirm guest details in the booking platform and prep the experience plan.\n",
                dry_run))
    hours = cfg.get("alerts", {}).get("imminent_checkin_hours", 48)
    cutoff = (date.today() + timedelta(days=hours / 24)).isoformat()
    for unit, blocks in queue.get("busy_map", {}).items():
        for start, end in blocks:
            if date.today().isoformat() <= start <= cutoff:
                results.append(_send(
                    f"[Sun & Moon 38] Imminent check-in — {unit.title()} Suite arrives {start}",
                    f"Guest checks in to the {unit.title()} Suite on {start} (out {end}).\n"
                    "Time to run the arrival checklist.\n",
                    dry_run))
    return results


def daily_digest(queue: dict, publish_results: list[dict], dry_run: bool = True) -> dict:
    cfg = load_notifications()
    if not cfg.get("alerts", {}).get("daily_digest", True):
        return {"status": "disabled"}
    lines = [f"Sun & Moon 38 — daily engine digest, {queue['date']}",
             f"Pillar of the day: {queue['pillar_of_day']}", ""]
    lines.append(f"Active platforms: {', '.join(queue['active_platforms']) or 'none yet'}")
    lines.append(f"Awaiting activation: {', '.join(queue['inactive_platforms'])}")
    lines.append("")
    lines.append("Top open windows (score | unit | dates):")
    for w in queue.get("open_windows", [])[:5]:
        lines.append(f"  {w['score']:>3} | {w['unit']:<5} | {w['start']} → {w['end']} ({w['nights']} nights)")
    if not queue.get("open_windows"):
        lines.append("  (no calendar sources configured yet — see config/calendars.yaml)")
    lines.append("")
    lines.append("Publish results:")
    for r in publish_results or [{"platform": "-", "status": "nothing queued"}]:
        lines.append(f"  {r.get('platform', '-'):<24} {r.get('status')}"
                     + (f" — {r['reason']}" if r.get("reason") else ""))
    lines.append("")
    lines.append(f"Generated {datetime.utcnow().isoformat()}Z by the Sun & Moon 38 content engine.")
    return _send(f"[Sun & Moon 38] Daily digest — {queue['date']}", "\n".join(lines), dry_run)
