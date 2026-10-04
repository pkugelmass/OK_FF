"""Turn raw stat lines into fantasy points using the league's scoring rules.

Scoring config values are either a number (points per unit of that stat column) or,
for `points_allowed_tiers`, a dict of {upper_bound: points} for D/ST points allowed.
"""
from __future__ import annotations

import pandas as pd

from .config import load_scoring


def _tier_points(points_allowed: pd.Series, tiers: dict) -> pd.Series:
    bounds = sorted((int(k), float(v)) for k, v in tiers.items() if not k.startswith("_"))

    def score(pa):
        if pd.isna(pa):
            return 0.0
        for upper, pts in bounds:
            if pa <= upper:
                return pts
        return bounds[-1][1]

    return points_allowed.map(score)


def apply(weekly: pd.DataFrame, scoring: dict | None = None) -> pd.DataFrame:
    """Add a `points` column: sum of (stat * weight) for every stat in the scoring config."""
    scoring = scoring or load_scoring()
    df = weekly.copy()
    pts = pd.Series(0.0, index=df.index)
    for col, weight in scoring.items():
        if col == "points_allowed_tiers":
            if "points_allowed" in df.columns:
                pts += _tier_points(df["points_allowed"], weight)
        elif col in df.columns:
            pts += df[col].fillna(0).astype(float) * weight
    df["points"] = pts.round(2)
    return df
