"""Visual language: a calm, light trading-desk look. White panels on a cool
grey desk, cobalt for interaction, and decision colours reserved strictly for
decision states so a BUY never looks like decoration."""
from __future__ import annotations

import html
import math

import streamlit as st

INK, MUTED, LINE = "#1B2433", "#667085", "#DCE2EB"
COBALT = "#2F5BD3"
DECISION_STYLE = {
    "BUY CANDIDATE": {"fg": "#0E7A53", "bg": "#E3F4EC", "edge": "#13885F", "short": "BUY"},
    "SELL CANDIDATE": {"fg": "#B42E24", "bg": "#FBE7E5", "edge": "#C9372C", "short": "SELL"},
    "WAIT / WATCH": {"fg": "#95600F", "bg": "#FBF0DC", "edge": "#D19424", "short": "WAIT"},
    "NO BUY": {"fg": "#4E5A6E", "bg": "#EBEEF3", "edge": "#9AA5B6", "short": "NO BUY"},
}

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&display=swap');
html, body, [class*="css"], .stMarkdown, .stButton button, .stTextInput input, .stSelectbox, .stDataFrame {
  font-family: 'IBM Plex Sans', 'Segoe UI', system-ui, -apple-system, sans-serif;
}
.block-container {padding-top: 3.2rem; padding-bottom: 3rem; max-width: 1480px;}
h1, h2, h3 {letter-spacing: -0.01em; color: #1B2433;}
[data-testid="stSidebar"] {background: #172238;}
[data-testid="stSidebar"] * {color: #D5DDEA;}
[data-testid="stSidebarNav"] a[aria-current="page"] {background: rgba(90,140,240,.18);}
.num {font-variant-numeric: tabular-nums;}

/* top status bar */
.ts-bar {display:flex; flex-wrap:wrap; align-items:center; gap:.6rem 1.4rem; background:#fff; border:1px solid #DCE2EB;
  border-radius:12px; padding:.7rem 1.1rem; margin-bottom:1rem;}
.ts-bar .brand {font-weight:700; font-size:1.05rem; color:#1B2433; margin-right:auto;}
.ts-bar .item {font-size:.85rem; color:#667085;}
.ts-bar .item b {color:#1B2433; font-weight:600;}
.dot {display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:6px; vertical-align:1px;}

/* stat tiles */
.tiles {display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:.8rem; margin-bottom:1.1rem;}
.tile {background:#fff; border:1px solid #DCE2EB; border-radius:12px; padding:.8rem .9rem; display:flex; gap:.65rem; align-items:center;}
.tile .ico {width:32px; height:32px; border-radius:10px; display:flex; align-items:center; justify-content:center; font-size:1.05rem; flex:none;}
.tile .lbl {font-size:.78rem; color:#667085; font-weight:500; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.tile > div:last-child {min-width:0;}
[data-testid="stMetricValue"] {font-size:1.45rem;}
.tile .val {font-size:1.55rem; font-weight:700; color:#1B2433; line-height:1.15; font-variant-numeric:tabular-nums;}

/* call card */
.card {background:#fff; border:1px solid #DCE2EB; border-left:5px solid var(--edge); border-radius:14px; padding:1rem 1.2rem .9rem;}
.card-head {display:flex; justify-content:space-between; gap:1rem; align-items:flex-start;}
.card-head .mini {flex:0 1 300px; min-width:160px; border:1px solid #EEF1F6; border-radius:8px; padding:2px 4px; background:#FBFCFE;}
@media (max-width: 760px) {.card-head {flex-wrap:wrap;} .card-head .mini {order:3; flex-basis:100%;}}
.sym {font-size:1.35rem; font-weight:700; color:#1B2433; line-height:1.1;}
.sub {font-size:.82rem; color:#667085; margin-top:.15rem;}
.chips {display:flex; flex-wrap:wrap; gap:.35rem; margin-top:.55rem;}
.chip {font-size:.74rem; font-weight:600; padding:.18rem .55rem; border-radius:999px; background:#EEF1F6; color:#44506A; border:1px solid #DCE2EB;}
.status {font-size:.8rem; font-weight:700; padding:.3rem .7rem; border-radius:999px; white-space:nowrap;}
.scorebox {text-align:right;}
.scorebox .s {font-size:1.6rem; font-weight:700; line-height:1; font-variant-numeric:tabular-nums;}
.scorebox .t {font-size:.72rem; color:#667085;}
.strip {display:grid; grid-template-columns:repeat(5,1fr); border:1px solid #E3E8F0; border-radius:10px; margin-top:.85rem; overflow:hidden;}
.strip > div {padding:.55rem .6rem; text-align:center; border-right:1px solid #E3E8F0;}
.strip > div:last-child {border-right:none; background:#F7F9FC;}
.strip .k {font-size:.72rem; color:#667085; font-weight:500;}
.strip .v {font-size:1.02rem; font-weight:700; color:#1B2433; font-variant-numeric:tabular-nums;}
.strip .d {font-size:.72rem; font-variant-numeric:tabular-nums;}
@media (max-width: 760px) {.strip {grid-template-columns:repeat(2,1fr);} .strip > div {border-bottom:1px solid #E3E8F0;}}

/* milestone track */
.track {position:relative; height:86px; margin:.9rem .8rem .2rem;}
.track .rail {position:absolute; top:30px; left:0; right:0; height:3px; background:#E3E8F0; border-radius:3px;}
.track .risk {position:absolute; top:30px; height:3px; background:#F1B7B1;}
.track .reward {position:absolute; top:30px; height:3px; background:#A9DCC4;}
.track .m {position:absolute; top:0; transform:translateX(-50%); text-align:center; width:90px;}
.track .m .n {font-size:.72rem; font-weight:600; color:#667085; height:18px;}
.track .m .p {width:14px; height:14px; border-radius:50%; margin:3px auto 0; border:2px solid; background:#fff;}
.track .m .x {font-size:.74rem; color:#44506A; margin-top:20px; font-variant-numeric:tabular-nums;}
.track .ltp {position:absolute; top:37px; width:9px; height:9px; background:#2F5BD3; transform:translateX(-50%) rotate(45deg);}

.why {font-size:.86rem; color:#35415A; margin-top:.55rem; line-height:1.5;}
.why b {color:#1B2433;}
.flag {color:#B42E24;}

/* panels */
.panel {background:#fff; border:1px solid #DCE2EB; border-radius:12px; padding:.9rem 1rem;}
.panel h4 {margin:0 0 .6rem; font-size:.95rem; color:#1B2433;}
.bar {height:8px; background:#EEF1F6; border-radius:5px; overflow:hidden;}
.bar > i {display:block; height:100%; border-radius:5px;}
.rule {display:flex; gap:.5rem; font-size:.86rem; padding:.28rem 0; border-bottom:1px dashed #EEF1F6; color:#35415A;}
.rule .ok {color:#13885F; font-weight:700; width:1rem;}
.rule .no {color:#C9372C; font-weight:700; width:1rem;}
.rule .pts {margin-left:auto; color:#667085; font-variant-numeric:tabular-nums; white-space:nowrap;}
.kv {display:grid; grid-template-columns:1fr auto; gap:.3rem .8rem; font-size:.88rem;}
.kv span:nth-child(odd) {color:#667085;}
.kv span:nth-child(even) {text-align:right; font-weight:600; color:#1B2433; font-variant-numeric:tabular-nums;}
.note {font-size:.78rem; color:#667085; margin-top:.5rem;}
.empty {background:#fff; border:1px dashed #C8D1DE; border-radius:12px; padding:1.4rem; text-align:center; color:#667085;}
</style>
"""


def inject_css():
    st.markdown(CSS, unsafe_allow_html=True)


def esc(x) -> str:
    return html.escape(str(x))


def inr(x, decimals=2) -> str:
    """Indian digit grouping: 12,34,567.89"""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    neg = x < 0
    s = f"{abs(x):.{decimals}f}"
    whole, _, frac = s.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    return ("-" if neg else "") + "₹" + whole + ("." + frac if frac else "")


def pct(a, b) -> float:
    return (a / b - 1) * 100 if b else 0.0


def status_pill(decision: str) -> str:
    d = DECISION_STYLE[decision]
    return f'<span class="status" style="color:{d["fg"]};background:{d["bg"]}">{esc(decision)}</span>'


def tiles(items: list[tuple]) -> str:
    """items: (label, value, icon, icon_bg, icon_fg)"""
    out = ['<div class="tiles">']
    for lbl, val, ico, bg, fg in items:
        out.append(f'<div class="tile"><div class="ico" style="background:{bg};color:{fg}">{ico}</div>'
                   f'<div><div class="lbl">{esc(lbl)}</div><div class="val">{esc(val)}</div></div></div>')
    out.append("</div>")
    return "".join(out)


def milestone_track(plan: dict, ltp: float | None, direction: str) -> str:
    long = direction == "LONG"
    pts = [("SL", plan["stop"], "#C9372C", True), ("Entry", plan["entry"], "#D19424", True),
           ("T1", plan["t1"], "#13885F", False), ("T2", plan["t2"], "#13885F", False)]
    vals = [p[1] for p in pts] + ([ltp] if ltp else [])
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1

    def x(v):
        f = (v - lo) / span
        return 4 + 92 * (f if long else 1 - f)

    e, s, t2 = x(plan["entry"]), x(plan["stop"]), x(plan["t2"])
    out = ['<div class="track"><div class="rail"></div>',
           f'<div class="risk" style="left:{min(e, s):.1f}%;width:{abs(e - s):.1f}%"></div>',
           f'<div class="reward" style="left:{min(e, t2):.1f}%;width:{abs(t2 - e):.1f}%"></div>']
    for name, v, col, filled in pts:
        fill = col if filled else "#fff"
        out.append(f'<div class="m" style="left:{x(v):.1f}%"><div class="n" style="color:{col}">{name}</div>'
                   f'<div class="p" style="border-color:{col};background:{fill}"></div><div class="x">{inr(v, 1)}</div></div>')
    if ltp:
        out.append(f'<div class="ltp" style="left:{x(ltp):.1f}%" title="Last price {inr(ltp)}"></div>')
    out.append("</div>")
    return "".join(out)


def rule_rows(rules, show_points=False) -> str:
    out = []
    for r in rules:
        mark = '<span class="ok">✓</span>' if (r.passed or (show_points and r.points > 0)) else '<span class="no">✕</span>'
        pts = f'<span class="pts">{r.points:g} / {r.max_points:g}</span>' if show_points else ""
        out.append(f'<div class="rule">{mark}<span>{esc(r.explanation)}</span>{pts}</div>')
    return "".join(out)


def bar(frac: float, color: str) -> str:
    return f'<div class="bar"><i style="width:{max(0, min(frac, 1)) * 100:.0f}%;background:{color}"></i></div>'


def mini_candles(df, plan: dict | None = None, bars: int = 45, w: int = 300, h: int = 78) -> str:
    """Tiny inline SVG candlestick chart with entry / SL / T1 guides."""
    if df is None or len(df) < 5:
        return ""
    d = df.iloc[-bars:]
    lo, hi = float(d["low"].min()), float(d["high"].max())
    lines = []
    if plan:
        lines = [(plan["stop"], "#C9372C"), (plan["entry"], "#D19424"), (plan["t1"], "#13885F")]
        lo = min(lo, *(v for v, _ in lines))
        hi = max(hi, *(v for v, _ in lines))
    span = (hi - lo) or 1
    pad = 4
    y = lambda v: pad + (hi - v) / span * (h - 2 * pad)  # noqa: E731
    cw = (w - 40) / len(d)
    out = [f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}" preserveAspectRatio="none" '
           f'style="display:block" role="img" aria-label="Recent candles">']
    for v, col in lines:
        out.append(f'<line x1="0" x2="{w - 40}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="{col}" stroke-width="1" stroke-dasharray="3 3"/>')
    for k, (_, r) in enumerate(d.iterrows()):
        x = k * cw + cw / 2
        up = r["close"] >= r["open"]
        col = "#13885F" if up else "#C9372C"
        top, bot = y(max(r["open"], r["close"])), y(min(r["open"], r["close"]))
        out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{y(r["high"]):.1f}" y2="{y(r["low"]):.1f}" stroke="{col}" stroke-width="1"/>')
        out.append(f'<rect x="{x - cw * 0.35:.1f}" y="{top:.1f}" width="{cw * 0.7:.1f}" height="{max(bot - top, 1):.1f}" fill="{col}"/>')
    last = float(d["close"].iloc[-1])
    out.append(f'<text x="{w - 36}" y="{min(max(y(last) + 4, 10), h - 2):.1f}" font-size="10" fill="#2F5BD3" font-weight="700">{last:,.0f}</text>')
    out.append("</svg>")
    return "".join(out)
