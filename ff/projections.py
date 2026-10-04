"""Homemade next-week projection.

    projection = blend(recent form, season average) * opponent factor

- recent form: weighted mean of the last 3 games (3/6, 2/6, 1/6)
- season average: points per game played
- opponent factor: how many points the next opponent allows to this position
  versus the league average, shrunk toward 1.0 so a few games don't swing it wildly.

Simple on purpose. Every knob is at the top so you can experiment.
"""
from __future__ import annotations

import pandas as pd

RECENT_WEIGHTS = [3, 2, 1]      # most recent game first
RECENT_BLEND = 0.6              # 60% recent form, 40% season average
OPP_SHRINK = 0.5                # 0 = ignore opponent, 1 = trust it fully

DEFAULTS = {"blend": RECENT_BLEND, "shrink": OPP_SHRINK}


def _recent_avg(points: pd.Series) -> float:
    pts = list(points)[-len(RECENT_WEIGHTS):][::-1]
    w = RECENT_WEIGHTS[: len(pts)]
    return sum(p * wt for p, wt in zip(pts, w)) / sum(w) if pts else 0.0


def opponent_factors(scored: pd.DataFrame, shrink: float = OPP_SHRINK) -> pd.DataFrame:
    """Points each defense allows per game to each position, relative to league average."""
    allowed = (
        scored.groupby(["opponent_team", "position", "week"])["points"].sum().reset_index()
        .groupby(["opponent_team", "position"])["points"].mean().reset_index()
        .rename(columns={"opponent_team": "team", "points": "allowed_pg"})
    )
    league_avg = allowed.groupby("position")["allowed_pg"].transform("mean")
    raw = allowed["allowed_pg"] / league_avg
    allowed["opp_factor"] = 1 + shrink * (raw - 1)
    return allowed[["team", "position", "opp_factor"]]


def next_opponents(schedule: pd.DataFrame, week: int) -> dict[str, str]:
    games = schedule[schedule["week"] == week]
    opp = {}
    for _, g in games.iterrows():
        opp[g["home_team"]] = g["away_team"]
        opp[g["away_team"]] = g["home_team"]
    return opp


def project(scored: pd.DataFrame, schedule: pd.DataFrame, next_week: int,
            blend: float = RECENT_BLEND, shrink: float = OPP_SHRINK) -> pd.DataFrame:
    """One row per player with recent_avg, season_avg, next_opp, opp_factor, projection.

    blend:  weight on recent form (the rest goes on season average)
    shrink: how much to trust the opponent factor (0 = ignore, 1 = fully)
    """
    scored = scored.sort_values(["player_id", "week"])
    g = scored.groupby("player_id")
    out = pd.DataFrame({
        "player_id": g.size().index,
        "team": g["team"].last().values,
        "position": g["position"].last().values,
        "recent_avg": g["points"].apply(_recent_avg).values,
        "season_avg": g["points"].mean().values,
    })
    base = blend * out["recent_avg"] + (1 - blend) * out["season_avg"]

    opps = next_opponents(schedule, next_week)
    out["next_opp"] = out["team"].map(opps).fillna("BYE")
    factors = opponent_factors(scored, shrink).rename(columns={"team": "next_opp"})
    out = out.merge(factors, on=["next_opp", "position"], how="left")
    out["opp_factor"] = out["opp_factor"].fillna(1.0)
    out["projection"] = (base * out["opp_factor"]).where(out["next_opp"] != "BYE", 0.0)
    return out.round({"recent_avg": 1, "season_avg": 1, "opp_factor": 2, "projection": 1})
