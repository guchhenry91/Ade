"""
NBA betting model.

Markets generated:
  • GAME_WINNER          – moneyline (home/away)
  • TOTAL_POINTS         – over/under team total or game total
  • PLAYER_POINTS_OU     – player points over/under (season avg)
  • PLAYER_REBOUNDS_OU   – player rebounds over/under
  • PLAYER_ASSISTS_OU    – player assists over/under
  • PLAYER_3PM_OU        – three-pointers made over/under
"""
from __future__ import annotations
import logging
import math
from typing import Dict, List, Optional

from data.nba_data import (
    get_games, get_team_net_rating, get_season_averages, search_players,
    get_player_game_log,
)
from utils.stats import (
    nba_win_prob, shot_attempt_over_under, edge_pct, kelly_fraction,
    confidence_label,
)
from utils.odds import BetSignal

logger = logging.getLogger(__name__)

# Default season averages used when live data is unavailable
NBA_LEAGUE_AVG = {
    "ppg":    25.0,  # points per game (top player)
    "rpg":     5.0,
    "apg":     5.0,
    "3pm":     2.5,
    "pace":  100.0,  # possessions per game
}

TEAM_PACE_DEFAULTS: Dict[str, float] = {}   # filled at runtime


class NBAModel:
    def __init__(self, season: int = 2024):
        self.season = season
        self._games_cache: List[Dict] = []

    # ── Game winner ──────────────────────────────────────────────────────────

    def game_winner_signals(self,
                            home_team: str,
                            away_team: str,
                            home_id:   Optional[str] = None,
                            away_id:   Optional[str] = None,
                            market_odds: Optional[Dict[str, float]] = None
                            ) -> List[BetSignal]:
        """Generate moneyline signals for an NBA game."""
        home_nr = get_team_net_rating(home_id) if home_id else 0.0
        away_nr = get_team_net_rating(away_id) if away_id else 0.0

        home_prob = nba_win_prob(home_nr, away_nr, home_advantage=3.5)
        away_prob = 1 - home_prob

        mo = market_odds or {}
        signals = []
        for label, prob, ok, team in [
            (f"{home_team} (Home)", home_prob, "home", home_team),
            (f"{away_team} (Away)", away_prob, "away", away_team),
        ]:
            odds = mo.get(ok)
            ep   = edge_pct(prob, odds) if odds else None
            kf   = kelly_fraction(prob, odds) if odds else None
            signals.append(BetSignal(
                sport       = "Basketball",
                league      = "NBA",
                market      = "GAME_WINNER",
                selection   = label,
                model_prob  = prob,
                market_odds = odds,
                edge_pct    = ep,
                kelly_frac  = kf,
                confidence  = confidence_label(prob),
                notes       = (f"Net ratings – Home: {home_nr:+.1f}  "
                               f"Away: {away_nr:+.1f}"),
            ))
        return signals

    # ── Total points ─────────────────────────────────────────────────────────

    def total_points_signals(self,
                             home_team:  str,
                             away_team:  str,
                             line:       float,
                             home_pace:  float = 100.0,
                             away_pace:  float = 100.0,
                             home_ortg:  float = 112.0,
                             away_ortg:  float = 112.0,
                             market_odds: Optional[Dict[str, float]] = None
                             ) -> List[BetSignal]:
        """
        Estimate expected total points using pace × offensive rating
        and compute O/U probabilities.
        """
        avg_pace   = (home_pace + away_pace) / 2
        exp_pts_h  = home_ortg * avg_pace / 100
        exp_pts_a  = away_ortg * avg_pace / 100
        exp_total  = exp_pts_h + exp_pts_a

        over_p, under_p = shot_attempt_over_under(exp_total, line,
                                                   std_factor=0.08)
        mo = market_odds or {}
        signals = []
        for label, prob, ok in [
            (f"Over {line} pts",  over_p,  "over"),
            (f"Under {line} pts", under_p, "under"),
        ]:
            odds = mo.get(ok)
            ep   = edge_pct(prob, odds) if odds else None
            kf   = kelly_fraction(prob, odds) if odds else None
            signals.append(BetSignal(
                sport       = "Basketball",
                league      = "NBA",
                market      = "TOTAL_POINTS",
                selection   = f"{home_team} vs {away_team} – {label}",
                model_prob  = prob,
                market_odds = odds,
                edge_pct    = ep,
                kelly_frac  = kf,
                confidence  = confidence_label(prob),
                notes       = f"Projected total: {exp_total:.1f} pts  Line: {line}",
            ))
        return signals

    # ── Player props (season average O/U) ───────────────────────────────────

    def _player_prop_signal(self, player_name: str, stat_name: str,
                             avg: float, line: float,
                             market_odds: Optional[Dict[str, float]] = None
                             ) -> List[BetSignal]:
        over_p, under_p = shot_attempt_over_under(avg, line, std_factor=0.28)
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
                sport       = "Basketball",
                league      = "NBA",
                market      = f"PLAYER_{stat_name.upper()}_OU",
                selection   = f"{player_name} – {stat_name} {label}",
                model_prob  = prob,
                market_odds = odds,
                edge_pct    = ep,
                kelly_frac  = kf,
                confidence  = confidence_label(prob),
                notes       = f"Season avg: {avg:.1f}  Line: {line}",
            ))
        return signals

    def player_props_by_id(self,
                           player_id: int,
                           player_name: str,
                           prop_lines: Dict[str, float],
                           market_odds: Optional[Dict[str, Dict]] = None
                           ) -> List[BetSignal]:
        """
        Generate O/U props for a player given a dict of lines:
          prop_lines = {"pts": 22.5, "reb": 7.5, "ast": 4.5, "3pm": 2.5}
        market_odds = {"pts": {"over": 1.90, "under": 1.90}, ...}
        """
        avgs_data = get_season_averages([player_id], self.season)
        avgs = avgs_data[0] if avgs_data else {}

        stat_map = {
            "pts": float(avgs.get("pts", NBA_LEAGUE_AVG["ppg"])),
            "reb": float(avgs.get("reb", NBA_LEAGUE_AVG["rpg"])),
            "ast": float(avgs.get("ast", NBA_LEAGUE_AVG["apg"])),
            "3pm": float(avgs.get("fg3m", NBA_LEAGUE_AVG["3pm"])),
        }

        signals = []
        mo = market_odds or {}
        for stat, line in prop_lines.items():
            avg = stat_map.get(stat)
            if avg is None:
                continue
            signals += self._player_prop_signal(
                player_name, stat, avg, line, mo.get(stat))
        return signals

    def player_props_by_name(self,
                             player_name: str,
                             prop_lines:  Dict[str, float],
                             market_odds: Optional[Dict[str, Dict]] = None,
                             known_avgs:  Optional[Dict[str, float]] = None,
                             ) -> List[BetSignal]:
        """
        Same as player_props_by_id but resolves name → id first.
        Pass known_avgs={"pts": 27.1, "reb": 7.4, "ast": 8.3} to skip
        network lookup (useful when API is unavailable).
        """
        if known_avgs:
            # Build signals directly from supplied averages
            signals = []
            mo = market_odds or {}
            stat_map = {
                "pts": float(known_avgs.get("pts", NBA_LEAGUE_AVG["ppg"])),
                "reb": float(known_avgs.get("reb", NBA_LEAGUE_AVG["rpg"])),
                "ast": float(known_avgs.get("ast", NBA_LEAGUE_AVG["apg"])),
                "3pm": float(known_avgs.get("3pm", NBA_LEAGUE_AVG["3pm"])),
            }
            for stat, line in prop_lines.items():
                avg = stat_map.get(stat)
                if avg is None:
                    continue
                signals += self._player_prop_signal(
                    player_name, stat, avg, line, mo.get(stat))
            return signals

        results = search_players(player_name)
        if not results:
            logger.warning("Player not found: %s", player_name)
            return []
        player_id = results[0]["id"]
        return self.player_props_by_id(player_id, player_name,
                                       prop_lines, market_odds)

    # ── Season average O/U (long-term) ──────────────────────────────────────

    def season_avg_ou_signals(self,
                              player_ids:   List[int],
                              player_names: List[str],
                              stat:         str,
                              line:         float,
                              market_odds:  Optional[Dict[str, float]] = None
                              ) -> List[BetSignal]:
        """
        Over/under on a player finishing the season above/below a stat line.
        Uses season-to-date average + regression toward mean.
        """
        avgs_data = get_season_averages(player_ids, self.season)
        stat_key  = {"pts": "pts", "reb": "reb", "ast": "ast", "3pm": "fg3m"
                     }.get(stat, stat)

        signals = []
        mo = market_odds or {}
        for i, player_id in enumerate(player_ids):
            name = player_names[i] if i < len(player_names) else str(player_id)
            entry = next((a for a in avgs_data
                          if a.get("player_id") == player_id), None)
            if not entry:
                continue
            current_avg = float(entry.get(stat_key, 0))
            games_played = float(entry.get("games_played", 40))
            # Bayesian shrinkage toward league mean
            prior = NBA_LEAGUE_AVG.get(stat, current_avg)
            weight = min(games_played / 82, 1.0)
            projected = weight * current_avg + (1 - weight) * prior

            over_p, under_p = shot_attempt_over_under(projected, line,
                                                       std_factor=0.15)
            for label, prob, ok in [
                (f"Season avg Over {line} {stat}",  over_p,  "over"),
                (f"Season avg Under {line} {stat}", under_p, "under"),
            ]:
                odds = mo.get(ok)
                ep   = edge_pct(prob, odds) if odds else None
                signals.append(BetSignal(
                    sport       = "Basketball",
                    league      = "NBA",
                    market      = f"SEASON_AVG_{stat.upper()}_OU",
                    selection   = f"{name} – {label}",
                    model_prob  = prob,
                    market_odds = odds,
                    edge_pct    = ep,
                    confidence  = confidence_label(prob),
                    notes       = (f"Current avg: {current_avg:.1f}  "
                                   f"Projected: {projected:.1f}  "
                                   f"Games: {int(games_played)}"),
                ))
        return signals

    # ── Run today's games ────────────────────────────────────────────────────

    def run_today(self) -> List[BetSignal]:
        games = get_games()
        signals: List[BetSignal] = []
        for g in games:
            sigs = self.game_winner_signals(
                g["home_team"], g["away_team"],
                g.get("home_id"), g.get("away_id"),
            )
            # total points if O/U line is available
            if g.get("over_under"):
                try:
                    line = float(g["over_under"])
                    sigs += self.total_points_signals(
                        g["home_team"], g["away_team"], line=line)
                except (TypeError, ValueError):
                    pass
            for s in sigs:
                s.extra["game_date"] = g.get("date", "")
            signals += sigs
        return signals
