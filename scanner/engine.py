"""End-to-end decision algorithm (requirements section 19).

decide() is the single source of truth used by the live scanner and by the
backtester. scan_symbol() wraps data loading + quality checks around it.
"""
from __future__ import annotations

import time as _time
import uuid

import pandas as pd

from .indicators import compute_indicators
from .models import BUY, NO_BUY, SELL, WAIT, Rule
from .patterns import add_patterns
from .providers import TF_MINUTES
from .scoring import category_totals, classify_regime, data_quality, in_session_window, risk_plan, score_confluence
from .setups import SETUP_LABELS, setups_for

DECISION_RANK = {BUY: 3, SELL: 3, WAIT: 2, NO_BUY: 1}
STAGE_RANK = {"TRIGGERED": 3, "PENDING": 2, "FAILED": 1}


def prepare_frame(raw: pd.DataFrame, cfg: dict, mode: str) -> pd.DataFrame:
    df = compute_indicators(raw, cfg, intraday=(mode == "INTRADAY"))
    df = add_patterns(df, cfg["setups"]["strong_candle_body_ratio"])
    df.attrs["mode"] = mode
    return df


def _vetoes(df, i, setup, plan, regime, cfg, mode, timeframe, dq_ok, dq_text, incomplete):
    r = df.iloc[i]
    sc = cfg["scoring"]
    out = [Rule("Veto", "DATA_QUALITY", dq_ok, dq_text)]
    out.append(Rule("Veto", "WARMUP", regime["trend"] != "INSUFFICIENT_DATA",
                    "Indicators fully warmed up" if regime["trend"] != "INSUFFICIENT_DATA" else "Insufficient history for 20/50/200"))
    if mode == "INTRADAY":
        ok, txt = in_session_window(df.index[i], cfg, TF_MINUTES.get(timeframe, 5))
        out.append(Rule("Veto", "SESSION_WINDOW", ok, txt))
    if setup.veto:
        out.append(Rule("Veto", setup.veto, False, "Price is between Pivot PP and VWAP (no-trade zone)"))
    out.append(Rule("Veto", "ENTRY_SL_VALID", plan["valid"], "Entry/stop relationship valid" if plan["valid"] else "Stop is on the wrong side of entry"))
    rr_ok = plan["rr1"] >= sc["min_rr"]
    out.append(Rule("Veto", "MIN_RR", rr_ok, f"R:R 1:{plan['rr1']:.2f} vs minimum 1:{sc['min_rr']}", plan["rr1"], f">= {sc['min_rr']}"))
    avg_vol = df["volume"].iloc[max(0, i - 19): i + 1].mean()
    liq = avg_vol >= sc["min_avg_volume"]
    out.append(Rule("Veto", "LIQUIDITY", liq, f"20-bar average volume {avg_vol:,.0f}", avg_vol, f">= {sc['min_avg_volume']:,}"))
    out.append(Rule("Veto", "COMPLETED_CANDLE", not incomplete,
                    "Signal uses a completed candle" if not incomplete else "Signal uses the forming candle (early mode)"))
    against = ("TREND_DOWN" if setup.direction == "LONG" else "TREND_UP")
    conflict = regime["trend"] == against and not setup.reversal
    out.append(Rule("Veto", "TREND_CONFLICT", not conflict,
                    f"Setup direction conflicts with {regime['trend']}" if conflict else "No conflict with trend filter"))
    return out


def _classify(setup, score, vetoes, cfg):
    sc = cfg["scoring"]
    if any(not v.passed for v in vetoes):
        return NO_BUY
    long = setup.direction == "LONG"
    if setup.stage == "TRIGGERED" and score >= (sc["buy_threshold"] if long else sc["sell_threshold"]):
        return BUY if long else SELL
    if setup.stage in ("TRIGGERED", "PENDING") and score >= sc["watch_threshold"]:
        return WAIT
    return NO_BUY


def decide(df, i, cfg, mode, timeframe, dq_ok=True, dq_text="Data checks passed", incomplete=False,
           lot_size=1, only_setup: str | None = None, only_direction: str | None = None) -> dict:
    regime = classify_regime(df, i)
    evaluated = []
    for key, fn, dirs in setups_for(mode):
        if only_setup and key != only_setup:
            continue
        for d in dirs:
            if d == "SHORT" and not cfg["scoring"]["enable_short"]:
                continue
            if only_direction and d != only_direction:
                continue
            res = fn(df, i, cfg, d)
            res.key = key
            evaluated.append(res)
    options = []
    for res in evaluated:
        if res.stage == "FAILED" and any(o for o in evaluated if o.stage != "FAILED"):
            continue
        plan = risk_plan(res, df, i, cfg, lot_size)
        rules = score_confluence(df, i, res, plan, regime, cfg, mode)
        score = round(sum(r.points for r in rules))
        vetoes = _vetoes(df, i, res, plan, regime, cfg, mode, timeframe, dq_ok, dq_text, incomplete)
        decision = _classify(res, score, vetoes, cfg)
        if res.stage == "FAILED":
            decision = NO_BUY
        options.append((DECISION_RANK[decision], STAGE_RANK[res.stage], score, res.gates_passed, res, plan, rules, vetoes, decision))
    options.sort(key=lambda o: (o[0], o[1], o[2], o[3]), reverse=True)
    _, _, score, _, best, plan, rules, vetoes, decision = options[0]
    r = df.iloc[i]
    pos = [ru for ru in rules if ru.points > 0] + [g for g in best.gates if g.passed]
    neg = [v for v in vetoes if not v.passed] + [g for g in best.gates if not g.passed] + \
          [ru for ru in rules if ru.points < ru.max_points]
    why_not = []
    if decision != (BUY if best.direction == "LONG" else SELL):
        why_not += [f"Hard veto - {v.explanation}" for v in vetoes if not v.passed]
        if best.stage != "TRIGGERED":
            why_not.append(best.missing or "Setup structure incomplete: " + "; ".join(g.explanation for g in best.gates if not g.passed))
        thr = cfg["scoring"]["buy_threshold"] if best.direction == "LONG" else cfg["scoring"]["sell_threshold"]
        if score < thr:
            why_not.append(f"Score {score} is below the {thr} threshold")
    return {
        "setup_key": best.key, "setup": best.name, "setup_label": SETUP_LABELS.get(best.key, best.name),
        "direction": best.direction, "stage": best.stage, "decision": decision, "score": int(score),
        "categories": category_totals(rules), "score_rules": rules, "gates": best.gates, "vetoes": vetoes,
        "plan": plan, "regime": regime, "trigger": best.trigger, "missing": best.missing,
        "bar_time": df.index[i], "price": round(float(r.close), 2), "reasons": pos, "flags": neg, "why_not": why_not,
        "all_setups": [{"setup": SETUP_LABELS.get(e.key, e.name), "direction": e.direction, "stage": e.stage,
                        "gates": f"{e.gates_passed}/{len(e.gates)}"} for e in evaluated],
    }


def scan_symbol(provider, symbol: str, mode: str, cfg: dict, meta: dict | None = None) -> tuple[dict, pd.DataFrame | None]:
    meta = meta or {}
    tf = cfg["timeframes"]["intraday_primary"] if mode == "INTRADAY" else cfg["timeframes"]["swing_primary"]
    base = {"signal_id": uuid.uuid4().hex[:12], "symbol": symbol, "mode": mode, "timeframe": tf,
            "sector": meta.get("sector", ""), "exchange": meta.get("exchange", "NSE")}
    try:
        if getattr(provider, "errors", {}).get(symbol):
            return {**base, **_error_signal("PROVIDER_ERROR", provider.errors[symbol], None)}, None
        raw = provider.get_historical_ohlcv(symbol, tf)
        dq = data_quality(raw, cfg, mode, tf, provider.now())
        if raw.empty or len(raw) < 30:
            return {**base, **_error_signal("NO_DATA", "Not enough candles from the provider", dq)}, None
        incomplete = False
        if not dq["complete_last"]:
            if cfg["data"]["early_candle_mode"]:
                incomplete = True
            else:
                raw = raw.iloc[:-1]
        df = prepare_frame(raw, cfg, mode)
        i = len(df) - 1
        dq_text = "All data checks passed" if dq["ok"] else "Failed: " + ", ".join(dq["flags"])
        sig = decide(df, i, cfg, mode, tf, dq["ok"], dq_text, incomplete, int(meta.get("lot_size", 1) or 1))
        sig["dq"] = dq
        return {**base, **sig}, df
    except Exception as exc:  # one bad symbol must not stop the scan
        print(f"[scan] {symbol} {mode} failed: {exc!r}")
        return {**base, **_error_signal("CALC_ERROR", f"{type(exc).__name__}: {exc}", None)}, None


def _error_signal(code, text, dq):
    v = Rule("Veto", code, False, text)
    return {"setup_key": None, "setup": "-", "setup_label": "-", "direction": "LONG", "stage": "FAILED", "decision": NO_BUY,
            "score": 0, "categories": {}, "score_rules": [], "gates": [], "vetoes": [v], "plan": None,
            "regime": {"trend": "INSUFFICIENT_DATA", "vol": "NORMAL", "label": "INSUFFICIENT_DATA"}, "trigger": None,
            "missing": None, "bar_time": None, "price": None, "reasons": [], "flags": [v], "why_not": [text],
            "all_setups": [], "dq": dq or {"ok": False, "flags": [code], "rules": [v], "last_ts": None, "age_min": None,
                                             "complete_last": False, "missing": 0}, "error": text}


def scan_universe(provider, instruments: pd.DataFrame, mode: str, cfg: dict):
    t0 = _time.perf_counter()
    signals, frames = [], {}
    tf = cfg["timeframes"]["intraday_primary"] if mode == "INTRADAY" else cfg["timeframes"]["swing_primary"]
    provider.prefetch(list(instruments["symbol"]), tf)
    for _, row in instruments.iterrows():
        sig, df = scan_symbol(provider, row["symbol"], mode, cfg, row.to_dict())
        signals.append(sig)
        if df is not None:
            frames[row["symbol"]] = df
    elapsed = _time.perf_counter() - t0
    print(f"[scan] {mode}: {len(signals)} symbols in {elapsed:.2f}s")
    return signals, frames, elapsed
