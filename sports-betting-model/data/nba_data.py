"""
NBA data layer.
Primary  : ESPN public API  (no key)
Secondary: balldontlie.io   (free, BALLDONTLIE_KEY env var for higher limits)
"""
from __future__ import annotations
import re
import logging
from typing import Any, Dict, List, Optional

from data.fetcher import espn_fetch, bdl_fetch

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

        # Extract season leaders included in the scoreboard payload
        leaders: List[Dict] = []
        _want = {"points", "threePointFieldGoalsMade", "rebounds", "assists"}
        for cat in comp.get("leaders", []):
            cat_name = cat.get("name", "")
            if cat_name not in _want:
                continue
            for entry in cat.get("leaders", [])[:5]:
                ath = entry.get("athlete", {})
                leaders.append({
                    "name":     ath.get("displayName", ""),
                    "team_id":  _espn_team_id(ath.get("team", {})),
                    "stat":     cat_name,
                    "display":  cat.get("displayName", cat_name),
                    "value":    float(entry.get("value", 0) or 0),
                    "display_value": entry.get("displayValue", ""),
                })

        def _record_wins_losses(competitor: dict) -> tuple:
            """Extract (wins, losses) from ESPN competitor records."""
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
            "leaders":         leaders,
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


# ── All players for a team (Ball Don't Lie) ──────────────────────────────────

def get_bdl_team_map() -> Dict[str, int]:
    """Return {abbreviation: bdl_team_id} for all 30 NBA teams."""
    data = bdl_fetch("teams", {"per_page": 40})
    if not data:
        return {}
    return {t.get("abbreviation", ""): t.get("id") for t in data.get("data", [])}


def get_team_players_with_averages(team_abbr: str, season: int = 2024) -> List[Dict]:
    """
    Fetch every active player on *team_abbr* with their season per-game averages
    from Ball Don't Lie.  Returns list sorted by minutes played desc.
    Players with 0 pts and < 5 min are skipped (DNP / two-ways).
    """
    team_map = get_bdl_team_map()
    team_id  = team_map.get(team_abbr.upper() if team_abbr else "")
    if not team_id:
        logger.debug("BDL team_id not found for abbr %s", team_abbr)
        return []

    players_data = bdl_fetch("players", {"team_ids[]": team_id, "per_page": 100})
    if not players_data:
        return []
    players   = players_data.get("data", [])
    player_ids = [p["id"] for p in players]
    if not player_ids:
        return []

    # Fetch all season averages in one request
    id_qs     = "&".join(f"player_ids[]={pid}" for pid in player_ids)
    avgs_data = bdl_fetch(f"season_averages?season={season}&{id_qs}")
    avgs_map  = {}
    if avgs_data:
        for a in avgs_data.get("data", []):
            avgs_map[a.get("player_id")] = a

    def _min(avg: dict) -> float:
        """Parse 'MM:SS' or numeric minutes string → float."""
        raw = avg.get("min") or "0"
        if isinstance(raw, str) and ":" in raw:
            parts = raw.split(":")
            return float(parts[0]) + float(parts[1]) / 60
        try:
            return float(raw)
        except (TypeError, ValueError):
            return 0.0

    result = []
    for p in players:
        pid  = p["id"]
        avg  = avgs_map.get(pid, {})
        pts  = float(avg.get("pts", 0) or 0)
        mins = _min(avg)
        if pts < 1.0 and mins < 5.0:   # skip non-contributors
            continue
        result.append({
            "player_id": pid,
            "name":      f"{p.get('first_name','').strip()} {p.get('last_name','').strip()}".strip(),
            "position":  p.get("position", ""),
            "pts":       round(pts, 1),
            "reb":       round(float(avg.get("reb", 0) or 0), 1),
            "ast":       round(float(avg.get("ast", 0) or 0), 1),
            "fg3m":      round(float(avg.get("fg3m", 0) or 0), 1),
            "stl":       round(float(avg.get("stl", 0) or 0), 1),
            "blk":       round(float(avg.get("blk", 0) or 0), 1),
            "min":       round(mins, 1),
            "gp":        int(avg.get("games_played", 0) or 0),
        })

    return sorted(result, key=lambda x: x["min"], reverse=True)


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
