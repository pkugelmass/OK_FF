"""Paths, environment, and small helpers shared by every module."""
from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "fantasy.sqlite"
ENV_PATH = ROOT / ".env"

SCORING_DEFAULT = CONFIG_DIR / "scoring.default.json"
SCORING_LEAGUE = DATA_DIR / "scoring.json"            # written by Yahoo sync (local, not in git)
POSITIONS_PATH = CONFIG_DIR / "positions.json"
MANUAL_ROSTERS = DATA_DIR / "rosters.csv"             # your league rosters (local, not in git)

FANTASY_POSITIONS = ["QB", "RB", "WR", "TE", "K", "DEF"]

# Bump this whenever ff/stats.py changes what it stores. The app then re-downloads on next launch
# instead of running new code against old data.
DATA_VERSION = "2026-10-04.2"
# How old the stats may be before the app refreshes them on its own.
AUTO_REFRESH_HOURS = 6

load_dotenv(ENV_PATH)


def yahoo_configured() -> bool:
    """True when all three Yahoo settings are present in .env."""
    return all(
        os.getenv(k) for k in ("YAHOO_CONSUMER_KEY", "YAHOO_CONSUMER_SECRET", "YAHOO_LEAGUE_ID")
    )


def load_scoring() -> dict[str, float]:
    """League scoring if Yahoo has synced it, otherwise the shipped default."""
    path = SCORING_LEAGUE if SCORING_LEAGUE.exists() else SCORING_DEFAULT
    raw = json.loads(path.read_text())
    out = {}
    for k, v in raw.items():
        if k.startswith("_"):
            continue
        out[k] = v if isinstance(v, dict) else float(v)
    return out


def scoring_source() -> str:
    return "Yahoo league settings" if SCORING_LEAGUE.exists() else "default (full PPR)"


def load_position_limits() -> dict[str, int]:
    raw = json.loads(POSITIONS_PATH.read_text())
    return {k: int(v) for k, v in raw.items() if not k.startswith("_")}


SETTINGS_PATH = DATA_DIR / "settings.json"       # small per-computer settings, e.g. which league team is yours


def load_settings() -> dict:
    if SETTINGS_PATH.exists():
        return json.loads(SETTINGS_PATH.read_text())
    return {}


def save_setting(key: str, value) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    s = load_settings()
    s[key] = value
    SETTINGS_PATH.write_text(json.dumps(s, indent=2))


def current_season() -> int:
    """NFL season year: the season that starts in September of a given year."""
    from datetime import date
    today = date.today()
    return today.year if today.month >= 8 else today.year - 1
