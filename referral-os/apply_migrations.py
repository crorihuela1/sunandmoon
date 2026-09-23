#!/usr/bin/env python3
"""
APPLY MIGRATIONS — run one or more .sql files against your Supabase Postgres.

Why this exists: the "apply migrations in Supabase" step is easy to skip. This
runs them from the terminal using DATABASE_URL in your .env, so there's nothing
to copy-paste into a browser.

Usage
-----
    python3 apply_migrations.py schema/008_30a_restaurants_seed.sql \
                                schema/009_restaurant_views_generic.sql

    # apply everything new from this work, in order:
    python3 apply_migrations.py schema/006_atlanta_restaurants_seed.sql \
                                schema/007_restaurant_views.sql \
                                schema/008_30a_restaurants_seed.sql \
                                schema/009_restaurant_views_generic.sql

Notes
-----
- Idempotent migrations (these use ON CONFLICT / CREATE OR REPLACE) are safe to
  re-run.
- Needs psycopg (you already have it — the dashboard/pipeline use it). If not:
      pip3 install "psycopg[binary]" --break-system-packages
- For files containing $$-quoted function bodies (e.g. 001_init.sql), use psql or
  the Supabase SQL editor instead; this splitter is meant for the seed/view files.
"""
from __future__ import annotations

import re
import sys


def load_database_url(path: str = ".env") -> str:
    for ln in open(path):
        ln = ln.strip()
        if ln.startswith("DATABASE_URL=") and not ln.startswith("#"):
            return ln.split("=", 1)[1].strip()
    raise SystemExit("❌ DATABASE_URL not found in .env")


def split_statements(sql: str) -> list[str]:
    """Split into statements on top-level ';', correctly ignoring ';' (and '$$')
    that appear inside single-quoted strings or real $tag$ dollar-quoted blocks.
    Handles e.g. segment names like '($$)' and JSONB literals without breaking."""
    # drop full-line comments first (some contain ';')
    body = "\n".join(ln for ln in sql.splitlines() if not ln.lstrip().startswith("--"))
    stmts: list[str] = []
    buf: list[str] = []
    i, n = 0, len(body)
    in_squote = False
    dollar_tag: str | None = None
    while i < n:
        c = body[i]
        if dollar_tag:                                  # inside $tag$ ... $tag$
            if body.startswith(dollar_tag, i):
                buf.append(dollar_tag); i += len(dollar_tag); dollar_tag = None; continue
            buf.append(c); i += 1; continue
        if in_squote:                                   # inside '...'
            buf.append(c)
            if c == "'":
                if i + 1 < n and body[i + 1] == "'":    # '' = escaped quote
                    buf.append("'"); i += 2; continue
                in_squote = False
            i += 1; continue
        if c == "'":
            in_squote = True; buf.append(c); i += 1; continue
        if c == "$":                                    # possible $tag$ open
            m = re.match(r"\$[A-Za-z0-9_]*\$", body[i:])
            if m:
                dollar_tag = m.group(0); buf.append(dollar_tag); i += len(dollar_tag); continue
        if c == ";":
            stmt = "".join(buf).strip()
            if stmt:
                stmts.append(stmt)
            buf = []; i += 1; continue
        buf.append(c); i += 1
    tail = "".join(buf).strip()
    if tail:
        stmts.append(tail)
    return stmts


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__); return 1
    try:
        import psycopg
    except ImportError:
        raise SystemExit('❌ psycopg not installed. Run:\n'
                         '   pip3 install "psycopg[binary]" --break-system-packages')

    dburl = load_database_url()
    try:
        conn_cm = psycopg.connect(dburl, autocommit=True)
    except psycopg.OperationalError as e:
        if "resolve host" in str(e) or "nodename nor servname" in str(e):
            raise SystemExit(
                "❌ Can't reach the database host.\n"
                "   Supabase's DIRECT host (db.<ref>.supabase.co) is IPv6-only and\n"
                "   won't resolve on most home/office IPv4 networks.\n\n"
                "   Fix: use the POOLER connection string (IPv4-friendly).\n"
                "   Supabase → Project Settings → Database → Connection string →\n"
                "   'Session pooler'. It looks like:\n"
                "     postgresql://postgres.<ref>:<PASSWORD>@aws-0-<region>.pooler.supabase.com:5432/postgres\n"
                "   Put that in .env as DATABASE_URL and re-run.\n\n"
                "   Or skip the terminal entirely: paste schema/008 + 009 into the\n"
                "   Supabase SQL editor (see how below).")
        raise
    with conn_cm as conn:
        for path in argv:
            try:
                sql = open(path).read()
            except FileNotFoundError:
                print(f"⚠️  {path}: not found, skipping"); continue
            stmts = split_statements(sql)
            print(f"\n▶ {path}  ({len(stmts)} statements)")
            for i, stmt in enumerate(stmts, 1):
                preview = re.sub(r"\s+", " ", stmt)[:70]
                try:
                    conn.execute(stmt)
                    print(f"   ✓ {i:>2}. {preview}…")
                except Exception as e:  # noqa: BLE001
                    print(f"   ✗ {i:>2}. {preview}…\n        {e}")
                    return 1
    print("\n✅ Done. Verify, e.g.:")
    print("   psql \"$DATABASE_URL\" -c \"SELECT slug FROM projects;\"")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
