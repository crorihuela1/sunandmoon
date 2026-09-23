"""
Operator dashboard — Referral OS

Run locally:
    streamlit run dashboard/streamlit_app.py

Deploy free:
    Push to GitHub, connect at share.streamlit.io. Set DATABASE_URL secret.

What it shows:
    * Partner heat-map (recent engagement, per segment)
    * Engagement timeline (clicks, opens, visits over time)
    * Segment funnel (prospect → contacted → engaged → partner)
    * Enrichment health (% complete, stale records)
    * Referrals → revenue (the money)
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import pandas as pd
import psycopg
import streamlit as st

st.set_page_config(page_title="Referral OS — Sun & Moon 30A",
                   page_icon="🌙", layout="wide")

# ---------------------------------------------------------------
# DB
# ---------------------------------------------------------------

def _database_url() -> str:
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    # fall back to referral-os/.env so `streamlit run` works with no env setup
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    with open(env_path) as f:
        for line in f:
            if line.startswith("DATABASE_URL="):
                return line.partition("=")[2].strip()
    raise RuntimeError("DATABASE_URL not set and not found in referral-os/.env")

@st.cache_resource
def get_conn():
    return psycopg.connect(_database_url())

@st.cache_data(ttl=60)
def query(sql: str, params: tuple = ()):
    with get_conn().cursor() as cur:
        cur.execute(sql, params)
        cols = [d.name for d in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=cols)

# ---------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------

st.sidebar.title("🌙 Referral OS")

projects = query("SELECT id, name, slug FROM projects ORDER BY (slug <> 'sun-moon-30a'), name")
if projects.empty:
    st.error("No projects yet. Run schema/002_seed.sql.")
    st.stop()
project = st.sidebar.selectbox("Project", projects["name"])
project_id = projects.loc[projects["name"] == project, "id"].iloc[0]

date_range = st.sidebar.selectbox("Window", ["7 days", "30 days", "90 days", "All time"], index=1)
days = {"7 days": 7, "30 days": 30, "90 days": 90, "All time": 3650}[date_range]

st.title(f"{project} — referral partner OS")
st.caption(f"Window: last {date_range.lower()}")

# ---------------------------------------------------------------
# Top-line KPIs
# ---------------------------------------------------------------

kpis = query("""
    SELECT
      (SELECT COUNT(*) FROM companies c
        JOIN company_segments cs ON cs.company_id = c.id
        JOIN segments s ON s.id = cs.segment_id
        WHERE s.project_id = %(pid)s) AS total_companies,
      (SELECT COUNT(*) FROM contacts ct
        JOIN companies c ON c.id = ct.company_id
        JOIN company_segments cs ON cs.company_id = c.id
        JOIN segments s ON s.id = cs.segment_id
        WHERE s.project_id = %(pid)s) AS total_contacts,
      (SELECT COUNT(*) FROM company_segments cs
        JOIN segments s ON s.id = cs.segment_id
        WHERE s.project_id = %(pid)s AND cs.status='partner') AS partners,
      (SELECT COUNT(*) FROM tracking_events
        WHERE project_id = %(pid)s
          AND occurred_at > now() - %(d)s::interval) AS events_window,
      (SELECT COALESCE(SUM(booking_value_usd),0) FROM referrals
        WHERE project_id=%(pid)s
          AND created_at > now() - %(d)s::interval) AS rev_window
""", {"pid": project_id, "d": f"{days} days"})

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Companies", int(kpis.total_companies[0]))
c2.metric("Contacts", int(kpis.total_contacts[0]))
c3.metric("Active partners", int(kpis.partners[0]))
c4.metric(f"Events / {days}d", int(kpis.events_window[0]))
c5.metric(f"Referral revenue / {days}d", f"${kpis.rev_window[0]:,.0f}")

st.divider()

# ---------------------------------------------------------------
# Engagement timeline
# ---------------------------------------------------------------

st.subheader("Engagement timeline")
timeline = query("""
    SELECT date_trunc('day', occurred_at)::date AS day,
           event_type,
           COUNT(*) AS n
    FROM tracking_events
    WHERE project_id = %(pid)s
      AND occurred_at > now() - %(d)s::interval
    GROUP BY 1, 2
    ORDER BY 1
""", {"pid": project_id, "d": f"{days} days"})

if timeline.empty:
    st.info("No events yet. Mint some tracking links and start campaigns.")
else:
    pivot = timeline.pivot(index="day", columns="event_type", values="n").fillna(0)
    st.bar_chart(pivot, height=250)

# ---------------------------------------------------------------
# Inquiries — website contact form + inbound email
# ---------------------------------------------------------------

st.subheader("Inquiries — contact form & inbox")

inq_kpis = query("""
    SELECT
      COUNT(*) FILTER (WHERE status NOT IN ('spam','not_inquiry'))          AS inquiries,
      COUNT(*) FILTER (WHERE channel = 'website_form')                       AS from_website,
      COUNT(*) FILTER (WHERE channel = 'email'
                         AND status NOT IN ('spam','not_inquiry'))           AS from_email,
      COUNT(*) FILTER (WHERE draft_prepared_at IS NOT NULL)                  AS drafts_prepared,
      COUNT(*) FILTER (WHERE status IN ('spam','not_inquiry'))               AS filtered_out
    FROM inquiries
    WHERE project_id = %(pid)s
      AND created_at > now() - %(d)s::interval
""", {"pid": project_id, "d": f"{days} days"})

i1, i2, i3, i4, i5 = st.columns(5)
i1.metric(f"Inquiries / {days}d", int(inq_kpis.inquiries[0]))
i2.metric("From website form", int(inq_kpis.from_website[0]))
i3.metric("From email", int(inq_kpis.from_email[0]))
i4.metric("Reply drafts prepared", int(inq_kpis.drafts_prepared[0]))
i5.metric("Filtered (spam/other)", int(inq_kpis.filtered_out[0]))

recent_inq = query("""
    SELECT created_at, channel, status, sender_name, sender_email,
           property_interest, check_in, check_out, party_size,
           LEFT(COALESCE(message, ''), 120) AS message_preview
    FROM inquiries
    WHERE project_id = %(pid)s
      AND created_at > now() - %(d)s::interval
    ORDER BY created_at DESC
    LIMIT 50
""", {"pid": project_id, "d": f"{days} days"})

if recent_inq.empty:
    st.info("No inquiries in this window yet. The contact form and the "
            "inquiry-agent (inquiry-agent/config.json → enabled) both feed this table.")
else:
    st.dataframe(recent_inq, use_container_width=True)

st.divider()

# ---------------------------------------------------------------
# Heat map
# ---------------------------------------------------------------

st.subheader("Partner heat-map — who's paying attention")

heat = query("""
    SELECT company_name, segment_slug, partner_status, partner_tier,
           events_7d, events_30d, clicks_30d, opens_30d, visits_30d,
           last_event_at
    FROM v_partner_heat
    WHERE company_id IN (
      SELECT cs.company_id FROM company_segments cs
      JOIN segments s ON s.id = cs.segment_id
      WHERE s.project_id = %(pid)s
    )
    ORDER BY events_30d DESC NULLS LAST, last_event_at DESC NULLS LAST
    LIMIT 100
""", {"pid": project_id})

st.dataframe(
    heat,
    use_container_width=True,
    column_config={
        "events_7d":  st.column_config.ProgressColumn("Events 7d",  min_value=0, max_value=int(heat.events_7d.max()  or 1)),
        "events_30d": st.column_config.ProgressColumn("Events 30d", min_value=0, max_value=int(heat.events_30d.max() or 1)),
    },
)

# ---------------------------------------------------------------
# Segment funnel
# ---------------------------------------------------------------

st.subheader("Segment funnel")
funnel = query("""
    SELECT segment_slug, segment_name, priority_tier, target_count,
           total_companies, contacted, engaged, partners,
           referrals_count, referral_revenue_usd
    FROM v_segment_performance
    WHERE segment_id IN (SELECT id FROM segments WHERE project_id=%(pid)s)
    ORDER BY priority_tier, segment_slug
""", {"pid": project_id})

st.dataframe(funnel, use_container_width=True,
             column_config={
                 "referral_revenue_usd": st.column_config.NumberColumn("Revenue", format="$%,.0f"),
             })

# ---------------------------------------------------------------
# Data health
# ---------------------------------------------------------------

st.subheader("Enrichment health")
h1, h2 = st.columns(2)
with h1:
    score_dist = query("""
        SELECT
          CASE
            WHEN enrichment_score IS NULL THEN 'unenriched'
            WHEN enrichment_score < 40 THEN '0-39 (poor)'
            WHEN enrichment_score < 70 THEN '40-69 (ok)'
            ELSE '70-100 (good)'
          END AS bucket,
          COUNT(*) AS n
        FROM companies
        GROUP BY 1
    """)
    st.bar_chart(score_dist.set_index("bucket"))
with h2:
    stale = query("""
        SELECT COUNT(*) AS n
        FROM companies
        WHERE last_enriched_at IS NULL
           OR last_enriched_at < now() - interval '30 days'
    """)
    st.metric("Records needing enrichment", int(stale.n[0]))
    st.caption("Run `python -m enrichment.pipeline enrich-stale --age-days 30`")
