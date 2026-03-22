"""
FastAPI web application for the Sports Betting Model.
"""
from __future__ import annotations
import sys, os
# Ensure the model root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Load .env for local development (no-op on Render where env vars are set natively)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from typing import Optional
import traceback
import json
from datetime import date
from pathlib import Path

from models.football_model import FootballModel
from models.nba_model      import NBAModel
from models.nfl_model      import NFLModel
from markets.value_engine  import filter_value_bets, rank_signals
from markets.report        import save_markdown_report
from utils.stats           import correct_score_grid

BASE_DIR   = Path(__file__).parent
TEMPLATES  = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="Sports Betting Model", version="1.0")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    tb = traceback.format_exc()
    return HTMLResponse(
        content=f"<pre style='color:red;background:#111;padding:20px;'>"
                f"ERROR on {request.url.path}\n\n{tb}</pre>",
        status_code=500,
    )


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


# ── Shared helpers ────────────────────────────────────────────────────────────

def signals_to_rows(signals) -> list[dict]:
    return [s.to_dict() for s in rank_signals(signals)]


def parse_odds_json(raw: str) -> dict:
    try:
        return json.loads(raw) if raw and raw.strip() else {}
    except Exception:
        return {}


def _ml_to_prob(ml) -> float:
    """American-odds string/number → implied win probability."""
    try:
        v = float(str(ml).replace("+", "").replace("EVEN", "100").strip())
        return 100 / (v + 100) if v > 0 else abs(v) / (abs(v) + 100)
    except (TypeError, ValueError):
        return 0.5


# ── Game-data builders (reused by today + sport pages) ───────────────────────

def _build_nba_games(today_str: str) -> list:
    from data.nba_data  import get_games as nba_get_games
    from data.odds_api  import build_odds_lookup, find_game_odds, decimal_to_american
    from utils.stats    import confidence_label, shot_attempt_over_under, threept_made_ou

    games = []
    try:
        nba_book_lookup = build_odds_lookup("NBA")

        for g in nba_get_games(dates=today_str):
            home_team = g.get("home_team") or "TBD"
            away_team = g.get("away_team") or "TBD"
            home_id   = str(g.get("home_id", ""))
            away_id   = str(g.get("away_id", ""))

            h_prob = _ml_to_prob(g.get("home_ml")) if g.get("home_ml") else 0.55
            a_prob = 1 - h_prob
            predicted_winner = home_team if h_prob >= a_prob else away_team
            win_prob = h_prob if h_prob >= a_prob else a_prob

            book = find_game_odds(nba_book_lookup, home_team, away_team) or {}

            def _nba_roster(team_id: str) -> list:
                player_map: dict = {}
                for ldr in g.get("leaders", []):
                    ldr_tid = ldr.get("team_id", "")
                    # Include if team_id matches or is empty (fallback — ESPN $ref)
                    if ldr_tid and ldr_tid != team_id:
                        continue
                    name = ldr.get("name", "")
                    if not name:
                        continue
                    stat = ldr["stat"]
                    val  = float(ldr["value"])
                    if name not in player_map:
                        player_map[name] = {"name": name, "pos": "",
                                            "pts": 0.0, "reb": 0.0,
                                            "ast": 0.0, "fg3m": 0.0}
                    sk = {"points": "pts", "rebounds": "reb",
                          "assists": "ast",
                          "threePointFieldGoalsMade": "fg3m"}.get(stat)
                    if sk:
                        player_map[name][sk] = val

                out = []
                for p in player_map.values():
                    props = []
                    for stat, avg, std in [
                        ("PTS", p["pts"],  0.28),
                        ("3PM", p["fg3m"], None),
                        ("REB", p["reb"],  0.32),
                        ("AST", p["ast"],  0.35),
                    ]:
                        if avg < 0.5:
                            continue
                        line = max(0.5, round(avg * 2) / 2 - 0.5)
                        if std is None:
                            over_p, under_p = threept_made_ou(avg, line)
                        else:
                            over_p, under_p = shot_attempt_over_under(avg, line, std_factor=std)
                        pick = "OVER" if over_p > 0.55 else ("UNDER" if over_p < 0.45 else "FAIR")
                        props.append({
                            "stat": stat, "avg": avg, "line": line,
                            "over_prob":  round(over_p  * 100, 1),
                            "under_prob": round(under_p * 100, 1),
                            "pick": pick,
                            "confidence": confidence_label(max(over_p, under_p)),
                        })
                    if props:
                        out.append({**p, "props": props})
                return out

            home_roster = _nba_roster(home_id)
            away_roster = _nba_roster(away_id)

            games.append({
                "sport": "Basketball", "league": "NBA", "sport_icon": "🏀",
                "home_team": home_team, "away_team": away_team,
                "home_abbr": g.get("home_abbr", ""),
                "away_abbr": g.get("away_abbr", ""),
                "kickoff": g.get("date", ""), "status": g.get("status", ""),
                "home_score": g.get("home_score"), "away_score": g.get("away_score"),
                "spread": g.get("spread"), "over_under": g.get("over_under"),
                "home_prob": round(h_prob * 100, 1),
                "away_prob": round(a_prob * 100, 1),
                "draw_prob": None,
                "predicted_winner": predicted_winner,
                "win_prob":   round(win_prob * 100, 1),
                "confidence": confidence_label(win_prob),
                "bookmaker":        book.get("bookmaker", ""),
                "book_home_ml":     decimal_to_american(book["home_ml"]) if book.get("home_ml") else None,
                "book_away_ml":     decimal_to_american(book["away_ml"]) if book.get("away_ml") else None,
                "book_home_spread": book.get("home_spread"),
                "book_total":       book.get("total_line"),
                "home_roster": home_roster,
                "away_roster": away_roster,
            })
    except Exception:
        traceback.print_exc()

    return games


def _build_nfl_games() -> list:
    from data.nfl_data  import get_games as nfl_get_games
    from data.odds_api  import build_odds_lookup, find_game_odds, decimal_to_american
    from utils.stats    import confidence_label, shot_attempt_over_under

    _NFL_GAMES = 17
    games = []
    try:
        nfl_book_lookup = build_odds_lookup("NFL")

        for g in nfl_get_games():
            home_team = g.get("home_team") or "TBD"
            away_team = g.get("away_team") or "TBD"
            home_id   = str(g.get("home_id", ""))
            away_id   = str(g.get("away_id", ""))

            h_prob = _ml_to_prob(g.get("home_ml")) if g.get("home_ml") else 0.55
            a_prob = 1 - h_prob
            predicted_winner = home_team if h_prob >= a_prob else away_team
            win_prob = h_prob if h_prob >= a_prob else a_prob

            book = find_game_odds(nfl_book_lookup, home_team, away_team) or {}

            _stat_map = {
                "passingYards":        ("Pass Yds",  0.40),
                "rushingYards":        ("Rush Yds",  0.55),
                "receivingYards":      ("Rec Yds",   0.60),
                "passingTouchdowns":   ("Pass TDs",  0.70),
                "rushingTouchdowns":   ("Rush TDs",  0.80),
                "receivingTouchdowns": ("Rec TDs",   0.80),
                "receptions":         ("Receptions", 0.40),
            }

            def _nfl_roster(team_id: str) -> list:
                player_map: dict = {}
                for ldr in g.get("leaders", []):
                    ldr_tid = ldr.get("team_id", "")
                    if ldr_tid and ldr_tid != team_id:
                        continue
                    name = ldr.get("name", "")
                    pos  = ldr.get("position", "")
                    if not name:
                        continue
                    stat = ldr["stat"]
                    val  = float(ldr.get("value", 0))
                    if name not in player_map:
                        player_map[name] = {"name": name, "pos": pos, "season_stats": {}}
                    player_map[name]["season_stats"][stat] = val
                    player_map[name]["pos"] = player_map[name]["pos"] or pos

                out = []
                for p in player_map.values():
                    props = []
                    for stat_key, (label, std) in _stat_map.items():
                        season_total = p["season_stats"].get(stat_key, 0)
                        if season_total < 10:
                            continue
                        avg  = season_total / _NFL_GAMES
                        line = max(0.5, round(avg * 2) / 2 - 0.5)
                        over_p, under_p = shot_attempt_over_under(avg, line, std_factor=std)
                        pick = "OVER" if over_p > 0.55 else ("UNDER" if over_p < 0.45 else "FAIR")
                        props.append({
                            "stat": label, "avg": round(avg, 1), "line": line,
                            "over_prob":  round(over_p  * 100, 1),
                            "under_prob": round(under_p * 100, 1),
                            "pick": pick,
                            "confidence": confidence_label(max(over_p, under_p)),
                        })
                    if props:
                        out.append({"name": p["name"], "pos": p["pos"], "props": props})
                return out

            home_roster = _nfl_roster(home_id)
            away_roster = _nfl_roster(away_id)
            if not home_roster and not away_roster:
                continue

            games.append({
                "sport": "Football", "league": "NFL", "sport_icon": "🏈",
                "home_team": home_team, "away_team": away_team,
                "home_abbr": g.get("home_abbr", ""),
                "away_abbr": g.get("away_abbr", ""),
                "kickoff": g.get("date", ""), "status": g.get("status", ""),
                "home_score": g.get("home_score"), "away_score": g.get("away_score"),
                "spread": g.get("spread"), "over_under": g.get("over_under"),
                "home_prob": round(h_prob * 100, 1),
                "away_prob": round(a_prob * 100, 1),
                "draw_prob": None,
                "predicted_winner": predicted_winner,
                "win_prob":   round(win_prob * 100, 1),
                "confidence": confidence_label(win_prob),
                "bookmaker":        book.get("bookmaker", ""),
                "book_home_ml":     decimal_to_american(book["home_ml"]) if book.get("home_ml") else None,
                "book_away_ml":     decimal_to_american(book["away_ml"]) if book.get("away_ml") else None,
                "book_home_spread": book.get("home_spread"),
                "book_total":       book.get("total_line"),
                "home_roster": home_roster,
                "away_roster": away_roster,
            })
    except Exception:
        traceback.print_exc()

    return games


def _build_soccer_games(today_str: str) -> list:
    from data.football_data import get_fixtures, get_team_players as get_soccer_players
    from data.odds_api      import build_odds_lookup, find_game_odds, decimal_to_american
    from utils.stats        import poisson_match_probs, confidence_label

    soccer_leagues = [
        ("EPL",  "Premier League"),
        ("UCL",  "Champions League"),
        ("LIGA", "La Liga"),
        ("L1",   "Ligue 1"),
    ]
    games = []
    for league_key, league_name in soccer_leagues:
        try:
            book_lookup = build_odds_lookup(league_key)
            fixtures    = get_fixtures(league_key, dates=today_str)
            model       = FootballModel(league_key)

            for fix in fixtures:
                home_team = fix.get("home_team") or "TBD"
                away_team = fix.get("away_team") or "TBD"
                home_xg, away_xg = model._match_xg(home_team, away_team)
                h_prob, d_prob, a_prob = poisson_match_probs(home_xg, away_xg)
                if h_prob >= d_prob and h_prob >= a_prob:
                    predicted_winner, win_prob = home_team, h_prob
                elif d_prob >= a_prob:
                    predicted_winner, win_prob = "Draw", d_prob
                else:
                    predicted_winner, win_prob = away_team, a_prob

                book = find_game_odds(book_lookup, home_team, away_team) or {}

                def _soccer_roster(team_name: str, team_xg: float) -> list:
                    team_pl = get_soccer_players(league_key, team_name, season=2024)
                    sigs = model.player_anytime_scorer_signals(
                        f"{home_team} vs {away_team}", team_name, team_xg,
                        players_override=team_pl if team_pl else None,
                    )
                    out = []
                    for s in sigs:
                        if "Lead Striker (estimate)" in s.selection:
                            continue
                        notes = s.notes or ""
                        shots = notes.split("Shots/game:")[1].strip().split()[0] if "Shots/game:" in notes else ""
                        xg_sh = notes.split("xG/shot:")[1].strip().split()[0] if "xG/shot:" in notes else ""
                        out.append({
                            "name":       s.selection,
                            "shots_pg":   shots,
                            "xg_shot":    xg_sh,
                            "goal_prob":  round(s.model_prob * 100, 1),
                            "confidence": s.confidence,
                        })
                    if not out and not team_pl:
                        for s in sigs:
                            out.append({
                                "name": s.selection, "shots_pg": "—", "xg_shot": "—",
                                "goal_prob": round(s.model_prob * 100, 1), "confidence": s.confidence,
                            })
                    return out

                games.append({
                    "sport": "Soccer", "league": league_name, "sport_icon": "⚽",
                    "home_team": home_team, "away_team": away_team,
                    "kickoff": fix.get("date", ""), "status": fix.get("status", ""),
                    "home_score": fix.get("home_score"), "away_score": fix.get("away_score"),
                    "spread": None, "over_under": None,
                    "home_prob": round(h_prob * 100, 1),
                    "away_prob": round(a_prob * 100, 1),
                    "draw_prob": round(d_prob * 100, 1),
                    "home_xg":   round(home_xg, 2),
                    "away_xg":   round(away_xg, 2),
                    "predicted_winner": predicted_winner,
                    "win_prob":   round(win_prob * 100, 1),
                    "confidence": confidence_label(win_prob),
                    "bookmaker":    book.get("bookmaker", ""),
                    "book_home_ml": decimal_to_american(book["home_ml"]) if book.get("home_ml") else None,
                    "book_away_ml": decimal_to_american(book["away_ml"]) if book.get("away_ml") else None,
                    "book_draw_ml": decimal_to_american(book.get("draw_ml")) if book.get("draw_ml") else None,
                    "book_total":   book.get("total_line"),
                    "home_roster":  _soccer_roster(home_team, home_xg),
                    "away_roster":  _soccer_roster(away_team, away_xg),
                })
        except Exception:
            traceback.print_exc()

    return games


# ── pages ─────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return TEMPLATES.TemplateResponse("index.html", {"request": request})


# ─────────────────────────────────────────────
#  SOCCER — live fixtures dashboard
# ─────────────────────────────────────────────

@app.get("/soccer", response_class=HTMLResponse)
async def soccer_page(request: Request):
    today_str   = date.today().strftime("%Y%m%d")
    today_label = date.today().strftime("%A, %B %d %Y")
    games       = _build_soccer_games(today_str)
    return TEMPLATES.TemplateResponse("soccer.html", {
        "request": request, "games": games,
        "today": today_label, "total": len(games),
        "has_odds_key": bool(os.getenv("ODDS_API_KEY")),
    })


# ─────────────────────────────────────────────
#  NBA — live games dashboard
# ─────────────────────────────────────────────

@app.get("/nba", response_class=HTMLResponse)
async def nba_page(request: Request):
    today_str   = date.today().strftime("%Y%m%d")
    today_label = date.today().strftime("%A, %B %d %Y")
    games       = _build_nba_games(today_str)
    return TEMPLATES.TemplateResponse("nba.html", {
        "request": request, "games": games,
        "today": today_label, "total": len(games),
        "has_odds_key": bool(os.getenv("ODDS_API_KEY")),
    })


# ─────────────────────────────────────────────
#  NFL — live games dashboard
# ─────────────────────────────────────────────

@app.get("/nfl", response_class=HTMLResponse)
async def nfl_page(request: Request):
    today_label = date.today().strftime("%A, %B %d %Y")
    games       = _build_nfl_games()
    return TEMPLATES.TemplateResponse("nfl.html", {
        "request": request, "games": games,
        "today": today_label, "total": len(games),
        "has_odds_key": bool(os.getenv("ODDS_API_KEY")),
    })


# ─────────────────────────────────────────────
#  REPORTS
# ─────────────────────────────────────────────

@app.get("/reports", response_class=HTMLResponse)
async def reports_page(request: Request):
    reports_dir = Path(__file__).parent.parent / "reports"
    files = sorted(reports_dir.glob("*.md"), reverse=True) if reports_dir.exists() else []
    reports = []
    for f in files:
        content = f.read_text()
        reports.append({"date": f.stem, "content": content, "name": f.name})
    return TEMPLATES.TemplateResponse("reports.html", {
        "request": request, "reports": reports,
    })


# ─────────────────────────────────────────────
#  DEMO
# ─────────────────────────────────────────────

@app.get("/demo", response_class=HTMLResponse)
async def demo_page(request: Request):
    try:
        import demo as demo_mod
        signals = (
            demo_mod.demo_epl()
            + demo_mod.demo_ucl()
            + demo_mod.demo_liga()
            + demo_mod.demo_ligue1()
            + demo_mod.demo_nba()
            + demo_mod.demo_nfl()
        )
        save_markdown_report(signals, outdir=str(Path(__file__).parent.parent / "reports"))
        rows  = signals_to_rows(signals)
        value = signals_to_rows(filter_value_bets(signals, min_edge=2.0))
    except Exception:
        traceback.print_exc()
        rows  = []
        value = []
    return TEMPLATES.TemplateResponse("demo.html", {
        "request": request, "rows": rows, "value": value, "total": len(rows),
    })


# ─────────────────────────────────────────────
#  TODAY — all sports combined
# ─────────────────────────────────────────────

@app.get("/today", response_class=HTMLResponse)
async def today_page(request: Request):
    today_str   = date.today().strftime("%Y%m%d")
    today_label = date.today().strftime("%A, %B %d %Y")
    games = (
        _build_nba_games(today_str)
        + _build_nfl_games()
        + _build_soccer_games(today_str)
    )
    return TEMPLATES.TemplateResponse("today.html", {
        "request":      request,
        "games":        games,
        "today":        today_label,
        "total":        len(games),
        "refresh_secs": 300,
        "has_odds_key": bool(os.getenv("ODDS_API_KEY")),
    })


@app.get("/api/correct-score")
async def api_correct_score(home_xg: float = 1.5, away_xg: float = 1.2):
    grid = correct_score_grid(home_xg, away_xg, max_goals=5)
    return [{"score": f"{h}-{a}", "prob": round(p * 100, 2)} for h, a, p in grid[:12]]


# ─────────────────────────────────────────────
#  Preset data (kept for demo / reference)
# ─────────────────────────────────────────────

_NBA_PRESETS = [
    {"name": "Luka Doncic",              "pts": 35.3, "reb": 7.5,  "ast": 8.6,  "3pm": 4.5},
    {"name": "Shai Gilgeous-Alexander",  "pts": 31.5, "reb": 4.5,  "ast": 6.6,  "3pm": 3.2},
    {"name": "Anthony Edwards",          "pts": 29.7, "reb": 5.1,  "ast": 3.7,  "3pm": 3.8},
    {"name": "Nikola Jokic",             "pts": 28.2, "reb": 12.6, "ast": 10.5, "3pm": 0.6},
    {"name": "Giannis Antetokounmpo",    "pts": 27.6, "reb": 9.8,  "ast": 5.4,  "3pm": 0.5},
    {"name": "Tyrese Maxey",             "pts": 26.5, "reb": 3.8,  "ast": 6.5,  "3pm": 3.2},
    {"name": "Donovan Mitchell",         "pts": 26.3, "reb": 4.5,  "ast": 5.8,  "3pm": 3.0},
    {"name": "Jalen Brunson",            "pts": 25.8, "reb": 3.5,  "ast": 7.5,  "3pm": 2.8},
    {"name": "Kevin Durant",             "pts": 25.0, "reb": 6.5,  "ast": 4.5,  "3pm": 1.8},
]

_NFL_PRESETS = [
    {"name": "James Cook",       "rush_yds": 1621, "rec_yds": 0,    "pass_yds": 0,    "tds": 12, "rush_att": 307, "rec_tgt": 52,  "games": 17},
    {"name": "Jonathan Taylor",  "rush_yds": 1585, "rec_yds": 0,    "pass_yds": 0,    "tds": 20, "rush_att": 271, "rec_tgt": 48,  "games": 16},
    {"name": "Puka Nacua",       "rush_yds": 0,    "rec_yds": 1715, "pass_yds": 0,    "tds": 11, "rush_att": 0,   "rec_tgt": 165, "games": 16},
    {"name": "Trey McBride",     "rush_yds": 0,    "rec_yds": 1260, "pass_yds": 0,    "tds": 8,  "rush_att": 0,   "rec_tgt": 155, "games": 17},
    {"name": "Matthew Stafford", "rush_yds": 0,    "rec_yds": 0,    "pass_yds": 4707, "tds": 46, "rush_att": 0,   "rec_tgt": 0,   "games": 17},
    {"name": "Josh Allen",       "rush_yds": 560,  "rec_yds": 0,    "pass_yds": 4250, "tds": 12, "rush_att": 95,  "rec_tgt": 0,   "games": 17},
    {"name": "Lamar Jackson",    "rush_yds": 780,  "rec_yds": 0,    "pass_yds": 4200, "tds": 5,  "rush_att": 110, "rec_tgt": 0,   "games": 17},
]
