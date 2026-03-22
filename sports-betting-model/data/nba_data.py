"""
NBA data layer.
Primary  : ESPN public API  (no key)
Secondary: balldontlie.io   (free, BALLDONTLIE_KEY env var for higher limits)
"""
from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional

from data.fetcher import espn_fetch, bdl_fetch

logger = logging.getLogger(__name__)


# ── Scoreboard / today's games ───────────────────────────────────────────────

def get_games(dates: Optional[str] = None) -> List[Dict]:
    """Return NBA games for today (or *dates* in YYYYMMDD format)."""
    params = {}
    if dates:
        params["dates"] = dates
    data = espn_fetch("basketball", "nba", "scoreboard", params=params)
    if not data:
        return []

    games = []
    for event in data.get("events", []):
        comp  = event.get("competitions", [{}])[0]
        teams = {t["homeAway"]: t for t in comp.get("competitors", [])}
        home  = teams.get("home", {})
        away  = teams.get("away", {})
        odds  = comp.get("odds", [{}])[0] if comp.get("odds") else {}
        games.append({
            "id":              event.get("id"),
            "name":            event.get("name"),
            "date":            event.get("date"),
            "status":          event.get("status", {}).get("type", {}).get("name"),
            "home_team":       home.get("team", {}).get("displayName"),
            "away_team":       away.get("team", {}).get("displayName"),
            "home_score":      home.get("score"),
            "away_score":      away.get("score"),
            "home_id":         home.get("team", {}).get("id"),
            "away_id":         away.get("team", {}).get("id"),
            "spread":          odds.get("spread"),
            "over_under":      odds.get("overUnder"),
            "home_ml":         odds.get("moneyLineOdds"),
        })
    return games


# ── Team stats (ESPN) ────────────────────────────────────────────────────────

def get_team_stats(team_id: str) -> Dict[str, Any]:
    """Pull season stats for an NBA team via ESPN."""
    data = espn_fetch("basketball", "nba", f"teams/{team_id}/statistics")
    if not data:
        return {}
    stats = {}
    for cat in data.get("results", {}).get("stats", {}).get("categories", []):
        for s in cat.get("stats", []):
            stats[s.get("name")] = s.get("value")
    return stats


def get_all_teams() -> List[Dict]:
    """Return list of all NBA teams with id / displayName."""
    data = espn_fetch("basketball", "nba", "teams")
    if not data:
        return []
    teams = []
    for sport in data.get("sports", []):
        for league in sport.get("leagues", []):
            for t in league.get("teams", []):
                team = t.get("team", {})
                teams.append({"id": team.get("id"), "name": team.get("displayName"),
                              "abbreviation": team.get("abbreviation")})
    return teams


# ── Player season averages (Ball Don't Lie) ──────────────────────────────────

def get_season_averages(player_ids: List[int], season: int = 2024) -> List[Dict]:
    """
    Fetch season averages for a list of player IDs from balldontlie.
    Returns list of stat dicts.
    """
    if not player_ids:
        return []

    id_params = "&".join(f"player_ids[]={pid}" for pid in player_ids)
    data = bdl_fetch(f"season_averages?season={season}&{id_params}")
    if not data:
        return []
    return data.get("data", [])


def search_players(name: str) -> List[Dict]:
    """Search players by name on balldontlie."""
    data = bdl_fetch("players", {"search": name, "per_page": 5})
    if not data:
        return []
    return data.get("data", [])


def get_player_game_log(player_id: int, season: int = 2024,
                        last_n: int = 10) -> List[Dict]:
    """Return last N game-log entries for a player."""
    data = bdl_fetch("stats", {
        "player_ids[]": player_id,
        "seasons[]":    season,
        "per_page":     last_n,
        "sort_order":   "desc",
    })
    if not data:
        return []
    return data.get("data", [])


# ── Net-rating helper ────────────────────────────────────────────────────────

def get_team_net_rating(team_id: str) -> float:
    """Return team net rating (pts/100 poss). Falls back to 0.0."""
    stats = get_team_stats(team_id)
    # ESPN may expose 'netRating' or we approximate from offensive / defensive
    if "netRating" in stats:
        return float(stats["netRating"])
    off = stats.get("offensiveRating", 110.0)
    dft = stats.get("defensiveRating", 110.0)
    return float(off) - float(dft)
