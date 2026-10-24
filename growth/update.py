"""Delta update - download only what is due:
  universe     Wikipedia S&P 500 + Nasdaq-100 lists, monthly
  eps/<sym>    Yahoo consensus + quarters + revisions + next report date:
                 never fetched / a report since the last fetch / every 2 days in the post-report window /
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
from datetime import date

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
    elif last_rep and C.REVIEW[0] - 3 <= (date.today() - date.fromisoformat(last_rep)).days <= C.REVIEW[1] and _age(ya) >= C.EPS_REVIEW_AGE:
        why_y = "post-report window"
    elif _age(ya) >= C.EPS_MAX_AGE or (_age(ya) >= 4 and zlib.crc32(sym.encode()) % 5 == date.today().weekday() % 5):
        why_y = "weekly refresh"
    if "." in sym or sym.startswith("^"):
        pass
    elif not n.get("asof") or rec.get("retry_n") or _age(n.get("asof")) >= C.NASDAQ_MAX_AGE or (why_y == "reported since last fetch"):
        why_n = "FY+2 refresh"
    return why_y, why_n


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

    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(work, jobs))
    bad = [s for s in syms if load_stock(s).get("retry_y")]
    if bad:
        log(f"EPS: {len(bad)} still incomplete, retried tomorrow: {' '.join(bad[:30])}")
    return len(jobs)


# ---------------------------------------------------------------- prices
def settled(df):
    """Drop bars whose session has not closed yet (a run during market hours would otherwise store intraday prices as
    the close). US tickers: day D counts after 16:15 New York time on D; ^HSI after 16:30 Hong Kong time on D."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    ny = datetime.now(ZoneInfo("America/New_York"))
    hk = datetime.now(ZoneInfo("Asia/Hong_Kong"))
    us_last = pd.Timestamp(ny.date()) if (ny.hour, ny.minute) >= (16, 15) else pd.Timestamp(ny.date()) - pd.Timedelta(days=1)
    hk_last = pd.Timestamp(hk.date()) if (hk.hour, hk.minute) >= (16, 30) else pd.Timestamp(hk.date()) - pd.Timedelta(days=1)
    df = df.copy()
    hk_cols = [c for c in df.columns if c.startswith("^HSI")]
    us_cols = [c for c in df.columns if c not in hk_cols]
    df.loc[df.index > us_last, us_cols] = float("nan")
    df.loc[df.index > hk_last, hk_cols] = float("nan")
    return df.dropna(how="all")


def _merge(path, new):
    old = pd.read_csv(path, index_col=0, parse_dates=True) if os.path.exists(path) else None
    if old is not None:
        new = pd.concat([old[~old.index.isin(new.index)], new]).sort_index()
        new = new.loc[:, ~new.columns.duplicated()]
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
