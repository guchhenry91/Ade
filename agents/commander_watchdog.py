#!/usr/bin/env python3
from __future__ import annotations
import datetime as dt
import json
import os
import pathlib
import shutil
import subprocess
import urllib.parse
import urllib.request

WORKSPACE = pathlib.Path('/data/.openclaw/workspace')
AGENTS = WORKSPACE / 'agents'
STATUS_LOG = AGENTS / 'commander-status.md'
ROUTES = AGENTS / 'telegram-routes.json'
RCFG = AGENTS / 'reporting-config.json'
CRON_FILE = WORKSPACE / 'jobs' / 'topic-routing-cron.txt'
CHAT_ID = '-1003659714036'
COMMANDER_THREAD_ID = '69'

REQ_DIRS = [
    AGENTS,
    AGENTS / 'status',
    AGENTS / 'approvals',
    WORKSPACE / 'sports-predictions',
    WORKSPACE / 'sports-betting',
    WORKSPACE / 'tiktok',
    WORKSPACE / 'jobs',
]

REQ_TOPICS = [
    'sports-analytics', 'tiktok-production', 'job-opportunities',
    'betting-insights', 'commander-control'
]

AGENT_WINDOWS_MIN = {
    'commander-worker.log': 20,
    'sports-worker.log': 30,
    'betting-worker.log': 45,
    'tiktok-worker.log': 120,
    'jobs-worker.log': 120,
}

REQUIRED_CRON_MARKERS = [
    'sports_prediction_worker.py',
    'betting_intelligence_worker.py',
    'tiktok_worker.py',
    'job_research_worker.py',
    'commander_supervisor_worker.py',
    'telegram_topic_listener.py',
    'commander_watchdog.py',
]


def now():
    return dt.datetime.now(dt.timezone.utc)


def run(cmd: str) -> tuple[int, str]:
    p = subprocess.run(['bash', '-lc', cmd], capture_output=True, text=True)
    return p.returncode, (p.stdout or p.stderr).strip()


def get_reporting_token() -> str | None:
    cfg = {}
    if RCFG.exists():
        try:
            cfg = json.loads(RCFG.read_text())
        except Exception:
            cfg = {}
    key = cfg.get('reporting_bot', {}).get('env_token_key', 'TELEGRAM_REPORTING_BOT_TOKEN')
    return os.getenv(key) or cfg.get('reporting_bot', {}).get('token') or os.getenv('TELEGRAM_REPORTING_BOT_TOKEN')

def telegram_alert(msg: str):
    token = get_reporting_token()
    sent = False
    if token:
        try:
            params = urllib.parse.urlencode({
                'chat_id': CHAT_ID,
                'message_thread_id': COMMANDER_THREAD_ID,
                'text': msg,
            })
            url = f"https://api.telegram.org/bot{token}/sendMessage?{params}"
            with urllib.request.urlopen(url, timeout=20) as r:
                data = json.loads(r.read().decode('utf-8'))
                sent = bool(data.get('ok'))
        except Exception:
            sent = False

    if not sent:
        alerts = AGENTS / 'status' / 'watchdog-alerts.ndjson'
        alerts.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            'ts': now().isoformat(),
            'chat_id': int(CHAT_ID),
            'thread_id': int(COMMANDER_THREAD_ID),
            'message': msg,
        }
        with alerts.open('a', encoding='utf-8') as f:
            f.write(json.dumps(payload) + '\n')
        log(f"ALERT_QUEUED: {msg}")
    else:
        log(f"ALERT_SENT: {msg}")


def log(line: str):
    STATUS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with STATUS_LOG.open('a', encoding='utf-8') as f:
        f.write(f"\n- {line}")


def check_container_runtime(issues: list[str]):
    c1, out1 = run('test -f /.dockerenv && echo running || echo unknown')
    c2, out2 = run('openclaw gateway status 2>/dev/null || true')
    c3, out3 = run("ss -ltn 2>/dev/null | awk '{print $4}' | grep -E ':(3000|8080|8765)$' | head -n1")
    if 'running' not in out1:
        run('openclaw gateway restart || true')
        issues.append('container restart')
    if not out2:
        run('openclaw gateway restart || true')
        issues.append('gateway restart')
    if not out3:
        issues.append('websocket inactive')


def ensure_workspace_dirs(issues: list[str]):
    for d in REQ_DIRS:
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            issues.append(f'directory repair: {d}')


def check_routes(issues: list[str]):
    repaired = False
    data = {}
    if ROUTES.exists():
        try:
            data = json.loads(ROUTES.read_text())
        except Exception:
            data = {}
    for t in REQ_TOPICS:
        if t not in data or not isinstance(data.get(t), dict):
            data[t] = {'chat_id': -1003659714036, 'thread_id': None}
            repaired = True
        if 'chat_id' not in data[t] or 'thread_id' not in data[t]:
            data[t]['chat_id'] = -1003659714036
            data[t]['thread_id'] = data[t].get('thread_id')
            repaired = True
    if repaired:
        ROUTES.write_text(json.dumps(data, indent=2) + '\n')
        run('python3 /data/.openclaw/workspace/agents/telegram_topic_listener.py || true')
        issues.append('routing repair')


def check_cron(issues: list[str]):
    CRON_FILE.parent.mkdir(parents=True, exist_ok=True)
    text = CRON_FILE.read_text() if CRON_FILE.exists() else ''
    changed = False
    if not text:
        text = '# OpenClaw topic routing + monitoring cron\n'
        changed = True
    templates = {
        'sports_prediction_worker.py': "*/15 * * * * /usr/bin/python3 /data/.openclaw/workspace/agents/workers/sports_prediction_worker.py --routes /data/.openclaw/workspace/agents/telegram-routes.json >> /data/.openclaw/workspace/agents/status/sports-worker.log 2>&1",
        'betting_intelligence_worker.py': "*/30 * * * * /usr/bin/python3 /data/.openclaw/workspace/agents/workers/betting_intelligence_worker.py --routes /data/.openclaw/workspace/agents/telegram-routes.json >> /data/.openclaw/workspace/agents/status/betting-worker.log 2>&1",
        'tiktok_worker.py': "*/30 * * * * /usr/bin/python3 /data/.openclaw/workspace/agents/workers/tiktok_worker.py --routes /data/.openclaw/workspace/agents/telegram-routes.json >> /data/.openclaw/workspace/agents/status/tiktok-worker.log 2>&1",
        'job_research_worker.py': "*/30 * * * * /usr/bin/python3 /data/.openclaw/workspace/agents/workers/job_research_worker.py --routes /data/.openclaw/workspace/agents/telegram-routes.json >> /data/.openclaw/workspace/agents/status/jobs-worker.log 2>&1",
        'commander_supervisor_worker.py': "*/15 * * * * /usr/bin/python3 /data/.openclaw/workspace/agents/workers/commander_supervisor_worker.py --routes /data/.openclaw/workspace/agents/telegram-routes.json >> /data/.openclaw/workspace/agents/status/commander-worker.log 2>&1",
        'telegram_topic_listener.py': "*/5 * * * * /usr/bin/python3 /data/.openclaw/workspace/agents/telegram_topic_listener.py >> /data/.openclaw/workspace/agents/status/topic-listener.log 2>&1",
        'commander_watchdog.py': "*/5 * * * * /usr/bin/python3 /data/.openclaw/workspace/agents/commander_watchdog.py >> /data/.openclaw/workspace/agents/status/watchdog.log 2>&1",
    }
    for marker, line in templates.items():
        if marker not in text:
            text += line + '\n'
            changed = True
    if changed:
        CRON_FILE.write_text(text)
        issues.append('cron repair')


def check_agent_health(issues: list[str]):
    base = AGENTS / 'status'
    base.mkdir(parents=True, exist_ok=True)
    t = now().timestamp()
    for logfile, window in AGENT_WINDOWS_MIN.items():
        p = base / logfile
        if not p.exists() or (t - p.stat().st_mtime) / 60 > window:
            issues.append(f'agent failure: {logfile}')
            # repair attempt marker
            p.touch()


def check_resources(issues: list[str]):
    _, cpu_raw = run("top -bn1 | awk '/Cpu\(s\)/{print 100-$8; exit}'")
    _, mem_raw = run("free | awk '/Mem:/{print ($3/$2)*100}'")
    du = shutil.disk_usage('/data')
    disk_pct = (du.used / du.total) * 100
    try:
        cpu = float(cpu_raw or 0)
    except Exception:
        cpu = 0
    try:
        mem = float(mem_raw or 0)
    except Exception:
        mem = 0
    if cpu > 90 or mem > 90 or disk_pct > 85:
        issues.append(f'resource warning cpu={cpu:.1f}% mem={mem:.1f}% disk={disk_pct:.1f}%')


def check_logs(issues: list[str]):
    s = AGENTS / 'status'
    patterns = ['error', 'failed', 'telegram connection', 'api error']
    hits = 0
    for p in s.glob('*.log'):
        txt = p.read_text(encoding='utf-8', errors='ignore')[-6000:].lower()
        for pat in patterns:
            hits += txt.count(pat)
    if hits >= 5:
        issues.append('repeated failures detected')


def main():
    issues: list[str] = []
    ensure_workspace_dirs(issues)
    check_container_runtime(issues)
    check_routes(issues)
    check_cron(issues)
    check_agent_health(issues)
    check_resources(issues)
    check_logs(issues)

    stamp = now().strftime('%Y-%m-%d %H:%M UTC')
    if issues:
        for i in issues:
            if any(k in i for k in ['agent failure', 'cron repair', 'routing repair', 'resource warning', 'container restart', 'gateway restart']):
                telegram_alert('@HenryAgentsbot [Watchdog] Issue detected → repair attempted.')
        log(f"## {stamp}\n- Watchdog run: issues={issues}")
    else:
        log(f"## {stamp}\n- Watchdog run: OK")


if __name__ == '__main__':
    main()
