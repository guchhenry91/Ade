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

    Handles two ESPN response formats:
      Format A — stats is list of {name, value} dicts
      Format B — stats is list of raw values aligned to top-level labels[]
    Returns dict with keys: pts, reb, ast, fg3m, stl, blk.
    """
    data = espn_fetch("basketball", "nba", f"athletes/{athlete_id}/stats")
    if not data:
        print(f"[NBA STATS] No response for athlete {athlete_id}")
        return {}

    # Top-level labels array present in Format B
    labels: List[str] = data.get("labels", [])

    # Stats live under splits.categories or directly under categories
    splits     = data.get("splits", {})
    categories = splits.get("categories", []) or data.get("categories", [])

    if not categories:
        print(f"[NBA STATS] No categories for {athlete_id} — keys={list(data.keys())}")
        return {}

    # Prefer a per-game average category; fall back to first available
    _PG_KEYWORDS = ("avg", "per", "game", "average")
    avg_cat = None
    for cat in categories:
        if any(k in (cat.get("name") or "").lower() for k in _PG_KEYWORDS):
            avg_cat = cat
            break
    if avg_cat is None:
        avg_cat = categories[0]
        print(f"[NBA STATS] {athlete_id}: no avg category — using '{avg_cat.get('name')}'")

    cat_stats = avg_cat.get("stats", [])
    if not cat_stats:
        print(f"[NBA STATS] Empty stats for {athlete_id} (cat='{avg_cat.get('name')}')")
        return {}

    # ── Detect format ────────────────────────────────────────────────────────
    first = cat_stats[0]
    if isinstance(first, dict):
        # FORMAT A: [{name: "avgPoints", value: 30.3}, ...]
        stats_map: Dict[str, float] = {
            s.get("name", ""): float(s.get("value", 0) or 0)
            for s in cat_stats if isinstance(s, dict)
        }
        pts  = float(stats_map.get("avgPoints",
               stats_map.get("pts", stats_map.get("points", 0))))
        reb  = float(stats_map.get("avgRebounds",
               stats_map.get("reb", stats_map.get("rebounds", 0))))
        ast  = float(stats_map.get("avgAssists",
               stats_map.get("ast", stats_map.get("assists", 0))))
        fg3m = float(stats_map.get("avgThreePointFieldGoalsMade",
               stats_map.get("fg3m",
               stats_map.get("threePointFieldGoalsMade",
               stats_map.get("3pm", 0)))))
        stl  = float(stats_map.get("avgSteals",
               stats_map.get("stl", stats_map.get("steals", 0))))
        blk  = float(stats_map.get("avgBlocks",
               stats_map.get("blk", stats_map.get("blocks", 0))))
    else:
        # FORMAT B: ["65", "34.2", ".463", ...] — values aligned to labels[]
        col_labels = labels  # top-level labels

        def _find_val(keywords: List[str]) -> float:
            """Exact-match first, then substring — returns first hit."""
            # Pass 1: exact match (avoids "3P" matching "3PA")
            for kw in keywords:
                kw_up = kw.upper()
                for i, lbl in enumerate(col_labels):
                    if kw_up == str(lbl).upper() and i < len(cat_stats):
                        try:
                            return float(cat_stats[i])
                        except (ValueError, TypeError):
                            pass
            # Pass 2: substring
            for kw in keywords:
                kw_up = kw.upper()
                for i, lbl in enumerate(col_labels):
                    if kw_up in str(lbl).upper() and i < len(cat_stats):
                        try:
                            return float(cat_stats[i])
                        except (ValueError, TypeError):
                            pass
            return 0.0

        pts  = _find_val(["PTS", "AVGPOINTS", "POINTS"])
        reb  = _find_val(["REB", "AVGREBOUNDS", "REBOUNDS", "TRB"])
        ast  = _find_val(["AST", "AVGASSISTS", "ASSISTS"])
        fg3m = _find_val(["3PM", "3P"])   # exact "3PM" or exact "3P" before "3PA"/"3P%"
        stl  = _find_val(["STL", "AVGSTEALS", "STEALS"])
        blk  = _find_val(["BLK", "AVGBLOCKS", "BLOCKS"])

    result = {
        "pts":  float(pts  or 0),
        "reb":  float(reb  or 0),
        "ast":  float(ast  or 0),
        "fg3m": float(fg3m or 0),
        "stl":  float(stl  or 0),
        "blk":  float(blk  or 0),
    }

    if pts == 0 and reb == 0:
        sample_stats = cat_stats[:5]
        print(f"[NBA STATS] ALL ZEROS for {athlete_id} "
              f"(cat='{avg_cat.get('name')}', fmt={'A' if isinstance(first, dict) else 'B'}, "
              f"labels={col_labels[:5] if not isinstance(first, dict) else '—'}, "
              f"stats_sample={sample_stats})")
    else:
        print(f"[NBA STATS] {athlete_id}: "
              f"{result['pts']}pts {result['reb']}reb {result['ast']}ast {result['fg3m']}3pm")

    return result


def get_espn_player_logs(athlete_id: str, num_games: int = 10) -> List[Dict]:
    """Fetch last N game logs for an ESPN NBA athlete.

    Returns list of dicts with keys: pts, reb, ast, fg3m.
    ESPN returns a labels array + events dict; we align values to labels.
    """
    data = espn_fetch("basketball", "nba", f"athletes/{athlete_id}/gamelog")
    if not data:
        return []

    labels: List[str] = data.get("labels", [])

    def _find_col(keywords: List[str]) -> Optional[int]:
        """Exact match first (avoids '3P' matching '3PA'), then substring."""
        for kw in keywords:
            kw_up = kw.upper()
            for i, lbl in enumerate(labels):
                if kw_up == str(lbl).upper():
                    return i
        for kw in keywords:
            kw_up = kw.upper()
            for i, lbl in enumerate(labels):
                if kw_up in str(lbl).upper():
                    return i
        return None

    pts_col  = _find_col(["PTS", "POINTS"])
    reb_col  = _find_col(["REB", "REBOUNDS", "TRB"])
    ast_col  = _find_col(["AST", "ASSISTS"])
    fg3m_col = _find_col(["3PM", "3P"])  # exact "3PM" or exact "3P" first

    print(f"[NBA LOGS] {athlete_id} labels={labels[:6]} "
          f"cols: PTS={pts_col} REB={reb_col} AST={ast_col} 3PM={fg3m_col}")

    events = data.get("events", {})
    event_list: list = list(events.values()) if isinstance(events, dict) else (events or [])

    if not event_list:
        print(f"[NBA LOGS] {athlete_id}: 0 events in response")
        return []

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
                    val = stats_arr[col]
                    entry[field] = float(val) if val not in ("--", "", None) else 0.0
                except (TypeError, ValueError):
                    entry[field] = 0.0
            else:
                entry[field] = 0.0
        logs.append(entry)

    last_n = logs[-num_games:] if logs else []
    if last_n:
        print(f"[NBA LOGS] {athlete_id}: {len(last_n)} games, "
              f"last game: {last_n[-1]}")
    return last_n


def get_espn_team_roster(team_id: str) -> List[Dict]:
    """Fetch NBA roster from ESPN team endpoint.

    Returns list of {id, name, pos, jersey} dicts for players on the roster.
    ESPN may return athletes grouped by position (list of groups with 'items')
    or as a flat list.
    """
    data = espn_fetch("basketball", "nba", f"teams/{team_id}/roster")
    if not data:
        return []
    athletes_raw = data.get("athletes", [])
    flat: List[Dict] = []
    for item in athletes_raw:
        if not isinstance(item, dict):
            continue
        if "items" in item:
            # grouped by position — item.items is a list of athlete dicts
            flat.extend(item["items"])
        elif item.get("id"):
            flat.append(item)
    result: List[Dict] = []
    for p in flat:
        pid     = str(p.get("id", ""))
        name    = (p.get("displayName") or p.get("fullName") or "").strip()
        pos_obj = p.get("position", {})
        pos     = pos_obj.get("abbreviation", "") if isinstance(pos_obj, dict) else ""
        jersey  = str(p.get("jersey", ""))
        if pid and name:
            result.append({"id": pid, "name": name, "pos": pos, "jersey": jersey})
    return result


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
