"""Reusable UI pieces: status bar, call cards, filters and the signal chart."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from scanner.indicators import fib_levels

from . import state
from .theme import DECISION_STYLE, esc, inr, milestone_track, mini_candles, pct, status_pill, tiles

OVERLAYS = ["9 EMA", "21 EMA", "20 EMA", "50 SMA", "200 SMA", "VWAP", "Supertrend", "Bollinger", "Pivot", "S/R", "Fibonacci"]


def status_bar(title: str):
    cfg, version = state.active_config()
    err = state.provider_error()
    if err:
        st.error(f"Data provider not ready: {err}. Open Settings > Data source to fix it.")
        return
    prov = state.provider()
    try:
        ms = prov.get_market_status(cfg)
        market = ("OPEN", "#13885F") if ms["is_open"] else ("CLOSED", "#C9372C")
        nxt = f" · next open {ms['next_open']:%a %d %b %H:%M}" if ms.get("next_open") else ""
    except Exception:
        market, nxt = ("UNKNOWN", "#9AA5B6"), ""
    data_lbl = {"upstox": ("Upstox (official API)", "#13885F"), "kite": ("Zerodha Kite (official API)", "#13885F"),
                "yahoo": ("Yahoo Finance (may be delayed)", "#D19424"), "csv": ("CSV files", "#13885F")}.get(
        prov.name, (prov.name, "#13885F"))
    runs = [r["at"] for r in state.scans().values()]
    last = f"{max(runs):%H:%M:%S}" if runs else "not yet"
    st.markdown(
        f'<div class="ts-bar"><span class="brand">{esc(title)}</span>'
        f'<span class="item"><span class="dot" style="background:{market[1]}"></span>Market <b>{market[0]}</b>{esc(nxt)}</span>'
        f'<span class="item"><span class="dot" style="background:{data_lbl[1]}"></span>Data <b>{data_lbl[0]}</b></span>'
        f'<span class="item">Last refresh <b>{last}</b></span>'
        f'<span class="item">Config <b>v{esc(version)}</b></span></div>', unsafe_allow_html=True)


def summary_tiles(signals: list):
    n = len(signals)
    c = lambda d: sum(1 for s in signals if s["decision"] == d)  # noqa: E731
    warn = sum(1 for s in signals if s.get("dq") and s["dq"].get("flags"))
    st.markdown(tiles([
        ("Scanned", n, "▦", "#E6ECFA", "#2F5BD3"),
        ("Buy candidates", c("BUY CANDIDATE"), "▲", "#E3F4EC", "#13885F"),
        ("Sell candidates", c("SELL CANDIDATE"), "▼", "#FBE7E5", "#C9372C"),
        ("Wait / watch", c("WAIT / WATCH"), "◷", "#FBF0DC", "#B7791F"),
        ("No buy", c("NO BUY"), "–", "#EBEEF3", "#4E5A6E"),
        ("Data warnings", warn, "!", "#FDEDE0", "#B8581B"),
    ]), unsafe_allow_html=True)


def filter_panel(key: str, signals: list, show_mode=False) -> dict:
    sectors = sorted({s.get("sector") or "Other" for s in signals})
    setups = sorted({s["setup_label"] for s in signals if s["setup_label"] != "-"})
    with st.container(border=True):
        st.markdown("**Filters**")
        f = {}
        if show_mode:
            f["mode"] = st.segmented_control("Mode", ["All", "Intraday", "Swing"], default="All", key=f"{key}_mode")
        f["decision"] = st.pills("Decision", ["Buy", "Sell", "Wait", "No buy"], selection_mode="multi",
                                 default=["Buy", "Sell", "Wait"], key=f"{key}_dec")
        f["direction"] = st.segmented_control("Direction", ["All", "Long", "Short"], default="All", key=f"{key}_dir")
        f["min_score"] = st.slider("Minimum score", 0, 100, 0, 5, key=f"{key}_score")
        f["sectors"] = st.multiselect("Sector", sectors, key=f"{key}_sec", placeholder="All sectors")
        f["setups"] = st.multiselect("Setup", setups, key=f"{key}_setup", placeholder="All setups")
        prices = [s["price"] for s in signals if s["price"]]
        if prices:
            lo, hi = float(min(prices)), float(max(prices))
            if hi > lo:
                f["price"] = st.slider("Price range (₹)", lo, hi, (lo, hi), key=f"{key}_price")
        f["min_relvol"] = st.number_input("Min relative volume", 0.0, 10.0, 0.0, 0.1, key=f"{key}_rv")
    return f


def apply_filters(signals: list, f: dict, frames: dict | None = None) -> list:
    dec_map = {"Buy": "BUY CANDIDATE", "Sell": "SELL CANDIDATE", "Wait": "WAIT / WATCH", "No buy": "NO BUY"}
    want = {dec_map[d] for d in (f.get("decision") or [])} or set(dec_map.values())
    out = []
    for s in signals:
        if s["decision"] not in want or s["score"] < f.get("min_score", 0):
            continue
        if f.get("mode") and f["mode"] != "All" and s["mode"].title() != f["mode"]:
            continue
        if f.get("direction") and f["direction"] != "All" and s["direction"] != f["direction"].upper():
            continue
        if f.get("sectors") and (s.get("sector") or "Other") not in f["sectors"]:
            continue
        if f.get("setups") and s["setup_label"] not in f["setups"]:
            continue
        if f.get("price") and s["price"] and not (f["price"][0] <= s["price"] <= f["price"][1]):
            continue
        if f.get("min_relvol") and frames:
            df = frames.get(s["symbol"])
            rv = df["relvol"].iloc[-1] if df is not None else 0
            if pd.isna(rv) or rv < f["min_relvol"]:
                continue
        out.append(s)
    rank = {"BUY CANDIDATE": 0, "SELL CANDIDATE": 0, "WAIT / WATCH": 1, "NO BUY": 2}
    return sorted(out, key=lambda s: (rank[s["decision"]], -s["score"]))


def call_card(sig: dict, key: str, detail_page=None, actions: bool = True, df=None):
    style = DECISION_STYLE[sig["decision"]]
    p = sig.get("plan")
    tf = sig["timeframe"]
    bt = sig["bar_time"]
    when = f"{bt:%d %b %H:%M}" if bt is not None and sig["mode"] == "INTRADAY" else (f"{bt:%d %b %Y}" if bt is not None else "–")
    dir_chip = ('<span class="chip" style="color:#0E7A53;background:#E3F4EC;border-color:#BFE6D3">Long</span>'
                if sig["direction"] == "LONG" else
                '<span class="chip" style="color:#B42E24;background:#FBE7E5;border-color:#F2C6C1">Short</span>')
    chips = (f'<span class="chip">{esc(sig["mode"].title())} {esc(tf)}</span>{dir_chip}'
             f'<span class="chip">{esc(sig["setup_label"])}</span><span class="chip">{esc(sig["regime"]["label"])}</span>')
    head = (f'<div class="card-head"><div><div class="sym">{esc(sig["symbol"])}</div>'
            f'<div class="sub">{esc(sig.get("exchange", "NSE"))}:{esc(sig["symbol"])} &nbsp; {esc(sig.get("sector", ""))} &nbsp; '
            f'candle closed {esc(when)}</div><div class="chips">{chips}</div></div>'
            + (f'<div class="mini">{mini_candles(df, sig.get("plan"))}</div>' if df is not None else "") +
            f'<div class="scorebox">{status_pill(sig["decision"])}'
            f'<div class="s" style="color:{style["fg"]};margin-top:.55rem">{sig["score"]}</div><div class="t">score / 100</div></div></div>')
    body = ""
    if p:
        sign = 1 if sig["direction"] == "LONG" else -1
        t1p, slp = pct(p["t1"], p["entry"]), pct(p["stop"], p["entry"])
        ltp = sig["price"]
        lp = pct(ltp, p["entry"]) if ltp else 0
        body += ('<div class="strip">'
                 f'<div><div class="k">Entry</div><div class="v">{inr(p["entry"])}</div><div class="d" style="color:#667085">'
                 f'{"above" if sign > 0 else "below"} trigger</div></div>'
                 f'<div><div class="k">Target 1</div><div class="v" style="color:#0E7A53">{inr(p["t1"])}</div>'
                 f'<div class="d" style="color:#0E7A53">{t1p:+.2f}%</div></div>'
                 f'<div><div class="k">Stop loss</div><div class="v" style="color:#B42E24">{inr(p["stop"])}</div>'
                 f'<div class="d" style="color:#B42E24">{slp:+.2f}%</div></div>'
                 f'<div><div class="k">Risk : reward</div><div class="v">1 : {p["rr1"]:.2f}</div>'
                 f'<div class="d" style="color:#667085">T2 1 : {p["rr2"]:.1f}</div></div>'
                 f'<div><div class="k">Last price <span style="color:#2F5BD3">◆</span></div><div class="v" style="color:{COBALT_}">{inr(ltp)}</div>'
                 f'<div class="d" style="color:#667085">{lp:+.2f}% vs entry</div></div></div>')
        body += milestone_track(p, ltp, sig["direction"])
    good = [r.explanation for r in sig["reasons"] if r.category not in ("Gate",)][:3]
    if sig["decision"] in ("BUY CANDIDATE", "SELL CANDIDATE"):
        warn = [r.explanation for r in sig["flags"] if r.category != "Veto"][:2]
        body += f'<div class="why"><b>Why:</b> {esc("; ".join(good))}.</div>'
        if warn:
            body += f'<div class="why"><b class="flag">Watch out:</b> {esc("; ".join(warn))}.</div>'
    else:
        body += f'<div class="why"><b>Why not {("buy" if sig["direction"] == "LONG" else "sell")}:</b> {esc("; ".join(sig["why_not"][:3]) or "Setup not complete")}.</div>'
    st.markdown(f'<div class="card" style="--edge:{style["edge"]}">{head}{body}</div>', unsafe_allow_html=True)
    if not actions:
        return
    b1, b2, b3, b4 = st.columns([1.2, 1.2, 1.2, 3])
    if b1.button("Open details", key=f"{key}_open", type="primary", width="stretch"):
        state.open_detail(sig["mode"], sig["symbol"])
        if detail_page is not None:
            st.switch_page(detail_page)
    if b2.button("Add to watchlist", key=f"{key}_watch", width="stretch"):
        added = state.get_store().add_watch(sig["symbol"], sig["mode"], f"From scan: {sig['setup_label']}")
        st.toast(f"{sig['symbol']} added to watchlist" if added else f"{sig['symbol']} is already on the watchlist")
    if b3.button("Save to journal", key=f"{key}_journal", width="stretch", disabled=not p):
        state.get_store().add_journal(sig)
        st.toast(f"Journal entry created for {sig['symbol']}")
    st.write("")


COBALT_ = "#2F5BD3"


def signal_chart(df: pd.DataFrame, sig: dict, overlays: list, bars: int, lower: str = "Volume",
                 project: int = 0) -> go.Figure:
    d = df.iloc[-bars:].copy()
    intraday = sig["mode"] == "INTRADAY"
    x = [t.strftime("%d %b %H:%M") if intraday else t.strftime("%d %b %y") for t in d.index]
    future = [f"+{k}" for k in range(1, project + 1)]
    rows = 2 if lower != "None" else 1
    fig = make_subplots(rows=rows, cols=1, shared_xaxes=True, vertical_spacing=0.03,
                        row_heights=[0.76, 0.24] if rows == 2 else [1.0])
    fig.add_trace(go.Candlestick(x=x, open=d["open"], high=d["high"], low=d["low"], close=d["close"], name="Price",
                                 increasing_line_color="#13885F", decreasing_line_color="#C9372C",
                                 increasing_fillcolor="#13885F", decreasing_fillcolor="#C9372C"), row=1, col=1)
    lines = {"9 EMA": ("ema_fast", "#E08A1E", None), "21 EMA": ("ema_slow", "#7B4FC9", None),
             "20 EMA": ("ema_trend", "#2F5BD3", None), "50 SMA": ("sma_mid", "#0F8FA3", "dot"),
             "200 SMA": ("sma_long", "#1B2433", "dot"), "VWAP": ("vwap", "#C23A8B", "dash")}
    for name, (col, color, dash) in lines.items():
        if name in overlays and col in d and d[col].notna().any():
            fig.add_trace(go.Scatter(x=x, y=d[col], name=name, line=dict(color=color, width=1.4, dash=dash)), row=1, col=1)
    if "Supertrend" in overlays:
        up = d["st"].where(d["st_dir"] == 1)
        dn = d["st"].where(d["st_dir"] == -1)
        fig.add_trace(go.Scatter(x=x, y=up, name="Supertrend ↑", line=dict(color="#13885F", width=1.6)), row=1, col=1)
        fig.add_trace(go.Scatter(x=x, y=dn, name="Supertrend ↓", line=dict(color="#C9372C", width=1.6)), row=1, col=1)
    if "Bollinger" in overlays:
        fig.add_trace(go.Scatter(x=x, y=d["bb_up"], name="BB upper", line=dict(color="#98A6BD", width=1)), row=1, col=1)
        fig.add_trace(go.Scatter(x=x, y=d["bb_lo"], name="BB lower", line=dict(color="#98A6BD", width=1),
                                 fill="tonexty", fillcolor="rgba(152,166,189,0.10)"), row=1, col=1)
    last = df.iloc[-1]
    if "Pivot" in overlays and not pd.isna(last.pp):
        for lv, c in (("s2", "#C9372C"), ("s1", "#E0776E"), ("pp", "#44506A"), ("r1", "#5FB38F"), ("r2", "#13885F")):
            fig.add_hline(y=last[lv], line=dict(color=c, width=1, dash="dot"), annotation_text=lv.upper(),
                          annotation_position="left", annotation_font_size=10, row=1, col=1)
    if "S/R" in overlays:
        for lv, c, lbl in ((last.swing_high, "#95600F", "Swing high"), (last.swing_low, "#95600F", "Swing low")):
            if not pd.isna(lv):
                fig.add_hline(y=lv, line=dict(color=c, width=1, dash="dashdot"), annotation_text=lbl,
                              annotation_position="left", annotation_font_size=10, row=1, col=1)
    if "Fibonacci" in overlays and not pd.isna(last.swing_high) and not pd.isna(last.swing_low) and last.swing_high > last.swing_low:
        for lbl, lv in fib_levels(last.swing_low, last.swing_high).items():
            fig.add_hline(y=lv, line=dict(color="#7B4FC9", width=0.8, dash="dot"), annotation_text=f"Fib {lbl}",
                          annotation_position="right", annotation_font_size=9, row=1, col=1)
    p = sig.get("plan")
    if project:
        # projection area: planned reward / risk zones and a 1-ATR-per-sqrt(bar) range cone
        last_x, last_c = x[-1], float(d["close"].iloc[-1])
        a = float(df["atr"].iloc[-1]) if not pd.isna(df["atr"].iloc[-1]) else 0
        cone_x = [last_x] + future
        up = [last_c] + [last_c + a * (k ** 0.5) for k in range(1, project + 1)]
        dn = [last_c] + [last_c - a * (k ** 0.5) for k in range(1, project + 1)]
        fig.add_trace(go.Scatter(x=cone_x, y=up, mode="lines", line=dict(color="#98A6BD", width=1, dash="dot"),
                                 name="Expected range (ATR)", hoverinfo="skip"), row=1, col=1)
        fig.add_trace(go.Scatter(x=cone_x, y=dn, mode="lines", line=dict(color="#98A6BD", width=1, dash="dot"),
                                 fill="tonexty", fillcolor="rgba(152,166,189,0.10)", showlegend=False, hoverinfo="skip"), row=1, col=1)
        if p:
            x0, x1 = future[0], future[-1]
            fig.add_shape(type="rect", x0=x0, x1=x1, y0=p["entry"], y1=p["t1"], fillcolor="rgba(19,136,95,0.16)",
                          line_width=0, row=1, col=1)
            fig.add_shape(type="rect", x0=x0, x1=x1, y0=p["t1"], y1=p["t2"], fillcolor="rgba(19,136,95,0.07)",
                          line_width=0, row=1, col=1)
            fig.add_shape(type="rect", x0=x0, x1=x1, y0=p["entry"], y1=p["stop"], fillcolor="rgba(201,55,44,0.14)",
                          line_width=0, row=1, col=1)
    if p:
        for lbl, v, c in (("Entry", p["entry"], "#D19424"), ("SL", p["stop"], "#C9372C"), ("T1", p["t1"], "#13885F"), ("T2", p["t2"], "#0E7A53")):
            fig.add_hline(y=v, line=dict(color=c, width=1.6), annotation_text=f"{lbl} {v:,.2f}",
                          annotation_position="right", annotation_font=dict(color=c, size=11), row=1, col=1)
    if sig.get("trigger"):
        long = sig["direction"] == "LONG"
        y = d["low"].iloc[-1] * 0.998 if long else d["high"].iloc[-1] * 1.002
        fig.add_trace(go.Scatter(x=[x[-1]], y=[y], mode="markers+text", name="Trigger",
                                 marker=dict(symbol="triangle-up" if long else "triangle-down", size=14, color=COBALT_),
                                 text=["Trigger"], textposition="bottom center" if long else "top center",
                                 textfont=dict(color=COBALT_, size=11)), row=1, col=1)
    if rows == 2:
        if lower == "Volume":
            colors = ["#9BD3BA" if c >= o else "#EFB2AC" for o, c in zip(d["open"], d["close"])]
            fig.add_trace(go.Bar(x=x, y=d["volume"], marker_color=colors, name="Volume", showlegend=False), row=2, col=1)
        elif lower == "RSI":
            fig.add_trace(go.Scatter(x=x, y=d["rsi"], name="RSI 14", line=dict(color="#7B4FC9")), row=2, col=1)
            for lv in (30, 50, 70):
                fig.add_hline(y=lv, line=dict(color="#C8D1DE", width=1, dash="dot"), row=2, col=1)
        elif lower == "MACD":
            fig.add_trace(go.Bar(x=x, y=d["macd_hist"], name="Histogram", showlegend=False,
                                 marker_color=["#9BD3BA" if v >= 0 else "#EFB2AC" for v in d["macd_hist"].fillna(0)]), row=2, col=1)
            fig.add_trace(go.Scatter(x=x, y=d["macd"], name="MACD", line=dict(color="#2F5BD3", width=1.2)), row=2, col=1)
            fig.add_trace(go.Scatter(x=x, y=d["macd_signal"], name="Signal", line=dict(color="#E08A1E", width=1.2)), row=2, col=1)
    fig.update_layout(height=560, margin=dict(l=10, r=90, t=10, b=10), paper_bgcolor="#FFFFFF", plot_bgcolor="#FFFFFF",
                      xaxis_rangeslider_visible=False, legend=dict(orientation="h", y=1.04, x=0, font=dict(size=11)),
                      font=dict(family="IBM Plex Sans, Segoe UI, sans-serif", color="#1B2433"), hovermode="x unified")
    fig.update_xaxes(type="category", nticks=10, showgrid=False, tickfont=dict(size=10),
                     categoryorder="array", categoryarray=x + future)
    fig.update_yaxes(gridcolor="#EEF1F6", tickfont=dict(size=10))
    return fig
