#!/bin/bash
#
# install-autodeploy.sh — Schedule deploy.py to run automatically every day.
#
# This installs a macOS launchd "user agent" that runs:
#     python3 deploy.py --skip-unchanged
# once a day, from this web/ folder. Because of --skip-unchanged, it only
# creates a new Cloudflare deployment when the site content has actually
# changed (e.g. after the events calendar is refreshed).
#
# USAGE (run once, from inside the web/ folder):
#     bash install-autodeploy.sh            # install / re-install (default: 12:30 PM)
#     bash install-autodeploy.sh 8 15       # run daily at 8:15 AM instead
#     bash install-autodeploy.sh --uninstall
#
set -euo pipefail

LABEL="com.sunandmoon30a.deploy"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---- uninstall path ---------------------------------------------------------
if [[ "${1:-}" == "--uninstall" ]]; then
  launchctl unload -w "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Auto-deploy removed."
  exit 0
fi

# ---- pick run time (defaults to 12:30 PM) -----------------------------------
HOUR="${1:-12}"
MINUTE="${2:-30}"

# ---- sanity checks ----------------------------------------------------------
PYTHON="$(command -v python3 || true)"
if [[ -z "$PYTHON" ]]; then
  echo "ERROR: python3 not found on PATH. Install Python 3 first." >&2
  exit 1
fi
if [[ ! -f "$SCRIPT_DIR/deploy.py" ]]; then
  echo "ERROR: deploy.py not found next to this installer ($SCRIPT_DIR)." >&2
  exit 1
fi
if [[ ! -f "$SCRIPT_DIR/deploy.config.json" ]]; then
  echo "WARNING: deploy.config.json not found. Copy deploy.config.example.json"
  echo "         to deploy.config.json and add your Cloudflare token/account ID,"
  echo "         or the scheduled job will fail. Installing anyway."
fi
# Confirm dependencies are importable by this python3.
if ! "$PYTHON" -c "import requests, blake3" 2>/dev/null; then
  echo "WARNING: this python3 is missing 'requests' and/or 'blake3'."
  echo "         Run:  $PYTHON -m pip install requests blake3"
fi

# ---- write the launchd plist ------------------------------------------------
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON}</string>
        <string>${SCRIPT_DIR}/deploy.py</string>
        <string>--skip-unchanged</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${SCRIPT_DIR}</string>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key><integer>${HOUR}</integer>
        <key>Minute</key><integer>${MINUTE}</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>${SCRIPT_DIR}/deploy.log</string>
    <key>StandardErrorPath</key>
    <string>${SCRIPT_DIR}/deploy.log</string>
</dict>
</plist>
PLISTEOF

# ---- (re)load it ------------------------------------------------------------
launchctl unload -w "$PLIST" 2>/dev/null || true
launchctl load -w "$PLIST"

printf "Auto-deploy installed.\n"
printf "  Runs daily at %02d:%02d via launchd (%s)\n" "$HOUR" "$MINUTE" "$LABEL"
printf "  Log file: %s/deploy.log\n" "$SCRIPT_DIR"
printf "\nTest it right now without waiting for the schedule:\n"
printf "  launchctl start %s && sleep 3 && cat \"%s/deploy.log\"\n" "$LABEL" "$SCRIPT_DIR"
printf "\nRemove it later with:\n  bash install-autodeploy.sh --uninstall\n"
