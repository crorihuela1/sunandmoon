# Postiz Self-Host Setup — Sun & Moon at 30a

Goal: one place to auto-post to Instagram, Facebook, and any other channel, driven by Claude via MCP. You say "schedule this week's posts," Claude does it.

Architecture: Postiz (Next.js app + Postgres + Redis + Temporal) in Docker on a small VPS, behind Caddy for automatic HTTPS, on a subdomain like `social.sunandmoonat30a.com` via Cloudflare DNS.

Verified against docs.postiz.com, July 2026. Postiz v2.12+ requires Temporal — always use the official compose repo rather than a copied snapshot.

---

## 1. Provision the VPS (~15 min)

Minimum tested spec: Ubuntu 24.04, 2 GB RAM, 2 vCPUs. Hetzner CX22 (~€4/mo) or DigitalOcean basic droplet ($6/mo) both work.

```bash
# On the fresh server:
apt update && apt upgrade -y
curl -fsSL https://get.docker.com | sh

# Basic hardening
ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw enable
```

## 2. DNS (Cloudflare)

Add an A record: `social` → your VPS IP. Start with the orange cloud OFF (DNS only) until Caddy has issued its TLS cert, then you can turn proxying on if you want. If you proxy through Cloudflare, set SSL mode to "Full (strict)".

## 3. Install Postiz

```bash
git clone https://github.com/gitroomhq/postiz-docker-compose
cd postiz-docker-compose
```

Copy `postiz.env.example` (in this folder) to the server as `postiz.env`, fill in the values, and wire it in per the repo's README (env file mounted at `/config`, or merge the variables into `docker-compose.yaml`). Key values:

- `MAIN_URL` / `FRONTEND_URL` = `https://social.sunandmoonat30a.com`
- `NEXT_PUBLIC_BACKEND_URL` = `https://social.sunandmoonat30a.com/api`
- `JWT_SECRET` = `openssl rand -base64 32`

Then:

```bash
docker compose up -d
```

Frontend comes up on port 4007 (Temporal dashboard on 8080 — don't expose either publicly).

Changed a variable? `docker compose down && docker compose up -d` — restart alone doesn't pick up env changes.

## 4. Reverse proxy (Caddy — easiest TLS)

```bash
apt install -y caddy
```

`/etc/caddy/Caddyfile`:

```
social.sunandmoonat30a.com {
    reverse_proxy localhost:4007
}
```

```bash
systemctl reload caddy
```

Caddy handles the TLS cert automatically. (Postiz docs also cover Nginx and Traefik if you prefer: docs.postiz.com/reverse-proxies/caddy.)

Now open `https://social.sunandmoonat30a.com`, create your account, then set `DISALLOW_REGISTRATION=true` and recreate the containers.

## 5. Meta app for Instagram + Facebook (~30 min, the fiddly part)

One Meta app covers both platforms. Prereq: the Sun & Moon Instagram account must be a **Professional (Business/Creator)** account linked to your Facebook Page.

1. Go to [developers.facebook.com/apps](https://developers.facebook.com/apps/) → Create App → type **Other** → **Business** → attach your business portfolio.
2. In the app, set up **Facebook Login for Business**.
3. Add the OAuth redirect URI:
   `https://social.sunandmoonat30a.com/integrations/social/instagram`
   (and `/integrations/social/facebook` for the Facebook channel).
4. Under Advanced permissions, request: `instagram_basic`, `pages_show_list`, `pages_read_engagement`, `business_management`, `instagram_content_publish`, `instagram_manage_comments`, `instagram_manage_insights`.
5. Copy App ID + App Secret from App settings → Basic into `postiz.env` (`FACEBOOK_APP_ID` / `FACEBOOK_APP_SECRET`), recreate containers.
6. In Postiz: Add Channel → Instagram → complete the OAuth flow.

**Skipping Meta app review:** since only *your own* accounts will post, you don't need public app review. Add your Instagram handle under App Roles → Add People → **Instagram Tester**, then accept the invite in Instagram → Settings → Apps and Websites. If you hit "Insufficient developer role," this is the fix.

What works on IG via API: feed posts (single, carousel, video/Reels) and Stories. What doesn't: story link stickers/swipe-up (API limitation, any tool).

## 6. Connect Claude via MCP

In Postiz: **Settings → Developers → Public API** → copy your API key.

Then add the connector in the Claude desktop app (Settings → Connectors → Add custom connector) with URL:

```
https://social.sunandmoonat30a.com/api/mcp/your-api-key
```

(Self-hosted MCP endpoint = your `NEXT_PUBLIC_BACKEND_URL` + `/mcp/<key>`. Transport: streamable HTTP.)

Test: ask Claude "list my connected social media accounts." From then on: "Draft 3 IG posts about the Golden Sun pool for this weekend, schedule Thu/Fri/Sat 6pm" works end-to-end.

## 7. Maintenance

- **Updates:** `cd postiz-docker-compose && git pull && docker compose pull && docker compose up -d`. Check the migration guide before major version jumps (v2.11→v2.12 required a Temporal migration).
- **Backups:** the Postgres volume is the state. `docker compose exec postiz-postgres pg_dump -U postiz-user postiz-db-local > backup.sql` on a cron (check actual user/db names in the compose file).
- **Media storage:** local disk by default; switch to Cloudflare R2 (you already have the account) via the R2 vars in the env template — keeps the VPS disk small and media on CDN.
- **Token expiry:** Meta tokens are long-lived (~60 days) and Postiz refreshes them, but if a channel shows "disconnected," re-auth from the Postiz UI.

## Cost summary

VPS ~$5–6/mo + domain you already own. Everything else free (AGPL). Compare: Buffer/Later at similar feature level ≈ $25–60/mo.

## Docs

- Install: https://docs.postiz.com/installation/docker-compose
- Instagram provider: https://docs.postiz.com/providers/instagram
- MCP setup: https://docs.postiz.com/mcp/setup
- Config reference: https://docs.postiz.com/configuration/reference
- Self-host gotchas: https://docs.postiz.com/troubleshooting/self-host
