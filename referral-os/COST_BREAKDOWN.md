# Monthly cost breakdown — $200-300 cap

Target: **$200-300/mo all-in**, leaving headroom for ad-hoc enrichment.

## Recommended stack — $211/mo at steady state

| Line item | Tier | Monthly | Why this one |
|---|---|---:|---|
| **Supabase Pro** | Pro | $25 | Postgres + auth + edge functions + 8GB DB + 100GB egress. Source of truth. |
| **Airtable Team** | Team (1 seat) | $24 | Operator UI. Postgres-sync via Sequin free tier. |
| **Cloudflare Workers** | Free | $0 | 100k req/day = ~3M/mo. We'll see ~50k/mo. Pay only if you blow past. |
| **Cloudflare R2** (link backups, logs) | Free tier | $0 | 10GB free; we use < 1GB. |
| **Apollo.io** | Basic | $59 | Enrichment waterfall layer 2. Email finder + verifier. 1,200 credits/mo. |
| **Hunter.io** | Starter | $34 | Layer 3 enrichment. Email verification specifically. 1,000 verifications/mo. |
| **Postmark or Resend** | Starter | $15 | Transactional email + open/click webhooks. Better deliverability than Gmail SMTP. |
| **Sequin** (Postgres → Airtable sync) | Free | $0 | Free tier covers 1M rows. |
| **Streamlit Community Cloud** | Free | $0 | Dashboard hosting. |
| **Domain — r.sunmoon30a.com** | (already owned) | $0 | DNS only, Cloudflare. |
| **GitHub** | Free | $0 | Repo + Actions for cron. |
| **ZoomInfo** | Already paid | $0 | Existing MCP connector, no incremental cost. |
| **TOTAL** | | **$157** | Headroom: $43-143 for variable spend |

## What goes in the headroom

- **Instantly.ai** ($37/mo) for outbound email sequencing — adds to the $157 to bring you to $194. Recommended when you start the cold outreach phase.
- **RB2B** ($199/mo, has free 500-reveal tier) for anonymous visitor identification — skip at start; add when you're ready to act on it.
- **Cloudflare Workers paid** ($5/mo) — only if you exceed 100k req/day.
- **ZoomInfo overage** — only if you blow past your existing plan's monthly credits. Phase the anchor build to avoid this.

## What I deliberately cut from $500 stack

- **Clay.com** ($349/mo Starter): we replicated the waterfall + AI research in Python. You give up Clay's drag-drop UI but keep the engine.
- **Common Room / Customers.ai** ($500+/mo): social-listening; defer until partners are warm.
- **HubSpot Marketing Hub Starter** ($20/mo): nice-to-have but Airtable + Postmark + your own pipeline gives you 80% of the value. Add later if needed.

## Hard cost ceiling rules

The codebase enforces nothing — you do. To stay under $300:

1. **Phase the ZoomInfo build**. Use `--limit` flags; don't pull all 2,000 in one weekend.
2. **Cap Apollo at 1,200 credits/mo**. Pipeline auto-throttles if Apollo returns rate-limit responses.
3. **Use Hunter free tier (25 verifications/mo)** for the first month to confirm utility before upgrading to Starter.
4. **Audit `enrichment_runs.cost_usd` monthly**. The pipeline logs every call's cost — sum it.

```sql
SELECT date_trunc('month', run_at) AS month,
       source,
       COUNT(*) AS calls,
       SUM(cost_usd)::numeric(10,2) AS spend
FROM enrichment_runs
GROUP BY 1, 2 ORDER BY 1 DESC, spend DESC;
```

## Annual snapshot

| Phase | Months | Monthly | Annual |
|---|---|---:|---:|
| Bootstrap (just DB + Airtable + Cloudflare) | 1-2 | ~$50 | $100 |
| Active enrichment (add Apollo + Hunter + Postmark) | 3-12 | ~$157 | ~$1,570 |
| Plus outbound (Instantly) when warm | 4-12 | ~$194 | extra $333 |
| **First-year total** | | | **~$2,000** |

For perspective: a single tier-1 referral conversion (wedding planner sending one 7-night wedding stay) is ~$4,200 in booking revenue. The whole platform breaks even on referral #1 of year 1.
