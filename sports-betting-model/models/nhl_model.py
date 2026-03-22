"""
NHL props model.
Calculates OVER/UNDER probabilities for skater points/goals/assists/shots
and goalie saves/save-percentage.
"""
from __future__ import annotations
import logging
from typing import Dict, List, Tuple

from utils.stats import shot_attempt_over_under, threept_made_ou, confidence_label

logger = logging.getLogger(__name__)

# (display_label, stat_key, std_factor, min_avg)
_SKATER_MARKETS: List[Tuple[str, str, float, float]] = [
    ("Points",       "pts_pg",     0.80, 0.20),
    ("Goals",        "goals_pg",   0.90, 0.10),
    ("Assists",      "assists_pg", 0.85, 0.10),
    ("Shots on Goal","shots_pg",   0.35, 1.0),
]

_GOALIE_MARKETS: List[Tuple[str, str, float, float]] = [
    ("Saves",        "saves_pg",   0.18, 15.0),
]


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


def build_skater_props(player: Dict) -> List[Dict]:
    """Build prop cards for a skater."""
    props: List[Dict] = []
    for label, key, std, min_avg in _SKATER_MARKETS:
        avg = float(player.get(key, 0) or 0)
        if avg < min_avg:
            continue
        # For low-average stats (goals, points < 1), use Poisson-like line floor
        line = max(0.5, round(avg * 2) / 2 - 0.5)
        over_p, under_p = shot_attempt_over_under(avg, line, std_factor=std)
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
    """Build prop cards for a goalie."""
    props: List[Dict] = []
    for label, key, std, min_avg in _GOALIE_MARKETS:
        avg = float(player.get(key, 0) or 0)
        if avg < min_avg:
            continue
        line = max(0.5, round(avg * 2) / 2 - 0.5)
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


def build_props_from_prizepicks(pp_projections: List[Dict]) -> List[Dict]:
    """
    Build NHL prop cards directly from PrizePicks projections.
    Rejects the entire batch if it contains MMA stats (wrong league_id).
    """
    # Guard: reject MMA data masquerading as NHL
    all_stats_lower = [(p.get("stat") or "").lower() for p in pp_projections]
    has_mma = any(any(mma in s for mma in _MMA_REJECT_STATS) for s in all_stats_lower)
    has_hockey = any(any(hk in s for hk in _HOCKEY_VALID_STATS) for s in all_stats_lower)
    if has_mma and not has_hockey:
        logger.warning("NHL PrizePicks batch appears to be MMA data — discarding %d projections",
                       len(pp_projections))
        return []

    _pp_stat_map: Dict[str, Tuple[str, float]] = {
        "Points":       ("pts",    0.80),
        "Goals":        ("goals",  0.90),
        "Assists":      ("assists",0.85),
        "Shots":        ("shots",  0.35),
        "Saves":        ("saves",  0.18),
        "Power Play":   ("pp",     0.85),
    }

    results: List[Dict] = []
    for proj in pp_projections:
        stat_raw = proj.get("stat", "")
        # Skip MMA stats that slipped through
        if any(mma in stat_raw.lower() for mma in _MMA_REJECT_STATS):
            continue
        line     = float(proj.get("line", 0) or 0)
        if line <= 0:
            continue

        stat_label = stat_raw
        std_factor = 0.60
        for key, (_, sf) in _pp_stat_map.items():
            if key.lower() in stat_raw.lower():
                stat_label = key
                std_factor = sf
                break

        # Market-efficient: PrizePicks line ≈ expected median → use as avg estimate
        # Variance around the line is captured by std_factor
        avg_est  = line
        over_p, under_p = shot_attempt_over_under(avg_est, line, std_factor=std_factor)
        pick = "OVER" if over_p > 0.55 else ("UNDER" if over_p < 0.45 else "FAIR")

        results.append({
            "name":       proj.get("name", ""),
            "team":       proj.get("team", ""),
            "pos":        proj.get("pos", ""),
            "stat":       stat_label,
            "line":       line,
            "pp_line":    line,
            "avg":        round(avg_est, 2),
            "over_prob":  round(over_p  * 100, 1),
            "under_prob": round(under_p * 100, 1),
            "pick":       pick,
            "confidence": confidence_label(max(over_p, under_p)),
            "stars":      _stars(max(over_p, under_p)),
            "source":     "PrizePicks",
        })

    return results
