"""
PrizePicks public API — fetch tonight's player prop lines.
No API key required.
"""
from __future__ import annotations
import logging
from typing import List, Dict

from data.fetcher import fetch

logger = logging.getLogger(__name__)

PP_BASE    = "https://api.prizepicks.com"
PP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; BetModel/1.0)",
    "Accept":     "application/json",
    "Referer":    "https://app.prizepicks.com/",
}

# PrizePicks league IDs (best-effort — may drift with PP changes)
# NOTE: league_id=12 is UFC/MMA, NOT NHL.  NHL hockey is 14.
_LEAGUE_IDS: dict[str, int] = {
    "NBA": 7,
    "NFL": 9,
    "MLB": 2,
    "NHL": 14,
}


def _parse_projections(data: dict) -> List[Dict]:
    """Parse a PrizePicks /projections response into flat dicts."""
    if not isinstance(data, dict):
        return []

    # Build player-id → attributes lookup from `included`
    player_map: dict[str, dict] = {}
    for item in data.get("included", []):
        if item.get("type") in ("new_player", "player", "Projection"):
            pid   = str(item.get("id", ""))
            attrs = item.get("attributes", {})
            name  = (attrs.get("display_name")
                     or attrs.get("name")
                     or attrs.get("first_name", "") + " " + attrs.get("last_name", "")).strip()
            if name and pid:
                player_map[pid] = {
                    "name": name,
                    "team": attrs.get("team", attrs.get("team_abbreviation", "")),
                    "pos":  attrs.get("position", ""),
                }

    results: List[Dict] = []
    for proj in data.get("data", []):
        ptype = proj.get("type", "")
        if "projection" not in ptype.lower() and ptype != "Projection":
            continue

        attrs  = proj.get("attributes", {})
        status = attrs.get("status", "pre_game")
        # Accept pre-game and active projections
        if status not in ("pre_game", "scheduled", "pre-game", "", None):
            continue

        rel         = proj.get("relationships", {})
        player_rel  = rel.get("new_player", rel.get("player", {}))
        player_id   = str(player_rel.get("data", {}).get("id", ""))
        player      = player_map.get(player_id, {})

        # stat_type may appear under different keys across API versions
        stat = (attrs.get("stat_type")
                or attrs.get("stat_display_name")
                or attrs.get("name")
                or "")

        line_raw = attrs.get("line_score") or attrs.get("projection") or attrs.get("line") or 0
        try:
            line = float(line_raw)
        except (TypeError, ValueError):
            line = 0.0

        if not player.get("name") or not stat or line <= 0:
            continue

        results.append({
            "name":      player["name"],
            "team":      player.get("team", ""),
            "pos":       player.get("pos", ""),
            "stat":      stat,
            "line":      line,
            "odds_type": attrs.get("odds_type", "standard"),
            "source":    "PrizePicks",
        })

    return results


def get_projections(sport: str = "NBA") -> List[Dict]:
    """
    Fetch tonight's PrizePicks projections for *sport*.
    Returns list of dicts: {name, team, pos, stat, line, source}.
    Falls back gracefully on any error.
    """
    league_id = _LEAGUE_IDS.get(sport.upper())
    if league_id is None:
        logger.debug("PrizePicks: unknown sport %s", sport)
        return []

    url    = f"{PP_BASE}/projections"
    params = {
        "league_id":   league_id,
        "per_page":    250,
        "single_stat": "true",
        "game_mode":   "pickem",
    }

    try:
        data = fetch(url, params=params, headers=PP_HEADERS, use_cache=True)
        if not data:
            logger.info("PrizePicks returned empty for %s (league_id=%s)", sport, league_id)
            return []
        rows = _parse_projections(data)
        logger.info("PrizePicks %s: %d projections fetched", sport, len(rows))
        return rows
    except Exception as exc:
        logger.warning("PrizePicks fetch failed for %s: %s", sport, exc)
        return []


def get_nba_projections() -> List[Dict]:
    return get_projections("NBA")


def get_mlb_projections() -> List[Dict]:
    return get_projections("MLB")


def get_nhl_projections() -> List[Dict]:
    return get_projections("NHL")
