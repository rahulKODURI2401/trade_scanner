"""Dashboard, Intraday scanner and Swing/Daily scanner screens."""
from __future__ import annotations

import io
from datetime import datetime

import pandas as pd
import streamlit as st

from . import nav, state
from .components import apply_filters, call_card, filter_panel, status_bar, summary_tiles

PAGE_SIZE = 6
DISCLAIMER = ("Live market data. Rule-based decision support. The score is a screening score, not a probability of profit, "
              "and a BUY CANDIDATE is not a guarantee or personalised investment advice.")


def _universe(mode: str, source: str) -> pd.DataFrame:
    inst = state.instruments()
    if source == "Watchlist":
        wl = state.get_store().watchlist()
        wl = wl[(wl["enabled"] == 1) & (wl["mode"] == mode)]
        extra = pd.DataFrame({"symbol": wl["symbol"].unique()})
        out = extra.merge(inst, on="symbol", how="left")
        out["sector"] = out["sector"].fillna("Watchlist")
        out["lot_size"] = out.get("lot_size", 1)
        return out.fillna({"lot_size": 1, "exchange": "NSE"})
    return inst


def _export_buttons(signals, key):
    df = state.signals_frame(signals)
    c1, c2 = st.columns(2)
    c1.download_button("Export CSV", df.to_csv(index=False).encode(), f"scan_{key}_{datetime.now():%Y%m%d_%H%M}.csv",
                       "text/csv", key=f"{key}_csv", width="stretch")
    buf = io.BytesIO()
    out = df.copy()
    if "Bar time" in out:
        out["Bar time"] = out["Bar time"].astype(str)
    out.to_excel(buf, index=False, sheet_name="scan")
    c2.download_button("Export Excel", buf.getvalue(), f"scan_{key}_{datetime.now():%Y%m%d_%H%M}.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key=f"{key}_xlsx",
                       width="stretch")


def _results(signals, frames, key):
    left, right = st.columns([1, 3.1], gap="medium")
    with left:
        f = filter_panel(key, signals, show_mode=(key == "dash"))
        _export_buttons(signals, key)
    shown = apply_filters(signals, f, frames)
    with right:
        h1, h2, h3 = st.columns([2.2, 1.6, 1.4])
        h1.markdown(f"#### Candidates ({len(shown)})")
        view = h2.segmented_control("View", ["Cards", "Table"], default="Cards", key=f"{key}_view",
                                    label_visibility="collapsed") or "Cards"
        pages = max(1, -(-len(shown) // PAGE_SIZE))
        page = h3.selectbox("Page", list(range(1, pages + 1)), key=f"{key}_page", label_visibility="collapsed",
                            format_func=lambda n: f"Page {n} of {pages}") if view == "Cards" and pages > 1 else 1
        if not shown:
            st.markdown('<div class="empty">No symbols match these filters. Widen the decision filter or lower the '
                        'minimum score to see WAIT and NO BUY explanations.</div>', unsafe_allow_html=True)
            return
        if view == "Table":
            df = state.signals_frame(shown)
            ev = st.dataframe(
                df.drop(columns=["Config"]), hide_index=True, width="stretch", height=520,
                on_select="rerun", selection_mode="single-row", key=f"{key}_table",
                column_config={
                    "Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%d"),
                    "Price": st.column_config.NumberColumn(format="₹%.2f"), "Entry": st.column_config.NumberColumn(format="₹%.2f"),
                    "SL": st.column_config.NumberColumn(format="₹%.2f"), "T1": st.column_config.NumberColumn(format="₹%.2f"),
                    "T2": st.column_config.NumberColumn(format="₹%.2f"), "R:R": st.column_config.NumberColumn(format="1:%.2f"),
                    "Risk ₹": st.column_config.NumberColumn(format="₹%.0f"),
                })
            rows = ev.selection.rows if ev and ev.selection else []
            if rows:
                sel = shown[rows[0]]
                st.caption(f"Selected {sel['symbol']}")
                call_card(sel, f"{key}_tsel", nav.PAGES.get("detail"), df=state.find_signal(sel["mode"], sel["symbol"])[1])
            else:
                st.caption("Select a row to preview it as a card and open the full signal detail.")
        else:
            for n, s in enumerate(shown[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]):
                call_card(s, f"{key}_{s['mode']}_{s['symbol']}_{n}", nav.PAGES.get("detail"),
                          df=state.find_signal(s["mode"], s["symbol"])[1])


def scanner_page(mode: str):
    title = "Intraday scanner" if mode == "INTRADAY" else "Swing / daily scanner"
    bar_slot = st.empty()
    cfg, _ = state.active_config()
    tf = cfg["timeframes"]["intraday_primary"] if mode == "INTRADAY" else cfg["timeframes"]["swing_primary"]
    st.markdown(f"### {title}")
    st.caption((f"{tf} primary timeframe on completed candles. Setups: Pivot + VWAP, Supertrend + 20 EMA, "
                "9/21 EMA + Supertrend, Bollinger squeeze." if mode == "INTRADAY" else
                f"{tf} primary timeframe. Setups: 20/50/200 trend pullback, RSI and MACD divergence, "
                "Bollinger contra, gap retest, Fibonacci pullback, Bollinger squeeze.") + " " + DISCLAIMER)

    c1, c2, c3, c4 = st.columns([1.3, 1.1, 1.3, 1.2])
    source = c1.segmented_control("Universe", ["Full universe", "Watchlist"], default="Full universe",
                                  key=f"{mode}_src") or "Full universe"
    run = c2.button("Run scan", type="primary", width="stretch", key=f"{mode}_run")
    auto = c3.toggle("Auto-refresh", key=f"{mode}_auto", value=False)
    every = c4.selectbox("Every", [60, 120, 300, 900], index=2 if mode == "INTRADAY" else 3,
                         format_func=lambda s: f"{s // 60} min", key=f"{mode}_every", disabled=not auto,
                         label_visibility="collapsed")

    universe = _universe(mode, source)
    if universe.empty:
        with bar_slot.container():
            status_bar("PC Trade Scanner")
        st.markdown('<div class="empty">Your watchlist has no enabled symbols for this mode. Add symbols from any '
                    'card or from the Watchlist screen.</div>', unsafe_allow_html=True)
        return
    existing = state.scans().get(mode)
    if run or existing is None or existing.get("universe") != list(universe["symbol"]):
        with st.spinner(f"Scanning {len(universe)} symbols…"):
            state.run_scan(mode, universe)
    with bar_slot.container():
        status_bar("PC Trade Scanner")

    @st.fragment(run_every=every if auto else None)
    def live():
        res = state.scans()[mode]
        if auto and (datetime.now(res["at"].tzinfo) - res["at"]).total_seconds() >= every - 1 and not state.provider().is_demo:
            state.run_scan(mode, universe)
            res = state.scans()[mode]
        summary_tiles(res["signals"])
        st.caption(f"Scanned {len(res['signals'])} symbols in {res['elapsed']:.1f}s, run {res['run_id']}, "
                   f"config v{res['version']}. New alerts this run: {len(res['alerts'])}.")
        _results(res["signals"], res["frames"], mode.lower())

    live()


def intraday_page():
    scanner_page("INTRADAY")


def swing_page():
    scanner_page("SWING")


def dashboard_page():
    inst = state.instruments()
    for mode in ("INTRADAY", "SWING"):
        if mode not in state.scans():
            with st.spinner(f"Running first {mode.lower()} scan…"):
                state.run_scan(mode, inst)
    status_bar("PC Trade Scanner")
    st.markdown("### Dashboard")
    st.caption("Best setups across intraday and swing, with every decision explained. " + DISCLAIMER)
    sigs = state.scans()["INTRADAY"]["signals"] + state.scans()["SWING"]["signals"]
    frames = {}
    summary_tiles(sigs)
    today = datetime.now().strftime("%Y-%m-%d")
    hist = state.get_store().signals_on(today)
    cand = hist[hist["decision"].isin(["BUY CANDIDATE", "SELL CANDIDATE"])]
    b1, b2, b3 = st.columns([1, 1, 3])
    b1.metric("Signals today", cand[["symbol", "setup"]].drop_duplicates().shape[0])
    b2.metric("Scans today", hist["run_id"].nunique() if not hist.empty else 0)
    if b3.button("Refresh both scans", type="primary"):
        for mode in ("INTRADAY", "SWING"):
            state.run_scan(mode, inst)
        st.rerun()
    _results(sigs, frames, "dash")
    st.markdown("#### Recent alerts")
    alerts = state.get_store().alert_history()
    if alerts.empty:
        st.caption("No alerts yet. An alert fires the first time a symbol becomes a BUY or SELL candidate.")
    else:
        st.dataframe(alerts, hide_index=True, width="stretch")
