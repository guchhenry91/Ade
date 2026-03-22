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
import logging
from typing import Dict, List, Optional

from data.fetcher import fetch

logger = logging.getLogger(__name__)

ODDS_BASE = "https://api.the-odds-api.com/v4"

SPORT_KEYS: Dict[str, str] = {
    "NBA":  "basketball_nba",
    "NFL":  "americanfootball_nfl",
    "EPL":  "soccer_epl",
    "UCL":  "soccer_uefa_champs_league",
    "LIGA": "soccer_spain_la_liga",
    "L1":   "soccer_france_ligue_one",
}

# Preferred bookmakers in display priority order
BOOKMAKER_PRIORITY = [
    "fanduel", "draftkings", "betmgm", "bet365",
    "betrivers", "williamhill_us", "bovada",
]


def _key() -> str:
    return os.getenv("ODDS_API_KEY", "")


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
