"""
MLB props model.
Calculates OVER/UNDER probabilities for pitcher strikeouts,
batter hits, total bases, home runs, RBIs, and runs.
"""
from __future__ import annotations
import math
import logging
from typing import Dict, List, Optional, Tuple

from utils.stats import shot_attempt_over_under, confidence_label


def _poisson_over(lam: float, line: float) -> float:
    """P(X > line) for X ~ Poisson(lam). For line=0.5: P(X>=1) = 1-e^(-lam)."""
    if lam <= 0:
        return 0.05
    if line <= 0.5:
        return min(0.92, max(0.05, 1.0 - math.exp(-lam)))
    k = int(math.ceil(line + 0.001))
    cdf = sum(math.exp(-lam) * (lam ** i) / math.factorial(i) for i in range(k))
    return min(0.92, max(0.05, 1.0 - cdf))

logger = logging.getLogger(__name__)

# (display_label, stat_key, std_factor, min_avg_threshold)
_PITCHER_MARKETS: List[Tuple[str, str, float, float]] = [
    ("Strikeouts",      "so_pg",  0.35, 1.0),
    ("Innings Pitched", "ip_pg",  0.20, 3.0),
]

_BATTER_MARKETS: List[Tuple[str, str, float, float]] = [
    ("Hits",        "hits_pg", 0.55, 0.30),
    ("Total Bases", "tb_pg",   0.50, 0.50),
    ("Home Runs",   "hr_pg",   0.90, 0.20),   # ~32 HR/season minimum (Poisson model)
    ("RBI",         "rbi_pg",  0.65, 0.20),   # ~33 RBI/season minimum
    ("Runs",        "runs_pg", 0.60, 0.30),
]

# Stats where Poisson is more accurate than normal dist (rare binary events per game)
_POISSON_STATS = frozenset({"Home Runs", "RBI", "Hits", "Runs"})


def build_pitcher_props(player: Dict) -> List[Dict]:
    """Build prop cards for a pitcher from season-average stats.
    If player dict contains a '<stat>_line' key, uses that as the market line.
    """
    props: List[Dict] = []
    for label, key, std, min_avg in _PITCHER_MARKETS:
        avg = float(player.get(key, 0) or 0)
        if avg < min_avg:
            continue
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
    Uses Poisson model for rare binary events (HR, Hits, RBI, Runs) at line ≤ 0.5.
    """
    props: List[Dict] = []
    for label, key, std, min_avg in _BATTER_MARKETS:
        avg = float(player.get(key, 0) or 0)
        if avg < min_avg:
            continue
        line_key = key[:-3] + "_line"  # "hits_pg" → "hits_line", "hr_pg" → "hr_line"
        raw_line = player.get(line_key)
        line     = float(raw_line) if raw_line else max(0.5, round(avg * 2) / 2 - 0.5)

        if label in _POISSON_STATS and line <= 0.5:
            over_p  = _poisson_over(avg, line)
            under_p = 1.0 - over_p
        else:
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


def _normalize_mlb_stat(stat_raw: str) -> Tuple[str, float]:
    """
    Map a raw PrizePicks stat_type string to (display_label, std_factor).
    Uses longest-match-first logic to avoid substring collisions
    (e.g. "earned runs" must not match "runs" first).
    """
    s = stat_raw.lower().strip()
    # Longer / more specific patterns first
    if "earned run" in s:           return "Earned Runs",   0.60
    if "home run" in s or s == "hr": return "Home Runs",    0.90
    if "total base" in s:           return "Total Bases",   0.50
    if "strikeout" in s or s == "k" or s == "so": return "Strikeouts", 0.35
    if "inning" in s:               return "Innings Pitched", 0.20
    if "rbi" in s or "run batted" in s: return "RBI",       0.65
    if "run" in s:                  return "Runs",          0.60
    if "hit" in s:                  return "Hits",          0.55
    if "walk" in s or s == "bb":    return "Walks",         0.60
    if "save" in s:                 return "Saves",         0.50
    return stat_raw, 0.50  # unknown — keep original label


def build_props_from_prizepicks(pp_projections: List[Dict]) -> List[Dict]:
    """
    Build prop cards directly from PrizePicks projections for MLB.
    The PrizePicks line IS the market line.
    Returns flat list — one entry per (player × stat).
    """
    results: List[Dict] = []
    for proj in pp_projections:
        stat_raw = proj.get("stat", "")
        line     = float(proj.get("line", 0) or 0)
        if line <= 0:
            continue

        stat_label, std_factor = _normalize_mlb_stat(stat_raw)

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
