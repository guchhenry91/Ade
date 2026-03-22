"""
NHL data layer.
Primary  : ESPN public API  (no key)
Secondary: NHL Stats API    (api-web.nhle.com/v1, free, no key)
"""
from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional

from data.fetcher import espn_fetch, fetch

logger = logging.getLogger(__name__)

_NHL_API_BASE = "https://api-web.nhle.com/v1"


# ── Scoreboard ───────────────────────────────────────────────────────────────

def get_games(dates: Optional[str] = None) -> List[Dict]:
    """Return today's NHL games from ESPN scoreboard."""
    params = {}
    if dates:
        params["dates"] = dates
    data = espn_fetch("hockey", "nhl", "scoreboard", params=params)
    if not data:
        return []

    games: List[Dict] = []
    _want = {"points", "goals", "assists", "plusMinus", "shots"}

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
            "leaders":    leaders,
        })

    return games


# ── Player stats via NHL API ──────────────────────────────────────────────────

def get_team_roster_stats(team_abbr: str) -> List[Dict]:
    """
    Fetch team roster + season stats from the NHL public API.
    Returns list sorted by points/game (skaters) or saves/game (goalies).
    """
    try:
        roster_data = fetch(f"{_NHL_API_BASE}/roster/{team_abbr}/current")
        if not roster_data:
            return []
    except Exception as e:
        logger.debug("NHL roster fetch error %s: %s", team_abbr, e)
        return []

    result: List[Dict] = []

    for group, is_goalie in [("forwards", False), ("defensemen", False), ("goalies", True)]:
        for p in roster_data.get(group, []):
            pid = p.get("id")
            if not pid:
                continue

            first_obj = p.get("firstName", "")
            last_obj  = p.get("lastName", "")
            first = first_obj.get("default", "") if isinstance(first_obj, dict) else str(first_obj)
            last  = last_obj.get("default",  "") if isinstance(last_obj, dict) else str(last_obj)
            name  = f"{first} {last}".strip()

            try:
                landing = fetch(f"{_NHL_API_BASE}/player/{pid}/landing")
                if not landing:
                    continue
                feat      = landing.get("featuredStats", {})
                ss        = feat.get("regularSeason", {}).get("subSeason", {})
                gp        = max(int(ss.get("gamesPlayed", 0) or 0), 1)

                if is_goalie:
                    saves_total = int(ss.get("saves", 0) or 0)
                    gaa         = round(float(ss.get("goalsAgainstAvg", 0) or 0), 2)
                    sv_pct      = round(float(ss.get("savePctg", 0) or 0), 3)
                    if gp < 3:
                        continue
                    result.append({
                        "name":     name,
                        "pos":      "G",
                        "is_goalie": True,
                        "gp":       gp,
                        "saves_pg": round(saves_total / gp, 1),
                        "gaa":      gaa,
                        "sv_pct":   sv_pct,
                    })
                else:
                    pts     = int(ss.get("points", 0) or 0)
                    goals   = int(ss.get("goals", 0) or 0)
                    assists = int(ss.get("assists", 0) or 0)
                    shots   = int(ss.get("shots", 0) or 0)
                    if gp < 5 or pts == 0:
                        continue
                    result.append({
                        "name":       name,
                        "pos":        p.get("positionCode", ""),
                        "is_goalie":  False,
                        "gp":         gp,
                        "pts_pg":     round(pts / gp, 2),
                        "goals_pg":   round(goals / gp, 2),
                        "assists_pg": round(assists / gp, 2),
                        "shots_pg":   round(shots / gp, 1),
                    })
            except Exception as e:
                logger.debug("NHL player landing error %s: %s", pid, e)
                continue

    skaters  = sorted([p for p in result if not p["is_goalie"]],
                      key=lambda x: x.get("pts_pg", 0), reverse=True)
    goalies  = sorted([p for p in result if p["is_goalie"]],
                      key=lambda x: x.get("saves_pg", 0), reverse=True)
    return skaters + goalies
