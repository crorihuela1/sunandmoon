#!/bin/bash
#
# run-dashboard.sh — start the Referral OS dashboard at http://localhost:8501
#
# One-time setup (already done):
#     python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
export DATABASE_URL="$(grep '^DATABASE_URL=' "$DIR/../.env" | cut -d= -f2-)"

exec "$DIR/.venv/bin/streamlit" run "$DIR/streamlit_app.py" \
  --server.port 8501 --server.headless true
