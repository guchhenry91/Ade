"""
FastAPI web application for the Sports Betting Model.
"""
from __future__ import annotations
import sys, os
# Ensure the model root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

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


# ── helpers ──────────────────────────────────────────────────────────────────

def signals_to_rows(signals) -> list[dict]:
    return [s.to_dict() for s in rank_signals(signals)]


def parse_odds_json(raw: str) -> dict:
    """Safely parse a JSON odds string from a form field."""
    try:
        return json.loads(raw) if raw and raw.strip() else {}
    except Exception:
        return {}


# ── pages ─────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return TEMPLATES.TemplateResponse("index.html", {"request": request})


# ─────────────────────────────────────────────
#  SOCCER
# ─────────────────────────────────────────────

@app.get("/soccer", response_class=HTMLResponse)
async def soccer_page(request: Request):
    return TEMPLATES.TemplateResponse("soccer.html", {
        "request": request,
        "rows": [],
        "error": None,
        "leagues": ["EPL", "UCL", "LIGA", "L1"],
        "league_names": {
            "EPL": "Premier League",
            "UCL": "Champions League",
            "LIGA": "La Liga",
            "L1": "Ligue 1",
        },
    })


@app.post("/soccer", response_class=HTMLResponse)
async def soccer_analyze(
    request: Request,
    home_team: str = Form(...),
    away_team: str = Form(...),
    league: str    = Form("EPL"),
    home_odds: str = Form(""),
    draw_odds: str = Form(""),
    away_odds: str = Form(""),
    ou_over:   str = Form(""),
    ou_under:  str = Form(""),
    btts_odds: str = Form(""),
    cs_odds:   str = Form(""),   # JSON string: {"1-0": 6.5, ...}
):
    error = None
    rows  = []
    cs_grid = []
    try:
        model = FootballModel(league)

        market_odds: dict = {}
        if home_odds or draw_odds or away_odds:
            winner = {}
            if home_odds: winner["home"] = float(home_odds)
            if draw_odds: winner["draw"] = float(draw_odds)
            if away_odds: winner["away"] = float(away_odds)
            market_odds["winner"] = winner
        if ou_over or ou_under:
            ou = {}
            if ou_over:  ou["over"]  = float(ou_over)
            if ou_under: ou["under"] = float(ou_under)
            market_odds["ou25"] = ou
        if btts_odds:
            market_odds["btts"] = float(btts_odds)
        if cs_odds.strip():
            market_odds["correct_score"] = parse_odds_json(cs_odds)

        signals  = model.analyze_fixture(home_team, away_team, market_odds)
        rows     = signals_to_rows(signals)

        # separate correct score grid for visual display
        home_xg, away_xg = model._match_xg(home_team, away_team)
        grid = correct_score_grid(home_xg, away_xg, max_goals=5)
        cs_grid = [
            {"score": f"{h}-{a}", "prob": f"{p*100:.1f}%",
             "prob_raw": round(p * 100, 1),
             "result": "H" if h > a else ("D" if h == a else "A")}
            for h, a, p in grid[:12]
        ]

    except Exception as e:
        error = str(e)
        traceback.print_exc()

    return TEMPLATES.TemplateResponse("soccer.html", {
        "request":    request,
        "rows":       rows,
        "cs_grid":    cs_grid,
        "error":      error,
        "home_team":  home_team,
        "away_team":  away_team,
        "league":     league,
        "leagues":    ["EPL", "UCL", "LIGA", "L1"],
        "league_names": {
            "EPL": "Premier League", "UCL": "Champions League",
            "LIGA": "La Liga",       "L1": "Ligue 1",
        },
    })


# ─────────────────────────────────────────────
#  NBA
# ─────────────────────────────────────────────

@app.get("/nba", response_class=HTMLResponse)
async def nba_page(request: Request):
    return TEMPLATES.TemplateResponse("nba.html", {
        "request": request, "rows": [], "error": None,
        "players": _NBA_PRESETS,
    })


@app.post("/nba", response_class=HTMLResponse)
async def nba_analyze(
    request: Request,
    mode:        str   = Form("props"),   # "game" | "props"
    home_team:   str   = Form(""),
    away_team:   str   = Form(""),
    home_ml:     str   = Form(""),
    away_ml:     str   = Form(""),
    total_line:  str   = Form(""),
    home_ortg:   str   = Form("115.0"),
    away_ortg:   str   = Form("115.0"),
    home_pace:   str   = Form("100.0"),
    away_pace:   str   = Form("100.0"),
    player_name: str   = Form(""),
    avg_pts:     str   = Form(""),
    avg_reb:     str   = Form(""),
    avg_ast:     str   = Form(""),
    avg_3pm:     str   = Form(""),
    line_pts:    str   = Form(""),
    line_reb:    str   = Form(""),
    line_ast:    str   = Form(""),
    line_3pm:    str   = Form(""),
):
    error = None
    rows  = []
    try:
        model   = NBAModel(season=2025)
        signals = []

        if mode == "game" and home_team and away_team:
            ml_odds = {}
            if home_ml: ml_odds["home"] = float(home_ml)
            if away_ml: ml_odds["away"] = float(away_ml)
            signals += model.game_winner_signals(home_team, away_team,
                                                  market_odds=ml_odds or None)
            if total_line:
                ou_odds = {"over": 1.91, "under": 1.91}
                signals += model.total_points_signals(
                    home_team, away_team,
                    line      = float(total_line),
                    home_ortg = float(home_ortg),
                    away_ortg = float(away_ortg),
                    home_pace = float(home_pace),
                    away_pace = float(away_pace),
                    market_odds = ou_odds,
                )

        elif mode == "props" and player_name:
            avgs  = {}
            lines = {}
            if avg_pts:  avgs["pts"]  = float(avg_pts)
            if avg_reb:  avgs["reb"]  = float(avg_reb)
            if avg_ast:  avgs["ast"]  = float(avg_ast)
            if avg_3pm:  avgs["3pm"]  = float(avg_3pm)
            if line_pts: lines["pts"] = float(line_pts)
            if line_reb: lines["reb"] = float(line_reb)
            if line_ast: lines["ast"] = float(line_ast)
            if line_3pm: lines["3pm"] = float(line_3pm)
            if avgs and lines:
                signals = model.player_props_by_name(
                    player_name, lines, known_avgs=avgs)

        rows = signals_to_rows(signals)

    except Exception as e:
        error = str(e)
        traceback.print_exc()

    return TEMPLATES.TemplateResponse("nba.html", {
        "request":     request,
        "rows":        rows,
        "error":       error,
        "mode":        mode,
        "player_name": player_name,
        "players":     _NBA_PRESETS,
    })


# ─────────────────────────────────────────────
#  NFL
# ─────────────────────────────────────────────

@app.get("/nfl", response_class=HTMLResponse)
async def nfl_page(request: Request):
    return TEMPLATES.TemplateResponse("nfl.html", {
        "request": request, "rows": [], "error": None,
        "players": _NFL_PRESETS,
    })


@app.post("/nfl", response_class=HTMLResponse)
async def nfl_analyze(
    request: Request,
    mode:       str = Form("game"),   # "game" | "td" | "yards"
    home_team:  str = Form(""),
    away_team:  str = Form(""),
    spread:     str = Form(""),
    home_ml:    str = Form(""),
    away_ml:    str = Form(""),
    total_line: str = Form(""),
    home_ppg:   str = Form("23.0"),
    away_ppg:   str = Form("23.0"),
    # TD / yards
    player_name: str = Form(""),
    season_tds:  str = Form(""),
    rush_att:    str = Form(""),
    rec_tgt:     str = Form(""),
    games:       str = Form("17"),
    td_odds:     str = Form(""),
    rush_yds:    str = Form(""),
    rec_yds:     str = Form(""),
    pass_yds:    str = Form(""),
    rush_line:   str = Form(""),
    rec_line:    str = Form(""),
    pass_line:   str = Form(""),
):
    error = None
    rows  = []
    try:
        model   = NFLModel(season=2025)
        signals = []

        if mode == "game" and home_team and away_team:
            sp   = float(spread) if spread else None
            ml   = {}
            if home_ml: ml["home"] = float(home_ml)
            if away_ml: ml["away"] = float(away_ml)
            signals += model.game_winner_signals(home_team, away_team,
                                                  spread=sp, market_odds=ml or None)
            if spread:
                signals += model.spread_signals(home_team, away_team, float(spread),
                                                 market_odds={"home": 1.91, "away": 1.91})
            if total_line:
                signals += model.total_points_signals(
                    home_team, away_team,
                    line        = float(total_line),
                    home_stats  = {"pointsPerGame": float(home_ppg)},
                    away_stats  = {"pointsPerGame": float(away_ppg)},
                    market_odds = {"over": 1.91, "under": 1.91},
                )

        elif mode == "td" and player_name:
            from utils.stats import nfl_td_prob, edge_pct, kelly_fraction, confidence_label
            from utils.odds  import BetSignal
            g    = int(games) if games else 17
            ra   = float(rush_att) if rush_att else 0
            rt   = float(rec_tgt)  if rec_tgt  else 0
            rz_c = ra / g * 0.15
            rz_t = rt / g * 0.12
            prob = nfl_td_prob(rz_t, rz_c, games=1)
            odds = float(td_odds) if td_odds else None
            ep   = edge_pct(prob, odds) if odds else None
            signals.append(BetSignal(
                sport="American Football", league="NFL",
                market="ANYTIME_TD_SCORER",
                selection=f"{player_name} – Anytime TD",
                model_prob=prob, market_odds=odds, edge_pct=ep,
                confidence=confidence_label(prob),
                notes=f"RZ carries/gm {rz_c:.1f}  RZ tgt/gm {rz_t:.1f}  TDs: {season_tds}",
            ))

        elif mode == "yards" and player_name:
            from utils.stats import shot_attempt_over_under, confidence_label
            from utils.odds  import BetSignal
            g  = int(games) if games else 17
            for total_yds, line_val, market_label in [
                (rush_yds, rush_line, "PLAYER_RUSHING_YARDS_OU"),
                (rec_yds,  rec_line,  "PLAYER_RECEIVING_YARDS_OU"),
                (pass_yds, pass_line, "PLAYER_PASSING_YARDS_OU"),
            ]:
                if total_yds and line_val:
                    avg  = float(total_yds) / g
                    line = float(line_val)
                    ov, un = shot_attempt_over_under(avg, line, std_factor=0.35)
                    for lbl, prob in [(f"Over {line}", ov), (f"Under {line}", un)]:
                        signals.append(BetSignal(
                            sport="American Football", league="NFL",
                            market=market_label,
                            selection=f"{player_name} – {lbl} yds",
                            model_prob=prob,
                            confidence=confidence_label(prob),
                            notes=f"Season avg: {avg:.1f} yds/gm",
                        ))

        rows = signals_to_rows(signals)

    except Exception as e:
        error = str(e)
        traceback.print_exc()

    return TEMPLATES.TemplateResponse("nfl.html", {
        "request":     request,
        "rows":        rows,
        "error":       error,
        "mode":        mode,
        "player_name": player_name,
        "players":     _NFL_PRESETS,
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
#  DEMO — run all real 2025-26 data
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
        rows = signals_to_rows(signals)
        value = signals_to_rows(filter_value_bets(signals, min_edge=2.0))
    except Exception as e:
        traceback.print_exc()
        rows  = []
        value = []
    return TEMPLATES.TemplateResponse("demo.html", {
        "request": request,
        "rows":    rows,
        "value":   value,
        "total":   len(rows),
    })


# ─────────────────────────────────────────────
#  JSON API endpoints (for JS fetch)
# ─────────────────────────────────────────────

@app.get("/today", response_class=HTMLResponse)
async def today_page(request: Request):
    from data.nba_data import get_games as nba_get_games, get_team_net_rating
    from data.football_data import get_fixtures
    from utils.stats import poisson_match_probs, nba_win_prob, confidence_label

    today_str = date.today().strftime("%Y%m%d")
    today_label = date.today().strftime("%A, %B %d %Y")
    games = []

    # ── NBA ──────────────────────────────────────────────────────────────────
    try:
        for g in nba_get_games(dates=today_str):
            home_team = g.get("home_team") or "TBD"
            away_team = g.get("away_team") or "TBD"
            home_nr = get_team_net_rating(g["home_id"]) if g.get("home_id") else 0.0
            away_nr = get_team_net_rating(g["away_id"]) if g.get("away_id") else 0.0
            h_prob = nba_win_prob(home_nr, away_nr, home_advantage=3.5)
            a_prob = 1 - h_prob
            if h_prob >= a_prob:
                predicted_winner, win_prob = home_team, h_prob
            else:
                predicted_winner, win_prob = away_team, a_prob
            games.append({
                "sport": "Basketball", "league": "NBA", "sport_icon": "🏀",
                "home_team": home_team, "away_team": away_team,
                "kickoff": g.get("date", ""), "status": g.get("status", ""),
                "home_score": g.get("home_score"), "away_score": g.get("away_score"),
                "home_prob": round(h_prob * 100, 1),
                "away_prob": round(a_prob * 100, 1),
                "draw_prob": None,
                "predicted_winner": predicted_winner,
                "win_prob": round(win_prob * 100, 1),
                "confidence": confidence_label(win_prob),
            })
    except Exception:
        traceback.print_exc()

    # ── Soccer ────────────────────────────────────────────────────────────────
    soccer_leagues = [
        ("EPL", "Premier League"), ("UCL", "Champions League"),
        ("LIGA", "La Liga"), ("L1", "Ligue 1"),
    ]
    for league_key, league_name in soccer_leagues:
        try:
            fixtures = get_fixtures(league_key, dates=today_str)
            model = FootballModel(league_key)
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
                games.append({
                    "sport": "Soccer", "league": league_name, "sport_icon": "⚽",
                    "home_team": home_team, "away_team": away_team,
                    "kickoff": fix.get("date", ""), "status": fix.get("status", ""),
                    "home_score": fix.get("home_score"), "away_score": fix.get("away_score"),
                    "home_prob": round(h_prob * 100, 1),
                    "away_prob": round(a_prob * 100, 1),
                    "draw_prob": round(d_prob * 100, 1),
                    "predicted_winner": predicted_winner,
                    "win_prob": round(win_prob * 100, 1),
                    "confidence": confidence_label(win_prob),
                })
        except Exception:
            traceback.print_exc()

    return TEMPLATES.TemplateResponse("today.html", {
        "request": request,
        "games": games,
        "today": today_label,
        "total": len(games),
    })


@app.get("/api/correct-score")
async def api_correct_score(home_xg: float = 1.5, away_xg: float = 1.2):
    grid = correct_score_grid(home_xg, away_xg, max_goals=5)
    return [{"score": f"{h}-{a}", "prob": round(p * 100, 2)} for h, a, p in grid[:12]]


# ─────────────────────────────────────────────
#  Preset data for quick-fill
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
