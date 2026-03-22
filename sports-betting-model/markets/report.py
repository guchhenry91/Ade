"""
Report generator – produces rich console tables and markdown reports.
"""
from __future__ import annotations
import os
from datetime import date
from pathlib import Path
from typing import List

from utils.odds import BetSignal
from markets.value_engine import rank_signals, summarise

try:
    from rich.console import Console
    from rich.table   import Table
    from rich         import box
    RICH = True
except ImportError:
    RICH = False


def _confidence_color(conf: str) -> str:
    return {"HIGH": "green", "MEDIUM": "yellow", "LOW": "red"}.get(conf, "white")


def print_signals_table(signals: List[BetSignal], title: str = "Betting Signals") -> None:
    if not RICH:
        _plain_table(signals, title)
        return

    console = Console()
    table   = Table(title=title, box=box.SIMPLE_HEAD, show_lines=True)
    table.add_column("League",     style="cyan",    no_wrap=True)
    table.add_column("Market",     style="magenta", no_wrap=True)
    table.add_column("Selection",  style="white",   no_wrap=False)
    table.add_column("Model Prob", justify="right")
    table.add_column("Mkt Odds",   justify="right")
    table.add_column("Edge %",     justify="right")
    table.add_column("Kelly",      justify="right")
    table.add_column("Conf",       justify="center")
    table.add_column("Notes",      style="dim")

    for s in rank_signals(signals):
        color = _confidence_color(s.confidence)
        table.add_row(
            s.league,
            s.market,
            s.selection,
            f"[{color}]{s.model_prob_pct}[/{color}]",
            str(s.market_odds) if s.market_odds else "–",
            f"[{color}]{s.edge_pct:.1f}%[/{color}]" if s.edge_pct is not None else "–",
            f"{s.kelly_frac*100:.2f}%" if s.kelly_frac is not None else "–",
            f"[{color}]{s.confidence}[/{color}]",
            s.notes[:60],
        )
    console.print(table)
    console.print(summarise(signals))


def _plain_table(signals: List[BetSignal], title: str) -> None:
    print(f"\n{'='*80}")
    print(f" {title}")
    print(f"{'='*80}")
    fmt = "{:<8} {:<22} {:<35} {:>8} {:>8} {:>7} {:>6}"
    print(fmt.format("League", "Market", "Selection",
                     "Prob%", "Odds", "Edge%", "Conf"))
    print("-" * 80)
    for s in rank_signals(signals):
        print(fmt.format(
            s.league[:8],
            s.market[:22],
            s.selection[:35],
            s.model_prob_pct,
            str(s.market_odds) if s.market_odds else "–",
            f"{s.edge_pct:.1f}%" if s.edge_pct is not None else "–",
            s.confidence[:6],
        ))


def save_markdown_report(signals: List[BetSignal],
                         outdir: str = "reports") -> Path:
    """Save signals as a markdown report and return the file path."""
    outdir_path = Path(outdir)
    outdir_path.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    path  = outdir_path / f"{today}.md"

    summary = summarise(signals)
    lines = [
        f"# Sports Betting Model Report — {today}",
        "",
        "## Summary",
        f"- Total signals: **{summary['total']}**",
        f"- By league: {summary['by_league']}",
        f"- By confidence: {summary['by_conf']}",
        "",
        "## Signals",
        "",
        "| League | Market | Selection | Prob% | Odds | Edge% | Conf | Notes |",
        "|--------|--------|-----------|-------|------|-------|------|-------|",
    ]

    for s in rank_signals(signals):
        edge  = f"{s.edge_pct:.1f}%" if s.edge_pct is not None else "–"
        odds  = str(s.market_odds)   if s.market_odds is not None else "–"
        lines.append(
            f"| {s.league} | {s.market} | {s.selection} | "
            f"{s.model_prob_pct} | {odds} | {edge} | {s.confidence} | {s.notes} |"
        )

    path.write_text("\n".join(lines))
    return path
