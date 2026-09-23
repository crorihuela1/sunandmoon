"""
Scoring helpers.

- data_completeness_score: how filled-in is this record? 0-100.
- relevance_score: how well does this company match the segment? 0-100.

Both are cheap heuristics. Replace with ML later if it earns its keep.
"""

from __future__ import annotations


# Field weights for completeness. Picked to bias toward fields that
# actually drive outreach (real email > industry code).
_COMPANY_FIELD_WEIGHTS = {
    "name":          5,
    "domain":        10,
    "main_email":    8,
    "main_phone":    8,
    "industry":      5,
    "employee_count": 4,
    "address_line1": 4,
    "city":          4,
    "state":         4,
    "linkedin_url":  6,
    "facebook_url":  3,
    "instagram_handle": 6,
    "website":       5,
    "description":   4,
    "founded_year":  2,
    "revenue_estimate_usd": 3,
    "latitude":      2,
    "longitude":     2,
}


def data_completeness_score(company: dict) -> int:
    """Weighted % of important fields that are populated."""
    total = sum(_COMPANY_FIELD_WEIGHTS.values())
    got   = sum(w for f, w in _COMPANY_FIELD_WEIGHTS.items()
                if company.get(f) not in (None, "", [], {}))
    return round(100 * got / total)


def relevance_score(company: dict, segment_yaml: dict) -> int:
    """
    Cheap match score:
      +30 if industry in segment's industry list
      +20 if state matches
      +20 if city/metro matches
      +15 if any keyword appears in name or description
      +15 base for being returned by the segment's query
    Caps at 100.
    """
    q = segment_yaml.get("zoominfo_query", {})
    score = 15
    if q.get("industries") and company.get("industry") in (q["industries"] or []):
        score += 30
    if q.get("states") and company.get("state") in (q["states"] or []):
        score += 20
    metros = [m.lower() for m in (q.get("metros") or [])]
    city = (company.get("city") or "").lower()
    if metros and any(m in city or city in m for m in metros):
        score += 20
    kws = [k.lower() for k in (q.get("keywords") or [])]
    text = " ".join([company.get("name") or "", company.get("description") or ""]).lower()
    if kws and any(k in text for k in kws):
        score += 15
    return min(score, 100)
