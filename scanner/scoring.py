"""Regime, data quality, risk plan and the 100-point confluence score
(requirements sections 5.1, 8, 11, 12)."""
from __future__ import annotations

import math
from datetime import datetime, time, timedelta

import numpy as np
import pandas as pd

from .models import CATEGORY_WEIGHTS, Rule, SetupResult
from .providers import IST, TF_MINUTES


# ------------------------------------------------------------ data quality ---
def data_quality(df: pd.DataFrame, cfg: dict, mode: str, timeframe: str, now: datetime) -> dict:
    flags, rules = [], []
    d = cfg["data"]
    if df.empty:
        return {"ok": False, "flags": ["NO_DATA"], "rules": [Rule("Data", "NO_DATA", False, "Provider returned no candles")],
                "last_ts": None, "age_min": None, "complete_last": False, "missing": 0}
    dup = int(df.index.duplicated().sum())
    bad_ohlc = int(((df["high"] < df[["open", "close"]].max(axis=1) - 1e-9) |
                    (df["low"] > df[["open", "close"]].min(axis=1) + 1e-9)).sum())
    neg_vol = int((df["volume"] < 0).sum())
    min_bars = d["min_bars_intraday"] if mode == "INTRADAY" else d["min_bars_swing"]
    rules.append(Rule("Data", "NO_DUPLICATES", dup == 0, f"{dup} duplicate timestamps", dup, "0"))
    rules.append(Rule("Data", "OHLC_SANITY", bad_ohlc == 0, f"{bad_ohlc} candles fail High/Low sanity", bad_ohlc, "0"))
    rules.append(Rule("Data", "VOLUME_NON_NEGATIVE", neg_vol == 0, f"{neg_vol} negative volume rows", neg_vol, "0"))
    rules.append(Rule("Data", "WARMUP_BARS", len(df) >= min_bars, f"{len(df)} bars available", len(df), f">= {min_bars}"))

    last_ts = df.index[-1]
    missing = 0
    if mode == "INTRADAY":
        step = TF_MINUTES.get(timeframe, 5)
        complete_last = last_ts + timedelta(minutes=step) <= now
        gaps = df.index.to_series().diff().dt.total_seconds().div(60)
        same_day = df.index.to_series().dt.date == df.index.to_series().shift(1).dt.date
        missing = int(((gaps > step * 1.5) & same_day).sum())
        last_done = last_ts if complete_last else (df.index[-2] if len(df) > 1 else last_ts)
        age = (now - (last_done + timedelta(minutes=step))).total_seconds() / 60
        fresh = -step <= age <= d["max_staleness_minutes_intraday"]
        o = datetime.combine(now.date(), time.fromisoformat(cfg["session"]["open"]), tzinfo=IST)
        c = datetime.combine(now.date(), time.fromisoformat(cfg["session"]["close"]), tzinfo=IST)
        session_live = now.weekday() < 5 and o <= now <= c
        if age < -step:
            txt = f"Candle timestamps are {-age:.0f} min in the future: check the PC clock or the file's timezone"
        elif not fresh and not session_live:
            txt = f"Market closed: last candle {last_done:%d %b %H:%M}. Intraday signals are review-only until the next session"
        else:
            txt = f"Last completed candle is {age:.0f} min old"
        rules.append(Rule("Data", "FRESHNESS", fresh, txt, round(age, 1),
                          f"<= {d['max_staleness_minutes_intraday']} min"))
    else:
        close_t = time.fromisoformat(cfg["session"]["close"])
        complete_last = last_ts.date() < now.date() or now.time() >= close_t
        bdays = int(np.busday_count(last_ts.date(), now.date()))
        age = bdays * 1440.0
        fresh = bdays <= d["max_staleness_days_swing"]
        rules.append(Rule("Data", "FRESHNESS", fresh, f"Last daily candle is {bdays} trading day(s) old", bdays,
                          f"<= {d['max_staleness_days_swing']} days"))
    rules.append(Rule("Data", "NO_MISSING_CANDLES", missing == 0, f"{missing} intraday gaps detected", missing, "0"))
    for r in rules:
        if not r.passed:
            flags.append(r.code)
    ok = not any(c in flags for c in ("NO_DUPLICATES", "OHLC_SANITY", "VOLUME_NON_NEGATIVE", "WARMUP_BARS", "FRESHNESS"))
    return {"ok": ok, "flags": flags, "rules": rules, "last_ts": last_ts, "age_min": age,
            "complete_last": bool(complete_last), "missing": missing}


# ------------------------------------------------------------------ regime ---
def classify_regime(df: pd.DataFrame, i: int) -> dict:
    r = df.iloc[i]
    if any(pd.isna(r[c]) for c in ("ema_trend", "sma_mid", "sma_long", "atr")):
        return {"trend": "INSUFFICIENT_DATA", "vol": "NORMAL", "label": "INSUFFICIENT_DATA"}
    if r.ema_trend > r.sma_mid > r.sma_long and r.ema_trend_slope > 0:
        trend = "TREND_UP"
    elif r.ema_trend < r.sma_mid < r.sma_long and r.ema_trend_slope < 0:
        trend = "TREND_DOWN"
    else:
        trend = "RANGE"
    atr_hist = df["atr_ratio_pct"].iloc[max(0, i - 100): i + 1].dropna()
    bw_hist = df["bb_width"].iloc[max(0, i - 100): i + 1].dropna()
    vol = "NORMAL"
    if len(atr_hist) > 20 and r.atr_ratio_pct >= np.percentile(atr_hist, 90):
        vol = "HIGH_VOLATILITY"
    elif len(bw_hist) > 20 and r.bb_width <= np.percentile(bw_hist, 10):
        vol = "LOW_VOLATILITY"
    return {"trend": trend, "vol": vol, "label": trend if vol == "NORMAL" else f"{trend} · {vol}"}


# --------------------------------------------------------------- risk plan ---
def risk_plan(setup: SetupResult, df: pd.DataFrame, i: int, cfg: dict, lot_size: int = 1) -> dict:
    r = df.iloc[i]
    s, rk = cfg["setups"], cfg["risk"]
    long = setup.direction == "LONG"
    buf = s["tick_buffer"]
    entry = (r.high + buf) if long else (r.low - buf)
    stop = setup.structural_stop
    min_dist = s["min_stop_atr"] * (r.atr if not pd.isna(r.atr) else 0)
    if stop is None or pd.isna(stop):
        stop = entry - min_dist if long else entry + min_dist
    # use the wider of structural and minimum ATR distance
    stop = min(stop, entry - min_dist) if long else max(stop, entry + min_dist)
    risk_ps = abs(entry - stop)
    valid = risk_ps > 0 and ((stop < entry) if long else (stop > entry))
    sign = 1 if long else -1
    pref = cfg["scoring"]["preferred_rr"]
    t1 = setup.target_level
    if t1 is None or pd.isna(t1) or (t1 - entry) * sign <= 0:
        t1 = entry + sign * pref * risk_ps
    t2_r = entry + sign * rk["target2_r"] * risk_ps
    t2 = setup.target_level2
    t2 = t2_r if t2 is None or pd.isna(t2) else (max(t2, t2_r) if long else min(t2, t2_r))
    if (t2 - t1) * sign <= 0:
        t2 = t1 + sign * risk_ps
    rr1 = abs(t1 - entry) / risk_ps if valid else 0.0
    rr2 = abs(t2 - entry) / risk_ps if valid else 0.0
    capital, risk_pct = float(rk["capital"]), float(rk["risk_percent"])
    budget = capital * risk_pct / 100
    raw_qty = math.floor(budget / risk_ps) if valid else 0
    max_cap = math.floor(capital / entry) if entry > 0 else 0
    qty = min(raw_qty, max_cap)
    qty = (qty // max(lot_size, 1)) * max(lot_size, 1)
    return {"entry": round(entry, 2), "stop": round(stop, 2), "t1": round(t1, 2), "t2": round(t2, 2),
            "rr1": round(rr1, 2), "rr2": round(rr2, 2), "risk_per_share": round(risk_ps, 2),
            "qty": int(qty), "risk_amount": round(qty * risk_ps, 2), "risk_budget": round(budget, 2),
            "reward_t1": round(qty * abs(t1 - entry), 2), "reward_t2": round(qty * abs(t2 - entry), 2),
            "capital_used": round(qty * entry, 2), "valid": bool(valid), "capped_by_capital": raw_qty > max_cap}


# ----------------------------------------------------------------- scoring ---
def _rule(cat, code, got, mx, text, value=None, threshold=None):
    return Rule(cat, code, got >= mx * 0.999 and mx > 0, text, None if value is None else float(value), threshold,
                round(float(got), 1), float(mx))


def score_confluence(df, i, setup: SetupResult, plan: dict, regime: dict, cfg: dict, mode: str) -> list:
    r = df.iloc[i]
    sc = cfg["scoring"]
    long = setup.direction == "LONG"
    up = 1 if long else -1
    rules = []
    side = "above" if long else "below"

    # Trend 25
    if mode == "INTRADAY":
        v = not pd.isna(r.vwap) and (r.close - r.vwap) * up > 0
        p = not pd.isna(r.pp) and (r.close - r.pp) * up > 0
        rules.append(_rule("Trend", "VWAP_BIAS", 7 if v else 0, 7, f"Close {side} VWAP" if v else f"Close not {side} VWAP"))
        rules.append(_rule("Trend", "PIVOT_BIAS", 6 if p else 0, 6, f"Close {side} Pivot PP" if p else f"Close not {side} Pivot PP"))
    else:
        h = (r.ema_trend - r.sma_mid) * up > 0 and (r.sma_mid - r.sma_long) * up > 0
        a = (r.close - r.sma_long) * up > 0
        rules.append(_rule("Trend", "MA_HIERARCHY", 7 if h else 0, 7, "20/50/200 hierarchy aligned" if h else "20/50/200 hierarchy not aligned"))
        rules.append(_rule("Trend", "VS_200SMA", 6 if a else 0, 6, f"Close {side} 200 SMA" if a else f"Close not {side} 200 SMA"))
    st_ok = r.st_dir == up
    rules.append(_rule("Trend", "SUPERTREND", 6 if st_ok else 0, 6, "Supertrend agrees" if st_ok else "Supertrend disagrees"))
    reg_ok = regime["trend"] == ("TREND_UP" if long else "TREND_DOWN")
    reg_pts = 6 if reg_ok else (3 if regime["trend"] == "RANGE" or setup.reversal else 0)
    rules.append(_rule("Trend", "REGIME_ALIGNMENT", reg_pts, 6, f"Regime {regime['trend']}"))

    # Momentum 15
    x = (r.ema_fast - r.ema_slow) * up > 0
    rules.append(_rule("Momentum", "EMA_9_21_STATE", 5 if x else 0, 5, "9 EMA on the right side of 21 EMA" if x else "9/21 EMA against the setup"))
    rv = r.rsi
    if setup.reversal:
        turning = rv > df["rsi"].iloc[i - 1]
        pts = 5 if turning else 0
        txt = f"RSI {rv:.1f} turning up" if turning else f"RSI {rv:.1f} still falling"
    elif long:
        pts = 5 if 50 <= rv <= 70 else (2 if 45 <= rv < 50 or 70 < rv <= 78 else 0)
        txt = f"RSI {rv:.1f} (ideal 50-70)"
    else:
        pts = 5 if 30 <= rv <= 50 else (2 if 50 < rv <= 55 or 22 <= rv < 30 else 0)
        txt = f"RSI {rv:.1f} (ideal 30-50)"
    rules.append(_rule("Momentum", "RSI_REGIME", pts, 5, txt, rv, "50-70" if long else "30-50"))
    hist, prev = r.macd_hist, df["macd_hist"].iloc[i - 1]
    m = 5 if hist * up > 0 and (hist - prev) * up > 0 else (3 if hist * up > 0 or (hist - prev) * up > 0 else 0)
    rules.append(_rule("Momentum", "MACD_STATE", m, 5, f"MACD histogram {hist:+.3f}, {'rising' if hist > prev else 'falling'}", hist))

    # Price action 20
    trig_pts = 12 if setup.stage == "TRIGGERED" else 0
    rules.append(_rule("Price action", "TRIGGER", trig_pts, 12, f"Trigger confirmed: {setup.trigger}" if trig_pts else (setup.missing or "No trigger")))
    struct = [g for g in setup.gates if g.code != "TRIGGER_CANDLE"]
    frac = sum(g.passed for g in struct) / len(struct) if struct else 0
    rules.append(_rule("Price action", "SETUP_STRUCTURE", 8 * frac, 8, f"{sum(g.passed for g in struct)}/{len(struct)} setup structure gates pass"))

    # Volume 10
    rvol = r.relvol if not pd.isna(r.relvol) else 0
    vp = 10 if rvol >= sc["relvol_threshold"] else (5 if rvol >= 1.0 else 0)
    rules.append(_rule("Volume", "RELATIVE_VOLUME", vp, 10, f"Relative volume {rvol:.2f}x", rvol, f">= {sc['relvol_threshold']}x"))

    # S&R / location 15
    ref = setup.ref_level
    near = ref is not None and not pd.isna(ref) and not pd.isna(r.atr) and abs(r.close - ref) <= 1.0 * r.atr
    rules.append(_rule("S&R / location", "NEAR_KEY_LEVEL", 8 if near else 0, 8,
                       f"Within 1 ATR of setup level {ref:,.2f}" if near else "Extended away from the setup level"))
    entry, rps = plan["entry"], plan["risk_per_share"] or 1e-9
    levels = [r.r1, r.r2, r.swing_high, r.bb_up] if long else [r.s1, r.s2, r.swing_low, r.bb_lo]
    blockers = [lv for lv in levels if not pd.isna(lv) and (lv - entry) * up > 0.1 * rps]
    room = min(abs(lv - entry) for lv in blockers) / rps if blockers else 9.9
    rp = 7 if room >= sc["preferred_rr"] else (4 if room >= 1 else 0)
    rules.append(_rule("S&R / location", "ROOM_TO_NEXT_LEVEL", rp, 7, f"Next level is {room:.1f}R away" if blockers
                       else "No overhead level nearby", round(room, 2), f">= {sc['preferred_rr']}R"))

    # Volatility 5
    ar = r.atr_ratio_pct
    ok = sc["atr_ratio_min_pct"] <= ar <= sc["atr_ratio_max_pct"]
    rules.append(_rule("Volatility", "ATR_HEALTHY", 5 if ok else 0, 5, f"ATR is {ar:.2f}% of price", ar,
                       f"{sc['atr_ratio_min_pct']}-{sc['atr_ratio_max_pct']}%"))

    # Risk / reward 10
    rr = plan["rr1"]
    rrp = 10 if rr >= sc["preferred_rr"] else (6 if rr >= sc["min_rr"] else 0)
    rules.append(_rule("Risk / reward", "RR_T1", rrp, 10, f"R:R to T1 is 1:{rr:.2f}", rr, f">= {sc['min_rr']} (pref {sc['preferred_rr']})"))
    return rules


def category_totals(rules: list) -> dict:
    out = {c: 0.0 for c in CATEGORY_WEIGHTS}
    for r in rules:
        if r.category in out:
            out[r.category] += r.points
    return {c: round(min(v, CATEGORY_WEIGHTS[c]), 1) for c, v in out.items()}


def in_session_window(ts: pd.Timestamp, cfg: dict, step_min: int) -> tuple[bool, str]:
    s = cfg["session"]
    o = datetime.combine(ts.date(), time.fromisoformat(s["open"]), tzinfo=IST)
    c = datetime.combine(ts.date(), time.fromisoformat(s["close"]), tzinfo=IST)
    t = ts.to_pydatetime() + timedelta(minutes=step_min)  # decision is made at candle close
    start = o + timedelta(minutes=s["start_delay_minutes"])
    end = c - timedelta(minutes=s["end_buffer_minutes"])
    if t < start:
        return False, f"Before {start:%H:%M} (first {s['start_delay_minutes']} min excluded)"
    if t > end:
        return False, f"After {end:%H:%M} (last {s['end_buffer_minutes']} min excluded for new entries)"
    return True, f"Inside entry window {start:%H:%M}-{end:%H:%M}"
