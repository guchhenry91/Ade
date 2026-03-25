"""
NFL betting model.

Markets generated:
  • GAME_WINNER            – moneyline
  • SPREAD                 – against the spread
  • TOTAL_POINTS           – game total over/under
  • ANYTIME_TD_SCORER      – player anytime touchdown scorer
  • PLAYER_RUSHING_YARDS_OU
  • PLAYER_RECEIVING_YARDS_OU
  • PLAYER_PASSING_YARDS_OU
"""
from __future__ import annotations
import logging
import math
from typing import Dict, List, Optional

from data.nfl_data import (
    get_games, get_team_stats, get_player_season_stats,
    estimate_rz_usage,
)
from utils.stats import (
    nfl_td_prob, shot_attempt_over_under, edge_pct, kelly_fraction,
    confidence_label,
)
from utils.odds import BetSignal

logger = logging.getLogger(__name__)

# Approximate league averages (2024)
NFL_LEAGUE_AVG = {
    "ppg":         23.0,
    "rypg":        85.0,   # rushing yards / game (skill player)
    "recypg":     65.0,    # receiving yards / game
    "passypg":   265.0,    # passing yards / game
}

# Logistic model parameters for spread → win prob
SPREAD_SIGMA = 13.45   # points; fitted to ~10 seasons


def spread_to_win_prob(spread: float) -> float:
    """
    Convert point spread (positive = favourite) to win probability.
    Uses normal CDF over spread with historical sigma.
    """
    from scipy.stats import norm
    return float(norm.cdf(spread / SPREAD_SIGMA))


class NFLModel:
    def __init__(self, season: int = 2024):
        self.season = season

    # ── Game winner / spread ─────────────────────────────────────────────────

    def game_winner_signals(self,
                            home_team: str,
                            away_team: str,
                            spread:    Optional[float] = None,
                            market_odds: Optional[Dict[str, float]] = None
                            ) -> List[BetSignal]:
        """
        Moneyline signals.
        spread: home team point spread (negative = home favoured).
        """
        if spread is not None:
            # spread is usually expressed as home – away differential
            home_prob = spread_to_win_prob(-spread + 2.5)   # +2.5 home field
        else:
            home_prob = 0.55   # mild home edge default
        away_prob = 1 - home_prob

        mo = market_odds or {}
        signals = []
        for label, prob, ok in [
            (f"{home_team} (Home)", home_prob, "home"),
            (f"{away_team} (Away)", away_prob, "away"),
        ]:
            odds = mo.get(ok)
            ep   = edge_pct(prob, odds) if odds else None
            kf   = kelly_fraction(prob, odds) if odds else None
            signals.append(BetSignal(
                sport       = "American Football",
                league      = "NFL",
                market      = "GAME_WINNER",
                selection   = label,
                model_prob  = prob,
                market_odds = odds,
                edge_pct    = ep,
                kelly_frac  = kf,
                confidence  = confidence_label(prob),
                notes       = f"Spread: {spread:+.1f}" if spread else "No spread data",
            ))
        return signals

    def spread_signals(self,
                       home_team: str,
                       away_team: str,
                       spread:    float,           # e.g. -3.5 means home -3.5
                       market_odds: Optional[Dict[str, float]] = None
                       ) -> List[BetSignal]:
        """Against-the-spread signals."""
        home_cover = spread_to_win_prob(-spread)
        away_cover = 1 - home_cover

        mo = market_odds or {}
        signals = []
        for label, prob, ok in [
            (f"{home_team} {spread:+.1f}", home_cover, "home"),
            (f"{away_team} +{-spread:.1f}", away_cover, "away"),
        ]:
            odds = mo.get(ok)
            ep   = edge_pct(prob, odds) if odds else None
            kf   = kelly_fraction(prob, odds) if odds else None
            signals.append(BetSignal(
                sport       = "American Football",
                league      = "NFL",
                market      = "SPREAD",
                selection   = label,
                model_prob  = prob,
                market_odds = odds,
                edge_pct    = ep,
                kelly_frac  = kf,
                confidence  = confidence_label(prob),
                notes       = f"Spread: {spread:+.1f}",
            ))
        return signals

    def total_points_signals(self,
                             home_team: str,
                             away_team: str,
                             line:      float,
                             home_stats: Optional[Dict] = None,
                             away_stats: Optional[Dict] = None,
                             market_odds: Optional[Dict[str, float]] = None
                             ) -> List[BetSignal]:
        """NFL total game points over/under."""
        # Use scoring averages if available
        h_pts = float((home_stats or {}).get("pointsPerGame", NFL_LEAGUE_AVG["ppg"]))
        a_pts = float((away_stats or {}).get("pointsPerGame", NFL_LEAGUE_AVG["ppg"]))
        exp_total = h_pts + a_pts

        over_p, under_p = shot_attempt_over_under(exp_total, line, std_factor=0.12)
        mo = market_odds or {}
        signals = []
        for label, prob, ok in [
            (f"Over {line}",  over_p,  "over"),
            (f"Under {line}", under_p, "under"),
        ]:
            odds = mo.get(ok)
            ep   = edge_pct(prob, odds) if odds else None
            kf   = kelly_fraction(prob, odds) if odds else None
            signals.append(BetSignal(
                sport       = "American Football",
                league      = "NFL",
                market      = "TOTAL_POINTS",
                selection   = f"{home_team} vs {away_team} – {label}",
                model_prob  = prob,
                market_odds = odds,
                edge_pct    = ep,
                kelly_frac  = kf,
                confidence  = confidence_label(prob),
                notes       = f"Projected: {exp_total:.1f}  Line: {line}",
            ))
        return signals

    # ── Anytime TD scorer ────────────────────────────────────────────────────

    def anytime_td_signals(self,
                           team_id:    str,
                           game_name:  str,
                           positions:  Optional[List[str]] = None,
                           market_odds: Optional[Dict[str, float]] = None
                           ) -> List[BetSignal]:
        """
        Generate anytime TD scorer probabilities for skill players on *team_id*.
        positions: filter to specific positions e.g. ["RB","WR","TE","QB"]
        """
        positions = positions or ["RB", "WR", "TE", "QB"]
        players: List[Dict] = []
        for pos in positions:
            players += get_player_season_stats(team_id, position=pos)

        if not players:
            logger.warning("No player data for team_id=%s", team_id)

        mo = market_odds or {}
        signals = []

        for p in players:
            rz = estimate_rz_usage(p)
            games = max(1, float(p.get("gamesPlayed", p.get("games", 10)) or 10))
            prob  = nfl_td_prob(
                red_zone_targets = rz["rz_targets"],
                carries          = rz["rz_carries"],
                games            = int(games),
            )
            name = p.get("name", "Unknown")
            odds = mo.get(name)
            ep   = edge_pct(prob, odds) if odds else None
            kf   = kelly_fraction(prob, odds) if odds else None

            total_td = rz["total_tds"]
            signals.append(BetSignal(
                sport       = "American Football",
                league      = "NFL",
                market      = "ANYTIME_TD_SCORER",
                selection   = f"{name} – Anytime TD",
                model_prob  = prob,
                market_odds = odds,
                edge_pct    = ep,
                kelly_frac  = kf,
                confidence  = confidence_label(prob),
                notes       = (f"Season TDs: {total_td:.0f}  "
                               f"Est RZ carries: {rz['rz_carries']:.1f}  "
                               f"RZ targets: {rz['rz_targets']:.1f}"),
            ))

        # Sort by probability descending
        signals.sort(key=lambda s: s.model_prob, reverse=True)
        return signals

    # ── Player yardage props ─────────────────────────────────────────────────

    def player_yards_signals(self,
                             team_id:    str,
                             position:   str,          # "RB", "WR", "QB"
                             stat_key:   str,          # "rushingYards" etc.
                             market_key: str,          # "RUSHING_YARDS" etc.
                             game_line_map: Optional[Dict[str, float]] = None,
                             market_odds:   Optional[Dict[str, Dict]] = None
                             ) -> List[BetSignal]:
        """Generic player yardage O/U signals."""
        players = get_player_season_stats(team_id, position=position)
        gl  = game_line_map or {}
        mo  = market_odds   or {}
        signals = []

        for p in players:
            name   = p.get("name", "Unknown")
            yards  = float(p.get(stat_key, 0) or 0)
            games  = max(1, float(p.get("gamesPlayed", 10) or 10))
            avg    = yards / games

            line   = gl.get(name, round(avg * 0.9, 1))
            o, u   = shot_attempt_over_under(avg, line, std_factor=0.35)
            odds   = mo.get(name, {})

            for label, prob, ok in [
                (f"Over {line}",  o, "over"),
                (f"Under {line}", u, "under"),
            ]:
                ods = odds.get(ok)
                ep  = edge_pct(prob, ods) if ods else None
                signals.append(BetSignal(
                    sport       = "American Football",
                    league      = "NFL",
                    market      = f"PLAYER_{market_key}_OU",
                    selection   = f"{name} – {label} yds",
                    model_prob  = prob,
                    market_odds = ods,
                    edge_pct    = ep,
                    confidence  = confidence_label(prob),
                    notes       = f"Season avg: {avg:.1f} yds/game",
                ))

        return signals

    # ── Run today ────────────────────────────────────────────────────────────

    def run_today(self) -> List[BetSignal]:
        games = get_games(season=self.season)
        signals: List[BetSignal] = []
        for g in games:
            spread = None
            try:
                spread = float(g.get("spread", 0) or 0)
            except (TypeError, ValueError):
                pass

            signals += self.game_winner_signals(
                g["home_team"], g["away_team"], spread=spread)

            if g.get("over_under"):
                try:
                    signals += self.total_points_signals(
                        g["home_team"], g["away_team"],
                        line=float(g["over_under"]))
                except (TypeError, ValueError):
                    pass

            if g.get("home_id"):
                signals += self.anytime_td_signals(
                    g["home_id"], g["name"])
            if g.get("away_id"):
                signals += self.anytime_td_signals(
                    g["away_id"], g["name"])

            for s in signals:
                if not s.extra.get("game_date"):
                    s.extra["game_date"] = g.get("date", "")

        return signals
