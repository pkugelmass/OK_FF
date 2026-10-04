"""Best starting lineup per league team, hand-entered actual lineups, and team-strength summaries."""
from __future__ import annotations

import json

import pandas as pd

from .config import CONFIG_DIR, DATA_DIR

LINEUP_PATH = CONFIG_DIR / "lineup.json"
STARTERS_PATH = DATA_DIR / "starters.csv"      # team,player rows: who each team is actually starting


def load_slots() -> tuple[list[tuple[str, int]], list[str]]:
    """Returns ([(slot, count), ...] in fill order, flex-eligible positions)."""
    raw = json.loads(LINEUP_PATH.read_text())
    flex_pos = raw.get("FLEX_POSITIONS", ["RB", "WR", "TE"])
    slots = [(k, int(v)) for k, v in raw.items() if not k.startswith("_") and k not in ("FLEX", "FLEX_POSITIONS")]
    if raw.get("FLEX"):
        slots.append(("FLEX", int(raw["FLEX"])))  # flex is filled last, from what's left
    return slots, flex_pos


def starter_count() -> int:
    return sum(n for _, n in load_slots()[0])


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


def check_starters(players: pd.DataFrame) -> str | None:
    """None if these players fit the lineup slots exactly, else a plain-English problem."""
    slots, flex_pos = load_slots()
    need = starter_count()
    if len(players) != need:
        return f"{len(players)} starters selected; the lineup has {need} slots."
    fitted = best_lineup(players, "PPG")
    if len(fitted) != need:
        counts = players["Pos"].value_counts().to_dict()
        return f"Those positions don't fit the slots ({counts})."
    return None


# ---------- hand-entered starting lineups ----------
def load_starters() -> dict[str, list[str]]:
    if not STARTERS_PATH.exists():
        return {}
    df = pd.read_csv(STARTERS_PATH).dropna()
    return {t: g["player"].tolist() for t, g in df.groupby("team")}


def save_starters(team: str, players: list[str]) -> None:
    rows = [(t, p) for t, ps in load_starters().items() if t != team for p in ps]
    rows += [(team, p) for p in players]
    DATA_DIR.mkdir(exist_ok=True)
    pd.DataFrame(rows, columns=["team", "player"]).to_csv(STARTERS_PATH, index=False)


# ---------- team strength ----------
def team_strength(table: pd.DataFrame, free_agent_label: str) -> tuple[pd.DataFrame, dict[str, dict]]:
    """One row per league team with best-lineup totals, actual-lineup totals (if entered), and per-slot PPG.

    Also returns per-team detail: {"best": lineup df, "actual": lineup df or None}.
    """
    rostered = table[table["Owner"] != free_agent_label]
    starters = load_starters()
    rows, details = [], {}
    for owner, roster in rostered.groupby("Owner"):
        best = best_lineup(roster, "PPG")
        week = best_lineup(roster, "Proj")
        row = {
            "Team": owner,
            "Best lineup PPG": round(best["PPG"].sum(), 1),
            "Best week Proj": round(week["Proj"].sum(), 1),
            "Rostered": len(roster),
        }
        actual = None
        if owner in starters:
            picked = roster[roster["full_name"].isin(starters[owner])]
            actual = best_lineup(picked, "PPG")  # assigns slots to the chosen starters
            row["Starting PPG"] = round(picked["PPG"].sum(), 1)
            row["Starting Proj"] = round(picked["Proj"].sum(), 1)
            row["Left on bench"] = round(row["Best lineup PPG"] - row["Starting PPG"], 1)
        for slot, grp in best.groupby("Slot", sort=False):
            row[slot] = round(grp["PPG"].sum(), 1)
        row["Bench PPG"] = round(roster.drop(best.index)["PPG"].sum(), 1)
        rows.append(row)
        cols = ["Slot", "Player", "Pos", "NFL", "Inj", "PPG", "Proj"]
        details[owner] = {
            "best": best[cols].reset_index(drop=True),
            "actual": actual[cols].reset_index(drop=True) if actual is not None else None,
        }

    if not rows:
        return pd.DataFrame(), {}
    summary = pd.DataFrame(rows)
    for c in ("Starting PPG", "Starting Proj", "Left on bench"):
        if c not in summary.columns:
            summary[c] = float("nan")
    summary = summary.sort_values("Best lineup PPG", ascending=False).reset_index(drop=True)
    summary.insert(0, "Rk", range(1, len(summary) + 1))
    summary["vs median"] = (summary["Best lineup PPG"] - summary["Best lineup PPG"].median()).round(1)
    slot_cols = [s for s, _ in load_slots()[0] if s in summary.columns]
    ordered = ["Rk", "Team", "Best lineup PPG", "vs median", "Starting PPG", "Left on bench",
               "Best week Proj", "Starting Proj"] + slot_cols + ["Bench PPG", "Rostered"]
    return summary[ordered], details
