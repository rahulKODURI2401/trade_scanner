# PC Trade Scanner

Local, browser-based NSE trade scanner built from *Trade Scanner Detailed Requirements v1.0*, running on
**real market data** (Upstox official API by default). Intraday (5m) and Swing/Daily modes, a 100-point explainable
score, BUY CANDIDATE / WAIT / NO BUY / SELL CANDIDATE with entry, SL, T1, T2, R:R and quantity, candle charts with
projection zones, a Market-today page for the daily routine, and a Calls page that tracks every signal on real candles.

> Rule-based decision support. The score is a screening score, not a probability of profit. A BUY CANDIDATE is not a
> guarantee or personalised investment advice. No orders are ever placed.

## Deploy on your PC

### 1. Install Python
Python **3.11 or 3.12** from https://www.python.org/downloads/.
On Windows tick **"Add Python to PATH"** in the installer. Check with `python --version` (Windows) or `python3 --version` (macOS/Linux).

### 2. Quick start (one click)
Unzip the project, then:
- **Windows:** double-click `run_windows.bat`
- **macOS / Linux:** in a terminal, `./run_mac_linux.sh`

The first run creates `.venv` and installs packages (a few minutes); later runs start in seconds.
Your browser opens **http://localhost:8501**.

### 3. Manual start (same result)
```bash
cd trade_scanner
python -m venv .venv                 # macOS/Linux: python3 -m venv .venv
.venv\Scripts\activate               # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```
Stop with **Ctrl+C**. The app listens on `localhost` only (see `.streamlit/config.toml`); change the port there if 8501 is busy.

### 4. Verify
```bash
pytest -q          # 17 tests: indicators, no look-ahead, risk maths, vetoes, data gates, provider parsing, call tracking
```
Then open **Settings > Data source > Test connection** (or *Logs / diagnostics*): it should report NIFTY 50
daily candles with a recent date. The tests run offline on generated test data; the app itself always uses real data.

## Market data
| Provider | Setup | Notes |
|---|---|---|
| **Upstox** (default) | none | Official API. Candle endpoints are free and need no login. 5m history ~1 month, daily ~600 days, plus today's live candles. |
| **Zerodha Kite** | API key + daily access token | Official Kite Connect (paid subscription). Enter both in Settings > Data source, or set `KITE_API_KEY` / `KITE_ACCESS_TOKEN`. The token expires every morning. |
| **Yahoo Finance** | none | Via `yfinance`. Unofficial, personal use only, intraday may be delayed: fine for swing scans, not for live intraday entries. |
| **CSV files** | your files | `data/csv/<SYMBOL>_5m.csv`, `<SYMBOL>_1D.csv` with `timestamp,open,high,low,close,volume`. |

TradingView and Chartink are **not** supported: neither offers a public data API, and scraping them breaks their terms
(and the requirements' "no scraped feeds" rule). You can still cross-check any signal on their charts.

Credentials are kept only in `data/credentials.json` on your PC (never in the database or config versions).

**Universe:** NIFTY 50 is bundled (as of Sep 2026). NIFTY Next 50 / 100 / 200 / 500 / BANK download from
niftyindices.com on first use (Settings > Universe, with a refresh button). A custom symbol list is also supported.
Watchlist symbols can be any NSE equity.

**When the market is closed** intraday scans still run on the last session, but every intraday result is NO BUY with
"Market closed: review-only". Swing scans use the last *completed* daily candle, so run them after 15:30.
NSE holidays are not modelled.

## Daily use
1. **Before 9:15** open *Market today*: session phase, NIFTY / BANK NIFTY candles with VWAP and pivots, index bias,
   India VIX, breadth, sector strength and your risk budget. Review last evening's swing candidates.
2. **9:30 - 14:30** keep the *Intraday scanner* on auto-refresh (5 min). Act only on candidates that agree with the
   index bias. Open *Signal detail* for the chart, projection zones, score drill-down and this setup's track record
   on that stock. Size from the risk panel, then "Save to journal".
3. **After 15:30** run the *Swing scanner*, review *Calls* (targets/stops hit, results in R), and fill actual fills in the
   *Journal*. The Market-today risk budget stops you after the max daily loss or max trades.

## Screens
| Screen | Purpose |
|---|---|
| Market today | Session phase, index candles + bias, VIX, breadth, sector strength, daily risk budget, routine checklist |
| Calls | Every candidate tracked on real candles: waiting entry, active, T1 (stop to cost), T2, SL, expiry, result in R |
| Dashboard | Both modes together: counts, filters, ranked call cards, recent alerts |
| Intraday / Swing scanner | Run or auto-refresh a scan, filter, cards or table view, CSV/Excel export |
| Signal detail | Chart with 11 overlays + entry/SL/targets, score drill-down, why / why-not, gates, data quality, position size |
| Watchlist | Symbols to track; scanners can scan the watchlist only |
| Journal | Save a plan, record actual entry/exit, P&L |
| Backtest | Same rules replayed on history, costs, in/out-of-sample metrics, trade log |
| Settings | All thresholds; every save is a new config version |
| Logs / diagnostics | Startup checks, per-symbol data quality, scan runs, events |

## Project layout
```
app.py                  Streamlit entry point and navigation
config/default_config.yaml  default thresholds
scanner/                engine (no UI code)
  providers.py          DataProvider base, CSVProvider, provider factory
  live_providers.py     Upstox, Kite, Yahoo
  universe.py           NIFTY lists and custom universe
  calls.py              call tracking on real candles
  credentials.py        local API credentials
  indicators.py         EMA/SMA/VWAP/Supertrend/BB/Pivot/RSI/MACD/ATR/swings (causal)
  patterns.py           candle patterns
  setups.py             intraday + swing setup families
  scoring.py            data quality, regime, risk plan, 100-point score
  engine.py             decide() shared by live scan and backtest
  backtest.py           replay engine and metrics
  storage.py            SQLite (data/scanner.db)
ui/                     screens and components
tests/                  pytest suite
```

## Backup
Settings, signals, calls, watchlist and journal live in `data/scanner.db`. Copy that file to back up.
`data/cache/` holds downloaded instrument lists and can be deleted at any time.

## Optional
- Desktop notifications: `pip install plyer`, then Settings → Alerts → Desktop notifications.
