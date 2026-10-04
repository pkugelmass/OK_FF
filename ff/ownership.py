"""Who owns each player in the league.

Source priority:
  1. `yahoo_rosters` table (filled by ff.yahoo.sync when credentials exist)
  2. data/rosters.csv, typed in by hand as a stopgap
Anyone not listed is a free agent.

Defenses are keyed as "DEF_<abbr>" (e.g. DEF_SF) in every source so they match our synthetic ids.
"""
from __future__ import annotations

import re
import unicodedata

import pandas as pd

from .config import MANUAL_ROSTERS
from . import store

FREE_AGENT = "Free Agent"

# Yahoo abbreviations that differ from nflverse
TEAM_ALIASES = {"LAR": "LA", "JAC": "JAX", "WSH": "WAS"}


def normalize_name(name: str) -> str:
    """'Jaxon Smith-Njigba Jr.' -> 'jaxonsmithnjigba' so sources with different spellings match."""
    if not isinstance(name, str):
        return ""
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    n = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", n, flags=re.I)
    return re.sub(r"[^a-z]", "", n.lower())


def normalize_team(abbr: str) -> str:
    a = str(abbr).upper().strip()
    return TEAM_ALIASES.get(a, a)


def source() -> str:
    if store.load("yahoo_rosters") is not None:
        return "Yahoo"
    if MANUAL_ROSTERS.exists():
        return "manual rosters.csv"
    return "none"


def _team_lookup() -> dict[str, str]:
    """'SF' / 'san francisco 49ers' / '49ers' -> 'SF'."""
    teams = store.load("teams")
    if teams is None:
        return {}
    lookup = {a: a for a in teams["team_abbr"]}
    lookup.update({normalize_name(n): a for n, a in zip(teams["team_name"], teams["team_abbr"])})
    lookup.update({normalize_name(n): a for n, a in zip(teams["team_nick"], teams["team_abbr"])})
    return lookup


def _manual_key(player: str, lookup: dict[str, str]) -> str:
    """A defense can be written as 'SF', 'SF D/ST', or 'San Francisco 49ers D/ST'."""
    raw = str(player).replace("D/ST", "").replace("DST", "").strip()
    abbr = lookup.get(normalize_team(raw)) or lookup.get(normalize_name(raw))
    return f"DEF_{abbr}" if abbr else normalize_name(player)


def table() -> pd.DataFrame:
    """Columns: owner, name_key, yahoo_id (may be null)."""
    yahoo = store.load("yahoo_rosters")
    if yahoo is not None and not yahoo.empty:
        out = yahoo.rename(columns={"team_name": "owner"})[
            ["owner", "player_name", "yahoo_id", "position", "nfl_team"]
        ].copy()
        out["name_key"] = out["player_name"].map(normalize_name)
        is_def = out["position"] == "DEF"
        out.loc[is_def, "name_key"] = "DEF_" + out.loc[is_def, "nfl_team"].map(normalize_team)
        return out[["owner", "name_key", "yahoo_id"]]

    if MANUAL_ROSTERS.exists():
        manual = pd.read_csv(MANUAL_ROSTERS)
        manual = manual[~manual["team"].astype(str).str.startswith("Example")]
        lookup = _team_lookup()
        return pd.DataFrame({
            "owner": manual["team"],
            "name_key": manual["player"].map(lambda p: _manual_key(p, lookup)),
            "yahoo_id": None,
        })

    return pd.DataFrame(columns=["owner", "name_key", "yahoo_id"])


def manual_rosters() -> pd.DataFrame:
    """The hand-entered rosters file as team,player rows (without the Example rows)."""
    if not MANUAL_ROSTERS.exists():
        return pd.DataFrame(columns=["team", "player"])
    df = pd.read_csv(MANUAL_ROSTERS).dropna()
    return df[~df["team"].astype(str).str.startswith("Example")].reset_index(drop=True)


def save_manual_rosters(df: pd.DataFrame) -> None:
    df = df[["team", "player"]].drop_duplicates()
    MANUAL_ROSTERS.parent.mkdir(exist_ok=True)
    df.to_csv(MANUAL_ROSTERS, index=False)


def set_team(team: str, players: list[str]) -> pd.DataFrame:
    """Replace one league team's roster in rosters.csv. Returns the full file."""
    current = manual_rosters()
    current = current[current["team"] != team]
    new = pd.DataFrame({"team": team, "player": players})
    merged = pd.concat([current, new], ignore_index=True)
    save_manual_rosters(merged)
    return merged


def extract_players(text: str, rosters: pd.DataFrame, teams: pd.DataFrame | None) -> list[str]:
    """Find every known player name in a blob of text copied from a Yahoo roster page.

    Returns display names as they appear in our roster list, with defenses as '<abbr> D/ST'.
    """
    blob = normalize_name(text)
    found: list[str] = []
    for name in rosters["full_name"].dropna().unique():
        if name.endswith(" D/ST"):
            continue
        key = normalize_name(name)
        if len(key) >= 6 and key in blob:
            found.append(name)
    # Yahoo shows a defense as its city/name next to "DEF", e.g. "San Francisco  SF - DEF"
    if teams is not None:
        for _, t in teams.iterrows():
            city = t["team_name"].replace(t["team_nick"], "").strip()
            for label in (t["team_name"], city, t["team_nick"]):
                if re.search(re.escape(label) + r"[\s\S]{0,40}?\bDEF\b", text, flags=re.I):
                    found.append(f"{t['team_abbr']} D/ST")
                    break
    return sorted(set(found))


def unmatched(players: pd.DataFrame) -> pd.DataFrame:
    """Roster entries that didn't match any known player (typos, retired players, etc.)."""
    own = table()
    if own.empty:
        return pd.DataFrame(columns=["owner", "name"])
    keys = set(players["full_name"].map(normalize_name))
    if "player_id" in players.columns:
        keys |= set(players["player_id"].astype(str))
    miss = own[~own["name_key"].isin(keys)]
    src = manual_rosters() if store.load("yahoo_rosters") is None else None
    names = []
    for _, r in miss.iterrows():
        label = r["name_key"]
        if src is not None:
            lookup = _team_lookup()
            hit = src[src["player"].map(lambda p: _manual_key(p, lookup)) == r["name_key"]]
            if not hit.empty:
                label = hit["player"].iloc[0]
        names.append({"owner": r["owner"], "name": label})
    return pd.DataFrame(names, columns=["owner", "name"])


def attach(players: pd.DataFrame) -> pd.DataFrame:
    """Add an `owner` column to a players frame that has `full_name`, `player_id`, and optionally `yahoo_id`."""
    own = table()
    df = players.copy()
    df["owner"] = FREE_AGENT
    if own.empty:
        return df

    # Prefer the exact Yahoo ID match, fall back to normalized name.
    if "yahoo_id" in df.columns and own["yahoo_id"].notna().any():
        by_id = own.dropna(subset=["yahoo_id"]).copy()
        by_id["yahoo_id"] = by_id["yahoo_id"].astype(str).str.replace(r"\.0$", "", regex=True)
        ids = df["yahoo_id"].astype(str).str.replace(r"\.0$", "", regex=True)
        m = ids.map(dict(zip(by_id["yahoo_id"], by_id["owner"])))
        df.loc[m.notna(), "owner"] = m[m.notna()]

    keys = df["full_name"].map(normalize_name)
    if "player_id" in df.columns:
        is_def = df["player_id"].astype(str).str.startswith("DEF_")
        keys = keys.where(~is_def, df["player_id"])
    m = keys.map(dict(zip(own["name_key"], own["owner"])))
    unmatched = df["owner"] == FREE_AGENT
    df.loc[unmatched & m.notna(), "owner"] = m[unmatched & m.notna()]
    return df
