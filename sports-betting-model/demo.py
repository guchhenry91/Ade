#!/usr/bin/env python3
"""
Offline demonstration – shows every market type with hardcoded data.
Useful to verify the model works without live API access.

Run:  python demo.py
"""
from __future__ import annotations
from models.football_model import FootballModel
from models.nba_model      import NBAModel
from models.nfl_model      import NFLModel
from markets.report        import print_signals_table, save_markdown_report
from utils.odds            import BetSignal
from utils.stats           import nfl_td_prob, shot_attempt_over_under, confidence_label


def demo_soccer() -> list[BetSignal]:
    """Demonstrate all soccer markets for sample fixtures."""
    sigs = []

    fixtures = [
        # (home, away, league, market_odds)
        ("Manchester City",  "Arsenal",    "EPL",
         {"winner": {"home": 1.75, "draw": 3.90, "away": 4.50},
          "ou25":   {"over": 1.90, "under": 1.95}, "btts": 1.72}),
        ("Real Madrid",      "Barcelona",  "LIGA",
         {"winner": {"home": 1.95, "draw": 3.60, "away": 3.80},
          "ou25":   {"over": 1.85, "under": 2.00}}),
        ("PSG",              "Lyon",       "L1",
         {"winner": {"home": 1.60, "draw": 4.00, "away": 5.50}}),
        ("Man City",         "Real Madrid","UCL",
         {"winner": {"home": 2.10, "draw": 3.50, "away": 3.20},
          "ou25":   {"over": 1.80, "under": 2.05}}),
    ]

    for home, away, league, mo in fixtures:
        model = FootballModel(league)
        sigs += model.analyze_fixture(home, away, market_odds=mo)

    return sigs


def demo_nba() -> list[BetSignal]:
    """Demonstrate NBA markets with hardcoded team/player data."""
    model = NBAModel()
    sigs  = []

    # Game winner + total points
    sigs += model.game_winner_signals(
        "Boston Celtics", "Golden State Warriors",
        market_odds={"home": 1.65, "away": 2.30},
    )
    sigs += model.total_points_signals(
        "Boston Celtics", "Golden State Warriors",
        line=224.5,
        home_ortg=119.5, away_ortg=117.0,
        home_pace=99.1,  away_pace=101.3,
        market_odds={"over": 1.91, "under": 1.91},
    )

    # Player props (offline – season averages supplied)
    players = [
        ("Jayson Tatum",     {"pts": 26.9, "reb": 8.1, "ast": 4.9, "3pm": 3.0},
         {"pts": 26.5, "reb": 7.5, "ast": 4.5}),
        ("Stephen Curry",    {"pts": 26.4, "reb": 4.5, "ast": 5.1, "3pm": 4.6},
         {"pts": 25.5, "reb": 4.5, "ast": 5.5}),
        ("LeBron James",     {"pts": 25.7, "reb": 7.3, "ast": 8.3, "3pm": 2.1},
         {"pts": 24.5, "reb": 7.5, "ast": 8.5}),
        ("Nikola Jokic",     {"pts": 29.5, "reb": 12.9, "ast": 9.6, "3pm": 0.9},
         {"pts": 29.5, "reb": 12.5, "ast": 9.5}),
    ]

    for name, avgs, lines in players:
        sigs += model.player_props_by_name(name, lines, known_avgs=avgs)

    # Season avg O/U (offline approximation)
    sigs += model.season_avg_ou_signals(
        player_ids=[],   # no lookup needed when we pass avgs manually
        player_names=[],
        stat="pts",
        line=26.5,
    )

    return sigs


def _make_nfl_player_signal(name: str, position: str,
                             season_tds: float,
                             rush_att: float, rec_tgt: float,
                             games: int,
                             market_odds: float | None = None) -> BetSignal:
    """Build an anytime TD BetSignal from season stats."""
    from utils.stats import nfl_td_prob, edge_pct, kelly_fraction
    rz_carries = rush_att / games * 0.15
    rz_targets = rec_tgt  / games * 0.12
    prob = nfl_td_prob(rz_targets, rz_carries, games=1)
    ep   = edge_pct(prob, market_odds) if market_odds else None
    kf   = kelly_fraction(prob, market_odds) if market_odds else None
    return BetSignal(
        sport       = "American Football",
        league      = "NFL",
        market      = "ANYTIME_TD_SCORER",
        selection   = f"{name} – Anytime TD",
        model_prob  = prob,
        market_odds = market_odds,
        edge_pct    = ep,
        kelly_frac  = kf,
        confidence  = confidence_label(prob),
        notes       = (f"Season TDs: {season_tds:.0f}  "
                       f"Rush att/gm: {rush_att/games:.1f}  "
                       f"Tgt/gm: {rec_tgt/games:.1f}"),
    )


def demo_nfl() -> list[BetSignal]:
    """Demonstrate NFL markets with hardcoded player/game data."""
    model = NFLModel()
    sigs  = []

    # Game winner + spread + total
    sigs += model.game_winner_signals(
        "Kansas City Chiefs", "Philadelphia Eagles",
        spread=-2.5,
        market_odds={"home": 1.87, "away": 2.00},
    )
    sigs += model.spread_signals(
        "Kansas City Chiefs", "Philadelphia Eagles",
        spread=-2.5,
        market_odds={"home": 1.91, "away": 1.91},
    )
    sigs += model.total_points_signals(
        "Kansas City Chiefs", "Philadelphia Eagles",
        line=47.5,
        home_stats={"pointsPerGame": 26.5},
        away_stats={"pointsPerGame": 25.0},
        market_odds={"over": 1.91, "under": 1.91},
    )

    # Anytime TD scorers (hardcoded 2024 stats)
    td_players = [
        # name,          pos,  season_tds, rush_att, rec_tgt, games
        ("Patrick Mahomes", "QB",  4,   0,   0,    17, 1.95),
        ("Travis Kelce",    "TE",  7,   0,  115,   16, 2.20),
        ("Isiah Pacheco",   "RB",  9,  189,  40,   14, 2.10),
        ("AJ Brown",        "WR",  7,   0,  119,   15, 2.25),
        ("DeVonta Smith",   "WR",  8,   0,  110,   16, 2.40),
        ("Saquon Barkley",  "RB", 13,  345,  63,   16, 1.90),
    ]
    for name, pos, tds, rush_att, rec_tgt, games, odds in td_players:
        sigs.append(_make_nfl_player_signal(
            name, pos, tds, rush_att, rec_tgt, games, market_odds=odds))

    # Rushing yards O/U (normal approx)
    rush_players = [
        ("Saquon Barkley",  345, 16, 95.5),
        ("Isiah Pacheco",   189, 14, 62.5),
        ("Derrick Henry",   1921, 16, 115.5),
    ]
    for name, total_rush, games, line in rush_players:
        avg = total_rush / games
        over_p, under_p = shot_attempt_over_under(avg, line, std_factor=0.38)
        for label, prob, ok in [
            (f"Over {line} rush yds",  over_p,  "over"),
            (f"Under {line} rush yds", under_p, "under"),
        ]:
            sigs.append(BetSignal(
                sport      = "American Football",
                league     = "NFL",
                market     = "PLAYER_RUSHING_YARDS_OU",
                selection  = f"{name} – {label}",
                model_prob = prob,
                confidence = confidence_label(prob),
                notes      = f"Season avg: {avg:.1f} rush yds/game",
            ))

    return sigs


# ── Run all demos ──────────────────────────────────────────────────────────

def main():
    all_signals: list[BetSignal] = []

    print("\n" + "=" * 60)
    print(" SOCCER DEMO  (EPL · UCL · La Liga · Ligue 1)")
    print("=" * 60)
    soccer_sigs = demo_soccer()
    print_signals_table(soccer_sigs, title="Soccer – All Markets")
    all_signals += soccer_sigs

    print("\n" + "=" * 60)
    print(" NBA DEMO")
    print("=" * 60)
    nba_sigs = demo_nba()
    print_signals_table(nba_sigs, title="NBA – All Markets")
    all_signals += nba_sigs

    print("\n" + "=" * 60)
    print(" NFL DEMO")
    print("=" * 60)
    nfl_sigs = demo_nfl()
    print_signals_table(nfl_sigs, title="NFL – All Markets")
    all_signals += nfl_sigs

    # Save combined report
    out = save_markdown_report(all_signals, outdir="reports")
    print(f"\n[+] Full demo report saved → {out}")


if __name__ == "__main__":
    main()
