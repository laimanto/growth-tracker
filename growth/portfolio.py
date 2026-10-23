"""Model (paper) portfolio: a ledger of trades that follows the signals. No orders are ever sent anywhere.

  update  once per new market close: a SELL is executed / a BUY bought at that close when the stock is in its
          post-report review window (3-14 days after reporting) or at the first close of a new month (monthly
          sweep, which also resets equal weights). Buys are funded from cash, then by trimming the others.
  trips   one row per position (first buy -> position closed), with P&L
  nav     daily value (cash earns 0)
"""
import json
import os

import pandas as pd

import config as C


def load():
    try:
        L = json.load(open(C.LEDGER, encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None
    for t in L["trades"]:                                  # v1.0 ledgers: trades had no price date
        t.setdefault("price_date", L["price_date"])
    L.setdefault("last_close", L["price_date"])
    return L


def save(L):
    tmp = C.LEDGER + ".tmp"
    json.dump(L, open(tmp, "w", encoding="utf-8"), indent=1)
    os.replace(tmp, C.LEDGER)


def positions(L, upto=None):
    pos, cash = {}, L["capital"]
    for t in L["trades"]:
        if upto and t["date"] > upto:
            break
        sh = t["shares"] if t["side"] == "BUY" else -t["shares"]
        pos[t["sym"]] = pos.get(t["sym"], 0.0) + sh
        cash -= sh * t["price"]
    return {k: v for k, v in pos.items() if abs(v) > 1e-9}, cash


def _trade(L, d, sym, side, shares, price, reason, px_date):
    L["trades"].append({"date": d, "price_date": px_date, "sym": sym, "side": side, "shares": round(shares, 6),
                        "price": round(price, 4), "value": round(shares * price, 2), "reason": reason})


def init(S, d):
    buys = S[S.state == "BUY"]
    pd_ = S.attrs["price_date"]
    L = {"capital": C.CAPITAL, "start": d, "price_date": pd_, "trades": [], "runs": [d], "last_close": pd_}
    w = C.CAPITAL / len(buys)
    for x in buys.itertuples():
        _trade(L, d, x.sym, "BUY", w / x.price, x.price, f"initial portfolio: {x.why}", pd_)
    save(L)
    return L


def update(L, S, d):
    px_date = S.attrs["price_date"]
    if px_date <= L.get("last_close", L["price_date"]):
        return []                                            # no new close since the last decision round
    month_sweep = px_date[:7] != L.get("last_close", L["price_date"])[:7]
    sig = S.set_index("sym")
    px = sig.price
    pos, cash = positions(L)
    done = []
    tag = " (monthly sweep)" if month_sweep else " (post-report review)"
    for sym in list(pos):
        x = sig.loc[sym] if sym in sig.index else None
        if x is not None and x.state == "SELL" and (bool(x.get("in_review")) or month_sweep):
            _trade(L, d, sym, "SELL", pos[sym], x.price, x.why + tag, px_date)
            cash += pos.pop(sym) * x.price
            done.append(("SELL", sym))
    new = [s for s, x in sig.iterrows() if x.state == "BUY" and (bool(x.get("in_review")) or month_sweep) and s not in pos]
    if new or month_sweep:
        names = list(pos) + new
        nav = cash + sum(pos[s] * px[s] for s in pos)
        target = nav / len(names) if names else 0
        for s in list(pos):
            diff = target / px[s] - pos[s]
            if month_sweep and abs(diff * px[s]) > 1:
                _trade(L, d, s, "BUY" if diff > 0 else "SELL", abs(diff), px[s], "monthly equal-weight rebalance", px_date)
            elif not month_sweep and diff < 0 and new:
                _trade(L, d, s, "SELL", -diff, px[s], "trim to make room for new buys", px_date)
        for s in new:
            _trade(L, d, s, "BUY", target / px[s], px[s], sig.loc[s].why + tag, px_date)
            done.append(("BUY", s))
    L["runs"].append(d)
    L["last_close"] = px_date
    save(L)
    return done


def trips(L, last_px):
    """One row per position: first buy -> closed (or open), average cost, average exit, P&L."""
    out, cur = [], {}
    for t in L["trades"]:
        s = t["sym"]
        c = cur.get(s)
        if t["side"] == "BUY":
            if c is None:
                c = cur[s] = {"sym": s, "entry_date": t["price_date"], "entry_reason": t["reason"], "sh": 0.0, "cost": 0.0,
                              "bought": 0.0, "bought_sh": 0.0, "sold_sh": 0.0, "proceeds": 0.0, "realized": 0.0}
            c["sh"] += t["shares"]; c["cost"] += t["shares"] * t["price"]; c["bought"] += t["shares"] * t["price"]; c["bought_sh"] += t["shares"]
        elif c is not None:
            avg = c["cost"] / c["sh"]
            c["realized"] += t["shares"] * (t["price"] - avg)
            c["sh"] -= t["shares"]; c["cost"] -= t["shares"] * avg
            c["sold_sh"] += t["shares"]; c["proceeds"] += t["shares"] * t["price"]
            if c["sh"] < 1e-6:
                c.update(exit_date=t["price_date"], exit_reason=t["reason"], status="closed", exit_px=c["proceeds"] / c["sold_sh"])
                out.append(c)
                del cur[s]
    for s, c in cur.items():
        c["status"] = "open"
        out.append(c)
    for c in out:
        c["entry_px"] = c["bought"] / c["bought_sh"]
        if c["status"] == "open":
            p = last_px.get(c["sym"])
            c["last_px"] = p
            c["unrealized"] = c["sh"] * (p - c["cost"] / c["sh"]) if p else 0.0
            c["pnl"] = c["realized"] + c["unrealized"]
        else:
            c["pnl"] = c["realized"]
        c["pnl_pct"] = c["pnl"] / c["bought"] if c["bought"] else None
    return out


def nav(L, closes):
    start = pd.Timestamp(L["price_date"])
    days = closes.index[closes.index >= start]
    vals = []
    for t in days:
        ds = str(t.date())
        tr = [x for x in L["trades"] if x["price_date"] <= ds]
        pos, cash = positions({"capital": L["capital"], "trades": tr})
        vals.append(cash + sum(sh * closes[s].loc[:t].dropna().iloc[-1] for s, sh in pos.items() if s in closes))
    return pd.Series(vals, index=days) / L["capital"]
