"""Check the backtest's forecast-error assumption against the recorded real forecasts (data/history/, growth/history.py).

Backtest assumption (Research/Cisco_2026-10-23 Part 19, mc_2022_pit.py), in log growth = log EPS:
    forecast = w x actual + (1 - w) x trailing growth + error,   w = 0.6, error SD 30%, AR(0.9) month to month per stock
Measured here, once fiscal years recorded in data/history/forecasts_*.csv have been reported (data/history/actuals.csv):
  1. error = ln(actual FY EPS / forecast FY EPS) by months-to-fiscal-year-end: count, bias, SD, robust SD (1.4826 x MAD)
  2. anchoring w: (forecast - trailing) = w x (actual - trailing), log growth vs the prior fiscal year's actual
     (needs the trailing growth from signals_*.csv, so only rows recorded from 2026-10-26 on)
  3. persistence: correlation of one stock's error on the same fiscal year one month apart (backtest 0.9 / month)
  4. reference available now: last-quarter surprise ln(actual / estimate just before the report), from Yahoo's history
The first answers arrive as the current fiscal years end (2027); FY+1 / FY+2 errors at long horizons need 2-3 years.

    git pull                                   # brings the CI-recorded history home (the repo is the store)
    python growth/forecast_check.py            # summary
    python growth/forecast_check.py --csv out.csv   # also every matched forecast with its error
"""
import argparse
import glob
import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as C      # noqa: E402

HIST = os.path.join(C.DATA, "history")
ASSUMED_SD, ASSUMED_W, ASSUMED_AR = 0.30, 0.6, 0.9
BUCKETS = [(0, 6), (6, 12), (12, 18), (18, 24), (24, 36), (36, 60)]


def load():
    F = pd.concat([pd.read_csv(p) for p in sorted(glob.glob(os.path.join(HIST, "forecasts_*.csv")))], ignore_index=True)
    A = pd.read_csv(os.path.join(HIST, "actuals.csv"))
    S = [pd.read_csv(p) for p in sorted(glob.glob(os.path.join(HIST, "signals_*.csv")))]
    S = pd.concat(S, ignore_index=True) if S else pd.DataFrame(columns=["date", "sym", "trail"])
    for df, cols in ((F, ["date", "end"]), (A, ["report_date", "first_seen"]), (S, ["date"])):
        for c in cols:
            df[c] = pd.to_datetime(df[c], errors="coerce")
    A = A.sort_values("first_seen").drop_duplicates(["sym", "report_date"], keep="last")    # latest (restated) value
    return F, A, S


def fy_actuals(A):
    """Actual fiscal-year EPS = sum of the 4 quarters reported ~9 months before to ~4 months after the FY end
    (same window as signals.fy_from_q). Returns a function (sym, fy_end) -> EPS or None."""
    by = {s: g[["report_date", "actual"]].values for s, g in A.groupby("sym")}

    def fy(sym, end):
        q = by.get(sym)
        if q is None or pd.isna(end):
            return None
        x = [v for d, v in q if end - pd.Timedelta(days=270) < d <= end + pd.Timedelta(days=110)]
        return float(sum(x)) if len(x) == 4 else None
    return fy


def stats(e):
    e = np.asarray(e, float)
    e = e[np.isfinite(e)]
    if len(e) == 0:
        return dict(n=0, bias=np.nan, sd=np.nan, rsd=np.nan)
    return dict(n=len(e), bias=float(np.mean(e)), sd=float(np.std(e, ddof=1)) if len(e) > 1 else np.nan,
                rsd=float(1.4826 * np.median(np.abs(e - np.median(e)))))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv")
    a = ap.parse_args()
    F, A, S = load()
    fy = fy_actuals(A)
    print(f"history: {len(F):,} forecast rows ({F.date.min().date()} - {F.date.max().date()}, {F.sym.nunique()} stocks), "
          f"{len(A):,} reported quarters, {S.date.nunique()} signal days")

    # 4. quarterly surprise (reference, horizon ~ days)
    q = A[(A.actual > 0) & (A.estimate > 0)]
    s = stats(np.log(q.actual / q.estimate))
    print(f"\nquarterly surprise ln(actual/estimate), {s['n']:,} quarters since {q.report_date.min().date()}: "
          f"bias {s['bias']:+.1%}, SD {s['sd']:.1%}, robust SD {s['rsd']:.1%}  (one quarter, days before the report)")

    # 1. fiscal-year forecast errors
    Y = F[F.period.isin(["0y", "+1y", "FY"]) & F.end.notna() & (F.avg > 0)].copy()
    Y["actual"] = pd.to_numeric(pd.Series([fy(s, e) for s, e in zip(Y.sym, Y.end)], index=Y.index, dtype=object))
    Y = Y[Y.actual.notna() & (Y.actual > 0) & (Y.date < Y.end + pd.Timedelta(days=1))]
    Y["err"] = np.log(Y.actual / Y.avg)
    Y["months"] = (Y.end - Y.date).dt.days / 30.44
    print(f"\nfiscal-year forecasts with a reported outcome: {len(Y):,} rows, {Y.sym.nunique()} stocks, "
          f"{Y.groupby(['sym', 'end']).ngroups} stock-years")
    if len(Y) == 0:
        print("  none yet - the first recorded fiscal years end in 2026-27; run again after they are reported.")
    else:
        print(f"  {'months to FY end':<18}{'rows':>7}{'stock-yrs':>10}{'bias':>8}{'SD':>8}{'robust SD':>11}   (backtest: SD {ASSUMED_SD:.0%})")
        for lo, hi in BUCKETS:
            b = Y[(Y.months >= lo) & (Y.months < hi)]
            if len(b):
                # one value per stock-year (its mean error in the bucket) so frequently-fetched stocks do not dominate
                e = b.groupby(["sym", "end"]).err.mean()
                s = stats(e)
                print(f"  {f'{lo}-{hi}':<18}{len(b):>7}{s['n']:>10}{s['bias']:>+8.1%}{s['sd']:>8.1%}{s['rsd']:>11.1%}")

        # 2. anchoring w (forecast leans on trailing growth)
        base = {(s, e): fy(s, e - pd.DateOffset(years=1)) for s, e in Y.groupby(["sym", "end"]).groups}
        Y["base"] = pd.to_numeric(pd.Series([base.get((s, e)) for s, e in zip(Y.sym, Y.end)], index=Y.index, dtype=object))
        Y["base"] = Y.base.where(Y.base.notna(), Y.year_ago.where(Y.period == "0y"))
        T = S[["date", "sym", "trail"]].dropna().sort_values("date")
        Z = pd.merge_asof(Y.sort_values("date"), T, on="date", by="sym", direction="backward", tolerance=pd.Timedelta(days=10))
        Z = Z[(Z.base > 0) & Z.trail.notna() & (Z.trail > -0.9)]
        if len(Z) >= 30:
            g0 = np.log1p(Z.trail)
            x, y = np.log(Z.actual / Z.base) - g0, np.log(Z.avg / Z.base) - g0
            w = float((x * y).sum() / (x * x).sum())
            res = stats(y - w * x)
            print(f"\nanchoring: w = {w:.2f} (backtest {ASSUMED_W}), residual SD {res['sd']:.1%} / robust {res['rsd']:.1%} "
                  f"on {len(Z):,} rows with trailing growth")
        else:
            print(f"\nanchoring: {len(Z)} rows with trailing growth and an outcome - not enough yet (needs 30+)")

        # 3. persistence: same stock and fiscal year, errors one month apart
        M = Y.assign(m=Y.date.dt.to_period("M")).groupby(["sym", "end", "m"]).err.mean().reset_index()
        M["m1"] = M.m + 1
        P = M.merge(M, left_on=["sym", "end", "m1"], right_on=["sym", "end", "m"], suffixes=("", "_next"))
        if len(P) >= 30:
            print(f"persistence: month-to-month correlation of a stock's error {P.err.corr(P.err_next):.2f} "
                  f"(backtest {ASSUMED_AR}) on {len(P):,} pairs")
        else:
            print(f"persistence: {len(P)} month pairs - not enough yet")
    if a.csv:
        Y.to_csv(a.csv, index=False)
        print(f"\nrows written to {a.csv}")


if __name__ == "__main__":
    main()
