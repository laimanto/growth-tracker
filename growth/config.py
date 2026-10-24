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
LOCAL_HTML = os.path.join(ROOT, "dashboard", "growth_tracker.html")   # local copy (git-ignored), written on local runs only
TEMPLATE = os.path.join(ROOT, "growth", "template.html")

# the rule (Research/Cisco_2026-10-23 Parts 14-21; v1.2 each forecast year must clear the bar - year_by_year_rule.py)
ENTRY, EXIT = 0.30, 0.25          # x relative P/E
FLOOR_IN, FLOOR_OUT = 0.15, 0.10  # growth floor under both bars at an average market P/E (v1.6, Research Part 23: cheap stocks needed only 5-8%)
MKT_PE_BASE = 18.96               # floor x (universe median P/E / this): average universe median P/E 2002-2026 (Part 23b) -> adapts to the era
WATCH = 0.05                      # WATCH = not held, weakest of trailing / forecast years at most 5 pts below the entry bar
REVIEW = (3, 14)                  # act 3-14 days after a report (consensus revisions cluster there)
HIGHLIGHT_DAYS = 7                # new entries / exits stay highlighted (and SOLD rows listed) for a week
CAPITAL = 100_000.0
# portfolio policy (v1.8, Research Part 24h-i): MAX_HOLD fixed slots, each stock keeps its own money (no trims, no
# rebalancing). A sale's whole value waits in cash for the next qualifier; a new BUY when full replaces the lowest-upside
# holding if its own 12m upside is >= SWAP_BUFFER higher; at the first close of each PRUNE_MONTHS month up to PRUNE_N
# holdings with 12m upside below PRUNE_UPSIDE are sold (lowest first)
MAX_HOLD = 10
SWAP_BUFFER = 0.05
PRUNE_MONTHS = (1, 4, 7, 10)
PRUNE_N = None                    # None = every holding below PRUNE_UPSIDE (Part 24k: a limit of 2 rarely binds)
PRUNE_UPSIDE = 0.10
# readiness to act (v1.9): a stock is only bought / sold once its data is mature
SETTLE_SHARE = 0.10               # forecasts settled when <= 10% of analysts (min 1) revised next year in the last 7 days ...
SETTLE_MOVE = 0.01                # ... or the next-year consensus moved < 1% in 7 days; decided anyway at the end of REVIEW
MIN_ANALYSTS = 3                  # at least 3 analysts on next year's forecast to buy
STALE_DAYS = 8                    # no trades on Yahoo data older than this (or flagged incomplete)
DELISTED_DAYS = 7                 # a holding without a price for this many days is sold at its last close
# second share classes use the main class's EPS data (same company, one consensus); only the price is their own.
# Yahoo covers the second class thinly and inconsistently (GOOG 2026-10: 8 analysts for FY26 and quarters on another basis)
SHARE_CLASS = {"GOOG": "GOOGL", "FOX": "FOXA", "NWS": "NWSA"}
# extra US tickers outside the S&P 500 / Nasdaq-100 (user requests): shown with the same rules, tagged 'watchlist';
# never bought by the model portfolio and left out of the sector / market P/E medians
EXTRA = {"FUTU": {"name": "Futu Holdings (ADR)", "sector": "Financial Services"}}

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
