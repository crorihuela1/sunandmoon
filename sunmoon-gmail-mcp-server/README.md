# sunmoon-gmail-mcp-server

Gives Claude Desktop / Claude Code direct visibility into the **Sun & Moon 30A** Google Workspace inbox — search, read, label/triage, draft, and **guardrailed automatic sending** — while your personal Gmail stays on the built-in connector. Two accounts, two connectors, zero collisions.

## How sending is governed ("overlap" model)

| Situation | Behavior |
|---|---|
| Recipient on allowlist (`guardrails.json`) — e.g. VRN | Sends automatically |
| Reply within an existing thread (guest already emailed you) | Sends automatically |
| New outbound to unknown address | **Blocked** → Claude drafts it, or asks you and retries with `owner_approved: true` |
| Body mentions money movement / credentials / refunds | **Blocked** regardless of recipient until you approve |
| More than `maxSendsPerHour` sends in an hour | **Blocked** (default 8) |

Every send is appended to `logs/send-audit.jsonl`. Ask Claude "what did you send this week?" anytime — that's your oversight loop.

---

## Setup (~20 minutes)

### 1. Google Cloud (do this signed in as the Sun & Moon Workspace admin)

1. Go to [console.cloud.google.com](https://console.cloud.google.com) → create project `sunmoon-gmail-mcp`.
2. **APIs & Services → Library** → search "Gmail API" → **Enable**.
3. **APIs & Services → OAuth consent screen**:
   - User type: **Internal** ← this is the key Workspace advantage. No Google verification review, no 7-day token expiry, tokens persist indefinitely.
   - App name: `Sun & Moon Claude Bridge`. Fill required contact fields. Save.
4. **APIs & Services → Credentials → Create Credentials → OAuth client ID**:
   - Application type: **Desktop app**, name it anything.
   - Download the JSON → save it as `credentials.json` in this project's root folder.

> If the consent screen won't offer "Internal", the account isn't Workspace — use "External", add the Sun & Moon address as a test user, and expect to re-auth every 7 days until you publish the app.

### 2. Build and authorize (on your Mac/PC)

```bash
cd sunmoon-gmail-mcp-server
npm install
npm run build
npm run auth   # browser opens → sign in with the SUN & MOON account → approve
```

`token.json` is written next to `credentials.json`. Done — the refresh token renews itself automatically from here.

⚠️ **`credentials.json` and `token.json` are the keys to the inbox.** They're git-ignored; never commit or share them.

### 3. Register the server

**Claude Code** — from any project directory:
```bash
claude mcp add sunmoon-gmail -- node /ABSOLUTE/PATH/TO/sunmoon-gmail-mcp-server/dist/index.js
```
(Add `-s user` to make it available in every project.)

**Claude Desktop** — add to `claude_desktop_config.json` (Settings → Developer → Edit Config):
```json
{
  "mcpServers": {
    "sunmoon-gmail": {
      "command": "node",
      "args": ["/ABSOLUTE/PATH/TO/sunmoon-gmail-mcp-server/dist/index.js"]
    }
  }
}
```
Restart Claude Desktop. You should see 9 `gmail_*` tools under `sunmoon-gmail`.

### 4. Install the skill (Claude Code)

```bash
mkdir -p ~/.claude/skills
cp -r skills/sunmoon-email ~/.claude/skills/
```
The skill encodes your triage rules (direct-booking leads first), reply voice, and the approval workflow. Edit it as the business evolves.

### 5. Tune the guardrails

Edit `guardrails.json`:
- `allowlist` — pre-filled with `*@vacayrentalnetwork.com` and `*@sunandmoon30a.com`. Add specific addresses (e.g. Mindy's actual address) as you confirm them.
- `maxSendsPerHour` — default 8.
- `blockedKeywords` — anything here forces owner approval. Pre-filled with payment/credential terms and `full refund` / `discount code`.

Changes take effect on the next tool call — no restart needed.

---

## Test drive

1. "Search the Sun & Moon inbox for unread emails from the last 7 days and triage them."
2. "Read thread `<id>` and draft a reply."
3. "Reply to Mindy's last email confirming the webhook config" → should auto-send (allowlist) and appear in the audit log.
4. "Email test-stranger@example.com hello" → should be **blocked** with instructions. That's the guardrail working.
5. "What have you sent this week?" → audit summary.

## Tools reference

| Tool | Guardrailed | Purpose |
|---|---|---|
| `gmail_search_emails` | — | Gmail query syntax search |
| `gmail_read_email` / `gmail_read_thread` | — | Full bodies / full conversations |
| `gmail_list_labels` / `gmail_modify_labels` | trash disabled | Triage labeling, mark read |
| `gmail_create_draft` | — | Safe default; auto-threads replies |
| `gmail_send_email` | ✅ | Direct send, auto-threads replies |
| `gmail_send_draft` | ✅ | Send a reviewed draft |
| `gmail_get_send_audit` | — | Oversight: what was sent, when, which tier |

## Troubleshooting

- **`invalid_grant` / token errors** → delete `token.json`, run `npm run auth` again.
- **`insufficientPermissions`** → the token was minted with old scopes; delete `token.json`, re-auth.
- **Tools missing in Claude** → check the absolute path in your config; run `node dist/index.js` manually and look for `running on stdio`.
- **Want to change scopes later** → edit `SCOPES` in `src/gmail.ts`, rebuild, re-auth.
