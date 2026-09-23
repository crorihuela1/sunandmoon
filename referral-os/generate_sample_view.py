#!/usr/bin/env python3
"""
Generate the "personalized view" as a self-contained HTML page.

Two modes:
  --from-db   Pull live rows from v_atlanta_restaurant_targets + v_atlanta_menu_pricing
              (use after you've run the ingest + scraper on your machine).
  (default)   Render a REPRESENTATIVE SAMPLE so you can see the exact output shape
              now, before spending a cent on the Places API. Sample data is
              illustrative (plausible Atlanta restaurants), clearly banner-flagged,
              and scored with the IDENTICAL formula as schema/007_restaurant_views.sql.

Output: atlanta_restaurants_view.html  (open in any browser)
"""
from __future__ import annotations

import argparse
import html
import json
import math
import sys
from datetime import datetime

# ------------------------------------------------------------------
# Scoring — MUST match v_atlanta_restaurant_targets in 007_restaurant_views.sql
# ------------------------------------------------------------------
def score(r: dict) -> dict:
    pt = r.get("price_tier") or 2
    rc = r.get("review_count") or 0
    rt = r.get("rating") or 0
    no_site = r.get("website") is None
    s_pay = {1: 10, 2: 22, 3: 32, 4: 35}.get(pt, 18)
    s_scale = min(30, int(math.log(rc + 1) * 6))
    if 4.0 <= rt <= 4.6:   s_quality = 20
    elif 3.6 <= rt < 4.0:  s_quality = 12
    elif rt > 4.6:         s_quality = 8
    elif rt > 0:           s_quality = 6
    else:                  s_quality = 4
    s_gap = 15 if no_site else 0
    fit = s_pay + s_scale + s_quality + s_gap
    tier = "A" if fit >= 75 else "B" if fit >= 55 else "C"
    dollars = "$" * (pt or 0)
    if no_site:
        pitch = (f"{r['name']} has {rc} Google reviews but no website — lead with the "
                 f'"you\'re invisible to anyone who searches you" gap.')
    elif 3.6 <= rt < 4.0:
        pitch = f"{r['name']} is at {rt}★ — momentum pitch: small fixes that lift rating & repeat visits."
    elif pt >= 3:
        pitch = f"{r['name']} is an upscale room ({dollars}) — premium ROI framing, owner has budget."
    elif rc >= 500:
        pitch = f"{r['name']} does real volume ({rc} reviews) — efficiency/scale pitch."
    else:
        pitch = f"{r['name']} — standard intro; lead with a local Atlanta proof point."
    return {**r, "fit_score": fit, "target_tier": tier, "pitch_angle": pitch,
            "dollars": dollars or "—",
            "components": {"pay": s_pay, "scale": s_scale, "quality": s_quality, "gap": s_gap}}


# ------------------------------------------------------------------
# Representative sample (illustrative — NOT a live Google pull)
# Plausible Atlanta restaurants spanning tiers/segments. Menus shown for 25.
# ------------------------------------------------------------------
def sample_rows() -> list[dict]:
    raw = [
        # name, segment, price_tier, rating, reviews, website?, neighborhood
        ("Bacchanalia",            "independent-fine-dining", 4, 4.6, 1180, True,  "Westside"),
        ("Lazy Betty",             "independent-fine-dining", 4, 4.7,  640, True,  "Edgewood"),
        ("Marcel",                 "independent-fine-dining", 4, 4.5,  890, True,  "Westside"),
        ("Kevin Rathbun Steak",    "independent-fine-dining", 4, 4.6, 1620, True,  "Inman Park"),
        ("Hayakawa",               "independent-fine-dining", 4, 4.8,  410, False, "Buckhead"),
        ("Miller Union",           "independent-full-service", 3, 4.6, 2230, True,  "Westside"),
        ("Staplehouse",            "independent-fine-dining", 4, 4.7,  980, True,  "Old Fourth Ward"),
        ("The Optimist",           "independent-full-service", 3, 4.5, 3410, True,  "Westside"),
        ("Gunshow",                "independent-full-service", 3, 4.5, 1870, True,  "Glenwood Park"),
        ("Fox Bros. Bar-B-Q",      "independent-full-service", 2, 4.6, 5240, True,  "Candler Park"),
        ("Home grown GA",          "cafe-bakery-brunch",       1, 4.6, 2980, True,  "Reynoldstown"),
        ("Buttermilk Kitchen",     "cafe-bakery-brunch",       2, 4.5, 2110, True,  "Buckhead"),
        ("Octane Coffee",          "cafe-bakery-brunch",       1, 4.4, 1430, True,  "Grant Park"),
        ("Little Tart Bakeshop",   "cafe-bakery-brunch",       2, 4.7,  920, True,  "Summerhill"),
        ("Banshee",                "independent-full-service", 2, 4.6,  760, True,  "East Atlanta"),
        ("Ladybird Grove",         "bar-brewery-nightlife",    2, 4.4, 2640, True,  "Old Fourth Ward"),
        ("Monday Night Brewing",   "bar-brewery-nightlife",    2, 4.7, 3120, True,  "Westside"),
        ("New Realm Brewing",      "bar-brewery-nightlife",    2, 4.6, 4010, True,  "Old Fourth Ward"),
        ("Argosy",                 "bar-brewery-nightlife",    2, 4.5, 1990, True,  "East Atlanta"),
        ("El Tesoro",              "independent-full-service", 2, 4.6,  540, False, "Edgewood"),
        ("La Casa del Sabor",      "independent-full-service", 2, 4.7,  210, False, "Buford Highway"),
        ("Pho Dai Loi #2",         "quick-service-counter",    1, 4.5,  880, False, "Buford Highway"),
        ("Hops & Hominy Counter",  "quick-service-counter",    1, 4.2,  120, True,  "West End"),
        ("Daddy D'z BBQ",          "independent-full-service", 2, 4.4, 1760, True,  "Cabbagetown"),
        ("Slutty Vegan",           "quick-service-counter",    2, 4.3, 6200, True,  "West End"),
        ("Gus's Fried Chicken",    "quick-service-counter",    2, 4.4, 2380, True,  "Downtown"),
        ("Taqueria del Sol",       "quick-service-counter",    1, 4.5, 4120, True,  "Westside"),
        ("Bones Restaurant",       "independent-fine-dining", 4, 4.6, 1540, True,  "Buckhead"),
        ("Aria",                   "independent-fine-dining", 4, 4.5,  870, True,  "Buckhead"),
        ("Nina & Rafi",            "independent-full-service", 2, 4.4,  930, True,  "Old Fourth Ward"),
        ("BoccaLupo",              "independent-full-service", 3, 4.6,  680, True,  "Inman Park"),
        ("Mujo",                   "independent-fine-dining", 4, 4.8,  220, True,  "Westside"),
        ("Talat Market",          "independent-full-service", 3, 4.7,  410, True,  "Summerhill"),
        ("9 Mile Station",         "bar-brewery-nightlife",    2, 4.3,  710, True,  "Old Fourth Ward"),
        ("The Busy Bee Cafe",      "independent-full-service", 2, 4.5, 3960, False, "Vine City"),
        ("Rreal Tacos",            "quick-service-counter",    1, 4.5, 1620, True,  "Midtown"),
        ("Bread & Butterfly",      "cafe-bakery-brunch",       2, 4.4, 1180, True,  "Inman Park"),
        ("Delbar",                 "independent-full-service", 3, 4.6,  990, True,  "Inman Park"),
        ("Poor Hendrix",           "independent-full-service", 2, 4.4,  620, True,  "East Lake"),
        ("Estrellita",             "independent-fine-dining", 3, 4.7,  150, False, "Decatur"),
    ]
    seg_to_pl = {1: "PRICE_LEVEL_INEXPENSIVE", 2: "PRICE_LEVEL_MODERATE",
                 3: "PRICE_LEVEL_EXPENSIVE", 4: "PRICE_LEVEL_VERY_EXPENSIVE"}
    rows = []
    for name, seg, pt, rt, rc, site, hood in raw:
        rows.append({
            "name": name, "segment_slug": seg, "price_tier": pt,
            "price_level": seg_to_pl[pt], "rating": rt, "review_count": rc,
            "website": (name.lower().replace(" ", "").replace("'", "").replace(".", "")
                        + ".com") if site else None,
            "neighborhood": hood,
        })
    return rows


def sample_menus(rows: list[dict]) -> None:
    """Attach illustrative menu rollups to 25 of the rows (those with websites)."""
    import random
    random.seed(30)
    band = {1: (8, 16), 2: (12, 28), 3: (22, 48), 4: (38, 120)}
    have_site = [r for r in rows if r["website"]]
    for r in random.sample(have_site, min(25, len(have_site))):
        lo, hi = band[r["price_tier"]]
        n = random.randint(14, 34)
        prices = sorted(round(random.uniform(lo, hi), 0) for _ in range(n))
        r["menu"] = {
            "item_count": n,
            "min_price": min(prices), "max_price": max(prices),
            "avg_price": round(sum(prices) / n, 2),
            "confidence": "high" if n >= 12 else "medium",
        }


# ------------------------------------------------------------------
# HTML render
# ------------------------------------------------------------------
def render(rows: list[dict], is_sample: bool) -> str:
    scored = sorted((score(r) for r in rows), key=lambda x: (-x["fit_score"], -(x["review_count"] or 0)))
    n = len(scored)
    a = sum(1 for r in scored if r["target_tier"] == "A")
    b = sum(1 for r in scored if r["target_tier"] == "B")
    c = sum(1 for r in scored if r["target_tier"] == "C")
    n_menu = sum(1 for r in scored if r.get("menu"))
    no_site = sum(1 for r in scored if r["website"] is None)
    seg_names = {
        "independent-fine-dining": "Fine Dining", "independent-full-service": "Full-Service",
        "cafe-bakery-brunch": "Café / Bakery", "bar-brewery-nightlife": "Bar / Brewery",
        "quick-service-counter": "Quick-Service", "no-website-opportunity": "No-Website",
    }
    tier_color = {"A": "#1a7f4b", "B": "#b8860b", "C": "#8a8a8a"}

    def menu_cell(r):
        m = r.get("menu")
        if not m:
            return '<span class="muted">—</span>'
        return (f'<span class="menu">${m["min_price"]:.0f}–${m["max_price"]:.0f}</span>'
                f'<span class="muted"> · avg ${m["avg_price"]:.0f} · {m["item_count"]} items</span>')

    trs = []
    for i, r in enumerate(scored, 1):
        rt = r["rating"] or 0
        site = (f'<a href="https://{html.escape(r["website"])}" target="_blank">site</a>'
                if r["website"] else '<span class="gap">no site</span>')
        trs.append(f"""
        <tr data-tier="{r['target_tier']}" data-seg="{r['segment_slug']}">
          <td class="num">{i}</td>
          <td><span class="tierdot" style="background:{tier_color[r['target_tier']]}">{r['target_tier']}</span></td>
          <td class="score">{r['fit_score']}</td>
          <td class="name">{html.escape(r['name'])}<div class="hood">{html.escape(r.get('neighborhood',''))}</div></td>
          <td>{seg_names.get(r['segment_slug'], r['segment_slug'])}</td>
          <td class="dollars">{r['dollars']}</td>
          <td>{rt:.1f}★ <span class="muted">({r['review_count']})</span></td>
          <td>{menu_cell(r)}</td>
          <td>{site}</td>
          <td class="pitch">{html.escape(r['pitch_angle'])}</td>
        </tr>""")

    banner = ""
    if is_sample:
        banner = """
      <div class="banner">
        <b>SAMPLE VIEW</b> — representative Atlanta restaurants for illustration, scored with the
        exact formula in <code>schema/007_restaurant_views.sql</code>. This is <b>not</b> a live Google
        pull. Run <code>ingest_atlanta_restaurants.py</code> then <code>generate_sample_view.py --from-db</code>
        on your machine to populate with real data.
      </div>"""

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Atlanta Restaurants — B2B Target View</title>
<style>
  :root {{ --ink:#1b1b1b; --line:#e7e3da; --bg:#faf8f3; --card:#fff; --accent:#c2410c; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font:15px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
         color:var(--ink); background:var(--bg); }}
  .wrap {{ max-width:1180px; margin:0 auto; padding:28px 22px 64px; }}
  h1 {{ font-size:24px; margin:0 0 2px; }}
  .sub {{ color:#6b675e; margin:0 0 18px; }}
  .banner {{ background:#fff7ed; border:1px solid #fed7aa; color:#7c2d12; padding:10px 14px;
             border-radius:10px; font-size:13px; margin-bottom:18px; }}
  .banner code {{ background:#ffedd5; padding:1px 5px; border-radius:4px; }}
  .cards {{ display:flex; gap:12px; flex-wrap:wrap; margin-bottom:20px; }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:12px;
           padding:14px 16px; min-width:120px; flex:1; }}
  .card .k {{ font-size:12px; color:#6b675e; text-transform:uppercase; letter-spacing:.04em; }}
  .card .v {{ font-size:26px; font-weight:700; margin-top:2px; }}
  .controls {{ display:flex; gap:8px; flex-wrap:wrap; margin-bottom:12px; align-items:center; }}
  .controls input, .controls select {{ padding:7px 10px; border:1px solid var(--line);
           border-radius:8px; background:#fff; font-size:14px; }}
  table {{ width:100%; border-collapse:collapse; background:#fff; border:1px solid var(--line);
           border-radius:12px; overflow:hidden; }}
  th, td {{ padding:9px 11px; text-align:left; border-bottom:1px solid var(--line); vertical-align:top; }}
  th {{ background:#f3efe6; font-size:12px; text-transform:uppercase; letter-spacing:.03em; color:#5b574e;
        position:sticky; top:0; cursor:pointer; }}
  td.num, td.score {{ text-align:right; font-variant-numeric:tabular-nums; }}
  td.score {{ font-weight:700; }}
  .name {{ font-weight:600; }}
  .hood {{ font-weight:400; color:#8a857b; font-size:12px; }}
  .dollars {{ color:var(--accent); font-weight:700; letter-spacing:1px; }}
  .muted {{ color:#9a958b; font-size:12px; }}
  .menu {{ font-weight:600; }}
  .gap {{ color:#b91c1c; font-weight:600; font-size:12px; }}
  .pitch {{ color:#3f3b34; font-size:13px; max-width:340px; }}
  .tierdot {{ display:inline-block; width:22px; height:22px; line-height:22px; text-align:center;
              border-radius:50%; color:#fff; font-weight:700; font-size:12px; }}
  tr:hover td {{ background:#fcfbf7; }}
  .foot {{ margin-top:18px; color:#8a857b; font-size:12px; }}
</style></head><body><div class="wrap">
  <h1>🍽️ Atlanta Restaurants — B2B Target View</h1>
  <p class="sub">Referral OS · project <code>atlanta-restaurants-b2b</code> · generated {datetime.now():%b %d, %Y}</p>
  {banner}
  <div class="cards">
    <div class="card"><div class="k">Restaurants</div><div class="v">{n}</div></div>
    <div class="card"><div class="k">Tier A targets</div><div class="v" style="color:#1a7f4b">{a}</div></div>
    <div class="card"><div class="k">Tier B</div><div class="v" style="color:#b8860b">{b}</div></div>
    <div class="card"><div class="k">Tier C</div><div class="v" style="color:#8a8a8a">{c}</div></div>
    <div class="card"><div class="k">Menu pricing</div><div class="v">{n_menu}</div></div>
    <div class="card"><div class="k">No website (gap)</div><div class="v" style="color:#b91c1c">{no_site}</div></div>
  </div>
  <div class="controls">
    <input id="q" placeholder="Search name…" oninput="filt()">
    <select id="tier" onchange="filt()"><option value="">All tiers</option>
      <option>A</option><option>B</option><option>C</option></select>
    <select id="seg" onchange="filt()"><option value="">All segments</option>
      {''.join(f'<option value="{s}">{n2}</option>' for s,n2 in seg_names.items())}</select>
    <span class="muted" id="count"></span>
  </div>
  <table id="tbl"><thead><tr>
    <th>#</th><th>Tier</th><th onclick="sortBy(2)">Fit ▾</th><th>Restaurant</th><th>Segment</th>
    <th>$</th><th onclick="sortBy(6)">Rating</th><th>Menu pricing</th><th>Web</th><th>Recommended pitch angle</th>
  </tr></thead><tbody>{''.join(trs)}</tbody></table>
  <p class="foot">Fit score = ability-to-pay ($-tier) + scale (reviews) + quality window (4.0–4.6★) + digital gap (no site).
     Identical formula in SQL view <code>v_atlanta_restaurant_targets</code>.</p>
</div>
<script>
  function filt() {{
    const q=document.getElementById('q').value.toLowerCase();
    const t=document.getElementById('tier').value, s=document.getElementById('seg').value;
    let shown=0;
    document.querySelectorAll('#tbl tbody tr').forEach(tr=>{{
      const okQ=tr.querySelector('.name').textContent.toLowerCase().includes(q);
      const okT=!t||tr.dataset.tier===t; const okS=!s||tr.dataset.seg===s;
      const show=okQ&&okT&&okS; tr.style.display=show?'':'none'; if(show)shown++;
    }});
    document.getElementById('count').textContent=shown+' shown';
  }}
  function sortBy(col) {{
    const tb=document.querySelector('#tbl tbody');
    const rows=[...tb.rows].filter(r=>r.style.display!=='none');
    const num=v=>parseFloat(v.replace(/[^0-9.]/g,''))||0;
    rows.sort((a,b)=>num(b.cells[col].textContent)-num(a.cells[col].textContent));
    rows.forEach(r=>tb.appendChild(r));
  }}
  filt();
</script>
</body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-db", action="store_true",
                    help="Pull live rows from Supabase instead of sample.")
    ap.add_argument("--out", default="atlanta_restaurants_view.html")
    args = ap.parse_args()

    if args.from_db:
        import urllib.request
        def load_env(p=".env"):
            e={}
            for ln in open(p):
                ln=ln.strip()
                if ln and not ln.startswith("#") and "=" in ln:
                    k,_,v=ln.partition("="); e[k.strip()]=v.strip()
            return e
        env=load_env(); base=env["SUPABASE_URL"]; key=env["SUPABASE_SERVICE_ROLE_KEY"]
        req=urllib.request.Request(
            f"{base.rstrip('/')}/rest/v1/v_atlanta_restaurant_targets?select=*&order=fit_score.desc",
            headers={"apikey":key,"Authorization":f"Bearer {key}","Accept":"application/json"})
        rows=json.loads(urllib.request.urlopen(req,timeout=30).read())
        # normalize field names the renderer expects
        for r in rows:
            r["website"]=r.get("website")
            r["menu"]=({"min_price":r.get("menu_min_price"),"max_price":r.get("menu_max_price"),
                        "avg_price":r.get("menu_avg_price"),"item_count":r.get("menu_item_count"),
                        "confidence":"db"} if r.get("menu_avg_price") is not None else None)
        out_html=render(rows, is_sample=False)
    else:
        rows=sample_rows(); sample_menus(rows)
        out_html=render(rows, is_sample=True)

    with open(args.out, "w") as f:
        f.write(out_html)
    print(f"Wrote {args.out}  ({len(out_html)//1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
