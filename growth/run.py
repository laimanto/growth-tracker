"""Growth Tracker daily run (local or GitHub Actions):
    universe (monthly) -> EPS deltas -> price deltas -> signals -> model portfolio -> docs/index.html

    python growth/run.py              normal run
    python growth/run.py --offline    no downloads, rebuild the page from data/
"""
import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as C      # noqa: E402
import pemodel          # noqa: E402
import perf             # noqa: E402
import portfolio        # noqa: E402
import signals          # noqa: E402
import update           # noqa: E402


def clean(v):
    if isinstance(v, (np.floating, float)):
        return None if not np.isfinite(v) else round(float(v), 5)
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.bool_):
        return bool(v)
    if isinstance(v, (pd.Timestamp, date)):
        return str(v)[:10]
    return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    now = datetime.now(timezone.utc)
    today = date.today().isoformat()
    log = []
    say = lambda m: (print(m, flush=True), log.append(m))
    U = update.universe(log=say) if not a.offline else update.jload(C.UNIVERSE)
    syms = sorted(U["stocks"])
    if a.offline:
        closes = pd.read_csv(C.CLOSES, index_col=0, parse_dates=True)
        bench = pd.read_csv(C.BENCHF, index_col=0, parse_dates=True)
        n_eps = 0
    else:
        n_eps = update.refresh_eps(syms, log=say)
        closes, bench = update.refresh_prices(syms, "2026-09-01", log=say)
    closes_m = update.refresh_monthly(syms, closes, log=say)
    recs = {s: update.load_stock(s) for s in syms}

    L = portfolio.load()
    held = set(portfolio.positions(L)[0]) if L else set()
    S = signals.compute(U, recs, closes, held=held)
    if L is None:
        L = portfolio.init(S, today)
        done = [("BUY", s) for s in S[S.state == "BUY"].sym]
    else:
        done = portfolio.update(L, S, today)
    say(f"model trades: {done or 'none'}")
    held = set(portfolio.positions(L)[0])
    S = signals.compute(U, recs, closes, held=held)

    # signal history: date each stock entered its current state
    H = update.jload(C.SIGHIST, {}) or {}
    for r in S.itertuples():
        h = H.get(r.sym)
        if not h or h["state"] != r.state:
            H[r.sym] = {"state": r.state, "since": S.attrs["price_date"], "px": r.price}
    update.jsave(C.SIGHIST, H, indent=0)

    TG = pemodel.targets(S, recs, closes_m, S.attrs["curves"], today)
    say(f"12m targets: {len(TG)} stocks ({(TG.kind == 'P/E model').sum()} P/E model, {(TG.kind != 'P/E model').sum()} growth-only)")
    last_px = S.set_index("sym").price.to_dict()
    T = portfolio.trips(L, last_px)
    sold_recent = {t["sym"]: t for t in T if t["status"] == "closed" and
                   (pd.Timestamp(S.attrs["price_date"]) - pd.Timestamp(t["exit_date"])).days <= C.HIGHLIGHT_DAYS}
    open_trip = {t["sym"]: t for t in T if t["status"] == "open"}
    rows = []
    for r in S.to_dict("records"):
        st = r["state"]
        if r["sym"] in sold_recent and not r["held"]:
            st = "SOLD"
        h = H.get(r["sym"], {})
        x = {k: clean(r.get(k)) for k in ("sym", "name", "sector", "ndx", "sp500", "held", "why", "price", "pe", "sector_pe", "relpe", "trail", "fwd",
                                          "gLast", "lastFlag", "gCur", "g1", "g2", "need_in", "need_out", "room", "n_analysts", "rev7", "rev30", "rev90",
                                          "last_report", "next_report", "days_since_report", "in_review")}
        x["fy"] = r.get("fy") if isinstance(r.get("fy"), list) else None
        x["fy_points"], x["qh"] = r.get("fy_points"), r.get("qh")
        x["state"] = st
        if r["sym"] in TG.index:
            t_ = TG.loc[r["sym"]]
            x.update(target=clean(t_.target), upside=clean(t_.upside), pe_chg=clean(t_.pe_chg), tkind=t_.kind)
        x["since"] = h.get("since")
        tr = open_trip.get(r["sym"]) or sold_recent.get(r["sym"])
        if tr:
            x.update(entry_date=tr["entry_date"], entry_px=clean(tr["entry_px"]), pnl_pct=clean(tr["pnl_pct"]), pnl=clean(tr["pnl"]),
                     exit_date=tr.get("exit_date"), exit_px=clean(tr.get("exit_px")))
        elif st == "BUY":
            x.update(entry_date=h.get("since"), entry_px=clean(h.get("px")))
        if st == "SELL":
            x["exit_date"] = h.get("since")
        rows.append(x)

    # performance
    pos, cash = portfolio.positions(L)
    nav = portfolio.nav(L, closes)
    navs = {"dates": [str(d.date()) for d in nav.index], "Model": [clean(v) for v in nav.values]}
    for b in C.BENCH:
        c = bench.get(f"{b}_TR")
        if c is not None:
            c = c.reindex(nav.index).ffill()
            if c.notna().any():
                navs[C.LABEL.get(b, b)] = [clean(v) for v in (c / c.dropna().iloc[0]).values]
    bt = json.load(open(C.BACKTEST))
    dA = bt.get("dailyA") or {}
    series = {"Strategy (backtest)": pd.Series(dA.get("ret", []), index=pd.to_datetime(dA.get("dates", [])), dtype=float)}
    for b in C.BENCH:
        c = bench.get(f"{b}_TR")
        if c is not None:
            series[C.LABEL.get(b, b)] = c.dropna().pct_change().dropna()
    live = {"Model (live)": nav.pct_change().dropna()} if len(nav) > 63 else {}
    risk, wlab = perf.table(series, live)
    weights = {}
    for s, sh in pos.items():
        sec = U["stocks"].get(s, {}).get("sector", "Unknown")
        weights[sec] = weights.get(sec, 0) + sh * last_px[s]
    navv = cash + sum(weights.values())
    D = {"updated_utc": now.strftime("%Y-%m-%d %H:%M"), "updated_hk": (now + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M"),
         "price_date": S.attrs["price_date"], "start": L["start"], "start_price_date": L["price_date"], "capital": L["capital"],
         "cash": cash, "nav": navv, "n_pos": len(pos), "sector_pe": S.attrs["sector_pe"],
         "rules": {"entry": C.ENTRY, "exit": C.EXIT, "watch": C.WATCH, "review": C.REVIEW, "highlight": C.HIGHLIGHT_DAYS},
         "signals": rows, "trips": [{k: clean(v) for k, v in t.items()} for t in T], "trades": L["trades"], "today": done,
         "navs": navs, "risk": risk, "risk_windows": wlab, "sector_w": {k: v / navv for k, v in sorted(weights.items(), key=lambda z: -z[1])},
         "bench_names": C.BENCH_NAME, "backtest": bt, "fetched": n_eps}
    html = open(C.TEMPLATE, encoding="utf-8").read().replace("/*DATA*/null", json.dumps(D, separators=(",", ":"), default=clean))
    os.makedirs(os.path.dirname(C.OUT_HTML), exist_ok=True)
    open(C.OUT_HTML, "w", encoding="utf-8").write(html)
    st = update.jload(C.STATE, {"runs": []}) or {"runs": []}
    st["runs"] = (st["runs"] + [{"utc": now.strftime("%Y-%m-%d %H:%M"), "eps_fetched": n_eps, "trades": len(done), "secs": round(time.time() - t0)}])[-60:]
    update.jsave(C.STATE, st, indent=1)
    say(f"signals {S.state.value_counts().to_dict()}; {len(pos)} holdings; page {C.OUT_HTML} ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
