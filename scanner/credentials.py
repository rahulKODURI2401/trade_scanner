"""API credentials kept only on this PC in data/credentials.json (never in the
database, never in config versions). Environment variables win if set:
KITE_API_KEY, KITE_ACCESS_TOKEN, UPSTOX_ACCESS_TOKEN."""
from __future__ import annotations

import json
import os
from pathlib import Path

ENV = {"kite_api_key": "KITE_API_KEY", "kite_access_token": "KITE_ACCESS_TOKEN",
       "upstox_access_token": "UPSTOX_ACCESS_TOKEN"}


def _path(root: Path) -> Path:
    return Path(root) / "data" / "credentials.json"


def load_credentials(root: Path) -> dict:
    p = _path(root)
    data = json.loads(p.read_text()) if p.exists() else {}
    for key, env in ENV.items():
        if os.environ.get(env):
            data[key] = os.environ[env]
    return data


def save_credentials(root: Path, values: dict) -> None:
    p = _path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    current = json.loads(p.read_text()) if p.exists() else {}
    current.update({k: v.strip() for k, v in values.items() if v is not None})
    p.write_text(json.dumps(current, indent=2))
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
