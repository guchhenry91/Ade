"""
The Odds API — sportsbook game odds from FanDuel, DraftKings, Bet365, etc.

Free tier (500 req/month): h2h, spreads, totals.
Player props require a paid plan.

Set ODDS_API_KEY environment variable to enable.
Without the key every function returns empty gracefully.
"""
from __future__ import annotations
import os
import re
import time
import logging
import threading
from typing import Dict, List, Optional, Tuple

from data.fetcher import fetch

logger = logging.getLogger(__name__)

ODDS_BASE = "https://api.the-odds-api.com/v4"

SPORT_KEYS: Dict[str, str] = {
    "NBA":  "basketball_nba",
    "MLB":  "baseball_mlb",
    "NHL":  "icehockey_nhl",
    "NFL":  "americanfootball_nfl",
    "EPL":  "soccer_epl",
    "UCL":  "soccer_uefa_champs_league",
    "LIGA": "soccer_spain_la_liga",
    "L1":   "soccer_france_ligue_one",
}

# Player props market keys per sport
NBA_PROP_MARKETS  = "player_points,player_rebounds,player_assists,player_threes"
MLB_PROP_MARKETS  = ("batter_hits,batter_total_bases,batter_home_runs,"
                     "batter_rbis,pitcher_strikeouts")
NHL_PROP_MARKETS  = ("player_points,player_shots_on_goal,"
                     "player_goals,player_assists")

# Odds API market_key → our internal stat label (for lookup key construction)
MARKET_STAT_MAP: Dict[str, str] = {
    # NBA
    "player_points":         "PTS",
    "player_rebounds":       "REB",
    "player_assists":        "AST",
    "player_threes":         "3PM",
    # MLB
    "batter_hits":           "hits",
    "batter_total_bases":    "total_bases",
    "batter_home_runs":      "home_runs",
    "batter_rbis":           "rbi",
    "pitcher_strikeouts":    "strikeouts",
    # NHL
    "player_goals":          "goals",
    "player_assists":        "assists",
    "player_shots_on_goal":  "shots",
}

_PROPS_CACHE: Dict[str, Tuple[Dict, float]] = {}
_PROPS_LOCK = threading.Lock()

# Preferred bookmakers in display priority order
BOOKMAKER_PRIORITY = [
    "fanduel", "draftkings", "betmgm", "bet365",
    "betrivers", "williamhill_us", "bovada",
]


def _key() -> str:
    return os.getenv("ODDS_API_KEY", "")


def fetch_player_props(sport: str, markets: str,
                       bookmakers: str = "draftkings,fanduel",
                       ttl: int = 900) -> Dict[str, float]:
    """Fetch all player prop lines for a sport via The Odds API.

    Returns {f"{player_name}_{market_key}": over_line} dict.
    Uses sport-level event list then per-event odds — requires a premium key.
    Results cached for ttl seconds (default 15 min).

    sport   : short key e.g. "NBA", "MLB", "NHL"
    markets : comma-separated Odds API market keys
    """
    sport_key = SPORT_KEYS.get(sport.upper())
    if not sport_key:
        return {}

    cache_key = f"props_{sport_key}_{markets}"
    now = time.time()
    with _PROPS_LOCK:
        cached = _PROPS_CACHE.get(cache_key)
        if cached:
            data, expires = cached
            if now < expires:
                return data

    api_key = _key()
    if not api_key:
        return {}

    # Step 1: get today's events
    events = fetch(
        f"{ODDS_BASE}/sports/{sport_key}/events",
        params={"apiKey": api_key, "dateFormat": "iso"},
        use_cache=False,
        timeout=12,
    )
    if not events or not isinstance(events, list):
        logger.warning("[ODDS] fetch_player_props: no events for %s", sport_key)
        with _PROPS_LOCK:
            _PROPS_CACHE[cache_key] = ({}, now + 300)
        return {}

    all_lines: Dict[str, float] = {}

    # Step 2: per-event player props (cap at 15 events to limit API usage)
    for event in events[:15]:
        event_id = event.get("id")
        if not event_id:
            continue
        props_data = fetch(
            f"{ODDS_BASE}/sports/{sport_key}/events/{event_id}/odds",
            params={
                "apiKey":      api_key,
                "regions":     "us",
                "markets":     markets,
                "bookmakers":  bookmakers,
                "oddsFormat":  "american",
            },
            use_cache=False,
            timeout=10,
        )
        if not props_data or not isinstance(props_data, dict):
            continue
        for bookmaker in props_data.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                market_key = market.get("key", "")
                stat_label = MARKET_STAT_MAP.get(market_key, market_key)
                for outcome in market.get("outcomes", []):
                    if outcome.get("name") != "Over":
                        continue
                    player = outcome.get("description", "")
                    line   = outcome.get("point")
                    if player and line is not None:
                        # Store under both market_key and stat_label for flexible lookup
                        key_mk = f"{player}_{market_key}"
                        key_sl = f"{player}_{stat_label}"
                        if key_mk not in all_lines:
                            all_lines[key_mk] = float(line)
                        if key_sl not in all_lines:
                            all_lines[key_sl] = float(line)

    logger.info("[ODDS] %s player props: %d lines", sport_key, len(all_lines) // 2)
    print(f"[ODDS] {sport.upper()} prop lines: {len(all_lines) // 2}")

    with _PROPS_LOCK:
        _PROPS_CACHE[cache_key] = (all_lines, now + ttl)
    return all_lines


# ── build_sport_props — Odds API as single source of truth ────────────────────

import math
import json as _json
import requests as _requests
from collections import defaultdict
from datetime import datetime as _dt

# ════════════════════════════════════════════════════════════════════════════════
# PART 1 — COMPLETE SPORT CONFIG (ALL MARKETS)
# ════════════════════════════════════════════════════════════════════════════════

_SPORT_CONFIG: Dict[str, Dict] = {
    "nba": {
        "sport_key": "basketball_nba",
        "sport_label": "Basketball",
        "league": "NBA",
        "icon": "\U0001f3c0",
        "espn_path": "basketball/nba",
        "primary_stat": "PTS",
        "markets": [
            "player_points", "player_rebounds", "player_assists",
            "player_threes", "player_blocks", "player_steals",
            "player_points_rebounds_assists",
            "player_points_rebounds", "player_points_assists",
            "player_rebounds_assists",
            "player_first_basket", "player_double_double",
        ],
        "stat_map": {
            "player_points":                   "PTS",
            "player_rebounds":                 "REB",
            "player_assists":                  "AST",
            "player_threes":                   "3PM",
            "player_blocks":                   "BLK",
            "player_steals":                   "STL",
            "player_points_rebounds_assists":   "PRA",
            "player_points_rebounds":           "PR",
            "player_points_assists":            "PA",
            "player_rebounds_assists":          "RA",
            "player_first_basket":              "first_basket",
            "player_double_double":             "double_double",
        },
    },
    "mlb": {
        "sport_key": "baseball_mlb",
        "sport_label": "Baseball",
        "league": "MLB",
        "icon": "\u26be",
        "espn_path": "baseball/mlb",
        "primary_stat": "hits",
        "markets": [
            "batter_hits", "batter_total_bases", "batter_home_runs",
            "batter_rbis", "batter_runs_scored", "batter_hits_runs_rbis",
            "batter_singles", "batter_stolen_bases",
            "pitcher_strikeouts", "pitcher_hits_allowed",
            "pitcher_earned_runs", "pitcher_outs",
        ],
        "stat_map": {
            "batter_hits":             "hits",
            "batter_total_bases":      "total_bases",
            "batter_home_runs":        "home_runs",
            "batter_rbis":             "rbi",
            "batter_runs_scored":      "runs",
            "batter_hits_runs_rbis":   "hits_runs_rbis",
            "batter_singles":          "singles",
            "batter_stolen_bases":     "stolen_bases",
            "pitcher_strikeouts":      "strikeouts",
            "pitcher_hits_allowed":    "hits_allowed",
            "pitcher_earned_runs":     "earned_runs",
            "pitcher_outs":            "pitcher_outs",
        },
    },
    "nhl": {
        "sport_key": "icehockey_nhl",
        "sport_label": "Ice Hockey",
        "league": "NHL",
        "icon": "\U0001f3d2",
        "espn_path": "hockey/nhl",
        "primary_stat": "shots",
        "markets": [
            "player_points", "player_goals", "player_assists",
            "player_shots_on_goal", "player_power_play_points",
            "player_blocked_shots", "player_saves",
        ],
        "stat_map": {
            "player_points":            "points",
            "player_goals":             "goals",
            "player_assists":           "assists",
            "player_shots_on_goal":     "shots",
            "player_power_play_points": "pp_points",
            "player_blocked_shots":     "blocked_shots",
            "player_saves":             "saves",
        },
    },
    "nfl": {
        "sport_key": "americanfootball_nfl",
        "sport_label": "Football",
        "league": "NFL",
        "icon": "\U0001f3c8",
        "espn_path": "football/nfl",
        "primary_stat": "pass_yds",
        "markets": [
            "player_pass_yds", "player_pass_tds", "player_pass_completions",
            "player_pass_attempts", "player_pass_interceptions",
            "player_rush_yds", "player_rush_attempts", "player_rush_tds",
            "player_reception_yds", "player_receptions",
            "player_receiving_tds", "player_sacks", "player_kicking_points",
        ],
        "stat_map": {
            "player_pass_yds":           "pass_yds",
            "player_pass_tds":           "pass_tds",
            "player_pass_completions":   "pass_comp",
            "player_pass_attempts":      "pass_att",
            "player_pass_interceptions": "pass_int",
            "player_rush_yds":           "rush_yds",
            "player_rush_attempts":      "rush_att",
            "player_rush_tds":           "rush_tds",
            "player_reception_yds":      "rec_yds",
            "player_receptions":         "receptions",
            "player_receiving_tds":      "rec_tds",
            "player_sacks":              "sacks",
            "player_kicking_points":     "kicking_pts",
        },
    },
    "soccer_epl": {
        "sport_key": "soccer_epl",
        "sport_label": "Soccer",
        "league": "Premier League",
        "icon": "\U0001f3f4\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f",
        "espn_path": "soccer/eng.1",
        "primary_stat": "anytime_goal",
        "match_markets": "h2h,btts,totals,correct_score",
        "markets": [
            "player_goal_scorer", "player_first_goal_scorer",
            "player_shots_on_target", "player_shots", "player_assists",
        ],
        "stat_map": {
            "player_goal_scorer":       "anytime_goal",
            "player_first_goal_scorer": "first_goal",
            "player_shots_on_target":   "shots_on_target",
            "player_shots":             "shots",
            "player_assists":           "assists",
        },
    },
}

# Copy soccer config for other leagues
for _lk in [
    "soccer_spain_la_liga", "soccer_uefa_champs_league",
    "soccer_italy_serie_a", "soccer_germany_bundesliga",
    "soccer_france_ligue_one", "soccer_usa_mls",
]:
    _SPORT_CONFIG[_lk] = {**_SPORT_CONFIG["soccer_epl"], "sport_key": _lk}

# Global stat labels dict
STAT_LABELS: Dict[str, str] = {
    # NBA
    "PTS": "Points", "REB": "Rebounds", "AST": "Assists",
    "3PM": "3-Pointers", "BLK": "Blocks", "STL": "Steals",
    "PRA": "Pts+Reb+Ast", "PR": "Pts+Reb", "PA": "Pts+Ast",
    "RA": "Reb+Ast", "first_basket": "First Basket",
    "double_double": "Double-Double",
    # MLB
    "hits": "Hits", "total_bases": "Total Bases", "home_runs": "Home Runs",
    "rbi": "RBI", "runs": "Runs", "hits_runs_rbis": "H+R+RBI",
    "singles": "Singles", "stolen_bases": "Stolen Bases",
    "strikeouts": "Strikeouts", "hits_allowed": "Hits Allowed",
    "earned_runs": "Earned Runs", "pitcher_outs": "Pitcher Outs",
    # NHL
    "points": "Points", "goals": "Goals", "assists": "Assists",
    "shots": "Shots on Goal", "pp_points": "PP Points",
    "blocked_shots": "Blocked Shots", "saves": "Saves",
    # NFL
    "pass_yds": "Pass Yards", "pass_tds": "Pass TDs",
    "pass_comp": "Completions", "pass_att": "Pass Attempts",
    "pass_int": "Interceptions", "rush_yds": "Rush Yards",
    "rush_att": "Rush Attempts", "rush_tds": "Rush TDs",
    "rec_yds": "Rec Yards", "receptions": "Receptions",
    "rec_tds": "Rec TDs", "sacks": "Sacks", "kicking_pts": "Kicking Points",
    # Soccer
    "anytime_goal": "Anytime Scorer", "first_goal": "First Scorer",
    "shots_on_target": "Shots on Target",
}

# Markets that use Yes/No outcomes instead of Over/Under
_YES_NO_MARKETS = {
    "player_goals",
    "player_anytime_scorer",
    "player_first_goal_scorer",
    "player_goal_scorer",
    "player_first_basket",
    "player_double_double",
}
# Keep old name as alias for any external references
_ANYTIME_SCORER_MARKETS = _YES_NO_MARKETS


# ════════════════════════════════════════════════════════════════════════════════
# PART 3 — SELF-CALIBRATING MODEL
# ════════════════════════════════════════════════════════════════════════════════

_CALIBRATION_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "calibration.json")


def load_calibration() -> Dict:
    """Load calibration data from JSON file."""
    try:
        if os.path.exists(_CALIBRATION_FILE):
            with open(_CALIBRATION_FILE) as f:
                return _json.load(f)
    except Exception as e:
        logger.debug("calibration load error: %s", e)
    return {}


def save_calibration(cal: Dict):
    """Save calibration data to JSON file."""
    os.makedirs(os.path.dirname(_CALIBRATION_FILE), exist_ok=True)
    with open(_CALIBRATION_FILE, "w") as f:
        _json.dump(cal, f, indent=2)


def get_calibration_factor(sport: str, stat: str) -> float:
    """Get calibration multiplier for a sport/stat. Returns 1.0 if no data."""
    cal = load_calibration()
    return cal.get(sport, {}).get(stat, {}).get("calibration_factor", 1.0)


def record_bet_result(sport: str, stat: str, confidence: float, result: bool):
    """Record a bet result for calibration. result: True=hit, False=miss."""
    cal = load_calibration()
    if sport not in cal:
        cal[sport] = {}
    if stat not in cal[sport]:
        cal[sport][stat] = {
            "buckets": {
                "55-60": {"bets": 0, "hits": 0},
                "60-65": {"bets": 0, "hits": 0},
                "65-70": {"bets": 0, "hits": 0},
                "70-75": {"bets": 0, "hits": 0},
                "75-80": {"bets": 0, "hits": 0},
                "80+":   {"bets": 0, "hits": 0},
            },
            "calibration_factor": 1.0,
            "total_bets": 0,
            "total_hits": 0,
            "last_updated": None,
        }

    bucket = ("80+" if confidence >= 80
              else "75-80" if confidence >= 75
              else "70-75" if confidence >= 70
              else "65-70" if confidence >= 65
              else "60-65" if confidence >= 60
              else "55-60")
    cal[sport][stat]["buckets"][bucket]["bets"] += 1
    if result:
        cal[sport][stat]["buckets"][bucket]["hits"] += 1
    cal[sport][stat]["total_bets"] += 1
    if result:
        cal[sport][stat]["total_hits"] += 1

    total = cal[sport][stat]["total_bets"]
    if total >= 20:
        actual_rate = cal[sport][stat]["total_hits"] / total
        expected_rate = 0.67
        factor = max(0.7, min(1.3, actual_rate / expected_rate))
        cal[sport][stat]["calibration_factor"] = round(factor, 3)

    cal[sport][stat]["last_updated"] = _dt.now().isoformat()
    save_calibration(cal)


def apply_calibration(confidence: float, sport: str, stat: str) -> float:
    """Apply calibration factor to raw confidence."""
    factor = get_calibration_factor(sport, stat)
    calibrated = confidence * factor
    return max(30.0, min(95.0, round(calibrated, 1)))


# ════════════════════════════════════════════════════════════════════════════════
# PART 4 — GRADING + SCORING SYSTEM
# ════════════════════════════════════════════════════════════════════════════════

def get_prop_grade(confidence: float, price: float = -110) -> Tuple[str, float]:
    """A/B/Watch/Pass grade based on confidence + edge vs market."""
    if price > 0:
        market_prob = 100 / (price + 100)
    elif price < 0:
        market_prob = abs(price) / (abs(price) + 100)
    else:
        market_prob = 0.5
    model_prob = confidence / 100
    edge = round((model_prob - market_prob) * 100, 1)
    if confidence >= 68 and edge >= 5:
        return "A", edge
    elif confidence >= 62 and edge >= 3:
        return "B", edge
    elif confidence >= 55:
        return "Watch", edge
    return "Pass", edge


def calculate_bet_score(prop: Dict) -> int:
    """Bet quality score 0-100."""
    score = 0
    conf = prop.get("confidence", 50)
    edge = prop.get("edge", 0)
    grade = prop.get("grade", "Pass")
    # Confidence (40pts)
    if conf >= 80:     score += 40
    elif conf >= 72:   score += 32
    elif conf >= 65:   score += 24
    elif conf >= 58:   score += 16
    else:              score += 8
    # Edge (35pts)
    if edge >= 12:     score += 35
    elif edge >= 8:    score += 28
    elif edge >= 5:    score += 21
    elif edge >= 3:    score += 14
    else:              score += 5
    # Grade (15pts)
    score += {"A": 15, "B": 10, "Watch": 5, "Pass": 1}.get(grade, 1)
    # Line shopping bonus (10pts)
    all_prices = prop.get("all_prices", {})
    if len(all_prices) >= 3:
        score += 10
    elif len(all_prices) >= 2:
        score += 5
    return min(100, score)


def get_score_label(score: int) -> Tuple[str, str]:
    """Return (label, css_class) for a bet score."""
    if score >= 85:
        return "Elite", "elite"
    if score >= 75:
        return "Strong", "strong"
    if score >= 65:
        return "Playable", "playable"
    return "Pass", "pass"


def get_playable_price(model_prob: float, min_edge: float = 0.03) -> int:
    """Return worst playable American odds price."""
    max_implied = max(0.01, min(0.99, model_prob - min_edge))
    if max_implied >= 0.5:
        american = -(max_implied / (1 - max_implied)) * 100
    else:
        american = ((1 - max_implied) / max_implied) * 100
    return round(american)


def get_stake_rec(grade: str, edge: float, score: int) -> str:
    """Return stake recommendation string."""
    if grade == "A" and score >= 80:
        return "1.0u"
    elif grade == "A" and edge >= 7:
        return "0.75u"
    elif grade == "A":
        return "0.5u"
    elif grade == "B" and edge >= 6:
        return "0.5u"
    elif grade == "B":
        return "0.25u"
    elif grade == "Watch":
        return "0.1u"
    return "Pass"


def get_red_flags(prop: Dict) -> List[str]:
    """Return list of red flag strings for a prop."""
    flags = []
    conf = prop.get("confidence", 50)
    price = prop.get("price", -110)
    playable = prop.get("playable_to", -150)
    stat = prop.get("stat", "")
    line = prop.get("line", 0)
    if conf < 55:
        flags.append("Low confidence")
    if price != -110 and price < playable:
        flags.append("Price at limit")
    if len(prop.get("all_prices", {})) <= 1:
        flags.append("Low market liquidity")
    if stat in ("goals", "home_runs") and line <= 0.5:
        flags.append("Binary prop")
    return flags


def get_bet_reasons(prop: Dict) -> List[str]:
    """Return list of reasons supporting the bet."""
    reasons = []
    conf = prop.get("confidence", 50)
    edge = prop.get("edge", 0)
    pick = prop.get("pick", "")
    books = prop.get("all_prices", {})
    if edge >= 8:
        reasons.append(f"+{edge}% edge vs market implied prob")
    elif edge >= 5:
        reasons.append(f"+{edge}% model edge over book")
    if conf >= 72 and pick == "OVER":
        reasons.append("Model projects well above posted line")
    elif conf >= 72 and pick == "UNDER":
        reasons.append("Market overpricing player output")
    best_book = prop.get("best_book", "")
    best_price = prop.get("price", -110)
    if best_book and len(books) >= 2:
        reasons.append(f"Best price at {best_book.upper()} ({format_american(best_price)})")
    cal_factor = prop.get("cal_factor", 1.0)
    if cal_factor < 0.95:
        reasons.append("Model historically overconfident in this market")
    elif cal_factor > 1.05:
        reasons.append("Model historically accurate in this market")
    return reasons[:3]


def format_american(price) -> str:
    """Format American odds price as display string (+150, -110)."""
    if price is None:
        return "N/A"
    price = float(price)
    if price > 0:
        return f"+{round(price)}"
    return str(round(price))

# Full team name → abbreviation (NBA, MLB, NHL combined)
_TEAM_ABBR: Dict[str, str] = {
    # NBA
    "Atlanta Hawks": "ATL", "Boston Celtics": "BOS",
    "Brooklyn Nets": "BKN", "Charlotte Hornets": "CHA",
    "Chicago Bulls": "CHI", "Cleveland Cavaliers": "CLE",
    "Dallas Mavericks": "DAL", "Denver Nuggets": "DEN",
    "Detroit Pistons": "DET", "Golden State Warriors": "GSW",
    "Houston Rockets": "HOU", "Indiana Pacers": "IND",
    "LA Clippers": "LAC", "Los Angeles Clippers": "LAC",
    "LA Lakers": "LAL", "Los Angeles Lakers": "LAL",
    "Memphis Grizzlies": "MEM", "Miami Heat": "MIA",
    "Milwaukee Bucks": "MIL", "Minnesota Timberwolves": "MIN",
    "New Orleans Pelicans": "NOP", "New York Knicks": "NYK",
    "Oklahoma City Thunder": "OKC", "Orlando Magic": "ORL",
    "Philadelphia 76ers": "PHI", "Phoenix Suns": "PHX",
    "Portland Trail Blazers": "POR", "Sacramento Kings": "SAC",
    "San Antonio Spurs": "SAS", "Toronto Raptors": "TOR",
    "Utah Jazz": "UTA", "Washington Wizards": "WAS",
}

_ROSTER_CACHE: Dict[str, Tuple[set, float]] = {}
_ROSTER_LOCK = threading.Lock()
_ROSTER_TTL = 86400  # 24 h


def _get_team_roster_names(espn_path: str, team_full_name: str) -> set:
    """Return set of player display names for a team via ESPN roster endpoint.

    Results cached 24 h in-process. On any error returns empty set.
    """
    cache_key = f"{espn_path}|{team_full_name}"
    now = time.time()
    with _ROSTER_LOCK:
        cached = _ROSTER_CACHE.get(cache_key)
        if cached:
            names, expires = cached
            if now < expires:
                return names

    names: set = set()
    try:
        # Step 1: find team ID
        teams_data = fetch(
            f"https://site.api.espn.com/apis/site/v2/sports/{espn_path}/teams",
            params={"limit": 40},
            timeout=8,
        )
        if not teams_data:
            return names

        team_id = None
        team_lower = team_full_name.lower()
        for sport in teams_data.get("sports", [teams_data]):
            for league in sport.get("leagues", [sport]):
                for t in league.get("teams", []):
                    info = t.get("team", t)
                    display = (info.get("displayName") or "").lower()
                    if team_lower in display or display in team_lower:
                        team_id = info.get("id")
                        break
                if team_id:
                    break
            if team_id:
                break

        if not team_id:
            return names

        # Step 2: fetch roster
        roster_data = fetch(
            f"https://site.api.espn.com/apis/site/v2/sports/{espn_path}"
            f"/teams/{team_id}/roster",
            timeout=8,
        )
        if not roster_data:
            return names

        for group in roster_data.get("athletes", []):
            if isinstance(group, dict):
                items = group.get("items", [group])
                for item in items:
                    if isinstance(item, dict):
                        name = item.get("displayName", "")
                        if name:
                            names.add(name)

        print(f"[ROSTER] {team_full_name}: {len(names)} players")
    except Exception as e:
        logger.debug("Roster fetch failed %s: %s", team_full_name, e)

    with _ROSTER_LOCK:
        _ROSTER_CACHE[cache_key] = (names, now + _ROSTER_TTL)
    return names


def _assign_to_team(player_name: str,
                    home_team: str, home_abbr: str, home_roster: set,
                    away_team: str, away_abbr: str, away_roster: set) -> str:
    """Assign a player to home or away abbr using roster fuzzy matching."""
    if player_name in home_roster:
        return home_abbr
    if player_name in away_roster:
        return away_abbr
    # Last-name match
    last = player_name.split()[-1].lower()
    for n in home_roster:
        if n.split()[-1].lower() == last:
            return home_abbr
    for n in away_roster:
        if n.split()[-1].lower() == last:
            return away_abbr
    # Substring match
    pl = player_name.lower()
    for n in home_roster:
        if pl in n.lower() or n.lower() in pl:
            return home_abbr
    for n in away_roster:
        if pl in n.lower() or n.lower() in pl:
            return away_abbr
    return home_abbr  # default to home


def _team_abbr(team_full_name: str) -> str:
    """Convert full team name to short abbreviation."""
    if team_full_name in _TEAM_ABBR:
        return _TEAM_ABBR[team_full_name]
    words = team_full_name.upper().split()
    return words[-1][:3] if words else "UNK"


# ════════════════════════════════════════════════════════════════════════════════
# PART 2 — LINE SHOPPING + GAME LINES
# ════════════════════════════════════════════════════════════════════════════════

def fetch_best_odds_props(sport_key: str, event_id: str,
                          markets: List[str]) -> Dict[str, Dict]:
    """Fetch props from ALL bookmakers and return best price per player/stat."""
    api_key = _key()
    if not api_key:
        return {}
    markets_str = ",".join(markets)
    data = fetch(
        f"{ODDS_BASE}/sports/{sport_key}/events/{event_id}/odds",
        params={
            "apiKey":     api_key,
            "regions":    "us",
            "markets":    markets_str,
            "oddsFormat": "american",
        },
        use_cache=False,
        timeout=12,
    )
    if not data or not isinstance(data, dict):
        return {}

    best_odds: Dict[str, Dict] = {}
    cfg = _SPORT_CONFIG.get(sport_key, {})
    stat_map = cfg.get("stat_map", {})
    # Also check by sport_name keys
    if not stat_map:
        for _sn, _sc in _SPORT_CONFIG.items():
            if _sc.get("sport_key") == sport_key:
                stat_map = _sc.get("stat_map", {})
                break

    for bookmaker in data.get("bookmakers", []):
        book_key = bookmaker.get("key", "")
        for market in bookmaker.get("markets", []):
            market_key = market.get("key", "")
            stat = stat_map.get(market_key)
            if not stat:
                continue
            is_yes_no = market_key in _YES_NO_MARKETS
            target = "Yes" if is_yes_no else "Over"
            for outcome in market.get("outcomes", []):
                if outcome.get("name") != target:
                    continue
                player_name = (outcome.get("description") or "").strip()
                if not player_name:
                    player_name = outcome.get("name", "").strip()
                line = 0.5 if is_yes_no else outcome.get("point")
                price = float(outcome.get("price", -110) or -110)
                if not player_name or line is None:
                    continue
                line = float(line)
                prob = _american_to_implied(price)
                key = f"{player_name}|{stat}"
                if key not in best_odds:
                    best_odds[key] = {
                        "player": player_name,
                        "stat":   stat,
                        "line":   line,
                        "over_prob": prob,
                        "price":  price,
                        "best_book": book_key,
                        "all_prices": {book_key: price},
                    }
                else:
                    best_odds[key]["all_prices"][book_key] = price
                    if price > best_odds[key]["price"]:
                        best_odds[key]["price"] = price
                        best_odds[key]["best_book"] = book_key
                        best_odds[key]["over_prob"] = prob
    return best_odds


def fetch_game_lines(sport_key: str) -> Dict[str, Dict]:
    """Get ML, spread, total for all games. Returns {home|away: lines_dict}."""
    api_key = _key()
    if not api_key:
        return {}
    data = fetch(
        f"{ODDS_BASE}/sports/{sport_key}/odds",
        params={
            "apiKey":     api_key,
            "regions":    "us",
            "markets":    "h2h,spreads,totals",
            "oddsFormat": "american",
            "bookmakers": "draftkings,fanduel",
        },
        use_cache=False,
        timeout=10,
    )
    if not data or not isinstance(data, list):
        return {}

    lines: Dict[str, Dict] = {}
    for game in data:
        home = game.get("home_team", "")
        away = game.get("away_team", "")
        key = f"{home}|{away}"
        gl: Dict = {
            "home_ml": None, "away_ml": None,
            "home_prob": 50.0, "away_prob": 50.0, "draw_prob": 0.0,
            "home_spread": None, "away_spread": None,
            "home_spread_price": -110, "away_spread_price": -110,
            "total_line": None, "total_over_price": -110,
        }
        for bookmaker in game.get("bookmakers", [])[:1]:
            for market in bookmaker.get("markets", []):
                mk = market.get("key", "")
                if mk == "h2h":
                    probs: Dict[str, float] = {}
                    for o in market.get("outcomes", []):
                        t = o.get("name", "")
                        p = float(o.get("price", 0) or 0)
                        if t == home:
                            gl["home_ml"] = p
                        elif t == away:
                            gl["away_ml"] = p
                        probs[t] = _american_to_implied(p)
                    total_prob = sum(probs.values())
                    if total_prob > 0:
                        gl["home_prob"] = round(probs.get(home, 0.5) / total_prob * 100, 1)
                        gl["away_prob"] = round(probs.get(away, 0.5) / total_prob * 100, 1)
                        gl["draw_prob"] = round(probs.get("Draw", 0) / total_prob * 100, 1)
                elif mk == "spreads":
                    for o in market.get("outcomes", []):
                        t = o.get("name", "")
                        if t == home:
                            gl["home_spread"] = o.get("point", 0)
                            gl["home_spread_price"] = float(o.get("price", -110) or -110)
                        elif t == away:
                            gl["away_spread"] = o.get("point", 0)
                            gl["away_spread_price"] = float(o.get("price", -110) or -110)
                elif mk == "totals":
                    for o in market.get("outcomes", []):
                        if o.get("name") == "Over":
                            gl["total_line"] = o.get("point")
                            gl["total_over_price"] = float(o.get("price", -110) or -110)
        lines[key] = gl
    return lines


# ════════════════════════════════════════════════════════════════════════════════
# PART 5 — REWRITTEN build_sport_props() INTEGRATING ALL SYSTEMS
# ════════════════════════════════════════════════════════════════════════════════

def build_sport_props(sport_name: str, ttl: int = 900) -> list:
    """Build all prop cards for a sport using Odds API with line shopping,
    calibration, grading, and scoring.

    Returns list of game dicts with home_players, away_players, top_props, etc.
    """
    cfg = _SPORT_CONFIG.get(sport_name)
    if not cfg:
        logger.warning("build_sport_props: unknown sport %s", sport_name)
        return []

    sport_key  = cfg["sport_key"]
    espn_path  = cfg.get("espn_path", "")
    markets    = cfg.get("markets", [])
    stat_map   = cfg.get("stat_map", {})
    primary    = cfg.get("primary_stat", "")
    api_key    = _key()

    if not api_key:
        logger.warning("[ODDS] ODDS_API_KEY not set — no props for %s", sport_name)
        return []

    # Step 1: get events
    events = fetch(
        f"{ODDS_BASE}/sports/{sport_key}/events",
        params={"apiKey": api_key, "dateFormat": "iso"},
        use_cache=False,
        timeout=12,
    )
    if not events or not isinstance(events, list):
        print(f"[ODDS] No events for {sport_name}")
        return []
    print(f"[ODDS] {sport_name}: {len(events)} events")

    # Step 2: game lines (ML + spread + total)
    game_lines_map = fetch_game_lines(sport_key)

    results: list = []

    for i, event in enumerate(events[:15]):
        if i > 0 and i % 3 == 0:
            time.sleep(0.5)

        event_id  = event.get("id", "")
        home_team = event.get("home_team", "")
        away_team = event.get("away_team", "")
        if not home_team or not away_team:
            continue

        # Game lines
        line_key = f"{home_team}|{away_team}"
        lines = game_lines_map.get(line_key, {})
        home_prob = lines.get("home_prob", 50.0)
        away_prob = lines.get("away_prob", 50.0)
        draw_prob = lines.get("draw_prob", 0.0)

        home_abbr = _team_abbr(home_team)
        away_abbr = _team_abbr(away_team)

        # Badge
        best_prob = max(home_prob, away_prob)
        fav_team = (home_team if home_prob > away_prob else away_team).split()[-1]
        if best_prob >= 70:
            badge_label = f"Strong Fav - {fav_team}"
            badge_class = "badge-strong"
        elif best_prob >= 60:
            badge_label = f"Favoured - {fav_team}"
            badge_class = "badge-favoured"
        elif best_prob >= 55:
            badge_label = f"Slight Edge - {fav_team}"
            badge_class = "badge-slight"
        else:
            badge_label = "Pick'em"
            badge_class = "badge-pickem"

        # Step 3: player props with line shopping
        best_odds = fetch_best_odds_props(sport_key, event_id, markets)

        if not best_odds:
            # No props — still include game with game lines
            results.append({
                "event_id":    event_id,
                "sport":       cfg.get("sport_label", ""),
                "league":      cfg.get("league", ""),
                "sport_icon":  cfg.get("icon", ""),
                "home_team":   home_team,
                "away_team":   away_team,
                "home_abbr":   home_abbr,
                "away_abbr":   away_abbr,
                "home_prob":   home_prob,
                "away_prob":   away_prob,
                "draw_prob":   draw_prob,
                "home_ml":     lines.get("home_ml"),
                "away_ml":     lines.get("away_ml"),
                "home_spread": lines.get("home_spread"),
                "away_spread": lines.get("away_spread"),
                "home_spread_price": lines.get("home_spread_price", -110),
                "away_spread_price": lines.get("away_spread_price", -110),
                "total_line":  lines.get("total_line"),
                "total_over_price": lines.get("total_over_price", -110),
                "pick_badge_label": badge_label,
                "pick_badge_class": badge_class,
                "kickoff":     event.get("commence_time", ""),
                "status":      "STATUS_SCHEDULED",
                "home_players": [],
                "away_players": [],
                "home_roster":  [],
                "away_roster":  [],
                "top_props":    [],
                "top_pick":     None,
                "is_soccer":    "soccer" in sport_name,
                # today.html compat
                "predicted_winner": home_team if home_prob >= away_prob else away_team,
                "win_prob":     best_prob,
                "confidence":   "HIGH" if best_prob >= 65 else "MEDIUM" if best_prob >= 55 else "LOW",
                "home_score":   None, "away_score": None,
                "home_wins": 0, "home_losses": 0,
                "away_wins": 0, "away_losses": 0,
                "bookmaker": "",
                "book_home_ml": format_american(lines.get("home_ml")),
                "book_away_ml": format_american(lines.get("away_ml")),
                "book_home_spread": lines.get("home_spread"),
                "book_total":  lines.get("total_line"),
                "spread":      lines.get("home_spread"),
                "over_under":  lines.get("total_line"),
            })
            continue

        # Step 4: ESPN rosters for team assignment
        home_roster_names = _get_team_roster_names(espn_path, home_team) if espn_path else set()
        away_roster_names = _get_team_roster_names(espn_path, away_team) if espn_path else set()

        # Build player props with grading + scoring
        player_data: Dict[str, Dict] = {}
        for key, odds in best_odds.items():
            player_name = odds["player"]
            stat        = odds["stat"]
            line        = odds["line"]
            over_prob   = odds["over_prob"]
            price       = odds["price"]
            all_prices  = odds["all_prices"]
            best_book   = odds["best_book"]

            raw_conf = round(over_prob * 100, 1)
            conf = apply_calibration(raw_conf, sport_name, stat)
            cal_factor = get_calibration_factor(sport_name, stat)

            if conf >= 55:
                pick = "OVER"
                display_conf = conf
            elif conf <= 45:
                pick = "UNDER"
                under_raw = round((1 - over_prob) * 100, 1)
                display_conf = apply_calibration(under_raw, sport_name, stat)
            else:
                pick = "FAIR"
                display_conf = round(max(conf, (1 - over_prob) * 100), 1)

            grade, edge = get_prop_grade(display_conf, price)
            playable = get_playable_price(display_conf / 100)

            prop = {
                "player_name":    player_name,
                "stat":           stat,
                "label":          STAT_LABELS.get(stat, stat),
                "line":           line,
                "pick":           pick,
                "confidence":     display_conf,
                "raw_confidence": raw_conf,
                "cal_factor":     cal_factor,
                "over_prob":      round(over_prob * 100, 1),
                "under_prob":     round((1 - over_prob) * 100, 1),
                "price":          price,
                "best_book":      best_book,
                "all_prices":     all_prices,
                "grade":          grade,
                "edge":           edge,
                "playable_to":    format_american(playable),
                "sport":          sport_name,
                "game":           f"{away_abbr} @ {home_abbr}",
                # Legacy compat
                "avg":            line,
                "stars":          5 if display_conf >= 85 else 4 if display_conf >= 78 else 3 if display_conf >= 68 else 2 if display_conf >= 58 else 1,
                "conf_label":     "Elite Pick" if display_conf >= 85 else "Strong Pick" if display_conf >= 78 else "Good Pick" if display_conf >= 68 else "Moderate" if display_conf >= 58 else "Use Caution",
                "trend":          "->",
            }
            prop["score"] = calculate_bet_score(prop)
            prop["score_label"], prop["score_class"] = get_score_label(prop["score"])
            prop["stake_rec"] = get_stake_rec(grade, edge, prop["score"])
            prop["red_flags"] = get_red_flags(prop)
            prop["reasons"]   = get_bet_reasons(prop)

            if player_name not in player_data:
                player_data[player_name] = {"name": player_name, "props": []}
            player_data[player_name]["props"].append(prop)

        # Assign players to teams
        home_players: list = []
        away_players: list = []
        all_game_props: list = []

        for player_name, pdata in player_data.items():
            team = _assign_to_team(
                player_name,
                home_team, home_abbr, home_roster_names,
                away_team, away_abbr, away_roster_names,
            )
            pdata["team"] = team
            pdata["props"].sort(key=lambda x: x["confidence"], reverse=True)
            for p in pdata["props"]:
                p["team"] = team
                all_game_props.append(p)
            if team == home_abbr:
                home_players.append(pdata)
            else:
                away_players.append(pdata)

        def _best_conf(player):
            return player["props"][0]["confidence"] if player["props"] else 0
        home_players.sort(key=_best_conf, reverse=True)
        away_players.sort(key=_best_conf, reverse=True)

        # Top props sorted by confidence
        all_game_props.sort(key=lambda x: x["confidence"], reverse=True)
        prop_count = max(10, min(30, len(all_game_props)))
        top_props = all_game_props[:prop_count]
        top_pick = top_props[0] if top_props else None

        print(f"[ODDS] {away_team} @ {home_team}: {len(player_data)} players, {len(top_props)} top props")

        results.append({
            "event_id":    event_id,
            "sport":       cfg.get("sport_label", ""),
            "league":      cfg.get("league", ""),
            "sport_icon":  cfg.get("icon", ""),
            "home_team":   home_team,
            "away_team":   away_team,
            "home_abbr":   home_abbr,
            "away_abbr":   away_abbr,
            "home_prob":   home_prob,
            "away_prob":   away_prob,
            "draw_prob":   draw_prob,
            "home_ml":     lines.get("home_ml"),
            "away_ml":     lines.get("away_ml"),
            "home_spread": lines.get("home_spread"),
            "away_spread": lines.get("away_spread"),
            "home_spread_price": lines.get("home_spread_price", -110),
            "away_spread_price": lines.get("away_spread_price", -110),
            "total_line":  lines.get("total_line"),
            "total_over_price": lines.get("total_over_price", -110),
            "pick_badge_label": badge_label,
            "pick_badge_class": badge_class,
            "kickoff":     event.get("commence_time", ""),
            "status":      "STATUS_SCHEDULED",
            "home_players": home_players[:15],
            "away_players": away_players[:15],
            "home_roster":  home_players[:15],
            "away_roster":  away_players[:15],
            "top_props":    top_props,
            "top_pick":     top_pick,
            "is_soccer":    "soccer" in sport_name,
            # today.html compat
            "predicted_winner": home_team if home_prob >= away_prob else away_team,
            "win_prob":     best_prob,
            "confidence":   "HIGH" if best_prob >= 65 else "MEDIUM" if best_prob >= 55 else "LOW",
            "home_score":   None, "away_score": None,
            "home_wins": 0, "home_losses": 0,
            "away_wins": 0, "away_losses": 0,
            "bookmaker":    "",
            "book_home_ml": format_american(lines.get("home_ml")),
            "book_away_ml": format_american(lines.get("away_ml")),
            "book_home_spread": lines.get("home_spread"),
            "book_total":  lines.get("total_line"),
            "spread":      lines.get("home_spread"),
            "over_under":  lines.get("total_line"),
        })

    games_with = sum(1 for g in results if g.get("top_props"))
    print(f"[ODDS] {sport_name}: {len(results)} games, {games_with} with props")
    return results


SOCCER_LEAGUES: Dict[str, Dict] = {
    "epl":        {"sport_key": "soccer_epl",                "league": "Premier League",   "icon": "🏴󠁧󠁢󠁥󠁮󠁧󠁿"},
    "laliga":     {"sport_key": "soccer_spain_la_liga",      "league": "La Liga",          "icon": "🇪🇸"},
    "ucl":        {"sport_key": "soccer_uefa_champs_league", "league": "Champions League", "icon": "⭐"},
    "seriea":     {"sport_key": "soccer_italy_serie_a",      "league": "Serie A",          "icon": "🇮🇹"},
    "bundesliga": {"sport_key": "soccer_germany_bundesliga", "league": "Bundesliga",       "icon": "🇩🇪"},
    "ligue1":     {"sport_key": "soccer_france_ligue_one",   "league": "Ligue 1",          "icon": "🇫🇷"},
    "mls":        {"sport_key": "soccer_usa_mls",            "league": "MLS",              "icon": "🇺🇸"},
}

_SOCCER_CACHE: Dict[str, Tuple] = {}
_SOCCER_LOCK = threading.Lock()


def _american_to_implied(price: float) -> float:
    """Convert American odds price to implied probability (0.0–1.0)."""
    if price > 0:
        return 100.0 / (price + 100.0)
    elif price < 0:
        return abs(price) / (abs(price) + 100.0)
    return 0.5


def _fmt_american(price: float) -> str:
    """Format American odds price as string (+150, -110)."""
    if price >= 0:
        return f"+{round(price)}"
    return str(round(price))


def build_soccer_props(ttl: int = 900) -> list:
    """Build soccer match data for all leagues from Odds API only.

    Returns list of game dicts with:
        home_team, away_team, home_prob, away_prob, draw_prob,
        btts_yes_prob, book_total, predicted_winner, win_prob, confidence,
        players (list of {name, goal_scorer_prob, first_scorer_prob,
                           shots_on_target_line, shots_on_target_prob,
                           shots_line, shots_prob}),
        home_roster / away_roster (today.html compatible format).
    """
    api_key = _key()
    if not api_key:
        logger.warning("[SOCCER] ODDS_API_KEY not set — no soccer props")
        return []

    cache_key = "soccer_all"
    now = time.time()
    with _SOCCER_LOCK:
        cached = _SOCCER_CACHE.get(cache_key)
        if cached:
            data, expires = cached
            if now < expires:
                return data

    all_games: list = []
    upcoming:  list = []   # fixtures with no odds posted yet

    for league_slug, league_cfg in SOCCER_LEAGUES.items():
        sport_key   = league_cfg["sport_key"]
        league_name = league_cfg["league"]
        icon        = league_cfg["icon"]

        # Events list
        events = fetch(
            f"{ODDS_BASE}/sports/{sport_key}/events",
            params={"apiKey": api_key, "dateFormat": "iso"},
            use_cache=False, timeout=12,
        )
        if not events or not isinstance(events, list):
            continue
        print(f"[SOCCER] {league_name}: {len(events)} events")

        # Match odds: h2h (3-way) + btts + totals in one bulk call
        match_odds_resp = fetch(
            f"{ODDS_BASE}/sports/{sport_key}/odds",
            params={
                "apiKey":     api_key,
                "regions":    "us,uk",
                "markets":    "h2h,btts,totals",
                "oddsFormat": "american",
            },
            use_cache=False, timeout=12,
        )
        match_odds_map: Dict[str, dict] = {}
        if match_odds_resp and isinstance(match_odds_resp, list):
            for mo in match_odds_resp:
                h = mo.get("home_team", "")
                a = mo.get("away_team", "")
                match_odds_map[f"{h}|{a}"] = mo

        for event in events[:10]:
            event_id  = event.get("id", "")
            home_team = event.get("home_team", "")
            away_team = event.get("away_team", "")
            if not home_team or not away_team:
                continue

            mo = match_odds_map.get(f"{home_team}|{away_team}", {})

            home_prob = draw_prob = away_prob = None
            btts_yes_prob = None
            total_goals_line = None
            book_home_ml = book_draw_ml = book_away_ml = None
            bookmaker_name = ""

            for bm in mo.get("bookmakers", [])[:1]:
                bookmaker_name = bm.get("title", "")
                for market in bm.get("markets", []):
                    mk       = market.get("key", "")
                    outcomes = market.get("outcomes", [])

                    if mk == "h2h":
                        raw: Dict[str, float] = {}
                        for o in outcomes:
                            name  = o.get("name", "")
                            price = float(o.get("price", 0) or 0)
                            raw[name] = _american_to_implied(price)
                            if name == home_team:
                                book_home_ml = _fmt_american(price)
                            elif name == away_team:
                                book_away_ml = _fmt_american(price)
                            elif name.lower() == "draw":
                                book_draw_ml = _fmt_american(price)
                        if raw:
                            total = sum(raw.values())
                            norm  = {k: round(v / total * 100, 1) for k, v in raw.items()}
                            home_prob = norm.get(home_team, 33.3)
                            away_prob = norm.get(away_team, 33.3)
                            draw_prob = norm.get("Draw", round(100.0 - home_prob - away_prob, 1))

                    elif mk == "btts":
                        raw_btts: Dict[str, float] = {}
                        for o in outcomes:
                            name  = o.get("name", "").lower()
                            price = float(o.get("price", 0) or 0)
                            raw_btts[name] = _american_to_implied(price)
                        if raw_btts:
                            btotal = sum(raw_btts.values())
                            bnorm  = {k: round(v / btotal * 100, 1) for k, v in raw_btts.items()}
                            btts_yes_prob = bnorm.get("yes")

                    elif mk == "totals":
                        for o in outcomes:
                            if o.get("name") == "Over":
                                total_goals_line = o.get("point")
                                break

            # No bookmaker lines posted yet — save as upcoming fixture and skip
            if home_prob is None:
                upcoming.append({
                    "home_team":  home_team,
                    "away_team":  away_team,
                    "league":     league_name,
                    "sport_icon": icon,
                    "kickoff":    event.get("commence_time", ""),
                })
                continue

            # Predicted winner
            if home_prob >= draw_prob and home_prob >= away_prob:
                predicted_winner, win_prob_val = home_team, home_prob
            elif draw_prob >= away_prob:
                predicted_winner, win_prob_val = "Draw", draw_prob
            else:
                predicted_winner, win_prob_val = away_team, away_prob

            conf_str = "HIGH" if win_prob_val >= 55 else ("MEDIUM" if win_prob_val >= 45 else "LOW")

            # Match pick label
            if win_prob_val >= 70:
                match_pick_label = "🔥 Strong Favourite"
                pick_badge_class = "badge-strong"
            elif win_prob_val >= 60:
                match_pick_label = "✅ Favoured"
                pick_badge_class = "badge-favoured"
            elif win_prob_val >= 50:
                match_pick_label = "📊 Slight Edge"
                pick_badge_class = "badge-slight"
            else:
                match_pick_label = "⚖️ Pick'em"
                pick_badge_class = "badge-pickem"

            # Per-event player props
            prop_markets = ("player_goal_scorer,player_first_goal_scorer,"
                            "player_shots_on_target,player_shots")
            props_resp = fetch(
                f"{ODDS_BASE}/sports/{sport_key}/events/{event_id}/odds",
                params={
                    "apiKey":     api_key,
                    "regions":    "us,uk",
                    "markets":    prop_markets,
                    "bookmakers": "draftkings,fanduel,betmgm,bet365",
                    "oddsFormat": "american",
                },
                use_cache=False, timeout=10,
            )

            player_data: Dict[str, Dict] = defaultdict(dict)
            if props_resp and isinstance(props_resp, dict):
                for bm in props_resp.get("bookmakers", [])[:1]:
                    for market in bm.get("markets", []):
                        mk       = market.get("key", "")
                        for outcome in market.get("outcomes", []):
                            # Player name: prefer description field
                            player = (outcome.get("description") or "").strip()
                            if not player:
                                # For shots markets 'name' IS the player
                                if mk in ("player_shots_on_target", "player_shots"):
                                    continue
                                player = outcome.get("name", "").strip()
                            if not player:
                                continue

                            price  = float(outcome.get("price", -110) or -110)
                            op     = round(_american_to_implied(price) * 100, 1)
                            oname  = outcome.get("name", "")
                            line   = outcome.get("point")

                            if mk == "player_goal_scorer" and oname == "Yes":
                                if "goal_scorer_prob" not in player_data[player]:
                                    player_data[player]["goal_scorer_prob"] = op
                            elif mk == "player_first_goal_scorer" and oname == "Yes":
                                if "first_scorer_prob" not in player_data[player]:
                                    player_data[player]["first_scorer_prob"] = op
                            elif mk == "player_shots_on_target" and oname == "Over" and line is not None:
                                if "shots_on_target_line" not in player_data[player]:
                                    player_data[player]["shots_on_target_line"] = float(line)
                                    player_data[player]["shots_on_target_prob"] = op
                            elif mk == "player_shots" and oname == "Over" and line is not None:
                                if "shots_line" not in player_data[player]:
                                    player_data[player]["shots_line"] = float(line)
                                    player_data[player]["shots_prob"] = op

            # Build sorted player list
            players = []
            for pname, pdata in player_data.items():
                if not pdata:
                    continue
                players.append({
                    "name":                 pname,
                    "goal_scorer_prob":     pdata.get("goal_scorer_prob"),
                    "first_scorer_prob":    pdata.get("first_scorer_prob"),
                    "shots_on_target_line": pdata.get("shots_on_target_line"),
                    "shots_on_target_prob": pdata.get("shots_on_target_prob"),
                    "shots_line":           pdata.get("shots_line"),
                    "shots_prob":           pdata.get("shots_prob"),
                })
            players.sort(key=lambda p: p.get("goal_scorer_prob") or 0, reverse=True)

            # today.html compat: convert to goal_prob roster format
            def _to_today_roster(plist):
                out = []
                for p in plist:
                    gp = p.get("goal_scorer_prob") or 0
                    out.append({
                        "name":       p["name"],
                        "shots_pg":   None,
                        "xg_shot":    None,
                        "goal_prob":  gp,
                        "confidence": "HIGH" if gp > 35 else ("MEDIUM" if gp > 20 else "LOW"),
                    })
                return out

            half = max(len(players) // 2, 1)
            home_roster_td = _to_today_roster(players[:half])
            away_roster_td = _to_today_roster(players[half:])

            print(f"[SOCCER] {home_team} vs {away_team}: {len(players)} players")

            all_games.append({
                "sport":      "Soccer",
                "league":     league_name,
                "sport_icon": icon,
                "is_soccer":  True,
                "home_team":  home_team,
                "away_team":  away_team,
                "home_prob":  home_prob,
                "away_prob":  away_prob,
                "draw_prob":  draw_prob,
                "kickoff":    event.get("commence_time", ""),
                "status":     "STATUS_SCHEDULED",
                "home_score": None,
                "away_score": None,
                "spread":     None,
                "over_under": total_goals_line,
                "bookmaker":  bookmaker_name,
                "book_home_ml": book_home_ml,
                "book_away_ml": book_away_ml,
                "book_draw_ml": book_draw_ml,
                "book_total":   total_goals_line,
                "btts_yes_prob": btts_yes_prob,
                "predicted_winner": predicted_winner,
                "win_prob":         win_prob_val,
                "confidence":       conf_str,
                # Match pick badge
                "match_pick_label": match_pick_label,
                "pick_badge_class": pick_badge_class,
                # Rich player data for soccer.html
                "players":      players[:20],
                # today.html compat
                "home_roster":  home_roster_td,
                "away_roster":  away_roster_td,
                "home_wins":    0, "home_losses": 0,
                "away_wins":    0, "away_losses": 0,
            })

    print(f"[SOCCER] Total: {len(all_games)} active games, {len(upcoming)} upcoming (no odds yet)")
    result = {"games": all_games, "upcoming": upcoming}
    with _SOCCER_LOCK:
        _SOCCER_CACHE[cache_key] = (result, now + ttl)
    return result


def get_sport_odds(sport: str, regions: str = "us,uk") -> List[Dict]:
    """
    Fetch upcoming game odds for a sport from The Odds API.
    Returns list of game dicts with moneyline, spread, and total odds.
    Returns [] if ODDS_API_KEY is not set.
    """
    api_key = _key()
    if not api_key:
        return []
    sport_key = SPORT_KEYS.get(sport.upper())
    if not sport_key:
        return []

    data = fetch(
        f"{ODDS_BASE}/sports/{sport_key}/odds",
        params={
            "apiKey":      api_key,
            "regions":     regions,
            "markets":     "h2h,spreads,totals",
            "oddsFormat":  "decimal",
            "dateFormat":  "iso",
        },
        timeout=15,
    )
    if not data or not isinstance(data, list):
        return []

    results = []
    for event in data:
        home       = event.get("home_team", "")
        away       = event.get("away_team", "")
        bookmakers = event.get("bookmakers", [])

        # Choose best available bookmaker by priority
        chosen = None
        for pkey in BOOKMAKER_PRIORITY:
            for bm in bookmakers:
                if bm.get("key") == pkey:
                    chosen = bm
                    break
            if chosen:
                break
        if not chosen and bookmakers:
            chosen = bookmakers[0]

        g: Dict = {
            "home_team":      home,
            "away_team":      away,
            "commence":       event.get("commence_time", ""),
            "bookmaker":      chosen.get("title", "") if chosen else "",
            "home_ml":        None,
            "away_ml":        None,
            "home_spread":    None,
            "away_spread":    None,
            "total_line":     None,
            "over_odds":      None,
            "under_odds":     None,
        }

        if chosen:
            for market in chosen.get("markets", []):
                mk       = market.get("key", "")
                outcomes = market.get("outcomes", [])
                if mk == "h2h":
                    for o in outcomes:
                        if o["name"] == home:
                            g["home_ml"] = round(float(o["price"]), 2)
                        elif o["name"] == away:
                            g["away_ml"] = round(float(o["price"]), 2)
                elif mk == "spreads":
                    for o in outcomes:
                        if o["name"] == home:
                            g["home_spread"] = o.get("point")
                        elif o["name"] == away:
                            g["away_spread"] = o.get("point")
                elif mk == "totals":
                    for o in outcomes:
                        if o["name"] == "Over":
                            g["total_line"] = o.get("point")
                            g["over_odds"]  = round(float(o["price"]), 2)
                        elif o["name"] == "Under":
                            g["under_odds"] = round(float(o["price"]), 2)

        results.append(g)
    return results


def build_odds_lookup(sport: str) -> Dict[tuple, Dict]:
    """
    Returns {(_norm(home), _norm(away)): odds_dict} for fuzzy matching.
    """
    return {(_norm(g["home_team"]), _norm(g["away_team"])): g
            for g in get_sport_odds(sport)}


def find_game_odds(
    lookup: Dict[tuple, Dict],
    home_team: str,
    away_team: str,
) -> Optional[Dict]:
    """
    Find odds for a game using normalized team name matching.
    Falls back to partial/substring matching when exact match fails.
    """
    h = _norm(home_team)
    a = _norm(away_team)

    if (h, a) in lookup:
        return lookup[(h, a)]

    # Partial match — covers cases like "Man City" vs "Manchester City"
    for (oh, oa), odds in lookup.items():
        if (h in oh or oh in h) and (a in oa or oa in a):
            return odds
    return None


def _norm(name: str) -> str:
    """Lowercase last meaningful word for fuzzy matching."""
    clean  = re.sub(r"[^a-z0-9 ]", "", name.lower().strip())
    tokens = clean.split()
    return tokens[-1] if tokens else clean


def decimal_to_american(dec: float) -> str:
    """Convert decimal odds to American format string (+150, -160)."""
    if dec >= 2.0:
        return f"+{round((dec - 1) * 100)}"
    return f"{round(-100 / (dec - 1))}"
