"""
NBA data layer.
Primary  : ESPN public API  (no key, no rate limits)
All BDL (Ball Don't Lie) calls have been removed. ESPN is free and reliable.
"""
from __future__ import annotations
import re
import time
import logging
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

from data.fetcher import espn_fetch, fetch

logger = logging.getLogger(__name__)

# NBA data: ESPN free API (no key required)
# Replaced BDL March 2026 — was causing 429 rate-limit errors


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


# ── ESPN Player lookup — in-memory cache (prevents parallel race conditions) ─

_espn_athlete_id_cache: Dict[str, Tuple[Optional[str], float]] = {}
_espn_cache_lock = threading.Lock()


def get_espn_athlete_id(player_name: str) -> Optional[str]:
    """Search ESPN for an NBA athlete by name. Returns ESPN athlete_id or None.

    Normalises accents (Dončić → Doncic) for reliable search.
    Cached in memory for 24 h.
    """
    # Normalize accents so Dončić → Doncic for search
    normalized = unicodedata.normalize("NFD", player_name)
    ascii_name  = normalized.encode("ascii", "ignore").decode()
    cache_key   = ascii_name.lower().strip()

    now = time.time()
    with _espn_cache_lock:
        entry = _espn_athlete_id_cache.get(cache_key)
        if entry is not None:
            athlete_id, expires = entry
            if now < expires:
                return athlete_id

    data = espn_fetch("basketball", "nba", "athletes",
                      params={"search": ascii_name})
    athlete_id: Optional[str] = None
    if data:
        athletes = data.get("athletes", [])
        target = cache_key
        for ath in athletes[:5]:
            ath_name = (ath.get("displayName") or ath.get("fullName") or "").lower()
            ath_norm = (unicodedata.normalize("NFD", ath_name)
                        .encode("ascii", "ignore").decode().lower())
            if ath_norm == target or target in ath_norm or ath_norm in target:
                athlete_id = str(ath.get("id", "")) or None
                break
        if not athlete_id and athletes:
            athlete_id = str(athletes[0].get("id", "")) or None

    with _espn_cache_lock:
        _espn_athlete_id_cache[cache_key] = (athlete_id, now + 86400)

    return athlete_id


def get_espn_player_stats(athlete_id: str) -> Dict[str, float]:
    """Fetch season per-game averages for an ESPN NBA athlete.

    Parses ESPN's splits.categories looking for the per-game average category.
    Returns dict with keys: pts, reb, ast, fg3m, stl, blk.
    """
    data = espn_fetch("basketball", "nba", f"athletes/{athlete_id}/stats")
    if not data:
        return {}

    stat_values: Dict[str, float] = {}
    # ESPN returns stats under splits.categories or directly under categories
    categories = (data.get("splits", {}).get("categories", [])
                  or data.get("categories", []))
    for cat in categories:
        cat_name = (cat.get("name") or "").lower()
        if cat_name not in ("avg", "pergame", "perGame", "average", "averages"):
            continue
        for s in cat.get("stats", []):
            stat_values[s.get("name", "")] = float(s.get("value", 0) or 0)
        if stat_values:
            break  # stop at first matching category

    if not stat_values:
        return {}

    return {
        "pts":  float(stat_values.get("avgPoints",
                stat_values.get("pts",
                stat_values.get("points", 0)))),
        "reb":  float(stat_values.get("avgRebounds",
                stat_values.get("reb",
                stat_values.get("rebounds", 0)))),
        "ast":  float(stat_values.get("avgAssists",
                stat_values.get("ast",
                stat_values.get("assists", 0)))),
        "fg3m": float(stat_values.get("avgThreePointFieldGoalsMade",
                stat_values.get("fg3m",
                stat_values.get("threePointFieldGoalsMade", 0)))),
        "stl":  float(stat_values.get("avgSteals",
                stat_values.get("stl",
                stat_values.get("steals", 0)))),
        "blk":  float(stat_values.get("avgBlocks",
                stat_values.get("blk",
                stat_values.get("blocks", 0)))),
    }


def get_espn_player_logs(athlete_id: str, num_games: int = 10) -> List[Dict]:
    """Fetch last N game logs for an ESPN NBA athlete.

    Returns list of dicts with keys: pts, reb, ast, fg3m.
    ESPN returns a labels array + events dict; we align values to labels.
    """
    data = espn_fetch("basketball", "nba", f"athletes/{athlete_id}/gamelog")
    if not data:
        return []

    labels = data.get("labels", [])

    def _find_col(keywords: List[str]) -> Optional[int]:
        """Find column index where label matches any keyword (case-insensitive)."""
        for i, lbl in enumerate(labels):
            lbl_up = lbl.upper()
            if any(k.upper() in lbl_up for k in keywords):
                return i
        return None

    pts_col  = _find_col(["PTS", "POINT"])
    reb_col  = _find_col(["REB", "TRB"])
    ast_col  = _find_col(["AST"])
    fg3m_col = _find_col(["3PM", "3P", "THREE", "FG3M"])

    events = data.get("events", {})
    event_list: list = list(events.values()) if isinstance(events, dict) else (events or [])

    logs: List[Dict] = []
    for event in event_list:
        stats_arr = event.get("stats", [])
        if not stats_arr:
            continue
        entry: Dict[str, float] = {}
        for field, col in [("pts", pts_col), ("reb", reb_col),
                            ("ast", ast_col),  ("fg3m", fg3m_col)]:
            if col is not None and col < len(stats_arr):
                try:
                    entry[field] = float(stats_arr[col])
                except (TypeError, ValueError):
                    entry[field] = 0.0
        if entry:
            logs.append(entry)

    return logs[-num_games:] if logs else []


def fetch_all_player_stats(player_names: List[str]) -> Dict[str, Dict]:
    """Parallel-fetch ESPN athlete ID + season stats + game logs for all names.

    Uses 10 workers — ESPN has no rate limit so parallelism is safe.

    Returns:
        {name_lower: {"name": str, "athlete_id": str,
                      "pts": float, "reb": float, "ast": float, "fg3m": float,
                      "stl": float, "blk": float, "game_logs": list}}
    """
    def _fetch_one(name: str) -> Tuple[str, Dict]:
        try:
            athlete_id = get_espn_athlete_id(name)
            if not athlete_id:
                logger.debug("ESPN: athlete_id not found for %s", name)
                return name, {}
            season_stats = get_espn_player_stats(athlete_id) or {}
            game_logs    = get_espn_player_logs(athlete_id)
            return name, {
                "name":       name,
                "athlete_id": athlete_id,
                "game_logs":  game_logs,
                **season_stats,
            }
        except Exception as exc:
            logger.debug("ESPN player fetch failed for %s: %s", name, exc)
            return name, {}

    results: Dict[str, Dict] = {}
    if not player_names:
        return results

    max_workers = min(10, max(1, len(player_names)))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for name, player_data in executor.map(_fetch_one, player_names):
            if player_data:
                results[name.lower()] = player_data

    return results


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
