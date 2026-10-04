# OK_FF — Fantasy Football HQ

A local app for ranking fantasy football players, seeing who owns them in our Yahoo league,
and making our own weekly projections.

## Run it

Double-click the **Fantasy Football HQ** shortcut on your Desktop. It checks GitHub for a newer
version, then opens the app in your browser. Click **Refresh data** in the sidebar to pull this
season's stats. Takes about 10 seconds. Close the black window to stop the app.

(The shortcut runs `update.bat`, which runs `start.bat`. Either can also be double-clicked directly.)

## Setting up on a new laptop (one time)

1. Install Python from https://www.python.org/downloads/ . On the first screen of the installer,
   tick **"Add python.exe to PATH"** before clicking Install.
2. Install Git from https://git-scm.com/download/win (defaults are fine), or GitHub Desktop.
3. Get the project: open https://github.com/pkugelmass/OK_FF , click the green **Code** button,
   copy the URL. In the folder where you want it (e.g. Documents), right-click, **Open in Terminal**,
   and run `git clone <that URL>`. (GitHub Desktop: File > Clone repository.)
4. Open the new `OK_FF` folder and double-click `start.bat`. The first run installs packages
   (a minute or two), puts a **Fantasy Football HQ** shortcut on the Desktop, and opens the app.

After that, just use the Desktop shortcut.

## What it does

- Downloads every player's weekly stats from [nflverse](https://github.com/nflverse) (free, no account).
- Scores them with the league's scoring rules (`data/scoring.json` once Yahoo is synced, otherwise the
  half-PPR default in `config/scoring.default.json`).
- Shows the top N at each position (change N in the sidebar), with total, per-game, last-3-game form,
  next opponent, a projection, and position-specific stats (passing yards for QBs, targets for WRs,
  field goal distances for kickers, sacks and points allowed for defenses, and so on).
- Includes team defense / special teams (DEF) built from team-level stats plus points allowed from the
  final scores. Scored with Yahoo's default tiers until the league's own rules are synced.
- Marks who owns each player. Until Yahoo is connected, it reads `data/rosters.csv`. Fill it from the
  sidebar: paste a copied Yahoo team page and the app finds the player names, or upload a spreadsheet
  with `team,player` columns. A defense can be written as `SF`, `SF D/ST`, or `San Francisco 49ers`.
  Anyone not listed shows as a Free Agent. Rows whose team starts with "Example" are ignored.
- Shows injury status (Out / Doubtful / Questionable / IR) and projects zero for anyone who can't play.
- Shades rows for players who have already played this week.
- **Matchups** tab: heatmap of how each opponent affects each position.
- **Accuracy** tab: re-runs the projections for past weeks and reports how far off they were, next to two
  naive baselines. Sliders there tune the model live; "Reset to defaults" puts it back.

## Connecting Yahoo (once approved)

1. Create the app at https://developer.yahoo.com/apps/create/ (Installed Application, redirect
   `https://localhost:8080`, Fantasy Sports read permission).
2. Copy `.env.example` to `.env` and paste in the Client ID (consumer key), Client Secret, and league ID.
3. Click Refresh. A browser tab asks you to sign in to Yahoo and approve. Paste the code back if asked.
   After that, the token is saved and syncs are automatic.

Once connected, the app reads scoring rules and every team's roster straight from the league.

## Projection model

`ff/projections.py`. Next-week projection = 60% weighted last-3-games + 40% season average,
multiplied by an opponent factor (how many points that defense gives up to the position, shrunk
halfway toward average). The defaults are constants at the top of the file; the Accuracy tab's sliders
override them for the session. `ff/backtest.py` measures accuracy. Ideas to try: snap share,
target share, last season as a prior, Vegas lines, home/away.

## Look

Colors live in `.streamlit/config.toml` (field green sidebar, gold accents). Tables stay plain for readability.

## Layout

```
app.py              Streamlit UI
start.bat           launcher; first run sets up Python env + Desktop shortcut
update.bat          git pull, then launch (what the Desktop shortcut runs)
assets/             shortcut icon
ff/stats.py         nflverse download -> SQLite
ff/backtest.py      how accurate were past projections
ff/scoring.py       stats -> fantasy points
ff/ownership.py     who owns whom (Yahoo table or rosters.csv)
ff/projections.py   the model
ff/yahoo.py         Yahoo sync (dormant without .env)
config/             scoring, position counts, manual rosters
.streamlit/         theme colors
data/               SQLite database (generated)
```
