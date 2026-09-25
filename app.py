"""PC Trade Scanner - local Streamlit app.

Run:  streamlit run app.py   (then open http://localhost:8501)
"""
import streamlit as st

st.set_page_config(page_title="PC Trade Scanner", page_icon="📈", layout="wide", initial_sidebar_state="expanded")

from ui import nav  # noqa: E402
from ui.page_detail import detail_page  # noqa: E402
from ui.page_market import calls_page, market_page  # noqa: E402
from ui.page_scanner import dashboard_page, intraday_page, swing_page  # noqa: E402
from ui.page_tools import backtest_page, diagnostics_page, journal_page, settings_page, watchlist_page  # noqa: E402
from ui.theme import inject_css  # noqa: E402

inject_css()

nav.PAGES.update({
    "market": st.Page(market_page, title="Market today", icon=":material/today:", default=True),
    "dashboard": st.Page(dashboard_page, title="Dashboard", icon=":material/space_dashboard:", url_path="dashboard"),
    "calls": st.Page(calls_page, title="Calls", icon=":material/campaign:", url_path="calls"),
    "intraday": st.Page(intraday_page, title="Intraday scanner", icon=":material/timer:", url_path="intraday"),
    "swing": st.Page(swing_page, title="Swing / daily scanner", icon=":material/calendar_month:", url_path="swing"),
    "detail": st.Page(detail_page, title="Signal detail", icon=":material/candlestick_chart:", url_path="signal"),
    "watchlist": st.Page(watchlist_page, title="Watchlist", icon=":material/visibility:", url_path="watchlist"),
    "backtest": st.Page(backtest_page, title="Backtest", icon=":material/history:", url_path="backtest"),
    "journal": st.Page(journal_page, title="Journal", icon=":material/edit_note:", url_path="journal"),
    "settings": st.Page(settings_page, title="Settings", icon=":material/tune:", url_path="settings"),
    "diagnostics": st.Page(diagnostics_page, title="Logs / diagnostics", icon=":material/monitor_heart:", url_path="diagnostics"),
})
P = nav.PAGES
pg = st.navigation({
    "Today": [P["market"], P["dashboard"]],
    "Scan": [P["intraday"], P["swing"], P["detail"]],
    "Track": [P["calls"], P["watchlist"], P["journal"], P["backtest"]],
    "System": [P["settings"], P["diagnostics"]],
})
with st.sidebar:
    st.caption("Local decision-support tool on live market data. Nothing here places orders, and nothing leaves "
               "this PC except candle requests to your data provider.")
pg.run()
