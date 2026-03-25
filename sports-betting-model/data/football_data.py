"""
Soccer data layer – Premier League, Champions League, La Liga, Ligue 1.
Primary source : ESPN public API (no key).
Supplementary  : api-football.com (key in API_FOOTBALL_KEY env var).
"""
from __future__ import annotations
import re
import logging
from typing import Any, Dict, List, Optional

from data.fetcher import espn_fetch, api_football_fetch

logger = logging.getLogger(__name__)

# In-process cache for team maps (avoids repeated api-football calls per request)
_teams_cache: Dict[str, Dict[str, int]] = {}


def _norm(name: str) -> str:
    """Normalize team name for fuzzy matching."""
    n = name.lower().strip()
    for suffix in [" fc", " cf", " sc", " ac", " afc", " f.c.", " c.f.", " s.c."]:
        if n.endswith(suffix):
            n = n[: -len(suffix)].strip()
    return re.sub(r"[^a-z0-9 ]", "", n)

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
        comp  = event.get("competitions", [{}])[0]
        teams = {t["homeAway"]: t for t in comp.get("competitors", [])}
        home  = teams.get("home", {})
        away  = teams.get("away", {})

        # Extract competition leaders (top scorers / assisters per team) as player fallback
        leaders: list = []
        _want = {"goals", "assists", "shots", "shotsOnTarget"}
        for cat in comp.get("leaders", []):
            cat_name = cat.get("name", "")
            if cat_name not in _want:
                continue
            for entry in cat.get("leaders", [])[:5]:
                ath = entry.get("athlete", {})
                team_obj = ath.get("team", {})
                team_id = str(team_obj.get("id", "") if isinstance(team_obj, dict) else "")
                leaders.append({
                    "name":     ath.get("displayName", ""),
                    "team_id":  team_id,
                    "stat":     cat_name,
                    "value":    float(entry.get("value", 0) or 0),
                })

        matches.append({
            "id":         event.get("id"),
            "name":       event.get("name", ""),
            "date":       event.get("date", ""),
            "status":     event.get("status", {}).get("type", {}).get("name", ""),
            "home_team":  home.get("team", {}).get("displayName", ""),
            "away_team":  away.get("team", {}).get("displayName", ""),
            "home_score": home.get("score", None),
            "away_score": away.get("score", None),
            "home_id":    str(home.get("team", {}).get("id", "")),
            "away_id":    str(away.get("team", {}).get("id", "")),
            "venue":      comp.get("venue", {}).get("fullName", ""),
            "leaders":    leaders,
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


# ── Per-team player stats (api-football) ─────────────────────────────────────

def get_api_football_teams(league_key: str, season: int = 2024) -> Dict[str, int]:
    """
    Returns {normalized_team_name: api_football_team_id} for a league.
    Cached in-process to avoid repeated calls within a single request.
    """
    cache_key = f"{league_key}_{season}"
    if cache_key in _teams_cache:
        return _teams_cache[cache_key]

    league_id = API_FOOTBALL_IDS.get(league_key.upper())
    if not league_id:
        return {}

    data = api_football_fetch("teams", {"league": league_id, "season": season})
    if not data:
        return {}

    result: Dict[str, int] = {}
    for entry in data.get("response", []):
        team = entry.get("team", {})
        name = team.get("name", "")
        tid  = team.get("id")
        if name and tid:
            result[_norm(name)] = int(tid)

    _teams_cache[cache_key] = result
    return result


def get_team_players(league_key: str, team_name: str, season: int = 2024) -> List[Dict]:
    """
    Fetch ALL players for a specific team from api-football using the
    team-based endpoint (much more efficient than loading the full league).
    Returns [] if API_FOOTBALL_KEY is not set or no match is found.
    """
    teams_map = get_api_football_teams(league_key, season)

    # Exact normalized match first, then substring fallback
    norm_query = _norm(team_name)
    team_id = teams_map.get(norm_query)
    if not team_id:
        for t_norm, tid in teams_map.items():
            if norm_query in t_norm or t_norm in norm_query:
                team_id = tid
                break

    if not team_id:
        logger.debug("No api-football team match for '%s' in %s", team_name, league_key)
        return []

    players: List[Dict] = []
    page = 1
    while True:
        data = api_football_fetch("players", {
            "team":   team_id,
            "season": season,
            "page":   page,
        })
        if not data:
            break

        for entry in data.get("response", []):
            p    = entry.get("player", {})
            sts  = entry.get("statistics", [{}])[0]
            shots = sts.get("shots", {})
            goals = sts.get("goals", {})
            games = sts.get("games", {})
            apps  = games.get("appearences", 0) or 0
            if apps < 1:
                continue
            players.append({
                "id":              p.get("id"),
                "name":            p.get("name"),
                "position":        games.get("position", ""),
                "team":            sts.get("team", {}).get("name"),
                "appearances":     apps,
                "minutes":         games.get("minutes", 0) or 0,
                "goals":           goals.get("total", 0) or 0,
                "assists":         goals.get("assists", 0) or 0,
                "shots_total":     shots.get("total", 0) or 0,
                "shots_on_target": shots.get("on", 0) or 0,
                "xg":              goals.get("xg") or None,
            })

        paging = data.get("paging", {})
        if page >= paging.get("total", 1):
            break
        page += 1

    return sorted(players, key=lambda x: x["shots_total"], reverse=True)


# ── Soccer standings (win% per team per league) ──────────────────────────────

import time as _time
_soccer_standings_cache: Dict[str, Dict] = {}  # {league_key: {"data": {...}, "ts": float}}
_SOCCER_STANDINGS_TTL = 6 * 3600  # 6-hour cache


def get_soccer_win_pcts(league_key: str) -> Dict[str, float]:
    """
    Return {team_name_lower: win_pct} from ESPN soccer standings for *league_key*.
    Cached per-league for 6 hours. Falls back to {} on failure.
    """
    now = _time.time()
    cached = _soccer_standings_cache.get(league_key, {})
    if cached.get("ts", 0) + _SOCCER_STANDINGS_TTL > now:
        return cached.get("data", {})

    slug = SOCCER_LEAGUES.get(league_key, "")
    result: Dict[str, float] = {}
    if not slug:
        return result
    try:
        data = espn_fetch("soccer", slug, "standings")
        if not data:
            return result
        # ESPN standings may be under "children" (by conference/group) or flat
        entries: list = []
        for child in data.get("children", [data]):
            st = child.get("standings", child)
            entries.extend(st.get("entries", []))
        if not entries:
            entries = data.get("standings", {}).get("entries", [])

        for entry in entries:
            team = entry.get("team", {})
            name = (team.get("displayName") or "").lower()
            abbr = (team.get("abbreviation") or "").lower()
            wins = losses = draws = 0
            for s in entry.get("stats", []):
                sname = s.get("name", "")
                if sname == "wins":
                    wins = int(s.get("value", 0) or 0)
                elif sname == "losses":
                    losses = int(s.get("value", 0) or 0)
                elif sname == "ties":
                    draws = int(s.get("value", 0) or 0)
            total = wins + losses + draws
            if total > 0:
                pct = wins / total
                if name:
                    result[name] = pct
                if abbr:
                    result[abbr] = pct
        logger.debug("Soccer standings %s: %d teams", league_key, len(result))
    except Exception as e:
        logger.warning("Soccer standings fetch failed %s: %s", league_key, e)

    _soccer_standings_cache[league_key] = {"data": result, "ts": now}
    return result


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
