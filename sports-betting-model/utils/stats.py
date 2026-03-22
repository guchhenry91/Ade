"""Statistical utilities for the betting model."""
import math
from typing import Tuple
import numpy as np
from scipy.stats import poisson, norm


# ── Probability / odds helpers ────────────────────────────────────────────────

def implied_prob(decimal_odds: float) -> float:
    """Convert decimal odds to implied probability (0-1)."""
    if decimal_odds <= 1.0:
        return 1.0
    return 1.0 / decimal_odds


def prob_to_american(prob: float) -> int:
    """Convert win probability to American moneyline odds."""
    prob = max(0.001, min(0.999, prob))
    if prob >= 0.5:
        return round(-prob / (1 - prob) * 100)
    return round((1 - prob) / prob * 100)


def edge_pct(model_prob: float, market_odds: float) -> float:
    """Kelly-style edge percentage."""
    imp = implied_prob(market_odds)
    return (model_prob - imp) * 100


def kelly_fraction(model_prob: float, decimal_odds: float, fraction: float = 0.25) -> float:
    """Fractional Kelly bet size (default quarter-Kelly)."""
    b = decimal_odds - 1
    q = 1 - model_prob
    k = (b * model_prob - q) / b
    return max(0.0, k * fraction)


# ── Poisson / Dixon-Coles soccer xG ──────────────────────────────────────────

def poisson_match_probs(home_xg: float, away_xg: float, max_goals: int = 10
                        ) -> Tuple[float, float, float]:
    """
    Return (home_win, draw, away_win) using independent Poisson distributions.
    """
    h = np.clip(home_xg, 0.1, 8.0)
    a = np.clip(away_xg, 0.1, 8.0)
    home_win = draw = away_win = 0.0
    for i in range(max_goals + 1):
        for j in range(max_goals + 1):
            p = poisson.pmf(i, h) * poisson.pmf(j, a)
            if i > j:
                home_win += p
            elif i == j:
                draw += p
            else:
                away_win += p
    total = home_win + draw + away_win
    return home_win / total, draw / total, away_win / total


def poisson_over_under(lam: float, line: float) -> Tuple[float, float]:
    """Probability of over / under for a given Poisson rate and line."""
    line_floor = int(math.floor(line))
    under = poisson.cdf(line_floor, lam)
    over  = 1 - poisson.cdf(line_floor, lam)
    # push probability at exact integer line
    if line == line_floor:
        push = poisson.pmf(line_floor, lam)
        under -= push / 2
        over  -= push / 2
    return over, under


def scorer_probability(xg_per_shot: float, shots: float) -> float:
    """
    P(player scores ≥ 1 goal) using 1 - P(0 goals).
    Assumes each shot is an independent Bernoulli with p = xg_per_shot.
    """
    p_no_score = (1 - xg_per_shot) ** shots
    return max(0.0, min(1.0, 1 - p_no_score))


def shot_attempt_over_under(avg_shots: float, line: float, std_factor: float = 0.30
                            ) -> Tuple[float, float]:
    """
    Normal approximation for player shot attempts over/under.
    std_factor: coefficient of variation (default 30 %).
    """
    std = avg_shots * std_factor
    over  = float(1 - norm.cdf(line, loc=avg_shots, scale=std))
    under = float(norm.cdf(line, loc=avg_shots, scale=std))
    return over, under


# ── NBA / NFL helpers ─────────────────────────────────────────────────────────

def nba_win_prob(team_rating: float, opp_rating: float,
                 home_advantage: float = 3.5) -> float:
    """
    Point-spread based win probability using logistic transformation.
    team_rating / opp_rating: season net-rating (pts per 100 poss diff).
    """
    spread = (team_rating - opp_rating) + home_advantage
    # ~3 pts ≈ 10% win probability shift
    logit = spread / 10.0
    return float(1 / (1 + math.exp(-logit * math.log(10) / 4)))


def nfl_td_prob(red_zone_targets: float, carries: float,
                games: int = 1, game_fraction: float = 1.0) -> float:
    """
    Simplified anytime TD probability based on red-zone usage.
    """
    rz_per_game = (red_zone_targets + carries) / max(1, games)
    # league-average TD conversion in red zone ~40 %
    td_per_game = rz_per_game * 0.40 * game_fraction
    prob = 1 - math.exp(-td_per_game)
    return max(0.0, min(1.0, prob))


def confidence_label(prob: float) -> str:
    pct = prob * 100
    if pct >= 75:
        return "HIGH"
    if pct >= 55:
        return "MEDIUM"
    return "LOW"
