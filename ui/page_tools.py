"""Watchlist, Backtest, Journal, Settings and Diagnostics screens."""
from __future__ import annotations

import copy
import platform
import sys
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yaml

from scanner.backtest import run_backtest
from scanner.config import ROOT, config_hash, load_default_config
from scanner.credentials import load_credentials, save_credentials
from scanner.universe import UNIVERSES, download_index_list, universe_age_days
from scanner.setups import SETUP_LABELS, setups_for

from . import nav, state
from .components import call_card, status_bar
from .theme import tiles


# ------------------------------------------------------------------ watchlist
def watchlist_page():
    status_bar("PC Trade Scanner")
    st.markdown("### Watchlist")
    store = state.get_store()
    inst = state.instruments()
    with st.container(border=True):
        c1, c2, c3, c4 = st.columns([1.3, 1, 2.2, 0.9])
        sym = c1.selectbox("Symbol", inst["symbol"].tolist(), key="wl_sym", accept_new_options=True,
                           help="Pick from the universe or type any NSE symbol, e.g. BDL")
        mode = c2.segmented_control("Mode", ["INTRADAY", "SWING"], default="SWING", format_func=str.title, key="wl_mode") or "SWING"
        notes = c3.text_input("Notes", key="wl_notes", placeholder="Why you are watching it")
        c4.write("")
        if c4.button("Add", type="primary", width="stretch"):
            sym = (sym or "").strip().upper()
            st.toast(f"{sym} added" if store.add_watch(sym, mode, notes) else f"{sym} is already on the {mode.lower()} watchlist")
    wl = store.watchlist()
    if wl.empty:
        st.markdown('<div class="empty">Nothing on the watchlist yet. Add a symbol above or use "Add to watchlist" on any card.</div>',
                    unsafe_allow_html=True)
        return
    wl["enabled"] = wl["enabled"].astype(bool)
    wl["remove"] = False
    edited = st.data_editor(wl, hide_index=True, width="stretch", key="wl_editor",
                            disabled=["watchlist_id", "symbol", "mode", "added_at"],
                            column_config={"watchlist_id": None, "enabled": st.column_config.CheckboxColumn("Scan"),
                                           "remove": st.column_config.CheckboxColumn("Remove")})
    if st.button("Save watchlist changes"):
        for _, r in edited.iterrows():
            if r["remove"]:
                store.remove_watch(r["watchlist_id"])
            else:
                store.update_watch(r["watchlist_id"], r["notes"], r["enabled"])
        st.toast("Watchlist saved")
        st.rerun()
    st.markdown("#### Latest status of watched symbols")
    shown = 0
    for _, r in wl[wl["enabled"]].iterrows():
        sig, wdf = state.find_signal(r["mode"], r["symbol"])
        if sig:
            call_card(sig, f"wl_{r['watchlist_id']}", nav.PAGES.get("detail"), df=wdf)
            shown += 1
    if not shown:
        st.caption("Run the intraday or swing scanner to see live status for watched symbols.")


# ------------------------------------------------------------------- backtest
def backtest_page():
    status_bar("PC Trade Scanner")
    st.markdown("### Backtest")
    st.caption("Replays the same rule engine used by the live scanner on completed candles, with next-candle stop entry, "
               "costs, and an in-sample / out-of-sample split. Past results do not predict future results.")
    cfg, version = state.active_config()
    inst = state.instruments()
    with st.form("bt_form"):
        c1, c2, c3 = st.columns(3)
        mode = c1.selectbox("Mode", ["INTRADAY", "SWING"], format_func=str.title)
        keys = sorted({k for k, _, _ in setups_for("INTRADAY")} | {k for k, _, _ in setups_for("SWING")})
        setup = c2.selectbox("Setup", keys, format_func=lambda k: SETUP_LABELS[k])
        direction = c3.selectbox("Direction", ["LONG", "SHORT"], format_func=str.title)
        syms = st.multiselect("Symbols", inst["symbol"].tolist(), default=inst["symbol"].tolist()[:8])
        c4, c5, c6, c7, c8 = st.columns(5)
        target = c4.selectbox("Exit target", ["t1", "t2"], format_func=str.upper)
        valid = c5.number_input("Entry valid (bars)", 1, 10, 3)
        hold = c6.number_input("Max hold (bars)", 1, 500, 75 if mode == "INTRADAY" else 20)
        cost = c7.number_input("Costs + slippage (bps/side)", 0.0, 100.0, 5.0, 0.5)
        oos = c8.slider("Out-of-sample %", 10, 50, 30, 5)
        same_bar = st.radio("If stop and target hit in the same candle", ["stop_first", "target_first"], horizontal=True,
                            format_func=lambda x: "Assume stop first (conservative)" if x == "stop_first" else "Assume target first")
        go_ = st.form_submit_button("Run backtest", type="primary")
    if go_:
        valid_keys = {k for k, _, _ in setups_for(mode)}
        dirs = {k: d for k, _, d in setups_for(mode)}
        if setup not in valid_keys or direction not in dirs[setup]:
            st.error(f"{SETUP_LABELS[setup]} ({direction.title()}) is not a {mode.title()} setup. Pick another combination.")
            return
        prog = st.progress(0.0, "Starting…")
        trades, m, split = run_backtest(state.provider(), syms, mode, setup, direction, cfg, target, int(valid), int(hold),
                                        cost, same_bar, oos / 100, progress=lambda f, s: prog.progress(f, f"Replaying {s}…"))
        prog.empty()
        params = {"mode": mode, "setup": setup, "direction": direction, "symbols": syms, "target": target,
                  "entry_valid": valid, "max_hold": hold, "cost_bps": cost, "same_bar": same_bar, "oos_frac": oos / 100}
        period = f"{trades['signal_time'].min()} to {trades['signal_time'].max()}" if not trades.empty else "no trades"
        state.get_store().save_backtest(version, period, params, m)
        st.session_state["bt"] = (trades, m, split, params, version)
    if "bt" not in st.session_state:
        return
    trades, m, split, params, ver = st.session_state["bt"]
    a = m["all"]
    st.markdown(tiles([
        ("Trades", a["trades"], "#", "#E6ECFA", "#2F5BD3"), ("Win rate", f"{a['win_rate']}%", "%", "#E3F4EC", "#13885F"),
        ("Expectancy", f"{a['expectancy_R']:+.2f}R", "R", "#FBF0DC", "#B7791F"),
        ("Profit factor", a["profit_factor"], "×", "#EDE7FA", "#7B4FC9"),
        ("Max drawdown", f"{a['max_drawdown_R']}R", "↓", "#FBE7E5", "#C9372C"), ("Exposure", f"{a['exposure_pct']}%", "◔", "#EBEEF3", "#4E5A6E"),
    ]), unsafe_allow_html=True)
    st.caption(f"{SETUP_LABELS[params['setup']]} ({params['direction'].title()}) on {len(params['symbols'])} symbols, "
               f"strategy config v{ver}" + (f", out-of-sample from {split:%d %b %Y %H:%M}" if split is not None else ""))
    if trades.empty:
        st.markdown('<div class="empty">No qualifying trades in this period. Try more symbols, the other direction, '
                    'or a lower buy threshold in Settings.</div>', unsafe_allow_html=True)
        return
    st.dataframe(pd.DataFrame(m).T.rename(index={"all": "All", "in_sample": "In-sample", "out_of_sample": "Out-of-sample"}),
                 width="stretch")
    c1, c2 = st.columns([2, 1])
    fig = go.Figure()
    for smp, col in (("In-sample", "#2F5BD3"), ("Out-of-sample", "#E08A1E")):
        t = trades[trades["sample"] == smp]
        fig.add_trace(go.Scatter(x=t["exit_time"].astype(str), y=t["cum_R"], mode="lines+markers", name=smp,
                                 line=dict(color=col), marker=dict(size=4)))
    fig.update_layout(height=340, margin=dict(l=10, r=10, t=30, b=10), title="Cumulative R", paper_bgcolor="#fff",
                      plot_bgcolor="#fff", xaxis=dict(type="category", nticks=8), yaxis=dict(gridcolor="#EEF1F6"))
    c1.plotly_chart(fig, width="stretch")
    h = go.Figure(go.Histogram(x=trades["R"], nbinsx=30, marker_color="#2F5BD3"))
    h.update_layout(height=340, margin=dict(l=10, r=10, t=30, b=10), title="R distribution", paper_bgcolor="#fff", plot_bgcolor="#fff")
    c2.plotly_chart(h, width="stretch")
    st.markdown("#### Trade log")
    st.dataframe(trades, hide_index=True, width="stretch", height=320)
    st.download_button("Export trade log (CSV)", trades.to_csv(index=False), "backtest_trades.csv", "text/csv")
    with st.expander("Backtest history"):
        st.dataframe(state.get_store().backtests(), hide_index=True, width="stretch")


# -------------------------------------------------------------------- journal
def journal_page():
    status_bar("PC Trade Scanner")
    st.markdown("### Trade journal")
    store = state.get_store()
    j = store.journal()
    if j.empty:
        st.markdown('<div class="empty">No journal entries yet. Use "Save to journal" on any candidate to record the plan, '
                    'then fill in your actual entry and exit here.</div>', unsafe_allow_html=True)
        return
    closed = j[j["pnl"].notna()]
    st.markdown(tiles([
        ("Entries", len(j), "✎", "#E6ECFA", "#2F5BD3"), ("Open", int((j["outcome"] == "OPEN").sum()), "◷", "#FBF0DC", "#B7791F"),
        ("Closed", len(closed), "✓", "#E3F4EC", "#13885F"),
        ("Net P&L (gross)", f"₹{closed['pnl'].sum():,.0f}" if not closed.empty else "₹0", "₹", "#EDE7FA", "#7B4FC9"),
    ]), unsafe_allow_html=True)
    j["delete"] = False
    edited = st.data_editor(
        j, hide_index=True, width="stretch", key="jr_editor",
        disabled=["journal_id", "signal_id", "symbol", "mode", "setup", "direction", "planned_entry", "planned_stop",
                  "planned_t1", "pnl", "created_at"],
        column_config={"journal_id": None, "signal_id": None,
                       "outcome": st.column_config.SelectboxColumn("Outcome", options=["OPEN", "WIN", "LOSS", "BREAKEVEN", "SKIPPED"]),
                       "pnl": st.column_config.NumberColumn("P&L ₹", format="₹%.0f"),
                       "delete": st.column_config.CheckboxColumn("Delete")})
    c1, c2 = st.columns([1, 4])
    if c1.button("Save journal", type="primary"):
        for _, r in edited.iterrows():
            if r["delete"]:
                store.delete_journal(r["journal_id"])
            else:
                store.update_journal({k: (None if pd.isna(v) else v) for k, v in r.to_dict().items()})
        st.toast("Journal saved")
        st.rerun()
    c2.download_button("Export journal (CSV)", j.drop(columns=["delete"]).to_csv(index=False), "trade_journal.csv", "text/csv")
    st.caption("P&L is calculated as (exit - entry) x quantity, reversed for shorts. Brokerage and taxes are not included.")


# ------------------------------------------------------------------- settings
def _num(container, label, sec, key, cfg, step=None, fmt=None):
    v = cfg[sec][key]
    if isinstance(v, bool):
        cfg[sec][key] = container.toggle(label, v, key=f"set_{sec}_{key}")
    elif isinstance(v, int):
        cfg[sec][key] = int(container.number_input(label, value=v, step=step or 1, key=f"set_{sec}_{key}"))
    elif isinstance(v, float):
        cfg[sec][key] = float(container.number_input(label, value=v, step=step or 0.1, format=fmt, key=f"set_{sec}_{key}"))
    else:
        cfg[sec][key] = container.text_input(label, v, key=f"set_{sec}_{key}")


def settings_page():
    status_bar("PC Trade Scanner")
    st.markdown("### Settings")
    st.caption("Thresholds can only be changed here. Saving creates a new config version; every scan and backtest records the version it used.")
    store = state.get_store()
    cfg0, version = state.active_config()
    cfg = copy.deepcopy(cfg0)
    tabs = st.tabs(["Data source & universe", "Session", "Indicators", "Setups", "Scoring & gates", "Risk", "Alerts"])
    with tabs[0]:
        _data_source_tab(cfg)
    with tabs[1]:
        c1, c2 = st.columns(2)
        _num(c1, "Session open (HH:MM)", "session", "open", cfg)
        _num(c2, "Session close (HH:MM)", "session", "close", cfg)
        _num(c1, "Skip first N minutes", "session", "start_delay_minutes", cfg)
        _num(c2, "No new entries in last N minutes", "session", "end_buffer_minutes", cfg)
    with tabs[2]:
        cols = st.columns(3)
        for n, key in enumerate(cfg["indicators"]):
            _num(cols[n % 3], key.replace("_", " ").capitalize(), "indicators", key, cfg)
    with tabs[3]:
        cols = st.columns(3)
        for n, key in enumerate(cfg["setups"]):
            _num(cols[n % 3], key.replace("_", " ").capitalize(), "setups", key, cfg, step=0.05 if isinstance(cfg["setups"][key], float) else None)
    with tabs[4]:
        cols = st.columns(3)
        for n, key in enumerate(cfg["scoring"]):
            _num(cols[n % 3], key.replace("_", " ").capitalize(), "scoring", key, cfg)
        if cfg["scoring"]["min_rr"] < 1.5:
            st.warning("Minimum R:R below the 1.5 hard floor recommended in the requirements.")
    with tabs[5]:
        c1, c2, c3 = st.columns(3)
        cfg["risk"]["capital"] = float(c1.number_input("Capital ₹", value=float(cfg["risk"]["capital"]), step=10000.0, format="%.0f"))
        cfg["risk"]["risk_percent"] = float(c2.number_input("Risk per trade %", value=float(cfg["risk"]["risk_percent"]), step=0.05,
                                                            min_value=0.05, max_value=5.0))
        cfg["risk"]["target2_r"] = float(c3.number_input("T2 at R multiple", value=float(cfg["risk"]["target2_r"]), step=0.5))
        cfg["risk"]["max_daily_loss_pct"] = float(c1.number_input("Max loss per day %", value=float(cfg["risk"]["max_daily_loss_pct"]), step=0.25))
        cfg["risk"]["max_trades_per_day"] = int(c2.number_input("Max trades per day", value=int(cfg["risk"]["max_trades_per_day"]), step=1))
    with tabs[6]:
        c1, c2, c3 = st.columns(3)
        _num(c1, "Alerts enabled", "alerts", "enabled", cfg)
        _num(c2, "Desktop notifications (needs plyer)", "alerts", "desktop_notifications", cfg)
        _num(c3, "Cooldown (minutes)", "alerts", "cooldown_minutes", cfg)
    changed = config_hash(cfg) != config_hash(cfg0)
    c1, c2, c3 = st.columns([1.2, 1.2, 3])
    note = c3.text_input("Change note", placeholder="e.g. raised buy threshold to 80", label_visibility="collapsed")
    if c1.button("Save new version", type="primary", disabled=not changed):
        v = store.save_config(cfg, note or "edited in Settings")
        store.event("INFO", "settings", f"Config saved as v{v}")
        st.session_state.pop("scans", None)
        st.toast(f"Saved config v{v}. Scans will re-run with it.")
        st.rerun()
    if c2.button("Reset to defaults"):
        store.save_config(load_default_config(), "reset to defaults")
        st.session_state.pop("scans", None)
        st.rerun()
    with st.expander("Version history"):
        st.dataframe(store.config_history(), hide_index=True, width="stretch")
    with st.expander("Active configuration (YAML)"):
        st.code(yaml.safe_dump(cfg0, sort_keys=False), language="yaml")


# ---------------------------------------------------------------- diagnostics
def startup_checks() -> list[tuple[str, bool, str]]:
    out = [("Python 3.11+", sys.version_info >= (3, 11), platform.python_version())]
    try:
        state.get_store().event("INFO", "startup", "Storage check")
        out.append(("Local database writable", True, str(state.DB_PATH)))
    except Exception as exc:
        out.append(("Local database writable", False, repr(exc)))
    err = state.provider_error()
    if err:
        out.append(("Data provider", False, err))
        return out
    try:
        prov = state.provider()
        inst = prov.get_instruments()
        out.append(("Universe", len(inst) > 0, f"{len(inst)} symbols ({state.active_config()[0]['universe']['name']})"))
        if hasattr(prov, "test_connection"):
            ok, msg = prov.test_connection()
            out.append((f"Provider: {prov.name}", ok, msg))
        elif len(inst):
            df = prov.get_historical_ohlcv(inst["symbol"].iloc[0], "1D")
            out.append(("Provider daily candles", len(df) > 0, f"{len(df)} bars for {inst['symbol'].iloc[0]}"))
    except Exception as exc:
        out.append(("Provider connectivity", False, repr(exc)))
    return out


def diagnostics_page():
    status_bar("PC Trade Scanner")
    st.markdown("### Logs and diagnostics")
    checks = startup_checks()
    st.dataframe(pd.DataFrame(checks, columns=["Check", "OK", "Detail"]), hide_index=True, width="stretch")
    import numpy, plotly  # noqa: E401
    st.caption(f"Streamlit {st.__version__}, pandas {pd.__version__}, numpy {numpy.__version__}, plotly {plotly.__version__}, "
               f"{platform.system()} {platform.release()}")
    st.markdown("#### Data quality by symbol (latest scans)")
    rows = []
    for mode, res in state.scans().items():
        for s in res["signals"]:
            dq = s.get("dq") or {}
            rows.append({"Mode": mode.title(), "Symbol": s["symbol"], "Data OK": dq.get("ok"),
                         "Flags": ", ".join(dq.get("flags", [])), "Last candle": str(dq.get("last_ts")),
                         "Age (min)": None if dq.get("age_min") is None else round(dq["age_min"], 1),
                         "Missing gaps": dq.get("missing"), "Error": s.get("error", "")})
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=300)
    else:
        st.caption("No scans in this session yet.")
    if not state.provider_error() and getattr(state.provider(), "errors", None):
        st.markdown("#### Provider errors (last scan)")
        st.dataframe(pd.DataFrame(list(state.provider().errors.items()), columns=["Symbol", "Error"]), hide_index=True, width="stretch")
    st.markdown("#### Scan runs")
    st.dataframe(state.get_store().scan_runs(), hide_index=True, width="stretch")
    st.markdown("#### Application events")
    st.dataframe(state.get_store().events(), hide_index=True, width="stretch", height=260)


PROVIDER_HELP = {
    "upstox": "Official Upstox API. Free; candle data needs no login or account keys. Recommended default.",
    "kite": "Official Zerodha Kite Connect API. Needs a Kite Connect app (API key) and a fresh access token each day.",
    "yahoo": "Yahoo Finance via yfinance. Works without keys, but it is unofficial, for personal use only, and intraday "
             "data may be delayed. Fine for swing scans; do not rely on it for live intraday entries.",
    "csv": "Your own licensed OHLCV files in the CSV folder.",
}


def _data_source_tab(cfg):
    names = {"upstox": "Upstox (recommended)", "kite": "Zerodha Kite", "yahoo": "Yahoo Finance", "csv": "CSV files"}
    cfg["data"]["provider"] = st.radio("Market data provider", list(names), index=list(names).index(cfg["data"]["provider"])
                                       if cfg["data"]["provider"] in names else 0, format_func=names.get, horizontal=True)
    st.caption(PROVIDER_HELP[cfg["data"]["provider"]] + " TradingView and Chartink are not options: neither has a public "
               "data API and scraping them breaks their terms.")
    creds = load_credentials(ROOT)
    if cfg["data"]["provider"] == "kite":
        with st.container(border=True):
            st.markdown("**Kite Connect credentials** (stored only in data/credentials.json on this PC)")
            c1, c2 = st.columns(2)
            key = c1.text_input("API key", creds.get("kite_api_key", ""))
            tok = c2.text_input("Access token (valid for today)", creds.get("kite_access_token", ""), type="password")
            st.caption("Get the access token each morning by logging in through your Kite Connect app "
                       "(kite.trade/docs/connect/v3/user/). Environment variables KITE_API_KEY / KITE_ACCESS_TOKEN also work.")
            if st.button("Save Kite credentials"):
                save_credentials(ROOT, {"kite_api_key": key, "kite_access_token": tok})
                st.cache_resource.clear()
                st.toast("Saved. Reconnecting with the new token.")
    if cfg["data"]["provider"] == "upstox":
        with st.expander("Optional: Upstox access token"):
            tok = st.text_input("Access token", creds.get("upstox_access_token", ""), type="password",
                                help="Not needed for candle data. Only add it if Upstox starts requiring it.")
            if st.button("Save Upstox token"):
                save_credentials(ROOT, {"upstox_access_token": tok})
                st.cache_resource.clear()
                st.toast("Saved")
    if cfg["data"]["provider"] == "csv":
        cfg["data"]["csv_folder"] = st.text_input("CSV folder (relative to the app folder or absolute)", cfg["data"]["csv_folder"])
        st.caption("<SYMBOL>_5m.csv and <SYMBOL>_1D.csv with columns timestamp,open,high,low,close,volume. "
                   "Optional instruments.csv with symbol,exchange,sector,lot_size,tick_size.")
    if st.button("Test connection with the saved provider"):
        err = state.provider_error()
        if err:
            st.error(err)
        else:
            prov = state.provider()
            ok, msg = prov.test_connection() if hasattr(prov, "test_connection") else (True, "CSV provider: no connection needed")
            (st.success if ok else st.error)(msg)
    st.divider()
    st.markdown("**Scan universe**")
    c1, c2 = st.columns([1.2, 2])
    cfg["universe"]["name"] = c1.selectbox("Universe", UNIVERSES, index=UNIVERSES.index(cfg["universe"]["name"])
                                           if cfg["universe"]["name"] in UNIVERSES else 0)
    if cfg["universe"]["name"] == "Custom list":
        cfg["universe"]["custom_symbols"] = c2.text_area("NSE symbols, comma separated", cfg["universe"]["custom_symbols"], height=90)
    else:
        age = universe_age_days(cfg, ROOT)
        c2.caption("NIFTY 50 is bundled; other lists download from niftyindices.com on first use. "
                   + (f"Cached list is {age} day(s) old." if age is not None else "Using the bundled list."))
        if c2.button("Refresh list from niftyindices.com"):
            try:
                df = download_index_list(cfg["universe"]["name"], ROOT)
                st.cache_resource.clear()
                st.success(f"Downloaded {len(df)} symbols")
            except Exception as exc:
                st.error(f"Download failed: {exc}. The bundled/cached list stays in use.")
    st.caption("Larger universes take longer: NIFTY 500 needs about 1,000 candle requests per scan.")
    c1, c2, c3 = st.columns(3)
    for n, (lbl, key) in enumerate([("Max intraday staleness (min)", "max_staleness_minutes_intraday"),
                                    ("Max swing staleness (trading days)", "max_staleness_days_swing"),
                                    ("Min intraday bars", "min_bars_intraday"), ("Min swing bars", "min_bars_swing"),
                                    ("Intraday history (days)", "intraday_history_days"), ("Daily history (days)", "daily_history_days"),
                                    ("Parallel downloads", "max_workers"), ("Intraday cache (s)", "cache_seconds_intraday"),
                                    ("Daily cache (s)", "cache_seconds_daily")]):
        _num([c1, c2, c3][n % 3], lbl, "data", key, cfg)
    _num(st, "Early candle mode (allow signals on the forming candle)", "data", "early_candle_mode", cfg)
