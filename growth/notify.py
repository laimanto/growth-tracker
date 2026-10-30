"""Change notice when the model portfolio changes: new BUY / SELL trades (a SELL shows the whole round trip with its
P&L) and stocks entering or leaving "BUY, pending" (BUY signal, not bought yet: data not ready, outside the review
window, or no free slot). Nothing changed -> no notice.

Delivered as a GitHub issue assigned to the repo owner (same set-up as the Bull Bear Monitor, user 2026-10-26): GitHub
mails it to the account's notification address, so there is no SMTP password to store. This module only writes
notice.md and the step outputs changed / title; .github/workflows/update.yml creates the issue BEFORE the commit step,
so if that fails the advanced data/notify.json is never committed and the next run re-detects the same changes.
Runs only on GitHub Actions (local runs never touch data/notify.json). The first run sends a "notifications on" notice.
"""
import json
import os

import pandas as pd

import config as C

STATE = os.path.join(C.DATA, "notify.json")
NOTICE = os.path.join(C.ROOT, "notice.md")
PAGE = "https://laimanto.github.io/growth-tracker/"


def _load():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None


def _usd(v):
    return "" if v is None else f"${v:,.0f}" if abs(v) >= 1000 else f"${v:,.2f}"


def _px(v):
    return "" if v is None or v != v else f"{v:,.2f}"


def _pct(v):
    return "" if v is None or v != v else f"{v:+.1%}"


def _md(s):
    return str(s).replace("|", "\\|").replace("\n", " ")


def _weekday(d):
    while d.weekday() >= 5:
        d += pd.Timedelta(days=1)
    return d


def _dm(d):
    return f"{d.day} {d:%b}"


def pending(r, price_date, full=False):
    """Every reason a BUY (not held) / SELL (held) signal is not traded yet -> list of plain-language reasons ([] = trades
    at the next close). Mirrors portfolio.update: trade only when the data is ready AND (post-report review OR month start);
    a BUY with an empty slot free (full=False) is bought at the next close whatever the date (v1.11 refill)."""
    act = "sold" if r.get("state") == "SELL" else "bought"
    wt = r.get("wait")
    out = [x for x in wt.split("; ") if x] if isinstance(wt, str) else []
    rv = r.get("in_review")
    if not (rv is True or rv == 1) and not out and not (act == "bought" and not full):
        pd_ = pd.Timestamp(price_date)
        month = _weekday((pd_ + pd.offsets.MonthBegin(1)).normalize())
        nxt = r.get("next_report")
        if nxt:
            d3 = _weekday(pd.Timestamp(nxt) + pd.Timedelta(days=C.REVIEW[0]))
            rep = f"the next quarterly report ({nxt}): {act} from day {C.REVIEW[0]} after it ({_dm(d3)}) once analysts have updated"
        else:
            rep = "the next quarterly report (date not announced yet)"
        out.append(f"waiting for {rep} – or at the first close of {month:%B} ({_dm(month)}), whichever comes first")
    if full and act == "bought":
        out.append(f"portfolio full ({C.MAX_HOLD} slots): bought only if its 12m upside is ≥ {C.SWAP_BUFFER:.0%} above the weakest holding’s")
    return out


def waiting(S, L):
    """BUY signals not held (model universe only) -> {sym: why it is not bought yet}."""
    from portfolio import positions
    pos, _ = positions(L)
    full = len(pos) >= C.MAX_HOLD
    out = {}
    for r in S.to_dict("records"):
        if r["state"] != "BUY" or r["held"] or bool(r.get("extra", False)):
            continue
        out[r["sym"]] = "; ".join(pending(r, S.attrs["price_date"], full)) or "bought at the next close"
    return out


def _trip_of(T, t):
    """The round trip a SELL trade closed."""
    return next((x for x in T if x["sym"] == t["sym"] and x.get("exit_date") == t["price_date"]), None)


def _table(rows):
    return "| | |\n|---|---|\n" + "".join(f"| {_md(k)} | {_md(v)} |\n" for k, v in rows)


def build(L, S, T, TG, st):
    """-> (markdown sections, title parts, waiting list)"""
    trades = L["trades"][st["n_trades"]:]
    W = waiting(S, L)
    w_old = st.get("waiting") or {}
    sig = S.set_index("sym")
    nm = lambda s: sig.name.get(s, s)
    out, title = [], []
    for t in [t for t in trades if t["side"] == "SELL"]:
        x = _trip_of(T, t) or {}
        days = (pd.Timestamp(t["price_date"]) - pd.Timestamp(x["entry_date"])).days if x.get("entry_date") else None
        pnl = x.get("pnl")
        rows = [("Bought", f"{x.get('entry_date', '')} close at {_px(x.get('entry_px'))} ({x.get('bought_sh', 0):,.2f} shares, {_usd(x.get('bought'))})"),
                ("Sold", f"{t['price_date']} close at {_px(t['price'])} ({t['shares']:,.2f} shares, {_usd(t['value'])})"),
                ("**Profit / loss**", f"**{_usd(pnl)} ({_pct(x.get('pnl_pct'))})**" + (f" over {days} days" if days is not None else "")),
                ("Why bought", x.get("entry_reason", "")), ("Why sold", t["reason"])]
        icon = "🟢" if (pnl or 0) >= 0 else "🔴"
        out.append(f"### {icon} SELL {t['sym']} · {nm(t['sym'])} · {_pct(x.get('pnl_pct'))}\n\n" + _table(rows))
        title.append(f"SELL {t['sym']} {_pct(x.get('pnl_pct'))}")
    for t in [t for t in trades if t["side"] == "BUY"]:
        tg = TG.loc[t["sym"]] if t["sym"] in TG.index else None
        rows = [("Bought", f"{t['price_date']} close at {_px(t['price'])} ({t['shares']:,.2f} shares, {_usd(t['value'])})"),
                ("12m target", f"{_px(tg.target)} ({_pct(tg.upside)})" if tg is not None else "n/a"), ("Why", t["reason"])]
        out.append(f"### 🔵 BUY {t['sym']} · {nm(t['sym'])}\n\n" + _table(rows))
        title.append(f"BUY {t['sym']}")
    w_new = {s: w for s, w in W.items() if s not in w_old}
    if w_new:
        out.append("### 🟡 New BUY, pending\n\n" + "".join(f"- **{s}** {nm(s)} ({_px(sig.price.get(s))}): {w}\n" for s, w in w_new.items()))
        title += [f"{s} BUY pending" for s in w_new]
    bought = {t["sym"] for t in trades if t["side"] == "BUY"}
    gone = [s for s in w_old if s not in W and s not in bought]
    if gone:
        out.append("### ⚪ No longer pending (BUY signal gone, not bought)\n\n" +
                   "".join(f"- **{s}** {nm(s)}: now {sig.state.get(s, 'not tracked')}\n" for s in gone))
        title += [f"{s} no longer BUY" for s in gone]
    return out, title, W


def summary(L, S, T):
    from portfolio import positions
    pos, cash = positions(L)
    op = sorted((x for x in T if x["status"] == "open"), key=lambda x: x["sym"])
    nav = cash + sum(x["sh"] * (x.get("last_px") or 0) for x in op)
    free = C.MAX_HOLD - len(pos)
    tab = "| Stock | Bought | Entry | Last | P&L | % |\n|---|---|--:|--:|--:|--:|\n" + "".join(
        f"| **{x['sym']}** | {x['entry_date']} | {_px(x['entry_px'])} | {_px(x.get('last_px'))} | {_usd(x['pnl'])} | {_pct(x['pnl_pct'])} |\n" for x in op)
    return (f"### Portfolio now\n\n{tab}\nValue **{_usd(nav)}** ({_pct(nav / L['capital'] - 1)} since {L['price_date']}); "
            f"cash {_usd(cash)} in {free} empty slot{'s' * (free != 1)}. Prices of the {S.attrs['price_date']} close.\n")


def _output(**kv):
    p = os.environ.get("GITHUB_OUTPUT")
    if p:
        with open(p, "a", encoding="utf-8") as f:
            for k, v in kv.items():
                f.write(f"{k}={v}\n")


def run(L, S, T, TG, log=print):
    if not os.environ.get("GITHUB_ACTIONS"):
        log("notice: local run - no change notice (sent by the GitHub workflow only)")
        return
    st = _load()
    first = st is None
    st = st or {"n_trades": len(L["trades"]), "waiting": {}}
    if st["n_trades"] > len(L["trades"]):                   # ledger restarted
        st = {"n_trades": 0, "waiting": {}}
    secs, title, W = build(L, S, T, TG, st)
    json.dump({"n_trades": len(L["trades"]), "waiting": W}, open(STATE, "w", encoding="utf-8"), indent=1)
    if not secs and not first:
        log("notice: no position change")
        return
    if first:
        secs.insert(0, "Notifications are on: a notice is sent when the model portfolio buys or sells, and when a stock gets a "
                       "BUY signal it is still waiting to buy.\n")
        title = title or ["notifications on"]
    body = "\n".join(secs) + "\n" + summary(L, S, T) + f"\n[Open the Growth Tracker]({PAGE}) · paper portfolio, a strategy simulation (no orders are sent).\n"
    open(NOTICE, "w", encoding="utf-8").write(body)
    t = ", ".join(title)
    t = f"Growth Tracker {S.attrs['price_date']}: " + (t if len(t) <= 200 else t[:197] + "...")
    _output(changed="true", title=t)
    log(f"notice: {t}")
