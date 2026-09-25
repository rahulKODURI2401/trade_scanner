"""Synthetic OHLCV generator used ONLY by the automated tests (offline, deterministic).
The app itself never uses synthetic data."""
import hashlib
from datetime import datetime, time, timedelta

import numpy as np
import pandas as pd

from scanner.providers import IST, TF_MINUTES, DataProvider

# ----------------------------------------------------------------------------
# Demo provider
# ----------------------------------------------------------------------------
DEMO_UNIVERSE = [
    ("RELIANCE", "Energy", 2950), ("TCS", "IT", 4150), ("HDFCBANK", "Banking", 1680),
    ("INFY", "IT", 1880), ("ICICIBANK", "Banking", 1270), ("SBIN", "Banking", 820),
    ("BHARTIARTL", "Telecom", 1620), ("ITC", "FMCG", 480), ("LT", "Infra", 3620),
    ("HINDUNILVR", "FMCG", 2710), ("AXISBANK", "Banking", 1190), ("KOTAKBANK", "Banking", 1850),
    ("MARUTI", "Auto", 12600), ("TATAMOTORS", "Auto", 980), ("SUNPHARMA", "Pharma", 1820),
    ("TITAN", "Consumer", 3550), ("BAJFINANCE", "NBFC", 7300), ("ASIANPAINT", "Consumer", 3050),
    ("NTPC", "Power", 410), ("POWERGRID", "Power", 335), ("TATAPOWER", "Power", 445),
    ("ONGC", "Energy", 290), ("COALINDIA", "Mining", 495), ("HAL", "Defence", 4700),
    ("BEL", "Defence", 305), ("BDL", "Defence", 1180), ("TATASTEEL", "Metals", 165),
    ("JSWSTEEL", "Metals", 960), ("WIPRO", "IT", 560), ("DRREDDY", "Pharma", 1290),
]
# scenario per symbol keeps the demo interesting and reproducible
INDEX_BASE = {"NIFTY 50": 25400, "NIFTY BANK": 56200, "INDIA VIX": 12.8}
SCENARIOS = ["up_pullback", "down_rally", "range", "squeeze_break", "up_trend", "noise"]


def _seed(*parts) -> int:
    return int(hashlib.md5("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


def _session_minutes(cfg_open="09:15", cfg_close="15:30"):
    o, c = time.fromisoformat(cfg_open), time.fromisoformat(cfg_close)
    return o, c


def _build_bars(closes: np.ndarray, rng, vol_scale: float, base_vol: float) -> pd.DataFrame:
    opens = np.r_[closes[0], closes[:-1]] * (1 + rng.normal(0, vol_scale * 0.15, len(closes)))
    wick = np.abs(rng.normal(0, vol_scale * 0.55, len(closes)))
    highs = np.maximum(opens, closes) * (1 + wick * rng.uniform(0.2, 1.0, len(closes)))
    lows = np.minimum(opens, closes) * (1 - wick * rng.uniform(0.2, 1.0, len(closes)))
    volume = base_vol * rng.lognormal(0, 0.35, len(closes))
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes, "volume": volume.round()})


def _craft_bull_trigger(df: pd.DataFrame, ref: float, rng) -> None:
    """Overwrite last 5 bars: pullback into ref level, then a bullish engulfing."""
    n = len(df)
    start = df["close"].iloc[n - 6]
    path = np.linspace(start, ref * 1.0015, 4)
    for k, px in enumerate(path):
        i = n - 5 + k
        o = df["close"].iloc[i - 1]
        df.iloc[i, df.columns.get_loc("open")] = o
        df.iloc[i, df.columns.get_loc("close")] = px
        df.iloc[i, df.columns.get_loc("high")] = max(o, px) * 1.0008
        df.iloc[i, df.columns.get_loc("low")] = min(o, px) * 0.9992
        df.iloc[i, df.columns.get_loc("volume")] *= 0.7
    i = n - 1
    prev_o, prev_c = df["open"].iloc[i - 1], df["close"].iloc[i - 1]
    lo_body = min(prev_o, prev_c)
    o = lo_body * 0.9995
    c = max(prev_o, prev_c) * 1.004
    df.iloc[i, df.columns.get_loc("open")] = o
    df.iloc[i, df.columns.get_loc("close")] = c
    df.iloc[i, df.columns.get_loc("high")] = c * 1.0006
    df.iloc[i, df.columns.get_loc("low")] = o * 0.9993
    df.iloc[i, df.columns.get_loc("volume")] *= 2.3


def _craft_bear_trigger(df: pd.DataFrame, ref: float, rng) -> None:
    n = len(df)
    start = df["close"].iloc[n - 6]
    path = np.linspace(start, ref * 0.9985, 4)
    for k, px in enumerate(path):
        i = n - 5 + k
        o = df["close"].iloc[i - 1]
        df.iloc[i, df.columns.get_loc("open")] = o
        df.iloc[i, df.columns.get_loc("close")] = px
        df.iloc[i, df.columns.get_loc("high")] = max(o, px) * 1.0008
        df.iloc[i, df.columns.get_loc("low")] = min(o, px) * 0.9992
        df.iloc[i, df.columns.get_loc("volume")] *= 0.7
    i = n - 1
    prev_o, prev_c = df["open"].iloc[i - 1], df["close"].iloc[i - 1]
    o = max(prev_o, prev_c) * 1.0005
    c = min(prev_o, prev_c) * 0.996
    df.iloc[i, df.columns.get_loc("open")] = o
    df.iloc[i, df.columns.get_loc("close")] = c
    df.iloc[i, df.columns.get_loc("high")] = o * 1.0007
    df.iloc[i, df.columns.get_loc("low")] = c * 0.9994
    df.iloc[i, df.columns.get_loc("volume")] *= 2.3


class DemoProvider(DataProvider):
    """Synthetic, reproducible data. The simulated clock is 'today' at 12:30 IST
    on the most recent weekday (or the real time if a weekday session is live)."""

    name = "synthetic-test"
    is_demo = True

    def test_connection(self):
        return True, "synthetic test data"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        real = datetime.now(IST)
        o, c = _session_minutes(cfg["session"]["open"], cfg["session"]["close"])
        d = real
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        if d.date() == real.date() and o <= real.time() < c:
            self._now = real.replace(second=0, microsecond=0)
        else:
            self._now = datetime.combine(d.date(), time(12, 32), tzinfo=IST)
        self._cache: dict = {}

    def now(self) -> datetime:
        return self._now

    def get_instruments(self) -> pd.DataFrame:
        rows = [{"symbol": s, "exchange": "NSE", "segment": "EQ", "sector": sec,
                 "lot_size": 1, "tick_size": 0.05, "active": True, "base_price": px}
                for s, sec, px in DEMO_UNIVERSE]
        return pd.DataFrame(rows)

    def _scenario(self, symbol: str) -> str:
        return SCENARIOS[_seed(symbol, "scn") % len(SCENARIOS)]

    def _daily(self, symbol: str) -> pd.DataFrame:
        key = (symbol, "1D")
        if key in self._cache:
            return self._cache[key]
        base = {**{sy: px for sy, _, px in DEMO_UNIVERSE}, **INDEX_BASE}[symbol]
        rng = np.random.default_rng(_seed(symbol, "daily", self._now.date()))
        scn = self._scenario(symbol)
        n = 420
        drift = {"up_pullback": 0.0011, "up_trend": 0.0014, "down_rally": -0.0010,
                 "range": 0.0, "squeeze_break": 0.0004, "noise": 0.0002}[scn]
        rets = rng.normal(drift, 0.015, n)
        if scn == "range":
            rets[-150:] = rng.normal(0, 0.008, 150)
        closes = base * np.exp(np.cumsum(rets) - np.sum(rets))
        df = _build_bars(closes, rng, 0.012, 2.5e6 * 1000 / max(base, 50))
        days = pd.bdate_range(end=self._now.date(), periods=n + 1)[:-1]  # up to yesterday
        df.index = pd.DatetimeIndex(days).tz_localize(IST) + pd.Timedelta(hours=15, minutes=30)
        if scn == "up_pullback":
            ema20 = df["close"].ewm(span=20, adjust=False).mean().iloc[-6]
            _craft_bull_trigger(df, ema20, rng)
        elif scn == "down_rally":
            ema20 = df["close"].ewm(span=20, adjust=False).mean().iloc[-6]
            _craft_bear_trigger(df, ema20, rng)
        self._cache[key] = df
        return df

    def _intraday(self, symbol: str, timeframe: str) -> pd.DataFrame:
        key = (symbol, timeframe)
        if key in self._cache:
            return self._cache[key]
        step = TF_MINUTES.get(timeframe, 5)
        daily = self._daily(symbol)
        last_close = float(daily["close"].iloc[-1])
        rng = np.random.default_rng(_seed(symbol, "intra", timeframe, self._now.date()))
        scn = self._scenario(symbol)
        o, c = _session_minutes(self.cfg["session"]["open"], self.cfg["session"]["close"])
        sessions = pd.bdate_range(end=self._now.date(), periods=20)
        stamps = []
        for d in sessions:
            t = datetime.combine(d.date(), o, tzinfo=IST)
            end = datetime.combine(d.date(), c, tzinfo=IST)
            if d.date() == self._now.date():
                end = min(end, self._now)
            while t + timedelta(minutes=step) <= end + timedelta(minutes=step):
                if t >= end:
                    break
                stamps.append(t)
                t += timedelta(minutes=step)
        n = len(stamps)
        drift = {"up_pullback": 0.00025, "up_trend": 0.0003, "down_rally": -0.00025,
                 "range": 0.0, "squeeze_break": 0.00005, "noise": 0.0}[scn]
        sig = 0.0022 * np.sqrt(step / 5)
        rets = rng.normal(drift, sig, n)
        if scn == "squeeze_break":
            rets[-40:-1] = rng.normal(0, sig * 0.25, 39)
            rets[-1] = sig * 4
        closes = last_close * 0.99 * np.exp(np.cumsum(rets))
        df = _build_bars(closes, rng, sig * 0.8, 1.5e5 * 1000 / max(last_close, 50))
        df.index = pd.DatetimeIndex(stamps)
        # bar containing 'now' is still forming: keep it, the engine marks it incomplete
        today_mask = df.index.date == self._now.date()
        if today_mask.sum() > 12 and scn in ("up_pullback", "down_rally"):
            completed = df.iloc[:-1].copy()
            tp = (completed["high"] + completed["low"] + completed["close"]) / 3
            day = completed.index.date
            vwap = (tp * completed["volume"]).groupby(day).cumsum() / completed["volume"].groupby(day).cumsum()
            ref = float(vwap.iloc[-6])
            if scn == "up_pullback":
                _craft_bull_trigger(completed, ref, rng)
            else:
                _craft_bear_trigger(completed, ref, rng)
            df = pd.concat([completed, df.iloc[-1:]])
        if scn == "squeeze_break":
            df.iloc[-2, df.columns.get_loc("volume")] *= 2.5
        self._cache[key] = df
        return df

    def get_historical_ohlcv(self, symbol, timeframe, start=None, end=None) -> pd.DataFrame:
        df = self._daily(symbol) if timeframe in ("1D", "D") else self._intraday(symbol, timeframe)
        if start is not None:
            s = pd.Timestamp(start)
            df = df[df.index >= (s.tz_localize(IST) if s.tzinfo is None else s)]
        return df.copy()


