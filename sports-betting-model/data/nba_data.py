"""
NBA data layer.
Primary  : ESPN public API  (no key, no rate limits) for schedule/scoreboard
Player stats: NBA Stats API (stats.nba.com, free, no key) — official NBA data
All ESPN /athletes/{id}/stats and /athletes/{id}/gamelog calls removed;
those IDs don't match across ESPN endpoints (roster ID ≠ stats athlete ID).
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

# ── NBA Stats API helpers ──────────────────────────────────────────────────────

# Headers required by stats.nba.com to avoid 403/429
_NBA_STATS_HEADERS: Dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer":            "https://www.nba.com",
    "Accept":             "application/json, text/plain, */*",
    "Accept-Language":    "en-US,en;q=0.9",
    "Origin":             "https://www.nba.com",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token":  "true",
}

# ESPN team abbreviation → NBA Stats API abbreviation (mismatches only)
ESPN_TO_NBA: Dict[str, str] = {
    "GS":   "GSW",
    "NO":   "NOP",
    "NY":   "NYK",
    "SA":   "SAS",
    "UTAH": "UTA",
    "WSH":  "WAS",
    "NJ":   "BKN",
}

# In-memory cache for all-players list (24 h TTL)
_nba_players_cache: Dict = {}
_NBA_PLAYERS_TTL = 86400


def _nba_stats_fetch(endpoint: str, params: Optional[Dict] = None,
                     timeout: int = 12) -> Optional[Any]:
    """Fetch from stats.nba.com with required headers and disk cache."""
    url = f"https://stats.nba.com/stats/{endpoint}"
    return fetch(url, params=params, headers=_NBA_STATS_HEADERS,
                 use_cache=True, timeout=timeout)


def get_all_nba_players() -> Dict[str, List[Dict]]:
    """Fetch all active NBA players from NBA Stats API (cached 24 h).

    Returns {team_abbr: [{id, name, team}]} keyed by NBA Stats API abbreviation.
    Single call; result covers every team so we only hit the API once per day.
    """
    now = time.time()
    if _nba_players_cache.get("ts", 0) + _NBA_PLAYERS_TTL > now:
        return _nba_players_cache.get("data", {})

    data = _nba_stats_fetch("commonallplayers", {
        "LeagueID": "00",
        "Season":   "2025-26",
        "IsOnlyCurrentSeason": "1",
    }, timeout=15)

    if not data:
        print("[NBA] commonallplayers: no response from NBA Stats API")
        return {}

    try:
        result_set   = data["resultSets"][0]
        headers_list = result_set["headers"]
        rows         = result_set["rowSet"]

        id_idx   = headers_list.index("PERSON_ID")
        name_idx = headers_list.index("DISPLAY_FIRST_LAST")
        team_idx = headers_list.index("TEAM_ABBREVIATION")

        team_players: Dict[str, List[Dict]] = {}
        for row in rows:
            pid  = str(row[id_idx])
            name = row[name_idx] or ""
            team = row[team_idx] or ""
            if not team or not name:
                continue
            team_players.setdefault(team, []).append(
                {"id": pid, "name": name, "team": team}
            )

        total = sum(len(v) for v in team_players.values())
        print(f"[NBA] NBA Stats API: {total} players on "
              f"{len(team_players)} teams: {sorted(team_players)}")

        _nba_players_cache["data"] = team_players
        _nba_players_cache["ts"]   = now
        return team_players

    except Exception as e:
        print(f"[NBA] get_all_nba_players parse error: {e}")
        return {}


def get_nba_player_season_stats(person_id: str) -> Dict[str, float]:
    """Fetch season per-game averages from stats.nba.com.

    Returns {pts, reb, ast, fg3m, stl, blk}.
    Uses 2025-26 season row; falls back to most-recent row.
    """
    data = _nba_stats_fetch("playercareerstats", {
        "PlayerID": person_id,
        "PerMode":  "PerGame",
    })
    if not data:
        return {}

    try:
        season_set = None
        for rs in data.get("resultSets", []):
            if rs["name"] == "SeasonTotalsRegularSeason":
                season_set = rs
                break

        if not season_set or not season_set.get("rowSet"):
            print(f"[NBA STATS] No season rows for {person_id}")
            return {}

        h    = season_set["headers"]
        rows = season_set["rowSet"]

        # Prefer 2025-26; fall back to most-recent row
        current = None
        if "SEASON_ID" in h:
            sid_i = h.index("SEASON_ID")
            for row in rows:
                if row[sid_i] == "2025-26":
                    current = row
                    break
        if current is None and rows:
            current = rows[-1]

        if current is None:
            return {}

        def val(col: str) -> float:
            try:
                return float(current[h.index(col)] or 0)
            except (ValueError, IndexError):
                return 0.0

        result = {
            "pts":  val("PTS"),
            "reb":  val("REB"),
            "ast":  val("AST"),
            "fg3m": val("FG3M"),
            "stl":  val("STL"),
            "blk":  val("BLK"),
        }

        if result["pts"] == 0 and result["reb"] == 0:
            print(f"[NBA STATS] All zeros for {person_id} — "
                  f"available headers sample: {h[:8]}")
        else:
            print(f"[NBA STATS] {person_id}: "
                  f"{result['pts']}pts {result['reb']}reb "
                  f"{result['ast']}ast {result['fg3m']}3pm")

        return result

    except Exception as e:
        print(f"[NBA STATS] Parse error for {person_id}: {e}")
        return {}


def get_nba_player_game_logs(person_id: str, num_games: int = 10) -> List[Dict]:
    """Fetch recent game logs from stats.nba.com.

    Returns list of {pts, reb, ast, fg3m} dicts, newest first, capped at num_games.
    """
    data = _nba_stats_fetch("playergamelog", {
        "PlayerID":   person_id,
        "Season":     "2025-26",
        "SeasonType": "Regular Season",
    })
    if not data:
        return []

    try:
        rs   = data["resultSets"][0]
        h    = rs["headers"]
        rows = rs["rowSet"]

        if not rows:
            return []

        def val(row, col: str) -> float:
            try:
                return float(row[h.index(col)] or 0)
            except (ValueError, IndexError):
                return 0.0

        logs = [
            {"pts":  val(row, "PTS"),
             "reb":  val(row, "REB"),
             "ast":  val(row, "AST"),
             "fg3m": val(row, "FG3M")}
            for row in rows[:num_games]
        ]

        if logs:
            print(f"[NBA LOGS] {person_id}: {len(logs)} games, "
                  f"last: {logs[0]}")
        return logs

    except Exception as e:
        print(f"[NBA LOGS] Parse error for {person_id}: {e}")
        return []


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


# ── ESPN game summary (per-game boxscore stats) ───────────────────────────────

def get_espn_game_summary(event_id: str) -> Dict[str, List[Dict]]:
    """Fetch ESPN game summary boxscore — returns players with their game stats.

    For pre-game events this may be empty; for live/completed it has full boxscore.
    Returns {team_abbr: [{"id", "name", "pos", "stats": {LABEL: float}}]}.
    """
    data = fetch(
        "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary",
        params={"event": event_id},
        use_cache=True,
        timeout=10,
    )
    if not data:
        return {}

    team_players: Dict[str, List[Dict]] = {}
    boxscore = data.get("boxscore", {})

    for team_section in boxscore.get("players", []):
        team_info = team_section.get("team", {})
        team_abbr = team_info.get("abbreviation", "")
        if not team_abbr:
            continue

        players_seen: Dict[str, Dict] = {}
        for stat_group in team_section.get("statistics", []):
            labels = [str(l).upper() for l in stat_group.get("labels", [])]
            for athlete_data in stat_group.get("athletes", []):
                athlete = athlete_data.get("athlete", {})
                stats   = athlete_data.get("stats", [])
                name    = athlete.get("displayName", "")
                pid     = str(athlete.get("id", ""))
                pos_obj = athlete.get("position", {})
                pos     = pos_obj.get("abbreviation", "") if isinstance(pos_obj, dict) else ""
                if not name or not pid:
                    continue
                stats_map: Dict[str, float] = {}
                for i, label in enumerate(labels):
                    if i < len(stats):
                        try:
                            stats_map[label] = float(stats[i])
                        except (TypeError, ValueError):
                            pass
                if pid not in players_seen:
                    players_seen[pid] = {"id": pid, "name": name, "pos": pos, "stats": {}}
                players_seen[pid]["stats"].update(stats_map)

        if players_seen:
            team_players[team_abbr] = list(players_seen.values())
            print(f"[NBA] Summary {team_abbr}: {len(team_players[team_abbr])} players")

    return team_players


# ── BallDontLie v1 season averages (free, no auth) ────────────────────────────

_bdl_player_cache: Dict[str, Tuple[Dict, float]] = {}
_BDL_TTL = 43200  # 12 hours


def get_bdl_player_avgs(player_name: str) -> Dict[str, float]:
    """Search BallDontLie v1 (free, no API key) for NBA season averages.

    Returns {pts, reb, ast, fg3m} or {} on failure.
    Cached for 12 h to avoid hammering the free endpoint.
    """
    import requests as _req
    cache_key = player_name.lower().strip()
    now = time.time()
    cached = _bdl_player_cache.get(cache_key)
    if cached:
        data, expires = cached
        if now < expires:
            return data

    result: Dict[str, float] = {}
    try:
        r = _req.get(
            "https://www.balldontlie.io/api/v1/players",
            params={"search": player_name, "per_page": 5},
            timeout=8,
        )
        if r.status_code != 200:
            _bdl_player_cache[cache_key] = (result, now + 3600)
            return result

        players = r.json().get("data", [])
        if not players:
            _bdl_player_cache[cache_key] = (result, now + 3600)
            return result

        bdl_id = players[0]["id"]

        avg_r = _req.get(
            "https://www.balldontlie.io/api/v1/season_averages",
            params={"season": 2025, "player_ids[]": bdl_id},
            timeout=8,
        )
        if avg_r.status_code == 200:
            avgs = avg_r.json().get("data", [])
            if avgs:
                a = avgs[0]
                result = {
                    "pts":  float(a.get("pts",  0) or 0),
                    "reb":  float(a.get("reb",  0) or 0),
                    "ast":  float(a.get("ast",  0) or 0),
                    "fg3m": float(a.get("fg3m", 0) or 0),
                }
                print(f"[BDL] {player_name}: "
                      f"{result['pts']}pts {result['reb']}reb {result['ast']}ast")
    except Exception as e:
        print(f"[BDL] Failed for {player_name}: {e}")

    _bdl_player_cache[cache_key] = (result, now + _BDL_TTL)
    return result


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
