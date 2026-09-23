#!/usr/bin/env python3
"""
BOOKING NOTIFY AGENT — delivers booking_notifications queued by the
booking worker when a direct reservation is made.

  email → Gmail SMTP from experience@sunandmoon30a.com
  sms   → Twilio if TWILIO_* creds exist in referral-os/.env,
          else carrier email-to-SMS gateway (notify_config.json),
          else falls back to emailing the owner with an [SMS] subject
          so no notification is ever lost.

Run:  python3 notify_agent.py [--dry-run]
Install every 5 min:  bash install-launchd.sh
"""
from __future__ import annotations

import argparse
import base64
import json
import smtplib
import ssl
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENV_PATH = HERE.parent / "referral-os" / ".env"
CONFIG_PATH = HERE / "notify_config.json"
SMTP_HOST, SMTP_PORT = "smtp.gmail.com", 587
HTTP_TIMEOUT = 60


def log(msg: str) -> None:
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  {msg}", flush=True)


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


def sb(env, method, path, payload=None, headers=None):
    url = env["SUPABASE_URL"].rstrip("/") + path
    hdrs = {
        "apikey": env["SUPABASE_SERVICE_ROLE_KEY"],
        "Authorization": f"Bearer {env['SUPABASE_SERVICE_ROLE_KEY']}",
        "Content-Type": "application/json",
        **(headers or {}),
    }
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        body = resp.read().decode()
        return json.loads(body) if body else None


def send_email(env, to_addr: str, subject: str, body: str) -> None:
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = env["GMAIL_USER"]
    msg["To"] = to_addr
    msg["Subject"] = subject
    ctx = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
        s.starttls(context=ctx)
        s.login(env["GMAIL_USER"], env["GMAIL_APP_PASSWORD"])
        s.send_message(msg)


def send_sms(env, cfg, number: str, body: str) -> str:
    """Returns a description of how the SMS was delivered."""
    digits = "".join(c for c in number if c.isdigit())

    # 1. Twilio, if configured (canonical TWILIO_FROM_NUMBER; legacy TWILIO_FROM)
    twilio_from = env.get("TWILIO_FROM_NUMBER") or env.get("TWILIO_FROM")
    if env.get("TWILIO_ACCOUNT_SID") and env.get("TWILIO_AUTH_TOKEN") and twilio_from:
        sid = env["TWILIO_ACCOUNT_SID"]
        payload = urllib.parse.urlencode({
            "To": f"+1{digits[-10:]}",
            "From": twilio_from,
            "Body": body[:1500],
        }).encode()
        auth = base64.b64encode(f"{sid}:{env['TWILIO_AUTH_TOKEN']}".encode()).decode()
        req = urllib.request.Request(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
            data=payload,
            headers={"Authorization": f"Basic {auth}",
                     "Content-Type": "application/x-www-form-urlencoded"},
            method="POST")
        with urllib.request.urlopen(req, timeout=30):
            pass
        return "twilio"

    # 2. iMessage via this Mac's Messages.app (works locally, no accounts/fees).
    #    Uses the modern service/buddy form — the older account/participant
    #    objects are broken on macOS 26. Falls through to email on any failure
    #    so a booking alert is never lost.
    if cfg.get("sms_method") == "imessage":
        import subprocess
        script = (
            'on run {targetNumber, msgBody}\n'
            '  with timeout of 20 seconds\n'
            '    tell application "Messages"\n'
            '      set svc to 1st service whose service type = iMessage\n'
            '      set bud to buddy targetNumber of svc\n'
            '      send msgBody to bud\n'
            '    end tell\n'
            '  end timeout\n'
            'end run'
        )
        try:
            r = subprocess.run(
                ["osascript", "-e", script, f"+1{digits[-10:]}", body[:1500]],
                capture_output=True, text=True, timeout=25)
            if r.returncode == 0:
                return "imessage"
            err = r.stderr.strip()[:200]
        except Exception as e:
            err = str(e)[:200]
        # fall through to email so nothing is lost
        send_email(env, env["GMAIL_USER"],
                   f"[SMS → {digits[-10:]}] Direct Sun & Moon reservation (iMessage failed)",
                   body + f"\n\n(iMessage delivery failed: {err})")
        return f"email-fallback (imessage failed: {err})"

    # 3. Carrier email-to-SMS gateway, if configured
    gateway = cfg.get("sms_gateway_domain")
    if gateway:
        send_email(env, f"{digits[-10:]}@{gateway}", "", body[:150])
        return f"gateway:{gateway}"

    # 3. Fallback — deliver as email to the owner so nothing is lost
    send_email(env, env["GMAIL_USER"],
               f"[SMS → {digits[-10:]}] Direct Sun & Moon reservation", body)
    return "email-fallback (configure sms_gateway_domain or TWILIO_* for real texts)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = json.load(open(CONFIG_PATH))
    if not cfg.get("enabled"):
        log("notify agent disabled (notify_config.json) — exiting")
        return 0

    env = load_env(ENV_PATH)
    queued = sb(env, "GET",
        f"/rest/v1/booking_notifications?status=eq.queued&order=created_at.asc&limit={cfg.get('max_per_run', 20)}") or []
    log(f"{len(queued)} queued notification(s)")

    for n in queued:
        label = f"{n['channel']} → {n['recipient']}"
        if args.dry_run:
            log(f"  DRY RUN would send {label}")
            continue
        try:
            if n["channel"] == "email":
                send_email(env, n["recipient"], n.get("subject") or "Sun & Moon notification", n["body"])
                how = "smtp"
            else:
                how = send_sms(env, cfg, n["recipient"], n["body"])
            sb(env, "PATCH", f"/rest/v1/booking_notifications?id=eq.{n['id']}",
               {"status": "sent", "sent_at": datetime.now(timezone.utc).isoformat(),
                "error": None if how in ("smtp", "twilio") else how},
               headers={"Prefer": "return=minimal"})
            log(f"  sent {label} ({how})")
        except Exception as e:
            sb(env, "PATCH", f"/rest/v1/booking_notifications?id=eq.{n['id']}",
               {"status": "failed", "error": str(e)[:500]},
               headers={"Prefer": "return=minimal"})
            log(f"  FAILED {label}: {e}")
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
