"""
NFL data layer.
Source: ESPN public API (no key required).
"""
from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional

from data.fetcher import espn_fetch

logger = logging.getLogger(__name__)


# ── Scoreboard ───────────────────────────────────────────────────────────────

def get_games(week: Optional[int] = None, season: int = 2024) -> List[Dict]:
    """Return NFL games for the given week / season."""
    params: Dict = {}
    if week:
        params["week"]        = week
        params["seasontype"]  = 2   # regular season
    data = espn_fetch("americanfootball", "nfl", "scoreboard", params=params)
    if not data:
        return []

    games = []
    _nfl_want = {
        "passingYards", "rushingYards", "receivingYards",
        "passingTouchdowns", "rushingTouchdowns", "receivingTouchdowns",
        "receptions",
    }
    for event in data.get("events", []):
        comp  = event.get("competitions", [{}])[0]
        teams = {t["homeAway"]: t for t in comp.get("competitors", [])}
        home  = teams.get("home", {})
        away  = teams.get("away", {})
        odds  = comp.get("odds", [{}])[0] if comp.get("odds") else {}

        leaders: List[Dict] = []
        for cat in comp.get("leaders", []):
            cat_name = cat.get("name", "")
            if cat_name not in _nfl_want:
                continue
            for entry in cat.get("leaders", [])[:3]:
                ath = entry.get("athlete", {})
                leaders.append({
                    "name":          ath.get("displayName", ""),
                    "team_id":       str(ath.get("team", {}).get("id", "")),
                    "position":      ath.get("position", {}).get("abbreviation", ""),
                    "stat":          cat_name,
                    "value":         float(entry.get("value", 0) or 0),
                    "display_value": entry.get("displayValue", ""),
                })

        games.append({
            "id":         event.get("id"),
            "name":       event.get("name"),
            "date":       event.get("date"),
            "week":       event.get("week", {}).get("number"),
            "status":     event.get("status", {}).get("type", {}).get("name"),
            "home_team":  home.get("team", {}).get("displayName"),
            "away_team":  away.get("team", {}).get("displayName"),
            "home_abbr":  home.get("team", {}).get("abbreviation", ""),
            "away_abbr":  away.get("team", {}).get("abbreviation", ""),
            "home_score": home.get("score"),
            "away_score": away.get("score"),
            "home_id":    home.get("team", {}).get("id"),
            "away_id":    away.get("team", {}).get("id"),
            "spread":     odds.get("spread"),
            "over_under": odds.get("overUnder"),
            "home_ml":    odds.get("moneyLineOdds"),
            "leaders":    leaders,
        })
    return games


# ── Team stats ───────────────────────────────────────────────────────────────

def get_team_stats(team_id: str) -> Dict[str, Any]:
    data = espn_fetch("americanfootball", "nfl", f"teams/{team_id}/statistics")
    if not data:
        return {}
    stats = {}
    for cat in data.get("results", {}).get("stats", {}).get("categories", []):
        for s in cat.get("stats", []):
            stats[s.get("name")] = s.get("value")
    return stats


def get_all_teams() -> List[Dict]:
    data = espn_fetch("americanfootball", "nfl", "teams")
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


# ── Roster / player stats ────────────────────────────────────────────────────

def get_roster(team_id: str) -> List[Dict]:
    """Return team roster from ESPN."""
    data = espn_fetch("americanfootball", "nfl", f"teams/{team_id}/roster")
    if not data:
        return []
    players = []
    for group in data.get("athletes", []):
        for p in group.get("items", []):
            players.append({
                "id":       p.get("id"),
                "name":     p.get("fullName"),
                "position": p.get("position", {}).get("abbreviation"),
                "jersey":   p.get("jersey"),
            })
    return players


def get_player_stats(player_id: str, season: int = 2024) -> Dict[str, Any]:
    """Fetch season stats for an NFL player via ESPN."""
    data = espn_fetch("americanfootball", "nfl",
                      f"athletes/{player_id}/statisticslog")
    if not data:
        return {}
    # Flatten the first season-level split we find
    for entry in data.get("seasonTypes", []):
        for split in entry.get("splits", {}).get("categories", []):
            stats = {}
            for s in split.get("stats", []):
                stats[s.get("name")] = s.get("value")
            if stats:
                return stats
    return {}


def get_player_season_stats(team_id: str, position: Optional[str] = None
                            ) -> List[Dict]:
    """
    Pull aggregated season stats for all players on a team.
    Optionally filter by position (QB, RB, WR, TE, K …).
    """
    roster  = get_roster(team_id)
    results = []
    for player in roster:
        if position and player.get("position") != position:
            continue
        stats = get_player_stats(player["id"])
        if stats:
            results.append({**player, **stats})
    return results


# ── Red-zone usage helper (approximation from season stats) ──────────────────

def estimate_rz_usage(player_stats: Dict[str, Any]) -> Dict[str, float]:
    """
    Estimate red-zone targets + carries from available stats.
    Keys used: rushingAttempts, receivingTargets, rushingTouchdowns,
               receivingTouchdowns.
    """
    rush_att = float(player_stats.get("rushingAttempts", 0) or 0)
    rec_tgt  = float(player_stats.get("receivingTargets", 0) or 0)
    rush_td  = float(player_stats.get("rushingTouchdowns", 0) or 0)
    rec_td   = float(player_stats.get("receivingTouchdowns", 0) or 0)
    total_td = rush_td + rec_td

    # Approximate RZ share: TDs / team_avg_rz_td_rate
    # Without granular RZ data we use a coarse proxy
    rz_carries  = rush_att * 0.15   # ~15 % of carries happen in RZ
    rz_targets  = rec_tgt  * 0.12  # ~12 % of targets happen in RZ
    return {
        "rz_carries":   rz_carries,
        "rz_targets":   rz_targets,
        "total_tds":    total_td,
        "total_carries": rush_att,
        "total_targets": rec_tgt,
    }
