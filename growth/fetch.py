"""Downloads (Yahoo via yfinance, Nasdaq analyst API). One function per source, per stock - the caller decides
WHAT is due (update.py), so a normal day only touches stocks that need it."""
import time
from datetime import date

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
        out["trend"] = {p: {k: (None if pd.isna(tr.loc[p, k]) else float(tr.loc[p, k])) for k in tr.columns if k != "currency"}
                        for p in ("0y", "+1y") if p in tr.index}
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
            rows = (((d.get("data") or {}).get("yearlyForecast") or {}).get("rows")) or []
            res = []
            for r in rows:
                mon, yr = r["fiscalEnd"].split()
                fye = (pd.Timestamp(int(yr), _MON[mon[:3]], 1) + pd.offsets.MonthEnd(0)).date()
                res.append({"fye": str(fye), "eps": r.get("consensusEPSForecast"), "n": int(r.get("noOfEstimates") or 0)})
            return {"asof": date.today().isoformat(), "rows": res}
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
