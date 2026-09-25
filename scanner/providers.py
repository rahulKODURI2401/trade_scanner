"""Market data provider abstraction (requirements section 4.2).

Scanner logic never talks to a vendor directly. Two providers ship with the MVP:

* UpstoxProvider - official Upstox v3 candle API (free, no login for candles). Default.
* KiteProvider   - official Zerodha Kite Connect API (needs your API key + daily access token).
* YahooProvider  - Yahoo Finance via yfinance (quick fallback; unofficial, personal use, may be delayed).
* CSVProvider    - your own licensed OHLCV exports from data/csv/.

To add another broker, subclass DataProvider (see live_providers.py) and register
it in get_provider(); nothing else in the scanner needs to change.
"""
from __future__ import annotations

import hashlib
import os
from abc import ABC, abstractmethod
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

IST = ZoneInfo("Asia/Kolkata")
TF_MINUTES = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60}
OHLCV = ["open", "high", "low", "close", "volume"]


class DataProvider(ABC):
    name = "base"
    is_demo = False

    @abstractmethod
    def get_instruments(self) -> pd.DataFrame: ...

    @abstractmethod
    def get_historical_ohlcv(self, symbol: str, timeframe: str, start=None, end=None) -> pd.DataFrame: ...

    def get_intraday_ohlcv(self, symbol: str, timeframe: str) -> pd.DataFrame:
        return self.get_historical_ohlcv(symbol, timeframe)

    def get_latest_quote(self, symbol: str) -> dict:
        df = self.get_intraday_ohlcv(symbol, "5m")
        if df.empty:
            return {"symbol": symbol, "last": None, "timestamp": None}
        return {"symbol": symbol, "last": float(df["close"].iloc[-1]), "timestamp": df.index[-1]}

    def now(self) -> datetime:
        return datetime.now(IST)

    def get_market_status(self, cfg: dict) -> dict:
        now = self.now()
        o = time.fromisoformat(cfg["session"]["open"])
        c = time.fromisoformat(cfg["session"]["close"])
        is_weekday = now.weekday() < 5
        is_open = is_weekday and o <= now.time() < c
        nxt = now
        if not is_open:
            nxt = now + timedelta(days=1) if now.time() >= c or not is_weekday else now
            while nxt.weekday() >= 5:
                nxt += timedelta(days=1)
        next_open = datetime.combine(nxt.date(), o, tzinfo=IST)
        return {"is_open": is_open, "now": now, "next_open": next_open if not is_open else None}

    def get_exchange_calendar(self) -> list:
        return []  # exchange holidays are not modelled; weekends are skipped

    def prefetch(self, symbols: list, timeframe: str) -> None:
        """Optional bulk download before a scan (providers override for speed)."""
        return None

    def index_symbols(self) -> dict:
        """Display name -> provider symbol for market-context indices."""
        return {}


# ----------------------------------------------------------------------------
# CSV provider
# ----------------------------------------------------------------------------
class CSVProvider(DataProvider):
    """Reads <folder>/<SYMBOL>_<timeframe>.csv (e.g. RELIANCE_5m.csv, RELIANCE_1D.csv)
    with columns: timestamp,open,high,low,close,volume. Timestamps without a
    timezone are treated as Asia/Kolkata. Optional <folder>/instruments.csv with
    symbol,exchange,sector,lot_size,tick_size."""

    name = "csv"

    def __init__(self, cfg: dict, root: Path):
        folder = Path(cfg["data"]["csv_folder"])
        self.folder = folder if folder.is_absolute() else root / folder

    def get_instruments(self) -> pd.DataFrame:
        meta = self.folder / "instruments.csv"
        if meta.exists():
            df = pd.read_csv(meta)
        else:
            syms = sorted({p.stem.rsplit("_", 1)[0] for p in self.folder.glob("*_*.csv")})
            df = pd.DataFrame({"symbol": syms})
        for col, default in [("exchange", "NSE"), ("segment", "EQ"), ("sector", "Unclassified"),
                             ("lot_size", 1), ("tick_size", 0.05), ("active", True)]:
            if col not in df.columns:
                df[col] = default
        return df

    def get_historical_ohlcv(self, symbol, timeframe, start=None, end=None) -> pd.DataFrame:
        path = self.folder / f"{symbol}_{timeframe}.csv"
        if not path.exists():
            return pd.DataFrame(columns=OHLCV)
        df = pd.read_csv(path)
        df.columns = [c.strip().lower() for c in df.columns]
        ts = pd.to_datetime(df["timestamp"])
        ts = ts.dt.tz_localize(IST) if ts.dt.tz is None else ts.dt.tz_convert(IST)
        df = df.assign(timestamp=ts).set_index("timestamp").sort_index()[OHLCV].astype(float)
        return df


def get_provider(cfg: dict, root: Path) -> DataProvider:
    name = cfg["data"].get("provider", "upstox")
    if os.environ.get("TRADE_SCANNER_TEST_SYNTHETIC") == "1":  # automated tests only, never used by the app
        import sys
        sys.path.insert(0, str(root))
        from tests.synthetic import DemoProvider
        return DemoProvider(cfg)
    if name == "csv":
        return CSVProvider(cfg, root)
    from .live_providers import KiteProvider, UpstoxProvider, YahooProvider
    cls = {"upstox": UpstoxProvider, "yahoo": YahooProvider, "kite": KiteProvider}.get(name, UpstoxProvider)
    return cls(cfg, root)
