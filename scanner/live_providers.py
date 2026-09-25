"""Real market data providers.

UpstoxProvider (default)  official Upstox v3 candle API. Candle endpoints need no login.
KiteProvider              official Zerodha Kite Connect API. Needs API key + daily access token.
YahooProvider             Yahoo Finance via yfinance. Quick fallback: unofficial, personal use only,
                          intraday may be delayed. Not recommended for live intraday decisions.

TradingView and Chartink are deliberately not supported: neither offers a public data API,
and scraping them breaks their terms (and the requirements' 'no scraped feeds' rule).
"""
from __future__ import annotations

import gzip
import json
import threading
import time as _time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import pandas as pd

from .credentials import load_credentials
from .providers import IST, OHLCV, DataProvider
from .universe import load_universe

INDICES = ["NIFTY 50", "NIFTY BANK", "INDIA VIX"]


def _normalise(rows, cols=("timestamp", "open", "high", "low", "close", "volume")) -> pd.DataFrame:
    if rows is None or len(rows) == 0:
        return pd.DataFrame(columns=OHLCV)
    df = pd.DataFrame([r[:6] for r in rows], columns=list(cols))
    ts = pd.to_datetime(df["timestamp"])
    ts = ts.dt.tz_localize(IST) if ts.dt.tz is None else ts.dt.tz_convert(IST)
    df = df.assign(timestamp=ts).set_index("timestamp").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df[OHLCV].astype(float)


class LiveProvider(DataProvider):
    """Shared caching, universe and parallel prefetch for HTTP providers."""

    def __init__(self, cfg: dict, root: Path):
        self.cfg, self.root = cfg, Path(root)
        self.cache_dir = self.root / "data" / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._mem: dict = {}
        self.errors: dict = {}
        self._universe = None

    # -- universe --------------------------------------------------------------
    def get_instruments(self) -> pd.DataFrame:
        if self._universe is None:
            u = load_universe(self.cfg, self.root).copy()
            u["exchange"], u["segment"], u["lot_size"], u["tick_size"], u["active"] = "NSE", "EQ", 1, 0.05, True
            self._universe = u
        return self._universe

    def index_symbols(self) -> dict:
        return {name: name for name in INDICES}

    # -- caching ---------------------------------------------------------------
    def _ttl(self, timeframe: str) -> int:
        if timeframe in ("1D", "D"):
            return self.cfg["data"].get("cache_seconds_daily", 900)
        return self.cfg["data"].get("cache_seconds_intraday", 45)

    def get_historical_ohlcv(self, symbol, timeframe, start=None, end=None) -> pd.DataFrame:
        key = (symbol, timeframe)
        hit = self._mem.get(key)
        if hit and _time.time() - hit[0] < self._ttl(timeframe):
            return hit[1].copy()
        df = self._fetch(symbol, timeframe)
        self._mem[key] = (_time.time(), df)
        self.errors.pop(symbol, None)
        return df.copy()

    def prefetch(self, symbols: list, timeframe: str) -> None:
        todo = [s for s in symbols if not ((s, timeframe) in self._mem and
                                         _time.time() - self._mem[(s, timeframe)][0] < self._ttl(timeframe))]
        if not todo:
            return

        def one(sym):
            try:
                self.get_historical_ohlcv(sym, timeframe)
            except Exception as exc:
                self.errors[sym] = f"{type(exc).__name__}: {exc}"[:300]
                print(f"[{self.name}] {sym} {timeframe}: {self.errors[sym]}")

        with ThreadPoolExecutor(max_workers=int(self.cfg["data"].get("max_workers", 6))) as pool:
            list(pool.map(one, todo))

    def test_connection(self) -> tuple[bool, str]:
        try:
            df = self.get_historical_ohlcv("NIFTY 50", "1D")
            if df.empty:
                return False, "Connected but no candles returned for NIFTY 50"
            return True, f"OK: {len(df)} daily NIFTY 50 candles, last {df.index[-1]:%d %b %Y}, close {df['close'].iloc[-1]:,.2f}"
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"

    def _fetch(self, symbol: str, timeframe: str) -> pd.DataFrame:
        raise NotImplementedError


# ------------------------------------------------------------------------------
class UpstoxProvider(LiveProvider):
    name = "upstox"
    BASE = "https://api.upstox.com/v3/historical-candle"
    MASTER = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
    INDEX_KEYS = {"NIFTY 50": "NSE_INDEX|Nifty 50", "NIFTY BANK": "NSE_INDEX|Nifty Bank", "INDIA VIX": "NSE_INDEX|India VIX"}
    UNITS = {"1m": ("minutes", 1), "3m": ("minutes", 3), "5m": ("minutes", 5), "15m": ("minutes", 15),
             "30m": ("minutes", 30), "1h": ("hours", 1), "1D": ("days", 1)}

    def __init__(self, cfg, root):
        super().__init__(cfg, root)
        import requests
        self.http = requests.Session()
        self.http.headers.update({"Accept": "application/json"})
        token = load_credentials(self.root).get("upstox_access_token")
        if token:  # optional; candle endpoints work without it
            self.http.headers["Authorization"] = f"Bearer {token}"
        self._keys = None
        self._keys_error = None
        self._lock = threading.Lock()

    def _instrument_keys(self) -> dict:
        with self._lock:  # one download shared by all worker threads; a failure is not retried per symbol
            if self._keys is None and self._keys_error is None:
                try:
                    self._keys = self._load_master()
                except Exception as exc:
                    self._keys_error = f"Upstox instrument list unavailable: {exc}"
            if self._keys_error:
                raise RuntimeError(self._keys_error)
            return self._keys

    def _load_master(self) -> dict:
        path = self.cache_dir / f"upstox_NSE_{date.today():%Y%m%d}.json.gz"
        if not path.exists():
            r = self.http.get(self.MASTER, timeout=60)
            r.raise_for_status()
            path.write_bytes(r.content)
            for old in self.cache_dir.glob("upstox_NSE_*.json.gz"):
                if old != path:
                    old.unlink(missing_ok=True)
        rows = json.loads(gzip.decompress(path.read_bytes()))
        return {r["trading_symbol"]: r["instrument_key"] for r in rows
                if r.get("segment") == "NSE_EQ" and r.get("instrument_type") == "EQ"}

    def instrument_key(self, symbol: str) -> str:
        if symbol in self.INDEX_KEYS:
            return self.INDEX_KEYS[symbol]
        u = self.get_instruments()
        row = u[u["symbol"] == symbol]
        if not row.empty and isinstance(row["isin"].iloc[0], str) and row["isin"].iloc[0].startswith("IN"):
            return f"NSE_EQ|{row['isin'].iloc[0]}"
        key = self._instrument_keys().get(symbol)
        if not key:
            raise KeyError(f"{symbol} not found in the Upstox NSE instrument list")
        return key

    def _get(self, path: str) -> list:
        r = self.http.get(f"{self.BASE}/{path}", timeout=20)
        if r.status_code != 200:
            raise RuntimeError(f"Upstox HTTP {r.status_code}: {r.text[:200]}")
        return (r.json().get("data") or {}).get("candles") or []

    def _fetch(self, symbol, timeframe):
        unit, interval = self.UNITS[timeframe]
        key = quote(self.instrument_key(symbol), safe="")
        today = datetime.now(IST).date()
        if unit == "days":
            frm = today - timedelta(days=int(self.cfg["data"].get("daily_history_days", 600)))
            hist = self._get(f"{key}/days/1/{today:%Y-%m-%d}/{frm:%Y-%m-%d}")
        else:
            # minute data: at most one month per request for intervals <= 15 minutes
            frm = today - timedelta(days=int(self.cfg["data"].get("intraday_history_days", 28)))
            hist = self._get(f"{key}/{unit}/{interval}/{(today - timedelta(days=1)):%Y-%m-%d}/{frm:%Y-%m-%d}")
        live = self._get(f"intraday/{key}/{unit}/{interval}")
        df = pd.concat([_normalise(hist), _normalise(live)])
        if unit == "days":
            df.index = df.index.normalize() + pd.Timedelta(hours=15, minutes=30)
        return df[~df.index.duplicated(keep="last")].sort_index()


# ------------------------------------------------------------------------------
class KiteProvider(LiveProvider):
    name = "kite"
    BASE = "https://api.kite.trade"
    INTERVALS = {"1m": "minute", "3m": "3minute", "5m": "5minute", "15m": "15minute", "30m": "30minute",
                 "1h": "60minute", "1D": "day"}

    def __init__(self, cfg, root):
        super().__init__(cfg, root)
        import requests
        creds = load_credentials(self.root)
        if not creds.get("kite_api_key") or not creds.get("kite_access_token"):
            raise RuntimeError("Kite needs an API key and today's access token (Settings > Data source)")
        self.http = requests.Session()
        self.http.headers.update({"X-Kite-Version": "3",
                                  "Authorization": f"token {creds['kite_api_key']}:{creds['kite_access_token']}"})
        self._tokens = None

    def _instrument_tokens(self) -> dict:
        if self._tokens is not None:
            return self._tokens
        path = self.cache_dir / f"kite_NSE_{date.today():%Y%m%d}.csv"
        if not path.exists():
            r = self.http.get(f"{self.BASE}/instruments/NSE", timeout=60)
            r.raise_for_status()
            path.write_bytes(r.content)
        df = pd.read_csv(path)
        df = df[(df["instrument_type"] == "EQ") | (df["segment"] == "INDICES")]
        self._tokens = dict(zip(df["tradingsymbol"], df["instrument_token"]))
        return self._tokens

    def _fetch(self, symbol, timeframe):
        token = self._instrument_tokens().get(symbol)
        if token is None:
            raise KeyError(f"{symbol} not found in the Kite NSE instrument list")
        now = datetime.now(IST)
        days = 700 if timeframe == "1D" else 60
        params = {"from": f"{now - timedelta(days=days):%Y-%m-%d %H:%M:%S}", "to": f"{now:%Y-%m-%d %H:%M:%S}"}
        r = self.http.get(f"{self.BASE}/instruments/historical/{token}/{self.INTERVALS[timeframe]}", params=params, timeout=20)
        if r.status_code != 200:
            raise RuntimeError(f"Kite HTTP {r.status_code}: {r.text[:200]}")
        df = _normalise(r.json()["data"]["candles"])
        if timeframe == "1D":
            df.index = df.index.normalize() + pd.Timedelta(hours=15, minutes=30)
        return df


# ------------------------------------------------------------------------------
class YahooProvider(LiveProvider):
    name = "yahoo"
    INDEX_TICKERS = {"NIFTY 50": "^NSEI", "NIFTY BANK": "^NSEBANK", "INDIA VIX": "^INDIAVIX"}
    INTERVALS = {"1m": ("1m", "7d"), "5m": ("5m", "60d"), "15m": ("15m", "60d"), "30m": ("30m", "60d"),
                 "1h": ("60m", "360d"), "1D": ("1d", "3y")}

    def ticker(self, symbol: str) -> str:
        return self.INDEX_TICKERS.get(symbol, f"{symbol}.NS")

    @staticmethod
    def _clean(raw: pd.DataFrame, timeframe: str) -> pd.DataFrame:
        if raw is None or raw.empty:
            return pd.DataFrame(columns=OHLCV)
        df = raw.copy()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.columns = [str(c).lower() for c in df.columns]
        df = df[OHLCV].dropna(subset=["open", "high", "low", "close"]).astype(float)
        idx = pd.DatetimeIndex(df.index)
        df.index = idx.tz_localize(IST) if idx.tz is None else idx.tz_convert(IST)
        if timeframe == "1D":
            df.index = df.index.normalize() + pd.Timedelta(hours=15, minutes=30)
        return df[~df.index.duplicated(keep="last")].sort_index()

    def _fetch(self, symbol, timeframe):
        import yfinance as yf
        interval, period = self.INTERVALS[timeframe]
        raw = yf.download(self.ticker(symbol), period=period, interval=interval, auto_adjust=False,
                          progress=False, threads=False)
        return self._clean(raw, timeframe)

    def prefetch(self, symbols, timeframe):
        """One bulk request for the whole universe instead of one per symbol."""
        import yfinance as yf
        todo = [s for s in symbols if (s, timeframe) not in self._mem]
        if len(todo) < 2:
            return super().prefetch(symbols, timeframe)
        interval, period = self.INTERVALS[timeframe]
        try:
            raw = yf.download([self.ticker(s) for s in todo], period=period, interval=interval, auto_adjust=False,
                              progress=False, threads=True, group_by="ticker")
        except Exception as exc:
            print(f"[yahoo] bulk download failed: {exc!r}")
            return super().prefetch(symbols, timeframe)
        now = _time.time()
        for s in todo:
            t = self.ticker(s)
            try:
                part = raw[t] if isinstance(raw.columns, pd.MultiIndex) and t in raw.columns.get_level_values(0) else None
                df = self._clean(part, timeframe)
                if df.empty:
                    self.errors[s] = "Yahoo returned no candles"
                self._mem[(s, timeframe)] = (now, df)
            except Exception as exc:
                self.errors[s] = f"{type(exc).__name__}: {exc}"[:300]
