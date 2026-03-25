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
            "player_blocked_shots",
        ],
        "stat_map": {
            "player_points":            "points",
            "player_goals":             "goals",
            "player_assists":           "assists",
            "player_shots_on_goal":     "shots",
            "player_power_play_points": "pp_points",
            "player_blocked_shots":     "blocked_shots",
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
    "blocked_shots": "Blocked Shots",
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

def get_prop_grade(confidence: float, price: float = -110, prop: Dict = None) -> Tuple[str, float]:
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
    """Bet quality score 0-100 using edge, confidence, market reliability, liquidity, price."""
    score = 0
    conf = prop.get("confidence", 50)
    edge = prop.get("edge", 0)
    stat = prop.get("stat", "")
    price = prop.get("price", -110)
    all_prices = prop.get("all_prices", {})
    # Confidence (35pts)
    if conf >= 80:     score += 35
    elif conf >= 72:   score += 28
    elif conf >= 65:   score += 21
    elif conf >= 58:   score += 14
    else:              score += 7
    # Edge (30pts)
    if edge >= 12:     score += 30
    elif edge >= 8:    score += 24
    elif edge >= 5:    score += 18
    elif edge >= 3:    score += 12
    else:              score += 4
    # Market reliability (20pts)
    reliability = MARKET_RELIABILITY.get(stat, 0.75)
    score += round(reliability * 20)
    # Liquidity (10pts)
    n_books = len(all_prices)
    if n_books >= 4:    score += 10
    elif n_books >= 3:  score += 8
    elif n_books >= 2:  score += 5
    else:               score += 2
    # Price discipline (5pts)
    if price >= -115:   score += 5
    elif price >= -130: score += 4
    elif price >= -150: score += 3
    elif price >= -200: score += 2
    elif price >= -300: score += 1
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
    playable = prop.get("playable_to_raw", -150)
    try:
        playable = float(playable)
    except:
        playable = -150
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


# ════════════════════════════════════════════════════════════════════════════════
# PART 4b — MARKET CONSTANTS + FILTERING HELPERS
# ════════════════════════════════════════════════════════════════════════════════

MARKET_RELIABILITY: Dict[str, float] = {
    # NBA
    "player_points":        0.90,
    "player_rebounds":      0.85,
    "player_assists":       0.85,
    "player_threes":        0.80,
    "player_steals":        0.70,
    "player_blocks":        0.70,
    "player_turnovers":     0.65,
    "player_pts_rebs_asts": 0.75,
    "player_pts_rebs":      0.75,
    "player_pts_asts":      0.75,
    "player_rebs_asts":     0.75,
    # MLB
    "batter_hits":          0.85,
    "batter_home_runs":     0.70,
    "batter_rbis":          0.75,
    "batter_runs_scored":   0.75,
    "batter_strikeouts":    0.80,
    "batter_walks":         0.70,
    "batter_hits_runs_rbis":0.70,
    "batter_total_bases":   0.80,
    "pitcher_strikeouts":   0.90,
    "pitcher_hits_allowed": 0.80,
    "pitcher_walks":        0.75,
    "pitcher_earned_runs":  0.75,
    "pitcher_outs":         0.85,
    # NHL
    "player_shots_on_goal": 0.85,
    "player_goals":         0.70,
    "player_power_play_points": 0.65,
    "player_blocked_shots": 0.70,
    # Specialty
    "first_basket":         0.40,
    "double_double":        0.50,
    "triple_double":        0.45,
    "first_goal":           0.40,
    "anytime_goal":         0.55,
}

SPECIALTY_MARKETS: set = {
    "first_basket",
    "double_double",
    "triple_double",
    "first_goal",
    "anytime_goal",
}


def get_market_reliability(stat: str) -> float:
    """Return reliability weight for a stat market (0.0–1.0)."""
    return MARKET_RELIABILITY.get(stat, 0.75)


def is_specialty_market(stat: str) -> bool:
    """Return True if stat is a specialty/novelty market."""
    return stat in SPECIALTY_MARKETS


def is_bettable(prop: Dict, max_juice: int = -350) -> bool:
    """Return True if prop passes basic bettability checks."""
    price = prop.get("price", -110)
    pick = prop.get("pick", "")
    if pick == "FAIR":
        return False
    if isinstance(price, (int, float)) and price < 0 and price < max_juice:
        return False
    return True


def dedupe_by_player(props: List[Dict], max_per_player: int = 2) -> List[Dict]:
    """Max 2 props per player. Core markets preferred over specialty."""
    seen: Dict[str, int] = {}
    # Non-specialty first (preserves score order within each group)
    sorted_props = sorted(
        props,
        key=lambda p: (1 if is_specialty_market(p.get("stat", "")) else 0, -p.get("score", 0)),
    )
    result = []
    for prop in sorted_props:
        name = prop.get("player_name", "")
        count = seen.get(name, 0)
        if count < max_per_player:
            result.append(prop)
            seen[name] = count + 1
    return result


def apply_game_cap(props: List[Dict], max_per_game: int = 3) -> Tuple[List[Dict], List[Dict]]:
    """Return (capped, overflow). Max props per game in default view."""
    game_counts: Dict[str, int] = {}
    capped: List[Dict] = []
    overflow: List[Dict] = []
    for prop in props:
        game = prop.get("game", "")
        count = game_counts.get(game, 0)
        if count < max_per_game:
            capped.append(prop)
            game_counts[game] = count + 1
        else:
            overflow.append(prop)
    return capped, overflow


def get_mycard_props(
    games: List[Dict],
    sport_key: str = "",
    sport_emoji: str = "",
    sport_label: str = "",
) -> List[Dict]:
    """Full filter pipeline: bettable → no specialty → no Pass → dedupe → sort by score → top 15."""
    all_props: List[Dict] = []
    for game in games:
        game_label = f"{game.get('away_abbr', '')} @ {game.get('home_abbr', '')}"
        for player in game.get("home_roster", []) + game.get("away_roster", []):
            for prop in player.get("props", []):
                p = {
                    **prop,
                    "sport_key":   sport_key,
                    "sport_emoji": sport_emoji,
                    "sport_label": sport_label,
                    "player_name": player.get("name", prop.get("player_name", "")),
                    "team":        player.get("team", prop.get("team", "")),
                    "game":        game_label,
                }
                all_props.append(p)
    all_props = [p for p in all_props if is_bettable(p)]
    all_props = [p for p in all_props if not is_specialty_market(p.get("stat", ""))]
    all_props = [p for p in all_props if p.get("grade", "Pass") != "Pass"]
    all_props.sort(key=lambda x: x.get("score", 0), reverse=True)
    all_props = dedupe_by_player(all_props)
    return all_props[:15]


def get_sharp_props(
    games: List[Dict],
    sport_key: str = "",
    sport_emoji: str = "",
    sport_label: str = "",
) -> List[Dict]:
    """A grade only, -300 max juice, edge≥6, max 2/game, max 1/player."""
    all_props: List[Dict] = []
    for game in games:
        game_label = f"{game.get('away_abbr', '')} @ {game.get('home_abbr', '')}"
        for player in game.get("home_roster", []) + game.get("away_roster", []):
            for prop in player.get("props", []):
                p = {
                    **prop,
                    "sport_key":   sport_key,
                    "sport_emoji": sport_emoji,
                    "sport_label": sport_label,
                    "player_name": player.get("name", prop.get("player_name", "")),
                    "team":        player.get("team", prop.get("team", "")),
                    "game":        game_label,
                }
                all_props.append(p)
    all_props = [p for p in all_props if is_bettable(p, max_juice=-300)]
    all_props = [p for p in all_props if not is_specialty_market(p.get("stat", ""))]
    all_props = [p for p in all_props if p.get("grade") == "A"]
    all_props = [p for p in all_props if p.get("edge", 0) >= 6]
    all_props.sort(key=lambda x: x.get("score", 0), reverse=True)
    # Max 1/player
    seen_players: set = set()
    deduped: List[Dict] = []
    for p in all_props:
        pname = p.get("player_name", "")
        if pname not in seen_players:
            deduped.append(p)
            seen_players.add(pname)
    # Max 2/game
    game_counts: Dict[str, int] = {}
    result: List[Dict] = []
    for p in deduped:
        g = p.get("game", "")
        if game_counts.get(g, 0) < 2:
            result.append(p)
            game_counts[g] = game_counts.get(g, 0) + 1
    return result


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

def assign_players_to_teams(
    player_data: dict,
    home_team: str, home_abbr: str,
    away_team: str, away_abbr: str,
) -> tuple:
    """Assign players to home/away teams without ESPN roster lookups.

    Strategy:
    1. Check if player name contains a team hint like "LaMelo Ball (CHA)"
    2. Fall back to even split: whoever has fewer players gets the next one.
    Returns (home_players, away_players) lists.
    """
    home_players: list = []
    away_players: list = []

    for pdata in player_data.values():
        name = pdata["name"]
        team = None

        # Check for embedded team abbreviation hint "(ABBR)"
        if "(" in name and ")" in name:
            hint = name[name.find("(") + 1: name.find(")")]
            if hint == home_abbr:
                team = home_abbr
            elif hint == away_abbr:
                team = away_abbr
            # Strip hint from display name
            pdata["name"] = name[: name.find("(")].strip()
            for p in pdata.get("props", []):
                p["player_name"] = pdata["name"]

        # Even split fallback
        if not team:
            team = home_abbr if len(home_players) <= len(away_players) else away_abbr

        pdata["team"] = team
        for p in pdata.get("props", []):
            p["team"] = team

        if team == home_abbr:
            home_players.append(pdata)
        else:
            away_players.append(pdata)

    return home_players, away_players


# Keep private alias for any remaining internal references
_assign_players_to_teams = assign_players_to_teams


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
    total_events = min(len(events), 15)
    print(f"[ODDS] {sport_name}: processing {total_events} events")

    for i, event in enumerate(events[:15]):
        if i > 0 and i % 3 == 0:
            time.sleep(0.5)

        try:
            event_id  = event.get("id", "")
            home_team = event.get("home_team", "")
            away_team = event.get("away_team", "")
            print(f"[ODDS] {sport_name}: event {i+1}/{total_events} - {away_team} @ {home_team}")
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
                    "top_props":       [],
                    "more_props":      [],
                    "specialty_props": [],
                    "top_pick":        None,
                    "is_soccer":    "soccer" in sport_name,
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
                print(f"[ODDS] {sport_name}: event {i+1} - no props")
                continue

            # Step 4: Build player props with grading + scoring
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
                    "playable_to":     format_american(playable),
                    "playable_to_raw": float(playable),
                    "sport":          sport_name,
                    "game":           f"{away_abbr} @ {home_abbr}",
                    "avg":            line,
                    "stars":          5 if display_conf >= 85 else 4 if display_conf >= 78 else 3 if display_conf >= 68 else 2 if display_conf >= 58 else 1,
                    "conf_label":     "Elite Pick" if display_conf >= 85 else "Strong Pick" if display_conf >= 78 else "Good Pick" if display_conf >= 68 else "Moderate" if display_conf >= 58 else "Use Caution",
                    "trend":          "->",
                }
                prop["is_specialty"]       = is_specialty_market(stat)
                prop["market_reliability"] = get_market_reliability(stat)
                prop["score"]              = calculate_bet_score(prop)
                prop["is_bettable"]        = is_bettable(prop)
                prop["score_label"], prop["score_class"] = get_score_label(prop["score"])
                prop["stake_rec"] = get_stake_rec(grade, edge, prop["score"])
                prop["red_flags"] = get_red_flags(prop)
                prop["reasons"]   = get_bet_reasons(prop)

                if player_name not in player_data:
                    player_data[player_name] = {"name": player_name, "props": []}
                player_data[player_name]["props"].append(prop)

            # Step 5: Sort each player's props by confidence
            for pdata in player_data.values():
                pdata["props"].sort(key=lambda x: x["confidence"], reverse=True)

            # Step 6: Assign players to teams (no ESPN roster calls — even split)
            home_players, away_players = assign_players_to_teams(
                player_data, home_team, home_abbr, away_team, away_abbr,
            )

            def _best_conf(player):
                return player["props"][0]["confidence"] if player["props"] else 0
            home_players.sort(key=_best_conf, reverse=True)
            away_players.sort(key=_best_conf, reverse=True)

            # Top props: score-sorted, filtered, capped
            all_game_props: list = []
            for pdata in player_data.values():
                all_game_props.extend(pdata["props"])
            all_game_props.sort(key=lambda x: x.get("score", 0), reverse=True)

            # Core props: bettable, non-specialty, non-Pass
            core_props = [
                p for p in all_game_props
                if p.get("is_bettable") and not p.get("is_specialty") and p.get("grade") != "Pass"
            ]
            core_props = dedupe_by_player(core_props)
            top_props, more_props = apply_game_cap(core_props, max_per_game=3)

            # Specialty props: bettable specialty market props
            specialty_props = [
                p for p in all_game_props
                if p.get("is_bettable") and p.get("is_specialty")
            ]

            # Top pick: best non-specialty bettable prop
            non_spec = [p for p in all_game_props if p.get("is_bettable") and not p.get("is_specialty")]
            top_pick = non_spec[0] if non_spec else (all_game_props[0] if all_game_props else None)

            print(f"[ODDS] {sport_name}: event {i+1} done - {len(player_data)} players, {len(top_props)} props")

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
                "top_props":       top_props,
                "more_props":      more_props,
                "specialty_props": specialty_props,
                "top_pick":        top_pick,
                "is_soccer":    "soccer" in sport_name,
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

        except Exception as _ev_err:
            import traceback as _tb
            print(f"[ODDS] {sport_name}: event {i+1} FAILED - {_ev_err}")
            _tb.print_exc()
            continue

    games_with = sum(1 for g in results if g.get("top_props"))
    print(f"[ODDS] {sport_name}: complete - {len(results)} games built, {games_with} with props")
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


def fetch_soccer_game_data(
    sport_key: str, event_id: str, home_team: str, away_team: str
) -> dict:
    """Fetch full match data for a single soccer game.

    Returns a game dict with:
      home_prob, draw_prob, away_prob,
      btts_yes_prob, total_goals_line, over_goals_prob,
      correct_scores (top 8 [{score, prob, odds}]),
      home_players / away_players (player card format),
      pick_badge_label, pick_badge_class, match_pick,
      book_home_ml, book_draw_ml, book_away_ml.
    """
    api_key = _key()
    game_data: dict = {
        "event_id":         event_id,
        "home_team":        home_team,
        "away_team":        away_team,
        "home_prob":        50.0,
        "draw_prob":        25.0,
        "away_prob":        25.0,
        "btts_yes_prob":    None,
        "total_goals_line": None,
        "over_goals_prob":  None,
        "correct_scores":   [],
        "home_players":     [],
        "away_players":     [],
        "match_pick":       None,
        "pick_badge_label": "\u2696\ufe0f Pick'em",
        "pick_badge_class": "badge-pickem",
        "book_home_ml":     None,
        "book_draw_ml":     None,
        "book_away_ml":     None,
    }

    # ── FETCH 1: Match odds (h2h + btts + totals) ────────────────────────────
    try:
        r = _requests.get(
            f"{ODDS_BASE}/sports/{sport_key}/events/{event_id}/odds",
            params={
                "apiKey":     api_key,
                "regions":    "us",
                "markets":    "h2h,btts,totals",
                "oddsFormat": "american",
            },
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            for bk in data.get("bookmakers", [])[:1]:
                for market in bk.get("markets", []):
                    mk = market.get("key", "")
                    if mk == "h2h":
                        raw: dict = {}
                        for o in market.get("outcomes", []):
                            t = o.get("name", "")
                            p = float(o.get("price", 0) or 0)
                            imp = _american_to_implied(p)
                            raw[t] = imp
                            if t == home_team:
                                game_data["book_home_ml"] = _fmt_american(p)
                            elif t == away_team:
                                game_data["book_away_ml"] = _fmt_american(p)
                            elif t.lower() == "draw":
                                game_data["book_draw_ml"] = _fmt_american(p)
                        total = sum(raw.values())
                        if total > 0:
                            hp = round(raw.get(home_team, 0) / total * 100, 1)
                            dp_raw = raw.get("Draw", 0)
                            dp = round(dp_raw / total * 100, 1)
                            ap = round(100.0 - hp - dp, 1)
                            game_data["home_prob"] = hp
                            game_data["draw_prob"] = dp
                            game_data["away_prob"] = ap
                    elif mk == "btts":
                        raw_b: dict = {}
                        for o in market.get("outcomes", []):
                            n = o.get("name", "").lower()
                            p = float(o.get("price", 0) or 0)
                            raw_b[n] = _american_to_implied(p)
                        total_b = sum(raw_b.values())
                        if total_b > 0:
                            game_data["btts_yes_prob"] = round(
                                raw_b.get("yes", 0) / total_b * 100, 1
                            )
                    elif mk == "totals":
                        for o in market.get("outcomes", []):
                            if o.get("name") == "Over":
                                game_data["total_goals_line"] = o.get("point", 2.5)
                                p = float(o.get("price", -110) or -110)
                                prob = _american_to_implied(p)
                                game_data["over_goals_prob"] = round(prob * 100, 1)
    except Exception as e:
        print(f"[SOCCER] {home_team} vs {away_team}: match odds failed: {e}")

    # ── Match pick badge ──────────────────────────────────────────────────────
    hp = game_data["home_prob"]
    ap = game_data["away_prob"]
    dp = game_data["draw_prob"]
    best_prob = max(hp, ap, dp)
    if hp == best_prob:
        pick_team = home_team.split()[-1]
        game_data["match_pick"] = home_team
    elif ap == best_prob:
        pick_team = away_team.split()[-1]
        game_data["match_pick"] = away_team
    else:
        pick_team = "Draw"
        game_data["match_pick"] = "Draw"

    if best_prob >= 70:
        game_data["pick_badge_label"] = f"\U0001f525 Strong Fav \u00b7 {pick_team}"
        game_data["pick_badge_class"] = "badge-strong"
    elif best_prob >= 60:
        game_data["pick_badge_label"] = f"\u2705 Favoured \u00b7 {pick_team}"
        game_data["pick_badge_class"] = "badge-favoured"
    elif best_prob >= 55:
        game_data["pick_badge_label"] = f"\U0001f4ca Slight Edge \u00b7 {pick_team}"
        game_data["pick_badge_class"] = "badge-slight"

    # ── FETCH 2: Correct score ────────────────────────────────────────────────
    try:
        r2 = _requests.get(
            f"{ODDS_BASE}/sports/{sport_key}/events/{event_id}/odds",
            params={
                "apiKey":     api_key,
                "regions":    "us",
                "markets":    "correct_score",
                "oddsFormat": "american",
            },
            timeout=10,
        )
        if r2.status_code == 200:
            data2 = r2.json()
            scores: list = []
            for bk in data2.get("bookmakers", [])[:1]:
                for market in bk.get("markets", []):
                    if market.get("key") != "correct_score":
                        continue
                    for o in market.get("outcomes", []):
                        price = float(o.get("price", 0) or 0)
                        if price <= 0:
                            continue
                        prob = _american_to_implied(price)
                        scores.append({
                            "score": o.get("name", ""),
                            "prob":  round(prob * 100, 1),
                            "odds":  f"+{round(price)}",
                        })
            scores.sort(key=lambda x: x["prob"], reverse=True)
            game_data["correct_scores"] = scores[:8]
    except Exception as e:
        print(f"[SOCCER] {home_team} vs {away_team}: correct score failed: {e}")

    # ── FETCH 3: Player props ─────────────────────────────────────────────────
    _SOCCER_PLAYER_MARKETS = [
        "player_goal_scorer",
        "player_first_goal_scorer",
        "player_shots_on_target",
        "player_shots",
        "player_assists",
    ]
    _GOAL_MARKETS_SET = {"player_goal_scorer", "player_first_goal_scorer"}
    _SOCCER_STAT_MAP = {
        "player_goal_scorer":       "anytime_goal",
        "player_first_goal_scorer": "first_goal",
        "player_shots_on_target":   "shots_on_target",
        "player_shots":             "total_shots",
        "player_assists":           "assists",
    }
    try:
        r3 = _requests.get(
            f"{ODDS_BASE}/sports/{sport_key}/events/{event_id}/odds",
            params={
                "apiKey":     api_key,
                "regions":    "us",
                "markets":    ",".join(_SOCCER_PLAYER_MARKETS),
                "oddsFormat": "american",
            },
            timeout=10,
        )
        if r3.status_code == 200:
            data3 = r3.json()
            player_props_map: Dict[str, Dict] = {}
            for bk in data3.get("bookmakers", [])[:2]:
                bk_key = bk.get("key", "")
                for market in bk.get("markets", []):
                    mk   = market.get("key", "")
                    stat = _SOCCER_STAT_MAP.get(mk)
                    if not stat:
                        continue
                    is_goal = mk in _GOAL_MARKETS_SET
                    for o in market.get("outcomes", []):
                        oname = o.get("name", "")
                        price = float(o.get("price", -110) or -110)
                        line  = float(o.get("point", 0.5) or 0.5)
                        if is_goal:
                            pname = oname
                            line  = 0.5
                        else:
                            if oname != "Over":
                                continue
                            pname = (o.get("description") or "").strip()
                            if not pname:
                                continue

                        prob = _american_to_implied(price)
                        conf = round(prob * 100, 1)

                        if is_goal:
                            pick = "SCORE"
                        elif conf >= 55:
                            pick = "OVER"
                        elif conf <= 45:
                            pick = "UNDER"
                            conf = round((1.0 - prob) * 100, 1)
                        else:
                            pick = "FAIR"

                        grade = "A" if conf >= 68 else ("B" if conf >= 60 else "Watch")
                        prop_entry = {
                            "stat":        stat,
                            "label":       STAT_LABELS.get(stat, stat),
                            "line":        line,
                            "pick":        pick,
                            "confidence":  conf,
                            "over_prob":   round(prob * 100, 1),
                            "under_prob":  round((1.0 - prob) * 100, 1),
                            "price":       price,
                            "grade":       grade,
                            "edge":        round(conf - 50, 1),
                            "score":       int(conf),
                            "score_class": "elite" if conf >= 80 else ("strong" if conf >= 70 else "playable"),
                            "stake_rec":   "0.5u" if conf >= 68 else "0.25u",
                            "best_book":   bk_key,
                            "playable_to": "-130",
                            "red_flags":   [],
                            "reasons":     [f"{conf}% probability"],
                            "sport":       "soccer",
                        }
                        if pname not in player_props_map:
                            player_props_map[pname] = {"name": pname, "props": []}
                        # Only add if not already present for this stat
                        existing_stats = {pp["stat"] for pp in player_props_map[pname]["props"]}
                        if stat not in existing_stats:
                            player_props_map[pname]["props"].append(prop_entry)

            # Even-split into home/away
            plist = list(player_props_map.values())
            home_p: list = []
            away_p: list = []
            home_abbr_s = home_team[:3].upper()
            away_abbr_s = away_team[:3].upper()
            for idx, p in enumerate(plist):
                if len(home_p) <= len(away_p):
                    p["team"] = home_abbr_s
                    home_p.append(p)
                else:
                    p["team"] = away_abbr_s
                    away_p.append(p)
                for prop in p.get("props", []):
                    prop["team"] = p["team"]

            game_data["home_players"] = home_p[:12]
            game_data["away_players"] = away_p[:12]
            print(f"[SOCCER] {home_team} vs {away_team}: {len(plist)} players")
    except Exception as e:
        print(f"[SOCCER] {home_team} vs {away_team}: player props failed: {e}")

    return game_data


def build_soccer_props(ttl: int = 900) -> dict:
    """Build soccer match data for all leagues from Odds API only.

    Returns {"games": [league_blocks], "upcoming": [fixture_dicts]}
    where each league_block = {"league": ..., "sport_key": ..., "games": [...]}
    and each game dict has the full fetch_soccer_game_data() structure plus
    today.html-compat fields (home_roster / away_roster, players).
    """
    api_key = _key()
    if not api_key:
        logger.warning("[SOCCER] ODDS_API_KEY not set — no soccer props")
        return {"games": [], "upcoming": []}

    cache_key = "soccer_all"
    now = time.time()
    with _SOCCER_LOCK:
        cached = _SOCCER_CACHE.get(cache_key)
        if cached:
            data, expires = cached
            if now < expires:
                return data

    all_league_blocks: list = []
    upcoming: list = []

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

        league_games: list = []
        for event in events[:10]:
            event_id  = event.get("id", "")
            home_team = event.get("home_team", "")
            away_team = event.get("away_team", "")
            if not home_team or not away_team or not event_id:
                continue

            try:
                game = fetch_soccer_game_data(sport_key, event_id, home_team, away_team)
            except Exception as _e:
                print(f"[SOCCER] {home_team} vs {away_team} failed: {_e}")
                continue

            # If no odds posted yet, treat as upcoming
            if game["home_prob"] == 50.0 and game["draw_prob"] == 25.0:
                upcoming.append({
                    "home_team":  home_team,
                    "away_team":  away_team,
                    "league":     league_name,
                    "sport_icon": icon,
                    "kickoff":    event.get("commence_time", ""),
                })
                continue

            # Predicted winner
            hp = game["home_prob"]
            dp = game["draw_prob"]
            ap = game["away_prob"]
            if hp >= dp and hp >= ap:
                predicted_winner, win_prob_val = home_team, hp
            elif dp >= ap:
                predicted_winner, win_prob_val = "Draw", dp
            else:
                predicted_winner, win_prob_val = away_team, ap

            conf_str = "HIGH" if win_prob_val >= 55 else ("MEDIUM" if win_prob_val >= 45 else "LOW")

            # today.html compat: build home_roster / away_roster from home_players / away_players
            def _to_today_roster(plist: list) -> list:
                out = []
                for p in plist:
                    best_prop = next(
                        (pp for pp in p.get("props", []) if pp.get("stat") == "anytime_goal"),
                        p.get("props", [{}])[0] if p.get("props") else {},
                    )
                    gp = best_prop.get("confidence", 0) if best_prop.get("stat") == "anytime_goal" else 0
                    out.append({
                        "name":       p["name"],
                        "shots_pg":   None,
                        "xg_shot":    None,
                        "goal_prob":  gp,
                        "confidence": "HIGH" if gp > 35 else ("MEDIUM" if gp > 20 else "LOW"),
                    })
                return out

            # Merge everything into a single game dict
            game.update({
                "sport":            "Soccer",
                "league":           league_name,
                "sport_icon":       icon,
                "sport_key":        sport_key,
                "is_soccer":        True,
                "kickoff":          event.get("commence_time", ""),
                "game_time":        event.get("commence_time", ""),
                "status":           "STATUS_SCHEDULED",
                "home_score":       None,
                "away_score":       None,
                "home_abbr":        home_team[:3].upper(),
                "away_abbr":        away_team[:3].upper(),
                "spread":           None,
                "over_under":       game.get("total_goals_line"),
                "book_total":       game.get("total_goals_line"),
                "predicted_winner": predicted_winner,
                "win_prob":         win_prob_val,
                "confidence":       conf_str,
                "home_wins":        0, "home_losses": 0,
                "away_wins":        0, "away_losses": 0,
                # Legacy compat fields for today.html
                "match_pick_label": game.get("pick_badge_label", ""),
                "players":          game.get("home_players", []) + game.get("away_players", []),
                "home_roster":      _to_today_roster(game.get("home_players", [])),
                "away_roster":      _to_today_roster(game.get("away_players", [])),
            })
            league_games.append(game)

        if league_games:
            all_league_blocks.append({
                "league":    league_name,
                "sport_key": sport_key,
                "icon":      icon,
                "games":     league_games,
            })

    total_games = sum(len(b["games"]) for b in all_league_blocks)
    print(f"[SOCCER] Total: {total_games} active games, {len(upcoming)} upcoming (no odds yet)")
    result = {"games": all_league_blocks, "upcoming": upcoming}
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
