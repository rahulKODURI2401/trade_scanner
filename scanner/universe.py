"""Scan universes. NIFTY 50 is bundled; the other NSE index lists are downloaded
from niftyindices.com (official constituent CSVs, which include ISINs) and cached
in data/cache/. A custom list is just symbols you type in Settings."""
from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import pandas as pd

# NIFTY 50 as of September 2026 (WIPRO -> BSE change is effective 30 Sep 2026).
# Use Settings > Universe > "Refresh from niftyindices.com" to get the live list.
NIFTY50 = [
    ("ADANIENT", "Metals & Mining"), ("ADANIPORTS", "Services"), ("APOLLOHOSP", "Healthcare"),
    ("ASIANPAINT", "Consumer Durables"), ("AXISBANK", "Financial Services"), ("BAJAJ-AUTO", "Automobile"),
    ("BAJFINANCE", "Financial Services"), ("BAJAJFINSV", "Financial Services"), ("BEL", "Capital Goods"),
    ("BHARTIARTL", "Telecommunication"), ("CIPLA", "Healthcare"), ("COALINDIA", "Oil Gas & Fuels"),
    ("DRREDDY", "Healthcare"), ("EICHERMOT", "Automobile"), ("ETERNAL", "Consumer Services"),
    ("GRASIM", "Construction Materials"), ("HCLTECH", "Information Technology"), ("HDFCBANK", "Financial Services"),
    ("HDFCLIFE", "Financial Services"), ("HINDALCO", "Metals & Mining"), ("HINDUNILVR", "FMCG"),
    ("ICICIBANK", "Financial Services"), ("INDIGO", "Services"), ("INFY", "Information Technology"),
    ("ITC", "FMCG"), ("JIOFIN", "Financial Services"), ("JSWSTEEL", "Metals & Mining"),
    ("KOTAKBANK", "Financial Services"), ("LT", "Construction"), ("M&M", "Automobile"), ("MARUTI", "Automobile"),
    ("MAXHEALTH", "Healthcare"), ("NESTLEIND", "FMCG"), ("NTPC", "Power"), ("ONGC", "Oil Gas & Fuels"),
    ("POWERGRID", "Power"), ("RELIANCE", "Oil Gas & Fuels"), ("SBILIFE", "Financial Services"),
    ("SHRIRAMFIN", "Financial Services"), ("SBIN", "Financial Services"), ("SUNPHARMA", "Healthcare"),
    ("TCS", "Information Technology"), ("TATACONSUM", "FMCG"), ("TMPV", "Automobile"), ("TATASTEEL", "Metals & Mining"),
    ("TECHM", "Information Technology"), ("TITAN", "Consumer Durables"), ("TRENT", "Consumer Services"),
    ("ULTRACEMCO", "Construction Materials"), ("WIPRO", "Information Technology"),
]

INDEX_FILES = {
    "NIFTY 100": "ind_nifty100list.csv", "NIFTY 200": "ind_nifty200list.csv", "NIFTY 500": "ind_nifty500list.csv",
    "NIFTY NEXT 50": "ind_niftynext50list.csv", "NIFTY BANK": "ind_niftybanklist.csv", "NIFTY 50": "ind_nifty50list.csv",
}
UNIVERSES = ["NIFTY 50", "NIFTY NEXT 50", "NIFTY 100", "NIFTY 200", "NIFTY 500", "NIFTY BANK", "Custom list"]


def _cache_dir(root: Path) -> Path:
    d = root / "data" / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def download_index_list(name: str, root: Path) -> pd.DataFrame:
    import requests
    url = f"https://niftyindices.com/IndexConstituent/{INDEX_FILES[name]}"
    r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 (PC Trade Scanner)"})
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    df.columns = [c.strip() for c in df.columns]
    out = pd.DataFrame({"symbol": df["Symbol"].str.strip(), "sector": df.get("Industry", "Unclassified"),
                        "isin": df.get("ISIN Code")})
    out.to_csv(_cache_dir(root) / f"universe_{name.replace(' ', '_')}.csv", index=False)
    print(f"[universe] downloaded {name}: {len(out)} symbols")
    return out


def load_universe(cfg: dict, root: Path) -> pd.DataFrame:
    """Returns symbol, sector, isin (isin may be empty) for the configured universe."""
    name = cfg["universe"]["name"]
    if name == "Custom list":
        syms = [s.strip().upper() for s in cfg["universe"]["custom_symbols"].replace("\n", ",").split(",") if s.strip()]
        base = pd.DataFrame(NIFTY50, columns=["symbol", "sector"]).set_index("symbol")["sector"]
        return pd.DataFrame({"symbol": syms, "sector": [base.get(s, "Custom") for s in syms], "isin": None})
    cached = _cache_dir(root) / f"universe_{name.replace(' ', '_')}.csv"
    if cached.exists():
        return pd.read_csv(cached)
    if name == "NIFTY 50":
        return pd.DataFrame(NIFTY50, columns=["symbol", "sector"]).assign(isin=None)
    try:
        return download_index_list(name, root)
    except Exception as exc:
        print(f"[universe] could not download {name} ({exc!r}); falling back to bundled NIFTY 50")
        return pd.DataFrame(NIFTY50, columns=["symbol", "sector"]).assign(isin=None)


def universe_age_days(cfg: dict, root: Path):
    cached = _cache_dir(root) / f"universe_{cfg['universe']['name'].replace(' ', '_')}.csv"
    if not cached.exists():
        return None
    return (date.today() - date.fromtimestamp(cached.stat().st_mtime)).days
