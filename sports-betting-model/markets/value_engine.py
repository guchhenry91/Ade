"""
Value engine – filters BetSignals by edge and ranks them.
"""
from __future__ import annotations
from typing import List, Optional

from utils.odds import BetSignal
from config import MIN_EDGE_PCT


def filter_value_bets(signals: List[BetSignal],
                      min_edge: float = MIN_EDGE_PCT,
                      min_prob: float = 0.0,
                      max_prob: float = 1.0
                      ) -> List[BetSignal]:
    """Return signals that have a positive edge ≥ min_edge %."""
    value = []
    for s in signals:
        if s.edge_pct is None:
            continue
        if s.edge_pct < min_edge:
            continue
        if not (min_prob <= s.model_prob <= max_prob):
            continue
        value.append(s)
    value.sort(key=lambda s: s.edge_pct, reverse=True)
    return value


def rank_signals(signals: List[BetSignal]) -> List[BetSignal]:
    """Sort signals by confidence tier then model probability."""
    tier_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    return sorted(signals,
                  key=lambda s: (tier_order.get(s.confidence, 3),
                                 -s.model_prob))


def summarise(signals: List[BetSignal]) -> dict:
    """Return a dict summary of signal counts by market / league."""
    from collections import Counter
    by_league = Counter(s.league   for s in signals)
    by_market = Counter(s.market   for s in signals)
    by_conf   = Counter(s.confidence for s in signals)
    return {
        "total":     len(signals),
        "by_league": dict(by_league),
        "by_market": dict(by_market),
        "by_conf":   dict(by_conf),
    }
