#!/usr/bin/env bash
# Sets up and deploys the sunmoon-outreach sender on a fresh machine.
# Safe to re-run. Run it from anywhere:  bash workers/setup-outreach.sh
set -euo pipefail

cd "$(dirname "$0")/sunmoon-outreach"

echo "==> Checking prerequisites"
if ! command -v node >/dev/null 2>&1; then
  echo "node is not installed. Install it first:  brew install node"
  exit 1
fi
echo "    node $(node --version)"

if ! command -v wrangler >/dev/null 2>&1; then
  echo "    wrangler not found; using npx wrangler"
  WRANGLER="npx --yes wrangler@latest"
else
  WRANGLER="wrangler"
fi

echo
echo "==> Cloudflare login (opens a browser; skipped if already logged in)"
$WRANGLER whoami >/dev/null 2>&1 || $WRANGLER login

echo
echo "==> Setting secrets"
echo "    You will be prompted once per secret. Paste the value, press Enter."
echo "    Leave a value blank and press Enter to skip one you have already set."
for s in SUPABASE_URL SUPABASE_SERVICE_KEY RESEND_API_KEY ADMIN_TOKEN; do
  printf '\n--- %s ---\n' "$s"
  read -r -p "Set $s now? [y/N] " yn
  case "$yn" in
    [Yy]*) $WRANGLER secret put "$s" ;;
    *)     echo "    skipped $s" ;;
  esac
done

echo
echo "==> Deploying (DRY_RUN is currently: $(grep -E '^DRY_RUN' wrangler.toml | cut -d'"' -f2))"
$WRANGLER deploy

cat <<'NEXT'

==> Deployed.

The deploy output above prints your worker URL. It looks like:
    https://sunmoon-outreach.SOMETHING.workers.dev

Copy that URL, then run a dry-run preview (no email is sent):

    export SM_URL=https://sunmoon-outreach.SOMETHING.workers.dev
    export SM_TOKEN=the-admin-token-you-just-set
    curl "$SM_URL/preview?token=$SM_TOKEN&limit=5"

That prints the next 5 recipients, their partner slugs, subject lines and
destination URLs, without sending anything.

When it looks right, flip DRY_RUN to "false" in wrangler.toml, redeploy,
then send a small real batch:

    curl -X POST "$SM_URL/run?token=$SM_TOKEN&cap=3"

Check those 3 landed in Supabase as status='sent' before letting the
weekday cron run at the full 25.
NEXT
