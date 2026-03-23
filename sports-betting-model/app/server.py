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
from concurrent.futures import ThreadPoolExecutor, as_completed
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

app = FastAPI(title="Sports Betting Model", version="1.0")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.on_event("startup")
async def _startup_preload():
    """Warm the cache in background so the first real request is fast."""
    def _preload():
        today_str = date.today().strftime("%Y%m%d")
        print("[STARTUP] Preloading NBA / MLB / NHL in background… (ESPN data source)")
        try:
            with ThreadPoolExecutor(max_workers=3) as ex:
                futs = {
                    ex.submit(_build_nba_games, today_str): f"nba_{today_str}",
                    ex.submit(_build_mlb_props, today_str): f"mlb_{today_str}",
                    ex.submit(_build_nhl_props, today_str): f"nhl_{today_str}",
                }
                for fut in as_completed(futs):
                    key = futs[fut]
                    try:
                        data = fut.result()
                        with _CACHE_LOCK:
                            _PAGE_CACHE[key] = (data, _time.time() + 600)
                        print(f"[STARTUP] {key} preloaded OK")
                    except Exception as _e:
                        print(f"[STARTUP] {key} preload failed: {_e}")
        except Exception as _e:
            print(f"[STARTUP] Preload error: {_e}")
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

def _build_nba_games(today_str: str) -> list:
    from data.nba_data   import get_games as nba_get_games, get_nba_win_pcts, \
                                fetch_all_player_stats
    from data.odds_api   import build_odds_lookup, find_game_odds, decimal_to_american
    from data.prizepicks import get_nba_projections
    from utils.stats     import confidence_label, shot_attempt_over_under, threept_made_ou

    # ── PrizePicks line lookup: real market lines per (player_name, stat_key) ─
    _PP_STAT_KEY = {
        "points":   "pts", "rebounds": "reb", "assists": "ast",
        "3-point":  "3pm", "threes":   "3pm", "three":   "3pm", "made": "3pm",
        "steals":   "stl", "blocks":   "blk",
    }
    _PP_SKIP_FRAGMENTS = ("pra", "pts+", "reb+", "ast+", "+reb", "+ast",
                          "points+", "fantasy", "score", "combo")

    pp_lines: dict[tuple, float] = {}
    # team_abbr_upper → {name_lower → {name, pos}} for ESPN batch fetch
    pp_by_team: dict[str, dict] = {}
    try:
        for proj in get_nba_projections():
            stat_raw = (proj.get("stat") or "").lower()
            if any(frag in stat_raw for frag in _PP_SKIP_FRAGMENTS) or "+" in stat_raw:
                continue
            sk = next((v for k, v in _PP_STAT_KEY.items() if k in stat_raw), None)
            if not sk:
                continue
            name_raw  = (proj.get("name") or "").strip()
            name_key  = name_raw.lower()
            line_val  = float(proj.get("line") or 0)
            team_abbr = (proj.get("team") or "").upper().strip()
            if line_val > 0:
                pp_lines[(name_key, sk)] = line_val
                if team_abbr:
                    if team_abbr not in pp_by_team:
                        pp_by_team[team_abbr] = {}
                    if name_key not in pp_by_team[team_abbr]:
                        pp_by_team[team_abbr][name_key] = {
                            "name": name_raw,
                            "pos":  (proj.get("pos") or ""),
                        }
        print(f"[NBA] PrizePicks lines: {len(pp_lines)} entries, {len(pp_by_team)} teams")
    except Exception as _ppe:
        print(f"[NBA] PrizePicks line fetch failed: {_ppe}")

    # NBA per-game stat caps to reject ESPN fantasy composites / season totals
    _STAT_CAPS = {"pts": 45.0, "reb": 20.0, "ast": 15.0, "fg3m": 7.0}

    # Stat key → field name in ESPN game-log dicts (same names used by get_espn_player_logs)
    _STAT_FIELD = {"pts": "pts", "reb": "reb", "ast": "ast", "3pm": "fg3m",
                   "stl": "stl", "blk": "blk"}

    def _make_prop(stat_label: str, avg: float, std, player_name: str, stat_key: str,
                   player_id=None, game_logs=None):
        """
        Build one prop dict using:
          1. Real ESPN season avg for normal-distribution base
          2. PrizePicks line as market line (if available)
          3. Hit-rate from last 5 / last 10 ESPN game logs
          Weighted formula: 35% L5 hit-rate + 35% L10 hit-rate + 30% season-avg model
          Clamped [0.30, 0.82] so result always varies by player.
        """
        if avg < 0.5:
            return None
        pp_line = pp_lines.get((player_name.lower().strip(), stat_key))
        line    = pp_line if (pp_line and pp_line > 0) else max(0.5, round(avg * 2) / 2 - 0.5)

        # Season-avg model probability
        if std is None:
            season_over, _ = threept_made_ou(avg, line)
        else:
            season_over, _ = shot_attempt_over_under(avg, line, std_factor=std)

        # Game-log hit rates (ESPN game-log dicts use same field names as BDL)
        stat_field   = _STAT_FIELD.get(stat_key, stat_key)
        player_logs  = (game_logs or {}).get(player_id, []) if player_id else []
        last10_vals  = [float(g.get(stat_field) or 0) for g in player_logs[:10]]
        last5_vals   = last10_vals[:5]
        last5_avg    = round(sum(last5_vals)  / len(last5_vals),  1) if last5_vals  else None
        last10_avg   = round(sum(last10_vals) / len(last10_vals), 1) if last10_vals else None

        if len(last10_vals) >= 5:
            l10_hit = sum(1 for v in last10_vals if v > line) / len(last10_vals)
            l5_hit  = sum(1 for v in last5_vals  if v > line) / len(last5_vals)
            hit_over_last5 = sum(1 for v in last5_vals if v > line)
            over_p  = max(0.30, min(0.82,
                          0.35 * l5_hit + 0.35 * l10_hit + 0.30 * season_over))
        else:
            hit_over_last5 = None
            over_p  = max(0.30, min(0.82, season_over))

        under_p = 1.0 - over_p
        pick    = "OVER" if over_p > 0.55 else ("UNDER" if over_p < 0.45 else "FAIR")
        best    = max(over_p, under_p)

        # Trend arrow: compare last-5 avg vs last-10 avg
        trend = "→"
        if last5_avg is not None and last10_avg is not None and last10_avg > 0:
            if last5_avg > last10_avg * 1.04:
                trend = "↑"
            elif last5_avg < last10_avg * 0.96:
                trend = "↓"

        return {
            "stat": stat_label, "avg": avg, "line": line,
            "over_prob":  round(over_p  * 100, 1),
            "under_prob": round(under_p * 100, 1),
            "pick": pick,
            "confidence": confidence_label(best),
            "stars": (5 if best >= 0.85 else 4 if best >= 0.75 else
                      3 if best >= 0.65 else 2 if best >= 0.55 else 1),
            "last5_avg":      last5_avg,
            "last10_avg":     last10_avg,
            "hit_over_last5": hit_over_last5,
            "trend":          trend,
        }

    # Hard per-game caps — values above these are season totals or fantasy composites
    _PG_MAX = {"pts": 45.0, "reb": 20.0, "ast": 15.0, "3pm": 7.0}

    def _nba_props_from_avgs(players: list, game_logs: dict = None) -> list:
        """Build props from player dicts with real BDL per-game averages."""
        out = []
        for p in players:
            name      = p.get("name", "")
            player_id = p.get("player_id")
            props     = []
            for stat_label, avg_key, sk, std in [
                ("PTS", "pts",  "pts",  0.28),
                ("REB", "reb",  "reb",  0.32),
                ("AST", "ast",  "ast",  0.35),
                ("3PM", "fg3m", "3pm",  None),
            ]:
                avg_val = float(p.get(avg_key) or p.get(sk) or 0)
                # Reject impossible per-game values (season totals / fantasy composites)
                if avg_val > _PG_MAX.get(sk, 45.0):
                    avg_val = 0.0
                prop    = _make_prop(stat_label, avg_val, std, name, sk,
                                     player_id=player_id, game_logs=game_logs)
                if prop:
                    props.append(prop)
            if props:
                out.append({**p, "name": name, "pos": p.get("pos", ""),
                            "pts": p.get("pts", 0), "props": props})
        return out

    def _nba_roster_from_leaders(leaders: list, team_id: str) -> list:
        """Fallback: build roster from ESPN competition leaders."""
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
                    continue
                player_map.setdefault(name, {"name": name, "pos": "",
                                             "pts": 0.0, "reb": 0.0,
                                             "ast": 0.0, "fg3m": 0.0})
                player_map[name][sk] = val
            return _nba_props_from_avgs(list(player_map.values()))

        team_ldrs = [l for l in leaders if not l.get("team_id") or l.get("team_id") == team_id]
        result    = _build(team_ldrs)
        return result if result else _build(leaders)

    games = []
    try:
        nba_book_lookup  = build_odds_lookup("NBA")
        nba_standings    = get_nba_win_pcts()  # {team_name_lower: win_pct} — 6h cache

        raw_games = nba_get_games(dates=today_str)

        # ── ESPN batch fetch for all PrizePicks players ───────────────────────
        # Collect unique player names from PP by team so we can look up real
        # season averages + game logs from ESPN (no rate limit, no API key).
        all_pp_player_names = list({
            data["name"]
            for team_data in pp_by_team.values()
            for data in team_data.values()
            if data.get("name")
        })
        espn_player_data: dict = {}
        if all_pp_player_names:
            try:
                espn_player_data = fetch_all_player_stats(all_pp_player_names)
                print(f"[NBA] ESPN stats: {len(espn_player_data)}/{len(all_pp_player_names)} players found")
            except Exception as _espn_e:
                print(f"[NBA] ESPN batch fetch failed: {_espn_e}")

        # Game logs: {athlete_id: [{pts, reb, ast, fg3m}, ...]}
        espn_game_logs: dict = {
            data["athlete_id"]: data.get("game_logs", [])
            for data in espn_player_data.values()
            if data.get("athlete_id")
        }

        def _nba_props_from_pp_espn(abbr: str) -> list:
            """Build NBA roster from PrizePicks players enriched with ESPN season stats."""
            team_players = pp_by_team.get(abbr.upper() if abbr else "", {})
            if not team_players:
                return []
            enriched = []
            for name_lower, pp_data in team_players.items():
                espn_data = espn_player_data.get(name_lower, {})
                enriched.append({
                    "name":      pp_data.get("name", name_lower),
                    "pos":       pp_data.get("pos", ""),
                    "pts":       float(espn_data.get("pts", 0.0)),
                    "reb":       float(espn_data.get("reb", 0.0)),
                    "ast":       float(espn_data.get("ast", 0.0)),
                    "fg3m":      float(espn_data.get("fg3m", 0.0)),
                    "player_id": espn_data.get("athlete_id"),  # ESPN athlete_id
                })
            return _nba_props_from_avgs(enriched, espn_game_logs)

        for g in raw_games:
            home_team = g.get("home_team") or "TBD"
            away_team = g.get("away_team") or "TBD"
            home_id   = str(g.get("home_id", ""))
            away_id   = str(g.get("away_id", ""))
            home_abbr = g.get("home_abbr", "")
            away_abbr = g.get("away_abbr", "")

            # Win probability: 1) moneyline  2) ESPN standings  3) scoreboard records  4) 55%
            if g.get("home_ml"):
                h_prob = _ml_to_prob(g.get("home_ml"))
            else:
                h_wpct = (nba_standings.get(home_team.lower())
                          or nba_standings.get(home_abbr.lower()))
                a_wpct = (nba_standings.get(away_team.lower())
                          or nba_standings.get(away_abbr.lower()))

                if h_wpct is not None and a_wpct is not None and h_wpct + a_wpct > 0:
                    raw = h_wpct / (h_wpct + a_wpct)
                    h_prob = max(0.25, min(0.75, raw * 0.97 + 0.03))  # +3% HCA
                else:
                    hw = g.get("home_wins", 0) or 0
                    hl = g.get("home_losses", 0) or 0
                    aw = g.get("away_wins", 0) or 0
                    al = g.get("away_losses", 0) or 0
                    if hw + hl > 0 and aw + al > 0:
                        hr = (hw / (hw + hl)) * 0.97 + 0.03
                        ar = aw / (aw + al)
                        h_prob = max(0.25, min(0.75, hr / (hr + ar)))
                    else:
                        h_prob = 0.55
            a_prob = 1 - h_prob
            predicted_winner = home_team if h_prob >= a_prob else away_team
            win_prob = h_prob if h_prob >= a_prob else a_prob

            book    = find_game_odds(nba_book_lookup, home_team, away_team) or {}
            leaders = g.get("leaders", [])
            print(f"[NBA] {home_team} vs {away_team} — ESPN leaders: {len(leaders)}")

            # Primary: PP players with real ESPN season stats + game logs
            home_roster = _nba_props_from_pp_espn(home_abbr)
            away_roster = _nba_props_from_pp_espn(away_abbr)
            print(f"[NBA] ESPN home={len(home_roster)} away={len(away_roster)}")

            # Fallback: ESPN competition leaders (already embedded in scoreboard)
            if not home_roster:
                home_roster = _nba_roster_from_leaders(leaders, home_id)
            if not away_roster:
                away_roster = _nba_roster_from_leaders(leaders, away_id)

            # Last resort: presets split evenly to avoid duplicates
            if not home_roster and not away_roster:
                print(f"[NBA] No player data — using split presets")
                all_presets = _nba_props_from_avgs(_NBA_PRESETS)
                mid         = max(1, len(all_presets) // 2)
                home_roster = all_presets[:mid]
                away_roster = all_presets[mid:]

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
    today_str   = date.today().strftime("%Y%m%d")
    today_label = date.today().strftime("%A, %B %d %Y")
    games       = _build_soccer_games(today_str)
    return TEMPLATES.TemplateResponse(request, "soccer.html", {
        "games": games, "today": today_label,
        "total": len(games), "has_odds_key": bool(os.getenv("ODDS_API_KEY")),
        "generated_at": _now_iso(),
    })


# ─────────────────────────────────────────────
#  NBA — live games dashboard
# ─────────────────────────────────────────────

@app.get("/nba", response_class=HTMLResponse)
async def nba_page(request: Request):
    today_str   = date.today().strftime("%Y%m%d")
    today_label = date.today().strftime("%A, %B %d %Y")
    games       = _cached(f"nba_{today_str}", lambda: _build_nba_games(today_str))
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
        "generated_at": _now_iso(),
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
    Build MLB player prop cards.
    Primary: PrizePicks market lines + MLB Stats API per-game season averages.
    Falls back to line-as-median (50/50) when no MLB Stats data found.
    """
    from data.mlb_data    import get_games as mlb_get_games, search_mlb_player, \
                                   get_pitcher_season_stats, get_batter_season_stats
    from data.prizepicks  import get_mlb_projections
    from models.mlb_model import build_pitcher_props, build_batter_props

    all_props: list = []
    try:
        games = mlb_get_games(dates=today_str)
        total_games = len(games)
        print(f"[MLB] {total_games} games today")

        pp_projections: list = []
        try:
            pp_projections = get_mlb_projections()
            print(f"[MLB] PrizePicks: {len(pp_projections)} projections")
        except Exception as _ppe:
            print(f"[MLB] PrizePicks fetch failed: {_ppe}")

        if not pp_projections:
            return all_props, total_games

        # Map PP stat names → model stat keys
        _MLB_STAT_KEY = {
            "strikeout": "so",     "inning":      "ip",
            "hit":       "hits",   "total base":  "tb",
            "home run":  "hr",     "rbi":         "rbi",
            "run":       "runs",   "walk":        "bb",
            "earned run": "er",
        }

        # Group PP projections by player name
        player_projs: dict[str, list] = {}
        for proj in pp_projections:
            n = (proj.get("name") or "").strip()
            if n:
                player_projs.setdefault(n, []).append(proj)

        for player_name, projs in player_projs.items():
            stat_types = [(p.get("stat") or "").lower() for p in projs]
            is_pitcher = any(
                any(k in s for k in ("strikeout", "inning", "earned run"))
                for s in stat_types
            )

            # Fetch real per-game season averages from MLB Stats API
            season_stats: dict = {}
            pos = projs[0].get("pos", "")
            mlb_player = search_mlb_player(player_name)
            if mlb_player and mlb_player.get("id"):
                mlb_id = mlb_player["id"]
                pos = mlb_player.get("pos", pos)
                if is_pitcher or pos in ("SP", "RP", "P"):
                    season_stats = get_pitcher_season_stats(mlb_id) or {}
                    is_pitcher = True
                else:
                    season_stats = get_batter_season_stats(mlb_id) or {}

            # Collect PP line overrides for this player
            pp_lines: dict[str, float] = {}
            for proj in projs:
                stat_raw = (proj.get("stat") or "").lower()
                sk = next((v for k, v in _MLB_STAT_KEY.items() if k in stat_raw), None)
                if sk:
                    line_val = float(proj.get("line") or 0)
                    if line_val > 0:
                        pp_lines[sk + "_line"] = line_val

            if is_pitcher:
                # Use MLB Stats API per-game avg; fall back to PP line as median estimate
                so_pg = season_stats.get("so_pg") or pp_lines.get("so_line") or 0
                ip_pg = season_stats.get("ip_pg") or pp_lines.get("ip_line") or 0
                pstats = {"so_pg": so_pg, "ip_pg": ip_pg, **pp_lines}
                props = build_pitcher_props(pstats)
            else:
                hits_pg = season_stats.get("hits_pg") or pp_lines.get("hits_line") or 0
                tb_pg   = season_stats.get("tb_pg")   or pp_lines.get("tb_line")   or 0
                hr_pg   = season_stats.get("hr_pg",  0) or pp_lines.get("hr_line",  0) or 0
                rbi_pg  = season_stats.get("rbi_pg", 0) or pp_lines.get("rbi_line", 0) or 0
                runs_pg = season_stats.get("runs_pg", 0.6) or 0.6
                bstats  = {
                    "hits_pg": hits_pg, "tb_pg": tb_pg, "hr_pg": hr_pg,
                    "rbi_pg":  rbi_pg,  "runs_pg": runs_pg, **pp_lines,
                }
                props = build_batter_props(bstats)

            # Flatten: emit one entry per prop so the template renders each as
            # a separate card with the correct data-stat for filter tabs.
            if props:
                player_meta = {
                    "name":     player_name,
                    "pos":      pos,
                    "team":     projs[0].get("team", ""),
                    "opponent": "",
                }
                for prop in props:
                    all_props.append({**player_meta, **prop})
            if len(all_props) >= 120:  # cap (was 60 but now flat so ~2× entries)
                break

    except Exception:
        traceback.print_exc()
        total_games = 0

    return all_props, total_games


def _build_nhl_props(today_str: str) -> tuple[list, int]:
    """
    Build NHL player prop cards.
    PrizePicks lines + NHL API real averages; rejects MMA data.
    """
    from data.nhl_data    import get_games as nhl_get_games, get_team_roster_stats
    from data.prizepicks  import get_nhl_projections
    from models.nhl_model import (build_skater_props, build_goalie_props,
                                  build_props_from_prizepicks, _MMA_REJECT_STATS)
    from utils.stats      import confidence_label, shot_attempt_over_under

    all_props: list = []
    total_games = 0
    try:
        games = nhl_get_games(dates=today_str)
        total_games = len(games)
        print(f"[NHL] {total_games} games today")

        pp_projs = get_nhl_projections()
        # Filter out MMA stats immediately
        pp_projs = [p for p in pp_projs
                    if not any(m in (p.get("stat") or "").lower() for m in _MMA_REJECT_STATS)]
        print(f"[NHL] PrizePicks (after MMA filter): {len(pp_projs)} projections")

        # Build a {name_lower: {goals_pg, assists_pg, pts_pg, shots_pg, saves_pg}}
        # map from NHL roster API so build_props_from_prizepicks can use real
        # per-game averages instead of position priors.
        nhl_player_stats: dict = {}
        seen_teams: set = set()
        for g in games:
            for abbr in [g.get("home_abbr", ""), g.get("away_abbr", "")]:
                if not abbr or abbr in seen_teams:
                    continue
                seen_teams.add(abbr)
                try:
                    roster = get_team_roster_stats(abbr)
                    for p in roster:
                        pname = p.get("name", "")
                        if pname:
                            nhl_player_stats[pname.lower()] = p
                except Exception:
                    pass
        print(f"[NHL] NHL roster stats: {len(nhl_player_stats)} players")

        if pp_projs:
            # Pass real per-game averages so Poisson uses actual goal rates
            all_props = build_props_from_prizepicks(pp_projs, nhl_player_stats)
        else:
            # Fall back: build directly from NHL roster stats
            seen_players: set = set()
            for g in games:
                for abbr in [g.get("home_abbr", ""), g.get("away_abbr", "")]:
                    if not abbr:
                        continue
                    try:
                        players = get_team_roster_stats(abbr)
                    except Exception:
                        players = []
                    for p in players[:12]:
                        pname = p.get("name", "")
                        if pname in seen_players:
                            continue
                        seen_players.add(pname)
                        if p.get("is_goalie"):
                            props = build_goalie_props(p)
                        else:
                            props = build_skater_props(p)
                        player_meta = {
                            "name":      pname,
                            "team":      abbr,
                            "pos":       p.get("pos", ""),
                            "is_goalie": p.get("is_goalie", False),
                            "gp":        p.get("gp", 0),
                            "sv_pct":    p.get("sv_pct", 0),
                            "gaa":       p.get("gaa", 0),
                        }
                        for prop in props:
                            all_props.append({**player_meta, **prop})
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
    result      = _cached(f"mlb_{today_str}", lambda: _build_mlb_props(today_str))
    all_props, total_games = result if result else ([], 0)
    return TEMPLATES.TemplateResponse(request, "mlb.html", {
        "all_props":     all_props,
        "total_players": len(all_props),
        "total_games":   total_games,
        "today":         today_label,
        "generated_at":  _now_iso(),
    })


# ─────────────────────────────────────────────
#  NHL — live props dashboard
# ─────────────────────────────────────────────

@app.get("/nhl", response_class=HTMLResponse)
async def nhl_page(request: Request):
    today_str   = date.today().strftime("%Y%m%d")
    today_label = date.today().strftime("%A, %B %d %Y")
    result      = _cached(f"nhl_{today_str}", lambda: _build_nhl_props(today_str))
    all_props, total_games = result if result else ([], 0)
    return TEMPLATES.TemplateResponse(request, "nhl.html", {
        "all_props":     all_props,
        "total_players": len(all_props),
        "total_games":   total_games,
        "today":         today_label,
        "generated_at":  _now_iso(),
    })


@app.get("/api/debug/nba")
async def debug_nba():
    """Diagnostic: returns ESPN scoreboard leaders + ESPN athlete search test."""
    from data.nba_data import get_games as nba_get_games, get_espn_athlete_id, get_espn_player_stats
    from datetime import date
    today_str = date.today().strftime("%Y%m%d")
    raw_games = nba_get_games(dates=today_str)
    out = []
    for g in raw_games:
        home_abbr = g.get("home_abbr", "")
        away_abbr = g.get("away_abbr", "")
        leaders   = g.get("leaders", [])
        # Test ESPN athlete lookup for a known player (first leader found)
        test_player = next((l["name"] for l in leaders if l.get("name")), None)
        espn_test = {}
        if test_player:
            try:
                aid = get_espn_athlete_id(test_player)
                stats = get_espn_player_stats(aid) if aid else {}
                espn_test = {"player": test_player, "athlete_id": aid, "stats": stats}
            except Exception as _e:
                espn_test = {"player": test_player, "error": str(_e)}
        out.append({
            "game":               f"{g.get('home_team')} vs {g.get('away_team')}",
            "home_id":            g.get("home_id"),
            "away_id":            g.get("away_id"),
            "home_abbr":          home_abbr,
            "away_abbr":          away_abbr,
            "espn_leaders_count": len(leaders),
            "espn_leaders":       leaders[:4],
            "espn_player_test":   espn_test,
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
        mlb_props, _ = _build_mlb_props(today_str)
        if not mlb_props:
            _warn("MLB: no props today")
        else:
            for player in mlb_props:
                name = player.get("name", "?")
                for pr in player.get("props", []):
                    stat  = pr.get("stat", "?")
                    avg   = pr.get("avg", 0)
                    op    = pr.get("over_prob", 0)
                    line  = pr.get("line", 0)
                    if avg and avg > 0 and abs(avg - line) < 0.001:
                        _warn(f"MLB avg == line (may be fallback): {name} {stat} avg={avg} line={line}")
                    if avg and avg > 30:
                        _fail(f"MLB avg looks like season total: {name} {stat} avg={avg}")
            _ok(f"MLB: {len(mlb_props)} props loaded")
    except Exception as e:
        _fail(f"MLB build crashed: {e}")

    # ── Soccer checks ─────────────────────────────────────────────────────────
    try:
        soccer_games = _build_soccer_games(today_str)
        if not soccer_games:
            _warn("Soccer: no games today")
        else:
            for g in soccer_games:
                h = g.get("home_prob", 0)
                d = g.get("draw_prob") or 0
                a = g.get("away_prob", 0)
                total_prob = h + d + a
                if abs(total_prob - 100.0) > 1.5:
                    _fail(f"Soccer probs don't sum to 100: {g.get('home_team')} vs {g.get('away_team')}: {h}+{d}+{a}={total_prob}")
                for roster in [g.get("home_roster", []), g.get("away_roster", [])]:
                    for scorer in roster:
                        sname = scorer.get("name", "")
                        if "Lead Striker" in sname or "estimate" in sname.lower():
                            _fail(f"Soccer: fake placeholder name: {sname}")
                        gp = scorer.get("goal_prob", 0)
                        if not (0 < gp < 75):
                            _warn(f"Soccer goal_prob unusual: {sname} = {gp}%")
                        sp = scorer.get("shots_pg", 0)
                        try:
                            if not (1.4 <= float(sp) <= 9.0):
                                _warn(f"Soccer shots_pg unusual: {sname} = {sp}")
                        except (TypeError, ValueError):
                            pass
            _ok(f"Soccer: {len(soccer_games)} games loaded")
    except Exception as e:
        _fail(f"Soccer build crashed: {e}")

    # ── NHL checks ────────────────────────────────────────────────────────────
    _MMA_STATS = {"rounds", "strikes", "takedowns", "knockdowns", "submission"}
    try:
        nhl_props, _ = _build_nhl_props(today_str)
        for player in nhl_props:
            name = player.get("name", "?")
            for pr in player.get("props", []):
                stat = (pr.get("stat") or "").lower()
                if any(mma in stat for mma in _MMA_STATS):
                    _fail(f"NHL: MMA stat detected: {name} {stat}")
        if nhl_props:
            _ok(f"NHL: {len(nhl_props)} props (no MMA detected)")
        else:
            _warn("NHL: no props today (off season or no PP data)")
    except Exception as e:
        _fail(f"NHL build crashed: {e}")

    return {
        "timestamp": _now_iso(),
        "status": "FAIL" if failures else ("WARN" if warnings else "PASS"),
        "failures": failures,
        "warnings": warnings,
        "passed":   passed,
    }
