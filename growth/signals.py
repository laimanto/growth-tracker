"""The rule (Research/Cisco_2026-10-23 Parts 14-21), on the per-stock data files.

  relPE   = P/E (last close / last 4 reported quarters' EPS) / median P/E of the stock's sector (positive EPS only)
  (shown on the page as two tests: EPS growth >= floor AND PEG = P/E / growth% <= sector P/E / 30 to buy;
   sell when growth < keep floor OR PEG > sector P/E / 25 - identical to the bars below)
  trail   = trailing EPS growth over the last 12 months (last 4 quarters vs the 4 before)
  fwd     = average yearly EPS growth over the next 24 months, read off the fiscal-year consensus curve
            (last FY actual -> current FY -> FY+1 from Yahoo -> FY+2 = FY+1 x Nasdaq FY+2/FY+1; monthly
            log-interpolation, so it blends the years by how far into the current fiscal year we are)
  floor   80% / 53% of the universe median P/E, in % growth (= 15% / 10% at its 2002-26 average 18.96)
  ENTER   trailing 12m growth AND each forecast fiscal year (Cur, +1, +2) >= max(30% x relPE, entry floor)
  EXIT    any forecast fiscal year (Cur, +1, +2) < max(25% x relPE, exit floor)
          (v1.6, 2026-10-24: floor from Research Part 23 / 23b, growth_floor_rule*.py)
          (v1.2, user 2026-10-23: a forecast year below the bar is out of scope; backtest year_by_year_rule.py "EACH/min".
           +2 missing at Nasdaq -> taken equal to +1, flagged. The last fiscal year (actual) is shown, not required.)
Display: EPS growth of the last, current, next and next-but-one fiscal year (Last / Cur / +1 / +2), and
  room = for a holding: weakest forecast year - exit bar; otherwise: weakest of trailing / forecast years - entry bar.
States: BUY, HOLD, SELL, WATCH (not held, short of the entry bar by <= 5 pts), OUT, NO DATA.
"""
import math
from datetime import date

import numpy as np
import pandas as pd

import config as C


def qtr(qh):
    return sorted((pd.Timestamp(d), v) for d, v in qh or [])


def year_on(n, d):
    """Nasdaq's consensus for the same quarter one year after the quarter reported on `d` (its quarter end falls
    ~255-360 days after the report date), or None."""
    d = pd.Timestamp(d)
    for r in (n or {}).get("qrows") or []:
        if r.get("eps") is not None and 255 <= (pd.Timestamp(r["fye"]) - d).days <= 360:
            return r["eps"]
    return None


def nasdaq_q_fits(y, n, tol=0.25):
    """Nasdaq's quarterly consensus is on the same EPS basis as Yahoo's: its estimate for the coming quarter is within
    25% of Yahoo's (0q). Not so for REITs (Nasdaq/Zacks = FFO: UDR 0.64 vs 0.15) or near-zero / loss quarters."""
    q = qtr(y.get("qh"))
    y0 = ((y.get("est") or {}).get("0q") or {}).get("avg")
    if not q or not y0 or y0 <= 0:
        return False
    nxt = [r for r in (n or {}).get("qrows") or [] if r.get("eps") is not None and pd.Timestamp(r["fye"]) > q[-1][0]]
    return bool(nxt) and nxt[0]["eps"] > 0 and abs(nxt[0]["eps"] / y0 - 1) <= tol


def one_offs(y, n=None, k_hi=1.6, k_lo=0.4, k_yr=1.3):
    """Quarters distorted by one-off items (GOOGL Q1/Q2 2026: 5.11 / 9.11 reported vs 2.67 / 2.90 expected - gains on
    equity stakes). A quarter is a one-off when its reported EPS is > 1.6x (or < 0.4x) BOTH its own consensus estimate and
    the estimate for the next quarter (analysts' run rate after seeing it - a real beat lifts that too: SNDK 6.20 vs 3.54
    expected, next quarter expected 14.66), AND - when Nasdaq's quarterly consensus is on Yahoo's basis (n = Nasdaq
    record, nasdaq_q_fits) - > 1.3x (< 1/1.3x) the estimate for the same quarter a year later (1.3: room for a year of
    growth). The last test is for seasonal businesses (2026-11-05 fix): LUV Q2 2026 0.94 adjusted vs 0.52 expected with a
    weaker Q3 (0.58) ahead was a real beat - analysts expect Q2 2027 at 1.28; flagging it had cut LUV's FY2026 to 2.34,
    below guidance 2.60-2.85, and inflated FY2027 growth from +51% to +79%. GOOGL Q1 (5.11 vs Q1 2027 3.30) stays flagged.
    Flagged quarters are replaced by their estimate; the excess is also taken out of the fiscal-year figures that contain
    it (current-FY consensus, last-FY actual), which include it.
    Returns (adjusted copy of y, list of notes)."""
    qh, qe = y.get("qh") or [], dict(y.get("qe") or [])
    if not qh or not qe:
        return y, []
    nxt0 = ((y.get("est") or {}).get("0q") or {}).get("avg")
    fits = nasdaq_q_fits(y, n)
    out, notes, excess = [], [], []
    for i, (d, v) in enumerate(qh):
        e = qe.get(d)
        en = qe.get(qh[i + 1][0]) if i + 1 < len(qh) else nxt0
        if e is not None and en is not None and e > 0 and en > 0 and v is not None:
            lo, hi = min(e, en), max(e, en)
            ey = year_on(n, d) if fits else None
            ey = ey if ey is not None and ey > 0 else None
            if (v > k_hi * hi and (ey is None or v > k_yr * ey)) or (v < k_lo * lo and (ey is None or v < ey / k_yr)):
                out.append([d, e])
                excess.append((pd.Timestamp(d), v - e))
                if i >= len(qh) - 8:                        # shown only for the 8 quarters behind trailing growth / P/E
                    notes.append(f"{d[:10]}: reported {v:.2f} vs {e:.2f} expected (next quarter expected {en:.2f}"
                                 + (f", same quarter next year {ey:.2f})" if ey is not None else ")"))
                continue
        out.append([d, v])
    if not excess:
        return y, []
    y = dict(y, qh=out, est={k: dict(v) for k, v in (y.get("est") or {}).items()})
    if y.get("nextFye"):
        f0 = pd.Timestamp(y["nextFye"])
        in_fy = lambda d, f: f - pd.Timedelta(days=270) < d <= f + pd.Timedelta(days=110)
        e0, e1 = y["est"].get("0y") or {}, y["est"].get("+1y") or {}
        for d, x in excess:
            if in_fy(d, f0):
                for e, k in ((e0, "avg"), (e1, "yearAgoEps")):
                    if e.get(k) is not None:
                        e[k] -= x
            elif in_fy(d, f0 - pd.DateOffset(years=1)) and e0.get("yearAgoEps") is not None:
                e0["yearAgoEps"] -= x
    return y, notes


def ttm_pair(q, today):
    if len(q) < 8:
        return None, None
    l4, p4 = q[-4:], q[-8:-4]
    if (l4[-1][0] - l4[0][0]).days > 330 or (p4[-1][0] - p4[0][0]).days > 330 or (today - l4[-1][0]).days > 140:
        return None, None
    return sum(v for _, v in l4), sum(v for _, v in p4)


def fy_from_q(q, fye):
    """Sum of the 4 quarters of the fiscal year ending `fye` (reported within ~9 months before to ~4 after)."""
    x = [v for d, v in q if fye - pd.Timedelta(days=270) < d <= fye + pd.Timedelta(days=110)]
    return sum(x) if len(x) == 4 else None


def curve(y, n):
    est = y.get("est") or {}
    e0, e1 = est.get("0y") or {}, est.get("+1y") or {}
    if not (y.get("nextFye") and e0.get("avg") and e1.get("avg") and e0.get("yearAgoEps")):
        return None, None, None
    f0 = pd.Timestamp(y["nextFye"]) + pd.offsets.MonthEnd(0)
    pts = [(f0 - pd.DateOffset(years=1), e0["yearAgoEps"]), (f0, e0["avg"]), (f0 + pd.DateOffset(years=1), e1["avg"])]
    rows, f1 = (n or {}).get("rows") or [], pts[-1][0]
    a = next((r for r in rows if abs((pd.Timestamp(r["fye"]) - f1).days) < 40), None)
    b = next((r for r in rows if abs((pd.Timestamp(r["fye"]) - f1).days - 365) < 40), None)
    g2 = None
    if a and b and a.get("eps") and b.get("eps") and a["eps"] > 0 and b["eps"] > 0 and (b.get("n") or 0) >= 3:
        g2 = b["eps"] / a["eps"] - 1
        pts.append((f1 + pd.DateOffset(years=1), e1["avg"] * (1 + g2)))
    if any(v is None or v <= 0 for _, v in pts):
        return None, pts, g2
    pts = [(pd.Timestamp(d) + pd.offsets.MonthEnd(0), v) for d, v in pts]
    c = pd.Series({d: math.log(v) for d, v in pts}).reindex(pd.date_range(pts[0][0], pts[-1][0], freq="ME")).interpolate()
    slope = (math.log(pts[-1][1]) - math.log(pts[-2][1])) / 12
    # extend at the last year's slope: 24 months, so a fiscal year that has ended but not yet reported (Sep FY in October)
    # without a Nasdaq FY+2 still reaches today + 24 months
    tail = pd.Series([c.iloc[-1] + slope * k for k in range(1, 25)], index=pd.date_range(c.index[-1] + pd.offsets.MonthEnd(1), periods=24, freq="ME"))
    return pd.concat([c, tail]), pts, g2


def fy_growth(y, n, q):
    """EPS growth of the last (actual), current, next and next-but-one fiscal year from one Yahoo / Nasdaq record:
    {"fy": [years], "gLast", "lastFlag", "gCur", "g1", "g2", "eps": {year: EPS}, "nfy": {year: analysts}} (empty without a fiscal-year end)."""
    est = y.get("est") or {}
    e0, e1 = est.get("0y") or {}, est.get("+1y") or {}
    if not y.get("nextFye"):
        return {}
    f0 = pd.Timestamp(y["nextFye"])
    fl, fll = fy_from_q(q, f0 - pd.DateOffset(years=1)), fy_from_q(q, f0 - pd.DateOffset(years=2))
    o = {"fy": [f0.year - 1, f0.year, f0.year + 1, f0.year + 2]}
    o["gLast"] = fl / fll - 1 if fl and fll and fl > 0 and fll > 0 else None
    ya = e0.get("yearAgoEps")                         # analysts' actual for the last fiscal year
    if o["gLast"] is not None and ya and ya > 0 and abs(fl / ya - 1) > 0.15:
        o["lastFlag"] = (f"reported quarters of FY{f0.year - 1} add up to {fl:.2f} vs the analysts’ actual {ya:.2f}: "
                         f"one-off items (tax, write-downs, gains) likely distort this growth figure")
    o["gCur"] = e0["avg"] / ya - 1 if e0.get("avg") and ya and ya > 0 and e0["avg"] > 0 else None
    o["g1"] = e1["avg"] / e0["avg"] - 1 if e1.get("avg") and e0.get("avg") and e0["avg"] > 0 and e1["avg"] > 0 else None
    _, pts, o["g2"] = curve(y, n)
    o["eps"] = {f0.year - 1 + i: v for i, v in enumerate([ya, e0.get("avg"), e1.get("avg")]) if v is not None}
    if pts and len(pts) == 4:
        o["eps"][f0.year + 2] = pts[3][1]
    rows_n = (n or {}).get("rows") or []
    f2 = f0 + pd.DateOffset(years=2)
    b = next((r for r in rows_n if abs((pd.Timestamp(r["fye"]) - f2).days) < 40), None)
    o["nfy"] = {f0.year: e0.get("numberOfAnalysts"), f0.year + 1: e1.get("numberOfAnalysts"), f0.year + 2: (b or {}).get("n")}
    return o


def compute(U, recs, closes, held=(), today=None, revs=None):
    """revs = reports.revisions(): analysts revising next year since the latest report (settle test)."""
    today = pd.Timestamp(today or date.today())
    last_close = closes.index[closes.index <= today].max()
    m_now = today + pd.offsets.MonthEnd(0)
    rows, curves = [], {}
    for s, info in U["stocks"].items():
        rec = recs.get(s) or {}
        y, n = rec.get("yahoo") or {}, rec.get("nasdaq")
        row = {"sym": s, "name": info["name"], "sector": info["sector"], "ndx": info["ndx"], "sp500": info["sp500"], "held": s in held,
               "extra": bool(info.get("extra")), "left_index": bool(info.get("left_index")),
               "oneoff": rec.get("oneoff")}
        col = closes[s].loc[:last_close].dropna() if s in closes else pd.Series(dtype=float)
        row["price"] = float(col.iloc[-1]) if len(col) else None
        gone = bool(s in held and len(col) and (last_close - col.index[-1]).days > C.DELISTED_DAYS)
        q = qtr(y.get("qh"))
        last_rep = q[-1][0].normalize() if q else None
        row["last_report"] = None if last_rep is None else str(last_rep.date())
        nxt = (y.get("next") or "")[:10] or None
        row["next_report"] = nxt if nxt and (last_rep is None or (pd.Timestamp(nxt) - last_rep).days > 7) else None
        if last_rep is not None:
            row["days_since_report"] = int((today.normalize() - last_rep).days)
            row["in_review"] = C.REVIEW[0] <= row["days_since_report"] <= C.REVIEW[1]
        # fiscal-year growth: last (actual), current, +1, +2
        e1 = (y.get("est") or {}).get("+1y") or {}
        c, pts, g2 = curve(y, n)
        if c is not None:
            curves[s] = c
        fg = fy_growth(y, n, q)
        row.update({k: fg[k] for k in ("fy", "gLast", "lastFlag", "gCur", "g1", "g2") if k in fg})
        row["fy_eps"], row["fy_n"] = fg.get("eps"), fg.get("nfy")
        row["fy_points"] = [[str(pd.Timestamp(d).date()), round(float(v), 4)] for d, v in (pts or [])]
        row["qh"] = [[str(d.date()), v] for d, v in q][-12:]
        row["n_analysts"] = int(e1.get("numberOfAnalysts") or 0)
        # readiness to act (v1.9): settled forecasts after a report, fiscal-year rollover, fresh data
        wait, dsr = [], row.get("days_since_report")
        dm = lambda k: f"{(last_rep + pd.Timedelta(days=k)).day} {last_rep + pd.Timedelta(days=k):%b}"
        rvs = (revs or {}).get(s)
        if rvs:
            row["rev_since"], row["rev_n"], row["rev_how"] = rvs["rev"], rvs["n"], rvs["how"]
        if dsr is not None and dsr <= C.REVIEW[1]:
            if dsr < C.REVIEW[0]:
                wait.append(f"just reported ({row['last_report']}): no trade before day {C.REVIEW[0]} ({dm(C.REVIEW[0])}) while analysts update")
            elif dsr < C.SETTLE_DAY:
                # settled once >= 50% of analysts revised next year's forecast since the report, else on day 14 (user 2026-11-01)
                share = rvs["rev"] / rvs["n"] if rvs and rvs["n"] else None
                if share is None or share < C.SETTLE_SHARE:
                    got = (f"{rvs['rev']} of {rvs['n']} analysts ({min(share, 1):.0%}) revised next year’s forecast since the {row['last_report']} report"
                           if share is not None else f"no revision count since the {row['last_report']} report")
                    wait.append(f"forecasts not settled: {got} (needs {C.SETTLE_SHARE:.0%}) – settled on day {C.SETTLE_DAY} ({dm(C.SETTLE_DAY)}) at the latest")
                if y.get("nextFye") and last_rep is not None and last_rep > pd.Timestamp(y["nextFye"]) + pd.Timedelta(days=20):
                    wait.append("Yahoo has not switched to the new fiscal year yet (still shows the year just reported)")
                elif 0 < row["n_analysts"] < C.ROLLOVER_MIN:
                    wait.append(f"new fiscal year: only {row['n_analysts']} analysts forecast next year so far (needs {C.ROLLOVER_MIN})")
                rows_n = (n or {}).get("rows") or []
                if y.get("nextFye") and rows_n:
                    f2_ = pd.Timestamp(y["nextFye"]) + pd.DateOffset(years=2)
                    late = max(pd.Timestamp(r_["fye"]) for r_ in rows_n) >= f2_ - pd.Timedelta(days=40)
                    if late and not any(abs((pd.Timestamp(r_["fye"]) - f2_).days) < 40 for r_ in rows_n):
                        wait.append("the two-years-ahead (FY+2) forecast from Nasdaq is not for the same year as Yahoo’s yet")
        if nxt and 0 <= (today.normalize() - pd.Timestamp(nxt)).days <= C.REPORT_WAIT and (last_rep is None or (pd.Timestamp(nxt) - last_rep).days > 7):
            wait.append(f"reported on {nxt}, but the new quarter is not in Yahoo’s data yet (re-downloaded daily)")
        ya_ = y.get("asof")
        if not ya_ or (today.normalize() - pd.Timestamp(ya_)).days > C.STALE_DAYS:
            wait.append(f"Yahoo data {'missing' if not ya_ else str((today.normalize() - pd.Timestamp(ya_)).days) + ' days old'} (max {C.STALE_DAYS}): waiting for a fresh download")
        elif rec.get("retry_y"):
            wait.append("last Yahoo download incomplete: re-downloaded at the next daily run")
        row["wait"] = "; ".join(wait) or None
        row["ready"] = not wait
        ttm, ttm_ago = ttm_pair(q, today)
        fx = y.get("fx") or 1.0
        if gone:                                             # delisted / acquired: sell at the last close
            row.update(state="SELL", why=f"no price since {col.index[-1].date()} (delisted or acquired): sold at the last close", ready=True, wait=None)
        elif row["price"] is None:
            row.update(state="NO DATA", why="no price")
        elif not ttm or not ttm_ago:
            row.update(state="NO DATA", why=(f"only {len(q)} reported quarters (new listing)" if len(q) < 8 else
                                             "reported quarters missing at Yahoo (retried daily)"))
        elif ttm <= 0 or ttm_ago <= 0:
            row.update(state="NO DATA", why="loss in the last 12 months or the 12 before (growth undefined)")
        elif c is None or m_now not in c.index or (m_now + pd.offsets.MonthEnd(24)) not in c.index:
            row.update(state="NO DATA", why="consensus EPS not positive" if pts and any(v is None or v <= 0 for _, v in pts) else "no 2-year consensus")
        else:
            row["ttm"] = ttm
            row["pe"] = row["price"] * fx / ttm
            row["trail"] = ttm / ttm_ago - 1
            row["fwd"] = math.exp((c[m_now + pd.offsets.MonthEnd(24)] - c[m_now]) / 2) - 1
        rows.append(row)
    D = pd.DataFrame(rows)
    ok = D.pe.notna() & (D.pe > 0) & ~D.extra                # watch-only extras do not move the medians
    med = D[ok].groupby("sector").pe.median()
    D["sector_pe"] = D.sector.map(med)
    D["relpe"] = D.pe / D.sector_pe
    mkt_pe = float(D[ok].pe.median())
    f_in, f_out = C.FLOOR_IN * mkt_pe / 100, C.FLOOR_OUT * mkt_pe / 100
    D["need_in"], D["need_out"] = (C.ENTRY * D.relpe).clip(lower=f_in), (C.EXIT * D.relpe).clip(lower=f_out)
    for i, x in D.iterrows():
        if x.get("state") in ("NO DATA", "SELL"):              # SELL here = delisted (set above)
            continue
        g2 = x.g2 if pd.notna(x.g2) else x.g1
        yrs = {"Cur": x.gCur, "+1": x.g1, "+2" if pd.notna(x.g2) else "+2 (=+1)": g2}
        if any(pd.isna(v) for v in yrs.values()):
            D.at[i, "state"], D.at[i, "why"] = "NO DATA", "fiscal-year consensus incomplete"
            continue
        weak_k, weak_v = min(yrs.items(), key=lambda kv: kv[1])
        enter = x.trail >= x.need_in and weak_v >= x.need_in and x.n_analysts >= C.MIN_ANALYSTS
        exit_ = weak_v < x.need_out
        if x.held:
            room = weak_v - x.need_out
            st = "SELL" if exit_ else "HOLD"
            why = (f"{weak_k} year {weak_v:+.1%} < G out {x.need_out:.1%}" if exit_ else f"weakest forecast year ({weak_k}) {weak_v:+.1%} >= G out {x.need_out:.1%}")
        else:
            lows = {"trailing": x.trail, **yrs}
            lk, lv = min(lows.items(), key=lambda kv: kv[1])
            room = lv - x.need_in
            if enter:
                st, why = "BUY", f"trailing {x.trail:+.0%} and every forecast year (weakest {weak_k} {weak_v:+.1%}) >= G in {x.need_in:.1%}"
            elif room >= 0:                                  # passes both rules but too few analysts
                st, why = "WATCH", f"passes both rules, but only {int(x.n_analysts)} analysts forecast next year (minimum {C.MIN_ANALYSTS})"
            else:
                st = "WATCH" if room >= -C.WATCH and not exit_ else "OUT"
                why = f"{lk} {lv:+.1%} is {-room * 100:.1f} pts short of G in {x.need_in:.1%}"
        D.at[i, "state"], D.at[i, "why"], D.at[i, "room"] = st, why, room
    D.attrs["price_date"] = str(last_close.date())
    D.attrs["curves"] = curves
    D.attrs["sector_pe"] = {k: round(float(v), 1) for k, v in med.items()}
    D.attrs["floor"] = {"mkt_pe": round(mkt_pe, 1), "in": round(f_in, 4), "out": round(f_out, 4)}
    return D
