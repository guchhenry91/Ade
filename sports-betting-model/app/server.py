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
import time as _time
import threading

from datetime import date, datetime, timezone
from pathlib import Path

# ── In-memory page cache ───────────────────────────────────────────────────────
# Avoids recomputing expensive data fetches on every page request.
# TTL: 10 minutes for live props data.
_PAGE_CACHE: dict = {}
_CACHE_LOCK = threading.Lock()

def _cached(key: str, fetch_fn, ttl: int = 600):
    """Return cached value if fresh, else call fetch_fn(), cache, and return."""
    now = _time.time()
    with _CACHE_LOCK:
        entry = _PAGE_CACHE.get(key)
        if entry:
            data, expires = entry
            if now < expires:
                print(f"[CACHE HIT] {key}")
                return data
            print(f"[CACHE EXPIRED] {key}")
        else:
            print(f"[CACHE MISS] {key}")
    data = fetch_fn()
    if data is not None:
        with _CACHE_LOCK:
            _PAGE_CACHE[key] = (data, now + ttl)
    return data

from models.football_model import FootballModel
from models.nba_model      import NBAModel
from models.nfl_model      import NFLModel
from markets.value_engine  import filter_value_bets, rank_signals
from markets.report        import save_markdown_report
from utils.stats           import correct_score_grid

BASE_DIR   = Path(__file__).parent
TEMPLATES  = Jinja2Templates(directory=str(BASE_DIR / "templates"))
TEMPLATES.env.cache = None  # disable LRU cache (Python 3.14 compatibility)

# Part 8 — Jinja format_american filter
def _format_american_filter(price):
    """Jinja2 filter: format American odds price as display string."""
    if price is None:
        return "\u2013"
    try:
        price = float(price)
    except (TypeError, ValueError):
        return str(price)
    if price > 0:
        return f"+{round(price)}"
    return str(round(price))

TEMPLATES.env.filters["format_american"] = _format_american_filter

app = FastAPI(title="Sports Betting Model", version="1.0")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.on_event("startup")
async def _startup_preload():
    """Warm the cache in background so the first real request is fast.
    Sports are loaded sequentially with 2s gaps to avoid Odds API rate limits.
    """
    def _preload():
        today_str = date.today().strftime("%Y%m%d")
        print("[STARTUP] Preloading NBA / MLB / NHL sequentially… (Odds API source)")
        for key, fn in [
            (f"nba_{today_str}", lambda: _build_nba_games(today_str)),
            (f"mlb_{today_str}", lambda: _build_mlb_props(today_str)),
            (f"nhl_{today_str}", lambda: _build_nhl_props(today_str)),
        ]:
            try:
                data = fn()
                with _CACHE_LOCK:
                    _PAGE_CACHE[key] = (data, _time.time() + 600)
                print(f"[STARTUP] {key} preloaded OK")
            except Exception as _e:
                print(f"[STARTUP] {key} preload failed: {_e}")
            _time.sleep(3)  # 3s gap between sports to avoid rate limiting
    threading.Thread(target=_preload, daemon=True).start()


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    tb = traceback.format_exc()
    # Log for server-side debugging
    print(f"[ERROR] {request.url.path}: {exc}\n{tb}")
    # Return user-friendly error page
    return HTMLResponse(
        content="""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>BetModel Pro · Error</title>
<style>
body{font-family:system-ui,sans-serif;background:#0d0d0d;color:#fff;
  display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;}
.box{text-align:center;max-width:480px;padding:40px;}
.icon{font-size:56px;margin-bottom:16px;}
h1{font-size:22px;font-weight:700;margin-bottom:10px;}
p{color:#aaa;font-size:14px;line-height:1.6;margin-bottom:24px;}
a{display:inline-block;padding:10px 24px;background:#00d4ff;color:#000;
  border-radius:8px;font-weight:700;text-decoration:none;}
</style></head><body>
<div class="box">
  <div class="icon">⚠️</div>
  <h1>Something went wrong</h1>
  <p>We had trouble loading this page. This is usually a temporary issue
     with one of our data sources.</p>
  <a href="javascript:location.reload()">Try again</a>
  &nbsp;
  <a href="/" style="background:#1a1a2e;color:#aaa;border:1px solid #2a2a4e;">
    Go home
  </a>
</div></body></html>""",
        status_code=500,
    )


@app.get("/health")
@app.get("/healthz")
async def healthz():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


def _now_iso() -> str:
    """Current UTC time as ISO-8601 string, passed to templates."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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

def _normalize_games(games: list) -> list:
    """Ensure top_pick dicts use player_name key (odds_api may use 'player')."""
    for game in games:
        tp = game.get("top_pick")
        if isinstance(tp, dict) and "player_name" not in tp:
            tp["player_name"] = tp.get("player", "")
    return games


def _build_nba_games(today_str: str) -> list:
    from data.odds_api import build_sport_props
    return _normalize_games(build_sport_props("nba"))


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
    from data.football_data import (get_fixtures, get_team_players as get_soccer_players,
                                    get_soccer_win_pcts)
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
            book_lookup   = build_odds_lookup(league_key)
            fixtures      = get_fixtures(league_key, dates=today_str)
            model         = FootballModel(league_key)
            soc_standings = get_soccer_win_pcts(league_key)  # 6h cache

            for fix in fixtures:
                home_team = fix.get("home_team") or "TBD"
                away_team = fix.get("away_team") or "TBD"

                # Win probability: ESPN standings → Poisson xG model → defaults
                h_wpct = soc_standings.get(home_team.lower())
                a_wpct = soc_standings.get(away_team.lower())

                if h_wpct is not None and a_wpct is not None and h_wpct + a_wpct > 0:
                    # Standings-based: home advantage +5% for soccer
                    raw_h = h_wpct / (h_wpct + a_wpct)
                    raw_h = max(0.20, min(0.80, raw_h * 0.95 + 0.05))  # +5% HCA
                    # Draw probability: stronger when teams are evenly matched
                    d_prob = max(0.05, 0.28 - abs(raw_h - 0.5) * 0.30)
                    h_prob = raw_h * (1 - d_prob)
                    a_prob = (1 - raw_h) * (1 - d_prob)
                    # Normalize to 100%
                    total  = h_prob + d_prob + a_prob
                    h_prob, d_prob, a_prob = h_prob/total, d_prob/total, a_prob/total
                else:
                    # Fall back to Poisson xG model
                    home_xg, away_xg = model._match_xg(home_team, away_team)
                    h_prob, d_prob, a_prob = poisson_match_probs(home_xg, away_xg)

                if h_prob >= d_prob and h_prob >= a_prob:
                    predicted_winner, win_prob = home_team, h_prob
                elif d_prob >= a_prob:
                    predicted_winner, win_prob = "Draw", d_prob
                else:
                    predicted_winner, win_prob = away_team, a_prob

                book = find_game_odds(book_lookup, home_team, away_team) or {}

                def _soccer_roster(team_name: str, team_xg: float,
                                   fix_leaders: list, team_id: str) -> list:
                    """
                    Build scorer list for team_name.
                    Priority: api-football player stats → ESPN competition leaders.
                    Never returns fake placeholder data.
                    """
                    from utils.stats import scorer_probability
                    # 1. Try api-football (returns [] if no key set)
                    team_pl = get_soccer_players(league_key, team_name, season=2024)

                    if team_pl:
                        sigs = model.player_anytime_scorer_signals(
                            f"{home_team} vs {away_team}", team_name, team_xg,
                            players_override=team_pl,
                        )
                        out = []
                        for s in sigs:
                            if "Lead Striker (estimate)" in s.selection:
                                continue
                            notes  = s.notes or ""
                            shots  = notes.split("Shots/game:")[1].strip().split()[0] if "Shots/game:" in notes else ""
                            xg_sh  = notes.split("xG/shot:")[1].strip().split()[0] if "xG/shot:" in notes else ""
                            out.append({
                                "name":       s.selection,
                                "shots_pg":   shots,
                                "xg_shot":    xg_sh,
                                "goal_prob":  round(s.model_prob * 100, 1),
                                "confidence": s.confidence,
                                "source":     "api-football",
                            })
                        if out:
                            return out

                    # 2. ESPN competition leaders fallback (no key needed)
                    team_ldrs = [
                        l for l in fix_leaders
                        if not l.get("team_id") or l.get("team_id") == str(team_id)
                    ]
                    if not team_ldrs:
                        team_ldrs = fix_leaders  # widen if team filter leaves nothing

                    out = []
                    seen_names: set = set()
                    for ldr in team_ldrs:
                        lname = ldr.get("name", "")
                        stat  = ldr.get("stat", "")
                        val   = float(ldr.get("value") or 0)
                        if not lname or lname in seen_names:
                            continue
                        if stat not in ("goals", "shotsOnTarget", "shots"):
                            continue
                        seen_names.add(lname)
                        # ESPN leaders return season totals; divide by approx games played
                        approx_games = 30  # conservative mid-season estimate
                        if stat == "goals":
                            goals_pg = val / approx_games
                            shots_pg = max(1.5, goals_pg / 0.12)  # back-calc from goal rate
                            xg_shot  = 0.12
                        else:  # shotsOnTarget, shots
                            shots_pg = max(1.5, val / approx_games)
                            xg_shot  = 0.12
                        goal_prob = scorer_probability(xg_shot, shots_pg) * 100
                        confidence_lbl = "HIGH" if goal_prob >= 60 else "MEDIUM" if goal_prob >= 40 else "LOW"
                        out.append({
                            "name":       lname,
                            "shots_pg":   round(shots_pg, 1),
                            "xg_shot":    xg_shot,
                            "goal_prob":  round(goal_prob, 1),
                            "confidence": confidence_lbl,
                            "source":     "ESPN",
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
                    "home_roster":  _soccer_roster(home_team, home_xg,
                                                      fix.get("leaders", []),
                                                      fix.get("home_id", "")),
                    "away_roster":  _soccer_roster(away_team, away_xg,
                                                      fix.get("leaders", []),
                                                      fix.get("away_id", "")),
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
    from data.odds_api import build_soccer_props
    today_label = date.today().strftime("%A, %B %d %Y")
    result      = _cached("soccer", build_soccer_props, ttl=600)
    league_blocks: list = []
    upcoming: list = []
    if isinstance(result, dict):
        raw_games = result.get("games", [])
        upcoming  = result.get("upcoming", [])
        # New format: list of league blocks [{league, sport_key, games:[...]}]
        if raw_games and isinstance(raw_games[0], dict) and "games" in raw_games[0]:
            league_blocks = raw_games
        else:
            # Legacy flat list — wrap in a single block
            league_blocks = [{"league": "Soccer", "sport_key": "soccer", "games": raw_games}]
    else:
        league_blocks = []

    total = sum(len(b.get("games", [])) for b in league_blocks)

    # Group upcoming fixtures by league for the "Coming Up" display
    _ul: dict = {}
    for u in upcoming:
        lg = u.get("league", "")
        if lg not in _ul:
            _ul[lg] = {"league": lg, "icon": u.get("sport_icon", ""), "games": []}
        _ul[lg]["games"].append(u)
    upcoming_leagues = list(_ul.values())

    return TEMPLATES.TemplateResponse(request, "soccer.html", {
        "league_blocks": league_blocks,
        "today": today_label,
        "total": total,
        "has_odds_key": bool(os.getenv("ODDS_API_KEY")),
        "upcoming_leagues": upcoming_leagues,
        "generated_at": _now_iso(),
    })


# ─────────────────────────────────────────────
#  NBA — live games dashboard
# ─────────────────────────────────────────────

@app.get("/nba", response_class=HTMLResponse)
async def nba_page(request: Request):
    today_str   = date.today().strftime("%Y%m%d")
    today_label = date.today().strftime("%A, %B %d %Y")
    games       = _cached(f"nba_{today_str}", lambda: _build_nba_games(today_str), ttl=3600)
    return TEMPLATES.TemplateResponse(request, "nba.html", {
        "games": games or [], "today": today_label,
        "total": len(games or []), "has_odds_key": bool(os.getenv("ODDS_API_KEY")),
        "generated_at": _now_iso(),
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
        "offseason": len(games) == 0,
        "generated_at": _now_iso(),
    })


# ─────────────────────────────────────────────
#  REPORTS
# ─────────────────────────────────────────────

@app.get("/reports", response_class=HTMLResponse)
async def reports_page(request: Request):
    return TEMPLATES.TemplateResponse(request, "reports.html", {})


@app.get("/api/mycard-preview")
async def mycard_preview():
    """Return top 3 A/B grade props + passes for the homepage sharp card."""
    today_str = date.today().strftime("%Y%m%d")
    try:
        all_props = []
        for sport_key, build_fn in [
            ("nba", lambda: _build_nba_games(today_str)),
            ("mlb", lambda: _build_mlb_props(today_str)),
            ("nhl", lambda: _build_nhl_props(today_str)),
        ]:
            try:
                games = _cached(f"{sport_key}_{today_str}", build_fn, ttl=1800) or []
            except Exception:
                continue
            for game in games:
                game_label = f"{game.get('away_abbr', '')} @ {game.get('home_abbr', '')}"
                for player in (game.get("home_roster", []) + game.get("away_roster", [])):
                    for prop in player.get("props", []):
                        all_props.append({
                            **prop,
                            "player_name": player.get("name", ""),
                            "sport": sport_key,
                            "game": game_label,
                        })
        all_props.sort(key=lambda x: x.get("confidence", 0), reverse=True)
        a_props   = [p for p in all_props if p.get("grade") == "A"]
        b_props   = [p for p in all_props if p.get("grade") == "B"]
        pass_props = [p for p in all_props if p.get("grade") == "Pass"]
        best3  = (a_props + b_props)[:3]
        passes = sorted(pass_props, key=lambda x: x.get("confidence", 0), reverse=True)[:3]
        return JSONResponse({
            "best3":   best3,
            "passes":  passes,
            "a_count": len(a_props),
            "b_count": len(b_props),
        })
    except Exception as e:
        return JSONResponse({"error": str(e), "best3": []})


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
    try:
        nba_games = _cached(f"nba_{today_str}", lambda: _build_nba_games(today_str), ttl=1800) or []
        mlb_games = _cached(f"mlb_{today_str}", lambda: _build_mlb_props(today_str), ttl=1800) or []
        nhl_games = _cached(f"nhl_{today_str}", lambda: _build_nhl_props(today_str), ttl=1800) or []
    except Exception as _e:
        print(f"[TODAY] Error building games: {_e}")
        traceback.print_exc()
        nba_games = mlb_games = nhl_games = []
    total = len(nba_games) + len(mlb_games) + len(nhl_games)
    return TEMPLATES.TemplateResponse(request, "today.html", {
        "nba_games":    nba_games,
        "mlb_games":    mlb_games,
        "nhl_games":    nhl_games,
        "today":        today_label,
        "total":        total,
        "refresh_secs": 300,
        "has_odds_key": bool(os.getenv("ODDS_API_KEY")),
        "generated_at": _now_iso(),
    })


def _build_mlb_props(today_str: str) -> list:
    """Build MLB player props using Odds API as single source of truth."""
    from data.odds_api import build_sport_props
    return _normalize_games(build_sport_props("mlb"))


def _build_nhl_props(today_str: str) -> list:
    """Build NHL player props using Odds API as single source of truth."""
    from data.odds_api import build_sport_props
    return _normalize_games(build_sport_props("nhl"))


# ─────────────────────────────────────────────
#  MLB — live props dashboard
# ─────────────────────────────────────────────

@app.get("/mlb", response_class=HTMLResponse)
async def mlb_page(request: Request):
    today_str   = date.today().strftime("%Y%m%d")
    today_label = date.today().strftime("%A, %B %d %Y")
    games       = _cached(f"mlb_{today_str}", lambda: _build_mlb_props(today_str), ttl=3600)
    return TEMPLATES.TemplateResponse(request, "mlb.html", {
        "games":        games or [],
        "total":        len(games or []),
        "today":        today_label,
        "generated_at": _now_iso(),
    })


# ─────────────────────────────────────────────
#  NHL — live props dashboard
# ─────────────────────────────────────────────

@app.get("/nhl", response_class=HTMLResponse)
async def nhl_page(request: Request):
    today_str   = date.today().strftime("%Y%m%d")
    today_label = date.today().strftime("%A, %B %d %Y")
    games       = _cached(f"nhl_{today_str}", lambda: _build_nhl_props(today_str), ttl=3600)
    return TEMPLATES.TemplateResponse(request, "nhl.html", {
        "games":        games or [],
        "total":        len(games or []),
        "today":        today_label,
        "generated_at": _now_iso(),
    })


@app.get("/api/debug/mlb-markets")
async def debug_mlb_markets():
    """Redirect to generic debug route for backward compatibility."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/api/debug/markets/baseball_mlb")


@app.get("/api/debug/markets/{sport_key}")
async def debug_markets(sport_key: str):
    """Diagnostic: test which player prop markets work for a given sport key.
    Usage: /api/debug/markets/baseball_mlb
           /api/debug/markets/icehockey_nhl
           /api/debug/markets/basketball_nba
    """
    import requests as _req
    from data.odds_api import ODDS_BASE, _key

    api_key = _key()
    if not api_key:
        return {"error": "ODDS_API_KEY not set"}

    # Step 1: get events
    try:
        events_r = _req.get(
            f"{ODDS_BASE}/sports/{sport_key}/events",
            params={"apiKey": api_key, "dateFormat": "iso"},
            timeout=12,
        )
        events = events_r.json() if events_r.status_code == 200 else []
    except Exception as e:
        return {"error": str(e)}

    if not events or not isinstance(events, list):
        return {"sport": sport_key, "events": 0, "error": "No events found"}

    event = events[0]
    event_id = event.get("id", "")
    home = event.get("home_team", "")
    away = event.get("away_team", "")

    # Step 2: get available bookmakers via h2h (always works)
    try:
        base_r = _req.get(
            f"{ODDS_BASE}/sports/{sport_key}/events/{event_id}/odds",
            params={"apiKey": api_key, "regions": "us", "markets": "h2h", "oddsFormat": "american"},
            timeout=10,
        )
        available_bookmakers = [b["key"] for b in base_r.json().get("bookmakers", [])] if base_r.status_code == 200 else []
    except Exception:
        available_bookmakers = []

    # Step 3: markets to test per sport
    markets_to_test = {
        "baseball_mlb": [
            "batter_hits", "batter_total_bases", "batter_home_runs",
            "batter_rbis", "batter_runs_scored", "pitcher_strikeouts",
            "pitcher_innings_pitched", "batter_hits_runs_rbis",
            "batter_doubles", "batter_singles",
        ],
        "icehockey_nhl": [
            "player_points", "player_shots_on_goal", "player_assists",
            "player_anytime_scorer", "player_first_goal_scorer",
            "player_power_play_points", "player_blocked_shots", "player_goals",
        ],
        "basketball_nba": [
            "player_points", "player_rebounds", "player_assists",
            "player_threes", "player_blocks", "player_steals",
            "player_points_rebounds_assists",
        ],
    }.get(sport_key, ["player_points", "player_goals", "player_shots"])

    results = {}
    working = []
    for market in markets_to_test:
        try:
            r = _req.get(
                f"{ODDS_BASE}/sports/{sport_key}/events/{event_id}/odds",
                params={
                    "apiKey": api_key, "regions": "us",
                    "markets": market, "oddsFormat": "american",
                },
                timeout=8,
            )
            if r.status_code == 422:
                results[market] = {"status": 422, "works": False, "reason": "Invalid market for this sport"}
                continue
            if r.status_code == 401:
                return {"error": "Invalid API key"}
            data = r.json()
            bookmakers = data.get("bookmakers", [])
            player_count = 0
            sample_players = []
            for bk in bookmakers[:1]:
                for m in bk.get("markets", []):
                    overs = [o for o in m.get("outcomes", []) if o.get("name") in ["Over", "Yes"]]
                    player_count = len(overs)
                    sample_players = [(o.get("description") or o.get("name", "")) for o in overs[:3]]
            has_data = player_count > 0
            if has_data:
                working.append(market)
            results[market] = {
                "status": r.status_code, "works": has_data,
                "player_count": player_count, "sample_players": sample_players,
                "bookmakers_found": len(bookmakers),
            }
        except Exception as e:
            results[market] = {"error": str(e)}

    return {
        "sport": sport_key,
        "total_events": len(events),
        "sample_game": f"{home} vs {away}",
        "event_id": event_id,
        "available_bookmakers": available_bookmakers,
        "working_markets": working,
        "all_results": results,
    }


@app.get("/api/debug/nba")
async def debug_nba():
    """Diagnostic: returns today's NBA games from ESPN scoreboard."""
    from data.nba_data import get_games as nba_get_games
    today_str = date.today().strftime("%Y%m%d")
    raw_games = nba_get_games(dates=today_str)
    return {
        "today": today_str,
        "game_count": len(raw_games),
        "games": [{"home": g.get("home_team"), "away": g.get("away_team"),
                   "home_abbr": g.get("home_abbr"), "away_abbr": g.get("away_abbr"),
                   "status": g.get("status")} for g in raw_games],
    }


@app.get("/api/debug/espn-schedule")
async def debug_espn_schedule():
    """Diagnostic: show ESPN NBA scoreboard abbreviations for tonight's games."""
    from data.nba_data import get_games as nba_get_games
    today_str = date.today().strftime("%Y%m%d")
    raw_games = nba_get_games(dates=today_str)
    games_out = [{"home_team": g.get("home_team"), "home_abbr": g.get("home_abbr"),
                  "away_team": g.get("away_team"), "away_abbr": g.get("away_abbr"),
                  "status": g.get("status")} for g in raw_games]
    espn_abbrs = sorted({a for g in games_out for a in [g["home_abbr"], g["away_abbr"]] if a})
    return {"today": today_str, "total_games": len(raw_games),
            "espn_abbrs": espn_abbrs, "games": games_out}


@app.get("/api/correct-score")
async def api_correct_score(home_xg: float = 1.5, away_xg: float = 1.2):
    grid = correct_score_grid(home_xg, away_xg, max_goals=5)
    return [{"score": f"{h}-{a}", "prob": round(p * 100, 2)} for h, a, p in grid[:12]]


_NFL_PRESETS = [
    {"name": "James Cook",       "rush_yds": 1621, "rec_yds": 0,    "pass_yds": 0,    "tds": 12, "rush_att": 307, "rec_tgt": 52,  "games": 17},
    {"name": "Jonathan Taylor",  "rush_yds": 1585, "rec_yds": 0,    "pass_yds": 0,    "tds": 20, "rush_att": 271, "rec_tgt": 48,  "games": 16},
    {"name": "Puka Nacua",       "rush_yds": 0,    "rec_yds": 1715, "pass_yds": 0,    "tds": 11, "rush_att": 0,   "rec_tgt": 165, "games": 16},
    {"name": "Trey McBride",     "rush_yds": 0,    "rec_yds": 1260, "pass_yds": 0,    "tds": 8,  "rush_att": 0,   "rec_tgt": 155, "games": 17},
    {"name": "Matthew Stafford", "rush_yds": 0,    "rec_yds": 0,    "pass_yds": 4707, "tds": 46, "rush_att": 0,   "rec_tgt": 0,   "games": 17},
    {"name": "Josh Allen",       "rush_yds": 560,  "rec_yds": 0,    "pass_yds": 4250, "tds": 12, "rush_att": 95,  "rec_tgt": 0,   "games": 17},
    {"name": "Lamar Jackson",    "rush_yds": 780,  "rec_yds": 0,    "pass_yds": 4200, "tds": 5,  "rush_att": 110, "rec_tgt": 0,   "games": 17},
]


# ─────────────────────────────────────────────
#  /api/sanity  — data quality checks
# ─────────────────────────────────────────────

@app.post("/api/parlay/analyze")
async def parlay_analyze(request: Request):
    """AI-powered parlay analysis via Claude. Requires ANTHROPIC_API_KEY env var."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return JSONResponse(
            {"error": "ANTHROPIC_API_KEY is not configured on this server."},
            status_code=503,
        )

    try:
        import anthropic as _anthropic
    except ImportError:
        return JSONResponse(
            {"error": "anthropic SDK not installed. Add 'anthropic' to requirements.txt."},
            status_code=500,
        )

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body."}, status_code=400)

    legs          = body.get("legs", [])
    combined_prob = body.get("combined_prob", 0)
    grade         = body.get("grade", "")
    edge          = body.get("edge", 0)

    if len(legs) < 2:
        return JSONResponse({"error": "Need at least 2 legs to analyze."}, status_code=400)

    legs_text = "\n".join(
        f"- {l.get('player','?')} ({l.get('sport','?')}) "
        f"{l.get('stat','?')} {l.get('pick','OVER')} {l.get('line','?')} "
        f"| Confidence: {round(l.get('confidence', 0.5) * 100)}%"
        + (f" | Trend: {l['trend']}" if l.get("trend") else "")
        + (f" | L5 avg: {l['last5']}"  if l.get("last5") else "")
        for l in legs
    )

    prompt = (
        f"Analyze this sports betting parlay:\n{legs_text}\n"
        f"Combined probability: {combined_prob}%\n"
        f"Model edge: {edge:+.1f}%\n"
        f"Parlay grade: {grade}\n\n"
        "Provide:\n"
        "1. Brief analysis of each leg (1 sentence each)\n"
        "2. Strongest leg and why\n"
        "3. Weakest leg and whether to keep or replace\n"
        "4. Overall parlay assessment\n"
        "5. One alternative leg suggestion if edge is weak\n\n"
        "Keep response under 150 words. Be direct and specific."
    )

    try:
        client  = _anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model      = "claude-haiku-4-5-20251001",
            max_tokens = 350,
            messages   = [{"role": "user", "content": prompt}],
        )
        analysis = message.content[0].text
        return JSONResponse({"analysis": analysis})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/api/sanity")
async def sanity_check():
    """
    Programmatic sanity checks on live data. Call /api/sanity in production
    to verify all data pipelines are returning sane values.
    """
    today_str = date.today().strftime("%Y%m%d")
    failures: list = []
    warnings: list = []
    passed:   list = []

    def _fail(msg: str): failures.append(msg)
    def _warn(msg: str): warnings.append(msg)
    def _ok(msg: str):   passed.append(msg)

    # ── NBA checks ────────────────────────────────────────────────────────────
    try:
        nba_games = _build_nba_games(today_str)
        if not nba_games:
            _warn("NBA: no games today")
        else:
            seen_probs: set = set()
            for g in nba_games:
                for roster in [g.get("home_roster", []), g.get("away_roster", [])]:
                    for player in roster:
                        name = player.get("name", "?")
                        for pr in player.get("props", []):
                            stat = pr.get("stat", "?")
                            op   = pr.get("over_prob", 0)
                            avg  = pr.get("avg", 0)
                            stars = pr.get("stars", 0)
                            if not (30.0 <= op <= 82.1):
                                _fail(f"NBA over_prob out of range: {name} {stat} = {op}%")
                            if avg and avg > 45:
                                _fail(f"NBA avg too high (season total?): {name} {stat} = {avg}")
                            if not (1 <= stars <= 5):
                                _fail(f"NBA stars out of range: {name} {stat} stars={stars}")
                            seen_probs.add(round(op, 1))
            if len(seen_probs) < 3:
                _warn(f"NBA: only {len(seen_probs)} unique over_prob values — may be hardcoded")
            else:
                _ok(f"NBA: {len(seen_probs)} distinct over_prob values")
            _ok(f"NBA: {len(nba_games)} games loaded")
    except Exception as e:
        _fail(f"NBA build crashed: {e}")

    # ── MLB checks ────────────────────────────────────────────────────────────
    try:
        mlb_games = _build_mlb_props(today_str)
        if not mlb_games:
            _warn("MLB: no games today")
        else:
            for g in mlb_games:
                for roster in [g.get("home_roster", []), g.get("away_roster", [])]:
                    for player in roster:
                        name = player.get("name", "?")
                        for pr in player.get("props", []):
                            stat = pr.get("stat", "?")
                            avg  = pr.get("avg", 0)
                            line = pr.get("line", 0)
                            if avg and avg > 30:
                                _fail(f"MLB avg looks like season total: {name} {stat} avg={avg}")
            _ok(f"MLB: {len(mlb_games)} games loaded")
    except Exception as e:
        _fail(f"MLB build crashed: {e}")

    # ── Soccer checks ─────────────────────────────────────────────────────────
    try:
        from data.odds_api import build_soccer_props
        soccer_games = build_soccer_props()
        if not soccer_games:
            _warn("Soccer: no games today")
        else:
            for g in soccer_games:
                h = g.get("home_prob", 0)
                d = g.get("draw_prob") or 0
                a = g.get("away_prob", 0)
                total_prob = h + d + a
                if abs(total_prob - 100.0) > 2.0:
                    _fail(f"Soccer probs don't sum to 100: {g.get('home_team')} vs {g.get('away_team')}: {h}+{d}+{a}={total_prob}")
                for p in g.get("players", []):
                    gp = p.get("goal_scorer_prob") or 0
                    if gp < 0 or gp > 90:
                        _warn(f"Soccer goal_scorer_prob unusual: {p.get('name')} = {gp}%")
            _ok(f"Soccer: {len(soccer_games)} games loaded")
    except Exception as e:
        _fail(f"Soccer build crashed: {e}")

    # ── NHL checks ────────────────────────────────────────────────────────────
    _MMA_STATS = {"rounds", "strikes", "takedowns", "knockdowns", "submission"}
    try:
        nhl_games = _build_nhl_props(today_str)
        if nhl_games:
            for g in nhl_games:
                for roster in [g.get("home_roster", []), g.get("away_roster", [])]:
                    for player in roster:
                        name = player.get("name", "?")
                        for pr in player.get("props", []):
                            stat = (pr.get("stat") or "").lower()
                            if any(mma in stat for mma in _MMA_STATS):
                                _fail(f"NHL: MMA stat detected: {name} {stat}")
            _ok(f"NHL: {len(nhl_games)} games (no MMA detected)")
        else:
            _warn("NHL: no games today (off season or no props)")
    except Exception as e:
        _fail(f"NHL build crashed: {e}")

    return {
        "timestamp": _now_iso(),
        "status": "FAIL" if failures else ("WARN" if warnings else "PASS"),
        "failures": failures,
        "warnings": warnings,
        "passed":   passed,
    }


# ═════════════════════════════════════════════════════════════════════════════
#  PART 7 — MY CARD PAGE
# ═════════════════════════════════════════════════════════════════════════════

@app.get("/mycard", response_class=HTMLResponse)
async def mycard_page(request: Request):
    from data.odds_api import build_sport_props, build_soccer_props, get_mycard_props, get_sharp_props, get_prop_grade, calculate_bet_score
    try:
        all_props_by_sport: dict = {}
        all_mycard_props: list = []
        all_sharp_props: list = []
        sports_to_load = [
            ("nba", "\U0001f3c0", "NBA"),
            ("mlb", "\u26be", "MLB"),
            ("nhl", "\U0001f3d2", "NHL"),
        ]
        today_str = date.today().strftime("%Y%m%d")
        for sport_key, emoji, label in sports_to_load:
            try:
                games = _cached(
                    f"{sport_key}_{today_str}",
                    lambda sk=sport_key: build_sport_props(sk),
                    ttl=1800,
                ) or []
            except Exception as _se:
                print(f"[MYCARD] {sport_key}: {_se}")
                games = []

            # Full pipeline filter
            mycard = get_mycard_props(games, sport_key=sport_key, sport_emoji=emoji, sport_label=label)
            sharp  = get_sharp_props(games, sport_key=sport_key, sport_emoji=emoji, sport_label=label)
            all_mycard_props.extend(mycard)
            all_sharp_props.extend(sharp)

            # For legacy sport panel data, collect all props with grade info
            sport_props: list = []
            for game in games:
                game_label = f"{game.get('away_abbr', '')} @ {game.get('home_abbr', '')}"
                for player in (game.get("home_roster", []) + game.get("away_roster", [])):
                    for prop in player.get("props", []):
                        p = {
                            **prop,
                            "sport_key":   sport_key,
                            "sport_emoji": emoji,
                            "sport_label": label,
                            "player_name": player.get("name", prop.get("player_name", "")),
                            "team":        player.get("team", prop.get("team", "")),
                            "game":        game_label,
                        }
                        if "grade" not in p:
                            grade, edge = get_prop_grade(p.get("confidence", 50), p.get("price", -110))
                            p["grade"] = grade
                            p["edge"]  = edge
                        if "score" not in p:
                            p["score"] = calculate_bet_score(p)
                        if "score_class" not in p:
                            sc = p.get("score", 0)
                            p["score_class"] = "elite" if sc >= 80 else "strong" if sc >= 65 else "playable" if sc >= 50 else "pass"
                        sport_props.append(p)

            all_props_by_sport[sport_key] = {
                "emoji":        emoji,
                "label":        label,
                "all":          sport_props,
                "window":       mycard,
                "window_label": "Top Actionable Picks",
                "sharp":        sharp,
                "a_count":      sum(1 for p in sport_props if p.get("grade") == "A"),
                "b_count":      sum(1 for p in sport_props if p.get("grade") == "B"),
                "total":        len(sport_props),
            }

        # Soccer (unchanged — no get_mycard_props support for soccer)
        try:
            soccer_result = _cached("soccer", build_soccer_props, ttl=1800) or {}
            soccer_games_raw = soccer_result.get("games", []) if isinstance(soccer_result, dict) else []
            soccer_props: list = []
            for league_block in soccer_games_raw:
                for game in league_block.get("games", []) if isinstance(league_block, dict) and "games" in league_block else ([league_block] if isinstance(league_block, dict) else []):
                    home = game.get("home_team", "")
                    away = game.get("away_team", "")
                    hp = game.get("home_prob", 50)
                    ap = game.get("away_prob", 50)
                    if hp >= 60:
                        sp = {
                            "player_name": home, "sport_key": "soccer",
                            "sport_emoji": "⚽", "sport_label": "Soccer",
                            "stat": "match_winner", "label": "Match Winner",
                            "line": 1.0, "pick": "HOME", "confidence": hp,
                            "grade": "A" if hp >= 68 else "B", "edge": round(hp - 50, 1),
                            "score": int(hp), "team": home[:3].upper(),
                            "game": f"{away[:3].upper()} @ {home[:3].upper()}",
                            "stake_rec": "0.5u", "best_book": "", "playable_to": "-150",
                            "score_class": "strong", "reasons": [f"{hp}% win probability"],
                            "red_flags": [], "price": -110, "all_prices": {},
                        }
                        soccer_props.append(sp)
                    elif ap >= 60:
                        sp = {
                            "player_name": away, "sport_key": "soccer",
                            "sport_emoji": "⚽", "sport_label": "Soccer",
                            "stat": "match_winner", "label": "Match Winner",
                            "line": 1.0, "pick": "AWAY", "confidence": ap,
                            "grade": "A" if ap >= 68 else "B", "edge": round(ap - 50, 1),
                            "score": int(ap), "team": away[:3].upper(),
                            "game": f"{away[:3].upper()} @ {home[:3].upper()}",
                            "stake_rec": "0.5u", "best_book": "", "playable_to": "-150",
                            "score_class": "strong", "reasons": [f"{ap}% win probability"],
                            "red_flags": [], "price": -110, "all_prices": {},
                        }
                        soccer_props.append(sp)
            if soccer_props:
                soccer_window = [p for p in soccer_props if p.get("grade") in ("A", "B")][:15]
                all_props_by_sport["soccer"] = {
                    "emoji":        "⚽",
                    "label":        "Soccer",
                    "all":          soccer_props,
                    "window":       soccer_window,
                    "window_label": "Top Actionable Picks",
                    "sharp":        [p for p in soccer_props if p.get("grade") == "A"],
                    "a_count":      sum(1 for p in soccer_props if p.get("grade") == "A"),
                    "b_count":      sum(1 for p in soccer_props if p.get("grade") == "B"),
                    "total":        len(soccer_props),
                }
        except Exception as _soccer_err:
            print(f"[MYCARD] Soccer failed: {_soccer_err}")

        all_mycard_props.sort(key=lambda x: x.get("score", 0), reverse=True)
        all_sharp_props.sort(key=lambda x: x.get("score", 0), reverse=True)

        a_props     = [p for p in all_mycard_props if p.get("grade") == "A"]
        b_props     = [p for p in all_mycard_props if p.get("grade") == "B"]
        watch_props = [
            p for sport_data in all_props_by_sport.values()
            for p in sport_data.get("all", [])
            if p.get("grade") == "Watch"
        ]
        pass_props = [
            p for sport_data in all_props_by_sport.values()
            for p in sport_data.get("all", [])
            if p.get("grade") == "Pass"
        ]

        # best3: top bettable A+B props by score (no specialty)
        best3 = [
            p for p in all_mycard_props
            if p.get("grade") in ("A", "B") and not p.get("is_specialty")
        ][:3]

        # parlay legs: A+B, edge>=5, no specialty, max score
        parlay_legs = sorted(
            [p for p in all_mycard_props if p.get("grade") in ("A", "B") and p.get("edge", 0) >= 5],
            key=lambda x: x.get("score", 0), reverse=True,
        )[:5]

        low_risk_list = sorted(a_props + b_props, key=lambda x: x.get("confidence", 0), reverse=True)
        best_value_list = sorted(a_props + b_props, key=lambda x: x.get("edge", 0), reverse=True)

        return TEMPLATES.TemplateResponse(request, "mycard.html", {
            "sports":         all_props_by_sport,
            "best3":          best3,
            "sharp_picks":    all_sharp_props[:10],
            "low_risk":       low_risk_list[0] if low_risk_list else None,
            "best_value":     best_value_list[0] if best_value_list else None,
            "parlay_legs":    parlay_legs,
            "watchlist":      sorted(watch_props, key=lambda x: x.get("confidence", 0), reverse=True)[:8],
            "passlist":       sorted(pass_props, key=lambda x: x.get("confidence", 0), reverse=True)[:10],
            "a_count":        len(a_props),
            "b_count":        len(b_props),
            "watch_count":    len(watch_props),
            "pass_count":     len(pass_props),
            "total_eligible": len(a_props) + len(b_props),
            "generated_at":   _now_iso(),
        })
    except Exception as e:
        print(f"[MYCARD] Error: {e}")
        traceback.print_exc()
        return TEMPLATES.TemplateResponse(request, "mycard.html", {
            "sports": {}, "best3": [], "sharp_picks": [], "low_risk": None, "best_value": None,
            "parlay_legs": [], "watchlist": [], "passlist": [],
            "a_count": 0, "b_count": 0, "watch_count": 0, "pass_count": 0,
            "total_eligible": 0, "generated_at": _now_iso(), "error": str(e),
        })


# ═════════════════════════════════════════════════════════════════════════════
#  PART 9 — CALIBRATION API ENDPOINTS
# ═════════════════════════════════════════════════════════════════════════════

@app.get("/api/calibration")
async def get_calibration_data():
    """View current calibration state."""
    from data.odds_api import load_calibration
    cal = load_calibration()
    summary: dict = {}
    for sport, stats in cal.items():
        summary[sport] = {}
        for stat, data in stats.items():
            total = data.get("total_bets", 0)
            hits  = data.get("total_hits", 0)
            summary[sport][stat] = {
                "total_bets": total,
                "hit_rate": round(hits / total * 100, 1) if total > 0 else None,
                "calibration_factor": data.get("calibration_factor", 1.0),
                "min_bets_needed": max(0, 20 - total),
            }
    return summary


@app.post("/api/bet/result")
async def update_bet_result(request: Request):
    """Record a bet result for model calibration."""
    from data.odds_api import record_bet_result, get_calibration_factor
    data = await request.json()
    sport      = data.get("sport")
    stat       = data.get("stat")
    confidence = data.get("confidence")
    result     = data.get("result")
    if not all([sport, stat, confidence, result]):
        return JSONResponse({"error": "Missing fields"}, status_code=400)
    hit = result == "win"
    record_bet_result(sport, stat, float(confidence), hit)
    return {
        "status": "recorded",
        "new_factor": get_calibration_factor(sport, stat),
    }
