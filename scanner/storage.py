"""Local SQLite storage (requirements section 16). The file lives at
data/scanner.db and never leaves the PC."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from .config import config_hash, load_default_config, merge_config

SCHEMA = """
CREATE TABLE IF NOT EXISTS instruments (instrument_id TEXT PRIMARY KEY, symbol TEXT, exchange TEXT, segment TEXT,
    tick_size REAL, lot_size INTEGER, active INTEGER);
CREATE TABLE IF NOT EXISTS scan_config (config_id TEXT PRIMARY KEY, mode TEXT, timeframe TEXT, parameters_json TEXT,
    version TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS scan_runs (run_id TEXT PRIMARY KEY, mode TEXT, started_at TEXT, completed_at TEXT,
    data_timestamp TEXT, universe_size INTEGER, config_version TEXT, elapsed_sec REAL, provider TEXT);
CREATE TABLE IF NOT EXISTS signals (signal_id TEXT PRIMARY KEY, run_id TEXT, symbol TEXT, mode TEXT, timeframe TEXT,
    setup TEXT, direction TEXT, score INTEGER, decision TEXT, timestamp TEXT, bar_time TEXT, price REAL,
    config_version TEXT, alerted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS signal_reasons (signal_id TEXT, category TEXT, rule_code TEXT, passed INTEGER, value REAL,
    threshold TEXT, explanation TEXT, points REAL, max_points REAL);
CREATE TABLE IF NOT EXISTS risk_plan (signal_id TEXT PRIMARY KEY, entry REAL, stop REAL, target1 REAL, target2 REAL,
    rr1 REAL, rr2 REAL, quantity INTEGER, risk_amount REAL);
CREATE TABLE IF NOT EXISTS watchlist (watchlist_id TEXT PRIMARY KEY, symbol TEXT, mode TEXT, notes TEXT, enabled INTEGER,
    added_at TEXT);
CREATE TABLE IF NOT EXISTS journal (journal_id TEXT PRIMARY KEY, signal_id TEXT, symbol TEXT, mode TEXT, setup TEXT,
    direction TEXT, planned_entry REAL, planned_stop REAL, planned_t1 REAL, entry_actual REAL, exit_actual REAL,
    quantity INTEGER, pnl REAL, outcome TEXT, notes TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS backtests (backtest_id TEXT PRIMARY KEY, strategy_version TEXT, period TEXT, config TEXT,
    metrics_json TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS calls (call_id TEXT PRIMARY KEY, signal_id TEXT, symbol TEXT, mode TEXT, timeframe TEXT,
    setup TEXT, direction TEXT, score INTEGER, entry REAL, stop REAL, t1 REAL, t2 REAL, qty INTEGER, created_bar TEXT,
    created_at TEXT, status TEXT, active_sl REAL, entry_time TEXT, exit_time TEXT, exit_price REAL, result_r REAL,
    last_price REAL, updated_at TEXT, config_version TEXT, reasons TEXT);
CREATE TABLE IF NOT EXISTS app_events (event_id TEXT PRIMARY KEY, timestamp TEXT, level TEXT, component TEXT, message TEXT);
"""


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self):
        return sqlite3.connect(self.path, timeout=10)

    def _q(self, sql, params=()):
        with self._conn() as c:
            return pd.read_sql_query(sql, c, params=params)

    def _x(self, sql, params=()):
        with self._conn() as c:
            c.execute(sql, params)

    # -- events -------------------------------------------------------------
    def event(self, level: str, component: str, message: str):
        print(f"[{level}] {component}: {message}")
        self._x("INSERT INTO app_events VALUES (?,?,?,?,?)",
                (uuid.uuid4().hex[:12], datetime.now().isoformat(timespec="seconds"), level, component, message[:1000]))

    def events(self, limit=200):
        return self._q("SELECT timestamp, level, component, message FROM app_events ORDER BY timestamp DESC LIMIT ?", (limit,))

    # -- config -------------------------------------------------------------
    def active_config(self) -> tuple[dict, str]:
        df = self._q("SELECT parameters_json, version FROM scan_config ORDER BY updated_at DESC LIMIT 1")
        default = load_default_config()
        if df.empty:
            v = config_hash(default)
            self.save_config(default, note="defaults")
            return default, v
        cfg = merge_config(default, json.loads(df["parameters_json"].iloc[0]))
        return cfg, df["version"].iloc[0]

    def save_config(self, cfg: dict, note="") -> str:
        v = config_hash(cfg)
        self._x("INSERT INTO scan_config VALUES (?,?,?,?,?,?)",
                (uuid.uuid4().hex[:12], "ALL", note, json.dumps(cfg), v, datetime.now().isoformat(timespec="microseconds")))
        return v

    def config_history(self):
        return self._q("SELECT version, timeframe AS note, updated_at FROM scan_config ORDER BY updated_at DESC LIMIT 20")

    # -- scans --------------------------------------------------------------
    def save_scan(self, mode, signals, started, elapsed, cfg_version, provider_name, data_ts):
        run_id = uuid.uuid4().hex[:12]
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as c:
            c.execute("INSERT INTO scan_runs VALUES (?,?,?,?,?,?,?,?,?)",
                      (run_id, mode, started, now, str(data_ts), len(signals), cfg_version, round(elapsed, 3), provider_name))
            for s in signals:
                c.execute("INSERT INTO signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
                          (s["signal_id"], run_id, s["symbol"], s["mode"], s["timeframe"], s["setup_label"], s["direction"],
                           s["score"], s["decision"], now, str(s["bar_time"]), s["price"], cfg_version))
                rows = [(s["signal_id"], r.category, r.code, int(r.passed), r.value, r.threshold, r.explanation, r.points, r.max_points)
                        for r in (s["score_rules"] + s["gates"] + s["vetoes"])]
                c.executemany("INSERT INTO signal_reasons VALUES (?,?,?,?,?,?,?,?,?)", rows)
                p = s.get("plan")
                if p:
                    c.execute("INSERT INTO risk_plan VALUES (?,?,?,?,?,?,?,?,?)",
                              (s["signal_id"], p["entry"], p["stop"], p["t1"], p["t2"], p["rr1"], p["rr2"], p["qty"], p["risk_amount"]))
        return run_id

    def scan_runs(self, limit=30):
        return self._q("SELECT * FROM scan_runs ORDER BY started_at DESC LIMIT ?", (limit,))

    def signals_on(self, day: str):
        return self._q("SELECT * FROM signals WHERE substr(timestamp,1,10)=?", (day,))

    def new_alerts(self, signals, cfg) -> list:
        """Signals that just became BUY/SELL CANDIDATE and were not alerted within the cooldown."""
        out = []
        cutoff = (datetime.now() - timedelta(minutes=cfg["alerts"]["cooldown_minutes"])).isoformat(timespec="seconds")
        for s in signals:
            if s["decision"] not in ("BUY CANDIDATE", "SELL CANDIDATE"):
                continue
            prev = self._q("SELECT 1 FROM signals WHERE symbol=? AND setup=? AND timeframe=? AND alerted=1 AND timestamp>=? LIMIT 1",
                           (s["symbol"], s["setup_label"], s["timeframe"], cutoff))
            if prev.empty:
                self._x("UPDATE signals SET alerted=1 WHERE signal_id=?", (s["signal_id"],))
                out.append(s)
        return out

    # -- calls (tracked candidates) -------------------------------------------
    def add_call(self, s: dict, version: str):
        p = s["plan"]
        self._x("INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex[:12], s["signal_id"], s["symbol"], s["mode"], s["timeframe"], s["setup_label"],
                 s["direction"], s["score"], p["entry"], p["stop"], p["t1"], p["t2"], p["qty"], str(s["bar_time"]),
                 datetime.now().isoformat(timespec="seconds"), "WAITING_ENTRY", p["stop"], None, None, None, None,
                 s["price"], datetime.now().isoformat(timespec="seconds"), version,
                 "; ".join(r.explanation for r in s["reasons"][:4])))

    def calls(self, open_only=None):
        q = "SELECT * FROM calls"
        if open_only is True:
            q += " WHERE status IN ('WAITING_ENTRY','ACTIVE','T1_HIT')"
        elif open_only is False:
            q += " WHERE status NOT IN ('WAITING_ENTRY','ACTIVE','T1_HIT')"
        return self._q(q + " ORDER BY created_at DESC")

    def update_call(self, call_id: str, fields: dict):
        cols = ", ".join(f"{k}=?" for k in fields)
        self._x(f"UPDATE calls SET {cols}, updated_at=? WHERE call_id=?",
                (*fields.values(), datetime.now().isoformat(timespec="seconds"), call_id))

    def alert_history(self, limit=30):
        return self._q("SELECT timestamp, symbol, mode, setup, direction, score, decision FROM signals WHERE alerted=1 "
                       "ORDER BY timestamp DESC LIMIT ?", (limit,))

    # -- watchlist ----------------------------------------------------------
    def watchlist(self):
        return self._q("SELECT watchlist_id, symbol, mode, notes, enabled, added_at FROM watchlist ORDER BY added_at DESC")

    def add_watch(self, symbol, mode, notes=""):
        exists = self._q("SELECT 1 FROM watchlist WHERE symbol=? AND mode=?", (symbol, mode))
        if exists.empty:
            self._x("INSERT INTO watchlist VALUES (?,?,?,?,1,?)",
                    (uuid.uuid4().hex[:12], symbol, mode, notes, datetime.now().isoformat(timespec="seconds")))
            return True
        return False

    def update_watch(self, wid, notes, enabled):
        self._x("UPDATE watchlist SET notes=?, enabled=? WHERE watchlist_id=?", (notes, int(enabled), wid))

    def remove_watch(self, wid):
        self._x("DELETE FROM watchlist WHERE watchlist_id=?", (wid,))

    # -- journal ------------------------------------------------------------
    def add_journal(self, s: dict, notes=""):
        p = s.get("plan") or {}
        jid = uuid.uuid4().hex[:12]
        self._x("INSERT INTO journal VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (jid, s["signal_id"], s["symbol"], s["mode"], s["setup_label"], s["direction"], p.get("entry"), p.get("stop"),
                 p.get("t1"), None, None, p.get("qty"), None, "OPEN", notes, datetime.now().isoformat(timespec="seconds")))
        return jid

    def journal(self):
        return self._q("SELECT * FROM journal ORDER BY created_at DESC")

    def update_journal(self, row: dict):
        sign = 1 if row.get("direction") == "LONG" else -1
        pnl = None
        if row.get("entry_actual") and row.get("exit_actual") and row.get("quantity"):
            pnl = round((float(row["exit_actual"]) - float(row["entry_actual"])) * sign * int(row["quantity"]), 2)
        self._x("UPDATE journal SET entry_actual=?, exit_actual=?, quantity=?, pnl=?, outcome=?, notes=? WHERE journal_id=?",
                (row.get("entry_actual"), row.get("exit_actual"), row.get("quantity"), pnl, row.get("outcome"),
                 row.get("notes"), row["journal_id"]))

    def delete_journal(self, jid):
        self._x("DELETE FROM journal WHERE journal_id=?", (jid,))

    # -- backtests ----------------------------------------------------------
    def save_backtest(self, version, period, params, metrics):
        self._x("INSERT INTO backtests VALUES (?,?,?,?,?,?)",
                (uuid.uuid4().hex[:12], version, period, json.dumps(params), json.dumps(metrics, default=str),
                 datetime.now().isoformat(timespec="seconds")))

    def backtests(self):
        return self._q("SELECT * FROM backtests ORDER BY created_at DESC LIMIT 25")
