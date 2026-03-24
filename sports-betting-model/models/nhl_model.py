"""
NHL props model.
Calculates OVER/UNDER probabilities for skater points/goals/assists/shots
and goalie saves/save-percentage.

Key fix (March 2026):
- Goals/Assists/Points props at line 0.5 are BINARY events.
  Use Poisson P(X ≥ 1) = 1 − e^(−λ) rather than normal distribution.
  Old code set avg_est = line which always produced exactly 50% confidence.
- Position-based λ priors used when real per-game averages are unavailable.
"""
from __future__ import annotations
import math
import logging
from typing import Dict, List, Optional, Tuple

from utils.stats import shot_attempt_over_under, threept_made_ou, confidence_label

logger = logging.getLogger(__name__)

# (display_label, stat_key, std_factor, min_avg)
_SKATER_MARKETS: List[Tuple[str, str, float, float]] = [
    ("Points",       "pts_pg",     0.80, 0.20),
    ("Goals",        "goals_pg",   0.90, 0.10),
    ("Assists",      "assists_pg", 0.85, 0.10),
    ("Shots",        "shots_pg",   0.35, 1.0),
]

_GOALIE_MARKETS: List[Tuple[str, str, float, float]] = [
    ("Saves",        "saves_pg",   0.18, 15.0),
]

# Binary NHL stats — at line 0.5, use Poisson P(X ≥ 1) = 1 − e^(−λ)
_BINARY_STATS = frozenset({"Goals", "Assists", "Points"})

# Position-based λ priors (goals/assists/points per game) for when real stats
# are unavailable. These are conservative mid-season NHL league averages.
_NHL_STAT_PRIORS: Dict[str, Dict[str, float]] = {
    "Goals": {
        "C": 0.35, "LW": 0.32, "RW": 0.32, "D": 0.18, "G": 0.01, "": 0.28,
    },
    "Assists": {
        "C": 0.45, "LW": 0.40, "RW": 0.38, "D": 0.42, "G": 0.01, "": 0.40,
    },
    "Points": {
        "C": 0.80, "LW": 0.72, "RW": 0.70, "D": 0.60, "G": 0.01, "": 0.70,
    },
    "Shots": {
        "C": 3.2,  "LW": 3.0,  "RW": 3.0,  "D": 2.2,  "G": 1.0,  "": 2.8,
    },
    "Saves": {
        "G": 25.0, "": 25.0,
    },
}


def _poisson_prob_score(lam: float) -> float:
    """P(X ≥ 1) where X ~ Poisson(lambda). P = 1 − e^(−λ).
    Used for binary 0.5-line props (will this player score/assist tonight?).
    """
    if lam <= 0:
        return 0.05
    return round(1.0 - math.exp(-lam), 4)


def _stars(prob: float) -> int:
    if prob >= 0.85:
        return 5
    if prob >= 0.75:
        return 4
    if prob >= 0.65:
        return 3
    if prob >= 0.55:
        return 2
    return 1


def _nhl_over_prob(stat_label: str, avg: float, line: float, std: float) -> Tuple[float, float]:
    """Compute (over_prob, under_prob) for an NHL stat.

    For Goals/Assists/Points at line ≤ 0.5: Poisson (binary event).
    For Shots/Saves (counting stats): normal distribution.
    """
    is_binary = stat_label in _BINARY_STATS and line <= 0.5
    if is_binary:
        over_p = _poisson_prob_score(avg)
        under_p = 1.0 - over_p
    else:
        over_p, under_p = shot_attempt_over_under(avg, line, std_factor=std)
    return over_p, under_p


def build_skater_props(player: Dict) -> List[Dict]:
    """Build prop cards for a skater using real NHL per-game averages.
    If player dict contains a '<stat>_line' key, uses that as the market line.
    """
    props: List[Dict] = []
    for label, key, std, min_avg in _SKATER_MARKETS:
        avg = float(player.get(key, 0) or 0)
        if avg < min_avg:
            continue
        # Check for Odds API line override: "pts_pg" → "pts_line", "goals_pg" → "goals_line"
        line_key = key.replace("_pg", "_line")
        raw_line = player.get(line_key)
        line = float(raw_line) if raw_line else max(0.5, round(avg * 2) / 2 - 0.5)
        over_p, under_p = _nhl_over_prob(label, avg, line, std)
        pick = "OVER" if over_p > 0.55 else ("UNDER" if over_p < 0.45 else "FAIR")
        props.append({
            "stat":       label,
            "avg":        round(avg, 2) if avg < 2 else round(avg, 1),
            "line":       line,
            "over_prob":  round(over_p  * 100, 1),
            "under_prob": round(under_p * 100, 1),
            "pick":       pick,
            "confidence": confidence_label(max(over_p, under_p)),
            "stars":      _stars(max(over_p, under_p)),
        })
    return props


def build_goalie_props(player: Dict) -> List[Dict]:
    """Build prop cards for a goalie.
    If player dict contains a '<stat>_line' key, uses that as the market line.
    """
    props: List[Dict] = []
    for label, key, std, min_avg in _GOALIE_MARKETS:
        avg = float(player.get(key, 0) or 0)
        if avg < min_avg:
            continue
        line_key = key.replace("_pg", "_line")
        raw_line = player.get(line_key)
        line = float(raw_line) if raw_line else max(0.5, round(avg * 2) / 2 - 0.5)
        over_p, under_p = shot_attempt_over_under(avg, line, std_factor=std)
        pick = "OVER" if over_p > 0.55 else ("UNDER" if over_p < 0.45 else "FAIR")
        props.append({
            "stat":       label,
            "avg":        round(avg, 1),
            "line":       line,
            "over_prob":  round(over_p  * 100, 1),
            "under_prob": round(under_p * 100, 1),
            "pick":       pick,
            "confidence": confidence_label(max(over_p, under_p)),
            "stars":      _stars(max(over_p, under_p)),
        })
    return props


# MMA/UFC stats that should never appear in NHL data — reject entire batch if found
_MMA_REJECT_STATS = frozenset({
    "total rounds", "significant strikes", "takedowns", "submission attempts",
    "knockdowns", "control time",
})
# Valid hockey stat keywords — at least one must match for the batch to be accepted
_HOCKEY_VALID_STATS = frozenset({
    "goals", "assists", "points", "shots", "saves", "blocks",
    "hits", "power play", "plus minus",
})


def _normalize_nhl_stat(stat_raw: str) -> Tuple[str, float]:
    """
    Map a raw PrizePicks stat_type string to (display_label, std_factor).
    Longer/more-specific patterns checked first to avoid substring collisions:
      - "goals" IS a substring of "shots on goal" → check "shots on goal" first
      - "points" IS a substring of "power play points" → check "power play" first
    """
    s = stat_raw.lower().strip()

    # Most specific multi-word patterns first
    if "shots on goal" in s:        return "Shots",       0.35
    if "power play" in s:           return "Power Play",  0.85
    if "blocked shot" in s or "blocks" in s: return "Shots",  0.35

    # Single-word matches
    if "saves" in s or s == "sv":   return "Saves",       0.18
    if "assists" in s:              return "Assists",      0.85
    if "goals" in s:                return "Goals",        0.90
    if "shots" in s:                return "Shots",        0.35
    if "points" in s:               return "Points",       0.80

    # Fallback: keep original label
    return stat_raw, 0.60


def build_props_from_prizepicks(
    pp_projections: List[Dict],
    player_stats_map: Optional[Dict[str, Dict]] = None,
) -> List[Dict]:
    """
    Build NHL prop cards from PrizePicks projections.
    Rejects the entire batch if it contains MMA stats (wrong league_id).
    Returns flat list — one entry per (player × stat).

    player_stats_map: optional {name_lower → {goals_pg, assists_pg, pts_pg,
                                               shots_pg, saves_pg}} from NHL API.
                      When provided, real per-game averages are used for
                      probability calculation instead of position priors.
    """
    # Guard: reject MMA data masquerading as NHL
    all_stats_lower = [(p.get("stat") or "").lower() for p in pp_projections]
    has_mma    = any(any(mma in s for mma in _MMA_REJECT_STATS) for s in all_stats_lower)
    has_hockey = any(any(hk in s  for hk in _HOCKEY_VALID_STATS) for s in all_stats_lower)
    if has_mma and not has_hockey:
        logger.warning("NHL PrizePicks batch appears to be MMA data — discarding %d projections",
                       len(pp_projections))
        return []

    # Stat label → key in player_stats_map dict
    _LABEL_TO_STAT_KEY = {
        "Goals":   "goals_pg",
        "Assists":  "assists_pg",
        "Points":   "pts_pg",
        "Shots":    "shots_pg",
        "Saves":    "saves_pg",
    }

    results: List[Dict] = []
    for proj in pp_projections:
        stat_raw = proj.get("stat", "")
        # Skip individual MMA stats that slipped through
        if any(mma in stat_raw.lower() for mma in _MMA_REJECT_STATS):
            continue
        line = float(proj.get("line", 0) or 0)
        if line <= 0:
            continue

        stat_label, std_factor = _normalize_nhl_stat(stat_raw)

        # Determine best available average for this player + stat
        player_name = proj.get("name", "")
        pos         = (proj.get("pos") or "").upper()
        name_lower  = player_name.lower()

        real_stats = (player_stats_map or {}).get(name_lower, {})
        stat_key   = _LABEL_TO_STAT_KEY.get(stat_label)
        real_avg   = float(real_stats.get(stat_key, 0)) if stat_key else 0.0

        if real_avg > 0:
            # Real NHL per-game average available — most accurate
            avg_est = real_avg
        else:
            # Fall back to position-based prior (better than avg_est = line)
            pos_priors = _NHL_STAT_PRIORS.get(stat_label, {})
            avg_est = pos_priors.get(pos, pos_priors.get("", 0))
            if avg_est <= 0:
                avg_est = line  # last resort: line-based (50/50 estimate)

        over_p, under_p = _nhl_over_prob(stat_label, avg_est, line, std_factor)
        pick = "OVER" if over_p > 0.55 else ("UNDER" if over_p < 0.45 else "FAIR")

        results.append({
            "name":       player_name,
            "team":       proj.get("team", ""),
            "pos":        proj.get("pos", ""),
            "stat":       stat_label,
            "line":       line,
            "pp_line":    line,
            "avg":        round(avg_est, 2) if avg_est < 2 else round(avg_est, 1),
            "over_prob":  round(over_p  * 100, 1),
            "under_prob": round(under_p * 100, 1),
            "pick":       pick,
            "confidence": confidence_label(max(over_p, under_p)),
            "stars":      _stars(max(over_p, under_p)),
            "source":     "PrizePicks",
        })

    return results
