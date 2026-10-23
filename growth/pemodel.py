"""12-month price target = expected 12-month PEAK level (buy reference, not a sell rule).

    target = last close x (1 + forecast next-12m EPS growth) x exp(model P/E change vs the market)

The P/E model is the Sortino project's live pe28 model (gradient-boosted trees on S&P 500 / Nasdaq-100 stock-months
2004-2023, Research/PEAdjust_2026-10-20 Parts 8-13), copied to data/models/. Inputs, each relative to the
universe mean at the same month end (1/99% clipped, as in training):
    P/E change over the last 1, 1-3, 3-6, 6-12, 12-24 months; P/E vs the universe median; P/E vs own 36-month median;
    trailing EPS growth last year and the year before; forecast EPS growth next 12 months and 12-24 months.
Stocks without 24 months of usable P/E history use the growth-only model (pe28g: the two forecast growth inputs).
Backtest (Research/Cisco_2026-10-23 Part 22, 454 entries 2008-2024): vs the 12-month peak median error 16%, bias -1%;
reached within 12 months 47% of the time (median 156 days).
P/E history: month-end closes (data/closes_m.csv) / trailing EPS from the reported quarters announced by then.
"""
import os
import pickle

import numpy as np
import pandas as pd

import config as C

MODELS = os.path.join(C.DATA, "models")
RAW = ["w0_1", "w1_3", "w3_6", "w6_12", "w12_24", "own", "g0", "gm1"]


def _ttm_at(q, t):
    rep = [x for x in q if x[0] <= t + pd.Timedelta(hours=23)]
    if len(rep) < 4:
        return None
    l4 = rep[-4:]
    if (l4[-1][0] - l4[0][0]).days > 330 or (t - l4[-1][0]).days > 140:
        return None
    return sum(v for _, v in l4)


def _clip_rel(s):
    s = s.astype(float)
    lo, hi = s.quantile(.01), s.quantile(.99)
    c = s.clip(lo, hi)
    return c - c.mean()


def targets(S, recs, closes_m, curves, today):
    """S: signals frame (sym, price, pe ...); curves: {sym: monthly log consensus curve}. Returns frame sym -> target info."""
    m28 = pickle.load(open(os.path.join(MODELS, "pe28_model.pkl"), "rb"))
    m28g = pickle.load(open(os.path.join(MODELS, "pe28g_model.pkl"), "rb"))
    cur = pd.Timestamp(today) + pd.offsets.MonthEnd(0)
    rows = []
    for x in S.itertuples():
        s = x.sym
        c = curves.get(s)
        if c is None or x.price is None or not np.isfinite(x.price or np.nan):
            continue
        if cur not in c.index or (cur + pd.offsets.MonthEnd(24)) not in c.index:
            continue
        r = {"sym": s, "g1F": c[cur + pd.offsets.MonthEnd(12)] - c[cur], "g2F": c[cur + pd.offsets.MonthEnd(24)] - c[cur + pd.offsets.MonthEnd(12)]}
        y = (recs.get(s) or {}).get("yahoo") or {}
        q = sorted((pd.Timestamp(d), v) for d, v in y.get("qh") or [])
        fx = y.get("fx") or 1.0
        ttm = _ttm_at(q, pd.Timestamp(today))
        if ttm and ttm > 0 and s in closes_m:
            lpe = np.log(x.price * fx / ttm)
            pm = closes_m[s]

            def lpe_at(k):
                t = cur - pd.offsets.MonthEnd(k)
                p, tt = pm.get(t), _ttm_at(q, t)
                return float(np.log(p * fx / tt)) if p and np.isfinite(p) and tt and tt > 0 else None
            k = {i: lpe_at(i) for i in (1, 3, 6, 12, 24)}
            hist = [v for v in (lpe_at(i) for i in range(1, 36)) if v is not None] + [lpe]
            t12, t24 = _ttm_at(q, cur - pd.offsets.MonthEnd(12)), _ttm_at(q, cur - pd.offsets.MonthEnd(24))
            if all(v is not None for v in k.values()) and len(hist) >= 24 and t12 and t24 and t12 > 0 and t24 > 0:
                r.update(lpe=lpe, w0_1=lpe - k[1], w1_3=k[1] - k[3], w3_6=k[3] - k[6], w6_12=k[6] - k[12], w12_24=k[12] - k[24],
                         own=lpe - float(np.median(hist)), g0=np.log(ttm / t12), gm1=np.log(t12 / t24))
            elif ttm:
                r["lpe"] = lpe
        rows.append(r)
    T = pd.DataFrame(rows).set_index("sym")
    T["g1F_r"], T["g2F_r"] = _clip_rel(T.g1F), _clip_rel(T.g2F)
    full = T[RAW].notna().all(axis=1) if set(RAW) <= set(T.columns) else pd.Series(False, index=T.index)
    if full.sum() >= 50:
        F = T[full].copy()
        for c in RAW:
            F[c + "_r"] = _clip_rel(F[c])
        v = F.lpe - F.lpe.median()                               # P/E vs the universe median (as in training)
        F["val"] = v.clip(v.quantile(.01), v.quantile(.99))
        T.loc[full, "dPE"] = m28["model"].predict(F[m28["features"]])
        T.loc[full, "kind"] = "P/E model"
    rest = T.dPE.isna() if "dPE" in T else pd.Series(True, index=T.index)
    T.loc[rest, "dPE"] = m28g["model"].predict(T.loc[rest, m28g["features"]])
    T.loc[rest, "kind"] = "growth-only model"
    px = S.set_index("sym").price.reindex(T.index)
    T["target"] = px * np.exp(T.g1F + T.dPE)
    T["upside"] = T.target / px - 1
    T["pe_chg"] = np.exp(T.dPE) - 1
    return T[["target", "upside", "pe_chg", "kind"]]
