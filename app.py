"""Fantasy Football HQ - Streamlit app. Run with start.bat or `streamlit run app.py`."""
from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from ff import backtest, config, lineup, ownership, projections, scoring, stats, store
from ff import yahoo

st.set_page_config(page_title="Fantasy Football HQ", page_icon="🏈", layout="wide")

# ---------- position-specific stat columns ----------
# label -> (source column, how to aggregate across weeks). Season totals unless noted.
POSITION_STATS: dict[str, list[tuple[str, str, str]]] = {
    "QB": [
        ("Cmp", "completions", "sum"), ("Att", "attempts", "sum"), ("PassYds", "passing_yards", "sum"),
        ("PassTD", "passing_tds", "sum"), ("INT", "passing_interceptions", "sum"), ("Sacked", "sacks_suffered", "sum"),
        ("Car", "carries", "sum"), ("RushYds", "rushing_yards", "sum"), ("RushTD", "rushing_tds", "sum"),
        ("FumL", "fumbles_lost_total", "sum"),
    ],
    "RB": [
        ("Car", "carries", "sum"), ("RushYds", "rushing_yards", "sum"), ("RushTD", "rushing_tds", "sum"),
        ("Tgt", "targets", "sum"), ("Rec", "receptions", "sum"), ("RecYds", "receiving_yards", "sum"),
        ("RecTD", "receiving_tds", "sum"), ("FumL", "fumbles_lost_total", "sum"),
    ],
    "WR": [
        ("Tgt", "targets", "sum"), ("Rec", "receptions", "sum"), ("RecYds", "receiving_yards", "sum"),
        ("RecTD", "receiving_tds", "sum"), ("Tgt%", "target_share", "mean"), ("AirYds", "receiving_air_yards", "sum"),
        ("YAC", "receiving_yards_after_catch", "sum"), ("Car", "carries", "sum"), ("RushYds", "rushing_yards", "sum"),
    ],
    "TE": [
        ("Tgt", "targets", "sum"), ("Rec", "receptions", "sum"), ("RecYds", "receiving_yards", "sum"),
        ("RecTD", "receiving_tds", "sum"), ("Tgt%", "target_share", "mean"), ("AirYds", "receiving_air_yards", "sum"),
        ("YAC", "receiving_yards_after_catch", "sum"),
    ],
    "K": [
        ("FGM", "fg_made", "sum"), ("FGA", "fg_att", "sum"), ("40-49", "fg_made_40_49", "sum"),
        ("50+", "fg_made_50_59", "sum"), ("60+", "fg_made_60_", "sum"), ("Long", "fg_long", "max"),
        ("XPM", "pat_made", "sum"), ("XPA", "pat_att", "sum"),
    ],
    "DEF": [
        ("Sck", "def_sacks", "sum"), ("Int", "def_interceptions", "sum"), ("FumRec", "fumble_recovery_opp", "sum"),
        ("DefTD", "def_tds", "sum"), ("RetTD", "def_return_tds", "sum"), ("Sfty", "def_safeties", "sum"),
        ("Blk", "def_blocks", "sum"), ("PA/G", "points_allowed", "mean"),
    ],
}
# Derived columns: label -> (position list, function of the aggregated frame)
DERIVED = {
    "YPC": (["RB"], lambda d: (d["RushYds"] / d["Car"].replace(0, float("nan"))).round(1)),
    "Y/R": (["WR", "TE"], lambda d: (d["RecYds"] / d["Rec"].replace(0, float("nan"))).round(1)),
    "FG%": (["K"], lambda d: (100 * d["FGM"] / d["FGA"].replace(0, float("nan"))).round(0)),
}
# "Opportunities" for the scatter plot: label -> columns summed
OPPORTUNITY = {
    "QB": ("Pass attempts + carries", ["Att", "Car"]),
    "RB": ("Carries + targets", ["Car", "Tgt"]),
    "WR": ("Targets", ["Tgt"]),
    "TE": ("Targets", ["Tgt"]),
    "K": ("FG + XP attempts", ["FGA", "XPA"]),
}
INJURY_ABBR = {"Out": "Out", "Doubtful": "D", "Questionable": "Q"}
PLAYED_BG = "#F0F0ED"


# ---------- data loading ----------
@st.cache_data(show_spinner=False)
def build_player_table(_cache_key: str, blend: float, shrink: float) -> tuple[pd.DataFrame, int, pd.DataFrame]:
    """Returns (player table, next week number, scored weekly rows through the last completed week)."""
    weekly = stats.weekly_stats()
    sched = stats.schedule()
    rost = stats.rosters()
    if weekly is None or sched is None or rost is None:
        return pd.DataFrame(), 0, pd.DataFrame()

    scored_all = scoring.apply(weekly)
    last_week = stats.latest_completed_week(sched)
    next_week = last_week + 1
    # Everything season-level uses completed weeks only, so players who already played
    # this week are compared on equal footing with those who haven't.
    scored = scored_all[scored_all["week"] <= last_week]

    g = scored.groupby("player_id")
    summary = pd.DataFrame({
        "player_id": g.size().index,
        "Player": g["player_display_name"].last().values,
        "Pos": g["position"].last().values,
        "NFL": g["team"].last().values,
        "G": g.size().values,
        "Total": g["points"].sum().round(1).values,
        "PPG": g["points"].mean().round(1).values,
        "Last": g["points"].last().round(1).values,
        "Trend": g["points"].apply(list).values,
    })

    for pos, specs in POSITION_STATS.items():
        for label, col, how in specs:
            if col not in scored.columns or label in summary.columns:
                continue
            agg = g[col].agg(how)
            if how == "mean":
                agg = (agg * 100).round(1) if col == "target_share" else agg.round(1)
            summary[label] = summary["player_id"].map(agg).values
    for label, (_, fn) in DERIVED.items():
        summary[label] = fn(summary)

    # per-week points, including the current week for anyone who already played
    pivot = scored_all.pivot_table(index="player_id", columns="week", values="points", aggfunc="sum")
    pivot.columns = [f"W{w}" for w in pivot.columns]
    summary = summary.merge(pivot.reset_index(), on="player_id", how="left")
    summary["Played"] = summary.get(f"W{next_week}")

    proj = projections.project(scored, sched, next_week, blend, shrink)[
        ["player_id", "recent_avg", "next_opp", "opp_factor", "projection"]
    ].rename(columns={"recent_avg": "Last3", "next_opp": "Opp", "opp_factor": "OppFac", "projection": "Proj"})
    summary = summary.merge(proj, on="player_id", how="left")

    summary = summary.merge(
        rost[["gsis_id", "full_name", "yahoo_id", "status"]].rename(columns={"gsis_id": "player_id"}),
        on="player_id", how="left",
    )
    summary["full_name"] = summary["full_name"].fillna(summary["Player"])

    # injuries: latest report status, plus roster status for IR / PUP / suspended
    inj = stats.injuries()
    summary["Inj"] = ""
    if inj is not None and not inj.empty:
        latest = inj[inj["week"] >= next_week - 1]  # only this week's report is relevant
        status = dict(zip(latest["gsis_id"], latest["report_status"].map(INJURY_ABBR)))
        summary["Inj"] = summary["player_id"].map(status).fillna("")
    on_reserve = summary["status"].isin(["RES", "PUP", "NON", "SUS"])
    summary.loc[on_reserve & (summary["Inj"] == ""), "Inj"] = summary.loc[on_reserve, "status"].map(
        {"RES": "IR", "PUP": "PUP", "NON": "NFI", "SUS": "Susp"}
    )
    # no projection for players who can't play
    summary.loc[summary["Inj"].isin(["Out", "IR", "PUP", "NFI", "Susp"]) & summary["Played"].isna(), "Proj"] = 0.0

    summary = ownership.attach(summary).rename(columns={"owner": "Owner"})
    return summary, next_week, scored


def do_refresh():
    with st.status("Refreshing...", expanded=True) as s:
        st.write("Downloading NFL stats from nflverse...")
        counts = stats.refresh()
        st.write(f"Stats: {counts['weekly_stats']} player-weeks, {counts['rosters']} players and defenses, "
                 f"{counts['injuries']} injury reports.")
        if config.yahoo_configured():
            st.write("Syncing Yahoo league...")
            try:
                result = yahoo.sync()
                st.write(f"Yahoo: {result['teams']} teams, {result['rostered']} rostered players, scoring updated.")
            except Exception as e:  # show the real error; don't hide it
                st.error(f"Yahoo sync failed: {e}")
        else:
            st.write("Yahoo not configured yet (no .env). Using manual rosters.csv for ownership.")
        s.update(label="Refresh complete", state="complete", expanded=False)
    st.cache_data.clear()


# ---------- charts ----------
def scatter(df: pd.DataFrame, pos: str) -> alt.Chart | None:
    if pos not in OPPORTUNITY:
        return None
    label, cols = OPPORTUNITY[pos]
    d = df.copy()
    d["Opportunities"] = d[cols].fillna(0).sum(axis=1)
    d = d[d["Opportunities"] > 0]
    d["Status"] = d["Owner"].where(d["Owner"] == ownership.FREE_AGENT, "Rostered")
    d = d.nlargest(80, "Opportunities")
    base = alt.Chart(d).encode(
        x=alt.X("Opportunities:Q", title=f"{label} (season)"),
        y=alt.Y("Total:Q", title="Total points (season)"),
        tooltip=["Player", "NFL", "Owner", "Total", "PPG", "Opportunities", "Proj"],
    )
    dots = base.mark_circle(size=80, opacity=0.85).encode(
        color=alt.Color(
            "Status:N",
            scale=alt.Scale(domain=["Rostered", ownership.FREE_AGENT], range=["#2E7D32", "#D9772B"]),
            legend=alt.Legend(title=None, orient="top"),
        ),
    )
    labels = base.transform_window(
        rank="rank(Total)", sort=[alt.SortField("Total", order="descending")]
    ).transform_filter(alt.datum.rank <= 10).mark_text(dy=-11, fontSize=11, color="#1C1C1C").encode(text="Player:N")
    trend = base.transform_regression("Opportunities", "Total").mark_line(color="#9A9A94", strokeDash=[4, 4])
    return (dots + labels + trend).properties(height=380)


def heatmap(scored: pd.DataFrame, positions: list[str], shrink: float) -> alt.Chart:
    fac = projections.opponent_factors(scored, shrink)
    fac = fac[fac["position"].isin(positions)]
    base = alt.Chart(fac).encode(
        x=alt.X("position:N", title=None, sort=positions, axis=alt.Axis(orient="top", labelAngle=0)),
        y=alt.Y("team:N", title=None),
    )
    rect = base.mark_rect().encode(
        color=alt.Color(
            "opp_factor:Q",
            scale=alt.Scale(domain=[0.7, 1.0, 1.3], range=["#B9CDE5", "#F7F7F4", "#F3D3B0"], clamp=True),
            legend=alt.Legend(title="OppFac", orient="right"),
        ),
        tooltip=[alt.Tooltip("team:N", title="Opponent"), "position:N", alt.Tooltip("opp_factor:Q", format=".2f")],
    )
    text = base.mark_text(fontSize=11).encode(text=alt.Text("opp_factor:Q", format=".2f"), color=alt.value("#1C1C1C"))
    return (rect + text).properties(height=24 * fac["team"].nunique())


# ---------- look ----------
st.markdown(
    """
    <style>
    .ffhq-banner {
        color: #F4F4EF; padding: 14px 22px; border-radius: 10px;
        border-top: 4px solid #F2C14E; border-bottom: 4px solid #F2C14E;
        background-image:
            repeating-linear-gradient(90deg, rgba(255,255,255,0.10) 0 2px, transparent 2px 10%),
            linear-gradient(90deg, #1B4D2B 0%, #2E7D32 60%, #1B4D2B 100%);
        margin-bottom: 10px;
    }
    .ffhq-banner h1 { margin: 0; font-size: 1.9rem; letter-spacing: 0.5px; }
    .ffhq-banner p { margin: 4px 0 0 0; opacity: 0.9; }
    .stTabs [data-baseweb="tab"] { font-weight: 600; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------- sidebar ----------
with st.sidebar:
    st.title("🏈 Fantasy Football HQ")
    if st.button("🔄 Refresh data", type="primary", width="stretch"):
        do_refresh()
    upd = store.updated("weekly_stats")
    st.caption(f"Stats updated: {upd or 'never'}")
    st.caption(f"Scoring: {config.scoring_source()}")
    st.caption(f"Ownership: {ownership.source()}")
    if not config.yahoo_configured():
        st.info("Yahoo API not connected. Copy .env.example to .env and fill it in once approved.", icon="🔑")

    st.divider()
    st.subheader("Filters")
    fa_only = st.toggle("Free agents only", value=False)
    sort_by = st.selectbox("Sort by", ["Rk (season total)", "ProjRk (projection)", "PPG", "Last3", "Last"], index=0)
    min_games = st.number_input("Min games played", min_value=1, max_value=18, value=1)
    positions = list(config.load_position_limits().keys())

    st.divider()
    with st.expander("League rosters (no Yahoo API)"):
        st.caption("Two ways to tell the app who owns whom. Either one updates data/rosters.csv.")
        st.markdown("**Paste from Yahoo.** Open a team's page in Yahoo, select all (Ctrl+A), copy, paste here.")
        paste_team = st.text_input("League team name", key="paste_team", placeholder="e.g. Gridiron Gang")
        paste_text = st.text_area("Pasted roster page", key="paste_text", height=120)
        if st.button("Find players in pasted text", width="stretch"):
            rost = stats.rosters()
            if not paste_team.strip():
                st.warning("Enter the league team name first.")
            elif rost is None:
                st.warning("Click Refresh data first so the app knows the player list.")
            else:
                st.session_state["paste_found"] = ownership.extract_players(paste_text, rost, stats.teams())
        if st.session_state.get("paste_found") is not None:
            found = st.session_state["paste_found"]
            st.write(f"Found {len(found)} players:")
            keep = st.multiselect("Uncheck any that don't belong", options=found, default=found, key="paste_keep")
            if st.button("Save this roster", type="primary", width="stretch"):
                ownership.set_team(paste_team.strip(), keep)
                st.session_state["paste_found"] = None
                st.cache_data.clear()
                st.success(f"Saved {len(keep)} players for {paste_team}.")
                st.rerun()

        st.markdown("**Or upload a spreadsheet** with two columns: `team`, `player`.")
        up = st.file_uploader("CSV or Excel", type=["csv", "xlsx"], label_visibility="collapsed")
        if up is not None and st.button("Import spreadsheet", width="stretch"):
            df = pd.read_csv(up) if up.name.endswith(".csv") else pd.read_excel(up)
            df.columns = [c.strip().lower() for c in df.columns]
            if {"team", "player"} <= set(df.columns):
                ownership.save_manual_rosters(df)
                st.cache_data.clear()
                st.success(f"Imported {len(df)} rows.")
                st.rerun()
            else:
                st.error("Needs columns named 'team' and 'player'.")

        current = ownership.manual_rosters()
        if not current.empty:
            st.caption("Currently entered:")
            st.dataframe(current.groupby("team").size().rename("players"), width="stretch")

# ---------- main ----------
# Projection knobs live in session state so the Accuracy tab's sliders drive every table.
KNOBS = {"knob_blend": projections.DEFAULTS["blend"], "knob_shrink": projections.DEFAULTS["shrink"]}
for k, v in KNOBS.items():
    st.session_state.setdefault(k, v)
blend = float(st.session_state["knob_blend"])
shrink = float(st.session_state["knob_shrink"])


def reset_knobs():
    for k, v in KNOBS.items():
        st.session_state[k] = v


table, next_week, scored = build_player_table(store.updated("weekly_stats") or "none", blend, shrink)

if table.empty:
    st.markdown('<div class="ffhq-banner"><h1>🏈 Fantasy Football HQ</h1><p>Kickoff time.</p></div>', unsafe_allow_html=True)
    st.write("No data yet. Click **Refresh data** in the sidebar to download this season's stats.")
    st.stop()

st.markdown(
    f'<div class="ffhq-banner"><h1>🏈 Top Players</h1>'
    f'<p>Season stats through week {next_week - 1}. Rk = rank by season total. '
    f'Proj = projected points for week {next_week} ({blend:.0%} last-3-game form + {1 - blend:.0%} season average, '
    f'times the matchup factor OppFac at {shrink:.0%} strength; tune these on the Accuracy tab). '
    f'ProjRk = rank by projection. Shaded rows have already played this week.</p></div>',
    unsafe_allow_html=True,
)

week_cols = sorted([c for c in table.columns if c.startswith("W") and c[1:].isdigit()], key=lambda c: int(c[1:]))
BASE_COLS = ["Rk", "ProjRk", "Player", "Inj", "NFL", "Owner", "G", "Total", "PPG", "Last3", "Opp", "OppFac",
             "Proj", "Trend"]

tabs = st.tabs(positions + ["All", "Teams", "Matchups", "Accuracy"])
for tab, pos in zip(tabs, positions + ["All"]):
    with tab:
        df = table if pos == "All" else table[table["Pos"] == pos]
        # Ranks are within the position (or overall on the All tab), before any filtering
        df = df.assign(
            Rk=df["Total"].rank(ascending=False, method="min").astype(int),
            ProjRk=df["Proj"].rank(ascending=False, method="min").astype(int),
        )
        if fa_only:
            df = df[df["Owner"] == ownership.FREE_AGENT]
        df = df[df["G"] >= min_games]
        sort_col = sort_by.split(" ")[0]
        df = df.sort_values(sort_col, ascending=sort_col.endswith("Rk"))

        cols = BASE_COLS[:3] + (["Pos"] if pos == "All" else []) + BASE_COLS[3:]
        if pos in POSITION_STATS:
            cols += [label for label, _, _ in POSITION_STATS[pos] if label in df.columns]
            cols += [label for label, (poss, _) in DERIVED.items() if pos in poss]
        show_weeks = st.toggle("Show weekly points", key=f"wk_{pos}", value=True)
        if show_weeks:
            cols += week_cols

        view = df[cols]
        played = df["Played"].notna()
        # Sparklines share one scale per tab so a top player's bars are tall and a bench player's are short
        y_max = float(df[week_cols].max().max()) if week_cols else None
        styled = view.style.apply(
            lambda row: [f"background-color: {PLAYED_BG}" if played[row.name] else ""] * len(row), axis=1
        ).format(precision=1, na_rep="")
        st.dataframe(
            styled,
            width="stretch",
            hide_index=True,
            height=min(45 + 35 * len(view), 700),
            column_config={
                "Rk": st.column_config.NumberColumn("Rk", help="Rank by total points this season", pinned=True, width="small"),
                "ProjRk": st.column_config.NumberColumn("ProjRk", help="Rank by this week's projection", pinned=True, width="small"),
                "Player": st.column_config.TextColumn(pinned=True),
                "Inj": st.column_config.TextColumn("Inj", help="Out / D = Doubtful / Q = Questionable / IR", width="small"),
                "Proj": st.column_config.NumberColumn(format="%.1f", help=f"Projected points, week {next_week}. Zero if Out or on IR."),
                "Trend": st.column_config.BarChartColumn("Trend", help="Points by week, same scale for everyone on this tab",
                                                         y_min=0, y_max=y_max, width="small"),
                "Tgt%": st.column_config.NumberColumn(format="%.1f%%"),
                "FG%": st.column_config.NumberColumn(format="%.0f%%"),
                "Owner": st.column_config.TextColumn(width="medium"),
            },
        )

        chart = scatter(df, pos)
        if chart is not None:
            st.markdown("**Opportunity vs production.** Dots below the dashed line get volume without points "
                        "(buy low or stay away); dots above it are efficient. Orange = free agent.")
            st.altair_chart(chart, width="stretch")

with tabs[-3]:
    strength, lineups = lineup.team_strength(table, ownership.FREE_AGENT)
    teams_list = ownership.league_teams()
    settings = config.load_settings()
    my_team = settings.get("my_team") if settings.get("my_team") in teams_list else None
    any_starters = not strength.empty and strength["Starting PPG"].notna().any()
    MY_COLOR, OTHER_COLOR = "#C9A227", "#2E7D32"

    if strength.empty:
        st.subheader("League teams")
        st.write("No league rosters entered yet. Use **League rosters** in the sidebar to paste or upload them, "
                 "then come back here.")
    else:
        # ----- header row: my team + ranking metric -----
        h1, h2 = st.columns([2, 3])
        with h1:
            pick_my = st.selectbox("My team", ["(not set)"] + teams_list,
                                   index=(teams_list.index(my_team) + 1) if my_team else 0)
            if pick_my != "(not set)" and pick_my != my_team:
                config.save_setting("my_team", pick_my)
                st.rerun()
        with h2:
            rank_by = "Best lineup PPG"
            if any_starters:
                rank_by = st.radio("Rank teams by", ["Starting PPG", "Best lineup PPG"], horizontal=True,
                                   help="Starting = the lineups entered below. Teams without one use their best lineup.")

        # ----- league table: five columns that tell the story -----
        league = strength.copy()
        league["value"] = league[rank_by].fillna(league["Best lineup PPG"])
        league["This week Proj"] = league["Starting Proj"].fillna(league["Best week Proj"])
        league = league.sort_values("value", ascending=False).reset_index(drop=True)
        league["Rk"] = range(1, len(league) + 1)
        if my_team:
            mine = league.loc[league["Team"] == my_team, "value"].iloc[0]
            league["vs you"] = (league["value"] - mine).round(1)
        else:
            league["vs you"] = (league["value"] - league["value"].median()).round(1)
        league["Mine"] = league["Team"] == my_team

        bar = alt.Chart(league).mark_bar(cornerRadiusEnd=4).encode(
            x=alt.X("value:Q", title=f"{rank_by}, points per game"),
            y=alt.Y("Team:N", sort="-x", title=None),
            color=alt.condition(alt.datum.Mine, alt.value(MY_COLOR), alt.value(OTHER_COLOR)),
            tooltip=["Team", "Starting PPG", "Best lineup PPG", "Left on bench", "This week Proj"],
        ).properties(height=26 * len(league))
        st.altair_chart(bar, width="stretch")
        if my_team:
            st.caption(f"Your team ({my_team}) is gold. 'vs you' is each team's {rank_by} minus yours.")
        else:
            st.caption("Set **My team** above to highlight your team and compare everyone against you.")

        show = league[["Rk", "Team", "Starting PPG", "Best lineup PPG", "Left on bench", "This week Proj", "vs you"]]
        st.dataframe(
            show.style.format(precision=1, na_rep="").apply(
                lambda r: [f"background-color: {MY_COLOR}33" if league.loc[r.name, "Mine"] else ""] * len(r), axis=1),
            width="stretch", hide_index=True,
            column_config={
                "Rk": st.column_config.NumberColumn(width="small"),
                "Starting PPG": st.column_config.NumberColumn(help="PPG of the starters entered for that team"),
                "Best lineup PPG": st.column_config.NumberColumn(help="Strongest lineup their roster could field"),
                "Left on bench": st.column_config.NumberColumn(help="Best lineup minus starting lineup"),
                "This week Proj": st.column_config.NumberColumn(
                    help=f"Week {next_week} projection: starters if entered, otherwise best lineup"),
            },
        )

        # ----- slot heatmap: where each team is strong or thin -----
        slot_cols = [s for s, _ in lineup.load_slots()[0] if s in strength.columns]
        long = strength.melt(id_vars=["Team"], value_vars=slot_cols, var_name="Slot", value_name="PPG")
        long["vs avg"] = long["PPG"] - long.groupby("Slot")["PPG"].transform("mean")
        spread = max(abs(long["vs avg"].min()), abs(long["vs avg"].max()), 1.0)
        team_order = league["Team"].tolist()
        base = alt.Chart(long).encode(
            x=alt.X("Slot:N", title=None, sort=slot_cols, axis=alt.Axis(orient="top", labelAngle=0)),
            y=alt.Y("Team:N", title=None, sort=team_order),
        )
        cells = base.mark_rect().encode(
            color=alt.Color("vs avg:Q", scale=alt.Scale(domain=[-spread, 0, spread],
                                                        range=["#B9CDE5", "#F7F7F4", "#F3D3B0"]),
                            legend=alt.Legend(title="vs league avg")),
            tooltip=["Team", "Slot", alt.Tooltip("PPG:Q", format=".1f"), alt.Tooltip("vs avg:Q", format="+.1f")],
        )
        text = base.mark_text(fontSize=11).encode(text=alt.Text("PPG:Q", format=".1f"), color=alt.value("#1C1C1C"))
        st.markdown("**Strength by slot** (best lineup, PPG). Orange = above league average, blue = below. "
                    "A blue cell on a rival is a trade target; a blue cell on you is a need.")
        st.altair_chart((cells + text).properties(height=26 * len(team_order)), width="stretch")

        missing = ownership.unmatched(table)
        if not missing.empty:
            st.warning(f"{len(missing)} roster entries didn't match a player with stats this season. "
                       "Check spelling, or they may not have played yet.")
            st.dataframe(missing, width="stretch", hide_index=True)

    # ===== team detail: one picker for lineup entry, best lineup, and roster edits =====
    if teams_list:
        st.divider()
        st.subheader("Team detail")
        default_idx = teams_list.index(my_team) if my_team else 0
        team = st.selectbox("Team", teams_list, index=default_idx, key="detail_team")
        roster = table[table["Owner"] == team][["full_name", "Player", "Pos", "NFL", "Inj", "PPG", "Proj"]]
        roster = roster.sort_values(["Pos", "PPG"], ascending=[True, False]).reset_index(drop=True)
        best = lineup.best_lineup(roster, "PPG")
        need = lineup.starter_count()

        # A prefill (from "Use best lineup") stays until saved, so the ticks survive reruns while editing.
        prefill = st.session_state.get("starters_prefill")
        if prefill is not None and prefill[0] == team:
            current = set(prefill[1])
        else:
            current = set(lineup.load_starters().get(team, []))
        rev = st.session_state.get("starters_rev", 0)

        left, right = st.columns([3, 2])
        with left:
            st.markdown(f"**Starting lineup** — tick the {need} starters, then Save.")
            roster_view = roster.copy()
            roster_view.insert(0, "Start", roster_view["full_name"].isin(current))
            edited = st.data_editor(
                roster_view.drop(columns=["full_name"]),
                key=f"starters_editor_{team}_{rev}",
                width="stretch", hide_index=True,
                disabled=["Player", "Pos", "NFL", "Inj", "PPG", "Proj"],
                column_config={
                    "Start": st.column_config.CheckboxColumn("Start", default=False, width="small"),
                    "PPG": st.column_config.NumberColumn(format="%.1f"),
                    "Proj": st.column_config.NumberColumn(format="%.1f"),
                },
            )
            chosen = roster[edited["Start"].values]
            problem = lineup.check_starters(chosen)
            b1, b2 = st.columns(2)
            if b1.button("Use best lineup", width="stretch",
                         help="Tick the best possible lineup; you can adjust before saving"):
                st.session_state["starters_prefill"] = (team, best["full_name"].tolist())
                st.session_state["starters_rev"] = rev + 1
                st.rerun()
            if b2.button("Save starting lineup", type="primary", width="stretch", disabled=problem is not None):
                lineup.save_starters(team, chosen["full_name"].tolist())
                st.session_state.pop("starters_prefill", None)
                st.session_state["starters_rev"] = rev + 1
                st.cache_data.clear()
                st.rerun()
            if problem:
                st.warning(problem)
            else:
                saved = set(lineup.load_starters().get(team, []))
                state = "saved" if saved == set(chosen["full_name"]) else "not saved yet"
                st.success(f"{len(chosen)} starters, {chosen['PPG'].sum():.1f} PPG ({state}).")
        with right:
            st.markdown("**Best possible lineup**")
            st.dataframe(best[["Slot", "Player", "PPG", "Proj"]].style.format(precision=1),
                         width="stretch", hide_index=True)
            if not strength.empty:
                row = strength[strength["Team"] == team].iloc[0]
                m1, m2 = st.columns(2)
                m1.metric("Best lineup PPG", f"{row['Best lineup PPG']:.1f}")
                if pd.notna(row["Starting PPG"]):
                    m2.metric("Left on bench", f"{row['Left on bench']:.1f}")

        with st.expander("Edit this roster (move players between teams, add free agents)"):
            st.caption("Change the Owner dropdown, then Save. Search to find a free agent to add.")
            new_team = st.text_input("Add a new league team name", key="new_team_name")
            opts = sorted(set(teams_list + ([new_team.strip()] if new_team.strip() else []))) + [ownership.FREE_AGENT]
            q = st.text_input("Search all players", key="roster_search", placeholder="name, NFL team, or position")
            pool = table[["full_name", "Player", "Pos", "NFL", "Owner", "PPG"]]
            if q.strip():
                ql = q.strip().lower()
                hits = pool[pool["Player"].str.lower().str.contains(ql) | pool["NFL"].str.lower().eq(ql)
                            | pool["Pos"].str.lower().eq(ql)].nlargest(40, "PPG")
                pool = pd.concat([pool[pool["Owner"] == team], hits]).drop_duplicates("full_name")
            else:
                pool = pool[pool["Owner"] == team]
            pool = pool.sort_values(["Owner", "PPG"], ascending=[True, False]).reset_index(drop=True)
            rrev = st.session_state.get("roster_rev", 0)
            edited_r = st.data_editor(
                pool.drop(columns=["full_name"]),
                key=f"roster_editor_{team}_{rrev}",
                width="stretch", hide_index=True, height=min(45 + 35 * len(pool), 500),
                disabled=["Player", "Pos", "NFL", "PPG"],
                column_config={
                    "Owner": st.column_config.SelectboxColumn("Owner", options=opts, required=True),
                    "PPG": st.column_config.NumberColumn(format="%.1f"),
                },
            )
            changed = pool[edited_r["Owner"].values != pool["Owner"].values]
            if st.button(f"Save roster changes ({len(changed)})", type="primary", disabled=len(changed) == 0):
                for i in changed.index:
                    ownership.set_player(pool.loc[i, "full_name"], edited_r.loc[i, "Owner"])
                st.session_state["roster_rev"] = rrev + 1
                st.cache_data.clear()
                st.rerun()

with tabs[-2]:
    st.subheader(f"Week {next_week} matchups")
    st.caption("Each cell is the matchup factor (OppFac) for that opponent and position. Orange = gives up more than "
               "average (soft matchup). Blue = tough. 1.00 = average. For DEF, the opponent is the offense they face.")
    st.altair_chart(heatmap(scored, positions, shrink), width="stretch")

with tabs[-1]:
    st.subheader("How good are the projections?")

    st.markdown("**Tune the model.** These sliders change every projection in the app. Watch the numbers below "
                "to see whether your settings beat the defaults.")
    k1, k2, k3 = st.columns([3, 3, 1])
    k1.slider("Weight on recent form (last 3 games) vs season average", 0.0, 1.0, step=0.05,
              key="knob_blend", format="%.2f",
              help="0 = season average only. 1 = last-3-game form only.")
    k2.slider("Trust in the matchup factor", 0.0, 1.0, step=0.05, key="knob_shrink", format="%.2f",
              help="0 = ignore the opponent. 1 = use the opponent's full points-allowed effect.")
    k3.write("")
    k3.button("Reset to defaults", on_click=reset_knobs, width="stretch")

    results = backtest.run(scored, stats.schedule(), next_week - 1, blend=blend, shrink=shrink)
    if results.empty:
        st.write("Not enough completed weeks yet. Check back after week 2.")
    else:
        summary = backtest.summarize(results)
        overall = summary.loc["ALL"]
        weeks = f"weeks {results['week'].min()} to {results['week'].max()}"
        n = int(overall["Player-weeks"])
        st.markdown(
            f"For {weeks}, we re-ran the projection using only the data available before each week, "
            f"for {n} player-games among the top players at each position. The question is simple: "
            f"**on average, how many points was the projection off by?**"
        )
        is_default = (blend, shrink) == (projections.DEFAULTS["blend"], projections.DEFAULTS["shrink"])
        default_mae = None
        if not is_default:
            default_results = backtest.run(scored, stats.schedule(), next_week - 1)
            default_mae = backtest.summarize(default_results).loc["ALL", "Model MAE"]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Your settings" if not is_default else "Our model (defaults)", f"{overall['Model MAE']:.1f} pts off",
                  delta=None if default_mae is None else f"{overall['Model MAE'] - default_mae:+.2f} vs defaults",
                  delta_color="inverse")
        c2.metric("Default settings", f"{(default_mae if default_mae is not None else overall['Model MAE']):.1f} pts off")
        c3.metric("Just use season average", f"{overall['Season avg MAE']:.1f} pts off")
        c4.metric("Just use last game", f"{overall['Last game MAE']:.1f} pts off")
        better = overall["Model MAE"] < min(overall["Season avg MAE"], overall["Last game MAE"])
        if better:
            st.success("The model is beating both simple guesses. Lower is better.")
        else:
            st.warning("Right now a simple season average is as good or better. With more weeks of data, "
                       "the model's recent-form and matchup pieces have more to work with. Lower is better.")
        with st.expander("By position"):
            by_pos = summary[["Player-weeks", "Model MAE", "Season avg MAE", "Last game MAE"]].rename(
                columns={"Model MAE": "Model", "Season avg MAE": "Season avg", "Last game MAE": "Last game"}
            )
            st.caption("Average points off, by method. Lower is better.")
            st.dataframe(by_pos.round(1), width="stretch")
