"""
Soccer data layer – Premier League, Champions League, La Liga, Ligue 1.
Primary source : ESPN public API (no key).
Supplementary  : api-football.com (key in API_FOOTBALL_KEY env var).
"""
from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional

from data.fetcher import espn_fetch, api_football_fetch

logger = logging.getLogger(__name__)

# ESPN slug → league metadata
SOCCER_LEAGUES = {
    "EPL":  "eng.1",
    "UCL":  "uefa.champions",
    "LIGA": "esp.1",
    "L1":   "fra.1",
}

API_FOOTBALL_IDS = {
    "EPL": 39, "UCL": 2, "LIGA": 140, "L1": 61,
}


# ── Fixtures / scoreboard ────────────────────────────────────────────────────

def get_fixtures(league_key: str, dates: Optional[str] = None) -> List[Dict]:
    """
    Return today's (or supplied *dates*) scoreboard for *league_key*.
    Returns a flat list of match dicts.
    """
    slug  = SOCCER_LEAGUES.get(league_key.upper())
    if not slug:
        logger.error("Unknown league key: %s", league_key)
        return []

    params = {}
    if dates:
        params["dates"] = dates   # ESPN format: YYYYMMDD or YYYYMMDD-YYYYMMDD

    data = espn_fetch("soccer", slug, "scoreboard", params=params)
    if not data:
        return []

    matches = []
    for event in data.get("events", []):
        comp = event.get("competitions", [{}])[0]
        teams = {t["homeAway"]: t for t in comp.get("competitors", [])}
        home = teams.get("home", {})
        away = teams.get("away", {})
        matches.append({
            "id":         event.get("id"),
            "name":       event.get("name", ""),
            "date":       event.get("date", ""),
            "status":     event.get("status", {}).get("type", {}).get("name", ""),
            "home_team":  home.get("team", {}).get("displayName", ""),
            "away_team":  away.get("team", {}).get("displayName", ""),
            "home_score": home.get("score", None),
            "away_score": away.get("score", None),
            "home_id":    home.get("team", {}).get("id"),
            "away_id":    away.get("team", {}).get("id"),
            "venue":      comp.get("venue", {}).get("fullName", ""),
        })
    return matches


# ── Team season statistics (ESPN) ────────────────────────────────────────────

def get_team_stats(team_id: str, league_key: str) -> Dict[str, Any]:
    """Fetch season statistics for *team_id* from ESPN."""
    slug = SOCCER_LEAGUES.get(league_key.upper(), "eng.1")
    data = espn_fetch("soccer", slug, f"teams/{team_id}/statistics")
    if not data:
        return {}
    stats = {}
    for cat in data.get("results", {}).get("stats", {}).get("categories", []):
        cat_name = cat.get("name", "")
        for s in cat.get("stats", []):
            stats[f"{cat_name}.{s.get('name')}"] = s.get("value")
    return stats


# ── xG and shot data from api-football ───────────────────────────────────────

def get_xg_data(league_key: str, season: int = 2024) -> List[Dict]:
    """
    Pull xG stats per fixture from api-football.
    Requires API_FOOTBALL_KEY env var; returns [] otherwise.
    """
    league_id = API_FOOTBALL_IDS.get(league_key.upper())
    if not league_id:
        return []

    data = api_football_fetch("fixtures", {
        "league": league_id,
        "season": season,
        "last":   10,
    })
    if not data:
        return []

    results = []
    for fix in data.get("response", []):
        teams  = fix.get("teams", {})
        goals  = fix.get("goals", {})
        score  = fix.get("score", {})
        # xG not always present – fall back to None
        xg_h = fix.get("xg", {}).get("home") if fix.get("xg") else None
        xg_a = fix.get("xg", {}).get("away") if fix.get("xg") else None
        results.append({
            "fixture_id":  fix.get("fixture", {}).get("id"),
            "home_team":   teams.get("home", {}).get("name"),
            "away_team":   teams.get("away", {}).get("name"),
            "home_goals":  goals.get("home"),
            "away_goals":  goals.get("away"),
            "home_xg":     xg_h,
            "away_xg":     xg_a,
        })
    return results


def get_player_stats(league_key: str, season: int = 2024,
                     page: int = 1) -> List[Dict]:
    """
    Player stats (shots, goals, xG) from api-football.
    Returns a list of player stat dicts.
    """
    league_id = API_FOOTBALL_IDS.get(league_key.upper())
    if not league_id:
        return []

    data = api_football_fetch("players", {
        "league": league_id,
        "season": season,
        "page":   page,
    })
    if not data:
        return []

    players = []
    for entry in data.get("response", []):
        p   = entry.get("player", {})
        sts = entry.get("statistics", [{}])[0]
        shots  = sts.get("shots", {})
        goals  = sts.get("goals", {})
        games  = sts.get("games", {})
        players.append({
            "id":              p.get("id"),
            "name":            p.get("name"),
            "team":            sts.get("team", {}).get("name"),
            "appearances":     games.get("appearences", 0) or 0,
            "minutes":         games.get("minutes", 0) or 0,
            "goals":           goals.get("total", 0) or 0,
            "assists":         goals.get("assists", 0) or 0,
            "shots_total":     shots.get("total", 0) or 0,
            "shots_on_target": shots.get("on", 0) or 0,
            "xg":              goals.get("xg") or None,
        })
    return players


# ── Team form / head-to-head helper ─────────────────────────────────────────

def compute_team_xg_averages(xg_data: List[Dict], team_name: str) -> Dict[str, float]:
    """Average xG scored and conceded from recent fixtures for *team_name*."""
    scored    = []
    conceded  = []
    for fix in xg_data:
        if fix["home_team"] == team_name and fix["home_xg"] is not None:
            scored.append(float(fix["home_xg"]))
            if fix["away_xg"] is not None:
                conceded.append(float(fix["away_xg"]))
        elif fix["away_team"] == team_name and fix["away_xg"] is not None:
            scored.append(float(fix["away_xg"]))
            if fix["home_xg"] is not None:
                conceded.append(float(fix["home_xg"]))
    return {
        "avg_xg_scored":   sum(scored)   / len(scored)   if scored   else 1.2,
        "avg_xg_conceded": sum(conceded) / len(conceded) if conceded else 1.2,
    }
