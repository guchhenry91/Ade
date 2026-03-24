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
        "markets": ["player_points", "player_rebounds",
                    "player_assists", "player_threes"],
        "stat_map": {
            "player_points":   "PTS",
            "player_rebounds": "REB",
            "player_assists":  "AST",
            "player_threes":   "3PM",
        },
        "stat_labels": {
            "PTS": "Points", "REB": "Rebounds",
            "AST": "Assists", "3PM": "3-Pointers",
        },
    },
    "mlb": {
        "sport_key": "baseball_mlb",
        "sport_label": "Baseball",
        "league": "MLB",
        "icon": "⚾",
        "espn_path": "baseball/mlb",
        "primary_stat": "hits",
        "markets": ["batter_hits", "batter_total_bases", "batter_home_runs",
                    "batter_rbis", "batter_runs_scored", "pitcher_strikeouts",
                    "pitcher_innings_pitched"],
        "stat_map": {
            "batter_hits":             "hits",
            "batter_total_bases":      "total_bases",
            "batter_home_runs":        "home_runs",
            "batter_rbis":             "rbi",
            "batter_runs_scored":      "runs",
            "pitcher_strikeouts":      "strikeouts",
            "pitcher_innings_pitched": "innings",
        },
        "stat_labels": {
            "hits": "Hits", "total_bases": "Total Bases",
            "home_runs": "Home Runs", "rbi": "RBI",
            "runs": "Runs", "strikeouts": "Strikeouts",
            "innings": "Innings Pitched",
        },
    },
    "nhl": {
        "sport_key": "icehockey_nhl",
        "sport_label": "Ice Hockey",
        "league": "NHL",
        "icon": "🏒",
        "espn_path": "hockey/nhl",
        "primary_stat": "shots",
        "markets": ["player_points", "player_shots_on_goal",
                    "player_goals", "player_assists"],
        "stat_map": {
            "player_points":        "points",
            "player_shots_on_goal": "shots",
            "player_goals":         "goals",
            "player_assists":       "assists",
        },
        "stat_labels": {
            "points": "Points", "shots": "Shots on Goal",
            "goals": "Goals", "assists": "Assists",
        },
    },
}

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

    for event in events[:15]:
        event_id  = event.get("id", "")
        home_team = event.get("home_team", "")
        away_team = event.get("away_team", "")
        if not home_team or not away_team:
            continue

        # Win probabilities from h2h odds
        probs    = h2h_probs.get(f"{home_team}|{away_team}", {})
        home_prob = probs.get(home_team, 50.0)
        away_prob = probs.get(away_team, 100.0 - home_prob)

        # Step 3: player props for this game
        props_data = fetch(
            f"{ODDS_BASE}/sports/{sport_key}/events/{event_id}/odds",
            params={
                "apiKey":     api_key,
                "regions":    "us",
                "markets":    markets_str,
                "bookmakers": "draftkings,fanduel,betmgm",
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
                for outcome in market.get("outcomes", []):
                    if outcome.get("name") != "Over":
                        continue
                    player = outcome.get("description", "")
                    line   = outcome.get("point")
                    price  = float(outcome.get("price", -110) or -110)
                    if player and line is not None and stat not in player_lines[player]:
                        # Convert American odds to implied probability
                        if price > 0:
                            op = 100.0 / (price + 100.0)
                        else:
                            op = abs(price) / (abs(price) + 100.0)
                        player_lines[player][stat] = {
                            "line":     float(line),
                            "over_prob": round(op, 4),
                        }

        if not player_lines:
            print(f"[ODDS] {home_team} vs {away_team}: no props")
            continue

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
        })

    print(f"[ODDS] {sport_name}: {len(games)} games with players")
    return games


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
