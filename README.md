# Growth Tracker

Live tracker and paper portfolio for a sector-relative fast-grower rule on the S&P 500 + Nasdaq-100.
Research and backtests: `Sortino/Research/Cisco_2026-10-23` (README Parts 13-21). Started 2026-10-23
(bought at the 2026-10-22 close, $100,000 notional, equal weight).

**Page:** `docs/index.html`. It is self-contained; open it locally or serve `docs/` with GitHub Pages.

## The rule
* relative P/E = P/E (last close / last 4 reported quarters' EPS) / median P/E of the stock's sector
* **enter** when trailing 12-month EPS growth **and each** forecast fiscal year (current, next, year after) are >= 30% x relative P/E
* **exit** when **any** forecast fiscal year < 25% x relative P/E (v1.2; the last reported year is shown, not required)
* **12m target** (buy reference, not a sell price) = last close x (1 + forecast next-12m EPS growth) x P/E-model change; the P/E model is the Sortino project's pe28 (data/models/, needs scikit-learn 1.9.0), with the growth-only pe28g for stocks without 24 months of P/E history. Backtest: unbiased estimate of the 12-month peak (Research Part 22)
* act 3-14 days after each report (when analysts revise) or at the first close of each month (monthly sweep, equal-weight reset)

## Daily update (delta only)
`python growth/run.py`, run by `.github/workflows/update.yml` at 21:30 UTC Mon-Fri. Downloads only what is due:
| data | refreshed |
|---|---|
| `data/universe.json` (Wikipedia S&P 500 / Nasdaq-100) | monthly |
| `data/eps/<TICKER>.json`: Yahoo consensus, reported quarters, revisions, next report | new stock / after a report / every 2 days in the post-report window / otherwise weekly, spread over the week |
| same file, Nasdaq FY+2 consensus | every 2 weeks and after a report |
| `data/closes.csv`, `data/bench.csv` | only the days since the last stored close |
| `data/closes_m.csv` (month-end closes, 3+ years, P/E history for the target) | built once, then completed months appended |
A normal day fetches about 50-100 stocks and a few rows of prices. `--offline` rebuilds the page without downloading.

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
| `data/backtest.json` | Strategy-tab backtest results (static, from the research) |

No orders are ever sent to a broker. Delete `data/ledger.json` to restart the paper portfolio.

## Publishing
Public repository `laimanto/growth-tracker`; the workflow publishes `docs/index.html` to GitHub Pages:
https://laimanto.github.io/growth-tracker/ . This is a **simulation of a strategy** (paper portfolio), not anyone's investments.
Schedule: 21:35 UTC Mon-Fri (after the US close in both seasons), with a 03:30 UTC retry slot that only runs if nothing
was published in the last 14 hours. Typical run 3-5 minutes.
