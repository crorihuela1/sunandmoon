"""
Pipeline orchestrator.

Reads segments.yaml -> for each segment, calls ZoomInfo build_list ->
upserts companies into Postgres -> for top-priority segments, pulls
decision-maker contacts -> runs waterfall enrichment on anything stale.

CLI:
    python -m enrichment.pipeline build-anchor-list --limit 50      # smoke test
    python -m enrichment.pipeline build-anchor-list                 # full 2k
    python -m enrichment.pipeline enrich-stale --age-days 30
    python -m enrichment.pipeline pull-contacts --segment wedding-planner-local

The DB layer uses psycopg (v3). Connection string from $DATABASE_URL.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import yaml
import psycopg
from psycopg.rows import dict_row

from .zoominfo_client import BuildListQuery, ZoomInfoMCPClient, ZoomInfoHTTPClient
from .waterfall import EnrichmentWaterfall, ZoomInfoProvider, ApolloProvider, HunterProvider
from .scoring import data_completeness_score, relevance_score

log = logging.getLogger("pipeline")

CONFIG_PATH = Path(__file__).parent / "segments.yaml"


# =====================================================================
# Loading
# =====================================================================

def load_segments_config() -> dict:
    with CONFIG_PATH.open() as f:
        return yaml.safe_load(f)


def get_db() -> psycopg.Connection:
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row, autocommit=False)


def get_zoominfo_client() -> ZoomInfoMCPClient | ZoomInfoHTTPClient:
    """
    Two construction paths:
      - When MCP runtime callables are exported (env var ZI_VIA_MCP=1),
        the caller injects them. For pure-Python runs we use HTTP.
    """
    if os.getenv("ZI_VIA_MCP") == "1":
        # In Cowork sessions, the MCP runtime is responsible for injecting
        # tool-call callables. Stub here — real injection happens in
        # cli_cowork.py (see docs/mcp-injection.md).
        raise RuntimeError("MCP-mode requires runtime injection — use cli_cowork.py")
    return ZoomInfoHTTPClient()


def get_waterfall(zi_client) -> EnrichmentWaterfall:
    providers = [ZoomInfoProvider(zi_client)]
    if os.getenv("APOLLO_API_KEY"):
        providers.append(ApolloProvider(api_key=os.environ["APOLLO_API_KEY"]))
    if os.getenv("HUNTER_API_KEY"):
        providers.append(HunterProvider(api_key=os.environ["HUNTER_API_KEY"]))
    return EnrichmentWaterfall(providers)


# =====================================================================
# DB helpers
# =====================================================================

def get_project_id(conn, slug: str) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM projects WHERE slug=%s", (slug,))
        row = cur.fetchone()
        if not row:
            raise RuntimeError(f"Project {slug} not found — run schema/002_seed.sql")
        return row["id"]


def get_segments(conn, project_id: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, slug, name, priority_tier, target_count, zoominfo_query
            FROM segments
            WHERE project_id=%s
            ORDER BY priority_tier, slug
        """, (project_id,))
        return cur.fetchall()


def upsert_company(conn, fields: dict) -> str:
    """Insert by zoominfo_company_id OR domain; on conflict update fields
    only if currently NULL. Returns company UUID."""
    cols = [
        "zoominfo_company_id", "apollo_organization_id", "name", "domain",
        "description", "industry", "sub_industry", "employee_count",
        "revenue_estimate_usd", "founded_year",
        "address_line1", "city", "state", "postal_code", "country",
        "latitude", "longitude", "main_phone", "main_email", "website",
        "linkedin_url", "facebook_url", "instagram_handle", "tiktok_handle",
        "data_sources",
    ]
    values = [fields.get(c) for c in cols]
    # data_sources is JSONB — provide list of source names
    if fields.get("data_sources"):
        values[cols.index("data_sources")] = json.dumps(fields["data_sources"])
    else:
        values[cols.index("data_sources")] = json.dumps([])

    placeholders = ", ".join(["%s"] * len(cols))
    update_clause = ", ".join([f"{c} = COALESCE(companies.{c}, EXCLUDED.{c})" for c in cols if c != "data_sources"])
    sql = f"""
        INSERT INTO companies ({", ".join(cols)})
        VALUES ({placeholders})
        ON CONFLICT (zoominfo_company_id) DO UPDATE SET
          {update_clause},
          data_sources = (
            SELECT jsonb_agg(DISTINCT x)
            FROM jsonb_array_elements(companies.data_sources || EXCLUDED.data_sources) AS x
          ),
          updated_at = now()
        RETURNING id
    """
    with conn.cursor() as cur:
        cur.execute(sql, values)
        return cur.fetchone()["id"]


def link_company_segment(conn, company_id: str, segment_id: str, *, relevance_score: int):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO company_segments (company_id, segment_id, relevance_score, status)
            VALUES (%s, %s, %s, 'prospect')
            ON CONFLICT (company_id, segment_id) DO UPDATE
              SET relevance_score = GREATEST(company_segments.relevance_score, EXCLUDED.relevance_score)
        """, (company_id, segment_id, relevance_score))


def log_enrichment_run(conn, *, entity_type: str, entity_id: str, source: str,
                       operation: str, request: dict, response: dict | list,
                       fields_updated: list[str], succeeded: bool,
                       error: str | None = None, cost_usd: float = 0.0):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO enrichment_runs
              (entity_type, entity_id, source, operation,
               request_payload, response_payload, fields_updated,
               succeeded, error_message, cost_usd)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (entity_type, entity_id, source, operation,
              json.dumps(request), json.dumps(response, default=str),
              fields_updated, succeeded, error, cost_usd))


# =====================================================================
# Commands
# =====================================================================

def cmd_build_anchor_list(args):
    """Pull companies into the DB for every segment, up to its target_count."""
    cfg = load_segments_config()
    zi  = get_zoominfo_client()

    with get_db() as conn:
        project_id = get_project_id(conn, cfg["project"])
        segments_db = get_segments(conn, project_id)
        by_slug = {s["slug"]: s for s in segments_db}

        for s_yaml in cfg["segments"]:
            slug = s_yaml["slug"]
            if args.only_segments and slug not in args.only_segments:
                continue
            seg = by_slug.get(slug)
            if not seg:
                log.warning("segment %s not in DB — run seed.sql first", slug)
                continue

            cap = min(args.limit or seg["target_count"], seg["target_count"])
            query_dict = s_yaml["zoominfo_query"]
            q = BuildListQuery(
                industries   = query_dict.get("industries", []),
                keywords     = query_dict.get("keywords", []),
                states       = query_dict.get("states", []),
                metros       = query_dict.get("metros", []),
                employee_min = query_dict.get("employee_min"),
                employee_max = query_dict.get("employee_max"),
                max_results  = cap,
            )
            log.info("segment=%s cap=%d", slug, cap)
            companies = zi.build_company_list(q)
            log.info("  -> %d returned", len(companies))

            for raw in companies:
                fields = _zi_search_row_to_fields(raw)
                if not fields.get("name"):
                    continue
                fields["data_sources"] = ["zoominfo"]
                company_id = upsert_company(conn, fields)
                score = relevance_score(fields, s_yaml)
                link_company_segment(conn, company_id, seg["id"], relevance_score=score)
                log_enrichment_run(
                    conn,
                    entity_type="company", entity_id=company_id,
                    source="zoominfo", operation="search",
                    request=q.to_payload(), response={"matched": True},
                    fields_updated=list(fields.keys()),
                    succeeded=True,
                )
            conn.commit()

    log.info("anchor list build complete")


def cmd_enrich_stale(args):
    """Re-enrich anything older than --age-days via the waterfall."""
    zi = get_zoominfo_client()
    waterfall = get_waterfall(zi)
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.age_days)

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, name, domain, zoominfo_company_id
                FROM companies
                WHERE last_enriched_at IS NULL OR last_enriched_at < %s
                ORDER BY last_enriched_at NULLS FIRST
                LIMIT %s
            """, (cutoff, args.limit))
            rows = cur.fetchall()
        log.info("enriching %d stale companies", len(rows))
        for row in rows:
            base = {k: v for k, v in row.items() if v}
            final: dict[str, Any] = {}
            for prov, fields, err in waterfall.enrich_company(base):
                log_enrichment_run(
                    conn,
                    entity_type="company", entity_id=row["id"],
                    source=prov, operation="enrich",
                    request={"name": row["name"]}, response={"fields": fields},
                    fields_updated=fields, succeeded=(err is None),
                    error=err,
                )
            # Re-fetch the merged dict from waterfall by running once more, capturing
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE companies SET last_enriched_at = now(),
                           enrichment_score = %s
                    WHERE id = %s
                """, (data_completeness_score(base | final), row["id"]))
            conn.commit()


# =====================================================================
# helpers
# =====================================================================

def _zi_search_row_to_fields(raw: dict) -> dict:
    """Translate a ZoomInfo /search row into our DB fields. Search results
    are sparser than /enrich; use what's present, leave the rest for the
    enrichment pass."""
    addr = raw.get("address") if isinstance(raw.get("address"), dict) else {}
    return {k: v for k, v in {
        "zoominfo_company_id": raw.get("id") or raw.get("companyId"),
        "name":     raw.get("name") or raw.get("companyName"),
        "domain":   raw.get("domain") or raw.get("website"),
        "industry": raw.get("industry"),
        "employee_count": raw.get("employeeCount") or raw.get("employees"),
        "city":     addr.get("city")  or raw.get("city"),
        "state":    addr.get("state") or raw.get("state"),
        "postal_code": addr.get("zip") or raw.get("zip"),
        "main_phone": raw.get("phone"),
        "website":  raw.get("website"),
    }.items() if v}


# =====================================================================
# CLI
# =====================================================================

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="command", required=True)

    pb = sub.add_parser("build-anchor-list",
                        help="Pull companies into DB for each segment")
    pb.add_argument("--limit", type=int, default=None,
                    help="Cap per segment (smoke test). Default = segment target_count.")
    pb.add_argument("--only-segments", nargs="+", default=None,
                    help="Only process these segment slugs.")
    pb.set_defaults(func=cmd_build_anchor_list)

    pe = sub.add_parser("enrich-stale",
                        help="Re-enrich companies older than --age-days")
    pe.add_argument("--age-days", type=int, default=30)
    pe.add_argument("--limit", type=int, default=200)
    pe.set_defaults(func=cmd_enrich_stale)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
