"""
The Odds API — sportsbook game odds from FanDuel, DraftKings, Bet365, etc.

Free tier (500 req/month): h2h, spreads, totals.
Player props require a paid plan.

Set ODDS_API_KEY environment variable to enable.
Without the key every function returns empty gracefully.
"""
from __future__ import annotations
import os
import re
import time
import logging
import threading
from typing import Dict, List, Optional, Tuple

from data.fetcher import fetch

logger = logging.getLogger(__name__)

ODDS_BASE = "https://api.the-odds-api.com/v4"

SPORT_KEYS: Dict[str, str] = {
    "NBA":  "basketball_nba",
    "MLB":  "baseball_mlb",
    "NHL":  "icehockey_nhl",
    "NFL":  "americanfootball_nfl",
    "EPL":  "soccer_epl",
    "UCL":  "soccer_uefa_champs_league",
    "LIGA": "soccer_spain_la_liga",
    "L1":   "soccer_france_ligue_one",
}

# Player props market keys per sport
NBA_PROP_MARKETS  = "player_points,player_rebounds,player_assists,player_threes"
MLB_PROP_MARKETS  = ("batter_hits,batter_total_bases,batter_home_runs,"
                     "batter_rbis,pitcher_strikeouts")
NHL_PROP_MARKETS  = ("player_points,player_shots_on_goal,"
                     "player_goals,player_assists")

# Odds API market_key → our internal stat label (for lookup key construction)
MARKET_STAT_MAP: Dict[str, str] = {
    # NBA
    "player_points":         "PTS",
    "player_rebounds":       "REB",
    "player_assists":        "AST",
    "player_threes":         "3PM",
    # MLB
    "batter_hits":           "hits",
    "batter_total_bases":    "total_bases",
    "batter_home_runs":      "home_runs",
    "batter_rbis":           "rbi",
    "pitcher_strikeouts":    "strikeouts",
    # NHL
    "player_goals":          "goals",
    "player_assists":        "assists",
    "player_shots_on_goal":  "shots",
}

_PROPS_CACHE: Dict[str, Tuple[Dict, float]] = {}
_PROPS_LOCK = threading.Lock()

# Preferred bookmakers in display priority order
BOOKMAKER_PRIORITY = [
    "fanduel", "draftkings", "betmgm", "bet365",
    "betrivers", "williamhill_us", "bovada",
]


def _key() -> str:
    return os.getenv("ODDS_API_KEY", "")


def fetch_player_props(sport: str, markets: str,
                       bookmakers: str = "draftkings,fanduel",
                       ttl: int = 900) -> Dict[str, float]:
    """Fetch all player prop lines for a sport via The Odds API.

    Returns {f"{player_name}_{market_key}": over_line} dict.
    Uses sport-level event list then per-event odds — requires a premium key.
    Results cached for ttl seconds (default 15 min).

    sport   : short key e.g. "NBA", "MLB", "NHL"
    markets : comma-separated Odds API market keys
    """
    sport_key = SPORT_KEYS.get(sport.upper())
    if not sport_key:
        return {}

    cache_key = f"props_{sport_key}_{markets}"
    now = time.time()
    with _PROPS_LOCK:
        cached = _PROPS_CACHE.get(cache_key)
        if cached:
            data, expires = cached
            if now < expires:
                return data

    api_key = _key()
    if not api_key:
        return {}

    # Step 1: get today's events
    events = fetch(
        f"{ODDS_BASE}/sports/{sport_key}/events",
        params={"apiKey": api_key, "dateFormat": "iso"},
        use_cache=False,
        timeout=12,
    )
    if not events or not isinstance(events, list):
        logger.warning("[ODDS] fetch_player_props: no events for %s", sport_key)
        with _PROPS_LOCK:
            _PROPS_CACHE[cache_key] = ({}, now + 300)
        return {}

    all_lines: Dict[str, float] = {}

    # Step 2: per-event player props (cap at 15 events to limit API usage)
    for event in events[:15]:
        event_id = event.get("id")
        if not event_id:
            continue
        props_data = fetch(
            f"{ODDS_BASE}/sports/{sport_key}/events/{event_id}/odds",
            params={
                "apiKey":      api_key,
                "regions":     "us",
                "markets":     markets,
                "bookmakers":  bookmakers,
                "oddsFormat":  "american",
            },
            use_cache=False,
            timeout=10,
        )
        if not props_data or not isinstance(props_data, dict):
            continue
        for bookmaker in props_data.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                market_key = market.get("key", "")
                stat_label = MARKET_STAT_MAP.get(market_key, market_key)
                for outcome in market.get("outcomes", []):
                    if outcome.get("name") != "Over":
                        continue
                    player = outcome.get("description", "")
                    line   = outcome.get("point")
                    if player and line is not None:
                        # Store under both market_key and stat_label for flexible lookup
                        key_mk = f"{player}_{market_key}"
                        key_sl = f"{player}_{stat_label}"
                        if key_mk not in all_lines:
                            all_lines[key_mk] = float(line)
                        if key_sl not in all_lines:
                            all_lines[key_sl] = float(line)

    logger.info("[ODDS] %s player props: %d lines", sport_key, len(all_lines) // 2)
    print(f"[ODDS] {sport.upper()} prop lines: {len(all_lines) // 2}")

    with _PROPS_LOCK:
        _PROPS_CACHE[cache_key] = (all_lines, now + ttl)
    return all_lines


# ── build_sport_props — Odds API as single source of truth ────────────────────

import math
import requests as _requests
from collections import defaultdict

# Sport config: what markets to pull and how to label them
_SPORT_CONFIG: Dict[str, Dict] = {
    "nba": {
        "sport_key": "basketball_nba",
        "sport_label": "Basketball",
        "league": "NBA",
        "icon": "🏀",
        "espn_path": "basketball/nba",
        "primary_stat": "PTS",
        "markets": [
            "player_points", "player_rebounds", "player_assists",
            "player_threes", "player_blocks", "player_steals",
            "player_points_rebounds_assists",
        ],
        "stat_map": {
            "player_points":                  "PTS",
            "player_rebounds":                "REB",
            "player_assists":                 "AST",
            "player_threes":                  "3PM",
            "player_blocks":                  "blocks",
            "player_steals":                  "steals",
            "player_points_rebounds_assists":  "pra",
        },
        "stat_labels": {
            "PTS": "Points", "REB": "Rebounds",
            "AST": "Assists", "3PM": "3-Pointers",
            "blocks": "Blocks", "steals": "Steals",
            "pra": "Pts + Reb + Ast",
        },
    },
    "mlb": {
        "sport_key": "baseball_mlb",
        "sport_label": "Baseball",
        "league": "MLB",
        "icon": "⚾",
        "espn_path": "baseball/mlb",
        "primary_stat": "hits",
        "markets": [
            "batter_hits", "batter_total_bases", "batter_home_runs",
            "batter_rbis", "batter_runs_scored", "pitcher_strikeouts",
            "batter_hits_runs_rbis", "batter_singles",
        ],
        "stat_map": {
            "batter_hits":             "hits",
            "batter_total_bases":      "total_bases",
            "batter_home_runs":        "home_runs",
            "batter_rbis":             "rbi",
            "batter_runs_scored":      "runs",
            "pitcher_strikeouts":      "strikeouts",
            "batter_hits_runs_rbis":   "hits_runs_rbis",
            "batter_singles":          "singles",
        },
        "stat_labels": {
            "hits": "Hits", "total_bases": "Total Bases",
            "home_runs": "Home Runs", "rbi": "RBI",
            "runs": "Runs", "strikeouts": "Strikeouts",
            "hits_runs_rbis": "Hits + Runs + RBI", "singles": "Singles",
        },
    },
    "nhl": {
        "sport_key": "icehockey_nhl",
        "sport_label": "Ice Hockey",
        "league": "NHL",
        "icon": "🏒",
        "espn_path": "hockey/nhl",
        "primary_stat": "shots",
        # player_goals returns Yes/No outcomes — handled by _YES_NO_MARKETS below.
        "markets": [
            "player_points", "player_shots_on_goal", "player_assists",
            "player_goals", "player_power_play_points", "player_blocked_shots",
        ],
        "stat_map": {
            "player_points":            "points",
            "player_shots_on_goal":     "shots",
            "player_assists":           "assists",
            "player_goals":             "goals",
            "player_power_play_points": "pp_points",
            "player_blocked_shots":     "blocked_shots",
        },
        "stat_labels": {
            "points": "Points", "shots": "Shots on Goal",
            "goals": "Goals", "assists": "Assists",
            "pp_points": "Power Play Points", "blocked_shots": "Blocked Shots",
        },
    },
}

# Markets that use Yes/No outcomes instead of Over/Under
_YES_NO_MARKETS = {
    "player_goals",
    "player_anytime_scorer",
    "player_first_goal_scorer",
}
# Keep old name as alias for any external references
_ANYTIME_SCORER_MARKETS = _YES_NO_MARKETS

# Full team name → abbreviation (NBA, MLB, NHL combined)
_TEAM_ABBR: Dict[str, str] = {
    # NBA
    "Atlanta Hawks": "ATL", "Boston Celtics": "BOS",
    "Brooklyn Nets": "BKN", "Charlotte Hornets": "CHA",
    "Chicago Bulls": "CHI", "Cleveland Cavaliers": "CLE",
    "Dallas Mavericks": "DAL", "Denver Nuggets": "DEN",
    "Detroit Pistons": "DET", "Golden State Warriors": "GSW",
    "Houston Rockets": "HOU", "Indiana Pacers": "IND",
    "LA Clippers": "LAC", "Los Angeles Clippers": "LAC",
    "LA Lakers": "LAL", "Los Angeles Lakers": "LAL",
    "Memphis Grizzlies": "MEM", "Miami Heat": "MIA",
    "Milwaukee Bucks": "MIL", "Minnesota Timberwolves": "MIN",
    "New Orleans Pelicans": "NOP", "New York Knicks": "NYK",
    "Oklahoma City Thunder": "OKC", "Orlando Magic": "ORL",
    "Philadelphia 76ers": "PHI", "Phoenix Suns": "PHX",
    "Portland Trail Blazers": "POR", "Sacramento Kings": "SAC",
    "San Antonio Spurs": "SAS", "Toronto Raptors": "TOR",
    "Utah Jazz": "UTA", "Washington Wizards": "WAS",
}

_ROSTER_CACHE: Dict[str, Tuple[set, float]] = {}
_ROSTER_LOCK = threading.Lock()
_ROSTER_TTL = 86400  # 24 h


def _get_team_roster_names(espn_path: str, team_full_name: str) -> set:
    """Return set of player display names for a team via ESPN roster endpoint.

    Results cached 24 h in-process. On any error returns empty set.
    """
    cache_key = f"{espn_path}|{team_full_name}"
    now = time.time()
    with _ROSTER_LOCK:
        cached = _ROSTER_CACHE.get(cache_key)
        if cached:
            names, expires = cached
            if now < expires:
                return names

    names: set = set()
    try:
        # Step 1: find team ID
        teams_data = fetch(
            f"https://site.api.espn.com/apis/site/v2/sports/{espn_path}/teams",
            params={"limit": 40},
            timeout=8,
        )
        if not teams_data:
            return names

        team_id = None
        team_lower = team_full_name.lower()
        for sport in teams_data.get("sports", [teams_data]):
            for league in sport.get("leagues", [sport]):
                for t in league.get("teams", []):
                    info = t.get("team", t)
                    display = (info.get("displayName") or "").lower()
                    if team_lower in display or display in team_lower:
                        team_id = info.get("id")
                        break
                if team_id:
                    break
            if team_id:
                break

        if not team_id:
            return names

        # Step 2: fetch roster
        roster_data = fetch(
            f"https://site.api.espn.com/apis/site/v2/sports/{espn_path}"
            f"/teams/{team_id}/roster",
            timeout=8,
        )
        if not roster_data:
            return names

        for group in roster_data.get("athletes", []):
            if isinstance(group, dict):
                items = group.get("items", [group])
                for item in items:
                    if isinstance(item, dict):
                        name = item.get("displayName", "")
                        if name:
                            names.add(name)

        print(f"[ROSTER] {team_full_name}: {len(names)} players")
    except Exception as e:
        logger.debug("Roster fetch failed %s: %s", team_full_name, e)

    with _ROSTER_LOCK:
        _ROSTER_CACHE[cache_key] = (names, now + _ROSTER_TTL)
    return names


def _assign_to_team(player_name: str,
                    home_team: str, home_abbr: str, home_roster: set,
                    away_team: str, away_abbr: str, away_roster: set) -> str:
    """Assign a player to home or away abbr using roster fuzzy matching."""
    if player_name in home_roster:
        return home_abbr
    if player_name in away_roster:
        return away_abbr
    # Last-name match
    last = player_name.split()[-1].lower()
    for n in home_roster:
        if n.split()[-1].lower() == last:
            return home_abbr
    for n in away_roster:
        if n.split()[-1].lower() == last:
            return away_abbr
    # Substring match
    pl = player_name.lower()
    for n in home_roster:
        if pl in n.lower() or n.lower() in pl:
            return home_abbr
    for n in away_roster:
        if pl in n.lower() or n.lower() in pl:
            return away_abbr
    return home_abbr  # default to home


def _team_abbr(team_full_name: str) -> str:
    """Convert full team name to short abbreviation."""
    if team_full_name in _TEAM_ABBR:
        return _TEAM_ABBR[team_full_name]
    words = team_full_name.upper().split()
    return words[-1][:3] if words else "UNK"


def _build_prop_card(stat: str, line: float,
                     stat_labels: Dict[str, str], sport: str,
                     over_prob: Optional[float] = None) -> Dict:
    """Build a single prop card from an Odds API line + bookmaker price.

    over_prob: implied probability from American odds (0.0–1.0).
    If not provided, falls back to a lean-over default.
    """
    if over_prob is not None:
        over_p = float(over_prob)
    else:
        over_p = 0.52  # neutral fallback

    under_p = 1.0 - over_p
    pick    = "OVER" if over_p > 0.55 else ("UNDER" if over_p < 0.45 else "FAIR")
    best    = max(over_p, under_p)
    conf_pct = round(best * 100, 1)

    if conf_pct >= 85:
        stars, conf_label = 5, "Elite Pick"
    elif conf_pct >= 78:
        stars, conf_label = 4, "Strong Pick"
    elif conf_pct >= 68:
        stars, conf_label = 3, "Good Pick"
    elif conf_pct >= 58:
        stars, conf_label = 2, "Moderate"
    else:
        stars, conf_label = 1, "Use Caution"

    # Map conf_pct to legacy confidence string for template compatibility
    if best >= 0.78:
        conf_str = "HIGH"
    elif best >= 0.62:
        conf_str = "MEDIUM"
    else:
        conf_str = "LOW"

    return {
        "stat":           stat,
        "label":          stat_labels.get(stat, stat),
        "line":           line,
        "avg":            line,       # use line as season-avg reference
        "over_prob":      round(over_p  * 100, 1),
        "under_prob":     round(under_p * 100, 1),
        "pick":           pick,
        "confidence":     conf_str,
        "stars":          stars,
        "conf_label":     conf_label,
        "last5_avg":      None,
        "last10_avg":     None,
        "hit_over_last5": None,
        "trend":          "→",
    }


def _fetch_h2h_probs(sport_key: str) -> Dict[str, Dict[str, float]]:
    """Fetch h2h win probabilities. Returns {f"{home}|{away}": {home: %, away: %}}."""
    api_key = _key()
    if not api_key:
        return {}

    data = fetch(
        f"{ODDS_BASE}/sports/{sport_key}/odds",
        params={
            "apiKey": api_key,
            "regions": "us",
            "markets": "h2h",
            "oddsFormat": "american",
        },
        use_cache=False,
        timeout=12,
    )
    if not data or not isinstance(data, list):
        return {}

    result: Dict[str, Dict[str, float]] = {}
    for game in data:
        home = game.get("home_team", "")
        away = game.get("away_team", "")
        for bm in game.get("bookmakers", [])[:1]:
            for market in bm.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                raw_probs: Dict[str, float] = {}
                for outcome in market.get("outcomes", []):
                    team  = outcome.get("name", "")
                    price = float(outcome.get("price", 0) or 0)
                    if price > 0:
                        prob = 100 / (price + 100)
                    elif price < 0:
                        prob = abs(price) / (abs(price) + 100)
                    else:
                        prob = 0.5
                    raw_probs[team] = prob
                if raw_probs:
                    # Normalize to remove bookmaker vig — probs sum to 100%
                    total = sum(raw_probs.values())
                    probs = {k: round(v / total * 100, 1) for k, v in raw_probs.items()}
                    result[f"{home}|{away}"] = probs
    return result


def build_sport_props(sport_name: str, ttl: int = 900) -> list:
    """Build all prop cards for a sport using ONLY the Odds API.

    Returns list of game dicts each containing:
        home_team, away_team, home_abbr, away_abbr,
        home_prob, away_prob,
        home_roster, away_roster (list of player card dicts),
        sport, league, sport_icon, kickoff, status.

    Player cards: {name, pos, pts, props[]}
    Prop cards:   {stat, label, line, avg, over_prob, under_prob,
                   pick, confidence, stars, conf_label, trend, ...}
    """
    cfg = _SPORT_CONFIG.get(sport_name)
    if not cfg:
        logger.warning("build_sport_props: unknown sport %s", sport_name)
        return []

    sport_key  = cfg["sport_key"]
    espn_path  = cfg["espn_path"]
    markets    = cfg["markets"]
    stat_map   = cfg["stat_map"]
    stat_labels = cfg["stat_labels"]
    primary    = cfg["primary_stat"]
    api_key    = _key()

    if not api_key:
        logger.warning("[ODDS] ODDS_API_KEY not set — no props for %s", sport_name)
        return []

    # Step 1: get events
    events = fetch(
        f"{ODDS_BASE}/sports/{sport_key}/events",
        params={"apiKey": api_key, "dateFormat": "iso"},
        use_cache=False,
        timeout=12,
    )
    if not events or not isinstance(events, list):
        print(f"[ODDS] No events for {sport_name}")
        return []
    print(f"[ODDS] {sport_name}: {len(events)} events")

    # Step 2: h2h win probabilities
    h2h_probs = _fetch_h2h_probs(sport_key)

    games: list = []
    markets_str = ",".join(markets)

    for i, event in enumerate(events[:15]):
        # Throttle: pause 500 ms every 3 requests to avoid 429 rate limits
        if i > 0 and i % 3 == 0:
            import time as _t; _t.sleep(0.5)

        event_id  = event.get("id", "")
        home_team = event.get("home_team", "")
        away_team = event.get("away_team", "")
        if not home_team or not away_team:
            continue

        # Win probabilities from h2h odds
        probs    = h2h_probs.get(f"{home_team}|{away_team}", {})
        home_prob = probs.get(home_team, 50.0)
        away_prob = probs.get(away_team, 100.0 - home_prob)

        # Step 3: player props for this game — use regions=us without bookmakers
        # filter so the API returns data from any US bookmaker that has the market.
        props_data = fetch(
            f"{ODDS_BASE}/sports/{sport_key}/events/{event_id}/odds",
            params={
                "apiKey":     api_key,
                "regions":    "us",
                "markets":    markets_str,
                "oddsFormat": "american",
            },
            use_cache=False,
            timeout=10,
        )
        if not props_data or not isinstance(props_data, dict):
            continue

        # player_name → {stat: {"line": float, "over_prob": float}}
        # over_prob is derived from the American odds price (removes hardcoded 0.54)
        player_lines: Dict[str, Dict[str, Dict]] = defaultdict(dict)
        for bookmaker in props_data.get("bookmakers", [])[:2]:
            for market in bookmaker.get("markets", []):
                mk   = market.get("key", "")
                stat = stat_map.get(mk)
                if not stat:
                    continue
                is_yes_no = mk in _YES_NO_MARKETS
                for outcome in market.get("outcomes", []):
                    oname = outcome.get("name", "")
                    # Accept "Over" for standard O/U markets; "Yes" for Yes/No markets
                    if is_yes_no:
                        if oname != "Yes":
                            continue
                        # Yes/No market (e.g. player_goals): use line=0.5 (binary prop)
                        player = (outcome.get("description") or "").strip() or oname
                        line   = 0.5
                    else:
                        if oname != "Over":
                            continue
                        player = outcome.get("description", "")
                        line   = outcome.get("point")

                    price  = float(outcome.get("price", -110) or -110)
                    if player and line is not None and stat not in player_lines[player]:
                        op = _american_to_implied(price)
                        player_lines[player][stat] = {
                            "line":      float(line),
                            "over_prob": round(op, 4),
                        }

        print(f"[ODDS] {home_team} vs {away_team}: {len(player_lines)} players")

        # Step 4: get ESPN rosters to assign players to teams
        home_roster_names = _get_team_roster_names(espn_path, home_team)
        away_roster_names = _get_team_roster_names(espn_path, away_team)

        home_abbr = _team_abbr(home_team)
        away_abbr = _team_abbr(away_team)

        home_players: list = []
        away_players: list = []

        for player_name, lines in player_lines.items():
            if not lines:
                continue
            team_abbr = _assign_to_team(
                player_name,
                home_team, home_abbr, home_roster_names,
                away_team, away_abbr, away_roster_names,
            )
            props = []
            for stat, stat_data in lines.items():
                line_val = stat_data["line"]
                op       = stat_data["over_prob"]
                props.append(_build_prop_card(stat, line_val, stat_labels,
                                              sport_name, over_prob=op))

            # Primary stat value used for sort (first available)
            primary_data = lines.get(primary, {})
            primary_val  = primary_data.get("line", 0.0) if isinstance(primary_data, dict) else float(primary_data or 0)

            card = {
                "name":  player_name,
                "pos":   "",
                "pts":   primary_val,
                "props": props,
            }
            if team_abbr == home_abbr:
                home_players.append(card)
            else:
                away_players.append(card)

        # Sort by primary stat line descending
        home_players.sort(key=lambda p: p["pts"], reverse=True)
        away_players.sort(key=lambda p: p["pts"], reverse=True)

        # Compute predicted winner for today.html compatibility
        if home_prob >= away_prob:
            predicted_winner = home_team
            win_prob_val     = home_prob
        else:
            predicted_winner = away_team
            win_prob_val     = away_prob
        conf_str = "HIGH" if win_prob_val >= 65 else "MEDIUM" if win_prob_val >= 55 else "LOW"

        # Best single prop across all players for "TOP PICK" banner
        all_game_props: list = []
        for _pl, _abbr in [(home_players, home_abbr), (away_players, away_abbr)]:
            for _p in _pl:
                for _pr in _p.get("props", []):
                    _conf = _pr.get("over_prob" if _pr["pick"] != "UNDER" else "under_prob", 0)
                    all_game_props.append({
                        "player":     _p["name"],
                        "team":       _abbr,
                        "stat":       _pr.get("label", _pr.get("stat", "")),
                        "line":       _pr["line"],
                        "pick":       _pr["pick"],
                        "confidence": _conf,
                        "conf_label": _pr.get("conf_label", ""),
                    })
        all_game_props.sort(key=lambda x: x["confidence"], reverse=True)
        top_pick = all_game_props[0] if all_game_props else None

        # Win badge label for game header
        best_prob = max(home_prob, away_prob)
        best_team = home_team if home_prob >= away_prob else away_team
        best_team_short = best_team.split()[-1]
        if best_prob >= 70:
            pick_badge_label = f"🔥 Strong Fav · {best_team_short}"
            pick_badge_class = "badge-strong"
        elif best_prob >= 60:
            pick_badge_label = f"✅ Favoured · {best_team_short}"
            pick_badge_class = "badge-favoured"
        elif best_prob >= 55:
            pick_badge_label = f"📊 Slight Edge · {best_team_short}"
            pick_badge_class = "badge-slight"
        else:
            pick_badge_label = "⚖️ Pick'em"
            pick_badge_class = "badge-pickem"

        games.append({
            "sport":      cfg["sport_label"],
            "league":     cfg["league"],
            "sport_icon": cfg["icon"],
            "home_team":  home_team,
            "away_team":  away_team,
            "home_abbr":  home_abbr,
            "away_abbr":  away_abbr,
            "home_prob":  home_prob,
            "away_prob":  away_prob,
            "home_roster": home_players[:12],
            "away_roster": away_players[:12],
            "kickoff":    event.get("commence_time", ""),
            "status":     "STATUS_SCHEDULED",
            "spread":     None,
            "over_under": None,
            "bookmaker":  "",
            "book_home_ml":     None,
            "book_away_ml":     None,
            "book_home_spread": None,
            "book_total":       None,
            # today.html compatibility
            "predicted_winner": predicted_winner,
            "win_prob":         win_prob_val,
            "confidence":       conf_str,
            "draw_prob":        None,
            "home_score":       None,
            "away_score":       None,
            "home_wins":        0,
            "home_losses":      0,
            "away_wins":        0,
            "away_losses":      0,
            # Smart pick badges
            "top_pick":         top_pick,
            "pick_badge_label": pick_badge_label,
            "pick_badge_class": pick_badge_class,
        })

    print(f"[ODDS] {sport_name}: {len(games)} games built")
    return games


SOCCER_LEAGUES: Dict[str, Dict] = {
    "epl":        {"sport_key": "soccer_epl",                "league": "Premier League",   "icon": "🏴󠁧󠁢󠁥󠁮󠁧󠁿"},
    "laliga":     {"sport_key": "soccer_spain_la_liga",      "league": "La Liga",          "icon": "🇪🇸"},
    "ucl":        {"sport_key": "soccer_uefa_champs_league", "league": "Champions League", "icon": "⭐"},
    "seriea":     {"sport_key": "soccer_italy_serie_a",      "league": "Serie A",          "icon": "🇮🇹"},
    "bundesliga": {"sport_key": "soccer_germany_bundesliga", "league": "Bundesliga",       "icon": "🇩🇪"},
    "ligue1":     {"sport_key": "soccer_france_ligue_one",   "league": "Ligue 1",          "icon": "🇫🇷"},
    "mls":        {"sport_key": "soccer_usa_mls",            "league": "MLS",              "icon": "🇺🇸"},
}

_SOCCER_CACHE: Dict[str, Tuple] = {}
_SOCCER_LOCK = threading.Lock()


def _american_to_implied(price: float) -> float:
    """Convert American odds price to implied probability (0.0–1.0)."""
    if price > 0:
        return 100.0 / (price + 100.0)
    elif price < 0:
        return abs(price) / (abs(price) + 100.0)
    return 0.5


def _fmt_american(price: float) -> str:
    """Format American odds price as string (+150, -110)."""
    if price >= 0:
        return f"+{round(price)}"
    return str(round(price))


def build_soccer_props(ttl: int = 900) -> list:
    """Build soccer match data for all leagues from Odds API only.

    Returns list of game dicts with:
        home_team, away_team, home_prob, away_prob, draw_prob,
        btts_yes_prob, book_total, predicted_winner, win_prob, confidence,
        players (list of {name, goal_scorer_prob, first_scorer_prob,
                           shots_on_target_line, shots_on_target_prob,
                           shots_line, shots_prob}),
        home_roster / away_roster (today.html compatible format).
    """
    api_key = _key()
    if not api_key:
        logger.warning("[SOCCER] ODDS_API_KEY not set — no soccer props")
        return []

    cache_key = "soccer_all"
    now = time.time()
    with _SOCCER_LOCK:
        cached = _SOCCER_CACHE.get(cache_key)
        if cached:
            data, expires = cached
            if now < expires:
                return data

    all_games: list = []
    upcoming:  list = []   # fixtures with no odds posted yet

    for league_slug, league_cfg in SOCCER_LEAGUES.items():
        sport_key   = league_cfg["sport_key"]
        league_name = league_cfg["league"]
        icon        = league_cfg["icon"]

        # Events list
        events = fetch(
            f"{ODDS_BASE}/sports/{sport_key}/events",
            params={"apiKey": api_key, "dateFormat": "iso"},
            use_cache=False, timeout=12,
        )
        if not events or not isinstance(events, list):
            continue
        print(f"[SOCCER] {league_name}: {len(events)} events")

        # Match odds: h2h (3-way) + btts + totals in one bulk call
        match_odds_resp = fetch(
            f"{ODDS_BASE}/sports/{sport_key}/odds",
            params={
                "apiKey":     api_key,
                "regions":    "us,uk",
                "markets":    "h2h,btts,totals",
                "oddsFormat": "american",
            },
            use_cache=False, timeout=12,
        )
        match_odds_map: Dict[str, dict] = {}
        if match_odds_resp and isinstance(match_odds_resp, list):
            for mo in match_odds_resp:
                h = mo.get("home_team", "")
                a = mo.get("away_team", "")
                match_odds_map[f"{h}|{a}"] = mo

        for event in events[:10]:
            event_id  = event.get("id", "")
            home_team = event.get("home_team", "")
            away_team = event.get("away_team", "")
            if not home_team or not away_team:
                continue

            mo = match_odds_map.get(f"{home_team}|{away_team}", {})

            home_prob = draw_prob = away_prob = None
            btts_yes_prob = None
            total_goals_line = None
            book_home_ml = book_draw_ml = book_away_ml = None
            bookmaker_name = ""

            for bm in mo.get("bookmakers", [])[:1]:
                bookmaker_name = bm.get("title", "")
                for market in bm.get("markets", []):
                    mk       = market.get("key", "")
                    outcomes = market.get("outcomes", [])

                    if mk == "h2h":
                        raw: Dict[str, float] = {}
                        for o in outcomes:
                            name  = o.get("name", "")
                            price = float(o.get("price", 0) or 0)
                            raw[name] = _american_to_implied(price)
                            if name == home_team:
                                book_home_ml = _fmt_american(price)
                            elif name == away_team:
                                book_away_ml = _fmt_american(price)
                            elif name.lower() == "draw":
                                book_draw_ml = _fmt_american(price)
                        if raw:
                            total = sum(raw.values())
                            norm  = {k: round(v / total * 100, 1) for k, v in raw.items()}
                            home_prob = norm.get(home_team, 33.3)
                            away_prob = norm.get(away_team, 33.3)
                            draw_prob = norm.get("Draw", round(100.0 - home_prob - away_prob, 1))

                    elif mk == "btts":
                        raw_btts: Dict[str, float] = {}
                        for o in outcomes:
                            name  = o.get("name", "").lower()
                            price = float(o.get("price", 0) or 0)
                            raw_btts[name] = _american_to_implied(price)
                        if raw_btts:
                            btotal = sum(raw_btts.values())
                            bnorm  = {k: round(v / btotal * 100, 1) for k, v in raw_btts.items()}
                            btts_yes_prob = bnorm.get("yes")

                    elif mk == "totals":
                        for o in outcomes:
                            if o.get("name") == "Over":
                                total_goals_line = o.get("point")
                                break

            # No bookmaker lines posted yet — save as upcoming fixture and skip
            if home_prob is None:
                upcoming.append({
                    "home_team":  home_team,
                    "away_team":  away_team,
                    "league":     league_name,
                    "sport_icon": icon,
                    "kickoff":    event.get("commence_time", ""),
                })
                continue

            # Predicted winner
            if home_prob >= draw_prob and home_prob >= away_prob:
                predicted_winner, win_prob_val = home_team, home_prob
            elif draw_prob >= away_prob:
                predicted_winner, win_prob_val = "Draw", draw_prob
            else:
                predicted_winner, win_prob_val = away_team, away_prob

            conf_str = "HIGH" if win_prob_val >= 55 else ("MEDIUM" if win_prob_val >= 45 else "LOW")

            # Match pick label
            if win_prob_val >= 70:
                match_pick_label = "🔥 Strong Favourite"
                pick_badge_class = "badge-strong"
            elif win_prob_val >= 60:
                match_pick_label = "✅ Favoured"
                pick_badge_class = "badge-favoured"
            elif win_prob_val >= 50:
                match_pick_label = "📊 Slight Edge"
                pick_badge_class = "badge-slight"
            else:
                match_pick_label = "⚖️ Pick'em"
                pick_badge_class = "badge-pickem"

            # Per-event player props
            prop_markets = ("player_goal_scorer,player_first_goal_scorer,"
                            "player_shots_on_target,player_shots")
            props_resp = fetch(
                f"{ODDS_BASE}/sports/{sport_key}/events/{event_id}/odds",
                params={
                    "apiKey":     api_key,
                    "regions":    "us,uk",
                    "markets":    prop_markets,
                    "bookmakers": "draftkings,fanduel,betmgm,bet365",
                    "oddsFormat": "american",
                },
                use_cache=False, timeout=10,
            )

            player_data: Dict[str, Dict] = defaultdict(dict)
            if props_resp and isinstance(props_resp, dict):
                for bm in props_resp.get("bookmakers", [])[:1]:
                    for market in bm.get("markets", []):
                        mk       = market.get("key", "")
                        for outcome in market.get("outcomes", []):
                            # Player name: prefer description field
                            player = (outcome.get("description") or "").strip()
                            if not player:
                                # For shots markets 'name' IS the player
                                if mk in ("player_shots_on_target", "player_shots"):
                                    continue
                                player = outcome.get("name", "").strip()
                            if not player:
                                continue

                            price  = float(outcome.get("price", -110) or -110)
                            op     = round(_american_to_implied(price) * 100, 1)
                            oname  = outcome.get("name", "")
                            line   = outcome.get("point")

                            if mk == "player_goal_scorer" and oname == "Yes":
                                if "goal_scorer_prob" not in player_data[player]:
                                    player_data[player]["goal_scorer_prob"] = op
                            elif mk == "player_first_goal_scorer" and oname == "Yes":
                                if "first_scorer_prob" not in player_data[player]:
                                    player_data[player]["first_scorer_prob"] = op
                            elif mk == "player_shots_on_target" and oname == "Over" and line is not None:
                                if "shots_on_target_line" not in player_data[player]:
                                    player_data[player]["shots_on_target_line"] = float(line)
                                    player_data[player]["shots_on_target_prob"] = op
                            elif mk == "player_shots" and oname == "Over" and line is not None:
                                if "shots_line" not in player_data[player]:
                                    player_data[player]["shots_line"] = float(line)
                                    player_data[player]["shots_prob"] = op

            # Build sorted player list
            players = []
            for pname, pdata in player_data.items():
                if not pdata:
                    continue
                players.append({
                    "name":                 pname,
                    "goal_scorer_prob":     pdata.get("goal_scorer_prob"),
                    "first_scorer_prob":    pdata.get("first_scorer_prob"),
                    "shots_on_target_line": pdata.get("shots_on_target_line"),
                    "shots_on_target_prob": pdata.get("shots_on_target_prob"),
                    "shots_line":           pdata.get("shots_line"),
                    "shots_prob":           pdata.get("shots_prob"),
                })
            players.sort(key=lambda p: p.get("goal_scorer_prob") or 0, reverse=True)

            # today.html compat: convert to goal_prob roster format
            def _to_today_roster(plist):
                out = []
                for p in plist:
                    gp = p.get("goal_scorer_prob") or 0
                    out.append({
                        "name":       p["name"],
                        "shots_pg":   None,
                        "xg_shot":    None,
                        "goal_prob":  gp,
                        "confidence": "HIGH" if gp > 35 else ("MEDIUM" if gp > 20 else "LOW"),
                    })
                return out

            half = max(len(players) // 2, 1)
            home_roster_td = _to_today_roster(players[:half])
            away_roster_td = _to_today_roster(players[half:])

            print(f"[SOCCER] {home_team} vs {away_team}: {len(players)} players")

            all_games.append({
                "sport":      "Soccer",
                "league":     league_name,
                "sport_icon": icon,
                "is_soccer":  True,
                "home_team":  home_team,
                "away_team":  away_team,
                "home_prob":  home_prob,
                "away_prob":  away_prob,
                "draw_prob":  draw_prob,
                "kickoff":    event.get("commence_time", ""),
                "status":     "STATUS_SCHEDULED",
                "home_score": None,
                "away_score": None,
                "spread":     None,
                "over_under": total_goals_line,
                "bookmaker":  bookmaker_name,
                "book_home_ml": book_home_ml,
                "book_away_ml": book_away_ml,
                "book_draw_ml": book_draw_ml,
                "book_total":   total_goals_line,
                "btts_yes_prob": btts_yes_prob,
                "predicted_winner": predicted_winner,
                "win_prob":         win_prob_val,
                "confidence":       conf_str,
                # Match pick badge
                "match_pick_label": match_pick_label,
                "pick_badge_class": pick_badge_class,
                # Rich player data for soccer.html
                "players":      players[:20],
                # today.html compat
                "home_roster":  home_roster_td,
                "away_roster":  away_roster_td,
                "home_wins":    0, "home_losses": 0,
                "away_wins":    0, "away_losses": 0,
            })

    print(f"[SOCCER] Total: {len(all_games)} active games, {len(upcoming)} upcoming (no odds yet)")
    result = {"games": all_games, "upcoming": upcoming}
    with _SOCCER_LOCK:
        _SOCCER_CACHE[cache_key] = (result, now + ttl)
    return result


def get_sport_odds(sport: str, regions: str = "us,uk") -> List[Dict]:
    """
    Fetch upcoming game odds for a sport from The Odds API.
    Returns list of game dicts with moneyline, spread, and total odds.
    Returns [] if ODDS_API_KEY is not set.
    """
    api_key = _key()
    if not api_key:
        return []
    sport_key = SPORT_KEYS.get(sport.upper())
    if not sport_key:
        return []

    data = fetch(
        f"{ODDS_BASE}/sports/{sport_key}/odds",
        params={
            "apiKey":      api_key,
            "regions":     regions,
            "markets":     "h2h,spreads,totals",
            "oddsFormat":  "decimal",
            "dateFormat":  "iso",
        },
        timeout=15,
    )
    if not data or not isinstance(data, list):
        return []

    results = []
    for event in data:
        home       = event.get("home_team", "")
        away       = event.get("away_team", "")
        bookmakers = event.get("bookmakers", [])

        # Choose best available bookmaker by priority
        chosen = None
        for pkey in BOOKMAKER_PRIORITY:
            for bm in bookmakers:
                if bm.get("key") == pkey:
                    chosen = bm
                    break
            if chosen:
                break
        if not chosen and bookmakers:
            chosen = bookmakers[0]

        g: Dict = {
            "home_team":      home,
            "away_team":      away,
            "commence":       event.get("commence_time", ""),
            "bookmaker":      chosen.get("title", "") if chosen else "",
            "home_ml":        None,
            "away_ml":        None,
            "home_spread":    None,
            "away_spread":    None,
            "total_line":     None,
            "over_odds":      None,
            "under_odds":     None,
        }

        if chosen:
            for market in chosen.get("markets", []):
                mk       = market.get("key", "")
                outcomes = market.get("outcomes", [])
                if mk == "h2h":
                    for o in outcomes:
                        if o["name"] == home:
                            g["home_ml"] = round(float(o["price"]), 2)
                        elif o["name"] == away:
                            g["away_ml"] = round(float(o["price"]), 2)
                elif mk == "spreads":
                    for o in outcomes:
                        if o["name"] == home:
                            g["home_spread"] = o.get("point")
                        elif o["name"] == away:
                            g["away_spread"] = o.get("point")
                elif mk == "totals":
                    for o in outcomes:
                        if o["name"] == "Over":
                            g["total_line"] = o.get("point")
                            g["over_odds"]  = round(float(o["price"]), 2)
                        elif o["name"] == "Under":
                            g["under_odds"] = round(float(o["price"]), 2)

        results.append(g)
    return results


def build_odds_lookup(sport: str) -> Dict[tuple, Dict]:
    """
    Returns {(_norm(home), _norm(away)): odds_dict} for fuzzy matching.
    """
    return {(_norm(g["home_team"]), _norm(g["away_team"])): g
            for g in get_sport_odds(sport)}


def find_game_odds(
    lookup: Dict[tuple, Dict],
    home_team: str,
    away_team: str,
) -> Optional[Dict]:
    """
    Find odds for a game using normalized team name matching.
    Falls back to partial/substring matching when exact match fails.
    """
    h = _norm(home_team)
    a = _norm(away_team)

    if (h, a) in lookup:
        return lookup[(h, a)]

    # Partial match — covers cases like "Man City" vs "Manchester City"
    for (oh, oa), odds in lookup.items():
        if (h in oh or oh in h) and (a in oa or oa in a):
            return odds
    return None


def _norm(name: str) -> str:
    """Lowercase last meaningful word for fuzzy matching."""
    clean  = re.sub(r"[^a-z0-9 ]", "", name.lower().strip())
    tokens = clean.split()
    return tokens[-1] if tokens else clean


def decimal_to_american(dec: float) -> str:
    """Convert decimal odds to American format string (+150, -160)."""
    if dec >= 2.0:
        return f"+{round((dec - 1) * 100)}"
    return f"{round(-100 / (dec - 1))}"
