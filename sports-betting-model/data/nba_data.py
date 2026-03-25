"""
NBA data layer.
Primary: ESPN public API (no key, no rate limits) for schedule/scoreboard/standings.
Player props are now sourced exclusively from The Odds API via odds_api.build_sport_props().
"""
from __future__ import annotations
import re
import time
import logging
from typing import Any, Dict, List, Optional

from data.fetcher import espn_fetch

logger = logging.getLogger(__name__)


def _espn_team_id(team_obj) -> str:
    """Extract team_id from ESPN team object — handles $ref URL references."""
    if not isinstance(team_obj, dict):
        return ""
    if "$ref" in team_obj:
        m = re.search(r'/teams/(\d+)', team_obj["$ref"])
        return m.group(1) if m else ""
    return str(team_obj.get("id", ""))


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

        def _record_wins_losses(competitor: dict) -> tuple:
            for rec in competitor.get("records", []):
                if rec.get("type") in ("total", "overall"):
                    summary = rec.get("summary", "")
                    try:
                        parts = summary.split("-")
                        return int(parts[0]), int(parts[1])
                    except (IndexError, ValueError):
                        pass
            return 0, 0

        home_w, home_l = _record_wins_losses(home)
        away_w, away_l = _record_wins_losses(away)

        games.append({
            "id":              event.get("id"),
            "name":            event.get("name"),
            "date":            event.get("date"),
            "status":          event.get("status", {}).get("type", {}).get("name"),
            "home_team":       home.get("team", {}).get("displayName"),
            "away_team":       away.get("team", {}).get("displayName"),
            "home_abbr":       home.get("team", {}).get("abbreviation", ""),
            "away_abbr":       away.get("team", {}).get("abbreviation", ""),
            "home_score":      home.get("score"),
            "away_score":      away.get("score"),
            "home_id":         home.get("team", {}).get("id"),
            "away_id":         away.get("team", {}).get("id"),
            "spread":          odds.get("spread"),
            "over_under":      odds.get("overUnder"),
            "home_ml":         odds.get("moneyLineOdds"),
            "home_wins":       home_w,
            "home_losses":     home_l,
            "away_wins":       away_w,
            "away_losses":     away_l,
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


# ── Standings (win% per team) ─────────────────────────────────────────────────

_standings_cache: dict = {}
_STANDINGS_TTL = 6 * 3600   # 6-hour cache


def get_nba_win_pcts() -> Dict[str, float]:
    """
    Return {team_name_lower: win_pct} from ESPN NBA standings.
    Cached for 6 hours. Falls back to {} on failure.
    """
    now = time.time()
    if _standings_cache.get("ts", 0) + _STANDINGS_TTL > now:
        return _standings_cache.get("data", {})

    result: Dict[str, float] = {}
    try:
        data = espn_fetch("basketball", "nba", "standings")
        if not data:
            return result
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
            wins = losses = 0
            for s in entry.get("stats", []):
                sname = s.get("name", "")
                if sname == "wins":
                    wins = int(s.get("value", 0) or 0)
                elif sname == "losses":
                    losses = int(s.get("value", 0) or 0)
            total = wins + losses
            if total > 0:
                pct = wins / total
                result[name] = pct
                result[abbr] = pct
        logger.debug("NBA standings loaded: %d teams", len(result))
    except Exception as e:
        logger.warning("NBA standings fetch failed: %s", e)

    _standings_cache["data"] = result
    _standings_cache["ts"]   = now
    return result


# ── Net-rating helper ─────────────────────────────────────────────────────────

def get_team_net_rating(team_id: str) -> float:
    """Return team net rating (pts/100 poss). Falls back to 0.0."""
    stats = get_team_stats(team_id)
    if "netRating" in stats:
        return float(stats["netRating"])
    off = stats.get("offensiveRating", 110.0)
    dft = stats.get("defensiveRating", 110.0)
    return float(off) - float(dft)
