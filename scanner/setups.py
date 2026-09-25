"""Setup families (requirements sections 9 and 10).

Each setup is a function (df, i, cfg) -> SetupResult evaluated at bar i of a
frame that already holds indicators and pattern flags. The live scanner calls
it with i = last completed bar; the backtester calls it for every bar. Same
code path = same signals (FR-019, section 15).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .models import Rule, SetupResult
from .patterns import pattern_at

BULL_TRIGGERS = {"Hammer", "Bullish Engulfing", "Strong green candle", "Bullish Marubozu", "Morning Star"}
BEAR_TRIGGERS = {"Shooting Star", "Bearish Engulfing", "Strong red candle", "Bearish Marubozu", "Evening Star"}


def _g(code, passed, text, value=None, threshold=None):
    return Rule("Gate", code, bool(passed), text, None if value is None else float(value), threshold)


def _f(x):
    return f"{x:,.2f}" if x is not None and not pd.isna(x) else "n/a"


def _nan(*vals):
    return any(v is None or pd.isna(v) for v in vals)


def _finish(res: SetupResult, df, i, structure_ok: bool, allowed: set | None = None, need_text="trigger candle"):
    pats = [p for p in pattern_at(df, i, res.direction) if allowed is None or p.name in allowed]
    trig = pats[0] if pats else None
    res.gates.append(_g("TRIGGER_CANDLE", trig is not None,
                        f"Trigger: {trig.name}" if trig else f"No qualifying {need_text} on the last completed candle"))
    if res.veto:
        res.stage = "FAILED"
    elif structure_ok and trig:
        res.stage, res.trigger, res.trigger_index = "TRIGGERED", trig.name, i
    elif structure_ok:
        res.stage, res.missing = "PENDING", f"Waiting for a {need_text}"
    else:
        res.stage = "FAILED"
    return res


def _window(df, i, w):
    return df.iloc[max(0, i - w + 1): i + 1]


def _tol(cfg, atr_val):
    return cfg["setups"]["pullback_tolerance_atr"] * atr_val


# ---------------------------------------------------------------- intraday ---
def pivot_vwap(df, i, cfg, direction="LONG") -> SetupResult:
    r = df.iloc[i]
    long = direction == "LONG"
    res = SetupResult("Pivot + VWAP " + ("pullback" if long else "rally"), "INTRADAY", direction, "FAILED")
    if _nan(r.pp, r.vwap, r.atr):
        res.gates.append(_g("LEVELS_AVAILABLE", False, "Pivot / VWAP not available yet (need prior session)"))
        return res
    lo, hi = min(r.pp, r.vwap), max(r.pp, r.vwap)
    bias = (r.close > r.pp and r.close > r.vwap) if long else (r.close < r.pp and r.close < r.vwap)
    in_zone = lo <= r.close <= hi
    res.gates.append(_g("BIAS_PP_VWAP", bias,
                        f"Close {_f(r.close)} {'above' if long else 'below'} PP {_f(r.pp)} and VWAP {_f(r.vwap)}"
                        if bias else f"Close {_f(r.close)} is not {'above' if long else 'below'} both PP {_f(r.pp)} and VWAP {_f(r.vwap)}"))
    if in_zone:
        res.veto = "NO_TRADE_ZONE"
        res.gates.append(_g("NO_TRADE_ZONE", False, f"Price between PP and VWAP ({_f(lo)} - {_f(hi)}): no-trade zone"))
    w = _window(df, i, cfg["setups"]["pullback_window"])
    tol = _tol(cfg, r.atr)
    if long:
        touched = ((w["low"] <= w["vwap"] + tol) | (w["low"] <= w["pp"] + tol)).any()
        held = (w["close"] >= w["pp"]).all()
    else:
        touched = ((w["high"] >= w["vwap"] - tol) | (w["high"] >= w["pp"] - tol)).any()
        held = (w["close"] <= w["pp"]).all()
    res.gates.append(_g("PULLBACK_TO_VWAP_PP", touched,
                        f"{'Pullback' if long else 'Rally'} reached VWAP/PP zone within {len(w)} bars" if touched
                        else f"No {'pullback' if long else 'rally'} into VWAP/PP in the last {len(w)} bars"))
    res.gates.append(_g("HELD_PP", held, f"No close {'below' if long else 'above'} PP during the {'pullback' if long else 'rally'}"
                        if held else f"A close {'below' if long else 'above'} PP invalidated the {'pullback' if long else 'rally'}"))
    buf = cfg["setups"]["tick_buffer"]
    res.ref_level = r.vwap
    if long:
        res.structural_stop = min(r.pp, w["low"].min()) - buf
        res.target_level, res.target_level2 = r.r1, r.r2
    else:
        res.structural_stop = max(r.pp, w["high"].max()) + buf
        res.target_level, res.target_level2 = r.s1, r.s2
    return _finish(res, df, i, bias and touched and held, BULL_TRIGGERS if long else BEAR_TRIGGERS,
                   "bullish trigger candle (Hammer / Engulfing / strong green)" if long else
                   "bearish trigger candle (Shooting Star / Engulfing / strong red)")


def supertrend_ema20(df, i, cfg, direction="LONG") -> SetupResult:
    r = df.iloc[i]
    long = direction == "LONG"
    res = SetupResult("Supertrend + 20 EMA pullback", "INTRADAY", direction, "FAILED")
    if _nan(r.st, r.ema_trend, r.atr):
        res.gates.append(_g("WARMUP", False, "Supertrend / 20 EMA not warmed up"))
        return res
    st_ok = (r.st_dir == 1 and r.st < r.close) if long else (r.st_dir == -1 and r.st > r.close)
    ema_ok = r.close > r.ema_trend if long else r.close < r.ema_trend
    w = _window(df, i, cfg["setups"]["pullback_window"])
    tol = _tol(cfg, r.atr)
    if long:
        touched = ((w["low"] <= w["ema_trend"] + tol) | (w["low"] <= w["st"] + tol)).any()
    else:
        touched = ((w["high"] >= w["ema_trend"] - tol) | (w["high"] >= w["st"] - tol)).any()
    res.gates += [
        _g("SUPERTREND_STATE", st_ok, f"Supertrend(10,3) {'bullish, below' if long else 'bearish, above'} price at {_f(r.st)}"
           if st_ok else f"Supertrend is not {'bullish' if long else 'bearish'} (line {_f(r.st)})"),
        _g("PRICE_VS_EMA20", ema_ok, f"Close {'above' if long else 'below'} 20 EMA {_f(r.ema_trend)}"
           if ema_ok else f"Close on the wrong side of 20 EMA {_f(r.ema_trend)}"),
        _g("PULLBACK_TO_EMA_ST", touched, "Pullback touched 20 EMA / Supertrend zone" if touched
           else "No pullback into 20 EMA / Supertrend zone yet"),
    ]
    buf = cfg["setups"]["tick_buffer"]
    res.ref_level = r.ema_trend
    res.structural_stop = (r.low - buf) if long else (r.high + buf)
    res.target_level = r.swing_high if long else r.swing_low
    return _finish(res, df, i, st_ok and ema_ok and touched, BULL_TRIGGERS if long else BEAR_TRIGGERS,
                   "bullish trigger candle near the pullback zone" if long else "bearish trigger candle near the rally zone")


def ema_cross_supertrend(df, i, cfg, direction="LONG") -> SetupResult:
    r, p = df.iloc[i], df.iloc[i - 1]
    long = direction == "LONG"
    res = SetupResult("9/21 EMA cross + Supertrend", "INTRADAY", direction, "FAILED")
    if _nan(r.ema_fast, r.ema_slow, p.ema_fast, p.ema_slow, r.st):
        res.gates.append(_g("WARMUP", False, "9/21 EMA not warmed up"))
        return res
    cross = (r.ema_fast > r.ema_slow and p.ema_fast <= p.ema_slow) if long else (r.ema_fast < r.ema_slow and p.ema_fast >= p.ema_slow)
    aligned = r.ema_fast > r.ema_slow if long else r.ema_fast < r.ema_slow
    st_ok = r.st_dir == (1 if long else -1)
    res.gates += [
        _g("EMA_9_21_CROSS", cross, f"9 EMA crossed {'above' if long else 'below'} 21 EMA on this candle" if cross
           else ("9/21 already aligned, no fresh cross" if aligned else f"9 EMA is {'below' if long else 'above'} 21 EMA")),
        _g("SUPERTREND_STATE", st_ok, f"Supertrend {'bullish' if long else 'bearish'}" if st_ok
           else f"Supertrend not {'bullish' if long else 'bearish'}: Pine rule requires both"),
    ]
    buf = cfg["setups"]["tick_buffer"]
    w = _window(df, i, 3)
    res.ref_level = r.ema_slow
    res.structural_stop = (min(w["low"].min(), r.st) - buf) if long else (max(w["high"].max(), r.st) + buf)
    res.target_level = r.swing_high if long else r.swing_low
    ok = cross and st_ok
    res.gates.append(_g("TRIGGER_CANDLE", ok, "Crossover candle is the trigger; entry on break of its "
                        + ("high" if long else "low") if ok else "No crossover trigger"))
    if ok:
        res.stage, res.trigger, res.trigger_index = "TRIGGERED", "9/21 EMA crossover", i
    elif aligned and st_ok and not cross:
        res.stage = "FAILED"
    return res


def bb_squeeze(df, i, cfg, direction="LONG") -> SetupResult:
    r = df.iloc[i]
    s = cfg["setups"]
    res = SetupResult("Bollinger squeeze breakout", df.attrs.get("mode", "INTRADAY"), "LONG", "FAILED")
    hist = df["bb_width"].iloc[max(0, i - s["squeeze_lookback"]): i]
    if len(hist.dropna()) < 30 or _nan(r.bb_up, r.bb_mid):
        res.gates.append(_g("WARMUP", False, "Not enough bandwidth history"))
        return res
    pct = np.nanpercentile(hist, s["squeeze_percentile"])
    prior_bw = df["bb_width"].iloc[i - 1]
    squeeze = prior_bw <= pct
    cons = df["body_ratio"].iloc[max(0, i - 10): i].mean() < 0.55
    breakout = r.close > r.bb_up and r.close > r.open
    res.gates += [
        _g("BB_SQUEEZE", squeeze, f"Bandwidth {prior_bw:.4f} at/below {s['squeeze_percentile']}th percentile ({pct:.4f})"
           if squeeze else f"No squeeze: bandwidth {prior_bw:.4f} > {pct:.4f}", prior_bw, f"<= {pct:.4f}"),
        _g("CONSOLIDATION", cons, "Small overlapping candles before breakout" if cons else "Candles not consolidating"),
        _g("CLOSE_ABOVE_UPPER_BAND", breakout, f"Close {_f(r.close)} above upper band {_f(r.bb_up)}" if breakout
           else f"Close {_f(r.close)} still inside the bands (upper {_f(r.bb_up)})"),
    ]
    res.ref_level = r.bb_up
    res.structural_stop = r.bb_mid - s["tick_buffer"]
    res.target_level = None
    if squeeze and breakout and r.body_ratio >= s["strong_candle_body_ratio"]:
        res.gates.append(_g("TRIGGER_CANDLE", True, "Strong green breakout candle"))
        res.stage, res.trigger, res.trigger_index = "TRIGGERED", "Strong green breakout", i
    elif squeeze and cons:
        res.gates.append(_g("TRIGGER_CANDLE", False, "Waiting for a strong close above the upper band"))
        res.stage, res.missing = "PENDING", "Waiting for a strong green close above the upper band"
    return res


# ------------------------------------------------------------------- swing ---
def ma_hierarchy(df, i, cfg, direction="LONG") -> SetupResult:
    r = df.iloc[i]
    long = direction == "LONG"
    res = SetupResult("20/50/200 trend pullback", "SWING", direction, "FAILED")
    if _nan(r.sma_long, r.sma_mid, r.ema_trend, r.atr):
        res.gates.append(_g("WARMUP", False, "200 SMA needs at least 200 daily bars"))
        return res
    hier = (r.ema_trend > r.sma_mid > r.sma_long) if long else (r.ema_trend < r.sma_mid < r.sma_long)
    w = _window(df, i, cfg["setups"]["pullback_window"])
    tol = _tol(cfg, r.atr)
    if long:
        touched = ((w["low"] <= w["ema_trend"] + tol) | (w["low"] <= w["sma_mid"] + tol)).any()
        intact = r.close > r.sma_mid
    else:
        touched = ((w["high"] >= w["ema_trend"] - tol) | (w["high"] >= w["sma_mid"] - tol)).any()
        intact = r.close < r.sma_mid
    res.gates += [
        _g("MA_HIERARCHY", hier, ("20 EMA > 50 SMA > 200 SMA" if long else "20 EMA < 50 SMA < 200 SMA")
           + f" ({_f(r.ema_trend)} / {_f(r.sma_mid)} / {_f(r.sma_long)})" if hier else
           f"MA hierarchy not {'bullish' if long else 'bearish'} ({_f(r.ema_trend)} / {_f(r.sma_mid)} / {_f(r.sma_long)})"),
        _g("PULLBACK_TO_MA", touched, "Pullback reached 20 EMA / 50 SMA" if touched else "No pullback to 20 EMA / 50 SMA yet"),
        _g("HELD_50SMA", intact, f"Close holds {'above' if long else 'below'} 50 SMA" if intact else "Close broke through 50 SMA"),
    ]
    buf = cfg["setups"]["tick_buffer"]
    res.ref_level = r.ema_trend
    res.structural_stop = (w["low"].min() - buf) if long else (w["high"].max() + buf)
    res.target_level = r.swing_high if long else r.swing_low
    return _finish(res, df, i, hier and touched and intact, BULL_TRIGGERS if long else BEAR_TRIGGERS,
                   "bullish confirmation candle near the MA" if long else "bearish confirmation candle near the MA")


def _recent_swing_lows(df, i, window, n):
    flags = df["is_swing_low"].iloc[max(0, i - window): i + 1]
    return [df.index.get_loc(t) - n for t in flags[flags].index]


def _divergence(df, i, cfg, osc: str, label: str) -> SetupResult:
    r = df.iloc[i]
    n = cfg["indicators"]["swing_lookback"]
    res = SetupResult(f"{label} bullish divergence", "SWING", "LONG", "FAILED", reversal=True)
    lows = _recent_swing_lows(df, i, cfg["setups"]["divergence_window"], n)
    if len(lows) < 2:
        res.gates.append(_g("TWO_SWING_LOWS", False, f"Need two confirmed swing lows in {cfg['setups']['divergence_window']} bars"))
        return res
    k1, k2 = lows[-2], lows[-1]
    p1, p2 = df["low"].iloc[k1], df["low"].iloc[k2]
    o1, o2 = df[osc].iloc[k1], df[osc].iloc[k2]
    price_ll = p2 < p1
    osc_hl = o2 > o1
    recent = i - k2 <= 3 * n + 4
    res.gates += [
        _g("PRICE_LOWER_LOW", price_ll, f"Price lower low {_f(p2)} < {_f(p1)}" if price_ll else f"No lower low ({_f(p2)} vs {_f(p1)})"),
        _g(f"{osc.upper()}_HIGHER_LOW", osc_hl, f"{label} higher low {o2:.2f} > {o1:.2f}" if osc_hl else f"{label} did not make a higher low"),
        _g("DIVERGENCE_RECENT", recent, f"Second low {i - k2} bars ago" if recent else "Divergence too old"),
    ]
    buf = cfg["setups"]["tick_buffer"]
    res.ref_level = p2
    res.structural_stop = p2 - buf
    res.target_level = df["high"].iloc[k1:k2 + 1].max()
    return _finish(res, df, i, price_ll and osc_hl and recent, {"Hammer", "Bullish Engulfing", "Strong green candle", "Morning Star"},
                   "Hammer / Bullish Engulfing / strong green confirmation")


def rsi_divergence(df, i, cfg, direction="LONG"):
    return _divergence(df, i, cfg, "rsi", "RSI")


def macd_divergence(df, i, cfg, direction="LONG"):
    return _divergence(df, i, cfg, "macd", "MACD")


def bb_contra(df, i, cfg, direction="LONG") -> SetupResult:
    r = df.iloc[i]
    res = SetupResult("Bollinger contra (mean reversion)", "SWING", "LONG", "FAILED", reversal=True)
    if _nan(r.sma_long, r.bb_lo, r.sma_long_slope):
        res.gates.append(_g("WARMUP", False, "200 SMA / Bollinger not warmed up"))
        return res
    trend = r.close > r.sma_long and r.sma_long_slope > 0
    w = _window(df, i, 3)
    pierce = (w["low"] <= w["bb_lo"]).any()
    res.gates += [
        _g("ABOVE_RISING_200", trend, f"Above rising 200 SMA {_f(r.sma_long)}" if trend else "Not above a rising 200 SMA"),
        _g("LOWER_BAND_TEST", pierce, "Price tested the lower band in the last 3 bars" if pierce else "No lower-band test"),
    ]
    res.ref_level = r.bb_lo
    res.structural_stop = w["low"].min() - cfg["setups"]["tick_buffer"]
    res.target_level = r.bb_mid
    allowed = {"Hammer", "Bullish Engulfing", "Morning Star", "Strong green candle"}
    return _finish(res, df, i, trend and pierce, allowed, "reversal candle (long lower wick / engulfing)")


def gap_retest(df, i, cfg, direction="LONG") -> SetupResult:
    s = cfg["setups"]
    res = SetupResult("Breakaway gap retest", "SWING", "LONG", "FAILED")
    lo_i = max(25, i - s["gap_retest_max_bars"])
    gaps = [g for g in range(lo_i, i - 1) if df["gap_pct"].iloc[g] >= s["gap_min_pct"]]
    if not gaps:
        res.gates.append(_g("BREAKAWAY_GAP", False, f"No gap up >= {s['gap_min_pct']}% in the last {s['gap_retest_max_bars']} sessions"))
        return res
    g = gaps[-1]
    base = df.iloc[g - 20:g]
    base_ok = (base["high"].max() / base["low"].min() - 1) * 100 <= 12
    gap_top = df["low"].iloc[g]
    gap_bot = df["high"].iloc[g - 1]
    if gap_top <= gap_bot:
        gap_top, gap_bot = df["open"].iloc[g], df["close"].iloc[g - 1]
    mid = (gap_top + gap_bot) / 2
    w = _window(df, i, 3)
    retest = (w["low"] <= gap_top).any() and df["close"].iloc[i] >= mid
    vol_dry = df["volume"].iloc[g + 1:i + 1].mean() < df["volume"].iloc[g]
    res.gates += [
        _g("BREAKAWAY_GAP", True, f"Gap up {df['gap_pct'].iloc[g]:.1f}% on {df.index[g]:%d %b}"),
        _g("BASE_BEFORE_GAP", base_ok, "Gap left a multi-week base" if base_ok else "No tight base before the gap"),
        _g("RETEST_UPPER_HALF", retest, f"Retest into gap zone {_f(gap_bot)} - {_f(gap_top)} holding upper half"
           if retest else "Price has not retested the upper half of the gap"),
        _g("PULLBACK_VOLUME_LOWER", vol_dry, "Pullback volume below gap-day volume" if vol_dry else "Pullback volume is heavy"),
    ]
    res.ref_level = gap_top
    res.structural_stop = df["low"].iloc[g] - s["tick_buffer"]
    res.target_level = df["high"].iloc[g:i + 1].max()
    return _finish(res, df, i, base_ok and retest, None, "bullish reversal candle in the gap zone")


def fib_pullback(df, i, cfg, direction="LONG") -> SetupResult:
    r = df.iloc[i]
    res = SetupResult("Fibonacci pullback", "SWING", "LONG", "FAILED")
    if _nan(r.swing_high, r.swing_low, r.swing_high_pos, r.swing_low_pos, r.atr, r.ema_trend):
        res.gates.append(_g("SWINGS", False, "No validated swing high/low yet"))
        return res
    up_leg = r.swing_low_pos < r.swing_high_pos and r.swing_high > r.swing_low
    span = r.swing_high - r.swing_low
    f382, f50, f618, f786 = (r.swing_high - span * x for x in (0.382, 0.5, 0.618, 0.786))
    in_zone = up_leg and f618 - 0.25 * r.atr <= r.low <= f382 + 0.25 * r.atr
    confl = [lvl for lvl, name in ((r.ema_trend, "20 EMA"), (r.sma_mid, "50 SMA"))
             if not pd.isna(lvl) and f618 - 0.5 * r.atr <= lvl <= f382 + 0.5 * r.atr]
    res.gates += [
        _g("UP_LEG", up_leg, f"Up-leg {_f(r.swing_low)} -> {_f(r.swing_high)}" if up_leg else "Latest swing structure is not an up-leg"),
        _g("IN_FIB_ZONE", in_zone, f"Low {_f(r.low)} inside 38.2-61.8% zone ({_f(f618)} - {_f(f382)})" if in_zone
           else "Price not in the 38.2-61.8% zone"),
        _g("FIB_CONFLUENCE", bool(confl), "Zone overlaps a moving average" if confl else "No MA overlap: Fibonacci alone is not enough"),
    ]
    res.ref_level = f50
    res.structural_stop = f786 - cfg["setups"]["tick_buffer"]
    res.target_level = r.swing_high
    return _finish(res, df, i, up_leg and in_zone and bool(confl), BULL_TRIGGERS, "price-action confirmation candle")


INTRADAY_SETUPS = [
    ("pivot_vwap", pivot_vwap, ["LONG", "SHORT"]),
    ("st_ema20", supertrend_ema20, ["LONG", "SHORT"]),
    ("ema_cross_st", ema_cross_supertrend, ["LONG", "SHORT"]),
    ("bb_squeeze", bb_squeeze, ["LONG"]),
]
SWING_SETUPS = [
    ("ma_hierarchy", ma_hierarchy, ["LONG", "SHORT"]),
    ("rsi_div", rsi_divergence, ["LONG"]),
    ("macd_div", macd_divergence, ["LONG"]),
    ("bb_contra", bb_contra, ["LONG"]),
    ("gap_retest", gap_retest, ["LONG"]),
    ("fib_pullback", fib_pullback, ["LONG"]),
    ("bb_squeeze", bb_squeeze, ["LONG"]),
]
SETUP_LABELS = {
    "pivot_vwap": "Pivot + VWAP", "st_ema20": "Supertrend + 20 EMA", "ema_cross_st": "9/21 EMA + Supertrend",
    "bb_squeeze": "BB squeeze breakout", "ma_hierarchy": "20/50/200 trend pullback", "rsi_div": "RSI divergence",
    "macd_div": "MACD divergence", "bb_contra": "BB contra", "gap_retest": "Gap retest", "fib_pullback": "Fibonacci pullback",
}


def setups_for(mode: str):
    return INTRADAY_SETUPS if mode == "INTRADAY" else SWING_SETUPS
