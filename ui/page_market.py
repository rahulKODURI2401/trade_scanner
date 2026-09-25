"""Market today (daily routine, index context, breadth, risk budget) and Calls
(every candidate tracked on real candles after it fired)."""
from __future__ import annotations

from datetime import datetime, time

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from scanner.calls import OPEN, update_open_calls
from scanner.engine import prepare_frame
from scanner.providers import IST
from scanner.scoring import classify_regime

from . import nav, state
from .components import status_bar
from .theme import DECISION_STYLE, esc, inr, pct, tiles

PHASES = [
    (time(0, 0), time(9, 0), "Pre-market", "Review last close's swing candidates, check global cues and news, mark key levels. No live intraday signals yet."),
    (time(9, 0), time(9, 15), "Pre-open", "Pre-open auction. Prices are discovering; do not act on intraday scans."),
    (time(9, 15), time(9, 30), "Opening range", "First 15 minutes are excluded from intraday entries. Watch how NIFTY opens against its pivot and VWAP."),
    (time(9, 30), time(14, 30), "Prime window", "Main window for intraday setups. Take only BUY/SELL candidates that agree with the index bias, sized by the risk panel."),
    (time(14, 30), time(15, 30), "Last hour", "No new intraday entries. Manage open trades; intraday positions square off by the close."),
    (time(15, 30), time(23, 59, 59), "Post-close", "Run the swing scan on today's completed daily candle, update the journal, review calls."),
]


def _phase(now: datetime):
    if now.weekday() >= 5:
        return "Weekend", "Market closed. Good time for the swing scan, backtests and journal review."
    for a, b, name, text in PHASES:
        if a <= now.time() < b:
            return name, text
    return "Post-close", PHASES[-1][3]


def _index_card(prov, cfg, name):
    try:
        daily = prov.get_historical_ohlcv(name, "1D")
    except Exception as exc:
        return None, f"{name}: {exc}"
    if daily.empty or len(daily) < 30:
        return None, f"{name}: not available from {prov.name}"
    try:
        intra = prov.get_historical_ohlcv(name, "5m")
    except Exception:
        intra = pd.DataFrame()
    d = prepare_frame(daily, cfg, "SWING")
    reg = classify_regime(d, len(d) - 1)
    last = float(intra["close"].iloc[-1]) if len(intra) else float(daily["close"].iloc[-1])
    today = prov.now().date()
    prev_close = float(daily[daily.index.date < today]["close"].iloc[-1]) if (daily.index.date < today).any() else float(daily["close"].iloc[-2])
    info = {"name": name, "last": last, "chg": pct(last, prev_close), "regime": reg["trend"], "daily": d, "intra": intra,
            "pp": None, "vwap": None}
    if len(intra):
        di = prepare_frame(intra, cfg, "INTRADAY")
        info["pp"], info["vwap"] = float(di["pp"].iloc[-1]), float(di["vwap"].iloc[-1])
        info["intra"] = di
    return info, None


def _mini_fig(df, title, intraday):
    d = df.iloc[-75:] if intraday else df.iloc[-90:]
    x = [t.strftime("%H:%M" if intraday else "%d %b") for t in d.index]
    fig = go.Figure(go.Candlestick(x=x, open=d["open"], high=d["high"], low=d["low"], close=d["close"],
                                   increasing_line_color="#13885F", decreasing_line_color="#C9372C",
                                   increasing_fillcolor="#13885F", decreasing_fillcolor="#C9372C", name=title))
    if intraday and "vwap" in d:
        fig.add_trace(go.Scatter(x=x, y=d["vwap"], line=dict(color="#C23A8B", width=1.2, dash="dash"), name="VWAP"))
    elif "ema_trend" in d:
        fig.add_trace(go.Scatter(x=x, y=d["ema_trend"], line=dict(color="#2F5BD3", width=1.2), name="20 EMA"))
        fig.add_trace(go.Scatter(x=x, y=d["sma_mid"], line=dict(color="#0F8FA3", width=1.1, dash="dot"), name="50 SMA"))
    fig.update_layout(height=250, margin=dict(l=5, r=5, t=5, b=5), showlegend=False, paper_bgcolor="#fff", plot_bgcolor="#fff",
                      xaxis_rangeslider_visible=False, font=dict(family="IBM Plex Sans, sans-serif", size=10))
    fig.update_xaxes(type="category", nticks=6, showgrid=False)
    fig.update_yaxes(gridcolor="#EEF1F6")
    return fig


def market_page():
    status_bar("PC Trade Scanner")
    if state.provider_error():
        return
    cfg, _ = state.active_config()
    prov = state.provider()
    now = prov.now()
    phase, advice = _phase(now)
    st.markdown("### Market today")
    st.markdown(f'<div class="panel"><h4>{esc(phase)} &nbsp;<span style="color:#667085;font-weight:500">'
                f'{now:%A %d %b %Y, %H:%M} IST</span></h4><div style="font-size:.92rem;color:#35415A">{esc(advice)}</div>'
                '<div class="note">NSE holidays are not modelled: on a holiday the latest candles will simply be from the previous session.</div></div>',
                unsafe_allow_html=True)
    st.write("")

    idx = {}
    cols = st.columns(2)
    for col, name in zip(cols, ["NIFTY 50", "NIFTY BANK"]):
        info, err = _index_card(prov, cfg, name)
        with col:
            with st.container(border=True):
                if err:
                    st.caption(err)
                    continue
                idx[name] = info
                color = "#13885F" if info["chg"] >= 0 else "#C9372C"
                vs = ""
                if info["vwap"]:
                    vs = f' &nbsp; {"above" if info["last"] > info["vwap"] else "below"} VWAP {info["vwap"]:,.0f}, PP {info["pp"]:,.0f}'
                st.markdown(f'<div style="display:flex;justify-content:space-between;align-items:baseline">'
                            f'<b style="font-size:1.05rem">{name}</b><span class="num" style="font-size:1.25rem;font-weight:700">'
                            f'{info["last"]:,.2f} <span style="color:{color};font-size:.9rem">{info["chg"]:+.2f}%</span></span></div>'
                            f'<div class="sub">Daily regime {info["regime"]}{vs}</div>', unsafe_allow_html=True)
                t1, t2 = st.tabs(["Today (5m)", "Daily"])
                with t1:
                    if len(info["intra"]):
                        today = info["intra"][info["intra"].index.date == info["intra"].index[-1].date()]
                        st.plotly_chart(_mini_fig(today, name, True), width="stretch", config={"displayModeBar": False}, key=f"mi_{name}")
                with t2:
                    st.plotly_chart(_mini_fig(info["daily"], name, False), width="stretch", config={"displayModeBar": False}, key=f"md_{name}")

    bias = "Mixed: be selective, smaller size"
    n50 = idx.get("NIFTY 50")
    if n50:
        above_vwap = n50["vwap"] is not None and n50["last"] > n50["vwap"]
        below_vwap = n50["vwap"] is not None and n50["last"] < n50["vwap"]
        if n50["regime"] == "TREND_UP" and above_vwap:
            bias = "Long bias: NIFTY in an uptrend and above VWAP. Favour BUY candidates."
        elif n50["regime"] == "TREND_DOWN" and below_vwap:
            bias = "Short bias: NIFTY in a downtrend and below VWAP. Favour SELL candidates; be strict with longs."
    try:
        vix = prov.get_historical_ohlcv("INDIA VIX", "1D")
        v = float(vix["close"].iloc[-1])
        vix_txt = f"India VIX {v:.2f}: " + ("calm, ranges may be tight" if v < 13 else "normal" if v < 18 else "elevated, widen stops or cut size")
    except Exception:
        vix_txt = "India VIX not available from this provider"
    st.markdown(f'<div class="panel"><h4>Index bias</h4><div style="font-size:.92rem">{esc(bias)}</div>'
                f'<div class="note">{esc(vix_txt)}. Bias is a rule of thumb from index trend and VWAP, not a forecast.</div></div>',
                unsafe_allow_html=True)
    st.write("")

    # breadth and sectors from the swing scan (daily candles)
    if "SWING" not in state.scans():
        with st.spinner("Loading daily candles for breadth…"):
            state.run_scan("SWING", state.instruments())
    res = state.scans()["SWING"]
    rows = []
    for s in res["signals"]:
        df = res["frames"].get(s["symbol"])
        if df is None or len(df) < 2:
            continue
        last = df.iloc[-1]
        rows.append({"symbol": s["symbol"], "sector": s.get("sector") or "Other",
                     "chg": pct(last["close"], df["close"].iloc[-2]),
                     "above20": last["close"] > last["ema_trend"], "above200": bool(last["close"] > last["sma_long"]) if not pd.isna(last["sma_long"]) else None})
    c1, c2 = st.columns([1, 1.4])
    with c1:
        with st.container(border=True):
            st.markdown("**Breadth (last completed daily candle)**")
            if rows:
                b = pd.DataFrame(rows)
                adv, dec = int((b["chg"] > 0).sum()), int((b["chg"] < 0).sum())
                st.markdown(tiles([("Advancing", adv, "▲", "#E3F4EC", "#13885F"), ("Declining", dec, "▼", "#FBE7E5", "#C9372C"),
                                   ("Above 20 EMA", f"{b['above20'].mean() * 100:.0f}%", "∿", "#E6ECFA", "#2F5BD3"),
                                   ("Above 200 SMA", f"{b['above200'].dropna().mean() * 100:.0f}%", "∿", "#EDE7FA", "#7B4FC9")]),
                            unsafe_allow_html=True)
    with c2:
        with st.container(border=True):
            st.markdown("**Sector strength (average daily change)**")
            if rows:
                sec = pd.DataFrame(rows).groupby("sector")["chg"].mean().sort_values()
                fig = go.Figure(go.Bar(x=sec.values, y=sec.index, orientation="h",
                                       marker_color=["#13885F" if v >= 0 else "#C9372C" for v in sec.values],
                                       text=[f"{v:+.2f}%" for v in sec.values], textposition="outside"))
                fig.update_traces(cliponaxis=False)
                fig.update_layout(height=max(220, 24 * len(sec)), margin=dict(l=5, r=60, t=5, b=5), paper_bgcolor="#fff",
                                  plot_bgcolor="#fff", font=dict(family="IBM Plex Sans, sans-serif", size=11))
                st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

    _risk_budget(cfg)
    _routine()


def _risk_budget(cfg):
    j = state.get_store().journal()
    today = datetime.now().strftime("%Y-%m-%d")
    jt = j[j["created_at"].str[:10] == today] if not j.empty else j
    trades = len(jt[jt["outcome"] != "SKIPPED"]) if not jt.empty else 0
    pnl = float(jt["pnl"].fillna(0).sum()) if not jt.empty else 0.0
    cap = float(cfg["risk"]["capital"])
    max_loss = cap * cfg["risk"]["max_daily_loss_pct"] / 100
    per_trade = cap * cfg["risk"]["risk_percent"] / 100
    stop = pnl <= -max_loss or trades >= cfg["risk"]["max_trades_per_day"]
    with st.container(border=True):
        st.markdown("**Today's risk budget**")
        st.markdown(tiles([
            ("Risk per trade", inr(per_trade, 0), "₹", "#E6ECFA", "#2F5BD3"),
            ("Max loss today", inr(max_loss, 0), "⛔", "#FBE7E5", "#C9372C"),
            ("Trades taken", f"{trades} / {cfg['risk']['max_trades_per_day']}", "#", "#FBF0DC", "#B7791F"),
            ("Journal P&L today", inr(pnl, 0), "Σ", "#E3F4EC" if pnl >= 0 else "#FBE7E5", "#13885F" if pnl >= 0 else "#C9372C"),
        ]), unsafe_allow_html=True)
        if stop:
            st.error("Daily limit reached. The plan says no new trades today.")
        else:
            st.caption(f"Room for {max(0, int((max_loss + min(pnl, 0)) // max(per_trade, 1)))} more full-risk losses today. "
                       "Counts come from journal entries created today.")


def _routine():
    with st.expander("Daily routine checklist", expanded=False):
        items = {
            "Before 9:15": ["Review swing candidates from last close (Swing scanner)", "Note NIFTY/BANK NIFTY pivot, prior high/low",
                            "Check news/results for your watchlist", "Confirm today's risk budget above"],
            "9:30 - 14:30": ["Run the intraday scanner (auto-refresh 5 min)", "Act only on BUY/SELL candidates that match the index bias",
                             "Size every trade from the risk panel", "Save each trade to the journal"],
            "After 15:30": ["Run the swing scan on the completed daily candle", "Review Calls: which targets and stops were hit",
                            "Fill actual entry/exit in the journal", "Backtest any setup you are unsure about"],
        }
        for head, lst in items.items():
            st.markdown(f"**{head}**")
            for k, it in enumerate(lst):
                st.checkbox(it, key=f"rt_{head}_{k}")


# ------------------------------------------------------------------------ calls
STATUS_STYLE = {
    "WAITING_ENTRY": ("Waiting entry", "#95600F", "#FBF0DC"), "ACTIVE": ("Active", "#2F5BD3", "#E6ECFA"),
    "T1_HIT": ("T1 hit, SL at cost", "#0E7A53", "#E3F4EC"), "T2_HIT": ("T2 hit", "#0E7A53", "#E3F4EC"),
    "T1_HIT_THEN_BE": ("T1 hit, rest at cost", "#0E7A53", "#E3F4EC"), "SL_HIT": ("Stop hit", "#B42E24", "#FBE7E5"),
    "EXPIRED": ("Expired, no entry", "#4E5A6E", "#EBEEF3"), "CLOSED_EOD": ("Closed at session end", "#4E5A6E", "#EBEEF3"),
    "TIME_EXIT": ("Time exit", "#4E5A6E", "#EBEEF3"),
}


def _milestones(c) -> str:
    long = c["direction"] == "LONG"
    reached = {"SL": c["status"] == "SL_HIT", "Entry": c["status"] not in ("WAITING_ENTRY", "EXPIRED"),
               "T1": c["status"] in ("T1_HIT", "T2_HIT", "T1_HIT_THEN_BE"), "T2": c["status"] == "T2_HIT"}
    pts = [("SL", c["stop"], "#C9372C"), ("Entry", c["entry"], "#D19424"), ("T1", c["t1"], "#13885F"), ("T2", c["t2"], "#13885F")]
    vals = [p[1] for p in pts] + ([c["last_price"]] if c["last_price"] else [])
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    xf = lambda v: 4 + 92 * ((v - lo) / span if long else 1 - (v - lo) / span)  # noqa: E731
    out = ['<div class="track"><div class="rail"></div>']
    for name, v, col in pts:
        fill = col if reached[name] else "#fff"
        out.append(f'<div class="m" style="left:{xf(v):.1f}%"><div class="n" style="color:{col}">{name}</div>'
                   f'<div class="p" style="border-color:{col};background:{fill}"></div><div class="x">{inr(v, 1)}</div></div>')
    if c["last_price"]:
        out.append(f'<div class="ltp" style="left:{xf(c["last_price"]):.1f}%" title="Last price"></div>')
    out.append("</div>")
    hits = int(reached["T1"]) + int(reached["T2"])
    return "".join(out), hits


def _call_card(c, key):
    long = c["direction"] == "LONG"
    dec = DECISION_STYLE["BUY CANDIDATE" if long else "SELL CANDIDATE"]
    lbl, fg, bg = STATUS_STYLE.get(c["status"], (c["status"], "#4E5A6E", "#EBEEF3"))
    ltp = c["last_price"]
    nxt = c["t2"] if c["status"] == "T1_HIT" else c["t1"]
    left = pct(nxt, ltp) * (1 if long else -1) if ltp else 0
    track, hits = _milestones(c)
    created = pd.Timestamp(c["created_bar"])
    result = ""
    if c["result_r"] is not None and not pd.isna(c["result_r"]):
        rc = "#0E7A53" if c["result_r"] > 0 else "#B42E24" if c["result_r"] < 0 else "#4E5A6E"
        result = f'<div class="scorebox"><div class="s" style="color:{rc}">{c["result_r"]:+.2f}R</div><div class="t">result</div></div>'
    html = (f'<div class="card" style="--edge:{dec["edge"]}"><div class="card-head"><div>'
            f'<div class="sym">{esc(c["symbol"])}</div><div class="sub">NSE:{esc(c["symbol"])} &nbsp; called {created:%d %b %H:%M}'
            f' &nbsp; score {c["score"]}</div><div class="chips"><span class="chip">{esc(c["mode"].title())} {esc(c["timeframe"])}</span>'
            f'<span class="chip" style="color:{dec["fg"]};background:{dec["bg"]}">{"BUY" if long else "SELL"}</span>'
            f'<span class="chip">{esc(c["setup"])}</span></div></div>'
            f'<div style="text-align:right"><span class="status" style="color:{fg};background:{bg}">{esc(lbl)}</span>'
            f'<div class="t" style="font-size:.75rem;color:#667085;margin-top:.4rem">{hits}/2 targets hit</div></div>{result}</div>'
            '<div class="strip">'
            f'<div><div class="k">Entry</div><div class="v">{inr(c["entry"])}</div></div>'
            f'<div><div class="k">Target 1</div><div class="v" style="color:#0E7A53">{inr(c["t1"])}</div>'
            f'<div class="d" style="color:#0E7A53">{pct(c["t1"], c["entry"]):+.2f}%</div></div>'
            f'<div><div class="k">Active SL</div><div class="v" style="color:#B42E24">{inr(c["active_sl"])}</div></div>'
            f'<div><div class="k">Potential left</div><div class="v" style="color:#2F5BD3">'
            f'{(f"{left:+.2f}%" if c["status"] in OPEN else "–")}</div></div>'
            f'<div><div class="k">Last price ◆</div><div class="v" style="color:#2F5BD3">{inr(ltp)}</div>'
            f'<div class="d" style="color:#667085">{pct(ltp, c["entry"]):+.2f}% vs entry</div></div></div>'
            f'{track}<div class="why"><b>Why it was called:</b> {esc(c["reasons"] or "")}</div></div>')
    st.markdown(html, unsafe_allow_html=True)
    if st.button("Open chart", key=f"cc_{key}"):
        state.open_detail(c["mode"], c["symbol"])
        st.switch_page(nav.PAGES["detail"])
    st.write("")


def calls_page():
    status_bar("PC Trade Scanner")
    st.markdown("### Calls")
    st.caption("Every new BUY/SELL CANDIDATE is recorded as a call and followed on real candles: entry trigger, T1 "
               "(half booked, stop to cost), T2, stop, or expiry. This is the scanner's honest scorecard.")
    store = state.get_store()
    cfg, _ = state.active_config()
    if not state.provider_error() and st.button("Update calls with latest candles", type="primary"):
        with st.spinner("Checking open calls…"):
            n = update_open_calls(store, state.provider(), cfg)
        st.toast(f"Updated {n} open calls")
    calls = store.calls()
    if calls.empty:
        st.markdown('<div class="empty">No calls yet. Run the intraday or swing scanner; the first time a stock becomes a '
                    'BUY or SELL candidate it appears here and is tracked automatically.</div>', unsafe_allow_html=True)
        return
    today = datetime.now().strftime("%Y-%m-%d")
    past = calls[~calls["status"].isin(OPEN)]
    decided = past[past["result_r"].notna()]
    st.markdown(tiles([
        ("Total calls", len(calls), "▦", "#E6ECFA", "#2F5BD3"),
        ("Buy", int((calls["direction"] == "LONG").sum()), "▲", "#E3F4EC", "#13885F"),
        ("Sell", int((calls["direction"] == "SHORT").sum()), "▼", "#FBE7E5", "#C9372C"),
        ("Today", int((calls["created_at"].str[:10] == today).sum()), "◷", "#FBF0DC", "#B7791F"),
        ("Win rate (closed)", f"{(decided['result_r'] > 0).mean() * 100:.0f}%" if len(decided) else "–", "%", "#E3F4EC", "#13885F"),
        ("Average result", f"{decided['result_r'].mean():+.2f}R" if len(decided) else "–", "R", "#EDE7FA", "#7B4FC9"),
    ]), unsafe_allow_html=True)
    left, right = st.columns([1, 3.1], gap="medium")
    with left:
        with st.container(border=True):
            st.markdown("**Filters**")
            mode = st.segmented_control("Trade type", ["All", "Intraday", "Swing"], default="All", key="c_mode") or "All"
            direction = st.segmented_control("Direction", ["All", "Buy", "Sell"], default="All", key="c_dir") or "All"
            symbol = st.text_input("Symbol contains", key="c_sym").strip().upper()
    with right:
        h1, h2 = st.columns([2, 2])
        view = h2.segmented_control("Calls", ["Active", "Past"], default="Active", key="c_view",
                                    label_visibility="collapsed") or "Active"
        df = calls[calls["status"].isin(OPEN)] if view == "Active" else past
        if mode != "All":
            df = df[df["mode"] == mode.upper()]
        if direction != "All":
            df = df[df["direction"] == ("LONG" if direction == "Buy" else "SHORT")]
        if symbol:
            df = df[df["symbol"].str.contains(symbol, regex=False)]
        h1.markdown(f"#### {view} calls ({len(df)})")
        if df.empty:
            st.markdown('<div class="empty">Nothing here with these filters.</div>', unsafe_allow_html=True)
        for n, (_, c) in enumerate(df.head(30).iterrows()):
            _call_card(c, f"{c['call_id']}_{n}")
        if len(decided):
            with st.expander("Results by setup"):
                g = decided.groupby(["mode", "setup"]).agg(calls=("result_r", "size"), win_rate=("result_r", lambda r: round((r > 0).mean() * 100, 1)),
                                                           avg_R=("result_r", "mean")).round(2).reset_index()
                st.dataframe(g, hide_index=True, width="stretch")
