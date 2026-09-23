#!/usr/bin/env python3
"""Sun & Moon booking platform — 30-test validation suite.
Runs against local wrangler dev (http://localhost:8787) + live Vacay + Supabase.
Every fixture it creates is removed afterward; the one reservation it makes is
cancelled and its dates released."""
import json, time, urllib.request, urllib.error, urllib.parse, re, sys, datetime
from pathlib import Path

BASE = "http://localhost:8787"
DEV_VARS = Path("/Users/samantha/Documents/Claude/Projects/Sun & Moon at 30a/booking-os/.dev.vars")
env = {}
for line in DEV_VARS.read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("="); env[k.strip()] = v.strip().strip('"')
SB_URL, SB_KEY = env["SUPABASE_URL"].rstrip("/"), env["SUPABASE_SERVICE_KEY"]
RUN = "YT" + datetime.datetime.now().strftime("%H%M%S")

def wdays(ci):
    """days-to-check-in exactly as the worker computes it (UTC floor)"""
    dt = datetime.datetime.fromisoformat(ci + "T00:00:00+00:00")
    return int((dt - datetime.datetime.now(datetime.timezone.utc)).total_seconds() // 86400)

def ladder_expect(ci, ladder):
    """deepest ladder step matching the worker's days-out for this check-in"""
    days = wdays(ci); best = None
    for s in ladder:
        if days <= s["days"] and (best is None or s["pct"] > best["pct"]): best = s
    return best

def sb(method, path, payload=None, prefer=None):
    hdrs = {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Content-Type": "application/json"}
    if prefer: hdrs["Prefer"] = prefer
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(SB_URL + path, data=data, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        b = r.read().decode()
        return json.loads(b) if b else None

def http(method, path, payload=None, headers=None):
    hdrs = {"Content-Type": "application/json", **(headers or {})}
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()

def quote(prop, ci, co, guests=4, session=None, coupon=None, sleep=2.0):
    p = {"property": prop, "checkIn": ci, "checkOut": co, "guests": guests}
    if session: p["session"] = session
    if coupon: p["coupon"] = coupon
    st, body = http("GET", "/quote?" + urllib.parse.urlencode(p))
    time.sleep(sleep)
    j = json.loads(body)
    return j.get("quote", {})

def rules_of(qt): return {a["rule"]: a for a in qt.get("yieldAdjustments", [])}
def approx(a, b, tol=0.05): return abs(float(a) - float(b)) <= tol

TODAY = datetime.date.today()
def d(days): return (TODAY + datetime.timedelta(days=days)).isoformat()

# ── discover open dates from the synced calendar ──────────────────────
def open_days(prop_id, lo, hi):
    rows = sb("GET", f"/rest/v1/rate_calendar?property_id=eq.{prop_id}&day=gte.{d(lo)}&day=lte.{d(hi)}&select=day,available&order=day")
    return {r["day"]: r["available"] for r in rows}

def find_run(avail, lo, hi, nights):
    for start in range(lo, hi + 1):
        days = [d(start + i) for i in range(nights)]
        if all(avail.get(x) is True for x in days): return d(start), d(start + nights)
    return None, None

def find_orphan(avail, lo, hi):
    for start in range(lo, hi + 1):
        day, prev, nxt = d(start), d(start - 1), d(start + 1)
        if avail.get(day) is True and avail.get(prev) is False and avail.get(nxt) is False:
            return day, d(start + 1)
    return None, None

PROPS = {"golden-sun": "503319", "blue-moon": "232268", "full-property": "507559"}
cal = {slug: open_days(pid, -1, 130) for slug, pid in PROPS.items()}

results, fixtures = [], {"coupons": [], "floors": [], "protected": [], "gs_rules": False, "resv": None}
def T(n, name, ok, detail=""):
    results.append((n, name, bool(ok), detail))
    print(f"T{n:02d} {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not ok else ""))

orig_default = sb("GET", "/rest/v1/yield_rules?property_id=is.null&select=*")[0]
DEF_ID = orig_default["id"]

try:
    # T1 health
    st, body = http("GET", "/health")
    T(1, "health endpoint", st == 200 and json.loads(body).get("ok") is True)

    # T2 guest page + Together flagship messaging + default selection
    st, body = http("GET", "/")
    html = body.decode()
    T(2, "guest page: Together card, preferred pill, default-selected",
      st == 200 and "Preferred rate — best value" in html
      and 'value="full-property" checked' in html and "preferred together rate" in html.lower())

    # T3 photos
    ok3 = all(http("GET", f"/img/{s}.jpg")[0] == 200 for s in PROPS)
    T(3, "property photos serve", ok3)

    # far-out control quote (GS Nov 2-6, known open)
    gs_far = quote("golden-sun", "2026-11-02", "2026-11-06", session=f"{RUN}_ctl")
    r4 = rules_of(gs_far)
    autos = [k for k in r4 if k.startswith(("ladder_", "gap_", "lockin"))]
    T(4, "far-out anchor: markup 15, no auto discounts",
      gs_far.get("available") and gs_far["markupPct"] == 15 and not autos
      and approx(gs_far["nightlyTotal"], round(gs_far["baseNightlyTotal"] * 1.15, 2)),
      json.dumps(r4))

    # T5 booking fee excluded, taxes = 11% of (nightly+cleaning)
    fee_names = [f["name"] for f in gs_far.get("fees", [])]
    exc = [f["name"] for f in gs_far.get("excludedFees", [])]
    expected_tax = round(0.11 * (gs_far["nightlyTotal"] + gs_far["cleaningFee"]), 2)
    T(5, "booking fee excluded; taxes 11% of nightly+cleaning",
      not any("booking" in n.lower() for n in fee_names)
      and any("booking" in n.lower() for n in exc)
      and approx(gs_far["taxesTotal"], expected_tax))

    # T6 deposit/balance split
    T(6, "deposit 25% / balance / due date −30d",
      approx(gs_far["deposit"], round(gs_far["total"] * 0.25, 2))
      and approx(gs_far["balance"], round(gs_far["total"] - gs_far["deposit"], 2))
      and gs_far["balanceDueDate"] == "2026-10-03")

    # T7 near-in full payment — need any open near stay
    ci7, co7 = find_run(cal["blue-moon"], 2, 25, 2)
    if not ci7: ci7, co7 = find_run(cal["golden-sun"], 2, 25, 2)
    near = quote("blue-moon" if ci7 and cal["blue-moon"].get(ci7) else "golden-sun", ci7, co7, session=f"{RUN}_n7") if ci7 else {}
    T(7, "inside 30d: full payment now", bool(near.get("fullPaymentNow")) and approx(near["deposit"], near["total"]),
      f"stay {ci7}..{co7}")

    # T8 Together preferred rate −5
    full_far = quote("full-property", "2026-11-02", "2026-11-06", guests=10, session=f"{RUN}_ctl")
    r8 = rules_of(full_far)
    oa = r8.get("owner_adjustments", {})
    T(8, "Together preferred: owner_adjustments −5 with amount; nightly = base×1.10",
      full_far.get("available") and oa.get("pct") == -5 and oa.get("amount", 0) < 0
      and approx(full_far["nightlyTotal"], round(full_far["baseNightlyTotal"] * 1.10, 2)),
      json.dumps(r8))

    # T9 Together beats separate
    bm_far = quote("blue-moon", "2026-11-02", "2026-11-06", session=f"{RUN}_ctl")
    separate = gs_far["nightlyTotal"] + bm_far["nightlyTotal"]
    T(9, "Together nightly beats separate cottages",
      full_far["nightlyTotal"] < separate,
      f"together {full_far['nightlyTotal']} vs separate {separate}")

    # T10 ladder: engine picks the CORRECT window for the true days-out and
    # prices exactly max(floor, marked×(1−pct)) — dates discovered dynamically
    prop10 = None
    for slug in ("blue-moon", "golden-sun", "full-property"):
        ci, co = find_run(cal[slug], 9, 16, 2)
        if ci and 8 <= wdays(ci) <= 14: prop10 = (slug, ci, co); break
        ci, co = find_run(cal[slug], 8, 16, 2)
        if ci: prop10 = (slug, ci, co); break
    if prop10:
        q10 = quote(prop10[0], prop10[1], prop10[2], session=f"{RUN}_l14")
        r10 = rules_of(q10)
        exp = ladder_expect(prop10[1], orig_default["ladder"])
        lad = r10.get(f"ladder_{exp['days']}d", {})
        adj10 = r10.get("owner_adjustments", {}).get("pct", 0)
        marked = round(q10["baseNightlyTotal"] * (1.15 + adj10 / 100), 2)
        floor10 = round(q10["baseNightlyTotal"] * q10.get("yieldFloorPct", 100) / 100, 2)
        expected_nightly = max(floor10, round(marked * (1 - exp["pct"] / 100), 2))
        T(10, f"ladder picks correct window ({exp['days']}d → −{exp['pct']}%) and respects floor",
          q10.get("available") and lad.get("pct") == -exp["pct"]
          and approx(q10["nightlyTotal"], expected_nightly),
          f"{prop10} wdays={wdays(prop10[1])} {json.dumps(r10)}")
    else:
        T(10, "ladder window selection", False, "no open 2-night run 8-16d out on any property")

    # T11 ladder 7d clamped to floor (=base)
    prop11 = None
    for slug in ("blue-moon", "golden-sun", "full-property"):
        ci, co = find_run(cal[slug], 4, 7, 1)
        if ci: prop11 = (slug, ci, co); break
    if prop11:
        q11 = quote(prop11[0], prop11[1], prop11[2], guests=2, session=f"{RUN}_l7")
        r11 = rules_of(q11)
        step = next((v for k, v in r11.items() if k.startswith("ladder_")), {})
        # with preferred −5 the anchor is 1.10×base → −15% target 0.935 still clamps at base
        T(11, "ladder ≤7d: clamped to floor (nightly = base)",
          q11.get("available") and step.get("clamped_to_floor") is True
          and approx(q11["nightlyTotal"], q11["baseNightlyTotal"]),
          f"{prop11} {json.dumps(r11)}")
    else:
        T(11, "ladder ≤7d clamp", False, "no open night 4-7d out on any property")

    # T12 per-property rules override default (GS custom ladder 120d → 12%)
    sb("POST", "/rest/v1/yield_rules", {"property_id": "503319", "ladder": [{"days": 120, "pct": 12}],
       "gap_enabled": False, "lockin_enabled": False}, prefer="return=minimal")
    fixtures["gs_rules"] = True
    q12 = quote("golden-sun", "2026-11-02", "2026-11-06", session=f"{RUN}_p12")
    r12 = rules_of(q12)
    T(12, "per-property yield rules override default",
      r12.get("ladder_120d", {}).get("pct") == -12
      and approx(q12["nightlyTotal"], round(round(q12["baseNightlyTotal"] * 1.15, 2) * 0.88, 2)),
      json.dumps(r12))
    sb("DELETE", "/rest/v1/yield_rules?property_id=eq.503319"); fixtures["gs_rules"] = False

    # T13/T14 floor overrides (need the T11 stay again)
    if prop11:
        slug, ci, co = prop11
        pid = PROPS[slug]
        f90 = sb("POST", "/rest/v1/yield_floor_overrides",
                 {"property_id": pid, "start_date": ci, "end_date": co, "floor_pct": 90, "note": f"{RUN} t13"},
                 prefer="return=representation")[0]
        fixtures["floors"].append(f90["id"])
        q13 = quote(slug, ci, co, guests=2, session=f"{RUN}_f90")
        r13 = rules_of(q13)
        step13 = next((v for k, v in r13.items() if k.startswith("ladder_")), {})
        marked13 = round(q13["baseNightlyTotal"] * (1.15 + r13.get("owner_adjustments", {}).get("pct", 0) / 100), 2)
        T(13, "floor override 90%: ladder applies un-clamped",
          not step13.get("clamped_to_floor") and q13["nightlyTotal"] < q13["baseNightlyTotal"]
          and approx(q13["nightlyTotal"], round(marked13 * (1 + step13.get("pct", 0) / 100), 2)),
          json.dumps(r13))
        sb("PATCH", f"/rest/v1/yield_floor_overrides?id=eq.{f90['id']}", {"floor_pct": 98}, prefer="return=minimal")
        q14 = quote(slug, ci, co, guests=2, session=f"{RUN}_f98")
        r14 = rules_of(q14)
        step14 = next((v for k, v in r14.items() if k.startswith("ladder_")), {})
        T(14, "floor override 98%: clamps exactly at 98% of base",
          step14.get("clamped_to_floor") is True
          and approx(q14["nightlyTotal"], round(q14["baseNightlyTotal"] * 0.98, 2)),
          json.dumps(r14))
        sb("DELETE", f"/rest/v1/yield_floor_overrides?id=eq.{f90['id']}")
        fixtures["floors"].remove(f90["id"])
    else:
        T(13, "floor override 90%", False, "no near stay"); T(14, "floor override 98%", False, "no near stay")

    # T15/T16 protected dates (reuse T10 stay: near-in with discount)
    if prop10:
        slug, ci, co = prop10
        pr = sb("POST", "/rest/v1/protected_dates",
                {"property_id": None, "start_date": ci, "end_date": co, "note": f"{RUN} t15"},
                prefer="return=representation")[0]
        fixtures["protected"].append(pr["id"])
        q15 = quote(slug, ci, co, session=f"{RUN}_p15")
        r15 = rules_of(q15)
        autos15 = [k for k in r15 if k.startswith(("ladder_", "gap_", "lockin"))]
        T(15, "protected dates: no automatic discounts", q15.get("yieldProtected") is True and not autos15, json.dumps(r15))
        cp = sb("POST", "/rest/v1/coupons", {"code": f"{RUN}T16", "pct": 10, "active": True}, prefer="return=representation")[0]
        fixtures["coupons"].append(cp["id"])
        q16 = quote(slug, ci, co, session=f"{RUN}_p16", coupon=f"{RUN}T16")
        c16 = q16.get("coupon") or {}
        T(16, "protected + coupon: coupon still applies",
          q16.get("yieldProtected") is True and c16.get("pct") == 10
          and approx(c16.get("discount", -1), round(0.10 * (q16["nightlyTotal"] + c16.get("discount", 0)), 2)),
          f"couponErr={q16.get('couponError')}")
        sb("DELETE", f"/rest/v1/protected_dates?id=eq.{pr['id']}"); fixtures["protected"].remove(pr["id"])
    else:
        T(15, "protected dates", False, "no stay"); T(16, "protected + coupon", False, "no stay")

    # T17 invalid coupon
    q17 = quote("golden-sun", "2026-11-02", "2026-11-06", coupon="NOSUCHCODE", session=f"{RUN}_c17")
    T(17, "invalid coupon → error, no discount", q17.get("couponError") and not (q17.get("coupon") or {}))

    # T18 property-limited coupon rejected elsewhere
    cp18 = sb("POST", "/rest/v1/coupons", {"code": f"{RUN}T18", "pct": 10, "active": True, "property_id": "503319"},
              prefer="return=representation")[0]
    fixtures["coupons"].append(cp18["id"])
    q18 = quote("blue-moon", "2026-11-02", "2026-11-06", coupon=f"{RUN}T18", session=f"{RUN}_c18")
    T(18, "property-limited coupon rejected on other cottage", q18.get("couponError") and not (q18.get("coupon") or {}))

    # T19 exhausted coupon
    cp19 = sb("POST", "/rest/v1/coupons", {"code": f"{RUN}T19", "pct": 10, "active": True,
              "max_redemptions": 1, "redemptions": 1}, prefer="return=representation")[0]
    fixtures["coupons"].append(cp19["id"])
    q19 = quote("golden-sun", "2026-11-02", "2026-11-06", coupon=f"{RUN}T19", session=f"{RUN}_c19")
    T(19, "max-redemptions coupon rejected", q19.get("couponError") and not (q19.get("coupon") or {}))

    # T20 valid coupon math + tax recompute
    cp20 = sb("POST", "/rest/v1/coupons", {"code": f"{RUN}T20", "pct": 10, "active": True}, prefer="return=representation")[0]
    fixtures["coupons"].append(cp20["id"])
    q20 = quote("golden-sun", "2026-11-02", "2026-11-06", coupon=f"{RUN}T20", session=f"{RUN}_c20")
    c20 = q20.get("coupon") or {}
    tax20 = round(0.11 * (q20["nightlyTotal"] + q20["cleaningFee"]), 2)
    T(20, "coupon −10% of yielded nightly; taxes recomputed",
      c20.get("pct") == 10 and approx(q20["taxesTotal"], tax20, 0.06),
      f"couponErr={q20.get('couponError')}")

    # T21 gap fill: raise gap depth so it beats the ladder on a real orphan night
    orphan = None
    for slug in ("golden-sun", "blue-moon", "full-property"):
        ci, co = find_orphan(cal[slug], 2, 30)
        if ci: orphan = (slug, ci, co); break
    if orphan:
        sb("PATCH", f"/rest/v1/yield_rules?id=eq.{DEF_ID}", {"gap_discount_pct": 25}, prefer="return=minimal")
        q21 = quote(orphan[0], orphan[1], orphan[2], guests=2, session=f"{RUN}_g21")
        r21 = rules_of(q21)
        sb("PATCH", f"/rest/v1/yield_rules?id=eq.{DEF_ID}",
           {"gap_discount_pct": orig_default["gap_discount_pct"]}, prefer="return=minimal")
        T(21, "gap-night fill wins when deepest (orphan night)",
          q21.get("available") and r21.get("gap_fill", {}).get("pct") == -25, f"{orphan} {json.dumps(r21)}")
    else:
        T(21, "gap-night fill", False, "no orphan open night found ≤30d out")

    # T22 lock-in: honest at the floor (no hollow offers), triggers when room exists
    if prop10:
        slug, ci, co = prop10
        pid10 = PROPS[slug]
        s22 = f"{RUN}_lock"
        # two views at floor: repeat-view signal accrues but NO offer may appear
        quote(slug, ci, co, session=s22); q_pre = quote(slug, ci, co, session=s22, sleep=3)
        no_hollow = not q_pre.get("lockinOffer")
        # open room above floor, then the 3rd view must produce a real offer
        f22 = sb("POST", "/rest/v1/yield_floor_overrides",
                 {"property_id": pid10, "start_date": ci, "end_date": co, "floor_pct": 85, "note": f"{RUN} t22"},
                 prefer="return=representation")[0]
        fixtures["floors"].append(f22["id"])
        q22 = quote(slug, ci, co, session=s22)
        off = q22.get("lockinOffer") or {}
        exp_ok = False
        if off.get("expiresAt"):
            exp = datetime.datetime.fromisoformat(off["expiresAt"].replace("Z", "+00:00"))
            mins = (exp - datetime.datetime.now(datetime.timezone.utc)).total_seconds() / 60
            exp_ok = 225 <= mins <= 245
        T(22, "lock-in: no hollow offer at floor; real offer (5%, ≈4h) once room exists",
          no_hollow and off.get("pct") == 5 and off.get("reason") == "repeat_views" and exp_ok
          and rules_of(q22).get("lockin_offer", {}).get("pct") == -5,
          f"no_hollow={no_hollow} offer={json.dumps(off)} trail={json.dumps(rules_of(q22))}")

        # T23 honest expiry
        sb("PATCH", f"/rest/v1/lockin_offers?session_id=eq.{s22}",
           {"expires_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}, prefer="return=minimal")
        q23 = quote(slug, ci, co, session=s22)
        row23 = sb("GET", f"/rest/v1/lockin_offers?session_id=eq.{s22}&select=status")
        T(23, "expired lock-in: discount gone, status=expired",
          not q23.get("lockinOffer") and "lockin_offer" not in rules_of(q23)
          and row23 and row23[0]["status"] == "expired")

        # T24 one per session, ever
        quote(slug, ci, co, session=s22); quote(slug, ci, co, session=s22, sleep=3)
        q24 = quote(slug, ci, co, session=s22)
        n24 = sb("GET", f"/rest/v1/lockin_offers?session_id=eq.{s22}&select=id")
        T(24, "one lock-in per session (no second offer after expiry)",
          not q24.get("lockinOffer") and len(n24) == 1)

        # T25 nearby-unavailable trigger
        s25 = f"{RUN}_na"
        blocked_day = next((k for k, v in sorted(cal[slug].items()) if v is False and k > d(1)), None)
        if blocked_day:
            nb = (datetime.date.fromisoformat(blocked_day) + datetime.timedelta(days=1)).isoformat()
            quote(slug, blocked_day, nb, session=s25, sleep=3)          # → no_availability event
            q25 = quote(slug, ci, co, session=s25)
            off25 = q25.get("lockinOffer") or {}
            T(25, "no-availability search triggers lock-in", off25.get("reason") == "nearby_unavailable", json.dumps(off25))
        else:
            T(25, "no-availability trigger", False, "no blocked day found")
    else:
        for n, nm in [(22, "lock-in trigger"), (23, "lock-in expiry"), (24, "one per session"), (25, "na trigger")]:
            T(n, nm, False, "no near-in open stay")

    # T26 price_display_log audit rows
    logs = sb("GET", f"/rest/v1/price_display_log?session_id=like.{RUN}*&select=adjustments,ab_bucket&limit=50")
    T(26, "price_display_log rows with full trails",
      len(logs) >= 5 and all(any(a["rule"] == "markup" for a in r["adjustments"]) for r in logs))

    # T27 funnel events
    ds = sb("GET", f"/rest/v1/booking_events?session_id=like.{RUN}*&event=eq.date_search&select=id&limit=5")
    na = sb("GET", f"/rest/v1/booking_events?session_id=like.{RUN}*&event=eq.no_availability&select=id&limit=5")
    T(27, "booking_events: date_search + no_availability captured", len(ds) >= 3 and len(na) >= 1)

    # T28 A/B bucketing with variant ladder
    sb("PATCH", f"/rest/v1/yield_rules?id=eq.{DEF_ID}",
       {"ab_split_pct": 100, "ab_variant_ladder": [{"days": 120, "pct": 11}]}, prefer="return=minimal")
    q28 = quote("blue-moon", "2026-11-02", "2026-11-06", session=f"{RUN}_ab")
    r28 = rules_of(q28)
    sb("PATCH", f"/rest/v1/yield_rules?id=eq.{DEF_ID}",
       {"ab_split_pct": orig_default["ab_split_pct"], "ab_variant_ladder": orig_default["ab_variant_ladder"]},
       prefer="return=minimal")
    T(28, "A/B: 100% split → bucket B with variant ladder",
      q28.get("abBucket") == "B" and r28.get("ladder_120d", {}).get("pct") == -11, json.dumps(r28))

    # T29 reserve E2E: hold → overlap-reject → cancel/release
    ci29, co29 = "2027-01-20", "2027-01-23"
    q29a = quote("golden-sun", ci29, co29, session=f"{RUN}_r29")
    if not q29a.get("available"):
        ci29, co29 = "2027-02-16", "2027-02-19"
        q29a = quote("golden-sun", ci29, co29, session=f"{RUN}_r29")
    ok29 = False; detail29 = "no open far window"
    if q29a.get("available"):
        st, body = http("POST", "/reserve", {"property": "golden-sun", "checkIn": ci29, "checkOut": co29,
            "guests": 4, "name": "Suite Test", "email": "suite-test@example.com", "session_id": f"{RUN}_r29"})
        j29 = json.loads(body)
        if st == 200 and j29.get("ok"):
            fixtures["resv"] = (j29["reservationId"] if "reservationId" in j29 else None) or None
            rows = sb("GET", f"/rest/v1/reservations?confirmation_code=eq.{j29['confirmationCode']}&select=id,status,yield_adjustments,ab_bucket")
            fixtures["resv"] = rows[0]["id"]
            q29b = quote("golden-sun", ci29, co29, session=f"{RUN}_r29b")   # must now be blocked
            ok29 = (rows[0]["status"] == "reserved" and rows[0]["yield_adjustments"]
                    and rows[0]["ab_bucket"] in ("A", "B") and q29b.get("available") is False)
            detail29 = f"resv={rows[0]['status']} requote_avail={q29b.get('available')}"
    T(29, "reserve: hold w/ yield audit → double-book rejected", ok29, detail29)

    # T30 auth walls
    st_a, _ = http("GET", "/admin/wrong-token-000")
    st_w, _ = http("POST", "/webhook/reservation", {"property": "golden-sun", "start": "2027-03-01",
                   "end": "2027-03-04", "action": "booked", "ref": "authtest"}, headers={"x-webhook-secret": "WRONG"})
    T(30, "admin bad token → 404; webhook bad secret → 401", st_a == 404 and st_w == 401, f"admin={st_a} webhook={st_w}")

finally:
    # ── cleanup ────────────────────────────────────────────────────────
    try:
        if fixtures["resv"]:
            rows = sb("GET", f"/rest/v1/reservations?id=eq.{fixtures['resv']}&select=property_id,check_in,check_out")
            sb("PATCH", f"/rest/v1/reservations?id=eq.{fixtures['resv']}", {"status": "cancelled"}, prefer="return=minimal")
            if rows:
                r = rows[0]; day = r["check_in"]; days = []
                while day < r["check_out"]:
                    days.append({"property_id": r["property_id"], "day": day, "available": True, "block_source": None})
                    day = (datetime.date.fromisoformat(day) + datetime.timedelta(days=1)).isoformat()
                sb("POST", "/rest/v1/rate_calendar?on_conflict=property_id,day", days,
                   prefer="resolution=merge-duplicates,return=minimal")
        if fixtures["gs_rules"]: sb("DELETE", "/rest/v1/yield_rules?property_id=eq.503319")
        sb("PATCH", f"/rest/v1/yield_rules?id=eq.{DEF_ID}",
           {"gap_discount_pct": orig_default["gap_discount_pct"], "ab_split_pct": orig_default["ab_split_pct"],
            "ab_variant_ladder": orig_default["ab_variant_ladder"], "ladder": orig_default["ladder"]},
           prefer="return=minimal")
        for cid in fixtures["coupons"]: sb("DELETE", f"/rest/v1/coupons?id=eq.{cid}")
        for fid in fixtures["floors"]: sb("DELETE", f"/rest/v1/yield_floor_overrides?id=eq.{fid}")
        for pid_ in fixtures["protected"]: sb("DELETE", f"/rest/v1/protected_dates?id=eq.{pid_}")
        sb("DELETE", f"/rest/v1/lockin_offers?session_id=like.{RUN}*")
        sb("DELETE", f"/rest/v1/booking_events?session_id=like.{RUN}*")
        sb("DELETE", f"/rest/v1/price_display_log?session_id=like.{RUN}*")
        sb("DELETE", "/rest/v1/reservations?guest_email=eq.suite-test@example.com&status=eq.cancelled")
        sb("DELETE", "/rest/v1/booking_notifications?body=like." + urllib.parse.quote("*suite-test@example.com*"))
        print("cleanup: done")
    except Exception as e:
        print("CLEANUP WARNING:", e)

passed = sum(1 for r in results if r[2])
print(f"\n===== {passed}/{len(results)} PASSED =====")
for n, name, ok, detail in results:
    if not ok: print(f"  FAILED T{n:02d}: {name} — {detail}")
sys.exit(0 if passed == len(results) else 1)
