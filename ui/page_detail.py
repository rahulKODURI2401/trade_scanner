"""Signal detail: chart, score drill-down, reasons, failed rules, risk plan."""
from __future__ import annotations

import json
import math

import pandas as pd
import streamlit as st

from scanner.models import CATEGORY_WEIGHTS

from . import state
from .components import OVERLAYS, call_card, signal_chart, status_bar
from .theme import DECISION_STYLE, bar, esc, inr, rule_rows

CAT_COLORS = {"Trend": "#2F5BD3", "Momentum": "#7B4FC9", "Price action": "#E08A1E", "Volume": "#0F8FA3",
              "S&R / location": "#13885F", "Volatility": "#98A6BD", "Risk / reward": "#C23A8B"}


def _picker():
    sel = st.session_state.get("detail") or {}
    c1, c2 = st.columns([1, 2])
    mode = c1.segmented_control("Mode", ["INTRADAY", "SWING"], default=sel.get("mode", "INTRADAY"),
                                format_func=str.title, key="det_mode") or "INTRADAY"
    if mode not in state.scans():
        with st.spinner("Running scan…"):
            state.run_scan(mode, state.instruments())
    syms = [s["symbol"] for s in state.scans()[mode]["signals"]]
    default = sel.get("symbol") if sel.get("symbol") in syms else syms[0]
    sym = c2.selectbox("Symbol", syms, index=syms.index(default), key=f"det_sym_{mode}_{default}")
    st.session_state["detail"] = {"mode": mode, "symbol": sym}
    return mode, sym


def detail_page():
    bar_slot = st.empty()
    st.markdown("### Signal detail")
    mode, sym = _picker()
    with bar_slot.container():
        status_bar("PC Trade Scanner")
    sig, df = state.find_signal(mode, sym)
    if sig is None:
        st.info("Run a scan first.")
        return
    if df is None:
        st.error(f"{sym}: {sig.get('error', 'no data')}. See Diagnostics for details.")
        return
    style = DECISION_STYLE[sig["decision"]]
    bt = sig["bar_time"]
    st.caption(f"{sig.get('exchange', 'NSE')}:{sym}  |  {sig['timeframe']}  |  {sig['setup']}  |  "
               f"last completed candle {bt:%d %b %Y %H:%M}  |  config v{sig.get('config_version', '')}")
    call_card(sig, "detail_card", actions=False)

    a1, a2, a3, a4 = st.columns(4)
    if a1.button("Add to watchlist", width="stretch"):
        ok = state.get_store().add_watch(sym, mode, f"From detail: {sig['setup_label']}")
        st.toast("Added to watchlist" if ok else "Already on the watchlist")
    if a2.button("Create journal entry", width="stretch", disabled=not sig.get("plan")):
        state.get_store().add_journal(sig)
        st.toast("Journal entry created")
    export = {k: v for k, v in sig.items() if k not in ("score_rules", "gates", "vetoes", "reasons", "flags", "dq")}
    export["rules"] = [r.as_dict() for r in sig["score_rules"] + sig["gates"] + sig["vetoes"]]
    a3.download_button("Export signal (JSON)", json.dumps(export, default=str, indent=2), f"signal_{sym}_{mode.lower()}.json",
                       "application/json", width="stretch")
    a4.download_button("Export rules (CSV)", pd.DataFrame(export["rules"]).to_csv(index=False), f"signal_{sym}_rules.csv",
                       "text/csv", width="stretch")

    default_ov = ["20 EMA", "VWAP", "Supertrend", "Pivot"] if mode == "INTRADAY" else ["20 EMA", "50 SMA", "200 SMA", "S/R"]
    ov = st.pills("Overlays", OVERLAYS, selection_mode="multi", default=default_ov, key=f"ov_{mode}")
    c1, c2, c3 = st.columns([2, 1.2, 1])
    n = c1.slider("Candles shown", 40, min(400, len(df)), min(150, len(df)), 10, key=f"bars_{mode}")
    lower = c2.segmented_control("Lower panel", ["Volume", "RSI", "MACD", "None"], default="Volume", key=f"low_{mode}") or "Volume"
    proj = c3.toggle("Show projection", value=True, key=f"proj_{mode}",
                     help="Planned reward/risk zones and a 1-ATR expected-range cone for the next candles. "
                          "A range of plausible movement, not a forecast.")
    st.plotly_chart(signal_chart(df, sig, ov or [], n, lower, project=12 if proj else 0), width="stretch",
                    config={"displaylogo": False})

    left, right = st.columns([2, 1], gap="medium")
    with left:
        t1, t2, t3, t4, t5 = st.tabs(["Why this signal", "Why not buy?", "Setup checklist", "Hard gates", "Data quality"])
        with t1:
            pos = [r for r in sig["score_rules"] if r.points > 0]
            st.markdown(f'<div class="panel">{rule_rows(pos, True) or "No positive evidence."}</div>', unsafe_allow_html=True)
        with t2:
            items = sig["why_not"] or ["Nothing blocks this signal. All hard gates pass and the score is above threshold."]
            st.markdown('<div class="panel">' + "".join(f'<div class="rule"><span class="no">•</span><span>{esc(w)}</span></div>'
                                                        for w in items) + "</div>", unsafe_allow_html=True)
            weak = [r for r in sig["score_rules"] if r.points < r.max_points]
            if weak:
                st.markdown("**Points not earned**")
                st.markdown(f'<div class="panel">{rule_rows(weak, True)}</div>', unsafe_allow_html=True)
        with t3:
            st.markdown(f"**{sig['setup']}** ({sig['direction'].title()}), stage {sig['stage']}")
            st.markdown(f'<div class="panel">{rule_rows(sig["gates"])}</div>', unsafe_allow_html=True)
            st.markdown("**Every setup evaluated on this candle**")
            st.dataframe(pd.DataFrame(sig["all_setups"]), hide_index=True, width="stretch")
        with t4:
            st.caption("Any failed hard gate forces NO BUY regardless of score.")
            st.markdown(f'<div class="panel">{rule_rows(sig["vetoes"])}</div>', unsafe_allow_html=True)
        with t5:
            dq = sig["dq"]
            st.markdown(f'<div class="panel">{rule_rows(dq["rules"])}</div>', unsafe_allow_html=True)
            st.caption(f"Last candle {dq['last_ts']}; forming candle {'excluded' if not dq['complete_last'] else 'none'}.")

    with right:
        cats = sig["categories"]
        html = ['<div class="panel"><h4>Score breakdown</h4>',
                f'<div style="font-size:2rem;font-weight:700;color:{style["fg"]}">{sig["score"]}'
                f'<span style="font-size:.9rem;color:#667085;font-weight:500"> / 100</span></div>']
        for c, w in CATEGORY_WEIGHTS.items():
            v = cats.get(c, 0)
            html.append(f'<div style="display:flex;justify-content:space-between;font-size:.84rem;margin-top:.55rem">'
                        f'<span>{c}</span><span class="num">{v:g} / {w}</span></div>{bar(v / w, CAT_COLORS[c])}')
        html.append('<div class="note">Open a category below to see the calculation behind each point.</div></div>')
        st.markdown("".join(html), unsafe_allow_html=True)
        for c in CATEGORY_WEIGHTS:
            rules = [r for r in sig["score_rules"] if r.category == c]
            with st.expander(f"{c}: {cats.get(c, 0):g} / {CATEGORY_WEIGHTS[c]}"):
                for r in rules:
                    val = f" (value {r.value:.2f}, threshold {r.threshold})" if r.value is not None and r.threshold else ""
                    st.markdown(f"- `{r.code}`: {r.explanation}{val}: **{r.points:g}/{r.max_points:g}**")
        _risk_panel(sig)
        st.markdown(f'<div class="panel" style="margin-top:.8rem"><h4>Market regime</h4>'
                    f'<div class="kv"><span>Trend</span><span>{esc(sig["regime"]["trend"])}</span>'
                    f'<span>Volatility</span><span>{esc(sig["regime"]["vol"])}</span></div>'
                    f'<div class="note">Regime is a filter, not a trade signal.</div></div>', unsafe_allow_html=True)
        _track_record(sig)


def _risk_panel(sig):
    p = sig.get("plan")
    st.write("")
    if not p:
        return
    cfg, _ = state.active_config()
    with st.container(border=True):
        st.markdown("**Risk and position size**")
        c1, c2 = st.columns(2)
        cap = c1.number_input("Capital ₹", 1000.0, 1e9, float(cfg["risk"]["capital"]), 10000.0, format="%.0f", key="rp_cap")
        rk = c2.number_input("Risk %", 0.05, 5.0, float(cfg["risk"]["risk_percent"]), 0.05, key="rp_risk")
        rps = p["risk_per_share"]
        budget = cap * rk / 100
        qty = min(math.floor(budget / rps) if rps > 0 else 0, math.floor(cap / p["entry"]))
        st.markdown(
            '<div class="kv">'
            f'<span>Risk per share</span><span>{inr(rps)}</span>'
            f'<span>Risk budget</span><span>{inr(budget, 0)}</span>'
            f'<span>Quantity</span><span>{qty:,}</span>'
            f'<span>Planned risk</span><span style="color:#B42E24">{inr(qty * rps, 0)}</span>'
            f'<span>Scenario at T1</span><span style="color:#0E7A53">{inr(qty * abs(p["t1"] - p["entry"]), 0)}</span>'
            f'<span>Scenario at T2</span><span style="color:#0E7A53">{inr(qty * abs(p["t2"] - p["entry"]), 0)}</span>'
            f'<span>Capital used</span><span>{inr(qty * p["entry"], 0)}</span></div>'
            '<div class="note">Gross figures. Brokerage, taxes, slippage and gap risk are excluded. '
            'Changing values here does not change Settings.</div>', unsafe_allow_html=True)


@st.cache_data(ttl=3600, show_spinner=False)
def _record(symbol, mode, setup_key, direction, version, _hour):
    from scanner.backtest import run_backtest
    cfg, _ = state.active_config()
    trades, m, _ = run_backtest(state.provider(), [symbol], mode, setup_key, direction, cfg)
    return trades, m["all"]


def _track_record(sig):
    if not sig.get("setup_key"):
        return
    from datetime import datetime
    with st.container(border=True):
        st.markdown(f"**Track record: {sig['setup_label']} on {sig['symbol']}**")
        with st.spinner("Replaying history…"):
            try:
                trades, m = _record(sig["symbol"], sig["mode"], sig["setup_key"], sig["direction"],
                                    sig.get("config_version", ""), datetime.now().strftime("%Y%m%d%H"))
            except Exception as exc:
                st.caption(f"Could not replay history: {exc}")
                return
        if not m["trades"]:
            st.caption("This setup did not trigger on this stock in the loaded history, so there is no track record yet.")
            return
        st.markdown(
            '<div class="kv">'
            f'<span>Past signals</span><span>{m["trades"]}</span>'
            f'<span>Win rate</span><span>{m["win_rate"]}%</span>'
            f'<span>Average result</span><span>{m["avg_R"]:+.2f}R</span>'
            f'<span>Profit factor</span><span>{m["profit_factor"]}</span>'
            f'<span>Worst drawdown</span><span>{m["max_drawdown_R"]}R</span></div>'
            f'<div class="note">Same rules replayed on {"~4 weeks of 5-minute" if sig["mode"] == "INTRADAY" else "~2 years of daily"} '
            'candles with 5 bps costs per side. A small sample says little; past results do not predict future ones.</div>',
            unsafe_allow_html=True)
        st.dataframe(trades.tail(5)[["signal_time", "entry", "exit", "exit_reason", "R"]], hide_index=True, width="stretch")
