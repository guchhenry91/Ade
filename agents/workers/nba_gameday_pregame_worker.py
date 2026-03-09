#!/usr/bin/env python3
from __future__ import annotations
import json, pathlib, urllib.request, urllib.parse
from datetime import datetime, timezone

ROOT = pathlib.Path('/data/.openclaw/workspace')
REPORT_CFG = ROOT / 'agents' / 'reporting-config.json'
STATE = ROOT / 'agents' / 'status' / 'nba-pregame-state.json'


def fetch_json(url: str):
    req = urllib.request.Request(url, headers={'User-Agent': 'OpenClaw/1.0'})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode())


def pct(record_summary: str) -> float:
    try:
        w, l = record_summary.split('-')[:2]
        w, l = int(w), int(l)
        return w / max(1, (w + l))
    except Exception:
        return 0.5


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"last_sent_key": None}


def save_state(s):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=2) + "\n")


def tg_send(token: str, chat_id: int, thread_id: int, text: str):
    data = urllib.parse.urlencode({
        'chat_id': chat_id,
        'message_thread_id': thread_id,
        'text': text
    }).encode()
    req = urllib.request.Request(f'https://api.telegram.org/bot{token}/sendMessage', data=data)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode())


def main():
    cfg = json.loads(REPORT_CFG.read_text())
    token = cfg.get('reporting_bot', {}).get('token')
    topic = cfg.get('topics', {}).get('sports-analytics', {})
    chat_id = topic.get('chat_id')
    thread_id = topic.get('message_thread_id')
    if not token or chat_id is None or thread_id is None:
        return

    board = fetch_json('https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard')
    events = board.get('events', [])
    now = datetime.now(timezone.utc)

    # trigger around 3 hours before first tip (tolerance window)
    candidates = []
    for e in events:
        try:
            dt = datetime.fromisoformat(e['date'].replace('Z', '+00:00'))
        except Exception:
            continue
        mins = (dt - now).total_seconds() / 60
        if 120 <= mins <= 210:
            candidates.append(e)

    if not candidates:
        return

    slate_key = now.strftime('%Y-%m-%d') + '|' + ','.join(sorted([e.get('id', '') for e in candidates]))
    st = load_state()
    if st.get('last_sent_key') == slate_key:
        return

    games = []
    for e in candidates:
        comp = (e.get('competitions') or [{}])[0]
        c = comp.get('competitors') or []
        home = next((x for x in c if x.get('homeAway') == 'home'), None)
        away = next((x for x in c if x.get('homeAway') == 'away'), None)
        if not home or not away:
            continue
        hr = ((home.get('records') or [{}])[0]).get('summary', '0-0')
        ar = ((away.get('records') or [{}])[0]).get('summary', '0-0')

        hs = pct(hr) + 0.04
        aws = pct(ar)
        total = max(0.01, hs + aws)
        hp, ap = hs / total, aws / total
        if hp >= ap:
            winner = home['team']['displayName']
            wp = hp
        else:
            winner = away['team']['displayName']
            wp = ap

        margin = int(round((hp - ap) * 18))
        home_pts = int(round(112 + margin / 2))
        away_pts = int(round(112 - margin / 2))

        games.append({
            'match': f"{away['team']['displayName']} @ {home['team']['displayName']}",
            'winner': winner,
            'score': f"{away['team']['displayName']} {away_pts} - {home_pts} {home['team']['displayName']}",
            'prob': round(wp * 100, 1),
            'conf': int(max(52, min(78, round(50 + abs(hp - ap) * 100)))),
            'edge': round((wp - 0.5) * 100, 1)
        })

    if not games:
        return

    lines = ['@HenryAgentsbot NBA pregame model update (auto, ~3h before tipoff)', '', 'Match/Game Analysis']
    for g in games:
        lines += [
            f"- {g['match']}",
            f"  • Predicted winner: {g['winner']}",
            f"  • Projected score: {g['score']}",
            f"  • Win probability: {g['prob']}%",
            f"  • Confidence rating: {g['conf']}%",
        ]

    top3 = sorted(games, key=lambda x: (x['prob'], x['conf']), reverse=True)[:3]
    lines += ['', 'Safest 3-Leg Parlay']
    for i, g in enumerate(top3, 1):
        lines.append(f"- Leg {i}: {g['winner']} to win ({g['prob']}%)")
    comb = 1.0
    for g in top3:
        comb *= max(0.01, g['prob'] / 100)
    lines.append(f"- Combined hit estimate: {round(comb * 100, 1)}%")
    lines.append('- Combined risk explanation: parlay risk compounds; one upset loses ticket.')

    lines += ['', 'Value / Upset Opportunities']
    for g in sorted(games, key=lambda x: x['edge'], reverse=True)[:3]:
        lines.append(f"- {g['match']}: {g['winner']} value lean | model {g['prob']}% vs implied baseline 50.0% | edge +{round(g['prob']-50,1)}%")

    tg_send(token, chat_id, thread_id, '\n'.join(lines)[:3900])
    st['last_sent_key'] = slate_key
    save_state(st)


if __name__ == '__main__':
    main()
