"""Indicator engine (requirements section 6).

Every column is causal: the value at bar i only uses bars <= i. That is what
lets the live scanner and the backtester share one calculation (no look-ahead).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def atr(df: pd.DataFrame, n: int) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev_close).abs(),
                    (df["low"] - prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()  # Wilder


def rsi(s: pd.Series, n: int) -> pd.Series:
    delta = s.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100.0).where(gain.notna())


def macd(s: pd.Series, fast: int, slow: int, signal: int):
    line = s.ewm(span=fast, adjust=False).mean() - s.ewm(span=slow, adjust=False).mean()
    sig = line.ewm(span=signal, adjust=False).mean()
    return line, sig, line - sig


def supertrend(df: pd.DataFrame, period: int, mult: float):
    """Returns (supertrend line, direction) with direction +1 bullish / -1 bearish."""
    a = atr(df, period).to_numpy()
    hl2 = ((df["high"] + df["low"]) / 2).to_numpy()
    close = df["close"].to_numpy()
    n = len(df)
    upper = hl2 + mult * a
    lower = hl2 - mult * a
    fu, fl = upper.copy(), lower.copy()
    st = np.full(n, np.nan)
    direction = np.zeros(n)
    for i in range(n):
        if np.isnan(a[i]):
            continue
        if i == 0 or np.isnan(st[i - 1]):
            direction[i] = 1
            st[i] = fl[i]
            continue
        fu[i] = upper[i] if (upper[i] < fu[i - 1] or close[i - 1] > fu[i - 1]) else fu[i - 1]
        fl[i] = lower[i] if (lower[i] > fl[i - 1] or close[i - 1] < fl[i - 1]) else fl[i - 1]
        if direction[i - 1] == 1:
            direction[i] = -1 if close[i] < fl[i] else 1
        else:
            direction[i] = 1 if close[i] > fu[i] else -1
        st[i] = fl[i] if direction[i] == 1 else fu[i]
    return pd.Series(st, index=df.index), pd.Series(direction, index=df.index)


def session_vwap(df: pd.DataFrame) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    day = df.index.date
    pv = (tp * df["volume"]).groupby(day).cumsum()
    vol = df["volume"].groupby(day).cumsum()
    return pv / vol.replace(0, np.nan)


def pivots(df: pd.DataFrame, intraday: bool) -> pd.DataFrame:
    """Classic floor pivots from the previous session (intraday) or previous bar (daily)."""
    if intraday:
        day = pd.Series(df.index.date, index=df.index)
        g = df.groupby(day.values).agg(h=("high", "max"), l=("low", "min"), c=("close", "last")).shift(1)
        prev = g.reindex(day.values)
        prev.index = df.index
    else:
        prev = pd.DataFrame({"h": df["high"].shift(1), "l": df["low"].shift(1), "c": df["close"].shift(1)})
    pp = (prev["h"] + prev["l"] + prev["c"]) / 3
    rng = prev["h"] - prev["l"]
    return pd.DataFrame({"pp": pp, "r1": 2 * pp - prev["l"], "s1": 2 * pp - prev["h"],
                         "r2": pp + rng, "s2": pp - rng}, index=df.index)


def confirmed_swings(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """Swing high at k is only known at k+n. Columns carry the latest confirmed
    swing value and its bar position, as known at each bar."""
    win = 2 * n + 1
    is_sh = df["high"] == df["high"].rolling(win, center=True).max()
    is_sl = df["low"] == df["low"].rolling(win, center=True).min()
    pos = pd.Series(np.arange(len(df)), index=df.index, dtype=float)
    sh_val = df["high"].where(is_sh).shift(n).ffill()
    sl_val = df["low"].where(is_sl).shift(n).ffill()
    sh_pos = pos.where(is_sh).shift(n).ffill()
    sl_pos = pos.where(is_sl).shift(n).ffill()
    return pd.DataFrame({"swing_high": sh_val, "swing_low": sl_val,
                         "swing_high_pos": sh_pos, "swing_low_pos": sl_pos,
                         "is_swing_high": is_sh.shift(n, fill_value=False),
                         "is_swing_low": is_sl.shift(n, fill_value=False)}, index=df.index)


def compute_indicators(df: pd.DataFrame, cfg: dict, intraday: bool) -> pd.DataFrame:
    p = cfg["indicators"]
    out = df.copy()
    c = out["close"]
    out["ema_fast"] = ema(c, p["ema_fast"])
    out["ema_slow"] = ema(c, p["ema_slow"])
    out["ema_trend"] = ema(c, p["ema_trend"])
    out["sma_mid"] = sma(c, p["sma_mid"])
    out["sma_long"] = sma(c, p["sma_long"])
    out["atr"] = atr(out, p["atr_period"])
    out["rsi"] = rsi(c, p["rsi_period"])
    out["macd"], out["macd_signal"], out["macd_hist"] = macd(c, p["macd_fast"], p["macd_slow"], p["macd_signal"])
    out["st"], out["st_dir"] = supertrend(out, p["supertrend_period"], p["supertrend_multiplier"])
    mid = sma(c, p["bb_period"])
    sd = c.rolling(p["bb_period"], min_periods=p["bb_period"]).std(ddof=0)
    out["bb_mid"], out["bb_up"], out["bb_lo"] = mid, mid + p["bb_std"] * sd, mid - p["bb_std"] * sd
    out["bb_width"] = (out["bb_up"] - out["bb_lo"]) / mid
    out["vwap"] = session_vwap(out) if intraday else np.nan
    out = out.join(pivots(out, intraday))
    out = out.join(confirmed_swings(out, p["swing_lookback"]))
    out["relvol"] = out["volume"] / out["volume"].rolling(p["relvol_lookback"]).mean().shift(1)
    # derived features (6.1)
    rng = (out["high"] - out["low"]).replace(0, np.nan)
    out["body_ratio"] = (out["close"] - out["open"]).abs() / rng
    out["upper_wick"] = (out["high"] - out[["open", "close"]].max(axis=1)) / rng
    out["lower_wick"] = (out[["open", "close"]].min(axis=1) - out["low"]) / rng
    out["atr_ratio_pct"] = out["atr"] / c * 100
    out["ema_trend_slope"] = out["ema_trend"].pct_change(5) * 100
    out["sma_long_slope"] = out["sma_long"].pct_change(10) * 100
    out["gap_pct"] = (out["open"] / out["close"].shift(1) - 1) * 100
    return out


def fib_levels(swing_low: float, swing_high: float) -> dict:
    span = swing_high - swing_low
    return {f"{r:.1f}%": swing_high - span * r / 100 for r in (23.6, 38.2, 50.0, 61.8, 78.6)}
