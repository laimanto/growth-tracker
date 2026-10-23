"""Risk / return table for the Performance tab - same convention as the Sortino dashboard (program/engine.py):
DAILY returns, target (MAR) = 4% a year (the risk-free rate):
  annual return      compound annual growth over the window
  downside dev.      sqrt(mean(min(r - MAR_daily, 0)^2)) x sqrt(252), over all days
  Sortino            (annualised arithmetic return - 4%) / downside dev.
  max drawdown       on daily closes
Each series uses its own trading days (HSI: Hong Kong calendar). Windows (common end = the last day of the backtest):
  since 2022 | common (from the latest start of all series) | 2 years | 1 year.
The live model portfolio is shown over its own life once it has 63 trading days (about 3 months).
"""
import numpy as np
import pandas as pd

MAR, DAYS = 0.04, 252


def metrics(r):
    r = r.dropna()
    if len(r) < 40:
        return None
    nav = (1 + r).cumprod()
    yrs = (r.index[-1] - r.index[0]).days / 365.25 or len(r) / DAYS
    mar_d = (1 + MAR) ** (1 / DAYS) - 1
    dd = float(np.sqrt((np.minimum(r - mar_d, 0) ** 2).mean()) * np.sqrt(DAYS))
    return {"cagr": float(nav.iloc[-1] ** (1 / yrs) - 1), "ddev": dd, "sortino": float((r.mean() * DAYS - MAR) / dd) if dd > 0 else None,
            "mdd": float((nav / nav.cummax()).min() - 1), "days": len(r), "from": str(r.index[0].date()), "to": str(r.index[-1].date())}


def table(series, live=None):
    """series: {name: daily returns}; live: {name: daily returns} shown over their own life. -> ({window: {name: metrics}}, labels)"""
    end = min(s.index.max() for s in series.values())
    common = max(s.index.min() for s in series.values())
    W = {"since 2022": pd.Timestamp("2022-01-01"), "common": common, "2 years": end - pd.DateOffset(years=2), "1 year": end - pd.DateOffset(years=1)}
    out = {}
    for w, a in W.items():
        out[w] = {k: metrics(s.loc[a:end]) for k, s in series.items()}
        for k, s in (live or {}).items():
            out[w][k] = metrics(s)
    labels = {w: f"{a.date()} to {end.date()}" for w, a in W.items()}
    return out, labels
