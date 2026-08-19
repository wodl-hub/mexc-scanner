"""Local JSON store. Secrets never leave this machine."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

SETTINGS_PATH = DATA / "settings.json"
PNL_PATH = DATA / "pnl.json"
ORDERS_PATH = DATA / "orders.json"
HIDDEN_PATH = DATA / "hidden.json"

DEFAULT_SETTINGS = {
    "odds_api_key": "",
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "polymarket_private_key": "",
    "polymarket_funder": "",
    "signature_type": 2,
    "stake_mirror": "",
    "sound": True,
    "auto_refresh_sec": 20,
    "min_roi": 0.0,
}


def _read(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def load_settings() -> dict:
    data = {**DEFAULT_SETTINGS, **(_read(SETTINGS_PATH, {}) or {})}
    return data


def save_settings(patch: dict) -> dict:
    current = load_settings()
    for key in DEFAULT_SETTINGS:
        if key in patch and patch[key] is not None:
            current[key] = patch[key]
    _write(SETTINGS_PATH, current)
    return current


def public_settings() -> dict:
    s = load_settings()
    key = s.get("polymarket_private_key") or ""
    odds = s.get("odds_api_key") or ""
    tg = s.get("telegram_bot_token") or ""
    return {
        **{k: v for k, v in s.items() if k != "polymarket_private_key"},
        "polymarket_private_key": ("*" * 8 + key[-4:]) if len(key) > 8 else ("" if not key else "set"),
        "has_private_key": bool(key),
        "has_odds_key": bool(odds),
        "has_telegram": bool(tg and s.get("telegram_chat_id")),
        "odds_api_key": ("*" * 4 + odds[-4:]) if len(odds) > 6 else odds,
        "telegram_bot_token": ("*" * 4 + tg[-4:]) if len(tg) > 6 else tg,
    }


def load_list(path: Path) -> list:
    data = _read(path, [])
    return data if isinstance(data, list) else []


def save_list(path: Path, rows: list) -> list:
    _write(path, rows)
    return rows
