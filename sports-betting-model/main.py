#!/usr/bin/env python3
"""
Sports Betting Model — CLI entry point
=======================================
Usage examples
--------------
# Run all leagues, today's games
python main.py

# Soccer only
python main.py --sports soccer

# NFL only with value filter
python main.py --sports nfl --value-only

# Specific league
python main.py --league EPL

# Analyse a custom fixture (soccer)
python main.py --fixture "Man City" "Arsenal" --league EPL

# NBA player prop
python main.py --player-prop "LeBron James" --lines pts=25.5,reb=7.5,ast=8.5

# NFL anytime TD for a team
python main.py --td-scorer --team-id 1   # ESPN team id

# Save markdown report
python main.py --save-report
"""
from __future__ import annotations
import argparse
import logging
import sys
from typing import List

from config import LEAGUES
from models.football_model import FootballModel
from models.nba_model      import NBAModel
from models.nfl_model      import NFLModel
from markets.value_engine   import filter_value_bets, rank_signals
from markets.report         import print_signals_table, save_markdown_report
from utils.odds             import BetSignal

logging.basicConfig(
    level  = logging.INFO,
    format = "%(levelname)-8s %(name)s – %(message)s",
)
logger = logging.getLogger("main")


# ── Helpers ──────────────────────────────────────────────────────────────────

def parse_lines(lines_str: str) -> dict:
    """Parse 'pts=25.5,reb=7.5' into {'pts': 25.5, 'reb': 7.5}."""
    result = {}
    for part in lines_str.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            result[k.strip()] = float(v.strip())
    return result


# ── Soccer runner ────────────────────────────────────────────────────────────

def run_soccer(leagues: List[str]) -> List[BetSignal]:
    signals = []
    for lg in leagues:
        lg = lg.upper()
        if lg not in ("EPL", "UCL", "LIGA", "L1"):
            continue
        logger.info("Running soccer model for %s", lg)
        try:
            model = FootballModel(lg)
            sigs  = model.run_today()
            logger.info("  %d signals generated", len(sigs))
            signals += sigs
        except Exception as e:
            logger.error("Soccer model error (%s): %s", lg, e)
    return signals


def run_custom_fixture(home: str, away: str, league: str,
                       market_odds: dict = None) -> List[BetSignal]:
    model = FootballModel(league)
    return model.analyze_fixture(home, away, market_odds)


# ── NBA runner ───────────────────────────────────────────────────────────────

def run_nba() -> List[BetSignal]:
    logger.info("Running NBA model")
    try:
        model = NBAModel()
        sigs  = model.run_today()
        logger.info("  %d signals generated", len(sigs))
        return sigs
    except Exception as e:
        logger.error("NBA model error: %s", e)
        return []


def run_nba_player_prop(player_name: str, lines: dict) -> List[BetSignal]:
    model = NBAModel()
    return model.player_props_by_name(player_name, lines)


def run_nba_season_avg(player_ids: List[int], names: List[str],
                       stat: str, line: float) -> List[BetSignal]:
    model = NBAModel()
    return model.season_avg_ou_signals(player_ids, names, stat, line)


# ── NFL runner ───────────────────────────────────────────────────────────────

def run_nfl() -> List[BetSignal]:
    logger.info("Running NFL model")
    try:
        model = NFLModel()
        sigs  = model.run_today()
        logger.info("  %d signals generated", len(sigs))
        return sigs
    except Exception as e:
        logger.error("NFL model error: %s", e)
        return []


def run_nfl_td_scorer(team_id: str) -> List[BetSignal]:
    model = NFLModel()
    return model.anytime_td_signals(team_id, game_name="Custom")


# ── Main ─────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Multi-sport betting model: NBA | NFL | EPL | UCL | La Liga | Ligue 1",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--sports",    nargs="+",
                   default=["soccer", "nba", "nfl"],
                   help="Which sports to run: soccer nba nfl  (default: all)")
    p.add_argument("--league",    type=str, default=None,
                   help="Single soccer league: EPL UCL LIGA L1")
    p.add_argument("--fixture",   nargs=2, metavar=("HOME", "AWAY"),
                   help="Analyse a specific soccer fixture")
    p.add_argument("--player-prop", type=str, metavar="PLAYER_NAME",
                   help="NBA player prop analysis")
    p.add_argument("--lines",     type=str, default="pts=20.5",
                   help="Stat lines e.g. pts=25.5,reb=7.5,ast=8.5")
    p.add_argument("--player-avgs", type=str, default=None,
                   help="Known season averages (offline mode) e.g. pts=27.1,reb=7.4,ast=8.3")
    p.add_argument("--td-scorer", action="store_true",
                   help="NFL anytime TD scorer analysis")
    p.add_argument("--team-id",   type=str, default=None,
                   help="ESPN team ID for TD scorer analysis")
    p.add_argument("--value-only", action="store_true",
                   help="Only show signals with positive edge vs market odds")
    p.add_argument("--min-edge",  type=float, default=2.0,
                   help="Minimum edge %% for value filter (default: 2)")
    p.add_argument("--save-report", action="store_true",
                   help="Save a markdown report to reports/YYYY-MM-DD.md")
    p.add_argument("--season-avg", action="store_true",
                   help="Run season-average O/U demo for NBA players")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main() -> None:
    parser = build_parser()
    args   = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    all_signals: List[BetSignal] = []

    # ── Custom fixture mode ──────────────────────────────────────────────────
    if args.fixture:
        home, away = args.fixture
        league     = args.league or "EPL"
        logger.info("Custom fixture: %s vs %s (%s)", home, away, league)
        sigs = run_custom_fixture(home, away, league)
        all_signals += sigs

    # ── Player prop mode ─────────────────────────────────────────────────────
    elif args.player_prop:
        lines = parse_lines(args.lines)
        known = parse_lines(args.player_avgs) if args.player_avgs else None
        logger.info("NBA player prop: %s  lines=%s  avgs=%s",
                    args.player_prop, lines, known)
        model = NBAModel()
        all_signals += model.player_props_by_name(
            args.player_prop, lines, known_avgs=known)

    # ── Season average O/U demo ──────────────────────────────────────────────
    elif args.season_avg:
        # Demo: LeBron James (id=237) and Kevin Durant (id=140)
        sigs = run_nba_season_avg(
            player_ids=[237, 140],
            names=["LeBron James", "Kevin Durant"],
            stat="pts",
            line=25.5,
        )
        all_signals += sigs

    # ── TD scorer mode ───────────────────────────────────────────────────────
    elif args.td_scorer:
        team_id = args.team_id or "1"
        logger.info("NFL TD scorer for team %s", team_id)
        all_signals += run_nfl_td_scorer(team_id)

    # ── Full daily run ───────────────────────────────────────────────────────
    else:
        sports = [s.lower() for s in args.sports]

        if "soccer" in sports:
            leagues = [args.league] if args.league else ["EPL", "UCL", "LIGA", "L1"]
            all_signals += run_soccer(leagues)

        if "nba" in sports:
            all_signals += run_nba()

        if "nfl" in sports:
            all_signals += run_nfl()

    # ── Filter & display ─────────────────────────────────────────────────────
    if args.value_only:
        display = filter_value_bets(all_signals, min_edge=args.min_edge)
        title   = f"Value Bets (edge ≥ {args.min_edge}%)"
    else:
        display = all_signals
        title   = "All Signals"

    if not display:
        logger.warning("No signals generated. Check API keys / network connectivity.")
        print("\n[!] No signals generated.")
        print("    Tips:")
        print("    • Set API_FOOTBALL_KEY env var for soccer player/xG data")
        print("    • Games may not be scheduled today – try --fixture or --player-prop")
    else:
        print_signals_table(display, title=title)

    # ── Save report ──────────────────────────────────────────────────────────
    if args.save_report and all_signals:
        out = save_markdown_report(all_signals,
                                   outdir="reports")
        print(f"\n[+] Report saved → {out}")


if __name__ == "__main__":
    main()
