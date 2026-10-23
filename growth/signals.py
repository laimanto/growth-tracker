"""The rule (Research/Cisco_2026-10-23 Parts 14-21), on the per-stock data files.

  relPE   = P/E (last close / last 4 reported quarters' EPS) / median P/E of the stock's sector (positive EPS only)
  trail   = trailing EPS growth over the last 12 months (last 4 quarters vs the 4 before)
  fwd     = average yearly EPS growth over the next 24 months, read off the fiscal-year consensus curve
            (last FY actual -> current FY -> FY+1 from Yahoo -> FY+2 = FY+1 x Nasdaq FY+2/FY+1; monthly
            log-interpolation, so it blends the years by how far into the current fiscal year we are)
  ENTER   trailing 12m growth AND each forecast fiscal year (Cur, +1, +2) >= 30% x relPE
  EXIT    any forecast fiscal year (Cur, +1, +2) < 25% x relPE
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
    tail = pd.Series([c.iloc[-1] + slope * k for k in range(1, 13)], index=pd.date_range(c.index[-1] + pd.offsets.MonthEnd(1), periods=12, freq="ME"))
    return pd.concat([c, tail]), pts, g2


def compute(U, recs, closes, held=(), today=None):
    today = pd.Timestamp(today or date.today())
    last_close = closes.index[closes.index <= today].max()
    m_now = today + pd.offsets.MonthEnd(0)
    rows, curves = [], {}
    for s, info in U["stocks"].items():
        rec = recs.get(s) or {}
        y, n = rec.get("yahoo") or {}, rec.get("nasdaq")
        row = {"sym": s, "name": info["name"], "sector": info["sector"], "ndx": info["ndx"], "sp500": info["sp500"], "held": s in held}
        col = closes[s].loc[:last_close].dropna() if s in closes else pd.Series(dtype=float)
        row["price"] = float(col.iloc[-1]) if len(col) else None
        q = qtr(y.get("qh"))
        last_rep = q[-1][0].normalize() if q else None
        row["last_report"] = None if last_rep is None else str(last_rep.date())
        nxt = (y.get("next") or "")[:10] or None
        row["next_report"] = nxt if nxt and (last_rep is None or (pd.Timestamp(nxt) - last_rep).days > 7) else None
        if last_rep is not None:
            row["days_since_report"] = int((today.normalize() - last_rep).days)
            row["in_review"] = C.REVIEW[0] <= row["days_since_report"] <= C.REVIEW[1]
        tr = (y.get("trend") or {}).get("+1y") or {}
        cur = tr.get("current")
        for k, lab in (("7daysAgo", "rev7"), ("30daysAgo", "rev30"), ("90daysAgo", "rev90")):
            row[lab] = (cur / tr[k] - 1) if cur and tr.get(k) and cur > 0 and tr[k] > 0 else None
        # fiscal-year growth: last (actual), current, +1, +2
        est = y.get("est") or {}
        e0, e1 = est.get("0y") or {}, est.get("+1y") or {}
        c, pts, g2 = curve(y, n)
        if c is not None:
            curves[s] = c
        if y.get("nextFye"):
            f0 = pd.Timestamp(y["nextFye"])
            fl, fll = fy_from_q(q, f0 - pd.DateOffset(years=1)), fy_from_q(q, f0 - pd.DateOffset(years=2))
            row["fy"] = [f0.year - 1, f0.year, f0.year + 1, f0.year + 2]
            row["gLast"] = fl / fll - 1 if fl and fll and fl > 0 and fll > 0 else None
            ya = e0.get("yearAgoEps")                         # analysts' actual for the last fiscal year
            if row["gLast"] is not None and ya and ya > 0 and abs(fl / ya - 1) > 0.15:
                row["lastFlag"] = (f"reported quarters of FY{f0.year - 1} add up to {fl:.2f} vs the analysts’ actual {ya:.2f}: "
                                   f"one-off items (tax, write-downs, gains) likely distort this growth figure")
            row["gCur"] = e0["avg"] / e0["yearAgoEps"] - 1 if e0.get("avg") and e0.get("yearAgoEps") and e0["yearAgoEps"] > 0 and e0["avg"] > 0 else None
            row["g1"] = e1["avg"] / e0["avg"] - 1 if e1.get("avg") and e0.get("avg") and e0["avg"] > 0 and e1["avg"] > 0 else None
            row["g2"] = g2
        row["fy_points"] = [[str(pd.Timestamp(d).date()), round(float(v), 4)] for d, v in (pts or [])]
        row["qh"] = [[str(d.date()), v] for d, v in q][-12:]
        row["n_analysts"] = int(e1.get("numberOfAnalysts") or 0)
        ttm, ttm_ago = ttm_pair(q, today)
        fx = y.get("fx") or 1.0
        if row["price"] is None:
            row.update(state="NO DATA", why="no price")
        elif not ttm or ttm <= 0 or not ttm_ago or ttm_ago <= 0:
            row.update(state="NO DATA", why="trailing EPS missing or not positive")
        elif c is None or m_now not in c.index or (m_now + pd.offsets.MonthEnd(24)) not in c.index:
            row.update(state="NO DATA", why="no 2-year consensus")
        else:
            row["ttm"] = ttm
            row["pe"] = row["price"] * fx / ttm
            row["trail"] = ttm / ttm_ago - 1
            row["fwd"] = math.exp((c[m_now + pd.offsets.MonthEnd(24)] - c[m_now]) / 2) - 1
        rows.append(row)
    D = pd.DataFrame(rows)
    ok = D.pe.notna() & (D.pe > 0)
    med = D[ok].groupby("sector").pe.median()
    D["sector_pe"] = D.sector.map(med)
    D["relpe"] = D.pe / D.sector_pe
    D["need_in"], D["need_out"] = C.ENTRY * D.relpe, C.EXIT * D.relpe
    for i, x in D.iterrows():
        if x.get("state") == "NO DATA":
            continue
        g2 = x.g2 if pd.notna(x.g2) else x.g1
        yrs = {"Cur": x.gCur, "+1": x.g1, "+2" if pd.notna(x.g2) else "+2 (=+1)": g2}
        if any(pd.isna(v) for v in yrs.values()):
            D.at[i, "state"], D.at[i, "why"] = "NO DATA", "fiscal-year consensus incomplete"
            continue
        weak_k, weak_v = min(yrs.items(), key=lambda kv: kv[1])
        enter = x.trail >= x.need_in and weak_v >= x.need_in
        exit_ = weak_v < x.need_out
        if x.held:
            room = weak_v - x.need_out
            st = "SELL" if exit_ else "HOLD"
            why = (f"{weak_k} year {weak_v:+.0%} < exit bar {x.need_out:.0%}" if exit_ else f"weakest forecast year ({weak_k}) {weak_v:+.0%} >= exit bar {x.need_out:.0%}")
        else:
            lows = {"trailing": x.trail, **yrs}
            lk, lv = min(lows.items(), key=lambda kv: kv[1])
            room = lv - x.need_in
            if enter:
                st, why = "BUY", f"trailing {x.trail:+.0%} and every forecast year (weakest {weak_k} {weak_v:+.0%}) >= entry bar {x.need_in:.0%}"
            else:
                st = "WATCH" if room >= -C.WATCH and not exit_ else "OUT"
                why = f"{lk} {lv:+.0%} is {-room * 100:.1f} pts short of the entry bar {x.need_in:.0%}"
        D.at[i, "state"], D.at[i, "why"], D.at[i, "room"] = st, why, room
    D.attrs["price_date"] = str(last_close.date())
    D.attrs["curves"] = curves
    D.attrs["sector_pe"] = {k: round(float(v), 1) for k, v in med.items()}
    return D
