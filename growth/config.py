"""Growth Tracker settings and paths (self-contained: everything lives under GrowthTracker/)."""
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DATA = os.path.join(ROOT, "data")
EPS_DIR = os.path.join(DATA, "eps")                 # one JSON per stock (small git diffs)
UNIVERSE = os.path.join(DATA, "universe.json")
STATE = os.path.join(DATA, "state.json")            # run log
LEDGER = os.path.join(DATA, "ledger.json")          # model portfolio trades
SIGHIST = os.path.join(DATA, "signal_history.json") # date each stock entered its current signal
CLOSES = os.path.join(DATA, "closes.csv")           # universe daily closes (from the portfolio start, appended daily)
CLOSES_M = os.path.join(DATA, "closes_m.csv")       # month-end closes, 3+ years (P/E history for the 12m target)
BENCHF = os.path.join(DATA, "bench.csv")            # benchmark daily closes / total return since Dec 2021 (appended daily)
BACKTEST = os.path.join(DATA, "backtest.json")
NDX_SNAP = os.path.join(DATA, "ndx_snapshots.json")
OUT_HTML = os.path.join(ROOT, "docs", "index.html")
TEMPLATE = os.path.join(ROOT, "growth", "template.html")

# the rule (Research/Cisco_2026-10-23 Parts 14-21; v1.2 each forecast year must clear the bar - year_by_year_rule.py)
ENTRY, EXIT = 0.30, 0.25          # x relative P/E
WATCH = 0.05                      # WATCH = not held, weakest of trailing / forecast years at most 5 pts below the entry bar
REVIEW = (3, 14)                  # act 3-14 days after a report (consensus revisions cluster there)
HIGHLIGHT_DAYS = 7                # new entries / exits stay highlighted (and SOLD rows listed) for a week
CAPITAL = 100_000.0

# refresh policy (delta updates)
EPS_MAX_AGE = 7                   # every stock's Yahoo consensus at least weekly (spread over the week)
EPS_REVIEW_AGE = 2                # every 2 days while in the post-report window
NASDAQ_MAX_AGE = 14               # FY+2 consensus (Nasdaq / Zacks) every two weeks, and after a report
UNIVERSE_MAX_AGE = 30

BENCH = ["QQQ", "SPY", "^NDX", "QGRW", "GLD", "IBIT", "TLT", "CHAT", "^HSI", "BRK-B", "ALLW"]
LABEL = {"^NDX": "NDX", "^HSI": "HSI", "BRK-B": "BRK.B"}
BENCH_NAME = {"QQQ": "Invesco QQQ (Nasdaq-100 ETF)", "SPY": "SPDR S&P 500 ETF", "NDX": "Nasdaq-100 index (price only)",
              "QGRW": "WisdomTree US Quality Growth ETF", "GLD": "SPDR Gold", "IBIT": "iShares Bitcoin Trust",
              "TLT": "iShares 20+ Year Treasury", "CHAT": "Roundhill Generative AI & Technology ETF",
              "HSI": "Hang Seng index (HKD, price only)", "BRK.B": "Berkshire Hathaway B", "ALLW": "SPDR Bridgewater All Weather ETF"}
