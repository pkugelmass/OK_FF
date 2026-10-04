"""Yahoo Fantasy league sync via yfpy.

Dormant until .env has YAHOO_CONSUMER_KEY, YAHOO_CONSUMER_SECRET, and YAHOO_LEAGUE_ID.
The first sync opens a browser window to authorize; yfpy then stores the refresh
token in .env so later syncs are silent.

NOTE: written against yfpy 17 model definitions but not yet exercised against the live
API (we are waiting on Yahoo's approval). Expect to tweak field names on the first run.
"""
from __future__ import annotations

import json
import os

import pandas as pd

from .config import ENV_PATH, SCORING_LEAGUE, yahoo_configured
from . import store

# Yahoo stat_id -> nflverse weekly_stats column. Add more if your league scores them.
STAT_ID_TO_COLUMN: dict[int, str] = {
    4: "passing_yards",
    5: "passing_tds",
    6: "passing_interceptions",
    9: "rushing_yards",
    10: "rushing_tds",
    11: "receptions",
    12: "receiving_yards",
    13: "receiving_tds",
    15: "special_teams_tds",          # return TDs
    16: "passing_2pt_conversions",    # Yahoo lumps all 2-pt conversions into one stat
    18: "fumbles_lost_total",
    19: "fg_made_0_19",
    20: "fg_made_20_29",
    21: "fg_made_30_39",
    22: "fg_made_40_49",
    23: "fg_made_50_59",
    24: "fg_missed_0_19",
    25: "fg_missed_20_29",
    26: "fg_missed_30_39",
    27: "fg_missed_40_49",
    28: "fg_missed_50_59",
    29: "pat_made",
    30: "pat_missed",
    57: "fumble_recovery_tds",
    # D/ST
    32: "def_sacks",
    33: "def_interceptions",
    34: "fumble_recovery_opp",
    35: "def_tds",
    36: "def_safeties",
    37: "def_blocks",
    49: "def_return_tds",
}
# Yahoo points-allowed tiers: stat_id -> upper bound of the tier
POINTS_ALLOWED_TIERS = {50: 0, 51: 6, 52: 13, 53: 20, 54: 27, 55: 34, 56: 999}
# Yahoo stat 16 covers rushing/receiving 2-pt too; mirror it onto those columns.
MIRROR = {"passing_2pt_conversions": ["rushing_2pt_conversions", "receiving_2pt_conversions"],
          "fg_made_50_59": ["fg_made_60_"]}


def _query():
    from yfpy import YahooFantasySportsQuery

    return YahooFantasySportsQuery(
        league_id=os.environ["YAHOO_LEAGUE_ID"],
        game_code="nfl",
        yahoo_consumer_key=os.environ["YAHOO_CONSUMER_KEY"],
        yahoo_consumer_secret=os.environ["YAHOO_CONSUMER_SECRET"],
        env_file_location=ENV_PATH.parent,
        save_token_data_to_env_file=True,
    )


def _text(v) -> str:
    return v.decode() if isinstance(v, bytes) else str(v)


def scoring_from_settings(settings) -> tuple[dict[str, float], list[str]]:
    """Map Yahoo stat modifiers onto nflverse columns. Returns (scoring, unmapped stat names)."""
    names = {s.stat_id: s.display_name for s in settings.stat_categories.stats}
    scoring: dict[str, float] = {}
    unmapped: list[str] = []
    tiers: dict[str, float] = {}
    for mod in settings.stat_modifiers.stats:
        if mod.stat_id in POINTS_ALLOWED_TIERS:
            tiers[str(POINTS_ALLOWED_TIERS[mod.stat_id])] = float(mod.value)
            continue
        col = STAT_ID_TO_COLUMN.get(mod.stat_id)
        if col is None:
            unmapped.append(f"{mod.stat_id}:{names.get(mod.stat_id, '?')}")
            continue
        scoring[col] = float(mod.value)
        for extra in MIRROR.get(col, []):
            scoring[extra] = float(mod.value)
    if tiers:
        scoring["points_allowed_tiers"] = tiers
    return scoring, unmapped


def sync() -> dict:
    """Pull league settings + every team's roster into config/scoring.json and SQLite."""
    if not yahoo_configured():
        raise RuntimeError("Yahoo credentials missing from .env")

    q = _query()

    settings = q.get_league_settings()
    scoring, unmapped = scoring_from_settings(settings)
    payload = {"_comment": "Synced from Yahoo league settings. Unmapped Yahoo stats: " + ", ".join(unmapped)}
    payload.update(scoring)
    SCORING_LEAGUE.parent.mkdir(exist_ok=True)
    SCORING_LEAGUE.write_text(json.dumps(payload, indent=2))

    teams = q.get_league_teams()
    rows = []
    for t in teams:
        for p in q.get_team_roster_player_info_by_week(t.team_id, "current"):
            rows.append({
                "team_id": t.team_id,
                "team_name": _text(t.name),
                "yahoo_id": p.player_id,
                "player_name": p.full_name or (p.name.full if p.name else ""),
                "position": p.display_position,
                "nfl_team": p.editorial_team_abbr,
                "injury_note": p.injury_note,
            })
    rosters = pd.DataFrame(rows)
    store.save("yahoo_rosters", rosters)

    roster_positions = pd.DataFrame([
        {"position": rp.position, "count": rp.count, "is_starting": rp.is_starting_position}
        for rp in settings.roster_positions
    ])
    store.save("yahoo_roster_positions", roster_positions)

    return {"teams": len(teams), "rostered": len(rosters), "unmapped_stats": unmapped}
