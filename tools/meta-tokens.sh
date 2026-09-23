#!/usr/bin/env bash
# Sun & Moon 30A — fetch the Meta credentials the social engine needs.
#
# Give it a user token (short-lived is fine — it exchanges it for a
# long-lived one using your App ID + App Secret, the documented method).
# It then finds the Page, the never-expiring Page token, and the Instagram
# business account id, and stores them as GitHub Actions secrets.
#
# Secrets are never printed and never written to disk: they are read with
# hidden input and piped straight into `gh secret set`. Only ids are shown.

set -uo pipefail
GRAPH="https://graph.facebook.com/v26.0"
REPO="crorihuela1/sunandmoon"

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
warn() { printf '\033[33m%s\033[0m\n' "$1"; }
ok()   { printf '\033[32m%s\033[0m\n' "$1"; }
die()  { printf '\033[31m✗ %s\033[0m\n' "$1" >&2; exit 1; }

api() { # api <path> <token> [query]
  local path="$1" token="$2"
  if [ $# -ge 3 ] && [ -n "$3" ]; then
    curl -sS -G "$GRAPH/$path" --data-urlencode "$3" -H "Authorization: Bearer $token"
  else
    curl -sS -G "$GRAPH/$path" -H "Authorization: Bearer $token"
  fi
}

check() {
  python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("Non-JSON response from Meta", file=sys.stderr); sys.exit(1)
if "error" in d:
    e = d["error"]
    print("META ERROR {}: {}".format(e.get("code"), e.get("message")), file=sys.stderr)
    sys.exit(1)
print(json.dumps(d))'
}

py() { python3 -c "$1"; }

expiry_of() {
  py '
import json, sys, datetime
d = json.load(sys.stdin)["data"]
e = d.get("expires_at", -1)
if e == 0:
    print("0|never")
else:
    dt = datetime.datetime.fromtimestamp(e, datetime.timezone.utc)
    left = dt - datetime.datetime.now(datetime.timezone.utc)
    days = left.days
    txt = "{}d".format(days) if days >= 1 else "{}h".format(round(left.total_seconds()/3600, 1))
    print("{}|{} ({} left)".format(e, dt.strftime("%Y-%m-%d %H:%M UTC"), txt))'
}

echo
bold "Sun & Moon 30A — Meta credentials"
echo
echo "Copy any USER token to the clipboard (short-lived from the Graph API"
echo "Explorer is fine — this script extends it properly)."
echo
printf 'Press Enter once copied, or type "m" to paste manually: '
read -rs MODE
echo

case "$MODE" in
  EAA*) echo "  (token detected at this prompt — using it; it was not displayed)"
        USER_TOKEN="$MODE" ;;
  m|M)  printf '  token (hidden): '; read -rs USER_TOKEN; echo ;;
  *)    USER_TOKEN=$(pbpaste 2>/dev/null | tr -d '\r\n')
        [ -n "$USER_TOKEN" ] || die "clipboard is empty — copy a token, then re-run."
        echo "  read ${#USER_TOKEN} characters from the clipboard" ;;
esac
USER_TOKEN=$(printf '%s' "$USER_TOKEN" | tr -d '[:space:]')
[ -n "${USER_TOKEN:-}" ] || die "no token entered"
case "$USER_TOKEN" in
  EAA*) ;;
  *) warn "  ⚠ does not start with 'EAA' — that may not be a Meta token." ;;
esac
echo

bold "1. User token"
UD=$(api "debug_token" "$USER_TOKEN" "input_token=$USER_TOKEN" | check) \
  || die "token rejected by Meta — generate a fresh one in the Graph API Explorer."
UEXP_RAW=$(expiry_of <<<"$UD"); UEXP="${UEXP_RAW%%|*}"
echo "   expires: ${UEXP_RAW#*|}"

# The Access Token Debugger's "Extend" button yields a long-lived TOKEN on a
# short-lived SESSION: the derived Page token reports expires_at=0, works
# immediately, then dies with OAuthException 190 subcode 463 when the session
# lapses. Only the documented fb_exchange_token exchange fixes that, so we
# always run it — it is idempotent and costs one request.
NEED_EXCHANGE=1
warn "   → exchanging via App ID + Secret regardless (the only way to get a"
warn "     Page token whose session does not expire)."
echo

if [ "$NEED_EXCHANGE" = "1" ]; then
  bold "2. Exchange for a long-lived token"
  echo "   From your app dashboard → Settings → Basic."
  echo "   (App ID is public; the secret is hidden as you type.)"
  printf '   App ID: '
  read -r APP_ID
  printf '   App Secret (hidden): '
  read -rs APP_SECRET
  echo
  [ -n "$APP_ID" ] && [ -n "$APP_SECRET" ] || die "both App ID and App Secret are required."

  EX=$(curl -sS -G "$GRAPH/oauth/access_token" \
        --data-urlencode "grant_type=fb_exchange_token" \
        --data-urlencode "client_id=$APP_ID" \
        --data-urlencode "client_secret=$APP_SECRET" \
        --data-urlencode "fb_exchange_token=$USER_TOKEN" | check) \
    || die "exchange failed — check the App ID/Secret belong to the app that issued the token."

  USER_TOKEN=$(py 'import json,sys; print(json.load(sys.stdin)["access_token"])' <<<"$EX")
  unset APP_SECRET
  UD=$(api "debug_token" "$USER_TOKEN" "input_token=$USER_TOKEN" | check) \
    || die "could not inspect the exchanged token"
  UEXP_RAW=$(expiry_of <<<"$UD"); UEXP="${UEXP_RAW%%|*}"
  echo "   new expiry: ${UEXP_RAW#*|}"
  DAE=$(py '
import json, sys, datetime
d = json.load(sys.stdin)["data"].get("data_access_expires_at", 0)
print(datetime.datetime.fromtimestamp(d, datetime.timezone.utc).strftime("%Y-%m-%d") if d else "n/a")' <<<"$UD")
  echo "   data access until: $DAE"
  NOW=$(date +%s)
  if [ "$UEXP" = "0" ] || [ $((UEXP - NOW)) -gt 604800 ]; then
    ok "   ✓ long-lived user token obtained"
  else
    die "still short-lived after exchange — stopping rather than storing a dying token."
  fi
  echo
fi

bold "3. Pages you administer"
PAGES=$(api "me/accounts" "$USER_TOKEN" "fields=id,name,access_token" | check) \
  || die "could not list Pages — token needs pages_show_list."
COUNT=$(py 'import json,sys; print(len(json.load(sys.stdin).get("data",[])))' <<<"$PAGES")
[ "$COUNT" -gt 0 ] || die "no Pages returned. The token's user must be a Page admin."

py '
import json, sys
for i, p in enumerate(json.load(sys.stdin)["data"], 1):
    print("   {}) {}  (id {})".format(i, p["name"], p["id"]))' <<<"$PAGES"
echo
if [ "$COUNT" -eq 1 ]; then
  IDX=1; echo "   Only one Page — using it."
else
  printf '   Which Page is Sun & Moon? [1-%s]: ' "$COUNT"; read -r IDX
fi
case "$IDX" in ''|*[!0-9]*) die "invalid selection: '$IDX'";; esac
[ "$IDX" -ge 1 ] && [ "$IDX" -le "$COUNT" ] || die "invalid selection: $IDX"

PAGE_ID=$(I="$IDX" py '
import json, os, sys
print(json.load(sys.stdin)["data"][int(os.environ["I"])-1]["id"])' <<<"$PAGES")
PAGE_TOKEN=$(I="$IDX" py '
import json, os, sys
print(json.load(sys.stdin)["data"][int(os.environ["I"])-1]["access_token"])' <<<"$PAGES")
ok "   ✓ FB_PAGE_ID = $PAGE_ID"
echo

bold "4. Page token"
PD=$(api "debug_token" "$PAGE_TOKEN" "input_token=$PAGE_TOKEN" | check) \
  || die "could not inspect the Page token"
PEXP_RAW=$(expiry_of <<<"$PD"); PEXP="${PEXP_RAW%%|*}"
echo "   expires: ${PEXP_RAW#*|}"
if [ "$PEXP" = "0" ]; then ok "   ✓ never expires — this is what we want"
else warn "   ⚠ this Page token still expires."; fi
SCOPES=$(py 'import json,sys; print(" ".join(json.load(sys.stdin)["data"].get("scopes",[])))' <<<"$PD")
MISSING=""
for need in pages_manage_posts pages_read_engagement instagram_content_publish instagram_basic; do
  case " $SCOPES " in *" $need "*) ;; *) MISSING="$MISSING $need";; esac
done
if [ -n "$MISSING" ]; then warn "   ⚠ missing scopes:$MISSING"; else ok "   ✓ all required scopes present"; fi
echo

bold "5. Instagram business account"
IG=$(api "$PAGE_ID" "$PAGE_TOKEN" "fields=instagram_business_account{id,username}" | check) \
  || die "could not read the Page"
IG_ID=$(py 'import json,sys; print(json.load(sys.stdin).get("instagram_business_account",{}).get("id",""))' <<<"$IG")
IG_USER=$(py 'import json,sys; print(json.load(sys.stdin).get("instagram_business_account",{}).get("username",""))' <<<"$IG")
if [ -n "$IG_ID" ]; then
  ok "   ✓ IG_BUSINESS_ACCOUNT_ID = $IG_ID  (@$IG_USER)"
  ok "   ✓ the Page ↔ Instagram link is live — Instagram publishing will work"
else
  warn "   ✗ no instagram_business_account on this Page."
  echo "     Facebook posting works; Instagram needs the Page linked at"
  echo "     Page Settings → Linked accounts → Instagram, then re-run."
fi
echo

bold "6. Store as GitHub Actions secrets ($REPO)"
if [ "$PEXP" != "0" ]; then
  die "refusing: the Page token expires, so posting would break silently. Nothing stored."
fi
printf '   store them now? [y/N]: '
read -r YN
case "$YN" in
  [yY]*)
    printf '%s' "$PAGE_TOKEN" | gh secret set META_ACCESS_TOKEN --repo "$REPO" && ok "   ✓ META_ACCESS_TOKEN"
    printf '%s' "$PAGE_ID"    | gh secret set FB_PAGE_ID --repo "$REPO" && ok "   ✓ FB_PAGE_ID"
    if [ -n "$IG_ID" ]; then
      printf '%s' "$IG_ID" | gh secret set IG_BUSINESS_ACCOUNT_ID --repo "$REPO" && ok "   ✓ IG_BUSINESS_ACCOUNT_ID"
    else
      echo "   – IG_BUSINESS_ACCOUNT_ID skipped (no link yet)"
    fi
    echo
    echo "   Verify with:  gh secret list --repo $REPO"
    ;;
  *) echo "   Skipped — nothing stored." ;;
esac

unset USER_TOKEN PAGE_TOKEN APP_SECRET
echo
