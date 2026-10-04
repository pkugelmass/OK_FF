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
    st.subheader("League teams")
    strength, lineups = lineup.team_strength(table, ownership.FREE_AGENT)
    if strength.empty:
        st.write("No league rosters entered yet. Use **League rosters** in the sidebar to add them.")
    else:
        slots_text = ", ".join(f"{n} {s}" for s, n in lineup.load_slots()[0])
        st.caption(
            f"Each team's best possible starting lineup ({slots_text}), picked by season points per game. "
            f"**Lineup PPG** is the sum of those starters' per-game averages: the team's general strength. "
            f"**This week Proj** re-picks the lineup by this week's projections (injured players score zero). "
            f"Slot columns show the PPG each slot contributes, so you can see where a team is thin."
        )
        bar = alt.Chart(strength).mark_bar(color="#2E7D32", cornerRadiusEnd=4).encode(
            x=alt.X("Lineup PPG:Q", title="Best lineup, points per game"),
            y=alt.Y("Team:N", sort="-x", title=None),
            tooltip=["Team", "Lineup PPG", "This week Proj", "vs median"],
        ).properties(height=26 * len(strength))
        rule = alt.Chart(pd.DataFrame({"m": [strength["Lineup PPG"].median()]})).mark_rule(
            color="#9A9A94", strokeDash=[4, 4]).encode(x="m:Q")
        st.altair_chart(bar + rule, width="stretch")
        st.dataframe(
            strength.style.format(precision=1),
            width="stretch", hide_index=True,
            column_config={
                "Rk": st.column_config.NumberColumn(width="small"),
                "vs median": st.column_config.NumberColumn(help="Lineup PPG minus the league median"),
                "This week Proj": st.column_config.NumberColumn(help=f"Best lineup by week {next_week} projections"),
                "Bench PPG": st.column_config.NumberColumn(help="Combined PPG of everyone not in the lineup"),
            },
        )
        pick = st.selectbox("Show a team's lineup", options=list(strength["Team"]))
        st.dataframe(lineups[pick].style.format(precision=1), width="stretch", hide_index=True)

        missing = ownership.unmatched(table)
        if not missing.empty:
            st.warning(f"{len(missing)} roster entries didn't match a player with stats this season. "
                       "Check spelling, or they may not have played yet.")
            st.dataframe(missing, width="stretch", hide_index=True)

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
