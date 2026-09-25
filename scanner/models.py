"""Shared result objects. Every decision is built from Rule rows so the UI can
show exactly what passed, what failed and how many points each rule earned."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

BUY, WAIT, NO_BUY, SELL = "BUY CANDIDATE", "WAIT / WATCH", "NO BUY", "SELL CANDIDATE"
CATEGORY_WEIGHTS = {"Trend": 25, "Momentum": 15, "Price action": 20, "Volume": 10,
                    "S&R / location": 15, "Volatility": 5, "Risk / reward": 10}


@dataclass
class Rule:
    category: str          # score category, "Gate" or "Veto" or "Data"
    code: str              # stable machine code, e.g. TREND_VWAP_BIAS
    passed: bool
    explanation: str
    value: Optional[float] = None
    threshold: Optional[str] = None
    points: float = 0.0
    max_points: float = 0.0

    def as_dict(self):
        return asdict(self)


@dataclass
class SetupResult:
    name: str
    mode: str                      # INTRADAY | SWING
    direction: str                 # LONG | SHORT
    stage: str                     # TRIGGERED | PENDING | FAILED
    gates: list = field(default_factory=list)
    trigger: Optional[str] = None  # pattern name
    trigger_index: Optional[int] = None
    ref_level: Optional[float] = None       # level the setup is anchored on
    structural_stop: Optional[float] = None
    target_level: Optional[float] = None    # T1 candidate from structure
    target_level2: Optional[float] = None
    reversal: bool = False                  # counter-trend family (divergence, contra)
    missing: Optional[str] = None           # what is still needed (for WAIT)
    veto: Optional[str] = None              # setup-level hard veto code

    @property
    def gates_passed(self) -> int:
        return sum(1 for g in self.gates if g.passed)
