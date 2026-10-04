"""Pull player stats, team defense stats, rosters, and schedules from nflverse (free, no login)."""
from __future__ import annotations

import nflreadpy as nfl
import pandas as pd

from .config import FANTASY_POSITIONS, current_season
from . import store

ROSTER_COLS = ["gsis_id", "full_name", "team", "position", "status", "yahoo_id", "sleeper_id", "headshot_url"]


def _dst_rows(season: int, sched: pd.DataFrame, teams: pd.DataFrame) -> pd.DataFrame:
    """One row per team per week shaped like a player stat line, position DEF."""
    t = nfl.load_team_stats([season], summary_level="week").to_pandas()
    t = t[t["season_type"] == "REG"].copy()

    # points allowed = the opponent's final score, from the schedule
    home = sched.rename(columns={"home_team": "team", "away_score": "points_allowed"})[
        ["week", "team", "points_allowed"]
    ]
    away = sched.rename(columns={"away_team": "team", "home_score": "points_allowed"})[
        ["week", "team", "points_allowed"]
    ]
    t = t.merge(pd.concat([home, away]), on=["week", "team"], how="left")

    names = dict(zip(teams["team_abbr"], teams["team_name"]))
    t["def_blocks"] = t["def_punt_blocks"] + t["def_pat_blocks"] + t["def_fg_blocks"]
    t["def_return_tds"] = t["special_teams_tds"]

    # Keep only defensive columns. The team rows also carry the offense's passing/rushing
    # stats, which must not be scored as if the defense earned them.
    keep = [c for c in ["season", "week", "season_type", "team", "opponent_team", "points_allowed",
            "def_blocks", "def_return_tds", "fumble_recovery_opp"] if c in t.columns]
    keep += [c for c in t.columns if c.startswith("def_") and c not in keep]
    t = t[keep].copy()

    t["player_id"] = "DEF_" + t["team"]
    t["player_display_name"] = t["team"].map(names).fillna(t["team"]) + " D/ST"
    t["player_name"] = t["player_display_name"]
    t["position"] = "DEF"
    t["position_group"] = "DEF"
    t["season"] = season
    return t


def refresh(season: int | None = None) -> dict[str, int]:
    """Download the season's weekly stats, rosters, and schedule into SQLite.

    Returns row counts so the UI can show what happened.
    """
    season = season or current_season()

    sched = nfl.load_schedules([season]).to_pandas()
    sched = sched[sched["game_type"] == "REG"][
        ["game_id", "week", "gameday", "away_team", "home_team", "away_score", "home_score"]
    ].copy()
    store.save("schedule", sched)

    teams = nfl.load_teams().to_pandas()[["team_abbr", "team_name", "team_nick", "team_color", "team_color2"]]
    # nflverse lists historical abbreviations too (LAR, STL, SD, OAK); keep only teams playing this season
    active = set(sched["home_team"]) | set(sched["away_team"])
    teams = teams[teams["team_abbr"].isin(active)].drop_duplicates("team_abbr").reset_index(drop=True)
    store.save("teams", teams)

    weekly = nfl.load_player_stats([season], summary_level="week").to_pandas()
    weekly = weekly[weekly["season_type"] == "REG"]
    weekly = weekly[weekly["position"].isin(FANTASY_POSITIONS)].copy()
    weekly = pd.concat([weekly, _dst_rows(season, sched, teams)], ignore_index=True)
    store.save("weekly_stats", weekly)

    rosters = nfl.load_rosters([season]).to_pandas()
    rosters = rosters[rosters["position"].isin(FANTASY_POSITIONS)]
    rosters = rosters.drop_duplicates("gsis_id")[ROSTER_COLS].copy()
    dst_rosters = pd.DataFrame({
        "gsis_id": "DEF_" + teams["team_abbr"],
        "full_name": teams["team_name"] + " D/ST",
        "team": teams["team_abbr"],
        "position": "DEF",
        "status": "ACT",
        "yahoo_id": None,
        "sleeper_id": None,
        "headshot_url": None,
    })
    rosters = pd.concat([rosters, dst_rosters], ignore_index=True)
    store.save("rosters", rosters)

    # Latest injury report line per player (Out / Doubtful / Questionable)
    inj = nfl.load_injuries([season]).to_pandas()
    inj = inj[inj["season_type"] == "REG"].sort_values("week")
    inj = inj.drop_duplicates("gsis_id", keep="last")[
        ["gsis_id", "week", "report_status", "report_primary_injury", "practice_status"]
    ]
    store.save("injuries", inj)

    return {"weekly_stats": len(weekly), "rosters": len(rosters), "schedule": len(sched), "injuries": len(inj)}


def weekly_stats() -> pd.DataFrame | None:
    return store.load("weekly_stats")


def rosters() -> pd.DataFrame | None:
    return store.load("rosters")


def schedule() -> pd.DataFrame | None:
    return store.load("schedule")


def teams() -> pd.DataFrame | None:
    return store.load("teams")


def injuries() -> pd.DataFrame | None:
    return store.load("injuries")


def latest_completed_week(sched: pd.DataFrame) -> int:
    """Highest week where every game has a score."""
    done = sched.dropna(subset=["home_score", "away_score"])
    if done.empty:
        return 0
    counts = sched.groupby("week").size()
    done_counts = done.groupby("week").size()
    complete = [w for w in counts.index if done_counts.get(w, 0) == counts[w]]
    return max(complete) if complete else 0
