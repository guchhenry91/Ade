"""
MLB props model.
Calculates OVER/UNDER probabilities for pitcher strikeouts,
batter hits, total bases, home runs, RBIs, and runs.
"""
from __future__ import annotations
import logging
from typing import Dict, List, Optional, Tuple

from utils.stats import shot_attempt_over_under, confidence_label

logger = logging.getLogger(__name__)

# (display_label, stat_key, std_factor, min_avg_threshold)
_PITCHER_MARKETS: List[Tuple[str, str, float, float]] = [
    ("Strikeouts",      "so_pg",  0.35, 1.0),
    ("Innings Pitched", "ip_pg",  0.20, 3.0),
]

_BATTER_MARKETS: List[Tuple[str, str, float, float]] = [
    ("Hits",         "hits_pg", 0.55, 0.3),
    ("Total Bases",  "tb_pg",   0.50, 0.5),
    ("Runs",         "runs_pg", 0.60, 0.3),
]


def build_pitcher_props(player: Dict) -> List[Dict]:
    """Build prop cards for a pitcher from season-average stats.
    If player dict contains a '<stat>_line' key, uses that as the market line
    instead of generating one from the season avg (avoids constant-probability bug).
    """
    props: List[Dict] = []
    for label, key, std, min_avg in _PITCHER_MARKETS:
        avg = float(player.get(key, 0) or 0)
        if avg < min_avg:
            continue
        # Use pp_line if available, otherwise generate from avg
        line_key = key[:-3] + "_line"  # "so_pg" → "so_line"
        raw_line = player.get(line_key)
        line     = float(raw_line) if raw_line else max(0.5, round(avg * 2) / 2 - 0.5)
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


def build_batter_props(player: Dict) -> List[Dict]:
    """Build prop cards for a batter from season-average stats.
    Supports '<stat>_line' override keys for market lines.
    """
    props: List[Dict] = []
    for label, key, std, min_avg in _BATTER_MARKETS:
        avg = float(player.get(key, 0) or 0)
        if avg < min_avg:
            continue
        line_key = key[:-3] + "_line"  # "hits_pg" → "hits_line", "tb_pg" → "tb_line"
        raw_line = player.get(line_key)
        line     = float(raw_line) if raw_line else max(0.5, round(avg * 2) / 2 - 0.5)
        over_p, under_p = shot_attempt_over_under(avg, line, std_factor=std)
        pick = "OVER" if over_p > 0.55 else ("UNDER" if over_p < 0.45 else "FAIR")
        props.append({
            "stat":       label,
            "avg":        round(avg, 2),
            "line":       line,
            "over_prob":  round(over_p  * 100, 1),
            "under_prob": round(under_p * 100, 1),
            "pick":       pick,
            "confidence": confidence_label(max(over_p, under_p)),
            "stars":      _stars(max(over_p, under_p)),
        })
    return props


def build_props_from_prizepicks(pp_projections: List[Dict]) -> List[Dict]:
    """
    Build prop cards directly from PrizePicks projections for MLB.
    The PrizePicks line IS the market line — we calculate our O/U prob
    using the league-average std_factor for that stat type.
    """
    _pp_stat_map: Dict[str, Tuple[str, float]] = {
        "Strikeouts":    ("so",    0.35),
        "Hits":          ("hits",  0.55),
        "Home Runs":     ("hr",    0.90),
        "Total Bases":   ("tb",    0.50),
        "RBIs":          ("rbi",   0.65),
        "Runs":          ("runs",  0.60),
        "Earned Runs":   ("er",    0.60),
        "Walks":         ("walks", 0.60),
        "Saves":         ("saves", 0.50),
    }

    results: List[Dict] = []
    for proj in pp_projections:
        stat_raw  = proj.get("stat", "")
        line      = float(proj.get("line", 0) or 0)
        if line <= 0:
            continue

        # Normalize stat name
        stat_label = stat_raw
        std_factor = 0.50  # default
        for key, (_, sf) in _pp_stat_map.items():
            if key.lower() in stat_raw.lower():
                stat_label = key
                std_factor = sf
                break

        # Use the line as the avg estimate (market-efficient: line ≈ median)
        avg_est = line
        over_p, under_p = shot_attempt_over_under(avg_est, line, std_factor=std_factor)
        pick = "OVER" if over_p > 0.55 else ("UNDER" if over_p < 0.45 else "FAIR")

        results.append({
            "name":       proj.get("name", ""),
            "team":       proj.get("team", ""),
            "pos":        proj.get("pos", ""),
            "stat":       stat_label,
            "line":       line,
            "pp_line":    line,
            "avg":        round(avg_est, 1),
            "over_prob":  round(over_p  * 100, 1),
            "under_prob": round(under_p * 100, 1),
            "pick":       pick,
            "confidence": confidence_label(max(over_p, under_p)),
            "stars":      _stars(max(over_p, under_p)),
            "source":     "PrizePicks",
        })

    return results


def _stars(prob: float) -> int:
    """Convert probability to 1-5 star rating."""
    if prob >= 0.85:
        return 5
    if prob >= 0.75:
        return 4
    if prob >= 0.65:
        return 3
    if prob >= 0.55:
        return 2
    return 1
