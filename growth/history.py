"""Forecast history (append-only, data/history/): the real record needed to check the backtest's forecast-error
assumption (anchored forecasts w 0.6 + 30% log error, AR 0.9 - Research Part 19) and refit it later
(growth/forecast_check.py).

  forecasts_YYYY.csv  every consensus download, as fetched (before one-off adjustments):
                        date, sym, src, period, end, avg, low, high, n, year_ago, up7, dn7, up30, dn30
                        src yahoo: period 0q / +1q / 0y / +1y (end = fiscal-year end; quarter ends approximate)
                        src nasdaq: period FY, end = fiscal-year end (FY+1..FY+3)
                        src yahoo_trend: one-time seed from Yahoo's 7 / 30 / 60 / 90 days-ago consensus (0y / +1y)
  actuals.csv         reported quarterly EPS and the consensus estimate just before the report:
                        sym, report_date, actual, estimate, first_seen  (a new row only for a new or restated quarter)
  signals_YYYY.csv    the rule's inputs and outputs per stock per price date (re-runs replace that date):
                        date, sym, state, held, ready, price, pe, sector_pe, trail, gLast, gCur, g1, g2, fwd, n_analysts,
                        target, upside, tkind

Plain CSV, one row per record, so the daily git commits stay small diffs and `git pull` brings the whole history home.
"""
import csv
import os
from datetime import date

import pandas as pd

import config as C

HIST = os.path.join(C.DATA, "history")
F_COLS = ["date", "sym", "src", "period", "end", "avg", "low", "high", "n", "year_ago", "up7", "dn7", "up30", "dn30"]
A_COLS = ["sym", "report_date", "actual", "estimate", "first_seen"]
S_COLS = ["date", "sym", "state", "held", "ready", "price", "pe", "sector_pe", "trail", "gLast", "gCur", "g1", "g2", "fwd",
          "n_analysts", "target", "upside", "tkind"]


def _num(v, nd=5):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return ""
    return "" if v != v else (int(round(v)) if nd == 0 else round(v, nd))


def _read(path, cols):
    if not os.path.exists(path):
        return pd.DataFrame(columns=cols)
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def _append(path, cols, rows):
    if not rows:
        return
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(cols)
        w.writerows([[r.get(c, "") for c in cols] for r in rows])


def _yahoo_rows(sym, y):
    est, revs, d = y.get("est") or {}, y.get("revs") or {}, y["asof"]
    f0 = pd.Timestamp(y["nextFye"]) if y.get("nextFye") else None
    q0 = pd.Timestamp(y["mrq"]) + pd.offsets.MonthEnd(3) if y.get("mrq") else None
    ends = {"0y": f0, "+1y": f0 + pd.DateOffset(years=1) if f0 is not None else None,
            "0q": q0, "+1q": q0 + pd.offsets.MonthEnd(3) if q0 is not None else None}
    out = []
    for p in ("0q", "+1q", "0y", "+1y"):
        e = est.get(p) or {}
        if e.get("avg") is None:
            continue
        r = revs.get(p) or {}
        out.append({"date": d, "sym": sym, "src": "yahoo", "period": p, "end": str(ends[p].date()) if ends[p] is not None else "",
                    "avg": _num(e.get("avg")), "low": _num(e.get("low")), "high": _num(e.get("high")),
                    "n": _num(e.get("numberOfAnalysts"), 0), "year_ago": _num(e.get("yearAgoEps")),
                    "up7": _num(r.get("upLast7days"), 0), "dn7": _num(r.get("downLast7Days"), 0),
                    "up30": _num(r.get("upLast30days"), 0), "dn30": _num(r.get("downLast30days"), 0)})
    return out


def _trend_rows(sym, y):
    """One-time seed: Yahoo's consensus 7 / 30 / 60 / 90 days before the first logged download."""
    tr, d = y.get("trend") or {}, pd.Timestamp(y["asof"])
    f0 = pd.Timestamp(y["nextFye"]) if y.get("nextFye") else None
    out = []
    for p, end in (("0y", f0), ("+1y", f0 + pd.DateOffset(years=1) if f0 is not None else None)):
        for k, days in (("90daysAgo", 90), ("60daysAgo", 60), ("30daysAgo", 30), ("7daysAgo", 7)):
            v = (tr.get(p) or {}).get(k)
            if v is not None:
                out.append({"date": str((d - pd.Timedelta(days=days)).date()), "sym": sym, "src": "yahoo_trend", "period": p,
                            "end": str(end.date()) if end is not None else "", "avg": _num(v)})
    return out


def record(recs, S=None, TG=None, log=print):
    """Append today's new downloads (raw, un-adjusted per-stock records), new reported quarters and the day's signals."""
    os.makedirs(HIST, exist_ok=True)
    today = date.today().isoformat()
    # ---- consensus downloads: one batch per (sym, source, download date)
    seen, years = set(), sorted({today[:4], str(int(today[:4]) - 1)})
    for yr in years:
        f = _read(os.path.join(HIST, f"forecasts_{yr}.csv"), F_COLS)
        seen |= set(zip(f.sym, f.src, f.date))
    known = {s for s, _, _ in seen}
    rows = []
    for s, rec in recs.items():
        if s in C.SHARE_CLASS:
            continue
        y, n = rec.get("yahoo") or {}, rec.get("nasdaq") or {}
        if y.get("asof") and (s, "yahoo", y["asof"]) not in seen:
            if s not in known:
                rows += _trend_rows(s, y)
            rows += _yahoo_rows(s, y)
        if n.get("asof") and (s, "nasdaq", n["asof"]) not in seen:
            rows += [{"date": n["asof"], "sym": s, "src": "nasdaq", "period": "FY", "end": r.get("fye", ""),
                      "avg": _num(r.get("eps")), "n": _num(r.get("n"), 0)} for r in n.get("rows") or []]
    rows = [r for r in rows if (r["sym"], r["src"], r["date"]) not in seen]
    for yr in sorted({r["date"][:4] for r in rows}):
        _append(os.path.join(HIST, f"forecasts_{yr}.csv"), F_COLS, [r for r in rows if r["date"][:4] == yr])
    # ---- reported quarters (Yahoo keeps only a window; this file keeps all of them)
    pa = os.path.join(HIST, "actuals.csv")
    a = _read(pa, A_COLS)
    have = {(r.sym, r.report_date): r.actual for r in a.itertuples()}
    arows = []
    for s, rec in recs.items():
        if s in C.SHARE_CLASS:
            continue
        y = rec.get("yahoo") or {}
        qe = dict(y.get("qe") or [])
        for d, v in y.get("qh") or []:
            k, v = (s, d[:10]), _num(v)
            if v != "" and have.get(k) != str(v):
                arows.append({"sym": s, "report_date": d[:10], "actual": v, "estimate": _num(qe.get(d)), "first_seen": today})
                have[k] = str(v)
    _append(pa, A_COLS, arows)
    # ---- the day's signals (inputs and outputs of the rule); a re-run replaces that price date
    n_sig = 0
    if S is not None and len(S):
        d = S.attrs["price_date"]
        ps = os.path.join(HIST, f"signals_{d[:4]}.csv")
        tg = TG if TG is not None else pd.DataFrame()
        srows = []
        for r in S.to_dict("records"):
            t = tg.loc[r["sym"]] if r["sym"] in tg.index else {}
            srows.append({"date": d, "sym": r["sym"], "state": r.get("state", ""), "held": int(bool(r.get("held"))),
                          "ready": "" if r.get("ready") is None else int(bool(r.get("ready"))),
                          **{k: _num(r.get(k), 4) for k in ("price", "pe", "sector_pe", "trail", "gLast", "gCur", "g1", "g2", "fwd")},
                          "n_analysts": _num(r.get("n_analysts"), 0), "target": _num(t.get("target") if len(t) else None, 2),
                          "upside": _num(t.get("upside") if len(t) else None, 4), "tkind": t.get("kind", "") if len(t) else ""})
        old = _read(ps, S_COLS)
        if (old.date == d).any():
            old[old.date != d].to_csv(ps, index=False)
        _append(ps, S_COLS, srows)
        n_sig = len(srows)
    log(f"history: {len(rows)} forecast rows, {len(arows)} reported quarters, {n_sig} signal rows -> data/history/")
