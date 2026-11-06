"""Downloads (Yahoo via yfinance, Nasdaq analyst API). One function per source, per stock - the caller decides
WHAT is due (update.py), so a normal day only touches stocks that need it."""
import time
from datetime import date

import numpy as np
import pandas as pd

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36",
      "Accept": "application/json"}
_MON = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
_FX = {}


def fx_rate(cur, fcur):
    """Price currency -> reporting currency (EPS) rate, e.g. USD -> EUR for ASML."""
    if not cur or not fcur or cur == fcur:
        return 1.0
    k = (cur, fcur)
    if k not in _FX:
        import yfinance as yf
        h = yf.Ticker(f"{cur}{fcur}=X").history(period="5d")["Close"].dropna()
        _FX[k] = float(h.iloc[-1]) if len(h) else None
    return _FX[k]


def _nums(row, cast):
    """Numeric cells of one Yahoo table row ({column: value}); text columns (currency, and since yfinance 1.8.0
    methodology = 'normalized' / 'gaap', which broke the 4-5 Nov 2026 runs) are skipped."""
    o = {}
    for k, v in row.items():
        if k in ("currency", "methodology"):
            continue
        if pd.isna(v):
            o[k] = None
        elif isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, bool):
            o[k] = cast(v)
    return o


def yahoo(sym):
    """Consensus (FY0 / FY+1, quarters), the last ~24 reported quarters, fiscal year ends, revision history
    (eps_trend), next report date, sector. Returns None if Yahoo gives nothing usable."""
    import yfinance as yf
    t = yf.Ticker(sym)
    out = {"asof": date.today().isoformat()}
    for attempt in range(3):
        try:
            i = t.info or {}
            break
        except Exception:
            time.sleep(2 + 3 * attempt)
    else:
        return None
    ts = lambda k: str(pd.Timestamp(i[k], unit="s").date()) if i.get(k) else None
    out.update(currency=i.get("currency"), fcur=i.get("financialCurrency"), sector=i.get("sector"),
               lastFye=ts("lastFiscalYearEnd"), nextFye=ts("nextFiscalYearEnd"), mrq=ts("mostRecentQuarter"))
    missing = []

    def part(name, f, tries=3):
        """One Yahoo call, retried; an exception or empty answer after all tries -> listed in out["missing"]
        (update.py keeps the previous good copy and retries the stock on the next daily run)."""
        for k in range(tries):
            try:
                r = f()
                if r is not None and len(r):
                    return r
            except Exception:
                pass
            if k < tries - 1:
                time.sleep(1 + 2 * k)
        missing.append(name)
        return None

    ee = part("est", lambda: t.earnings_estimate)
    out["est"] = {} if ee is None else {p: {k: (None if pd.isna(ee.loc[p, k]) else float(ee.loc[p, k]))
                                             for k in ("avg", "low", "high", "yearAgoEps", "numberOfAnalysts") if k in ee.columns}
                                         for p in ("0q", "+1q", "0y", "+1y") if p in ee.index}
    if ee is not None and "methodology" in ee.columns and len(ee):      # yfinance >= 1.8: 'normalized' (adjusted) or 'gaap'
        out["basis"] = str(ee["methodology"].iloc[0])
    ed = part("qh", lambda: t.get_earnings_dates(limit=32))
    out["qh"] = []
    if ed is not None:
        idx = pd.to_datetime(ed.index, utc=True).tz_convert("America/New_York").tz_localize(None)
        ed.index = idx
        ed = ed[~ed.index.duplicated()].sort_index()
        rep = ed.dropna(subset=["Reported EPS"])
        qh = {d: float(v) for d, v in rep["Reported EPS"].items()}
        qe = {d: float(v) for d, v in rep["EPS Estimate"].dropna().items()} if "EPS Estimate" in rep else {}
        # Yahoo's calendar sometimes lists a past report date without its EPS (AVGO / HPE / NTAP, Sep 2026);
        # earnings_history (last 4 quarters, same basis) usually has it -> fill those dates from it
        gaps = ed[ed["Reported EPS"].isna() & (ed.index <= pd.Timestamp.now())].index
        if len(gaps):
            try:
                eh = t.earnings_history
                for qend, a in eh["epsActual"].dropna().items():
                    qend = pd.Timestamp(qend)
                    if any(qend < d <= qend + pd.Timedelta(days=100) for d in qh):
                        continue
                    d = next((d for d in gaps if qend < d <= qend + pd.Timedelta(days=100)), None)
                    if d is not None:
                        qh[d] = float(a)
                        e = eh["epsEstimate"].get(qend) if "epsEstimate" in eh else None
                        if e is not None and pd.notna(e):
                            qe[d] = float(e)
            except Exception:
                pass
        out["qh"] = [[str(d), v] for d, v in sorted(qh.items())][-24:]
        out["qe"] = [[str(d), qe[d]] for d, _ in sorted(qh.items())[-24:] if d in qe]   # consensus estimate per reported quarter
        fut = ed[ed["Reported EPS"].isna() & (ed.index > pd.Timestamp.now())]
        if len(fut):
            out["next"] = str(fut.index[0].date())
    tr = part("trend", lambda: t.eps_trend)
    if tr is not None:
        out["trend"] = {p: _nums(tr.loc[p], float) for p in ("0y", "+1y") if p in tr.index}
    try:                                                     # analysts revising in the last 7 / 30 days (readiness check)
        rv = t.eps_revisions
        if rv is not None and len(rv):
            out["revs"] = {p: _nums(rv.loc[p], int) for p in ("0y", "+1y") if p in rv.index}
    except Exception:
        pass
    if missing:
        out["missing"] = missing
    if "next" not in out:
        try:
            d = (t.calendar or {}).get("Earnings Date")
            if d:
                out["next"] = str(d[0] if isinstance(d, list) else d)[:10]
        except Exception:
            pass
    if out["fcur"] and out["currency"] and out["fcur"] != out["currency"]:
        out["fx"] = fx_rate(out["currency"], out["fcur"])
    return out if (out.get("est") or out.get("qh")) else None


def nasdaq(sym):
    """Yearly consensus EPS rows from Nasdaq (Zacks panel) - used only for the FY+2 / FY+1 growth."""
    import requests
    for i in range(3):
        try:
            d = requests.get(f"https://api.nasdaq.com/api/analyst/{sym.replace('-', '.')}/earnings-forecast", headers=UA, timeout=20).json()
            def parse(rows):
                res = []
                for r in rows or []:
                    mon, yr = r["fiscalEnd"].split()
                    fye = (pd.Timestamp(int(yr), _MON[mon[:3]], 1) + pd.offsets.MonthEnd(0)).date()
                    # up / down = analysts revising in the last 4 weeks (Zacks panel) - logged for the revision study
                    res.append({"fye": str(fye), "eps": r.get("consensusEPSForecast"), "n": int(r.get("noOfEstimates") or 0),
                                "high": r.get("highEPSForecast"), "low": r.get("lowEPSForecast"), "up": r.get("up"), "down": r.get("down")})
                return res
            dd = d.get("data") or {}
            rows = parse((dd.get("yearlyForecast") or {}).get("rows"))
            return {"asof": date.today().isoformat(), "rows": rows, "qrows": parse((dd.get("quarterlyForecast") or {}).get("rows"))}
        except Exception:
            time.sleep(1 + i)
    return None


def closes(tickers, start, retries=2):
    """Daily closes (split-adjusted, not dividend-adjusted) and adjusted closes (total return) from `start`.
    Tickers that come back empty (Yahoo throttling in bulk downloads) are retried in small batches."""
    import yfinance as yf

    def dl(tk):
        d = yf.download(list(tk), start=start, auto_adjust=False, progress=False, threads=True)
        c, a = d["Close"].copy(), d["Adj Close"].copy()
        if isinstance(c, pd.Series):
            c, a = c.to_frame(tk[0]), a.to_frame(tk[0])
        for x in (c, a):
            x.index = pd.to_datetime(x.index).tz_localize(None).normalize()
        return c, a
    c, a = dl(list(tickers))
    for _ in range(retries):
        miss = [s for s in tickers if s not in c or c[s].isna().all()]
        if not miss:
            break
        time.sleep(3)
        for i in range(0, len(miss), 20):
            c2, a2 = dl(miss[i:i + 20])
            for s in c2.columns:
                if c2[s].notna().any():
                    c[s], a[s] = c2[s].reindex(c.index.union(c2.index)), a2[s].reindex(a.index.union(a2.index))
    return c.dropna(how="all"), a.dropna(how="all")


def _quote(sym):
    """Last regular-session price and its exchange-local date (Yahoo chart meta), or None."""
    import yfinance as yf
    try:
        tk = yf.Ticker(sym)
        tk.history(period="1d", auto_adjust=False)
        m = tk.history_metadata or {}
        px, t = m.get("regularMarketPrice"), m.get("regularMarketTime")
        if px is None or t is None:
            return None
        t = pd.Timestamp(t, unit="s", tz="UTC") if not isinstance(t, pd.Timestamp) else t
        tz = m.get("exchangeTimezoneName") or "America/New_York"
        return float(px), (t.tz_convert(tz) if t.tzinfo else t.tz_localize(tz)).tz_localize(None).normalize()
    except Exception:
        return None


def fill_quotes(c, a, tickers, done, workers=8):
    """Yahoo's daily bar for the latest session sometimes appears hours after the close. For each ticker whose
    last close is older than its quote date, add the quote's regular-market price as that day's close (adjusted
    close carried with the previous day's adj/close ratio). done(sym, day) says whether that session has closed;
    the next run's download (5 days back) overwrites these cells with the real bar."""
    from concurrent.futures import ThreadPoolExecutor
    last = {s: (c[s].last_valid_index() if s in c else None) for s in tickers}
    todo = [s for s in tickers if last[s] is not None]
    if not todo:
        return c, a, 0
    newest = max(last[s] for s in todo)
    lag = [s for s in todo if last[s] < newest]
    # probe a few up-to-date tickers: quote all of them only if a newer, finished session shows up
    probe = [s for s in todo if last[s] == newest][:3]
    with ThreadPoolExecutor(workers) as ex:
        q = dict(zip(probe, ex.map(_quote, probe)))
    newer = any(v and v[1] > last[s] and done(s, v[1]) for s, v in q.items())
    rest = lag + ([s for s in todo if last[s] == newest and s not in q] if newer else [])
    with ThreadPoolExecutor(workers) as ex:
        q.update(zip(rest, ex.map(_quote, rest)))
    n = 0
    for s, v in q.items():
        if not v or v[1] <= last[s] or not done(s, v[1]):
            continue
        px, d = v
        if d not in c.index:
            c, a = c.reindex(c.index.union([d])), a.reindex(a.index.union([d]))
        c.loc[d, s] = px
        if s in a and pd.notna(a.loc[last[s], s]) and c.loc[last[s], s]:
            a.loc[d, s] = px * a.loc[last[s], s] / c.loc[last[s], s]
        n += 1
    return c, a, n
