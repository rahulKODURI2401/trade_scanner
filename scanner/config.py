"""Configuration loading, saving and versioning.

The YAML file under config/ holds the defaults. User edits made in Settings
are stored as versioned rows in SQLite (scan_config table); the active config
is the latest version. Every scan and backtest records the version it used.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT / "config" / "default_config.yaml"


def load_default_config() -> dict:
    with open(DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def config_hash(cfg: dict) -> str:
    payload = json.dumps(cfg, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha1(payload).hexdigest()[:10]


def merge_config(base: dict, override: dict) -> dict:
    """Deep-merge override onto base (override wins)."""
    out = copy.deepcopy(base)
    for key, val in (override or {}).items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = merge_config(out[key], val)
        else:
            out[key] = val
    return out
