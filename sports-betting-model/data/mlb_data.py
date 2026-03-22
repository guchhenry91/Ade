"""
MLB data layer.
Primary  : ESPN public API  (no key)
Secondary: MLB Stats API    (statsapi.mlb.com, free, no key)
"""
from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional

from data.fetcher import espn_fetch, fetch

logger = logging.getLogger(__name__)

_MLB_STATS_BASE = "https://statsapi.mlb.com/api/v1"


# ── Scoreboard ───────────────────────────────────────────────────────────────

def get_games(dates: Optional[str] = None) -> List[Dict]:
    """Return today's MLB games from ESPN scoreboard."""
    params = {}
    if dates:
        params["dates"] = dates
    data = espn_fetch("baseball", "mlb", "scoreboard", params=params)
    if not data:
        return []

    games: List[Dict] = []
    _want = {"strikeouts", "earnedRunAverage", "hits", "homeRuns",
             "battingAverage", "rbi", "era", "walks"}

    for event in data.get("events", []):
        comp  = event.get("competitions", [{}])[0]
        teams = {t["homeAway"]: t for t in comp.get("competitors", [])}
        home  = teams.get("home", {})
        away  = teams.get("away", {})
        odds  = comp.get("odds", [{}])[0] if comp.get("odds") else {}

        leaders: List[Dict] = []
        for cat in comp.get("leaders", []):
            cat_name = cat.get("name", "")
            if cat_name not in _want:
                continue
            for entry in cat.get("leaders", [])[:3]:
                ath = entry.get("athlete", {})
                team_obj = ath.get("team", {})
                tid = ""
                if "$ref" in team_obj:
                    import re
                    m = re.search(r"/teams/(\d+)", team_obj["$ref"])
                    tid = m.group(1) if m else ""
                else:
                    tid = str(team_obj.get("id", ""))

                pos_obj = ath.get("position", {})
                pos     = pos_obj.get("abbreviation", "") if isinstance(pos_obj, dict) else ""

                leaders.append({
                    "name":     ath.get("displayName", ""),
                    "team_id":  tid,
                    "stat":     cat_name,
                    "value":    float(entry.get("value", 0) or 0),
                    "position": pos,
                })

        venue = comp.get("venue", {})
        if isinstance(venue, dict) and "$ref" in venue:
            venue_name = ""
        else:
            venue_name = venue.get("fullName", "") if isinstance(venue, dict) else ""

        games.append({
            "id":         event.get("id"),
            "date":       event.get("date"),
            "status":     event.get("status", {}).get("type", {}).get("name"),
            "home_team":  home.get("team", {}).get("displayName"),
            "away_team":  away.get("team", {}).get("displayName"),
            "home_abbr":  home.get("team", {}).get("abbreviation", ""),
            "away_abbr":  away.get("team", {}).get("abbreviation", ""),
            "home_id":    str(home.get("team", {}).get("id", "")),
            "away_id":    str(away.get("team", {}).get("id", "")),
            "home_score": home.get("score"),
            "away_score": away.get("score"),
            "home_ml":    odds.get("moneyLineOdds"),
            "over_under": odds.get("overUnder"),
            "leaders":    leaders,
            "venue":      venue_name,
        })

    return games


# ── Player stats via MLB Stats API ──────────────────────────────────────────

def get_pitcher_season_stats(mlb_player_id: str | int) -> Dict[str, Any]:
    """Season pitching stats from MLB Stats API."""
    try:
        data = fetch(
            f"{_MLB_STATS_BASE}/people/{mlb_player_id}/stats",
            params={"stats": "season", "group": "pitching", "season": "2025"},
        )
        if not data:
            return {}
        splits = (data.get("stats") or [{}])[0].get("splits", [])
        if not splits:
            return {}
        s = splits[0].get("stat", {})
        gp = max(int(s.get("gamesPitched", 1) or 1), 1)
        return {
            "era":    round(float(s.get("era", 0) or 0), 2),
            "so":     int(s.get("strikeOuts", 0) or 0),
            "so_pg":  round(int(s.get("strikeOuts", 0) or 0) / gp, 1),
            "ip":     float(s.get("inningsPitched", 0) or 0),
            "ip_pg":  round(float(s.get("inningsPitched", 0) or 0) / gp, 1),
            "whip":   round(float(s.get("whip", 0) or 0), 2),
            "wins":   int(s.get("wins", 0) or 0),
            "games":  gp,
        }
    except Exception as e:
        logger.debug("MLB pitcher stats error %s: %s", mlb_player_id, e)
        return {}


def get_batter_season_stats(mlb_player_id: str | int) -> Dict[str, Any]:
    """Season batting stats from MLB Stats API."""
    try:
        data = fetch(
            f"{_MLB_STATS_BASE}/people/{mlb_player_id}/stats",
            params={"stats": "season", "group": "hitting", "season": "2025"},
        )
        if not data:
            return {}
        splits = (data.get("stats") or [{}])[0].get("splits", [])
        if not splits:
            return {}
        s = splits[0].get("stat", {})
        gp = max(int(s.get("gamesPlayed", 1) or 1), 1)
        hits = int(s.get("hits", 0) or 0)
        tb   = int(s.get("totalBases", 0) or 0)
        return {
            "avg":     round(float(s.get("avg", 0) or 0), 3),
            "hr":      int(s.get("homeRuns", 0) or 0),
            "rbi":     int(s.get("rbi", 0) or 0),
            "hits":    hits,
            "hits_pg": round(hits / gp, 2),
            "tb":      tb,
            "tb_pg":   round(tb / gp, 2),
            "runs":    int(s.get("runs", 0) or 0),
            "runs_pg": round(int(s.get("runs", 0) or 0) / gp, 2),
            "games":   gp,
        }
    except Exception as e:
        logger.debug("MLB batter stats error %s: %s", mlb_player_id, e)
        return {}


def search_mlb_player(name: str) -> Optional[Dict]:
    """Find a player by name in MLB Stats API."""
    try:
        data = fetch(
            f"{_MLB_STATS_BASE}/people/search",
            params={"names": name, "sportId": 1},
        )
        if not data:
            return None
        people = data.get("people", [])
        if not people:
            return None
        p = people[0]
        return {
            "id":  p.get("id"),
            "name": p.get("fullName", name),
            "pos":  p.get("primaryPosition", {}).get("abbreviation", ""),
        }
    except Exception as e:
        logger.debug("MLB player search error %s: %s", name, e)
        return None
