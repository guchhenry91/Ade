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
