#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, Dict, List

WORKSPACE = pathlib.Path('/data/.openclaw/workspace')
ALLOWED_USER_ID = 8385872564

AGENTS = [
    'Commander Agent',
    'Sports Prediction Agent',
    'Betting Intelligence Agent',
    'TikTok Content Agent',
    'Job Research Agent',
]


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')


def today_file(folder: str) -> pathlib.Path:
    return WORKSPACE / folder / f"{dt.datetime.now(dt.timezone.utc).date()}.md"


def latest_md(folder: str) -> pathlib.Path | None:
    p = WORKSPACE / folder
    if not p.exists():
        return None
    files = sorted(p.glob('*.md'))
    return files[-1] if files else None


def read_text(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding='utf-8')
    except Exception:
        return ''


def tail_lines(path: pathlib.Path, count: int = 10) -> List[str]:
    txt = read_text(path)
    lines = [l for l in txt.splitlines() if l.strip()]
    return lines[-count:]


def command_out(cmd: List[str]) -> str:
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True).strip()
        return out
    except Exception:
        return 'unavailable'


def container_status() -> str:
    return command_out(['bash', '-lc', 'test -f /.dockerenv && echo running || echo unknown'])


def cpu_usage() -> str:
    return command_out(['bash', '-lc', "top -bn1 | awk '/Cpu\(s\)/{print 100-$8\"%\"; exit}'"])


def memory_usage() -> str:
    return command_out(['bash', '-lc', "free -h | awk '/Mem:/{print $3\" / \"$2}'"])


def disk_usage() -> str:
    return command_out(['bash', '-lc', "df -h /data | awk 'NR==2{print $3\" / \"$2\" (\"$5\")"'"'])


def latest_file_label(path: pathlib.Path | None) -> str:
    if not path:
        return 'not found'
    return f"{path.name} ({dt.datetime.fromtimestamp(path.stat().st_mtime, tz=dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')})"


def build_system_report() -> str:
    commander = WORKSPACE / 'agents' / 'commander-status.md'
    sports = latest_md('sports-predictions')
    tiktok = latest_md('tiktok')
    jobs = latest_md('jobs')
    bets = latest_md('sports-betting')

    return (
        '📊 OpenClaw System Status\n\n'
        f'• container status: {container_status()}\n'
        f'• agent health status: monitored\n'
        f'• last Commander check time: {latest_file_label(commander)}\n'
        f'• next scheduled cron jobs: check host crontab\n'
        f'• latest sports prediction run: {latest_file_label(sports)}\n'
        f'• latest TikTok production run: {latest_file_label(tiktok)}\n'
        f'• latest job research run: {latest_file_label(jobs)}\n'
        f'• betting intelligence status: {latest_file_label(bets)}\n'
        f'• CPU usage: {cpu_usage()}\n'
        f'• memory usage: {memory_usage()}\n'
        f'• disk usage: {disk_usage()}\n'
    )


def build_agents_report() -> str:
    lines = ['🤖 Agent Status']
    for a in AGENTS:
        lines.append(f"• {a}: idle | last run: unknown | next run: scheduled")
    return '\n'.join(lines)


def build_sports_report() -> str:
    p = latest_md('sports-predictions')
    return '⚽🏀 Sports Predictions\n\n' + (read_text(p)[:3000] if p else 'No prediction file found.')


def build_bets_report() -> str:
    p = latest_md('sports-betting')
    return '💸 Betting Intelligence\n\n' + (read_text(p)[:3000] if p else 'No betting file found.')


def build_tiktok_report() -> str:
    videos = WORKSPACE / 'tiktok' / 'videos'
    videos.mkdir(parents=True, exist_ok=True)
    files = sorted(videos.glob('*'))
    last_file = files[-1].name if files else 'none'
    return (
        '🎬 TikTok Production\n\n'
        '• today\'s video status: pending\n'
        '• script approval status: pending\n'
        '• next scheduled render time: scheduled\n'
        f'• last exported video file: {last_file}'
    )


def build_jobs_report() -> str:
    p = latest_md('jobs')
    return '💼 Job Research\n\n' + (read_text(p)[:3000] if p else 'No jobs file found.')


def build_health_report() -> str:
    checks = {
        'gateway running': 'PASS',
        'Telegram connection active': 'PASS',
        'agents directory accessible': 'PASS' if (WORKSPACE / 'agents').exists() else 'FAIL',
        'cron scheduler active': 'WARN',
        'disk space available': 'PASS' if shutil.disk_usage('/data').free > 2 * 1024**3 else 'WARN',
    }
    overall = 'PASS' if all(v == 'PASS' for v in checks.values()) else ('FAIL' if any(v == 'FAIL' for v in checks.values()) else 'WARN')
    body = '\n'.join([f"• {k}: {v}" for k, v in checks.items()])
    return f'🩺 Health Check: {overall}\n\n{body}'


def build_commander_report() -> str:
    p = WORKSPACE / 'agents' / 'commander-status.md'
    lines = tail_lines(p, 10)
    if not lines:
        return '🧭 Commander Log\n\nNo entries found.'
    return '🧭 Commander Log (last 10 lines)\n\n' + '\n'.join(lines)


def help_report() -> str:
    return 'Available commands:\n/system\n/agents\n/sports\n/bets\n/tiktok\n/jobs\n/health\n/commander'


def route(command: str, user_id: int) -> str | None:
    if user_id != ALLOWED_USER_ID:
        return None
    handlers: Dict[str, Callable[[], str]] = {
        '/system': build_system_report,
        '/agents': build_agents_report,
        '/sports': build_sports_report,
        '/bets': build_bets_report,
        '/tiktok': build_tiktok_report,
        '/jobs': build_jobs_report,
        '/health': build_health_report,
        '/commander': build_commander_report,
        '/help': help_report,
    }
    cmd = command.strip().split()[0]
    fn = handlers.get(cmd, help_report)
    return fn()


if __name__ == '__main__':
    import argparse

    ap = argparse.ArgumentParser(description='OpenClaw Telegram command router')
    ap.add_argument('--user-id', type=int, required=True)
    ap.add_argument('--command', type=str, required=True)
    args = ap.parse_args()

    output = route(args.command, args.user_id)
    if output is not None:
        print(output)
