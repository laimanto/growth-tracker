"""Delta update - download only what is due:
  universe     Wikipedia S&P 500 + Nasdaq-100 lists, monthly
  eps/<sym>    Yahoo consensus + quarters + revisions + next report date:
                 never fetched / a report since the last fetch (daily until its quarter appears) / daily in the
                 first week after a report, every 2 days in the second /
                 daily while the last download failed or came back incomplete, or the latest quarter is missing /
                 otherwise weekly, spread over the week (stable hash of the ticker -> weekday)
               Nasdaq FY+2: every 2 weeks, and after a report
  closes.csv   universe closes: only the days after the last stored date (re-fetching the last 3 for revisions)
  bench.csv    benchmark closes + total return since Dec 2021, same delta rule
"""
import io
import json
import os
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pandas as pd

import config as C
import fetch

GICS2Y = {"Information Technology": "Technology", "Financials": "Financial Services", "Health Care": "Healthcare",
          "Consumer Discretionary": "Consumer Cyclical", "Consumer Staples": "Consumer Defensive", "Materials": "Basic Materials",
          "Industrials": "Industrials", "Utilities": "Utilities", "Real Estate": "Real Estate", "Energy": "Energy",
          "Communication Services": "Communication Services"}
WIKI_UA = {"User-Agent": "Mozilla/5.0 GrowthTracker"}


def jload(p, default=None):
    try:
        return json.load(open(p, encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return default


def jsave(p, obj, indent=None):
    tmp = p + ".tmp"
    json.dump(obj, open(tmp, "w", encoding="utf-8"), indent=indent, separators=(",", ":") if indent is None else None)
    os.replace(tmp, p)


def eps_path(sym):
    return os.path.join(C.EPS_DIR, sym.replace("^", "_") + ".json")


def load_stock(sym):
    return jload(eps_path(sym), {}) or {}


# ---------------------------------------------------------------- universe
def universe(log=print):
    u = jload(C.UNIVERSE)
    if u and (date.today() - date.fromisoformat(u["asof"])).days < C.UNIVERSE_MAX_AGE:
        return u
    import requests
    log("universe: refreshing S&P 500 / Nasdaq-100 lists")
    html = requests.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", headers=WIKI_UA, timeout=60).text
    sp = pd.read_html(io.StringIO(html))[0]
    sp["sym"] = sp.Symbol.str.replace(".", "-", regex=False)
    ndx = set()
    try:
        r = requests.get("https://en.wikipedia.org/w/api.php", params={"action": "parse", "page": "Nasdaq-100", "prop": "text", "format": "json"},
                         headers=WIKI_UA, timeout=60).json()
        for t in pd.read_html(io.StringIO(r["parse"]["text"]["*"])):
            c = [x for x in t.columns if str(x).lower() in ("ticker", "symbol")]
            if c and len(t) >= 90:
                ndx = {str(v).replace(".", "-") for v in t[c[0]]}
                break
    except Exception:
        pass
    if not ndx:
        snap = jload(C.NDX_SNAP, {})
        ndx = set(snap[max(snap)]) if snap else {s for s, v in (u or {}).get("stocks", {}).items() if v.get("ndx")}
    old = (u or {}).get("stocks", {})
    stocks = {}
    for r in sp.itertuples():
        added = pd.to_datetime(r._6, errors="coerce")
        stocks[r.sym] = {"name": r.Security, "sector": (old.get(r.sym) or {}).get("sector") or GICS2Y.get(r._3, r._3), "sp500": True,
                         "sp500_added": None if pd.isna(added) else str(added.date()), "ndx": r.sym in ndx}
    for s in ndx - set(stocks):
        o = old.get(s) or {}
        stocks[s] = {"name": o.get("name", s), "sector": o.get("sector") or load_stock(s).get("yahoo", {}).get("sector") or "Unknown",
                     "sp500": False, "sp500_added": None, "ndx": True}
    u = {"asof": date.today().isoformat(), "stocks": stocks}
    jsave(C.UNIVERSE, u, indent=0)
    return u


# ---------------------------------------------------------------- EPS
def _age(d):
    return 9999 if not d else (date.today() - date.fromisoformat(d[:10])).days


def milestone(last_rep, asof):
    """Revision study: a download on (or just after) day 7 / 14 / 30 / 60 / 90 after a report, so the 7-day and 30-day
    revision counts can be added up to 14 and 90 days (reports.report_log). Returns the day or None."""
    if not last_rep or not asof:
        return None
    r = date.fromisoformat(last_rep[:10])
    age = (date.today() - r).days
    for m in C.REV_MILESTONES:
        if m <= age <= m + 4 and asof[:10] < str(r + timedelta(days=m)):
            return m
    return None


def due(sym, rec):
    """Why the Yahoo / Nasdaq data of one stock must be refreshed today (or None)."""
    y, n = rec.get("yahoo") or {}, rec.get("nasdaq") or {}
    ya, today = y.get("asof"), date.today().isoformat()
    last_rep = max((q[0][:10] for q in y.get("qh") or []), default=None)
    nxt = (y.get("next") or "")[:10]
    why_y = why_n = None
    rep_age = (date.today() - date.fromisoformat(last_rep)).days if last_rep else None
    if not ya:
        why_y = "new"
    elif rec.get("retry_y") and _age(ya) >= 1:
        why_y = "retry: last download failed or incomplete"
    elif rep_age is not None and 120 < rep_age < 400 and _age(ya) >= 1:
        why_y = "retry: latest reported quarter missing"
    elif nxt and ya < nxt <= today:
        why_y = "reported since last fetch"
    elif nxt and nxt <= today and _age(ya) >= 1 and (not last_rep or last_rep < nxt) and _age(nxt) <= C.REPORT_WAIT:
        # report day passed but its quarter is not in the data yet (an after-close report downloaded the same morning:
        # MSFT 2026-10-28) -> daily until it appears
        why_y = "report not in data yet"
    elif rep_age is not None and rep_age <= C.EPS_DAILY_DAYS and _age(ya) >= 1:
        why_y = "post-report week (daily)"
    elif rep_age is not None and rep_age <= C.REVIEW[1] and _age(ya) >= C.EPS_REVIEW_AGE:
        why_y = "post-report window"
    elif (m := milestone(last_rep, ya)) is not None:
        why_y = f"day-{m} snapshot after the report"
    elif _age(ya) >= C.EPS_MAX_AGE or (_age(ya) >= 4 and zlib.crc32(sym.encode()) % 5 == date.today().weekday() % 5):
        why_y = "weekly refresh"
    if "." in sym or sym.startswith("^"):
        pass
    elif (not n.get("asof") or rec.get("retry_n") or _age(n.get("asof")) >= C.NASDAQ_MAX_AGE or why_y in ("reported since last fetch", "report not in data yet")
          or (rep_age is not None and C.REVIEW[0] <= rep_age <= C.REVIEW[1] and n["asof"] < str(date.fromisoformat(last_rep) + timedelta(days=C.REVIEW[0])))
          or milestone(last_rep, n.get("asof")) is not None):
        # ... and once more 3+ days after a report, when analysts have updated FY+2 too
        why_n = "FY+2 refresh"
    return why_y, why_n


def guard_quarters(y, old):
    """Yahoo sometimes returns the quarterly history on another basis (APH 2026-11-01: older quarters doubled, the new
    quarter not). When the quarters both downloads share differ by > 20% (median), keep the history whose last
    fiscal year adds up closest to the analysts' actual (yearAgoEps) and only add the newer quarters to it.
    Returns a note when the stored history was kept, else None."""
    oq, nq = dict(old.get("qh") or []), dict(y.get("qh") or [])
    both = [d for d in nq if d in oq and oq[d] and nq[d] and oq[d] > 0 and nq[d] > 0]
    if len(both) < 2:
        return None
    r = float(pd.Series([nq[d] / oq[d] for d in both]).median())
    if abs(r - 1) <= 0.2:
        return None
    ya = ((y.get("est") or {}).get("0y") or {}).get("yearAgoEps")
    lf = y.get("lastFye")
    def fy_sum(q):
        if not lf:
            return None
        f = pd.Timestamp(lf)
        x = [v for d, v in q.items() if f - pd.Timedelta(days=270) < pd.Timestamp(d) <= f + pd.Timedelta(days=110)]
        return sum(x) if len(x) == 4 else None
    so, sn = fy_sum(oq), fy_sum(nq)
    err = lambda s: abs(s / ya - 1) if s and ya else 9
    if err(sn) <= err(so):
        return None                                          # the new download matches the analysts (e.g. a stock split)
    last_old = max(oq)
    add = {d: v for d, v in nq.items() if d > last_old}
    y["qh"] = [[d, v] for d, v in sorted({**oq, **add}.items())][-24:]
    qe = {**dict(old.get("qe") or []), **{d: v for d, v in (y.get("qe") or []) if d in add}}
    y["qe"] = [[d, qe[d]] for d, _ in y["qh"] if d in qe]
    return f"quarters on another basis (x{r:.2f}): kept the stored history, added {len(add)} new"


def refresh_eps(syms, workers=6, log=print):
    jobs = []
    for s in syms:
        if s in C.SHARE_CLASS:                              # second share class: uses the main class's file
            continue
        rec = load_stock(s)
        wy, wn = due(s, rec)
        if wy or wn:
            jobs.append((s, rec, wy, wn))
    log(f"EPS: {len(jobs)} of {len(syms)} stocks due ({sum(1 for j in jobs if j[2])} Yahoo, {sum(1 for j in jobs if j[3])} Nasdaq)")
    n_retry = sum(1 for j in jobs if (j[2] or "").startswith("retry"))
    if n_retry:
        log(f"EPS: {n_retry} of them are retries of failed / incomplete downloads")

    def work(job):
        s, rec, wy, wn = job
        if wy:
            y, old = fetch.yahoo(s), rec.get("yahoo") or {}
            if y:
                for k in y.get("missing", []):              # keep the last good copy of a part that failed today
                    if old.get(k):
                        y[k] = old[k]
                        if k == "qh" and old.get("next") and not y.get("next"):
                            y["next"] = old["next"]
                note = guard_quarters(y, old) if "qh" not in y.get("missing", []) else None
                if note:
                    guarded.append(f"{s} ({note})")
                rec["yahoo"] = y
            rec["retry_y"] = not y or bool(y.get("missing"))  # -> fetched again on the next daily run
        if wn:
            n = fetch.nasdaq(s)
            if n:
                rec["nasdaq"] = n
            rec["retry_n"] = not n
        for k in ("retry_y", "retry_n"):
            if not rec.get(k):
                rec.pop(k, None)
        jsave(eps_path(s), rec)
        return s

    guarded = []
    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(work, jobs))
    if guarded:
        log(f"EPS: {len(guarded)} kept their stored quarters: {'; '.join(guarded)}")
    bad = [s for s in syms if s not in C.SHARE_CLASS and load_stock(s).get("retry_y")]
    if bad:
        log(f"EPS: {len(bad)} still incomplete, retried tomorrow: {' '.join(bad[:30])}")
    return len(jobs)


# ---------------------------------------------------------------- prices
def _last_settled():
    """Latest finished session date: US after 16:15 New York time, ^HSI after 16:30 Hong Kong time."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    ny = datetime.now(ZoneInfo("America/New_York"))
    hk = datetime.now(ZoneInfo("Asia/Hong_Kong"))
    us_last = pd.Timestamp(ny.date()) if (ny.hour, ny.minute) >= (16, 15) else pd.Timestamp(ny.date()) - pd.Timedelta(days=1)
    hk_last = pd.Timestamp(hk.date()) if (hk.hour, hk.minute) >= (16, 30) else pd.Timestamp(hk.date()) - pd.Timedelta(days=1)
    return us_last, hk_last


def session_done(sym, day):
    us_last, hk_last = _last_settled()
    return day <= (hk_last if sym.startswith("^HSI") else us_last)


def settled(df):
    """Drop bars whose session has not closed yet (a run during market hours would otherwise store intraday prices as
    the close). US tickers: day D counts after 16:15 New York time on D; ^HSI after 16:30 Hong Kong time on D."""
    us_last, hk_last = _last_settled()
    df = df.copy()
    hk_cols = [c for c in df.columns if c.startswith("^HSI")]
    us_cols = [c for c in df.columns if c not in hk_cols]
    df.loc[df.index > us_last, us_cols] = float("nan")
    df.loc[df.index > hk_last, hk_cols] = float("nan")
    return df.dropna(how="all")


def _merge(path, new):
    old = pd.read_csv(path, index_col=0, parse_dates=True) if os.path.exists(path) else None
    if old is not None:
        # cell by cell: a re-downloaded bar overrides the stored one, but a gap in the download keeps the stored value
        # (7 Nov 2026: a stray 30 Oct bar for one ticker replaced the whole stored 30 Oct row with blanks)
        new = new.loc[:, ~new.columns.duplicated()]
        cols = list(old.columns) + [c for c in new.columns if c not in old.columns]
        new = new.combine_first(old)[cols].sort_index()
    new.round(4).to_csv(path, date_format="%Y-%m-%d")
    return new


def refresh_monthly(syms, closes, log=print):
    """Month-end closes for the P/E model's 3-year P/E history (data/closes_m.csv): built once from 3 years of daily
    closes, then extended with month ends from closes.csv."""
    if os.path.exists(C.CLOSES_M):
        m = pd.read_csv(C.CLOSES_M, index_col=0, parse_dates=True)
    else:
        c, _ = fetch.closes(sorted(syms), (pd.Timestamp.today() - pd.DateOffset(years=3, months=3)).strftime("%Y-%m-%d"))
        m = c.resample("ME").last()
        log(f"monthly closes: built {m.shape}")
    gaps = [s for s in syms if s not in m or m[s].isna().sum() > 3]              # failed / new tickers: fetch their history
    if gaps and len(gaps) < len(syms):
        c, _ = fetch.closes(gaps, m.index.min().strftime("%Y-%m-%d"))
        g = c.resample("ME").last().reindex(m.index)
        for s in g.columns:
            m[s] = g[s]
        log(f"monthly closes: filled {len(gaps)} tickers with gaps")
    this_month = pd.Timestamp.today().normalize() + pd.offsets.MonthEnd(0)
    m = m[m.index < this_month]                                          # completed months only
    me = closes.resample("ME").last()
    me = me[(me.index >= m.index.max()) & (me.index < this_month)]       # last stored month may have been partial
    if len(me):
        m = pd.concat([m[~m.index.isin(me.index)], me.reindex(columns=m.columns.union(me.columns))]).sort_index()
    m = m.loc[:, ~m.columns.duplicated()]
    m.round(4).to_csv(C.CLOSES_M, date_format="%Y-%m-%d")
    return m


def refresh_prices(syms, start_universe, log=print):
    out = {}
    for path, tick, first, adj in ((C.CLOSES, sorted(syms), start_universe, False), (C.BENCHF, C.BENCH, "2021-11-30", True)):
        last = pd.read_csv(path, index_col=0, parse_dates=True).index.max() if os.path.exists(path) else None
        start = (last - pd.Timedelta(days=5)).strftime("%Y-%m-%d") if last is not None else first
        c, a = fetch.closes(tick, start)
        c, a, nq = fetch.fill_quotes(c, a, tick, session_done)
        if nq:
            log(f"prices: {os.path.basename(path)} {nq} closes from quotes (daily bar not out yet)")
        if adj:
            a.columns = [f"{x}_TR" for x in a.columns]
            c = pd.concat([c, a], axis=1)
        out[path] = _merge(path, settled(c))
        if path == C.CLOSES:                                     # tickers new to the file: fetch their full history
            new_t = [t for t in tick if t not in out[path] or out[path][t].notna().sum() < 10]
            if new_t and len(new_t) < len(tick):
                c2, _ = fetch.closes(new_t, first)
                c2 = settled(c2)
                R = out[path]
                for t in c2.columns:
                    s2 = c2[t].reindex(R.index.union(c2.index))
                    R = R.reindex(R.index.union(c2.index))
                    R[t] = s2.combine_first(R[t]) if t in R else s2
                R.round(4).to_csv(path, date_format="%Y-%m-%d")
                out[path] = R
                log(f"prices: backfilled {len(c2.columns)} new tickers from {first}: {' '.join(c2.columns[:10])}")
        log(f"prices: {os.path.basename(path)} {start} -> {out[path].index.max().date()} ({len(c)} new rows fetched)")
    return out[C.CLOSES], out[C.BENCHF]
