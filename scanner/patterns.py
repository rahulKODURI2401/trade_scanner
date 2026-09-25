"""Candlestick pattern detection (requirements section 7).

Vectorised boolean columns are added to the frame; pattern_at() turns the
flags at one bar into structured PatternResult objects with evidence.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

BULLISH = ["hammer", "bull_engulfing", "morning_star", "bull_marubozu", "strong_green"]
BEARISH = ["shooting_star", "bear_engulfing", "evening_star", "bear_marubozu", "strong_red"]
NAMES = {
    "hammer": "Hammer", "shooting_star": "Shooting Star", "bull_engulfing": "Bullish Engulfing",
    "bear_engulfing": "Bearish Engulfing", "bull_marubozu": "Bullish Marubozu",
    "bear_marubozu": "Bearish Marubozu", "doji": "Doji", "morning_star": "Morning Star",
    "evening_star": "Evening Star", "strong_green": "Strong green candle", "strong_red": "Strong red candle",
}


@dataclass
class PatternResult:
    name: str
    direction: str
    confidence: str
    candle_index: int
    score: int
    reasons: list = field(default_factory=list)
    invalidation: list = field(default_factory=list)


def add_patterns(df: pd.DataFrame, strong_body: float = 0.6) -> pd.DataFrame:
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    body = (c - o).abs()
    rng = (h - l).replace(0, float("nan"))
    up_w = h - pd.concat([o, c], axis=1).max(axis=1)
    lo_w = pd.concat([o, c], axis=1).min(axis=1) - l
    green, red = c > o, c < o
    po, pc = o.shift(1), c.shift(1)
    small = body / rng <= 0.35
    out = df.copy()
    out["hammer"] = small & (lo_w >= 2 * body) & (up_w <= body.clip(lower=rng * 0.1))
    out["shooting_star"] = small & (up_w >= 2 * body) & (lo_w <= body.clip(lower=rng * 0.1))
    out["bull_engulfing"] = green & (pc < po) & (o <= pc) & (c >= po) & (body > (pc - po).abs())
    out["bear_engulfing"] = red & (pc > po) & (o >= pc) & (c <= po) & (body > (pc - po).abs())
    maru = body / rng >= 0.9
    out["bull_marubozu"] = maru & green
    out["bear_marubozu"] = maru & red
    out["doji"] = body / rng <= 0.1
    b2, o2, c2 = body.shift(2), o.shift(2), c.shift(2)
    mid2 = (o2 + c2) / 2
    out["morning_star"] = (c2 < o2) & (b2 / rng.shift(2) > 0.5) & (body.shift(1) / rng.shift(1) < 0.3) & green & (c > mid2)
    out["evening_star"] = (c2 > o2) & (b2 / rng.shift(2) > 0.5) & (body.shift(1) / rng.shift(1) < 0.3) & red & (c < mid2)
    out["strong_green"] = green & (body / rng >= strong_body)
    out["strong_red"] = red & (body / rng >= strong_body)
    for col in NAMES:
        out[col] = out[col].fillna(False).astype(bool)
    return out


def pattern_at(df: pd.DataFrame, i: int, direction: str) -> list[PatternResult]:
    names = BULLISH if direction == "LONG" else BEARISH
    row = df.iloc[i]
    hits = []
    weights = {"bull_engulfing": 9, "bear_engulfing": 9, "hammer": 8, "shooting_star": 8,
               "morning_star": 9, "evening_star": 9, "bull_marubozu": 8, "bear_marubozu": 8,
               "strong_green": 6, "strong_red": 6}
    for key in names:
        if bool(row[key]):
            hits.append(PatternResult(
                name=NAMES[key], direction=direction, confidence="CONFIRMED", candle_index=i,
                score=weights[key],
                reasons=[f"{NAMES[key]} on completed candle (body {row['body_ratio']:.0%} of range)"],
                invalidation=[f"Close below {row['low']:.2f}" if direction == "LONG" else f"Close above {row['high']:.2f}"],
            ))
    return sorted(hits, key=lambda p: -p.score)
