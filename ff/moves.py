"""Waiver pickups and trade ideas, measured by how much they change a team's best lineup.

The one idea behind everything here: a player's value to YOU is the change in your best possible
lineup if you add him, not his raw points. Most free agents are worth zero to a given team.
"""
from __future__ import annotations

import pandas as pd

from . import lineup

OPP_COLS = {"QB": ["attempts", "carries"], "RB": ["carries", "targets"], "WR": ["targets"], "TE": ["targets"]}


def _pairs(df: pd.DataFrame, metric: str) -> list[tuple[str, float]]:
    return list(zip(df["Pos"], df[metric]))


def _starters(df: pd.DataFrame, metric: str) -> set[str]:
    return set(lineup.best_lineup(df, metric)["full_name"])


def opportunity_trend(scored: pd.DataFrame) -> pd.Series:
    """Per player: opportunities per game over the last 3 weeks minus season per game. Positive = role growing."""
    df = scored.copy()
    df["opp"] = 0.0
    for pos, cols in OPP_COLS.items():
        m = df["position"] == pos
        df.loc[m, "opp"] = df.loc[m, cols].fillna(0).sum(axis=1)
    df = df[df["position"].isin(OPP_COLS)].sort_values(["player_id", "week"])
    season = df.groupby("player_id")["opp"].mean()
    last3 = df.groupby("player_id").tail(3).groupby("player_id")["opp"].mean()
    return (last3 - season).round(1)


def pickups(table: pd.DataFrame, my_team: str, free_agent: str, scored: pd.DataFrame) -> pd.DataFrame:
    """Free agents ranked by how much they'd raise my best lineup (season PPG and this week's projection)."""
    mine = table[table["Owner"] == my_team]
    fas = table[table["Owner"] == free_agent]
    if mine.empty or fas.empty:
        return pd.DataFrame()
    base_ppg = lineup.lineup_value(_pairs(mine, "PPG"))
    base_proj = lineup.lineup_value(_pairs(mine, "Proj"))
    my_pairs_ppg, my_pairs_proj = _pairs(mine, "PPG"), _pairs(mine, "Proj")
    start_now = _starters(mine, "PPG")

    rows = []
    for _, fa in fas.iterrows():
        g_ppg = lineup.lineup_value(my_pairs_ppg + [(fa["Pos"], fa["PPG"])]) - base_ppg
        g_proj = lineup.lineup_value(my_pairs_proj + [(fa["Pos"], fa["Proj"])]) - base_proj
        if g_ppg <= 0 and g_proj <= 0:
            continue
        replaced = ""
        if g_ppg > 0:
            after = _starters(pd.concat([mine, fa.to_frame().T]), "PPG")
            out = start_now - after
            replaced = next(iter(out), "")
        rows.append({
            "Player": fa["Player"], "Pos": fa["Pos"], "NFL": fa["NFL"], "Inj": fa["Inj"],
            "PPG": fa["PPG"], "Last3": fa["Last3"], "Proj": fa["Proj"],
            "Gain (PPG)": round(g_ppg, 1), "Gain (this week)": round(g_proj, 1),
            "Would replace": replaced, "player_id": fa["player_id"],
        })
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    trend = opportunity_trend(scored)
    out["Opp trend"] = out["player_id"].map(trend)
    out = out.drop(columns=["player_id"]).sort_values(["Gain (PPG)", "Gain (this week)"], ascending=False)
    return out.reset_index(drop=True)


def coverage(table: pd.DataFrame, my_team: str, free_agent: str) -> pd.DataFrame:
    """My starters who can't go this week (bye or Out/IR), with the best free agent at that position."""
    mine = table[table["Owner"] == my_team]
    if mine.empty:
        return pd.DataFrame()
    starters = lineup.best_lineup(mine, "PPG")
    gone = starters[(starters["Opp"] == "BYE") | starters["Inj"].isin(["Out", "IR", "PUP", "NFI", "Susp"])]
    fas = table[(table["Owner"] == free_agent)]
    rows = []
    for _, p in gone.iterrows():
        best = fas[(fas["Pos"] == p["Pos"]) & (fas["Opp"] != "BYE")].sort_values("Proj", ascending=False).head(1)
        rows.append({
            "Your starter": p["Player"], "Pos": p["Pos"],
            "Why": "Bye" if p["Opp"] == "BYE" else p["Inj"],
            "Best free agent": best["Player"].iloc[0] if len(best) else "",
            "FA Proj": best["Proj"].iloc[0] if len(best) else None,
        })
    return pd.DataFrame(rows)


def trades(table: pd.DataFrame, my_team: str, free_agent: str, max_rival_loss: float = 1.0,
           min_my_gain: float = 0.5) -> pd.DataFrame:
    """Every 1-for-1 swap between my roster and each rival's, scored by both teams' best-lineup change.

    Keeps swaps where I gain at least `min_my_gain` PPG and the rival loses no more than `max_rival_loss`
    (a rival who also gains is a trade they'd actually accept).
    """
    mine = table[table["Owner"] == my_team]
    if mine.empty:
        return pd.DataFrame()
    my_base = lineup.lineup_value(_pairs(mine, "PPG"))
    my_list = list(zip(mine["full_name"], mine["Player"], mine["Pos"], mine["PPG"]))

    # value over replacement: PPG minus the best free agent at that position
    fa_best = table[table["Owner"] == free_agent].groupby("Pos")["PPG"].max().to_dict()

    rows = []
    for rival, roster in table[~table["Owner"].isin([my_team, free_agent])].groupby("Owner"):
        r_base = lineup.lineup_value(_pairs(roster, "PPG"))
        r_list = list(zip(roster["full_name"], roster["Player"], roster["Pos"], roster["PPG"]))
        for m_name, m_disp, m_pos, m_val in my_list:
            my_rest = [(p, v) for n, _, p, v in my_list if n != m_name]
            for r_name, r_disp, r_pos, r_val in r_list:
                if m_pos in ("K", "DEF") and r_pos in ("K", "DEF"):
                    continue
                my_gain = lineup.lineup_value(my_rest + [(r_pos, r_val)]) - my_base
                if my_gain < min_my_gain:
                    continue
                r_rest = [(p, v) for n, _, p, v in r_list if n != r_name]
                r_gain = lineup.lineup_value(r_rest + [(m_pos, m_val)]) - r_base
                if r_gain < -max_rival_loss:
                    continue
                rows.append({
                    "Rival": rival, "You give": f"{m_disp} ({m_pos})", "You get": f"{r_disp} ({r_pos})",
                    "Your gain": round(my_gain, 1), "Their gain": round(r_gain, 1),
                    "Give PPG": m_val, "Get PPG": r_val,
                    "Give VORP": round(m_val - fa_best.get(m_pos, 0), 1),
                    "Get VORP": round(r_val - fa_best.get(r_pos, 0), 1),
                })
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["Fairness"] = (out["Their gain"] - out["Your gain"]).abs().round(1)
    return out.sort_values(["Your gain", "Their gain"], ascending=False).reset_index(drop=True)


def buy_low_sell_high(table: pd.DataFrame, my_team: str, free_agent: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Buy low: rostered elsewhere, good season, cold last 3. Sell high: mine, hot last 3 vs season."""
    others = table[~table["Owner"].isin([my_team, free_agent])].copy()
    others["Cold by"] = (others["PPG"] - others["Last3"]).round(1)
    buy = others[(others["G"] >= 3) & (others["PPG"] >= 8) & (others["Cold by"] >= 3)]
    buy = buy.sort_values("Cold by", ascending=False)[["Player", "Pos", "NFL", "Owner", "PPG", "Last3", "Cold by", "Inj"]]

    mine = table[table["Owner"] == my_team].copy()
    mine["Hot by"] = (mine["Last3"] - mine["PPG"]).round(1)
    sell = mine[(mine["G"] >= 3) & (mine["Hot by"] >= 3)]
    sell = sell.sort_values("Hot by", ascending=False)[["Player", "Pos", "NFL", "PPG", "Last3", "Hot by", "Inj"]]
    return buy.head(15).reset_index(drop=True), sell.reset_index(drop=True)
