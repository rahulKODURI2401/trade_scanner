"""Call tracking: every new BUY/SELL CANDIDATE becomes a 'call' whose life is
replayed on real candles after it fired (no hindsight, completed candles only).

Rules (documented so results are honest and reproducible):
* Entry is a stop order at the planned entry, valid for N candles (and the same
  session for intraday). Not triggered -> EXPIRED (no trade).
* Stop first if stop and target are touched in the same candle.
* At T1, half the position is booked and the stop moves to entry (breakeven).
  The rest runs to T2 or the trailing stop.
* Intraday calls close at the session's last candle; swing calls close after
  max_hold_bars daily candles.
R is measured against the planned risk (entry - stop); costs are not included.
"""
from __future__ import annotations

import pandas as pd

OPEN = ("WAITING_ENTRY", "ACTIVE", "T1_HIT")


def replay(call: dict, df: pd.DataFrame, cfg: dict) -> dict:
    long = call["direction"] == "LONG"
    sign = 1 if long else -1
    entry, stop, t1, t2 = call["entry"], call["stop"], call["t1"], call["t2"]
    risk = abs(entry - stop) or 1e-9
    created = pd.Timestamp(call["created_bar"])
    after = df[df.index > created]
    intraday = call["mode"] == "INTRADAY"
    valid = cfg["calls"]["entry_valid_bars_intraday" if intraday else "entry_valid_bars_swing"]
    max_hold = cfg["calls"]["max_hold_bars_swing"]
    out = {"status": "WAITING_ENTRY", "active_sl": stop, "entry_time": None, "exit_time": None,
           "exit_price": None, "result_r": None, "last_price": float(df["close"].iloc[-1]) if len(df) else call.get("last_price")}
    if after.empty:
        return out
    if intraday:
        after = after[after.index.date == created.date()]
    state, sl, booked, bars_in = "WAITING_ENTRY", stop, 0.0, 0
    for n, (ts, bar) in enumerate(after.iterrows()):
        hi, lo = bar["high"], bar["low"]
        if state == "WAITING_ENTRY":
            if (long and hi >= entry) or (not long and lo <= entry):
                state, out["entry_time"] = "ACTIVE", str(ts)
            elif n + 1 >= valid:
                out.update(status="EXPIRED", exit_time=str(ts))
                return out
            else:
                continue
        bars_in += 1
        hit_sl = lo <= sl if long else hi >= sl
        hit_t1 = hi >= t1 if long else lo <= t1
        hit_t2 = hi >= t2 if long else lo <= t2
        if hit_sl:  # stop first on ambiguous candles
            if state == "T1_HIT":
                out.update(status="T1_HIT_THEN_BE", exit_time=str(ts), exit_price=sl, result_r=round(booked, 2))
            else:
                out.update(status="SL_HIT", exit_time=str(ts), exit_price=sl, result_r=-1.0)
            out["active_sl"] = sl
            return out
        if state == "ACTIVE" and hit_t1:
            state, sl = "T1_HIT", entry
            booked = 0.5 * abs(t1 - entry) / risk
        if state == "T1_HIT" and hit_t2:
            r = booked + 0.5 * abs(t2 - entry) / risk
            out.update(status="T2_HIT", exit_time=str(ts), exit_price=t2, result_r=round(r, 2), active_sl=sl)
            return out
        if not intraday and bars_in >= max_hold:
            px = bar["close"]
            part = 0.5 if state == "T1_HIT" else 1.0
            r = booked + part * (px - entry) * sign / risk
            out.update(status="TIME_EXIT", exit_time=str(ts), exit_price=float(px), result_r=round(r, 2), active_sl=sl)
            return out
    out["status"], out["active_sl"] = state, sl
    if intraday and state in OPEN:
        last = after.index[-1]
        session_over = last.hour * 60 + last.minute >= 15 * 60 + 25
        if session_over or pd.Timestamp.now(tz=last.tz).date() > created.date():
            if state == "WAITING_ENTRY":
                out.update(status="EXPIRED", exit_time=str(last))
            else:
                px = after["close"].iloc[-1]
                part = 0.5 if state == "T1_HIT" else 1.0
                out.update(status="CLOSED_EOD", exit_time=str(last), exit_price=float(px),
                           result_r=round(booked + part * (px - entry) * sign / risk, 2))
    return out


def update_open_calls(store, provider, cfg: dict) -> int:
    calls = store.calls(open_only=True)
    n = 0
    for _, c in calls.iterrows():
        try:
            df = provider.get_historical_ohlcv(c["symbol"], c["timeframe"])
            if c["mode"] == "INTRADAY" and len(df):
                step = pd.Timedelta(minutes=int(c["timeframe"].rstrip("m")))
                if df.index[-1] + step > provider.now():
                    df = df.iloc[:-1]  # forming candle excluded
            elif len(df) and df.index[-1] > provider.now():
                df = df.iloc[:-1]  # today's daily candle is still forming
            res = replay(c.to_dict(), df, cfg)
            store.update_call(c["call_id"], res)
            n += 1
        except Exception as exc:
            print(f"[calls] {c['symbol']}: {exc!r}")
    return n
