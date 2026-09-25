"""Core acceptance tests (requirements sections 15.1 and 20)."""
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scanner.config import load_default_config  # noqa: E402
from scanner.engine import decide, prepare_frame, scan_symbol  # noqa: E402
from scanner.indicators import compute_indicators, ema, rsi  # noqa: E402
from scanner.models import BUY, SELL, SetupResult  # noqa: E402
from scanner.patterns import add_patterns  # noqa: E402
from scanner.providers import IST  # noqa: E402
from tests.synthetic import DemoProvider  # noqa: E402
from scanner.scoring import data_quality, risk_plan  # noqa: E402


@pytest.fixture(scope="module")
def cfg():
    return load_default_config()


@pytest.fixture(scope="module")
def provider(cfg):
    return DemoProvider(cfg)


def test_ema_matches_reference():
    s = pd.Series(np.arange(1, 31, dtype=float))
    ref = s.ewm(span=9, adjust=False).mean()
    assert np.allclose(ema(s, 9).dropna(), ref.iloc[8:])


def test_rsi_bounds():
    s = pd.Series(np.random.default_rng(1).normal(0, 1, 300).cumsum() + 100)
    r = rsi(s, 14).dropna()
    assert r.between(0, 100).all()


def test_no_lookahead(provider, cfg):
    """Indicators at bar i must not change when future bars are appended."""
    raw = provider.get_historical_ohlcv("TCS", "1D")
    full = compute_indicators(raw, cfg, intraday=False)
    cut = compute_indicators(raw.iloc[:300], cfg, intraday=False)
    cols = ["ema_trend", "sma_mid", "rsi", "macd", "st", "atr", "bb_up", "swing_high", "swing_low", "relvol"]
    a, b = full[cols].iloc[:300], cut[cols]
    assert np.allclose(a.to_numpy(dtype=float), b.to_numpy(dtype=float), equal_nan=True)


def test_reproducible(provider, cfg):
    s1, _ = scan_symbol(provider, "INFY", "INTRADAY", cfg)
    s2, _ = scan_symbol(provider, "INFY", "INTRADAY", cfg)
    assert (s1["decision"], s1["score"], s1["plan"]) == (s2["decision"], s2["score"], s2["plan"])


def test_risk_plan_math(provider, cfg):
    df = prepare_frame(provider.get_historical_ohlcv("SBIN", "1D"), cfg, "SWING")
    i = len(df) - 1
    setup = SetupResult("test", "SWING", "LONG", "TRIGGERED", structural_stop=df["low"].iloc[i] - 5)
    p = risk_plan(setup, df, i, cfg)
    budget = cfg["risk"]["capital"] * cfg["risk"]["risk_percent"] / 100
    expected_qty = min(math.floor(budget / p["risk_per_share"]), math.floor(cfg["risk"]["capital"] / p["entry"]))
    assert p["stop"] < p["entry"] < p["t1"] < p["t2"]
    assert abs(p["qty"] - expected_qty) <= 1
    assert p["rr1"] == pytest.approx((p["t1"] - p["entry"]) / p["risk_per_share"], abs=0.02)


def test_hard_veto_blocks_buy(provider, cfg):
    df = prepare_frame(provider.get_historical_ohlcv("HAL", "1D"), cfg, "SWING")
    sig = decide(df, len(df) - 1, cfg, "SWING", "1D", dq_ok=False, dq_text="forced failure")
    assert sig["decision"] not in (BUY, SELL)


def test_stale_data_flagged(provider, cfg):
    raw = provider.get_historical_ohlcv("ITC", "5m")
    later = provider.now() + pd.Timedelta(hours=3)
    dq = data_quality(raw, cfg, "INTRADAY", "5m", later)
    assert "FRESHNESS" in dq["flags"] and not dq["ok"]


def test_invalid_ohlc_flagged(provider, cfg):
    raw = provider.get_historical_ohlcv("ITC", "1D").copy()
    raw.iloc[-5, raw.columns.get_loc("high")] = raw["low"].iloc[-5] - 1
    dq = data_quality(raw, cfg, "SWING", "1D", provider.now())
    assert "OHLC_SANITY" in dq["flags"]


def test_bullish_engulfing_detected():
    df = pd.DataFrame({"open": [10, 10.5, 9.5], "high": [10.6, 10.6, 11.2], "low": [9.8, 9.6, 9.4],
                       "close": [10.5, 9.7, 11.0], "volume": [1, 1, 1]},
                      index=pd.date_range("2026-01-01", periods=3, tz=IST))
    assert bool(add_patterns(df)["bull_engulfing"].iloc[-1])


def test_every_decision_has_reasons(provider, cfg):
    for sym in ["RELIANCE", "TCS", "BDL"]:
        s, _ = scan_symbol(provider, sym, "SWING", cfg)
        assert s["reasons"] or s["flags"]
        if s["decision"] not in (BUY, SELL):
            assert s["why_not"]
