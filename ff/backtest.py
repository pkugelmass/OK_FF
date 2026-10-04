"""How good are the projections? Re-run the model for each past week using only the
data that was available before that week, then compare with what actually happened.

Two naive baselines are included so "good" means something:
  - season average: project every player at their per-game average so far
  - last game: project every player at their previous game's points
If the model can't beat those, the extra machinery isn't earning its keep.
"""
from __future__ import annotations

import pandas as pd

from . import projections

# How many players per position count as "fantasy relevant" for the accuracy check.
# Benchwarmers who score 0.3 points every week would make any model look great.
RELEVANT = {"QB": 24, "RB": 48, "WR": 60, "TE": 24, "K": 24, "DEF": 32}


def run(scored: pd.DataFrame, schedule: pd.DataFrame, last_week: int, first_week: int = 2,
        blend: float = projections.RECENT_BLEND, shrink: float = projections.OPP_SHRINK) -> pd.DataFrame:
    """One row per (week, player): actual points plus each method's projection."""
    rows = []
    for week in range(first_week, last_week + 1):
        hist = scored[scored["week"] < week]
        if hist.empty:
            continue
        proj = projections.project(hist, schedule, week, blend, shrink)
        last_game = hist.sort_values("week").groupby("player_id")["points"].last().rename("last_game")
        proj = proj.merge(last_game, on="player_id", how="left")

        # keep the top-N per position by season average at that time
        proj["rank"] = proj.groupby("position")["season_avg"].rank(ascending=False, method="first")
        proj = proj[proj["rank"] <= proj["position"].map(RELEVANT).fillna(30)]
        proj = proj[proj["next_opp"] != "BYE"]

        actual = scored[scored["week"] == week][["player_id", "points"]].rename(columns={"points": "actual"})
        merged = proj.merge(actual, on="player_id", how="inner")
        merged["week"] = week
        rows.append(merged[["week", "player_id", "position", "actual", "projection", "season_avg", "last_game"]])
    if not rows:
        return pd.DataFrame(columns=["week", "player_id", "position", "actual", "projection", "season_avg", "last_game"])
    return pd.concat(rows, ignore_index=True)


def summarize(results: pd.DataFrame) -> pd.DataFrame:
    """Mean absolute error and correlation for each method, by position and overall."""
    if results.empty:
        return pd.DataFrame()
    methods = {"Model": "projection", "Season avg": "season_avg", "Last game": "last_game"}

    def stats(df: pd.DataFrame) -> dict:
        out = {"Player-weeks": len(df)}
        for name, col in methods.items():
            out[f"{name} MAE"] = (df[col] - df["actual"]).abs().mean()
            out[f"{name} corr"] = df[col].corr(df["actual"])
        return out

    by_pos = {pos: stats(g) for pos, g in results.groupby("position")}
    by_pos["ALL"] = stats(results)
    table = pd.DataFrame(by_pos).T
    order = ["QB", "RB", "WR", "TE", "K", "DEF", "ALL"]
    table = table.reindex([p for p in order if p in table.index])
    return table.round(2)
