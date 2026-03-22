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
TEMPLATES.env.cache = None  # disable LRU cache (Python 3.14 compatibility)

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
    from data.nba_data   import get_games as nba_get_games, get_team_players_with_averages
    from data.odds_api   import build_odds_lookup, find_game_odds, decimal_to_american
    from data.prizepicks import get_nba_projections
    from utils.stats     import confidence_label, shot_attempt_over_under, threept_made_ou

    # ── PrizePicks line lookup (built once per request) ───────────────────────
    # PrizePicks gives us accurate market LINES.  We use BDL for real per-game
    # AVERAGES.  The two sources are combined: BDL avg + PrizePicks line.
    # NEVER use PrizePicks to estimate a player's average (the old `avg=line*1.05`
    # bug always produced 56.8% because the z-score was a constant).
    _PP_STAT_KEY = {
        "points":    "pts",
        "rebounds":  "reb",
        "assists":   "ast",
        "3-point":   "3pm",
        "threes":    "3pm",
        "three":     "3pm",
        "made":      "3pm",
    }
    # Composite / multi-stat markets to skip — they are NOT per-game single stats
    _PP_SKIP_FRAGMENTS = ("pra", "pts+", "reb+", "ast+", "+reb", "+ast",
                          "points+", "fantasy", "score", "combo")

    pp_lines: dict[tuple, float] = {}
    try:
        for proj in get_nba_projections():
            stat_raw = (proj.get("stat") or "").lower()
            if any(frag in stat_raw for frag in _PP_SKIP_FRAGMENTS) or "+" in stat_raw:
                continue
            sk = next((v for k, v in _PP_STAT_KEY.items() if k in stat_raw), None)
            if not sk:
                continue
            name_key = (proj.get("name") or "").lower().strip()
            line_val  = float(proj.get("line") or 0)
            if line_val > 0:
                pp_lines[(name_key, sk)] = line_val
        print(f"[NBA] PrizePicks line lookup: {len(pp_lines)} entries")
    except Exception as _ppe:
        print(f"[NBA] PrizePicks line fetch failed: {_ppe}")

    # NBA per-game stat caps — values above these are composite/fantasy scores, not real
    _STAT_CAPS = {"pts": 50.0, "reb": 25.0, "ast": 20.0, "fg3m": 10.0}

    def _make_prop(stat_label: str, avg: float, std, player_name: str, stat_key: str):
        """Build one prop dict.  Uses BDL avg + PrizePicks line (if available)."""
        if avg < 0.5:
            return None
        pp_line = pp_lines.get((player_name.lower().strip(), stat_key))
        line    = pp_line if (pp_line and pp_line > 0) else max(0.5, round(avg * 2) / 2 - 0.5)
        if std is None:
            over_p, under_p = threept_made_ou(avg, line)
        else:
            over_p, under_p = shot_attempt_over_under(avg, line, std_factor=std)
        pick = "OVER" if over_p > 0.55 else ("UNDER" if over_p < 0.45 else "FAIR")
        best = max(over_p, under_p)
        return {
            "stat": stat_label, "avg": avg, "line": line,
            "over_prob":  round(over_p  * 100, 1),
            "under_prob": round(under_p * 100, 1),
            "pick": pick,
            "confidence": confidence_label(best),
            "stars": 5 if best >= 0.85 else 4 if best >= 0.75 else 3 if best >= 0.65 else 2 if best >= 0.55 else 1,
        }

    def _nba_props_from_avgs(players: list) -> list:
        """Build props from player dicts with real BDL per-game averages."""
        out = []
        for p in players:
            name  = p.get("name", "")
            props = []
            for stat_label, avg_key, sk, std in [
                ("PTS", "pts",  "pts",  0.28),
                ("3PM", "fg3m", "3pm",  None),
                ("REB", "reb",  "reb",  0.32),
                ("AST", "ast",  "ast",  0.35),
            ]:
                avg_val = float(p.get(avg_key) or p.get(sk) or 0)
                prop = _make_prop(stat_label, avg_val, std, name, sk)
                if prop:
                    props.append(prop)
            if props:
                out.append({**p, "name": name, "pos": p.get("pos", ""),
                            "pts": p.get("pts", 0), "props": props})
        return out

    def _nba_roster_from_leaders(leaders: list, team_id: str) -> list:
        """Fallback: build roster from ESPN competition leaders.
        Caps stat values at realistic NBA per-game maximums to reject
        any fantasy-composite values ESPN may embed in the 'value' field.
        """
        def _build(filtered: list) -> list:
            player_map: dict = {}
            for ldr in filtered:
                name = ldr.get("name", "")
                if not name:
                    continue
                stat = ldr.get("stat", "")
                val  = float(ldr.get("value", 0))
                sk   = {"points": "pts", "rebounds": "reb",
                        "assists": "ast",
                        "threePointFieldGoalsMade": "fg3m"}.get(stat)
                if not sk:
                    continue
                if val > _STAT_CAPS.get(sk, 50.0):
                    print(f"[NBA] ESPN leader {name}/{sk}={val} exceeds cap — skipping (likely fantasy composite)")
                    continue
                if name not in player_map:
                    player_map[name] = {"name": name, "pos": "",
                                        "pts": 0.0, "reb": 0.0, "ast": 0.0, "fg3m": 0.0}
                player_map[name][sk] = val
            return _nba_props_from_avgs(list(player_map.values()))

        team_ldrs = [l for l in leaders if not l.get("team_id") or l.get("team_id") == team_id]
        result    = _build(team_ldrs)
        return result if result else _build(leaders)

    games = []
    try:
        nba_book_lookup = build_odds_lookup("NBA")

        for g in nba_get_games(dates=today_str):
            home_team = g.get("home_team") or "TBD"
            away_team = g.get("away_team") or "TBD"
            home_id   = str(g.get("home_id", ""))
            away_id   = str(g.get("away_id", ""))
            home_abbr = g.get("home_abbr", "")
            away_abbr = g.get("away_abbr", "")

            h_prob = _ml_to_prob(g.get("home_ml")) if g.get("home_ml") else 0.55
            a_prob = 1 - h_prob
            predicted_winner = home_team if h_prob >= a_prob else away_team
            win_prob = h_prob if h_prob >= a_prob else a_prob

            book    = find_game_odds(nba_book_lookup, home_team, away_team) or {}
            leaders = g.get("leaders", [])
            print(f"[NBA] {home_team} vs {away_team} — ESPN leaders: {len(leaders)}")

            # Primary: BDL full team roster — real per-game season averages
            try:
                home_bdl = get_team_players_with_averages(home_abbr)
                away_bdl = get_team_players_with_averages(away_abbr)
                print(f"[NBA] BDL home={len(home_bdl)} away={len(away_bdl)}")
            except Exception as _e:
                print(f"[NBA] BDL error: {_e}")
                home_bdl, away_bdl = [], []

            home_roster = _nba_props_from_avgs(home_bdl) if home_bdl else _nba_roster_from_leaders(leaders, home_id)
            away_roster = _nba_props_from_avgs(away_bdl) if away_bdl else _nba_roster_from_leaders(leaders, away_id)

            # Last resort: presets — split evenly so no player appears for both teams
            if not home_roster and not away_roster:
                print(f"[NBA] No player data — using split presets")
                all_presets = _nba_props_from_avgs(_NBA_PRESETS)
                mid          = max(1, len(all_presets) // 2)
                home_roster  = all_presets[:mid]
                away_roster  = all_presets[mid:]

            print(f"[NBA] Final: home={len(home_roster)} away={len(away_roster)}")

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

            def _nfl_roster_from_leaders(leaders: list, team_id: str) -> list:
                def _build(filtered: list) -> list:
                    player_map: dict = {}
                    for ldr in filtered:
                        name = ldr.get("name", "")
                        pos  = ldr.get("position", "")
                        if not name:
                            continue
                        stat = ldr.get("stat", "")
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

                # Try team-filtered first, fall back to all leaders
                team_ldrs = [l for l in leaders if not l.get("team_id") or l.get("team_id") == team_id]
                result = _build(team_ldrs)
                return result if result else _build(leaders)

            nfl_leaders = g.get("leaders", [])
            print(f"[NFL] {home_team} vs {away_team} — ESPN leaders: {len(nfl_leaders)} entries")
            home_roster = _nfl_roster_from_leaders(nfl_leaders, home_id)
            away_roster = _nfl_roster_from_leaders(nfl_leaders, away_id)

            # Last resort: preset player data so props section is never empty
            if not home_roster and not away_roster:
                print(f"[NFL] No player data — using presets")
                def _preset_nfl_roster(presets):
                    out = []
                    for p in presets:
                        props = []
                        stats = [
                            ("pass_yds", "Pass Yds", 0.40),
                            ("rush_yds", "Rush Yds", 0.55),
                            ("rec_yds",  "Rec Yds",  0.60),
                        ]
                        for key, label, std in stats:
                            total = p.get(key, 0)
                            if total < 10:
                                continue
                            avg  = total / p.get("games", 17)
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
                            out.append({"name": p["name"], "pos": "", "props": props})
                    return out
                preset_roster = _preset_nfl_roster(_NFL_PRESETS[:4])
                home_roster = preset_roster
                away_roster = preset_roster

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
    return TEMPLATES.TemplateResponse(request, "index.html")


# ─────────────────────────────────────────────
#  SOCCER — live fixtures dashboard
# ─────────────────────────────────────────────

@app.get("/soccer", response_class=HTMLResponse)
async def soccer_page(request: Request):
    today_str   = date.today().strftime("%Y%m%d")
    today_label = date.today().strftime("%A, %B %d %Y")
    games       = _build_soccer_games(today_str)
    return TEMPLATES.TemplateResponse(request, "soccer.html", {
        "games": games, "today": today_label,
        "total": len(games), "has_odds_key": bool(os.getenv("ODDS_API_KEY")),
    })


# ─────────────────────────────────────────────
#  NBA — live games dashboard
# ─────────────────────────────────────────────

@app.get("/nba", response_class=HTMLResponse)
async def nba_page(request: Request):
    today_str   = date.today().strftime("%Y%m%d")
    today_label = date.today().strftime("%A, %B %d %Y")
    games       = _build_nba_games(today_str)
    return TEMPLATES.TemplateResponse(request, "nba.html", {
        "games": games, "today": today_label,
        "total": len(games), "has_odds_key": bool(os.getenv("ODDS_API_KEY")),
    })


# ─────────────────────────────────────────────
#  NFL — live games dashboard
# ─────────────────────────────────────────────

@app.get("/nfl", response_class=HTMLResponse)
async def nfl_page(request: Request):
    today_label = date.today().strftime("%A, %B %d %Y")
    games       = _build_nfl_games()
    return TEMPLATES.TemplateResponse(request, "nfl.html", {
        "games": games, "today": today_label,
        "total": len(games), "has_odds_key": bool(os.getenv("ODDS_API_KEY")),
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
    return TEMPLATES.TemplateResponse(request, "reports.html", {"reports": reports})


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
    return TEMPLATES.TemplateResponse(request, "demo.html", {
        "rows": rows, "value": value, "total": len(rows),
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
    return TEMPLATES.TemplateResponse(request, "today.html", {
        "games":        games,
        "today":        today_label,
        "total":        len(games),
        "refresh_secs": 300,
        "has_odds_key": bool(os.getenv("ODDS_API_KEY")),
    })


def _build_mlb_props(today_str: str) -> tuple[list, int]:
    """
    Build MLB player prop cards for tonight's games.
    Returns (all_props_flat, total_games).
    Priority: PrizePicks → ESPN leaders + MLB Stats API.
    """
    from data.mlb_data   import get_games as mlb_get_games
    from data.prizepicks import get_mlb_projections
    from models.mlb_model import build_props_from_prizepicks, build_pitcher_props, build_batter_props
    from utils.stats      import confidence_label, shot_attempt_over_under

    all_props: list = []
    try:
        games = mlb_get_games(dates=today_str)
        total_games = len(games)
        print(f"[MLB] {total_games} games today")

        # Try PrizePicks first — gives real lines for tonight
        pp_projs = get_mlb_projections()
        print(f"[MLB] PrizePicks: {len(pp_projs)} projections")
        if pp_projs:
            all_props = build_props_from_prizepicks(pp_projs)
        else:
            # Fall back: ESPN leaders + season-avg model
            for g in games:
                opponent_map = {
                    g.get("home_abbr", ""): g.get("away_abbr", ""),
                    g.get("away_abbr", ""): g.get("home_abbr", ""),
                }
                for ldr in g.get("leaders", []):
                    name = ldr.get("name", "")
                    stat = ldr.get("stat", "")
                    val  = float(ldr.get("value", 0) or 0)
                    pos  = ldr.get("position", "")
                    if not name:
                        continue
                    is_pitcher = pos in ("SP", "RP", "P") or stat in ("strikeouts", "earnedRunAverage")
                    if is_pitcher:
                        pstats = {"so_pg": val if stat == "strikeouts" else 5.0,
                                  "ip_pg": 5.5}
                        props = build_pitcher_props(pstats)
                    else:
                        bstats = {"hits_pg": val if stat == "hits" else 0.9,
                                  "tb_pg":   1.5, "runs_pg": 0.6}
                        props = build_batter_props(bstats)
                    if props:
                        all_props.append({
                            "name": name, "pos": pos,
                            "team": ldr.get("team_id", ""),
                            "props": props,
                        })
    except Exception:
        traceback.print_exc()
        total_games = 0

    return all_props, total_games


def _build_nhl_props(today_str: str) -> tuple[list, int]:
    """
    Build NHL player prop cards for tonight's games.
    Priority: PrizePicks → ESPN leaders + NHL API.
    """
    from data.nhl_data    import get_games as nhl_get_games, get_team_roster_stats
    from data.prizepicks  import get_nhl_projections
    from models.nhl_model import build_skater_props, build_goalie_props, build_props_from_prizepicks
    from utils.stats      import confidence_label

    all_props: list = []
    try:
        games = nhl_get_games(dates=today_str)
        total_games = len(games)
        print(f"[NHL] {total_games} games today")

        pp_projs = get_nhl_projections()
        print(f"[NHL] PrizePicks: {len(pp_projs)} projections")
        if pp_projs:
            all_props = build_props_from_prizepicks(pp_projs)
        else:
            for g in games:
                for abbr in [g.get("home_abbr",""), g.get("away_abbr","")]:
                    if not abbr:
                        continue
                    try:
                        players = get_team_roster_stats(abbr)
                    except Exception:
                        players = []
                    for p in players[:10]:
                        if p.get("is_goalie"):
                            props = build_goalie_props(p)
                        else:
                            props = build_skater_props(p)
                        if props:
                            all_props.append({**p, "team": abbr, "props": props})
    except Exception:
        traceback.print_exc()
        total_games = 0

    return all_props, total_games


# ─────────────────────────────────────────────
#  MLB — live props dashboard
# ─────────────────────────────────────────────

@app.get("/mlb", response_class=HTMLResponse)
async def mlb_page(request: Request):
    today_str   = date.today().strftime("%Y%m%d")
    today_label = date.today().strftime("%A, %B %d %Y")
    all_props, total_games = _build_mlb_props(today_str)
    return TEMPLATES.TemplateResponse(request, "mlb.html", {
        "all_props":    all_props,
        "total_players": len(all_props),
        "total_games":  total_games,
        "today":        today_label,
    })


# ─────────────────────────────────────────────
#  NHL — live props dashboard
# ─────────────────────────────────────────────

@app.get("/nhl", response_class=HTMLResponse)
async def nhl_page(request: Request):
    today_str   = date.today().strftime("%Y%m%d")
    today_label = date.today().strftime("%A, %B %d %Y")
    all_props, total_games = _build_nhl_props(today_str)
    return TEMPLATES.TemplateResponse(request, "nhl.html", {
        "all_props":    all_props,
        "total_players": len(all_props),
        "total_games":  total_games,
        "today":        today_label,
    })


@app.get("/api/debug/nba")
async def debug_nba():
    """Diagnostic: returns raw ESPN leaders + BDL availability for today's NBA games."""
    from data.nba_data import get_games as nba_get_games, get_team_players_with_averages
    from datetime import date
    today_str = date.today().strftime("%Y%m%d")
    raw_games = nba_get_games(dates=today_str)
    out = []
    for g in raw_games:
        home_abbr = g.get("home_abbr", "")
        away_abbr = g.get("away_abbr", "")
        try:
            home_bdl = get_team_players_with_averages(home_abbr)
        except Exception as e:
            home_bdl = f"ERROR: {e}"
        try:
            away_bdl = get_team_players_with_averages(away_abbr)
        except Exception as e:
            away_bdl = f"ERROR: {e}"
        out.append({
            "game": f"{g.get('home_team')} vs {g.get('away_team')}",
            "home_id": g.get("home_id"), "away_id": g.get("away_id"),
            "home_abbr": home_abbr, "away_abbr": away_abbr,
            "espn_leaders_count": len(g.get("leaders", [])),
            "espn_leaders": g.get("leaders", []),
            "bdl_home_count": len(home_bdl) if isinstance(home_bdl, list) else home_bdl,
            "bdl_home_sample": home_bdl[:2] if isinstance(home_bdl, list) else home_bdl,
            "bdl_away_count": len(away_bdl) if isinstance(away_bdl, list) else away_bdl,
            "bdl_away_sample": away_bdl[:2] if isinstance(away_bdl, list) else away_bdl,
        })
    return {"today": today_str, "game_count": len(raw_games), "games": out}


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
