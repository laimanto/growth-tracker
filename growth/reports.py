"""Forecasts at the last report vs now (Signals tab, Earnings columns).

  last report set = the consensus as it stood just before the latest report: the last download logged in
                    data/history/forecasts_YYYY.csv dated before the report day (Yahoo + Nasdaq FY+2), run through the
                    same one-off and growth code as today's figures (signals.fy_growth)
  approximate (≈) = no download before the report was logged (history starts 2026-10-21): Yahoo's own 7 / 30 / 60 / 90
                    days-ago consensus (one-time seed, current and next fiscal year only, no analyst counts)
  now             = today's figures (the Signals growth columns); they keep updating until the next report, which then
                    becomes the new last report set automatically (the reference moves with the report date)
Both sets are aligned by fiscal year, so after a fiscal-year rollover (Q4 report) the year just ended compares its
actual growth with what analysts expected before the report, and a newly added year has no last-report value.
"""
import glob
import os

import pandas as pd

import config as C
import signals

HIST = os.path.join(C.DATA, "history")
EST_KEYS = {"avg": "avg", "low": "low", "high": "high", "n": "numberOfAnalysts", "year_ago": "yearAgoEps"}


def load_history():
    fs = sorted(glob.glob(os.path.join(HIST, "forecasts_*.csv")))
    if not fs:
        return pd.DataFrame()
    f = pd.concat([pd.read_csv(p, dtype={"date": str, "end": str}) for p in fs], ignore_index=True)
    return f[f.sym.notna()]


def _yahoo_est(g):
    est = {}
    for r in g.itertuples():
        est[r.period] = {k2: float(getattr(r, k1)) for k1, k2 in EST_KEYS.items() if pd.notna(getattr(r, k1))}
    return est


def prior(sym, y, H):
    """Fiscal-year growth set before the latest report (signals.fy_growth output + asof / approx), or None."""
    q = signals.qtr(y.get("qh"))
    if not q or H is None or not len(H):
        return None
    rep = str(q[-1][0].date())
    h = H[(H.sym == sym) & (H.date < rep)]
    if not len(h):
        return None
    yb = h[h.src == "yahoo"]
    nb = h[(h.src == "nasdaq") & (h.period == "FY")]
    n_prev = None
    if len(nb):
        g = nb[nb.date == nb.date.max()]
        n_prev = {"rows": [{"fye": r.end, "eps": r.avg, "n": None if pd.isna(r.n) else int(r.n)} for r in g.itertuples() if pd.notna(r.avg)]}
    q_prev = [[d, v] for d, v in (y.get("qh") or []) if d[:10] < rep]
    if len(yb):
        d = yb.date.max()
        g = yb[yb.date == d]
        est = _yahoo_est(g)
        e0 = g[g.period == "0y"]
        if not len(e0):
            return None
        yp = dict(y, est=est, nextFye=e0.end.iloc[0], qh=q_prev)
        approx = None
    else:                                                    # seed: Yahoo's days-ago consensus, before history began
        tb = h[h.src == "yahoo_trend"]
        if not len(tb):
            return None
        d = tb.date.max()
        g = tb[tb.date == d]
        e0, e1 = g[g.period == "0y"], g[g.period == "+1y"]
        if not len(e0):
            return None
        end0 = e0.end.iloc[0]
        ya = H[(H.sym == sym) & (H.src == "yahoo") & (H.period == "0y") & (H.end == end0)].year_ago.dropna()
        est = {"0y": {"avg": float(e0.avg.iloc[0])}}
        if len(ya):
            est["0y"]["yearAgoEps"] = float(ya.iloc[0])
        if len(e1):
            est["+1y"] = {"avg": float(e1.avg.iloc[0])}
        yp = dict(y, est=est, nextFye=end0, qh=q_prev)
        approx = "Yahoo’s consensus from about that date (its 7 / 30 / 60 / 90 days-ago figures; no analyst counts)"
    yp, _ = signals.one_offs(yp)
    o = signals.fy_growth(yp, n_prev, signals.qtr(yp.get("qh")))
    if not o:
        return None
    o.update(asof=d, report=rep, approx=approx)
    return o


def _cnt(*v):
    v = [x for x in v if x is not None and not pd.isna(x)]
    return int(sum(v)) if v else None


def revised(sym, y, H):
    """Analysts who revised next year's forecast since the latest report (Yahoo counts, +1y):
    up to day 7 the 7-day count; days 8-14 the 7-day count plus the one logged ~7 days earlier; later the 30-day
    count. Returns {"rev", "n", "how"} or None. Yahoo keeps no per-analyst data: a count, not names."""
    q = signals.qtr(y.get("qh"))
    if not q or not y.get("asof"):
        return None
    rep, asof = q[-1][0].normalize(), pd.Timestamp(y["asof"])
    dsr = (asof - rep).days
    rv = (y.get("revs") or {}).get("+1y") or {}
    n = ((y.get("est") or {}).get("+1y") or {}).get("numberOfAnalysts")
    n = int(n) if n and not pd.isna(n) else 0
    now7 = _cnt(rv.get("upLast7days"), rv.get("downLast7Days", rv.get("downLast7days")))
    now30 = _cnt(rv.get("upLast30days"), rv.get("downLast30days"))
    if dsr <= 0:
        return {"rev": 0, "n": n, "how": "no download since the report yet"}
    if now7 is None:
        return None
    if dsr <= 7:
        return {"rev": now7, "n": n, "how": f"Yahoo’s 7-day revision count on {y['asof']}"}
    if dsr <= 14 and H is not None and len(H):
        g = H[(H.sym == sym) & (H.src == "yahoo") & (H.period == "+1y")]
        tgt = asof - pd.Timedelta(days=7)
        g = g[(pd.to_datetime(g.date) > rep) & ((pd.to_datetime(g.date) - tgt).abs() <= pd.Timedelta(days=2))]
        if len(g):
            r0 = g.iloc[((pd.to_datetime(g.date) - tgt).abs()).argmin()]
            c0 = _cnt(r0.up7, r0.dn7)
            if c0 is not None:
                return {"rev": now7 + c0, "n": n, "how": f"Yahoo’s 7-day revision counts on {r0.date} and {y['asof']} added"}
    if now30 is not None:
        return {"rev": now30, "n": n, "how": f"Yahoo’s 30-day revision count on {y['asof']} (may include a few revisions just before the report)"}
    return None


def revisions(recs, H):
    """revised() for every stock (second share classes use the main class)."""
    out = {}
    for s, rec in recs.items():
        y = (rec or {}).get("yahoo") or {}
        if y:
            out[s] = revised(C.SHARE_CLASS.get(s, s), y, H)
    return out


def report_log(log=print):
    """data/history/report_revisions.csv, rebuilt from the logged downloads: per stock and report, analysts revising
    next year's forecast by day 7 / 14 / 30 / 90 (Yahoo; Zacks 4-week count at day ~30) and the next-year consensus
    change vs the last download before the report by day 3 / 7 / 14 / 30 / 90. Blank = no download near that day
    (or a fiscal-year rollover changed which year is 'next'). To calibrate when forecasts count as settled."""
    H = load_history()
    pa = os.path.join(HIST, "actuals.csv")
    if not len(H) or not os.path.exists(pa):
        return
    A = pd.read_csv(pa, dtype=str)
    first = H[H.src == "yahoo"].date.min()
    A = A[A.report_date > first].drop_duplicates(["sym", "report_date"])
    Y = H[(H.src == "yahoo") & (H.period == "+1y")].copy()
    Y["ts"] = pd.to_datetime(Y.date)
    Z = H[(H.src == "nasdaq") & (H.period == "FY")].copy()
    Z["ts"] = pd.to_datetime(Z.date)
    Yg, Zg = dict(tuple(Y.groupby("sym"))), dict(tuple(Z.groupby("sym")))
    rows = []
    for a in A.itertuples():
        g = Yg.get(a.sym)
        if g is None:
            continue
        rp = pd.Timestamp(a.report_date)
        pre = g[g.ts < rp]
        if not len(pre):
            continue
        pre = pre.loc[pre.ts.idxmax()]
        at = lambda m: (lambda x: x.loc[x.ts.idxmin()] if len(x) else None)(g[(g.ts >= rp + pd.Timedelta(days=m)) & (g.ts <= rp + pd.Timedelta(days=m + 4))])
        snap = {m: at(m) for m in (3, 7, 14, 30, 60, 90)}
        c7 = lambda x: None if x is None else _cnt(x.up7, x.dn7)
        c30 = lambda x: None if x is None else _cnt(x.up30, x.dn30)
        chg = lambda x: (round(x.avg / pre.avg - 1, 5) if x is not None and x.end == pre.end and pre.avg and pre.avg > 0 and pd.notna(x.avg) else None)
        r = {"sym": a.sym, "report_date": a.report_date, "fy_end": pre.end, "n_pre": _cnt(pre.n),
             "n_d7": None if snap[7] is None else _cnt(snap[7].n),
             "rev_d7": c7(snap[7]), "rev_d14": (c7(snap[7]) + c7(snap[14]) if c7(snap[7]) is not None and c7(snap[14]) is not None else None),
             "rev_d30": c30(snap[30]),
             "rev_d90": (c30(snap[30]) + c30(snap[60]) + c30(snap[90]) if None not in (c30(snap[30]), c30(snap[60]), c30(snap[90])) else None)}
        z = Zg.get(a.sym)
        if z is not None:
            z = z[(z.ts >= rp + pd.Timedelta(days=28)) & (z.ts <= rp + pd.Timedelta(days=34))]
            z = z[(pd.to_datetime(z.end) - pd.Timestamp(pre.end)).abs() <= pd.Timedelta(days=40)]
            if len(z):
                zr = z.loc[z.ts.idxmin()]
                r.update(z_n=_cnt(zr.n), z_rev_4w=_cnt(zr.up30, zr.dn30))
        r.update({f"chg_d{m}": chg(snap[m]) for m in (3, 7, 14, 30, 90)})
        r["snap_dates"] = " ".join(f"d{m}:{snap[m].date}" for m in snap if snap[m] is not None)
        rows.append(r)
    cols = ["sym", "report_date", "fy_end", "n_pre", "n_d7", "rev_d7", "rev_d14", "rev_d30", "rev_d90", "z_n", "z_rev_4w",
            "chg_d3", "chg_d7", "chg_d14", "chg_d30", "chg_d90", "snap_dates"]
    pd.DataFrame(rows, columns=cols).sort_values(["report_date", "sym"]).to_csv(os.path.join(HIST, "report_revisions.csv"), index=False)
    log(f"report_revisions.csv: {len(rows)} reports since {first}")


def attach(S, recs, H=None, log=print):
    """Add the last-report columns to the signals frame (aligned with today’s fiscal years). recs = raw downloads
    (prior() applies the one-off adjustment itself, as signals.compute does on today’s data)."""
    H = load_history() if H is None else H
    out = {k: [] for k in ("p_g", "p_eps", "p_n", "p_asof", "p_approx", "p_fy")}
    n_ok = n_apx = 0
    for r in S.itertuples():
        y = ((recs.get(r.sym) or {}).get("yahoo")) or {}
        p = prior(C.SHARE_CLASS.get(r.sym, r.sym), y, H) if y else None   # GOOG -> GOOGL history
        fy = r.fy if isinstance(r.fy, list) else None
        if not p or not fy:
            for k in out:
                out[k].append(None)
            continue
        g_by = {p["fy"][0]: p.get("gLast"), p["fy"][1]: p.get("gCur"), p["fy"][2]: p.get("g1"), p["fy"][3]: p.get("g2")}
        out["p_g"].append([g_by.get(f) for f in fy])
        out["p_eps"].append([(p.get("eps") or {}).get(f) for f in fy])
        out["p_n"].append((p.get("nfy") or {}).get(p["fy"][2]))     # analysts on the next fiscal year at that time
        out["p_asof"].append(p["asof"])
        out["p_approx"].append(p["approx"])
        out["p_fy"].append(p["fy"])
        n_ok += 1
        n_apx += bool(p["approx"])
    for k, v in out.items():
        S[k] = v
    log(f"last-report forecasts: {n_ok} stocks ({n_apx} approximate from Yahoo's days-ago figures)")
    return S
