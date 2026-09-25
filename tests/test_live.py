"""Live providers and call tracking, tested offline with mocked responses."""
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner.calls import replay  # noqa: E402
from scanner.config import load_default_config  # noqa: E402
from scanner.live_providers import UpstoxProvider, YahooProvider  # noqa: E402
from scanner.providers import IST  # noqa: E402


class FakeResp:
    def __init__(self, payload, status=200):
        self.payload, self.status_code, self.text = payload, status, str(payload)

    def json(self):
        return self.payload


def test_upstox_parses_and_merges(tmp_path, monkeypatch):
    cfg = load_default_config()
    prov = UpstoxProvider(cfg, tmp_path)
    hist = {"status": "success", "data": {"candles": [
        ["2026-09-24T09:20:00+05:30", 101, 103, 100, 102, 5000, 0],
        ["2026-09-24T09:15:00+05:30", 100, 102, 99, 101, 7000, 0]]}}
    live = {"status": "success", "data": {"candles": [["2026-09-25T09:15:00+05:30", 102, 104, 101, 103, 6000, 0]]}}
    calls = []

    def fake_get(url, timeout=20):
        calls.append(url)
        return FakeResp(live if "/intraday/" in url else hist)

    monkeypatch.setattr(prov.http, "get", fake_get)
    monkeypatch.setattr(prov, "instrument_key", lambda s: "NSE_EQ|INE002A01018")
    df = prov.get_historical_ohlcv("RELIANCE", "5m")
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert len(df) == 3 and df.index.is_monotonic_increasing
    assert str(df.index.tz) == "Asia/Kolkata"
    assert "NSE_EQ%7CINE002A01018/minutes/5/" in calls[0] and "/intraday/" in calls[1]


def test_upstox_http_error_is_reported(tmp_path, monkeypatch):
    prov = UpstoxProvider(load_default_config(), tmp_path)
    monkeypatch.setattr(prov.http, "get", lambda url, timeout=20: FakeResp({"errors": ["bad"]}, 400))
    monkeypatch.setattr(prov, "instrument_key", lambda s: "NSE_EQ|X")
    prov.prefetch(["RELIANCE"], "1D")
    assert "HTTP 400" in prov.errors["RELIANCE"]


def test_yahoo_clean_multiindex():
    idx = pd.date_range("2026-09-22", periods=3, freq="D")
    cols = pd.MultiIndex.from_product([["Open", "High", "Low", "Close", "Adj Close", "Volume"], ["TCS.NS"]])
    raw = pd.DataFrame([[1, 2, 0.5, 1.5, 1.5, 10]] * 3, index=idx, columns=cols)
    df = YahooProvider._clean(raw, "1D")
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert df.index[0].hour == 15 and str(df.index.tz) == "Asia/Kolkata"


def _bars(rows, start="2026-09-24 10:00"):
    idx = pd.date_range(start, periods=len(rows), freq="5min", tz=IST)
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx).assign(volume=1000)


BASE = {"direction": "LONG", "mode": "INTRADAY", "entry": 100.0, "stop": 98.0, "t1": 104.0, "t2": 106.0,
        "created_bar": "2026-09-24 09:55:00+05:30", "last_price": 100}


@pytest.fixture
def cfg():
    return load_default_config()


def test_call_hits_t1_then_t2(cfg):
    df = _bars([[99.5, 100.5, 99.4, 100.2], [100.2, 104.5, 100, 104.2], [104.2, 106.5, 103, 106]])
    r = replay(BASE, df, cfg)
    assert r["status"] == "T2_HIT" and r["result_r"] == pytest.approx(0.5 * 2 + 0.5 * 3)


def test_call_stop_first_on_ambiguous_candle(cfg):
    df = _bars([[99.5, 100.5, 99.4, 100.2], [100, 104.5, 97.5, 101]])
    assert replay(BASE, df, cfg)["status"] == "SL_HIT"


def test_call_expires_without_entry(cfg):
    df = _bars([[99, 99.5, 98.8, 99.2]] * 4)
    assert replay(BASE, df, cfg)["status"] == "EXPIRED"


def test_call_t1_then_breakeven(cfg):
    df = _bars([[99.5, 100.5, 99.4, 100.2], [100.2, 104.5, 100.1, 104], [104, 104.2, 99.8, 100]])
    r = replay(BASE, df, cfg)
    assert r["status"] == "T1_HIT_THEN_BE" and r["result_r"] == pytest.approx(1.0)
