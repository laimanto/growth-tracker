# Growth Tracker

Live tracker and paper portfolio for a sector-relative fast-grower rule on the S&P 500 + Nasdaq-100.
Research and backtests: `Sortino/Research/Cisco_2026-10-23` (README Parts 13-23). Started 2026-10-24
(bought at the 2026-10-23 close, $100,000 notional, equal weight; reset while the rule was being tuned - v1.6 floor, one-off quarters).

**Page:** `docs/index.html`. It is self-contained; open it locally or serve `docs/` with GitHub Pages.
Local copy: `dashboard/growth_tracker.html` (refreshed by every local run; `git pull` updates `docs/index.html` after the daily CI run).
`dashboard/` is git-ignored and also keeps the archived `growth_tracker_v1.0.html`.

## The rule
* **EPS growth rule:** expected EPS growth in every forecast fiscal year (current, next, year after), and over the last 12 months
  when buying, must be at least the growth floor: 15% (buy) / 10% (keep) x universe median P/E / 18.96 (its 2002-26 average)
* **PEG rule:** PEG = P/E (last close / last 4 reported quarters' EPS) / growth in %, per year. Buy only if every PEG <= sector median
  P/E / 30; sell once any forecast-year PEG > sector median P/E / 25
* **buy** when both rules pass; **sell** when either fails. Together this equals growth >= max(30% x P/E / sector P/E, floor) to enter
  and < max(25% x P/E / sector P/E, keep floor) to exit (v1.2; floor v1.6, Research Part 23/23b; the last reported year is shown, not required)
* **12m target** (buy reference, not a sell price) = last close x (1 + forecast next-12m EPS growth) x P/E-model change; the P/E model is the Sortino project's pe28 (data/models/, needs scikit-learn 1.9.0), with the growth-only pe28g for stocks without 24 months of P/E history. Backtest: unbiased estimate of the 12-month peak (Research Part 22)
* act 3-14 days after each report (when analysts revise) or at the first close of each month (monthly sweep)
* **readiness (v1.9):** a stock trades only when its data is mature: forecasts settled after a report (<= 10% of analysts revised next
  year in 7 days, or consensus moved < 1%; decided anyway from day 12), fiscal year rolled at Yahoo, >= 3 next-year forecasts, FY+2 aligned,
  Yahoo data fresh (<= 8 days, complete). A buy needs >= 10 analysts on the next fiscal year (v1.10, Research Part 24m; >= 3 after a fiscal-year rollover to be 'ready'). Held stocks that leave the indexes stay tracked (no new buys);
  a holding without a price for 7 days (delisted / acquired) is sold at its last close. Page tags: "waiting", "left index"
* **portfolio (v1.8):** 10 fixed slots of equal start size; each stock keeps its own money (no trims, no rebalancing). A sale's
  whole value waits in cash for the next qualifier; when full, a new BUY replaces the lowest-12m-upside holding if its own upside is
  >= 5 points higher; at the first close of each quarter every holding with upside below 10% is sold. Empty slots wait in cash
  until a stock qualifies (no near-miss fill since v1.10). (`config.MAX_HOLD`, `SWAP_BUFFER`, `PRUNE_*`; Research Part 24h-i)

## Daily update (delta only)
`python growth/run.py`, run by `.github/workflows/update.yml` at 21:30 UTC Mon-Fri. Downloads only what is due:
| data | refreshed |
|---|---|
| `data/universe.json` (Wikipedia S&P 500 / Nasdaq-100) | monthly |
| `data/eps/<TICKER>.json`: Yahoo consensus, reported quarters, revisions, next report | new stock / after a report / every 2 days in the post-report window / otherwise weekly, spread over the week |
| same file, Nasdaq FY+2 consensus | every 2 weeks and after a report |
| `data/closes.csv`, `data/bench.csv` | only the days since the last stored close |
| `data/closes_m.csv` (month-end closes, 3+ years, P/E history for the target) | built once, then completed months appended |
Failed or incomplete Yahoo / Nasdaq downloads keep the last good copy and are retried on every daily run (`retry_y` / `retry_n` in the stock's file), as are stocks whose latest reported quarter is missing; a past report date without EPS is filled from Yahoo's `earnings_history`. Second share classes (GOOG, FOX, NWS) use the main class's EPS data (`config.SHARE_CLASS`).
A normal day fetches about 50-100 stocks and a few rows of prices. `--offline` rebuilds the page without downloading.

## E-mail notices
`growth/notify.py` (called by every run) e-mails when the model portfolio changes: each SELL with the whole trade (bought /
sold date, price, shares, value, profit or loss in $ and %, days held, why bought / why sold), each BUY (price, value, 12m target,
why), stocks that get a BUY signal but are still waiting (data not ready / next review window or month start / portfolio full)
and waiting stocks whose BUY signal went away; plus the current holdings with P&L. No change -> no e-mail.
Needs three repository secrets (unset -> no e-mail, the run is unaffected): `SMTP_USER` (Gmail address), `SMTP_PASS` (a Gmail
app password, https://myaccount.google.com/apppasswords - needs 2-step verification) and `MAIL_TO` (kept out of the public code).
`data/notify.json` = last notified trade + waiting list; it only advances after a successful send (a failed send is retried next run).

## Forecast history (data/history/)
The backtests use simulated forecasts: actual later EPS, leaning 40% on trailing growth, plus a 30% random log error (AR 0.9 per month;
Research Part 19). To check and refit this with real data, every run appends (`growth/history.py`; append-only plain CSV, never rewritten):
| file | content |
|---|---|
| `forecasts_YYYY.csv` | every consensus download as fetched (before one-off adjustments): Yahoo current / next quarter and current / next fiscal year (avg, low, high, analysts, year-ago EPS, revisions 7d / 30d) and Nasdaq FY+1..FY+3. `yahoo_trend` rows = one-time seed from Yahoo's 7-90 days-ago consensus |
| `actuals.csv` | every reported quarter (EPS, estimate just before the report); Yahoo only shows a window, this keeps them all. Restated quarters get a new row |
| `signals_YYYY.csv` | per stock and price date: state, price, P/E, sector P/E, growth Last / Cur / +1 / +2 / trailing / 24m, analysts, 12m target and upside |
Seeded 2026-10-26 from the git history of `data/eps/` (downloads from 2026-10-21) plus Yahoo's 90-day trend. About 15-20 MB a year.

**Download:** `git pull` in the local clone (the CI commits the files daily), or open
https://github.com/laimanto/growth-tracker/tree/main/data/history and download any file.
**Check the assumption:** `python growth/forecast_check.py [--csv errors.csv]` - error ln(actual / forecast FY EPS) by months to
fiscal-year end (bias, SD, robust SD vs the assumed 30%), anchoring weight w (assumed 0.6) and month-to-month persistence (assumed 0.9);
also the quarterly surprise since 2008 as a reference. Fiscal years start resolving as current FYs are reported (from Nov 2026);
a useful sample needs ~1 year for the current FY and 2-3 years for FY+1 / FY+2.

## Files
| file | role |
|---|---|
| `growth/config.py` | paths, rule parameters, refresh policy, benchmarks |
| `growth/fetch.py` | Yahoo / Nasdaq / price downloads |
| `growth/update.py` | what is due today, delta merges |
| `growth/signals.py` | the rule, fiscal-year growth (Last / Cur / +1 / +2), states BUY / HOLD / SELL / SOLD / WATCH / OUT |
| `growth/portfolio.py` | paper ledger `data/ledger.json`, positions (buy -> sell rows), NAV |
| `growth/pemodel.py` | 12-month target from the P/E model |
| `growth/perf.py` | annual return / Sortino / downside deviation / max drawdown for 4 windows |
| `growth/run.py`, `growth/template.html` | daily run and the page |
| `growth/history.py`, `growth/forecast_check.py` | forecast history (appended every run), check of the backtest's forecast-error assumption |
| `growth/notify.py` | e-mail notice on trades and new BUY-waiting stocks |
| `data/backtest.json` | Strategy-tab backtest results (static, from the research) |

No orders are ever sent to a broker. Delete `data/ledger.json` to restart the paper portfolio.

## Publishing
Public repository `laimanto/growth-tracker`; the workflow publishes `docs/index.html` to GitHub Pages:
https://laimanto.github.io/growth-tracker/ . This is a **simulation of a strategy** (paper portfolio), not anyone's investments.
Schedule: 21:35 UTC Mon-Fri (after the US close in both seasons), with a 03:30 UTC retry slot that only runs if nothing
was published in the last 14 hours. Typical run 3-5 minutes.
