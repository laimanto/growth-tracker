"""E-mail notice when the model portfolio changes: new BUY / SELL trades (a SELL shows the whole round trip with its
P&L) and stocks entering or leaving "BUY, waiting" (BUY signal, not bought yet: data not ready, outside the review
window, or no free slot). Nothing changed -> no e-mail.

Sent only when SMTP_USER, SMTP_PASS and MAIL_TO are set (GitHub secrets in update.yml; Gmail: an app password).
data/notify.json remembers the last notified trade and the waiting list; it only advances after a successful send, so
a failed send is retried (with everything since) on the next run. The first run sends a "notifications on" summary.
"""
import html
import json
import os
import smtplib
from email.message import EmailMessage

import pandas as pd

import config as C

STATE = os.path.join(C.DATA, "notify.json")
PAGE = "https://laimanto.github.io/growth-tracker/"


def _load():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None


def _usd(v):
    return "" if v is None else f"${v:,.0f}" if abs(v) >= 1000 else f"${v:,.2f}"


def _px(v):
    return "" if v is None else f"{v:,.2f}"


def _pct(v):
    return "" if v is None or v != v else f"{v:+.1%}"


def waiting(S, L):
    """BUY signals not held (model universe only) -> {sym: reason it is not bought yet}."""
    from portfolio import positions
    pos, _ = positions(L)
    full = len(pos) >= C.MAX_HOLD
    out = {}
    for r in S.itertuples():
        if r.state != "BUY" or r.held or bool(getattr(r, "extra", False)):
            continue
        wt, rv = getattr(r, "wait", None), getattr(r, "in_review", None)
        if isinstance(wt, str) and wt:
            why = f"data not ready: {wt}"
        elif full:
            why = f"portfolio full: buys only if its 12m upside is >= {C.SWAP_BUFFER:.0%} above the weakest holding's"
        elif rv is not True and rv != 1:
            why = "buys at the next post-report review or the first close of next month"
        else:
            why = "buys at the next close"
        out[r.sym] = why
    return out


def _trip_of(T, t):
    """The round trip a SELL trade closed (or reduced)."""
    for x in T:
        if x["sym"] == t["sym"] and x.get("exit_date") == t["price_date"]:
            return x
    return None


def build(L, S, T, TG, st):
    trades = L["trades"][st["n_trades"]:]
    W = waiting(S, L)
    w_old = st.get("waiting") or {}
    w_new = {s: w for s, w in W.items() if s not in w_old}
    w_gone = [s for s in w_old if s not in W]
    bought = {t["sym"] for t in trades if t["side"] == "BUY"}
    sig = S.set_index("sym")
    nm = lambda s: sig.name.get(s, s) if "name" in sig else s
    items, text = [], []
    h = lambda s: html.escape(str(s))

    for t in [t for t in trades if t["side"] == "SELL"]:
        x = _trip_of(T, t) or {}
        days = (pd.Timestamp(t["price_date"]) - pd.Timestamp(x["entry_date"])).days if x.get("entry_date") else None
        rows = [("Bought", f"{x.get('entry_date', '')} at {_px(x.get('entry_px'))} ({x.get('bought_sh', 0):,.2f} shares, {_usd(x.get('bought'))})"),
                ("Sold", f"{t['price_date']} at {_px(t['price'])} ({t['shares']:,.2f} shares, {_usd(t['value'])})"),
                ("Profit / loss", f"{_usd(x.get('pnl'))} ({_pct(x.get('pnl_pct'))})" + (f" over {days} days" if days is not None else "")),
                ("Why bought", x.get("entry_reason", "")), ("Why sold", t["reason"])]
        col = "#1a7f37" if (x.get("pnl") or 0) >= 0 else "#cf222e"
        items.append(f"<h3 style='color:{col}'>SELL {h(t['sym'])} &middot; {h(nm(t['sym']))} &middot; {h(_pct(x.get('pnl_pct')))}</h3><table>" +
                     "".join(f"<tr><td style='color:#666;padding-right:12px;vertical-align:top'>{h(k)}</td><td>{h(v)}</td></tr>" for k, v in rows) + "</table>")
        text += [f"SELL {t['sym']} ({nm(t['sym'])}) {_pct(x.get('pnl_pct'))}"] + [f"  {k}: {v}" for k, v in rows]
    for t in [t for t in trades if t["side"] == "BUY"]:
        tg = TG.loc[t["sym"]] if t["sym"] in TG.index else None
        rows = [("Bought", f"{t['price_date']} at {_px(t['price'])} ({t['shares']:,.2f} shares, {_usd(t['value'])})"),
                ("12m target", f"{_px(tg.target)} ({_pct(tg.upside)})" if tg is not None else "n/a"), ("Why", t["reason"])]
        items.append(f"<h3 style='color:#0969da'>BUY {h(t['sym'])} &middot; {h(nm(t['sym']))}</h3><table>" +
                     "".join(f"<tr><td style='color:#666;padding-right:12px;vertical-align:top'>{h(k)}</td><td>{h(v)}</td></tr>" for k, v in rows) + "</table>")
        text += [f"BUY {t['sym']} ({nm(t['sym'])})"] + [f"  {k}: {v}" for k, v in rows]
    if w_new:
        li = "".join(f"<li><b>{h(s)}</b> {h(nm(s))} ({h(_px(sig.price.get(s)))}): {h(w)}</li>" for s, w in w_new.items())
        items.append(f"<h3 style='color:#9a6700'>New BUY, waiting</h3><ul>{li}</ul>")
        text += ["New BUY, waiting:"] + [f"  {s} ({nm(s)}): {w}" for s, w in w_new.items()]
    gone = [s for s in w_gone if s not in bought]
    if gone:
        li = "".join(f"<li><b>{h(s)}</b> {h(nm(s))}: now {h(sig.state.get(s, 'not tracked'))}</li>" for s in gone)
        items.append(f"<h3 style='color:#666'>No longer waiting (BUY signal gone, not bought)</h3><ul>{li}</ul>")
        text += ["No longer waiting (not bought):"] + [f"  {s}: now {sig.state.get(s, 'not tracked')}" for s in gone]
    return items, text, W, trades


def summary(L, S, T):
    from portfolio import positions
    pos, cash = positions(L)
    op = [x for x in T if x["status"] == "open"]
    nav = cash + sum(x["sh"] * (x.get("last_px") or 0) for x in op)
    h = lambda s: html.escape(str(s))
    rows = "".join(f"<tr><td><b>{h(x['sym'])}</b></td><td>{h(x['entry_date'])}</td><td align=right>{_px(x['entry_px'])}</td>"
                   f"<td align=right>{_px(x.get('last_px'))}</td><td align=right>{_usd(x['pnl'])}</td><td align=right>{_pct(x['pnl_pct'])}</td></tr>"
                   for x in sorted(op, key=lambda x: x["sym"]))
    free = C.MAX_HOLD - len(pos)
    tab = ("<h3>Portfolio now</h3><table cellpadding=3><tr style='color:#666'><td>Stock</td><td>Bought</td><td align=right>Entry</td>"
           f"<td align=right>Last</td><td align=right>P&amp;L</td><td align=right>%</td></tr>{rows}</table>"
           f"<p>Value {_usd(nav)} ({_pct(nav / L['capital'] - 1)} since {h(L['price_date'])}); cash {_usd(cash)} in {free} empty slot{'s' * (free != 1)}. "
           f"Prices of {h(S.attrs['price_date'])} close.</p>")
    txt = [f"Portfolio now: value {_usd(nav)} ({_pct(nav / L['capital'] - 1)}), cash {_usd(cash)} in {free} empty slots"] + \
          [f"  {x['sym']}: bought {x['entry_date']} at {_px(x['entry_px'])}, last {_px(x.get('last_px'))}, P&L {_usd(x['pnl'])} ({_pct(x['pnl_pct'])})" for x in op]
    return tab, txt


def send(subject, body_html, body_text):
    user, pw, to = (os.environ.get(k) for k in ("SMTP_USER", "SMTP_PASS", "MAIL_TO"))
    m = EmailMessage()
    m["Subject"], m["From"], m["To"] = subject, f"Growth Tracker <{user}>", to
    m.set_content(body_text)
    m.add_alternative(body_html, subtype="html")
    with smtplib.SMTP(os.environ.get("SMTP_HOST", "smtp.gmail.com"), int(os.environ.get("SMTP_PORT", "587")), timeout=60) as s:
        s.starttls()
        s.login(user, pw)
        s.send_message(m)


def run(L, S, T, TG, log=print):
    if not all(os.environ.get(k) for k in ("SMTP_USER", "SMTP_PASS", "MAIL_TO")):
        log("e-mail: SMTP_USER / SMTP_PASS / MAIL_TO not set - no notice")
        return
    st = _load()
    first = st is None
    st = st or {"n_trades": len(L["trades"]), "waiting": {}}
    if st["n_trades"] > len(L["trades"]):                   # ledger restarted
        st = {"n_trades": 0, "waiting": {}}
    items, text, W, trades = build(L, S, T, TG, st)
    if not items and not first:
        log("e-mail: no position change - no notice")
        st["waiting"] = W
        json.dump(st, open(STATE, "w", encoding="utf-8"), indent=1)
        return
    tab, txt = summary(L, S, T)
    n_b, n_s = sum(t["side"] == "BUY" for t in trades), sum(t["side"] == "SELL" for t in trades)
    parts = [f"{n_s} sell" + "s" * (n_s != 1)] * bool(n_s) + [f"{n_b} buy" + "s" * (n_b != 1)] * bool(n_b)
    nw = len(set(W) - set(st.get("waiting") or {}))
    parts += [f"{nw} waiting"] * bool(nw)
    subj = "Growth Tracker: " + (", ".join(parts) if parts else "notifications on") + f" ({S.attrs['price_date']})"
    if first:
        items.insert(0, "<p>Notifications are on: you get an e-mail when the model portfolio buys or sells, and when a stock "
                        "gets a BUY signal it is still waiting to buy.</p>")
        if W:
            items.append("<h3 style='color:#9a6700'>BUY, waiting now</h3><ul>" +
                         "".join(f"<li><b>{html.escape(s)}</b>: {html.escape(w)}</li>" for s, w in W.items()) + "</ul>")
            text += ["BUY, waiting now:"] + [f"  {s}: {w}" for s, w in W.items()]
    body = ("<div style='font-family:Arial,sans-serif;font-size:14px'>" + "".join(items) + tab +
            f"<p><a href='{PAGE}'>Open the Growth Tracker</a> &middot; paper portfolio, a strategy simulation (no orders are sent).</p></div>")
    try:
        send(subj, body, "\n".join(text + [""] + txt + ["", PAGE]))
    except Exception as e:                                   # never break the daily run; retried next run
        log(f"e-mail: send failed ({type(e).__name__}: {e}) - retried next run")
        return
    json.dump({"n_trades": len(L["trades"]), "waiting": W}, open(STATE, "w", encoding="utf-8"), indent=1)
    log(f"e-mail: sent '{subj}'")
