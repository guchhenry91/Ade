"""Odds formatting and conversion utilities."""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BetSignal:
    sport:        str
    league:       str
    market:       str          # e.g. "ANYTIME_SCORER", "SHOT_ATTEMPTS_OVER"
    selection:    str          # e.g. "Man City ML", "Mbappe Over 3.5 shots"
    model_prob:   float        # 0-1
    market_odds:  Optional[float] = None   # decimal odds from book
    edge_pct:     Optional[float] = None
    kelly_frac:   Optional[float] = None
    confidence:   str = "LOW"
    notes:        str = ""
    extra:        dict = field(default_factory=dict)

    # ── derived props ────────────────────────────────────────────────────────
    @property
    def model_prob_pct(self) -> str:
        return f"{self.model_prob * 100:.1f}%"

    @property
    def american_odds(self) -> str:
        from utils.stats import prob_to_american
        return str(prob_to_american(self.model_prob))

    def to_dict(self) -> dict:
        return {
            "sport":        self.sport,
            "league":       self.league,
            "market":       self.market,
            "selection":    self.selection,
            "model_prob":   f"{self.model_prob*100:.1f}%",
            "market_odds":  self.market_odds,
            "edge_pct":     f"{self.edge_pct:.1f}%" if self.edge_pct is not None else "N/A",
            "kelly_frac":   f"{self.kelly_frac*100:.2f}%" if self.kelly_frac is not None else "N/A",
            "confidence":   self.confidence,
            "notes":        self.notes,
        }


def american_to_decimal(american: int) -> float:
    if american > 0:
        return american / 100 + 1
    return 100 / abs(american) + 1


def decimal_to_american(decimal: float) -> int:
    if decimal >= 2.0:
        return round((decimal - 1) * 100)
    return round(-100 / (decimal - 1))


def remove_vig(home_odds: float, away_odds: float,
               draw_odds: Optional[float] = None):
    """Return fair no-vig probabilities."""
    probs = [1 / home_odds, 1 / away_odds]
    if draw_odds:
        probs.append(1 / draw_odds)
    total = sum(probs)
    return [p / total for p in probs]
