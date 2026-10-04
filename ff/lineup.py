"""Best starting lineup per league team, and team-strength summaries for the Teams tab."""
from __future__ import annotations

import json

import pandas as pd

from .config import CONFIG_DIR

LINEUP_PATH = CONFIG_DIR / "lineup.json"


def load_slots() -> tuple[list[tuple[str, int]], list[str]]:
    """Returns ([(slot, count), ...] in fill order, flex-eligible positions)."""
    raw = json.loads(LINEUP_PATH.read_text())
    flex_pos = raw.get("FLEX_POSITIONS", ["RB", "WR", "TE"])
    slots = [(k, int(v)) for k, v in raw.items() if not k.startswith("_") and k != "FLEX_POSITIONS" and k != "FLEX"]
    if raw.get("FLEX"):
        slots.append(("FLEX", int(raw["FLEX"])))  # flex is filled last, from what's left
    return slots, flex_pos


def best_lineup(players: pd.DataFrame, metric: str) -> pd.DataFrame:
    """Greedy best lineup: fill each fixed slot with the top players by `metric`, then flex from the rest.

    Returns the chosen rows with a `Slot` column, in slot order.
    """
    slots, flex_pos = load_slots()
    pool = players.sort_values(metric, ascending=False).copy()
    pool[metric] = pool[metric].fillna(0)
    chosen = []
    for slot, count in slots:
        eligible = pool[pool["Pos"].isin(flex_pos)] if slot == "FLEX" else pool[pool["Pos"] == slot]
        picks = eligible.head(count).copy()
        picks["Slot"] = slot
        chosen.append(picks)
        pool = pool.drop(picks.index)
    if not chosen:
        return players.iloc[0:0].assign(Slot=[])
    return pd.concat(chosen)


def team_strength(table: pd.DataFrame, free_agent_label: str) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """One row per league team: lineup totals by season PPG and by this week's projection,
    plus each slot's PPG. Also returns each team's chosen PPG lineup for the detail view."""
    rostered = table[table["Owner"] != free_agent_label]
    rows, details = [], {}
    for owner, roster in rostered.groupby("Owner"):
        season = best_lineup(roster, "PPG")
        week = best_lineup(roster, "Proj")
        row = {
            "Team": owner,
            "Lineup PPG": round(season["PPG"].sum(), 1),
            "This week Proj": round(week["Proj"].sum(), 1),
            "Rostered": len(roster),
        }
        # per-slot PPG (sum when a slot has multiple players, e.g. RB x2)
        for slot, grp in season.groupby("Slot", sort=False):
            row[slot] = round(grp["PPG"].sum(), 1)
        bench = roster.drop(season.index)
        row["Bench PPG"] = round(bench["PPG"].sum(), 1)
        rows.append(row)
        details[owner] = season[["Slot", "Player", "Pos", "NFL", "Inj", "PPG", "Proj"]].reset_index(drop=True)

    if not rows:
        return pd.DataFrame(), {}
    summary = pd.DataFrame(rows).sort_values("Lineup PPG", ascending=False).reset_index(drop=True)
    summary.insert(0, "Rk", range(1, len(summary) + 1))
    slot_cols = [s for s, _ in load_slots()[0]]
    summary["vs median"] = (summary["Lineup PPG"] - summary["Lineup PPG"].median()).round(1)
    ordered = ["Rk", "Team", "Lineup PPG", "vs median", "This week Proj"] + \
              [c for c in slot_cols if c in summary.columns] + ["Bench PPG", "Rostered"]
    return summary[ordered], details
