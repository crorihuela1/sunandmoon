#!/usr/bin/env python3
"""
PRICING AI AGENT — the product brain.

Given what we know about a restaurant (its menu items, $-tier, rating, review
volume), produce concrete, defensible pricing recommendations — the same output
the paid agent would give, and the "taste" we drop into the first cold email.

Design goals
------------
- Deterministic + stdlib only, so it runs anywhere (sandbox, cron, in the email
  generator) with no API call. The production agent can layer an LLM on top for
  phrasing, but the *numbers* come from transparent rules a restaurateur trusts.
- Every recommendation cites the actual menu evidence it's based on.
- $ upside is ESTIMATED and conservative, with the assumption stated, never a
  black box.

The rules (classic menu-engineering / revenue management)
---------------------------------------------------------
1. round_number_pricing   — items ending in .00 leave money/psychology on the
                            table. Casual → charm price ($13.95); upscale →
                            drop cents entirely (cleaner, tested to raise spend).
2. underpriced_anchor      — an item far below its section's average is usually a
                            high-demand item you can nudge up with no volume hit.
3. missing_premium_anchor  — no high-end option means nothing makes the mid-tier
                            look reasonable; adding one lifts average selection.
4. tier_vs_benchmark       — avg entrée price vs the Atlanta benchmark for the
                            restaurant's $-tier; trailing the median = headroom.
5. compression             — tiny spread between cheapest and priciest entrée
                            signals timid pricing; widen the menu's range.

If we have no scraped menu yet, we still give a credible tier-level taste so the
email lands, and invite them to connect the menu for the itemized version.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field

# Entrée benchmarks by $-tier (median entrée price, illustrative — the production
# agent recomputes these live from the ingested local universe per market).
MARKET_ENTREE_MEDIAN = {1: 13.0, 2: 19.0, 3: 32.0, 4: 52.0}

# Credibility cap: in a first-touch email, a single eye-popping number reads as
# hype and gets deleted. Cap each recommendation's stated upside so the teaser
# stays believable; the full agent shows the real (often larger) figure post-signup.
MAX_REC_UPSIDE = 6000

# Rough "covers per year" proxy from Google review volume. Lifetime reviews are a
# small fraction of lifetime covers; we convert to a conservative annual run-rate.
# ~1 in 200 diners reviews, listing ~5 yrs old, scaled down by tier (fine-dining
# rooms turn far fewer covers than a casual spot). Deliberately understated so the
# email's $ estimate is defensible, not hype.
TIER_VOLUME_FACTOR = {1: 1.0, 2: 0.75, 3: 0.45, 4: 0.30}

def covers_per_year(review_count: int, tier: int = 2) -> int:
    lifetime_covers = (review_count or 0) * 200
    annual = (lifetime_covers / 5.0) * TIER_VOLUME_FACTOR.get(tier, 0.75)
    return max(5000, int(annual))          # floor ~14 covers/day

def entree_covers_per_year(review_count: int, tier: int = 2) -> int:
    return int(covers_per_year(review_count, tier) * 0.55)  # ~55% of covers order an entrée


@dataclass
class Rec:
    rule: str
    headline: str          # one-line recommendation
    evidence: str          # the menu fact it's based on
    annual_upside: int     # conservative $/yr estimate (0 if not quantified)
    confidence: str        # high | medium | low


@dataclass
class Analysis:
    name: str
    has_menu: bool
    recs: list[Rec] = field(default_factory=list)
    total_upside: int = 0
    summary: str = ""

    def top(self, n: int = 2) -> list[Rec]:
        return sorted(self.recs, key=lambda r: r.annual_upside, reverse=True)[:n]


def _round_number_items(items):
    return [it for it in items if float(it["price"]) == int(float(it["price"]))]


def _fmt_items(items, n=3):
    """'Snapper ($34), Pork Chop ($28) and 2 others' — names real dishes."""
    sel = items[:n]
    parts = [f"{it['name']} (${float(it['price']):.0f})" for it in sel]
    extra = len(items) - len(sel)
    s = ", ".join(parts)
    if extra > 0:
        s += f" and {extra} other{'s' if extra > 1 else ''}"
    return s


def _entrees(items):
    ent = [it for it in items
           if (it.get("section") or "").lower() in
           ("entree", "entrees", "main", "mains", "dinner", "")
           and 9 <= float(it["price"]) <= 200]
    inrange = [it for it in items if 9 <= float(it["price"]) <= 200]
    # Always non-empty when items exist (e.g. a bakery with only sub-$9 items),
    # so downstream max()/min() never see an empty list.
    return ent or inrange or list(items)


def analyze(r: dict, market: str = "your area") -> Analysis:
    """r: {name, price_tier, rating, review_count, website, menu:{items:[...]}}
    market: human label for the benchmark comparison ('30A', 'Atlanta', …)."""
    name = r["name"]
    menu = (r.get("menu") or {})
    items = menu.get("items") or []
    tier = r.get("price_tier") or 2
    rc = r.get("review_count") or 0
    cov_yr = covers_per_year(rc, tier)
    ent_cov_yr = entree_covers_per_year(rc, tier)

    if not items:
        # No menu yet — tier-level taste.
        a = Analysis(name=name, has_menu=False)
        med = MARKET_ENTREE_MEDIAN.get(tier, 19.0)
        a.recs.append(Rec(
            rule="tier_vs_benchmark",
            headline=f"As a {'$'*tier} spot, your pricing likely trails the {market} median for your tier.",
            evidence=f"{market} {'$'*tier} entrées run ~${med:.0f} median; most independents sit 5–9% under.",
            annual_upside=int(med * 0.05 * ent_cov_yr * 0.4),  # 40% of a 5% gap
            confidence="low"))
        a.summary = (f"{name} looks like a strong {'$'*tier} independent. Restaurants in your "
                     f"tier typically leave 4–7% on the table from round-number pricing and a "
                     f"missing premium anchor — connect your menu and the agent shows you exactly where.")
        _cap(a)
        return a

    a = Analysis(name=name, has_menu=True)
    prices = [float(it["price"]) for it in items]
    ent = _entrees(items)
    ent_prices = [float(it["price"]) for it in ent] or prices
    avg_ent = statistics.mean(ent_prices)
    med_ent = statistics.median(ent_prices)

    # --- Rule 1: round-number pricing ---
    rn = sorted(_round_number_items(ent), key=lambda it: -float(it["price"]))
    if rn:
        ex = rn[0]
        named = _fmt_items(rn, 3)
        share = len(rn) / max(1, len(ent))
        if tier <= 2:
            up = int(0.008 * avg_ent * ent_cov_yr * share)   # ~0.8% conversion lift
            head = (f"{named} are priced on the dollar — charm-pricing these "
                    f"(e.g. {ex['name']} ${float(ex['price']):.0f} → "
                    f"${float(ex['price'])-0.05:.2f}) reads cheaper and lifts orders.")
            ev = "Casual diners read $X.95 as a better deal; 1–3% conversion lift."
        else:
            up = int(0.015 * avg_ent * ent_cov_yr * share)
            head = (f"{named} sit on round numbers — on a {'$'*tier} menu, dropping the "
                    f"cents entirely reads more upscale and nudges average check up.")
            ev = f"Cents-free pricing on a {'$'*tier} menu is shown to raise spend."
        a.recs.append(Rec("round_number_pricing", head, ev, up, "medium"))

    # --- Rule 2: underpriced anchor (item well below section avg) ---
    if len(ent) >= 4:
        cheapest = min(ent, key=lambda it: float(it["price"]))
        cp = float(cheapest["price"])
        if cp < avg_ent * 0.72 and cp >= 9:
            target = round(cp * 1.12)
            up = int((target - cp) * ent_cov_yr * 0.10)      # ~10% of entrée covers order it
            a.recs.append(Rec(
                "underpriced_anchor",
                f"{cheapest['name']} at ${cp:.0f} is {round((1-cp/avg_ent)*100)}% below your "
                f"~${avg_ent:.0f} entrée average — test ${target:.0f}.",
                "High-demand items priced well under the menu average absorb a nudge with little volume loss.",
                up, "high"))

    # --- Rule 3: missing premium anchor ---
    top_item = max(ent, key=lambda it: float(it["price"]))
    top_ent = float(top_item["price"])
    if top_ent < MARKET_ENTREE_MEDIAN.get(tier, 19) * 1.45:
        anchor = round(top_ent * 1.5)
        up = int(avg_ent * 0.03 * ent_cov_yr)                # ~3% avg-selection lift
        a.recs.append(Rec(
            "missing_premium_anchor",
            f"Your priciest plate is {top_item['name']} at ${top_ent:.0f} — adding one "
            f"showpiece around ${anchor:.0f} makes dishes like it feel like the smart pick.",
            "An upper anchor lifts average entrée selection 6–12% via the compromise effect.",
            up, "medium"))

    # --- Rule 4: tier vs benchmark ---
    bench = MARKET_ENTREE_MEDIAN.get(tier, 19.0)
    if avg_ent < bench * 0.92:
        gap = bench - avg_ent
        up = int(gap * 0.25 * ent_cov_yr)                    # capture 25% of the gap to median
        # name two mains nearest the average, so the comparison is concrete
        mid = sorted(ent, key=lambda it: abs(float(it["price"]) - avg_ent))[:2]
        a.recs.append(Rec(
            "tier_vs_benchmark",
            f"Mains like {_fmt_items(mid, 2)} keep your average entrée at ${avg_ent:.0f} — under "
            f"the {market} {'$'*tier} median (~${bench:.0f}); there's room to nudge them up.",
            f"A measured move toward the median recovers margin you're currently giving away.",
            up, "medium"))

    # --- Rule 5: compression ---
    spread = max(ent_prices) - min(ent_prices)
    if ent_prices and spread < avg_ent * 0.6 and len(ent) >= 4:
        lo = min(ent, key=lambda it: float(it["price"]))
        hi = max(ent, key=lambda it: float(it["price"]))
        a.recs.append(Rec(
            "compression",
            f"Your entrées are bunched from {lo['name']} (${float(lo['price']):.0f}) to "
            f"{hi['name']} (${float(hi['price']):.0f}) — widen that range to capture both "
            f"budget and premium tables.",
            "A narrow price band caps the check; spreading it grows average order value.",
            0, "low"))

    n_items = menu.get("item_count", len(items))
    a.summary = (f"Read {n_items} items off {name}'s menu. Top opportunities below — "
                 f"the full agent re-checks these weekly and against live {market} comps.")
    _cap(a)
    return a


def _cap(a: Analysis) -> None:
    """Clamp each rec to a believable ceiling, then total."""
    for rec in a.recs:
        rec.annual_upside = min(rec.annual_upside, MAX_REC_UPSIDE)
    a.total_upside = sum(x.annual_upside for x in a.recs)


if __name__ == "__main__":
    # quick self-demo
    demo = {"name": "Banshee", "price_tier": 2, "rating": 4.6, "review_count": 760,
            "website": "banshee.com",
            "menu": {"item_count": 6, "items": [
                {"name": "Wood-Grilled Chicken", "price": 24.0, "section": "Entree"},
                {"name": "Pork Chop", "price": 28.0, "section": "Entree"},
                {"name": "Cavatelli", "price": 18.0, "section": "Entree"},
                {"name": "Smash Burger", "price": 15.0, "section": "Entree"},
                {"name": "Little Gem Salad", "price": 12.0, "section": "Salad"},
                {"name": "Fries", "price": 8.0, "section": "Side"}]}}
    a = analyze(demo)
    print(a.summary, f"\n~${a.total_upside:,}/yr est. upside\n")
    for rec in a.recs:
        print(f"• [{rec.rule}] {rec.headline}\n    {rec.evidence}  (~${rec.annual_upside:,}/yr, {rec.confidence})")
