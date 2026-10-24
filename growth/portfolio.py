"""Model (paper) portfolio: a ledger of trades that follows the signals. No orders are ever sent anywhere.

  update  once per new market close: a SELL is executed / a BUY bought at that close when the stock is in its
          post-report review window (3-14 days after reporting) or at the first close of a new month (monthly sweep).
          C.MAX_HOLD fixed slots; each stock keeps its own money (no trims, no rebalancing). A sale's whole value waits
          in cash (one free slot) for the next qualifier, which gets it all. When full, a new BUY replaces the holding
          with the lowest 12m upside if its own upside is >= C.SWAP_BUFFER higher. First close of each quarter: up to
          C.PRUNE_N holdings with upside below C.PRUNE_UPSIDE are sold (their money waits for the next qualifier).
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


def _upside(up, s):
    v = (up or {}).get(s)
    return v if v is not None and v == v else -9.0


def init(S, d, up=None):
    """C.MAX_HOLD equal slots: the BUY stocks (most upside first); if fewer qualify, the closest near-misses that still
    pass the exit bar and have upside >= C.PRUNE_UPSIDE fill the rest (start-up only)."""
    ok = S[~S.get("extra", False) & S.price.notna()]
    buys = ok[ok.state == "BUY"].assign(_u=lambda x: [_upside(up, s) for s in x.sym]).sort_values("_u", ascending=False).head(C.MAX_HOLD)
    picks = list(buys.itertuples())
    if len(picks) < C.MAX_HOLD:
        near = ok[ok.state.isin(["WATCH", "OUT"]) & ok.room.notna()].copy()
        wk = near[["gCur", "g1"]].min(axis=1).combine(near.g2.fillna(near.g1), min)
        near = near[(wk >= near.need_out) & (near.sym.map(lambda s: _upside(up, s)) >= C.PRUNE_UPSIDE)]   # not prunable at once
        near = near.sort_values("room", ascending=False).head(C.MAX_HOLD - len(picks))
        picks += list(near.itertuples())
    pd_ = S.attrs["price_date"]
    L = {"capital": C.CAPITAL, "start": d, "price_date": pd_, "trades": [], "runs": [d], "last_close": pd_, "free": []}
    w = C.CAPITAL / C.MAX_HOLD
    for x in picks:
        why = x.why if x.state == "BUY" else f"start-up fill (closest near-miss): {x.why}"
        _trade(L, d, x.sym, "BUY", w / x.price, x.price, f"initial portfolio: {why}", pd_)
    L["free"] = [w] * (C.MAX_HOLD - len(picks))
    save(L)
    return L


def _free(L, pos, cash):
    """cash amounts of the free slots (one per missing holding); rebuilt if the ledger has none / is inconsistent."""
    fr = [f for f in L.get("free", []) if f > 0.5]
    n_free = max(0, C.MAX_HOLD - len(pos))
    if len(fr) != n_free or abs(sum(fr) - cash) > 1:
        fr = [cash / n_free] * n_free if n_free else []
    return fr


def update(L, S, d, up=None):
    px_date = S.attrs["price_date"]
    if px_date <= L.get("last_close", L["price_date"]):
        return []                                            # no new close since the last decision round
    month_sweep = px_date[:7] != L.get("last_close", L["price_date"])[:7]
    prune_day = month_sweep and int(px_date[5:7]) in C.PRUNE_MONTHS
    sig = S.set_index("sym")
    px = sig.price
    pos, cash = positions(L)
    free = _free(L, pos, cash)
    done = []
    tag = " (monthly sweep)" if month_sweep else " (post-report review)"
    for sym in sorted(pos):                                  # rule sells: the whole slot goes to cash
        x = sig.loc[sym] if sym in sig.index else None
        if x is not None and x.state == "SELL" and (bool(x.get("in_review")) or month_sweep):
            val = pos[sym] * x.price
            _trade(L, d, sym, "SELL", pos.pop(sym), x.price, x.why + tag, px_date)
            free.append(val); done.append(("SELL", sym))
    if prune_day:                                            # quarterly prune: least upside left
        weak = sorted((k for k in pos if _upside(up, k) < C.PRUNE_UPSIDE), key=lambda k: _upside(up, k))[:C.PRUNE_N]
        for k in weak:
            val = pos[k] * px[k]
            _trade(L, d, k, "SELL", pos.pop(k), px[k], f"quarterly prune: 12m upside {_upside(up, k):+.0%} < {C.PRUNE_UPSIDE:.0%}", px_date)
            free.append(val); done.append(("SELL", k))
    new = [s for s, x in sig.iterrows() if x.state == "BUY" and not bool(x.get("extra")) and (bool(x.get("in_review")) or month_sweep) and s not in pos]
    new.sort(key=lambda s: -_upside(up, s))                  # most upside first
    for s in new:
        if free:                                             # empty slot: it gets the whole slot cash
            free.sort(reverse=True)
            amt = free.pop(0)
            _trade(L, d, s, "BUY", amt / px[s], px[s], sig.loc[s].why + tag, px_date)
            pos[s] = amt / px[s]; done.append(("BUY", s))
        elif pos:                                            # full: replace the lowest-upside holding if clearly weaker
            w = min(pos, key=lambda k: _upside(up, k))
            if _upside(up, s) >= _upside(up, w) + C.SWAP_BUFFER:
                val = pos[w] * px[w]
                _trade(L, d, w, "SELL", pos.pop(w), px[w], f"replaced by {s}: 12m upside {_upside(up, w):+.0%} vs {_upside(up, s):+.0%}" + tag, px_date)
                _trade(L, d, s, "BUY", val / px[s], px[s], sig.loc[s].why + f"; replaces {w} (upside {_upside(up, s):+.0%} vs {_upside(up, w):+.0%})" + tag, px_date)
                pos[s] = val / px[s]; done += [("SELL", w), ("BUY", s)]
    L["free"] = [round(f, 2) for f in free]
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
