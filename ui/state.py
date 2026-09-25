"""Shared app state: store, active config, provider and the latest scans."""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from scanner.config import ROOT
from scanner.calls import update_open_calls
from scanner.engine import scan_universe
from scanner.providers import get_provider
from scanner.storage import Store

DB_PATH = ROOT / "data" / "scanner.db"


@st.cache_resource
def get_store() -> Store:
    return Store(DB_PATH)


def active_config() -> tuple[dict, str]:
    return get_store().active_config()


@st.cache_resource
def _provider(version: str, _cfg: dict):
    return get_provider(_cfg, ROOT)


def provider():
    cfg, v = active_config()
    return _provider(v, cfg)


def instruments() -> pd.DataFrame:
    try:
        return provider().get_instruments()
    except Exception as exc:
        get_store().event("ERROR", "provider", f"get_instruments failed: {exc!r}")
        return pd.DataFrame(columns=["symbol", "sector", "exchange", "lot_size"])


def provider_error():
    """Returns a message if the configured provider cannot even be created (e.g. Kite without token)."""
    try:
        provider()
        return None
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"


def scans() -> dict:
    return st.session_state.setdefault("scans", {})


def run_scan(mode: str, universe: pd.DataFrame) -> dict:
    cfg, version = active_config()
    prov = provider()
    store = get_store()
    started = datetime.now().isoformat(timespec="seconds")
    signals, frames, elapsed = scan_universe(prov, universe, mode, cfg)
    for s in signals:
        s["config_version"] = version
        if s.get("error"):
            store.event("WARN", "scan", f"{s['symbol']} {mode}: {s['error']}")
    data_ts = max((s["bar_time"] for s in signals if s["bar_time"] is not None), default=None)
    run_id = store.save_scan(mode, signals, started, elapsed, version, prov.name, data_ts)
    alerts = store.new_alerts(signals, cfg)
    for a in alerts:
        if a.get("plan") and a["plan"]["valid"]:
            store.add_call(a, version)
    update_open_calls(store, prov, cfg)
    if not cfg["alerts"]["enabled"]:
        alerts = []
    result = {"signals": signals, "frames": frames, "at": prov.now(), "run_id": run_id, "elapsed": elapsed,
              "version": version, "alerts": alerts, "universe": list(universe["symbol"])}
    scans()[mode] = result
    for a in alerts[:2]:
        _notify(a, cfg)
    if len(alerts) > 2:
        st.toast(f"{len(alerts) - 2} more new {mode.lower()} candidates. See Recent alerts on the Dashboard.", icon="🔔")
    return result


def _notify(sig: dict, cfg: dict):
    p = sig.get("plan") or {}
    msg = (f"{sig['decision']}: {sig['symbol']} · {sig['setup_label']} · score {sig['score']} · "
           f"entry {p.get('entry')} SL {p.get('stop')} T1 {p.get('t1')} T2 {p.get('t2')} R:R 1:{p.get('rr1')}. "
           "Scanner signal, not a guaranteed outcome.")
    st.toast(msg, icon="🔔")
    if cfg["alerts"].get("desktop_notifications"):
        try:
            from plyer import notification
            notification.notify(title=f"Trade Scanner: {sig['symbol']}", message=msg[:250], timeout=8)
        except Exception as exc:
            get_store().event("WARN", "alerts", f"Desktop notification unavailable: {exc!r}")


def open_detail(mode: str, symbol: str):
    st.session_state["detail"] = {"mode": mode, "symbol": symbol}


def find_signal(mode: str, symbol: str):
    res = scans().get(mode)
    if not res:
        return None, None
    for s in res["signals"]:
        if s["symbol"] == symbol:
            return s, res["frames"].get(symbol)
    return None, None


def signals_frame(signals: list) -> pd.DataFrame:
    rows = []
    for s in signals:
        p = s.get("plan") or {}
        rows.append({"Symbol": s["symbol"], "Mode": s["mode"].title(), "Decision": s["decision"], "Score": s["score"],
                     "Direction": s["direction"], "Setup": s["setup_label"], "Sector": s.get("sector", ""),
                     "Price": s["price"], "Entry": p.get("entry"), "SL": p.get("stop"), "T1": p.get("t1"), "T2": p.get("t2"),
                     "R:R": p.get("rr1"), "Qty": p.get("qty"), "Risk ₹": p.get("risk_amount"),
                     "Regime": s["regime"]["label"], "Top reasons": "; ".join(r.explanation for r in s["reasons"][:3]),
                     "Risk flags": "; ".join(r.explanation for r in s["flags"][:3]),
                     "Bar time": s["bar_time"], "Config": s.get("config_version", "")})
    return pd.DataFrame(rows)
