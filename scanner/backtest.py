"""Historical replay (requirements section 15).

Uses the exact same prepare_frame() + setup functions + decide() as the live
scanner, evaluated bar by bar on completed candles. Entry is a stop order on
the candles after the trigger; if stop and target are both touched in one
candle the configurable ambiguity rule decides (default: stop first).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .engine import decide, prepare_frame
from .models import BUY, SELL
from .setups import setups_for


def _simulate(df, i, sig, direction, target_key, entry_valid, max_hold, cost_bps, same_bar, intraday):
    p = sig["plan"]
    long = direction == "LONG"
    sign = 1 if long else -1
    entry, stop, target = p["entry"], p["stop"], p[target_key]
    rps = p["risk_per_share"]
    day = df.index[i].date()
    n = len(df)
    fill_j = None
    for j in range(i + 1, min(i + 1 + entry_valid, n)):
        if intraday and df.index[j].date() != day:
            break
        hi, lo, op = df["high"].iloc[j], df["low"].iloc[j], df["open"].iloc[j]
        if (long and hi >= entry) or (not long and lo <= entry):
            fill = max(op, entry) if long else min(op, entry)
            fill_j = j
            break
    if fill_j is None:
        return None
    exit_px, reason, k = None, None, fill_j
    for k in range(fill_j, min(fill_j + max_hold, n)):
        hi, lo = df["high"].iloc[k], df["low"].iloc[k]
        hit_sl = lo <= stop if long else hi >= stop
        hit_tg = hi >= target if long else lo <= target
        if hit_sl and hit_tg:
            exit_px, reason = (stop, "SL (same bar)") if same_bar == "stop_first" else (target, "Target (same bar)")
            break
        if hit_sl:
            exit_px, reason = (min(stop, df["open"].iloc[k]) if long else max(stop, df["open"].iloc[k])) if k > fill_j else stop, "SL"
            break
        if hit_tg:
            exit_px, reason = target, "Target"
            break
        if intraday and (k + 1 >= n or df.index[k + 1].date() != df.index[k].date()):
            exit_px, reason = df["close"].iloc[k], "Session end"
            break
    if exit_px is None:
        exit_px, reason = df["close"].iloc[k], "Time exit"
    cost_r = (fill + exit_px) * cost_bps / 10000 / rps
    r_mult = (exit_px - fill) * sign / rps - cost_r
    return {"signal_time": df.index[i], "fill_time": df.index[fill_j], "exit_time": df.index[k],
            "entry": round(fill, 2), "stop": stop, "target": target, "exit": round(float(exit_px), 2),
            "exit_reason": reason, "R": round(float(r_mult), 3), "bars_held": k - fill_j + 1, "score": sig["score"]}, k


def metrics(trades: pd.DataFrame, total_bars: int) -> dict:
    if trades.empty:
        return {"trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "avg_R": 0.0, "expectancy_R": 0.0,
                "profit_factor": 0.0, "max_drawdown_R": 0.0, "exposure_pct": 0.0, "trades_per_100_bars": 0.0}
    r = trades.sort_values("exit_time")["R"]
    eq = r.cumsum()
    dd = (eq - eq.cummax()).min()
    gains, losses = r[r > 0].sum(), -r[r <= 0].sum()
    return {"trades": int(len(r)), "wins": int((r > 0).sum()), "losses": int((r <= 0).sum()),
            "win_rate": round(float((r > 0).mean() * 100), 1), "avg_R": round(float(r.mean()), 3),
            "expectancy_R": round(float(r.mean()), 3),
            "profit_factor": round(float(gains / losses), 2) if losses > 0 else float("inf"),
            "max_drawdown_R": round(float(dd), 2), "exposure_pct": round(float(trades["bars_held"].sum()) / max(total_bars, 1) * 100, 1),
            "trades_per_100_bars": round(len(r) / max(total_bars, 1) * 100, 2)}


def run_backtest(provider, symbols, mode, setup_key, direction, cfg, target_key="t1", entry_valid=3,
                 max_hold=None, cost_bps=5.0, same_bar="stop_first", oos_frac=0.3, progress=None):
    tf = cfg["timeframes"]["intraday_primary"] if mode == "INTRADAY" else cfg["timeframes"]["swing_primary"]
    intraday = mode == "INTRADAY"
    max_hold = max_hold or (75 if intraday else 20)
    fn = {k: f for k, f, _ in setups_for(mode)}[setup_key]
    all_trades, total_bars = [], 0
    starts, ends = [], []
    for n_sym, sym in enumerate(symbols):
        if progress:
            progress((n_sym + 1) / len(symbols), sym)
        try:
            raw = provider.get_historical_ohlcv(sym, tf)
            if len(raw) < 60:
                continue
            # replay only completed candles
            last_done = raw.index[-1] + pd.Timedelta(minutes=5 if intraday else 0)
            if intraday and last_done > provider.now():
                raw = raw.iloc[:-1]
            df = prepare_frame(raw, cfg, mode)
        except Exception as exc:
            print(f"[backtest] {sym}: {exc!r}")
            continue
        warm = 60 if intraday else 210
        total_bars += max(len(df) - warm, 0)
        starts.append(df.index[min(warm, len(df) - 1)])
        ends.append(df.index[-1])
        i = warm
        while i < len(df) - 1:
            res = fn(df, i, cfg, direction)
            if res.stage != "TRIGGERED":
                i += 1
                continue
            sig = decide(df, i, cfg, mode, tf, only_setup=setup_key, only_direction=direction)
            if sig["decision"] not in (BUY, SELL) or not sig["plan"]:
                i += 1
                continue
            out = _simulate(df, i, sig, direction, target_key, entry_valid, max_hold, cost_bps, same_bar, intraday)
            if out is None:
                i += 1
                continue
            trade, exit_k = out
            trade["symbol"] = sym
            all_trades.append(trade)
            i = exit_k + 1  # one position per symbol at a time
    trades = pd.DataFrame(all_trades)
    if trades.empty:
        return trades, {"all": metrics(trades, total_bars)}, None
    lo, hi = min(starts), max(ends)
    split = lo + (hi - lo) * (1 - oos_frac)
    trades["sample"] = np.where(trades["signal_time"] < split, "In-sample", "Out-of-sample")
    trades = trades.sort_values("exit_time").reset_index(drop=True)
    trades["cum_R"] = trades["R"].cumsum()
    is_bars = total_bars * (1 - oos_frac)
    res = {"all": metrics(trades, total_bars),
           "in_sample": metrics(trades[trades["sample"] == "In-sample"], is_bars),
           "out_of_sample": metrics(trades[trades["sample"] == "Out-of-sample"], total_bars - is_bars)}
    return trades, res, split
