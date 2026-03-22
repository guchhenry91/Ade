"""
Football (soccer) betting model.
Covers: EPL, Champions League, La Liga, Ligue 1.

Markets generated:
  • MATCH_WINNER        – 1X2
  • OVER_UNDER_GOALS    – total goals O/U line
  • BOTH_TEAMS_SCORE    – GG / BTTS
  • XG_COMPARISON       – value vs market total-goals line
  • PLAYER_SHOTS        – player shots-on-target O/U
  • PLAYER_ANYTIME_SCORER – anytime goal scorer probability
"""
from __future__ import annotations
import logging
from typing import Dict, List, Optional, Tuple

from data.football_data import (
    get_fixtures, get_xg_data, get_player_stats,
    compute_team_xg_averages,
)
from utils.stats import (
    poisson_match_probs, poisson_over_under, scorer_probability,
    shot_attempt_over_under, edge_pct, kelly_fraction, confidence_label,
)
from utils.odds import BetSignal

logger = logging.getLogger(__name__)

# League-level average xG baselines (2024 season priors)
LEAGUE_BASELINES: Dict[str, Dict[str, float]] = {
    "EPL":  {"home_xg": 1.52, "away_xg": 1.17, "avg_goals": 2.69},
    "UCL":  {"home_xg": 1.70, "away_xg": 1.35, "avg_goals": 3.05},
    "LIGA": {"home_xg": 1.48, "away_xg": 1.14, "avg_goals": 2.62},
    "L1":   {"home_xg": 1.44, "away_xg": 1.12, "avg_goals": 2.56},
}

# Typical player-level shot-on-target averages by position (per game)
PLAYER_SOT_BASELINES = {
    "forward":   2.1,
    "midfielder": 1.1,
    "defender":  0.4,
}


class FootballModel:
    """
    Generates BetSignal objects for all soccer markets for a given league.
    """

    def __init__(self, league_key: str, season: int = 2024):
        self.league_key  = league_key.upper()
        self.season      = season
        self.baseline    = LEAGUE_BASELINES.get(self.league_key,
                                                LEAGUE_BASELINES["EPL"])
        self._xg_cache:  List[Dict] = []
        self._player_cache: List[Dict] = []

    # ── Internals ────────────────────────────────────────────────────────────

    def _load_xg(self) -> List[Dict]:
        if not self._xg_cache:
            self._xg_cache = get_xg_data(self.league_key, self.season)
        return self._xg_cache

    def _load_players(self) -> List[Dict]:
        if not self._player_cache:
            self._player_cache = get_player_stats(self.league_key, self.season)
        return self._player_cache

    def _team_xg(self, team_name: str) -> Tuple[float, float]:
        """Return (avg_xg_scored, avg_xg_conceded) for a team."""
        xg_data = self._load_xg()
        if xg_data:
            avgs = compute_team_xg_averages(xg_data, team_name)
            return avgs["avg_xg_scored"], avgs["avg_xg_conceded"]
        return self.baseline["home_xg"], self.baseline["away_xg"]

    def _match_xg(self, home_team: str, away_team: str
                  ) -> Tuple[float, float]:
        """
        Estimate expected goals for both teams using Dixon-Coles attack/defence
        strength approximation.
        """
        h_att, h_def = self._team_xg(home_team)
        a_att, a_def = self._team_xg(away_team)
        league_avg   = self.baseline["avg_goals"] / 2

        # Attack × opponent defence / league average
        home_xg = (h_att * a_def) / league_avg * self.baseline["home_xg"]
        away_xg = (a_att * h_def) / league_avg * self.baseline["away_xg"]

        home_xg = max(0.3, min(5.0, home_xg))
        away_xg = max(0.3, min(5.0, away_xg))
        return home_xg, away_xg

    # ── Public market generators ─────────────────────────────────────────────

    def match_winner_signals(self,
                             home_team: str,
                             away_team: str,
                             market_odds: Optional[Dict[str, float]] = None
                             ) -> List[BetSignal]:
        """
        Generate 1X2 signals.
        market_odds: {"home": 2.10, "draw": 3.40, "away": 3.60}
        """
        home_xg, away_xg = self._match_xg(home_team, away_team)
        h_prob, d_prob, a_prob = poisson_match_probs(home_xg, away_xg)

        signals = []
        mo = market_odds or {}

        for label, prob, odds_key, sel in [
            ("Home Win", h_prob, "home",  f"{home_team} (Home)"),
            ("Draw",     d_prob, "draw",  "Draw"),
            ("Away Win", a_prob, "away",  f"{away_team} (Away)"),
        ]:
            odds = mo.get(odds_key)
            ep   = edge_pct(prob, odds) if odds else None
            kf   = kelly_fraction(prob, odds) if odds else None
            signals.append(BetSignal(
                sport       = "Soccer",
                league      = self.league_key,
                market      = "MATCH_WINNER",
                selection   = sel,
                model_prob  = prob,
                market_odds = odds,
                edge_pct    = ep,
                kelly_frac  = kf,
                confidence  = confidence_label(prob),
                notes       = f"xG: {home_xg:.2f}–{away_xg:.2f}",
            ))
        return signals

    def over_under_signals(self,
                           home_team: str,
                           away_team: str,
                           line: float = 2.5,
                           market_odds: Optional[Dict[str, float]] = None
                           ) -> List[BetSignal]:
        """Over/under total goals signals."""
        home_xg, away_xg = self._match_xg(home_team, away_team)
        total_xg = home_xg + away_xg
        over_p, under_p = poisson_over_under(total_xg, line)

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
                sport       = "Soccer",
                league      = self.league_key,
                market      = "OVER_UNDER_GOALS",
                selection   = f"{home_team} vs {away_team} – {label}",
                model_prob  = prob,
                market_odds = odds,
                edge_pct    = ep,
                kelly_frac  = kf,
                confidence  = confidence_label(prob),
                notes       = f"Total xG: {total_xg:.2f}  Line: {line}",
            ))
        return signals

    def btts_signal(self,
                    home_team: str,
                    away_team: str,
                    market_odds: Optional[float] = None
                    ) -> BetSignal:
        """Both Teams to Score signal."""
        from scipy.stats import poisson
        home_xg, away_xg = self._match_xg(home_team, away_team)
        p_home_scores = 1 - poisson.pmf(0, home_xg)
        p_away_scores = 1 - poisson.pmf(0, away_xg)
        btts_prob = p_home_scores * p_away_scores

        ep = edge_pct(btts_prob, market_odds) if market_odds else None
        kf = kelly_fraction(btts_prob, market_odds) if market_odds else None
        return BetSignal(
            sport       = "Soccer",
            league      = self.league_key,
            market      = "BTTS",
            selection   = f"{home_team} vs {away_team} – Both Teams Score",
            model_prob  = btts_prob,
            market_odds = market_odds,
            edge_pct    = ep,
            kelly_frac  = kf,
            confidence  = confidence_label(btts_prob),
            notes       = f"xG: {home_xg:.2f}–{away_xg:.2f}",
        )

    def player_anytime_scorer_signals(self,
                                      match_name: str,
                                      team_name: str,
                                      team_xg: float,
                                      player_shots_per_game: Optional[float] = None,
                                      player_xg_per_shot:    Optional[float] = None,
                                      market_odds_map: Optional[Dict[str, float]] = None
                                      ) -> List[BetSignal]:
        """
        Generate anytime scorer signals for players with known shot stats.
        Pulls from api-football player cache where available.
        """
        players = self._load_players()
        team_players = [p for p in players if p.get("team") == team_name]
        if not team_players:
            logger.debug("No player data for %s – using aggregate estimate", team_name)

        signals = []
        mo = market_odds_map or {}

        # If we have real player data
        for p in team_players[:10]:   # top-10 by shots
            shots = p.get("shots_total", 0) or 0
            apps  = p.get("appearances", 1) or 1
            goals = p.get("goals", 0) or 0

            shots_per_game = shots / apps
            xg_per_shot    = (p.get("xg") or (goals / shots if shots else 0.10))
            xg_per_shot    = float(xg_per_shot)

            prob = scorer_probability(xg_per_shot, shots_per_game)
            name = p.get("name", "Unknown")
            odds = mo.get(name)
            ep   = edge_pct(prob, odds) if odds else None
            kf   = kelly_fraction(prob, odds) if odds else None

            signals.append(BetSignal(
                sport       = "Soccer",
                league      = self.league_key,
                market      = "ANYTIME_SCORER",
                selection   = name,
                model_prob  = prob,
                market_odds = odds,
                edge_pct    = ep,
                kelly_frac  = kf,
                confidence  = confidence_label(prob),
                notes       = (f"Shots/game: {shots_per_game:.1f}  "
                               f"xG/shot: {xg_per_shot:.3f}"),
            ))

        # Fallback aggregate scorer for any team
        if not team_players and team_xg > 0:
            # Estimate a "lead striker" probability assuming 35% of team xG
            striker_xg = team_xg * 0.35
            prob = 1 - __import__("math").exp(-striker_xg)
            signals.append(BetSignal(
                sport      = "Soccer",
                league     = self.league_key,
                market     = "ANYTIME_SCORER",
                selection  = f"{team_name} – Lead Striker (estimate)",
                model_prob = prob,
                confidence = confidence_label(prob),
                notes      = f"Team xG {team_xg:.2f}, estimated striker share 35%",
            ))

        return signals

    def player_shots_signals(self,
                             match_name: str,
                             team_name:  str,
                             market_lines: Optional[Dict[str, float]] = None,
                             market_odds:  Optional[Dict[str, Dict]] = None,
                             ) -> List[BetSignal]:
        """
        Over/under shot-attempts and shots-on-target per player.
        market_lines: {player_name: line}  e.g. {"Erling Haaland": 3.5}
        market_odds : {player_name: {"over": 1.90, "under": 1.90}}
        """
        players = self._load_players()
        team_players = [p for p in players if p.get("team") == team_name]

        signals = []
        ml = market_lines or {}
        mo = market_odds  or {}

        for p in team_players[:10]:
            shots = p.get("shots_total", 0) or 0
            apps  = p.get("appearances", 1) or 1
            name  = p.get("name", "Unknown")
            shots_per_game = shots / apps

            line  = ml.get(name, round(shots_per_game))  # default line at average
            odds  = mo.get(name, {})

            over_p, under_p = shot_attempt_over_under(shots_per_game, line)

            for label, prob, ok in [
                (f"Over {line} shots",  over_p,  "over"),
                (f"Under {line} shots", under_p, "under"),
            ]:
                o     = odds.get(ok)
                ep    = edge_pct(prob, o) if o else None
                kf    = kelly_fraction(prob, o) if o else None
                signals.append(BetSignal(
                    sport       = "Soccer",
                    league      = self.league_key,
                    market      = "PLAYER_SHOTS",
                    selection   = f"{name} – {label}",
                    model_prob  = prob,
                    market_odds = o,
                    edge_pct    = ep,
                    kelly_frac  = kf,
                    confidence  = confidence_label(prob),
                    notes       = f"Season avg: {shots_per_game:.1f} shots/game",
                ))

        return signals

    # ── Run all markets for a fixture ────────────────────────────────────────

    def analyze_fixture(self,
                        home_team: str,
                        away_team: str,
                        market_odds: Optional[Dict] = None
                        ) -> List[BetSignal]:
        """
        Run all soccer markets for a single fixture.
        market_odds format:
          {
            "winner": {"home": 2.1, "draw": 3.4, "away": 3.6},
            "ou25":   {"over": 1.85, "under": 2.0},
            "btts":   1.75,
          }
        """
        mo = market_odds or {}
        home_xg, away_xg = self._match_xg(home_team, away_team)
        signals: List[BetSignal] = []

        signals += self.match_winner_signals(home_team, away_team,
                                             mo.get("winner"))
        signals += self.over_under_signals(home_team, away_team,
                                           line=2.5,
                                           market_odds=mo.get("ou25"))
        signals.append(self.btts_signal(home_team, away_team,
                                        market_odds=mo.get("btts")))
        signals += self.player_anytime_scorer_signals(
            f"{home_team} vs {away_team}", home_team, home_xg)
        signals += self.player_anytime_scorer_signals(
            f"{home_team} vs {away_team}", away_team, away_xg)
        signals += self.player_shots_signals(
            f"{home_team} vs {away_team}", home_team)
        signals += self.player_shots_signals(
            f"{home_team} vs {away_team}", away_team)

        return signals

    def run_today(self) -> List[BetSignal]:
        """Fetch today's fixtures and run the full model over all of them."""
        fixtures = get_fixtures(self.league_key)
        all_signals: List[BetSignal] = []
        for fix in fixtures:
            logger.info("Analyzing %s vs %s",
                        fix["home_team"], fix["away_team"])
            sigs = self.analyze_fixture(fix["home_team"], fix["away_team"])
            for s in sigs:
                s.extra["fixture_date"] = fix.get("date", "")
            all_signals += sigs
        return all_signals
